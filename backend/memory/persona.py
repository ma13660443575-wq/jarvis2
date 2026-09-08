# -*- coding: utf-8 -*-
"""
memory/persona.py — 三层人格注入
================================
系统提示词 = 底座人格 + 用户画像锚点 + 已确认记忆摘要。
读取 vault 的 01_Identity 文件；文件缺失/解析失败自动降级，绝不报错。
"""
from __future__ import annotations

import time

from ..vault import reader

_cache: dict = {}
_TTL = 30.0  # 30s 内复用，避免每次请求读盘


def _read_cached(rel_path: str) -> str:
    now = time.time()
    c = _cache.get(rel_path)
    if c and now - c["t"] < _TTL:
        return c["text"]
    n = reader.get_note(rel_path)
    text = n.body if n else ""
    _cache[rel_path] = {"t": now, "text": text}
    return text


def _profile_anchor() -> str:
    body = _read_cached("01_Identity/个人档案.md")
    if not body:
        return ""
    # 只取前若干行做「锚点」，防止系统提示词过长
    lines = [l.strip() for l in body.splitlines() if l.strip() and not l.startswith("#")]
    return "\n".join(lines[:40])[:1500]


def _memory_summary() -> str:
    body = _read_cached("01_Identity/用户模型.md")
    if not body:
        return ""
    # 抽取各分区首个非空表行/列表项，压缩成摘要
    out: list[str] = []
    for section in ("一、偏好记忆", "二、决策记忆", "三、项目记忆", "四、学习记忆"):
        if section not in body:
            continue
        seg = body.split(section, 1)[1]
        # 截到下一个 ## 或 ---
        for cut in ("## ", "\n---"):
            idx = seg.find(cut)
            if idx != -1:
                seg = seg[:idx]
                break
        items = [l.strip() for l in seg.splitlines()
                 if l.strip().startswith("|") and not l.strip().startswith("|---")
                 and len(l.strip()) > 4][:4]
        if items:
            out.append(f"【{section.split('、')[-1].split(' ')[0]}】")
            out.extend(items)
    return "\n".join(out)[:2000]


def build_system_prompt(has_context: bool = False) -> str:
    """组装三层系统提示词。has_context=True 时由 rag 在末尾注入参考资料。"""
    base = (
        "你是 JARVIS——用户的个人 AI 指挥官与认知操作系统助理。\n"
        "协作协议：\n"
        "1. 结论先行，再给证据，最后给下一步建议。\n"
        "2. 回答用简体中文，善用 wikilink（如 [[05_Frameworks/C-xxx]]）引用 vault 笔记。\n"
        "3. 不确定时明说不知道，绝不编造来源。\n"
        "4. 当提供了「参考资料」时，优先依据资料回答，并标注参考来源。\n"
    )
    anchor = _profile_anchor()
    memory = _memory_summary()
    if anchor:
        base += "\n【用户画像锚点】\n" + anchor + "\n"
    if memory:
        base += "\n【用户动态记忆摘要】\n" + memory + "\n"
    return base
