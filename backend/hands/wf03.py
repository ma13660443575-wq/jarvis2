# -*- coding: utf-8 -*-
"""
hands/wf03.py — 生成回忆
========================
为概念卡（C-）生成配套 R- 回忆卡，进入复习调度。
"""
from __future__ import annotations

from ..engine import contract
from ..vault import reader
from . import cards


class WF03(contract.WorkflowBase):
    name = "wf03"
    title = "生成回忆"
    desc = "为概念卡生成回忆问答卡，进入复习调度。"
    interactive = True

    def precheck(self, payload: dict) -> dict:
        targets = self._targets()
        if not targets:
            return {"ok": False, "error": "没有可生成回忆卡的概念卡。"}
        return {"ok": True, "count": len(targets)}

    def _targets(self):
        cs = reader.search_notes(prefix="C", folder="05_Frameworks", limit=200)
        have = {n.fm.get("linked_card", "") for n in reader.search_notes(prefix="R", folder="04_Knowledge", limit=500)}
        out = []
        for n in cs:
            if n.wikilink in have:
                continue
            out.append(n)
            if len(out) >= 3:
                break
        return out

    def execute(self, payload: dict) -> dict:
        drafts, notes = [], []
        for note in self._targets():
            res = cards.generate_cards(note)
            if not res["ok"]:
                continue
            for d in res["drafts"]:
                if d["rel_path"].startswith("04_Knowledge/回忆卡"):
                    drafts.append(d)
            notes.append(note.filename)
        if not drafts:
            raise contract.WorkflowError("未能生成回忆卡。")
        return {"drafts": drafts, "updates": [], "note": "；".join(notes)}

    def verify(self, payload: dict, result: dict) -> dict:
        bad = [d for d in result.get("drafts", []) if not d.get("content")]
        return {"ok": not bad, "errors": ["存在空草稿"] if bad else []}

    def build_report(self, payload: dict, result: dict) -> str:
        return f"生成回忆：为 {result.get('note','')} 生成 {len(result.get('drafts',[]))} 张回忆卡。"


def register():
    from ..engine import registry
    registry.register(WF03())
