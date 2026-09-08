# -*- coding: utf-8 -*-
"""
hands/wf09.py — 情报扫描
=========================
抓取 RSS 情报源（config.RSS_FEEDS）→ 生成 I- 采集卡草稿 → 确认后写入 00_Inbox。
无 key / 无 RSS 源 / 抓取失败 均优雅降级为清晰提示，绝不 500。
"""
from __future__ import annotations

from .. import core
from ..engine import contract
from ..vault import paths

_MAX_PER_RUN = 5


class WF09(contract.WorkflowBase):
    name = "wf09"
    title = "情报扫描"
    desc = "抓取 RSS 情报源，生成 I- 采集卡进入采集箱。"
    interactive = True

    def precheck(self, payload: dict) -> dict:
        from ..senses import rss
        r = rss.fetch(limit=1)
        if not r["ok"]:
            return {"ok": False, "error": r.get("error", "RSS 情报源不可用。")}
        return {"ok": True}

    def execute(self, payload: dict) -> dict:
        from ..senses import rss
        r = rss.fetch(limit=_MAX_PER_RUN)
        if not r["ok"]:
            raise contract.WorkflowError(r.get("error", "RSS 抓取失败。"))
        items = r.get("items", [])
        if not items:
            raise contract.WorkflowError("RSS 源没有可扫描的新条目。")

        today = core.today()
        drafts = []
        notes = []
        for i, it in enumerate(items[:_MAX_PER_RUN]):
            title = it.get("title") or "未命名情报"
            body = (it.get("summary") or "").strip() or title
            link = it.get("link") or ""
            if link:
                body += f"\n\n来源链接：{link}"
            name = "I-%s-%03d-%s" % (today.replace("-", ""), 900 + i, core.slugify(title, 20))
            rel = paths.rel("I", name)
            content = (
                f"---\ntype: 采集\nstatus: 未处理\ncreated: {today}\n"
                f"source: rss\ntags: [情报]\n---\n\n"
                f"# {name}\n\n{body}\n"
            )
            drafts.append({"rel_path": rel, "content": content, "action": "create"})
            notes.append(f"{title[:24]}")
        return {"drafts": drafts, "updates": [], "note": "；".join(notes)}

    def verify(self, payload: dict, result: dict) -> dict:
        bad = [d for d in result.get("drafts", [])
               if not d.get("rel_path", "").startswith("00_Inbox/")]
        return {"ok": not bad, "errors": ["情报卡必须落在 00_Inbox/"] if bad else []}

    def build_report(self, payload: dict, result: dict) -> str:
        return f"情报扫描：抓到 {len(result.get('drafts', []))} 条情报，确认后进入采集箱。"


def register():
    from ..engine import registry
    registry.register(WF09())
