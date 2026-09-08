# -*- coding: utf-8 -*-
"""
vault/indexer.py — 内存倒排索引（关键词检索基础）
- 对 vault 全部笔记建立 term → note 倒排；mtime 变化时重建。
- 供 brain/search.py 的关键词检索使用。
"""
from __future__ import annotations

import re
import threading

from . import reader

_WORD_RE = re.compile(r"[\w一-鿿]+", re.UNICODE)

_STOPWORDS = {
    "我", "你", "他", "她", "它", "我们", "你们", "他们", "的", "了", "是", "在",
    "和", "与", "及", "或", "这", "那", "吗", "呢", "啊", "吧", "么", "如何",
    "怎么", "什么", "为什么", "一个", "一下", "可以", "想要", "怎么", "哪些",
    "the", "a", "an", "of", "to", "and", "is", "in", "on", "for", "with",
}

_lock = threading.Lock()
_index: dict[str, dict[str, float]] | None = None   # term -> {rel_path: tf}
_index_key: tuple | None = None

# 中文分词：优先 jieba；不可用时退化为字符二元组（保证无额外依赖也可用）
try:
    import jieba
    jieba.setLogLevel(60)
    _USE_JIEBA = True
except Exception:  # noqa: BLE001
    _USE_JIEBA = False


def _tokenize(text: str) -> list[str]:
    text = text.lower()
    if _USE_JIEBA:
        toks = [t for t in jieba.lcut(text) if t.strip()]
        # 去掉停用词 / 纯符号 / 单字符标点
        return [t for t in toks
                if t not in _STOPWORDS and _WORD_RE.fullmatch(t) and t.strip("-_—.·")]
    # 退化：CJK 连续串切成二元组，英文按词
    words = _WORD_RE.findall(text)
    out: list[str] = []
    for w in words:
        if re.fullmatch(r"[\x00-\x7f]+", w):  # ASCII 整词
            out.append(w)
        else:
            if len(w) == 1:
                out.append(w)
            else:
                for i in range(len(w) - 1):
                    out.append(w[i:i + 2])
    return out


def _rebuild() -> dict[str, dict[str, float]]:
    idx: dict[str, dict[str, float]] = {}
    for note in reader.scan_vault():
        terms = _tokenize(note.title + "\n" + note.body)
        tf: dict[str, float] = {}
        for t in terms:
            tf[t] = tf.get(t, 0) + 1.0
        for t, c in tf.items():
            idx.setdefault(t, {})[note.rel_path] = c
    return idx


def get_index(force: bool = False) -> dict[str, dict[str, float]]:
    global _index, _index_key
    key = _mtime_key()
    with _lock:
        if not force and _index is not None and _index_key == key:
            return _index
        _index = _rebuild()
        _index_key = key
        return _index


def _mtime_key() -> tuple:
    from pathlib import Path
    from .. import config
    try:
        mtimes = sorted(p.stat().st_mtime_ns
                        for p in Path(config.VAULT_PATH).rglob("*.md"))
        return tuple(mtimes)
    except OSError:
        return ()


def search(query: str, limit: int = 20) -> list[dict]:
    """关键词检索：返回 [{rel_path, score, matched_terms}]，按匹配词数*词频排序。"""
    idx = get_index()
    terms = _tokenize(query)
    scores: dict[str, float] = {}
    matched: dict[str, list[str]] = {}
    for t in terms:
        for rel_path, tf in idx.get(t, {}).items():
            scores[rel_path] = scores.get(rel_path, 0) + 1 + tf
            matched.setdefault(rel_path, []).append(t)
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])[:limit]
    return [{"rel_path": r, "score": s, "matched_terms": matched.get(r, [])}
            for r, s in ranked]
