# -*- coding: utf-8 -*-
"""
hands/wf04.py — 复习会话
========================
列出今天到期的 R- 回忆卡（只读，不写）。实际问答/打分在「复习」页面闭环。
"""
from __future__ import annotations

from .. import core
from ..engine import contract
from ..vault import reader


class WF04(contract.WorkflowBase):
    name = "wf04"
    title = "复习会话"
    desc = "查看今日到期的回忆卡清单。"
    interactive = False

    def precheck(self, payload: dict) -> dict:
        due = self._due_cards()
        if not due:
            return {"ok": False, "error": "今天没有到期需要复习的卡片。"}
        return {"ok": True, "count": len(due)}

    def _due_cards(self):
        today = core.today()
        rs = reader.search_notes(prefix="R", folder="04_Knowledge", limit=500)
        out = []
        for n in rs:
            due = str(n.fm.get("due") or "")  # YAML date 可能被解析成 date 对象，统一转 str
            if due and due <= today:
                out.append(n)
        return sorted(out, key=lambda n: str(n.fm.get("due", "")))

    def execute(self, payload: dict) -> dict:
        due = self._due_cards()
        qa = []
        for n in due:
            q = next((l for l in n.body.splitlines() if l.strip().startswith("问：")), "")
            a = next((l for l in n.body.splitlines() if l.strip().startswith("答：")), "")
            qa.append({"rel_path": n.rel_path, "question": q or n.title, "answer": a})
        return {"drafts": [], "updates": [], "payload_out": {"cards": qa},
                "note": f"今日到期 {len(qa)} 张"}

    def verify(self, payload: dict, result: dict) -> dict:
        return {"ok": True}

    def build_report(self, payload: dict, result: dict) -> str:
        cards = result.get("payload_out", {}).get("cards", [])
        lines = [f"· {c['question'][:40]}（{c['rel_path']}）" for c in cards[:8]]
        return "今日复习清单：\n" + "\n".join(lines)


def register():
    from ..engine import registry
    registry.register(WF04())
