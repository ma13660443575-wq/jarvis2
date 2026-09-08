# -*- coding: utf-8 -*-
"""
skills/parser.py — S-技能卡解析
================================
把 06_Capability/S-*.md 解析成结构化 Skill：
frontmatter(type/status/confidence/source/trigger/tools/output)
+ 正文分区(一句话/输入/处理流程/输出/评估标准/与概念连接)。

解析失败不抛异常：返回 Skill 但字段尽力而为，保证扫描不中断。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .. import config, core
from ..vault import paths

# 正文分区标题
_SECTIONS = ("一句话", "输入", "处理流程", "输出", "评估标准", "与概念连接")


@dataclass
class Skill:
    rel_path: str
    name: str                 # 展示名（文件名去 S- 前缀）
    slug: str                 # 注册用 id（英文安全 slug）
    trigger: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    status: str = "可用"
    confidence: str = ""
    source_card: str = ""
    created: str = ""
    updated: str = ""
    output: str = "report"    # report | draft（执行结果形态）
    fm: dict = field(default_factory=dict)
    body: str = ""            # 完整正文
    one_liner: str = ""
    flow: str = ""            # 处理流程节
    out_req: str = ""         # 输出要求节

    def to_dict(self) -> dict:
        return {
            "rel_path": self.rel_path,
            "name": self.name,
            "slug": self.slug,
            "trigger": self.trigger,
            "tools": self.tools,
            "status": self.status,
            "confidence": self.confidence,
            "source_card": self.source_card,
            "output": self.output,
            "one_liner": self.one_liner,
        }


def _section(body: str, title: str) -> str:
    """取 ## title 小节内容（到下一个 ## 或文件尾）。"""
    m = re.search(r"^#{2,3}\s*%s\s*[：:】]?\s*(.*)$" % re.escape(title), body, re.MULTILINE)
    if not m:
        return ""
    start = m.end()
    nxt = re.search(r"^#{2,3}\s+", body[start:], re.MULTILINE)
    seg = body[start:start + nxt.start()] if nxt else body[start:]
    return seg.strip()


def _slugify(name: str) -> str:
    s = core.slugify(name, 32) or "skill"
    return "skill:" + s


def parse_skill(rel_path: str, raw: str) -> Skill:
    from ..vault import reader
    fm, body = reader.parse_frontmatter(raw)
    filename = rel_path.rsplit("/", 1)[-1]
    base = filename[:-3] if filename.endswith(".md") else filename
    name = base[2:] if base.startswith("S-") else base

    def _list(v, fallback: list[str]):
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        if isinstance(v, str) and v.strip():
            return [x.strip() for x in re.split(r"[,，、;；|/]", v) if x.strip()]
        return fallback

    trigger = _list(fm.get("trigger"), [name])
    tools = _list(fm.get("tools"), [])

    return Skill(
        rel_path=rel_path,
        name=name,
        slug=_slugify(name),
        trigger=trigger,
        tools=tools,
        status=str(fm.get("status") or "可用"),
        confidence=str(fm.get("confidence") or ""),
        source_card=str(fm.get("source") or ""),
        created=str(fm.get("created") or ""),
        updated=str(fm.get("updated") or ""),
        output="draft" if str(fm.get("output") or "report") == "draft" else "report",
        fm=fm,
        body=body.strip(),
        one_liner=_section(body, "一句话") or llm_summarize(body),
        flow=_section(body, "处理流程") or body[:600],
        out_req=_section(body, "输出") or "给出结构化、可直接使用的结果。",
    )


def llm_summarize(text: str) -> str:
    from ..brain import llm
    first = next((l.strip() for l in text.splitlines() if l.strip() and not l.startswith("#")), "")
    return llm.summarize(first or text, 50)


def scan_skills_dir() -> list[Skill]:
    """扫描 06_Capability 目录，返回全部可解析技能（按更新时间倒序）。"""
    base = paths.ensure_capability_dir()
    out: list[Skill] = []
    if not base.is_dir():
        return out
    for p in sorted(base.glob("S-*.md"), key=lambda x: x.stat().st_mtime, reverse=True):
        rel = p.relative_to(config.VAULT_PATH).as_posix()
        try:
            out.append(parse_skill(rel, p.read_text(encoding="utf-8", errors="replace")))
        except Exception:  # noqa: BLE001
            continue
    return out
