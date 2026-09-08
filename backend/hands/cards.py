# -*- coding: utf-8 -*-
"""
hands/cards.py — 知识卡片生成助手
=================================
一次 LLM 调用产出 U-理解卡 / C-概念卡 / R-回忆卡 的结构化内容；
无 key 时退化为基于原卡的规则抽取（模板填充），保证工作流可跑通。
所有产出物均为 DraftFile（交给 5 步契约，确认前不落盘）。
"""
from __future__ import annotations

import json
import re

from .. import core
from ..brain import llm
from ..vault import paths, templates

_KIND_LABEL = {"U": "理解", "C": "概念", "R": "回忆", "A": "应用"}


def _ts() -> str:
    return core.today()


def clean_title(raw: str) -> str:
    """清理 LLM 返回的标题：剥掉 I-/C-/U- 前缀与日期串，避免 U- 卡名过长。
    例：'I-20260814-901-E2E工作流验证采集卡' → 'E2E工作流验证采集卡'。
    """
    t = (raw or "").strip().strip("# ").strip()
    # 去前缀（如 I-20260814-901- / U-20260814- / C-）
    for p in ("I-", "U-", "C-", "R-", "A-", "S-"):
        if t.startswith(p):
            t = t[len(p):]
            break
    # 去开头的日期数字串（如 20260814-901-）
    m = core.slugify(t, 60)
    return m or "知识"


def classify_domain(note, default: str = "通用知识") -> str:
    """域分类：frontmatter domain > LLM 分类 > 默认通用知识。"""
    dom = (note.fm.get("domain") or "").strip()
    if dom in paths.KNOWLEDGE_DOMAINS:
        return dom
    if llm.is_enabled():
        try:
            resp = llm.chat_once([
                {"role": "system", "content": "你是知识分类器。从候选里选一个最合适的域，只输出域名词。"},
                {"role": "user", "content": "候选：" + "、".join(paths.KNOWLEDGE_DOMAINS) +
                                            "\n内容标题：" + note.title + "\n正文：" + note.body[:500]},
            ], temperature=0)
            for d in paths.KNOWLEDGE_DOMAINS:
                if d in resp:
                    return d
        except Exception:
            pass
    return default


def _json_from_llm(text: str) -> dict:
    """从 LLM 输出提取 JSON（容忍 ```json 围栏与前后缀）。"""
    if not text:
        return {}
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    cand = m.group(1) if m else text
    try:
        return json.loads(cand)
    except Exception:
        m2 = re.search(r"(\{.*\})", cand, re.DOTALL)
        if m2:
            try:
                return json.loads(m2.group(1))
            except Exception:
                pass
    return {}


def _mock_parse(note) -> dict:
    """规则化抽取：把 I- 卡转成三卡内容（无 LLM 时）。"""
    title = note.title or "未命名"
    first_line = next((l.strip() for l in note.body.splitlines()
                       if l.strip() and not l.startswith("#")), "")
    summary = llm.summarize(first_line or note.body, 60)
    return {
        "title": title,
        "summary": summary,
        "u_content": note.body.strip()[:1500] or f"整理自 {note.wikilink}。",
        "concept_name": title,
        "concept_one_liner": summary,
        "concept_body": f"围绕「{title}」的核心认知：{summary}。边界：避免过度泛化；反例：并非所有场景都适用，需结合上下文判断。",
        "concept_edge": "适用边界：在明确场景下有效；反例：脱离语境直接套用会失真。",
        "recall_q": f"「{title}」的核心要点是什么？",
        "recall_a": summary,
    }


def generate_cards(note) -> dict:
    """根据 I- 采集卡生成三卡草稿。返回 {ok, domain, drafts, note}。"""
    domain = classify_domain(note)
    if llm.is_enabled():
        try:
            prompt = (
                "你是一名个人知识库整理助手。根据输入的采集卡内容，产出一套「理解卡+概念卡+回忆卡」。\n"
                "只输出 JSON（不要 markdown 围栏之外的文字），键："
                "title, summary, u_content, concept_name, concept_one_liner, "
                "concept_body, concept_edge, recall_q, recall_a。\n"
                "要求：summary 一句话≤50字；u_content 是结构化正文（要点式，100-300字）；"
                "concept_body 50-200字含边界/反例；recall_a 精炼。"
                "概念卡名称用领域通用术语，不要带前缀。\n\n采集卡标题：" + note.title +
                "\n采集卡正文：\n" + note.body[:2500]
            )
            resp = llm.chat_once([{"role": "user", "content": prompt}], temperature=0.4, max_tokens=1500)
            data = _json_from_llm(resp)
            if not data.get("title") or not data.get("concept_name"):
                data = _mock_parse(note)
        except Exception:
            data = _mock_parse(note)
    else:
        data = _mock_parse(note)

    today = _ts()
    src = note.wikilink

    # 概念卡名先定，供 U 卡关联链接引用真实 C 卡
    c_name = "C-%s" % data.get("concept_name", "概念")
    c_name = clean_title(c_name)

    # U- 理解卡（clean_title 保证文件名不过长）
    u_tail = clean_title(data.get("title", "知识"))
    u_name = "U-%s-%s" % (today.replace("-", ""), core.slugify(u_tail, 20))
    u_rel = paths.rel("U", u_name, domain=domain)
    u_content = templates.understand_card(
        u_name, domain=domain, summary=data.get("summary", ""),
        content=data.get("u_content", ""), source_wl=src, created=today,
        concept_wl="[[%s]]" % c_name,
    )

    # C- 概念卡
    c_rel = paths.rel("C", c_name)
    c_content = templates.concept_card(
        c_name, summary=data.get("concept_one_liner", ""),
        body=data.get("concept_body", ""), edge=data.get("concept_edge", ""),
        source_wl=src, created=today,
    )

    # R- 回忆卡
    r_name = "R-%s" % core.slugify(clean_title(data.get("concept_name", "概念")), 20)
    r_rel = paths.rel("R", r_name)
    r_content = templates.recall_card(
        r_name, question=data.get("recall_q", ""), answer=data.get("recall_a", ""),
        linked_card="[[%s]]" % c_name, source_wl=src, created=today,
    )

    return {
        "ok": True,
        "domain": domain,
        "drafts": [
            {"rel_path": u_rel, "content": u_content, "action": "create"},
            {"rel_path": c_rel, "content": c_content, "action": "create"},
            {"rel_path": r_rel, "content": r_content, "action": "create"},
        ],
        "note": f"为 {note.filename} 生成 U/C/R 三卡（域={domain}）",
    }
