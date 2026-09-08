# -*- coding: utf-8 -*-
"""
memory/vector.py — 语义向量层（Qdrant 本地嵌入式 + fastembed）
=============================================================
- Qdrant 以本地目录模式运行（无需独立服务）；embedding 用 bge-small-zh-v1.5。
- 增量索引：以 (rel_path, mtime) 为指纹，只 upsert 变化笔记。
- 任何失败静默降级（返回空结果/disabled），由 search.py 决定关键词兜底，绝不 500。
"""
from __future__ import annotations

import logging
import hashlib
import threading

from .. import config
from ..vault import reader

log = logging.getLogger("jarvis2.vector")

_COLLECTION = "jarvis_notes"
_VECTOR_SIZE = 512
_EMBED_BATCH = 32

_client = None
_embed = None
_guard = threading.Lock()


def _stable_point_id(rel_path: str) -> int:
    """为笔记生成跨进程稳定的 Qdrant 数字 ID。

    Python 内置 hash 会因进程的 hash randomization 改变，不能作为持久化
    向量 ID。截取 63 位以兼容 Qdrant 的正整数点 ID，并避免 0。
    """
    digest = hashlib.blake2b(rel_path.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, byteorder="big") & ((1 << 63) - 1)
    return value or 1


def _delete_rel(client, rel_path: str) -> None:
    """删除某个 rel_path 的全部向量（包括旧版本留下的重复点）。"""
    from qdrant_client.models import Filter, FieldCondition, MatchValue, FilterSelector

    client.delete(
        collection_name=_COLLECTION,
        points_selector=FilterSelector(filter=Filter(
            must=[FieldCondition(key="rel_path", match=MatchValue(value=rel_path))]
        )),
    )


def _enabled() -> bool:
    return config.VECTOR_ENABLED


def _get_client():
    global _client
    if _client is None:
        from qdrant_client import QdrantClient
        _client = QdrantClient(path=str(config.QDRANT_PATH))
    return _client


def _get_embed():
    global _embed
    if _embed is None:
        from fastembed import TextEmbedding
        _embed = TextEmbedding(model_name=config.EMBEDDING_MODEL)
    return _embed


def _text_of(note) -> str:
    parts = [note.title or ""]
    if note.fm.get("tags"):
        parts.append(" ".join(note.fm["tags"]))
    parts.append(note.body or "")
    return "\n".join(parts)[:2000]


def _ensure_collection():
    client = _get_client()
    cols = [c.name for c in client.get_collections().collections]
    if _COLLECTION not in cols:
        client.create_collection(
            collection_name=_COLLECTION,
            vectors_config={
                "size": _VECTOR_SIZE,
                "distance": "Cosine",
                "on_disk": True,
            },
        )
        log.info("[vector] 已创建集合 %s (dim=%s)", _COLLECTION, _VECTOR_SIZE)


def ensure_index() -> dict:
    """全库增量索引。返回 {ok, enabled, indexed, reason?}。"""
    if not _enabled():
        return {"ok": True, "enabled": False, "reason": "VECTOR_ENABLED=false"}
    try:
        with _guard:
            _ensure_collection()
            client = _get_client()
            model = _get_embed()

            # 已有指纹
            existing: dict[str, dict] = {}
            offset = None
            while True:
                pts, offset = client.scroll(
                    collection_name=_COLLECTION,
                    limit=200,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )
                for p in pts:
                    rel = (p.payload or {}).get("rel_path")
                    if rel:
                        item = existing.setdefault(rel, {"mtime": "", "ids": []})
                        item["mtime"] = (p.payload or {}).get("mtime", "")
                        item["ids"].append(p.id)
                if offset is None:
                    break

            notes = reader.scan_vault()
            need: list = []
            current_rel: set[str] = set()
            for n in notes:
                current_rel.add(n.rel_path)
                mt = str(n.mtime)
                item = existing.get(n.rel_path)
                stable_id = _stable_point_id(n.rel_path)
                if (item is None or item.get("mtime") != mt or
                        item.get("ids") != [stable_id]):
                    need.append(n)

            # 删除已不存在的（按 rel_path payload 过滤；用模型对象避免版本兼容问题）
            gone = set(existing) - current_rel
            for rel in gone:
                _delete_rel(client, rel)

            # 增量 upsert（注意：本版本 qdrant-client 要求 PointStruct，传 dict 会抛异常）
            from qdrant_client.models import PointStruct
            if need:
                batch: list = []
                batch_notes: list = []
                stale_removed = 0
                for n in need:
                    vec = list(model.embed([_text_of(n)]))[0].tolist()
                    batch.append(PointStruct(
                        id=_stable_point_id(n.rel_path),
                        vector=vec,
                        payload={"rel_path": n.rel_path, "mtime": str(n.mtime)},
                    ))
                    batch_notes.append(n)
                    if len(batch) >= _EMBED_BATCH:
                        for item in batch_notes:
                            _delete_rel(client, item.rel_path)
                            stale_removed += len(existing.get(item.rel_path, {}).get("ids", []))
                        client.upsert(collection_name=_COLLECTION, points=batch)
                        batch = []
                        batch_notes = []
                if batch:
                    for item in batch_notes:
                        _delete_rel(client, item.rel_path)
                        stale_removed += len(existing.get(item.rel_path, {}).get("ids", []))
                    client.upsert(collection_name=_COLLECTION, points=batch)
            else:
                stale_removed = 0

            return {"ok": True, "enabled": True, "indexed": len(current_rel),
                    "added": len(need), "removed": len(gone) + stale_removed}
    except Exception as e:  # noqa: BLE001
        log.warning("[vector] ensure_index 降级: %s", e)
        return {"ok": False, "enabled": False, "reason": str(e)}


