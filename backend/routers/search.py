# -*- coding: utf-8 -*-
"""
routers/search.py — 全局搜索（顶栏）
====================================
GET /api/search?q=...  → 混合检索 + 摘录，供顶栏下拉即时出结果。
GET /api/notes?path=... → 取单篇笔记正文（前端查看/预览用）。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..brain import search as search_mod
from ..vault import reader

router = APIRouter()


@router.get("/api/search")
def search(q: str = "", limit: int = 10):
    q = (q or "").strip()
    if not q:
        return {"ok": True, "data": []}
    sr = search_mod.hybrid_search(q, limit=limit)
    results = []
    for r in sr.get("results", []):
        note = reader.get_note(r["rel_path"])
        results.append({
            "rel_path": r["rel_path"],
            "score": r.get("score"),
            "matched_terms": r.get("matched_terms", []),
            "title": note.title if note else r["rel_path"],
            "excerpt": (note.body[:200].replace("\n", " ").strip() if note else ""),
        })
    return {"ok": True, "data": results, "mode": sr.get("mode", "keyword")}


@router.get("/api/notes")
def note_detail(path: str = ""):
    path = (path or "").strip()
    if not path:
        raise HTTPException(400, "缺少 path 参数")
    # 优先精确路径（含 .md）；否则按 wikilink 规则解析：
    # 兼容 [[03_Projects/README]]（不带 .md）、[[README]]（裸名，跨目录按文件名找）。
    note = reader.get_note(path)
    if note is None:
        note = reader.find_by_ref(path)
    if note is None:
        raise HTTPException(404, f"笔记不存在: {path}")
    return {"ok": True, "data": {
        "rel_path": note.rel_path,
        "title": note.title,
        "fm": note.fm,
        "body": note.body,
        "prefix": note.prefix,
        "wikilink": note.wikilink,
    }}
