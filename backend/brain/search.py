# -*- coding: utf-8 -*-
"""
brain/search.py — 混合检索（关键词 × 向量，RRF 融合，自动降级）
================================================================
- keyword：vault/indexer 倒排（永远可用）。
- vector：memory/vector 语义检索（VECTOR_ENABLED 且模型可用时）。
- 结果用 RRF（Reciprocal Rank Fusion）合并：score = Σ 1/(k + rank)，k=60。
- 任一路失败自动降级，绝不让检索抛异常。
"""
from __future__ import annotations

from ..vault import indexer, reader
from ..memory import vector

_RRF_K = 60


def _rrf(lists: list[list[str]]) -> list[tuple[str, float]]:
    """输入多路候选 rel_path 有序列表，输出 RRF 融合后的 (rel_path, score)。"""
    score: dict[str, float] = {}
    for ranked in lists:
        for rank, rel in enumerate(ranked):
            score[rel] = score.get(rel, 0.0) + 1.0 / (_RRF_K + rank + 1)
    return sorted(score.items(), key=lambda kv: -kv[1])


def hybrid_search(query: str, limit: int = 8) -> dict:
    """混合检索。返回 {ok, results: [{rel_path, score, matched_terms?}], mode}。"""
    q = (query or "").strip()
    if not q:
        return {"ok": True, "results": [], "mode": "empty"}

    # 1) 关键词路（稳定）
    kw = indexer.search(q, limit=limit * 3)
    kw_terms = {r["rel_path"]: r["matched_terms"] for r in kw}
    kw_ranks = [r["rel_path"] for r in kw]

    # 2) 向量路（可降级）
    vec_ranks: list[str] = []
    vec_enabled = False
    try:
        vec_res = vector.search(q, limit=limit * 3)
        if vec_res:
            vec_ranks = [r["rel_path"] for r in vec_res]
            vec_enabled = True
    except Exception:
        vec_enabled = False

    # 3) RRF 融合
    if vec_ranks:
        fused = _rrf([kw_ranks, vec_ranks])
        mode = "hybrid"
    else:
        fused = [(r, 1.0 / (_RRF_K + i + 1)) for i, r in enumerate(kw_ranks)]
        mode = "keyword"  # 向量不可用时降级关键词

    results = []
    for rel, s in fused[:limit]:
        results.append({"rel_path": rel, "score": round(s, 5),
                        "matched_terms": kw_terms.get(rel, [])})
    return {"ok": True, "results": results, "mode": mode, "vector_enabled": vec_enabled}
