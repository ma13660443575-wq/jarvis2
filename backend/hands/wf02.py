# -*- coding: utf-8 -*-
"""
hands/wf02.py — 知识提取
========================
对「处理中」的 U- 理解卡：补全 C- 概念卡 + R- 回忆卡，并把 U- 标记为已固化。
"""
from __future__ import annotations

from .. import core
from ..engine import contract
from ..vault import reader
from . import cards


class WF02(contract.WorkflowBase):
    name = "wf02"
    title = "知识提取"
    desc = "把处理中的 U- 理解卡补全为概念卡 + 回忆卡，固化进知识库。"
    interactive = True

    def precheck(self, payload: dict) -> dict:
        targets = self._targets()
        if not targets:
            return {"ok": False, "error": "没有「处理中」的 U- 理解卡。"}
        return {"ok": True, "count": len(targets)}

    def _targets(self):
        us = reader.search_notes(prefix="U", folder="04_Knowledge", limit=200)
        return [n for n in us if n.fm.get("status") == "处理中"][:3]

    def execute(self, payload: dict) -> dict:
        drafts, updates, notes = [], [], []
        for note in self._targets():
            res = cards.generate_cards(note)
            if not res["ok"]:
                continue
            # 只取 C- 与 R- 卡（U- 已存在，仅更新状态）
            for d in res["drafts"]:
                rel = d["rel_path"]
                if rel.startswith("05_Frameworks/") or rel.startswith("04_Knowledge/回忆卡"):
                    drafts.append(d)
            updates.append({"rel_path": note.rel_path,
                            "fields": {"status": "已固化", "updated": core.today()}})
            notes.append(f"{note.filename} → 已固化")
        if not drafts:
            raise contract.WorkflowError("未能生成概念/回忆卡。")
        return {"drafts": drafts, "updates": updates, "note": "；".join(notes)}

    def verify(self, payload: dict, result: dict) -> dict:
        bad = [d for d in result.get("drafts", []) if not d.get("content")]
        return {"ok": not bad, "errors": ["存在空草稿"] if bad else []}

    def build_report(self, payload: dict, result: dict) -> str:
        return f"知识提取：{result.get('note','')}，产出 {len(result.get('drafts',[]))} 个草稿。"


def register():
    from ..engine import registry
    registry.register(WF02())