def search(query: str, limit: int = 10) -> list[dict]:
    """向量检索：返回 [{rel_path, score}]。失败返回空（调用方降级关键词）。"""
    if not _enabled():
        return []
    try:
        with _guard:
            _ensure_collection()
            client = _get_client()
            model = _get_embed()
            vec = list(model.embed([query[:500]]))[0].tolist()
            try:
                hits = client.query_points(
                    collection_name=_COLLECTION,
                    query=vec,
                    limit=limit,
                    with_payload=True,
                ).points
            except TypeError:
                hits = client.search(
                    collection_name=_COLLECTION,
                    query_vector=vec,
                    limit=limit,
                    with_payload=True,
                )
            out = []
            seen: set[str] = set()
            for h in hits:
                rel = (h.payload or {}).get("rel_path")
                if rel and rel not in seen:
                    seen.add(rel)
                    out.append({"rel_path": rel, "score": float(h.score)})
            return out
    except Exception as e:  # noqa: BLE001
        log.debug("[vector] search 降级: %s", e)
        return []


def list_nodes(limit: int = 120) -> list[dict]:
    """记忆星图节点：滚动拉取全部向量条目，返回 [{rel_path, title}]。

    title 优先取笔记标题（reader.get_note），读不到就用 rel_path。
    失败静默返回 []（与 search 一致的降级风格）。
    """
    if not _enabled():
        return []
    try:
        with _guard:
            _ensure_collection()
            client = _get_client()
            nodes: list[dict] = []
            seen: set[str] = set()
            offset = None
            while len(nodes) < limit:
                pts, offset = client.scroll(
                    collection_name=_COLLECTION,
                    limit=min(200, limit - len(nodes)),
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )
                for p in pts:
                    rel = (p.payload or {}).get("rel_path")
                    if not rel or rel in seen:
                        continue
                    seen.add(rel)
                    title = rel
                    try:
                        note = reader.get_note(rel)
                        if note and note.title:
                            title = note.title
                    except Exception:  # noqa: BLE001
                        pass
                    nodes.append({"rel_path": rel, "title": title})
                if offset is None:
                    break
            return nodes
    except Exception as e:  # noqa: BLE001
        log.debug("[vector] list_nodes 降级: %s", e)
        return []


def upsert_note(rel_path: str) -> dict:
    """单条笔记向量 upsert（记忆落库/笔记写入后调用）。失败静默。"""
    if not _enabled():
        return {"ok": True, "rel_path": rel_path, "enabled": False}
    try:
        n = reader.get_note(rel_path)
        if n is None:
            return {"ok": False, "rel_path": rel_path, "error": "note not found"}
        with _guard:
            _ensure_collection()
            client = _get_client()
            model = _get_embed()
            from qdrant_client.models import PointStruct
            vec = list(model.embed([_text_of(n)]))[0].tolist()
            _delete_rel(client, n.rel_path)
            client.upsert(
                collection_name=_COLLECTION,
                points=[PointStruct(
                    id=_stable_point_id(n.rel_path),
                    vector=vec,
                    payload={"rel_path": n.rel_path, "mtime": str(n.mtime)},
                )],
            )
        return {"ok": True, "rel_path": rel_path, "enabled": True}
    except Exception as e:  # noqa: BLE001
        log.debug("[vector] upsert_note 降级: %s", e)
        return {"ok": False, "rel_path": rel_path, "error": str(e)}
