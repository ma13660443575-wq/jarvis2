# -*- coding: utf-8 -*-
"""
brain/rag.py — 检索增强上下文构建
=================================
把检索命中的 vault 笔记拼成系统提示词里的「参考资料」，并返回可点击来源列表。
"""
from __future__ import annotations

from ..vault import reader, paths


def build_context(query: str, limit: int = 6) -> dict:
    """执行混合检索并组装上下文。

    返回：
      - context: str         已格式化的参考资料（无命中时为空串）
      - sources: [str]       来源 wikilink 相对路径
      - mode: str            hybrid / keyword
    """
    from . import search as search_mod

    sr = search_mod.hybrid_search(query, limit=limit)
    results = sr.get("results", [])
    if not results:
        return {"context": "", "sources": [], "mode": sr.get("mode", "empty")}

    blocks: list[str] = []
    sources: list[str] = []
    for r in results:
        note = reader.get_note(r["rel_path"])
        if note is None:
            continue
        body_excerpt = note.body[:600].strip()
        blocks.append(
            f"### 来源 {note.wikilink}\n"
            f"路径：`{note.rel_path}`\n"
            f"内容摘录：\n{body_excerpt}"
        )
        sources.append(note.rel_path)

    context = "以下是参考资料（回答时优先依据它们，并在合适处标注来源）：\n\n" + \
        "\n\n".join(blocks) if blocks else ""
    return {"context": context, "sources": sources, "mode": sr.get("mode", "hybrid")}
