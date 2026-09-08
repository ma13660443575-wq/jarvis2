# -*- coding: utf-8 -*-
"""
hands/wf01.py — 处理采集箱
==========================
把 00_Inbox 中「未处理」的 I- 采集卡 → U/C/R 三卡草稿 → 确认后原子写 +
原卡标记已处理 + 追加训练日志。确认前零写入。
"""
from __future__ import annotations

from .. import core
from ..engine import contract
from ..vault import paths, reader
from . import cards

_MAX_PER_RUN = 3


class WF01(contract.WorkflowBase):
    name = "wf01"
    title = "处理采集箱"
    desc = "把未处理的 I- 采集卡整理成 U/C/R 三卡，沉淀进知识库。"
    interactive = True

    def _pending(self, payload: dict) -> list:
        inbox = reader.search_notes(prefix="I", folder="00_Inbox", limit=200)
        pending = [n for n in inbox if (n.fm.get("status") or "未处理") != "已处理"]
        # 支持指定单卡处理（payload.inbox_path），便于精确控制
        only = (payload or {}).get("inbox_path")
        if only:
            pending = [n for n in pending if n.rel_path == only]
        # 优先处理最早的（created 可能是 date 对象或字符串，统一转 str）
        pending.sort(key=lambda n: (str(n.fm.get("created") or ""), n.rel_path))
        return pending

    def precheck(self, payload: dict) -> dict:
        pending = self._pending(payload)
        if not pending:
            return {"ok": False, "error": "采集箱没有待处理的 I- 卡片。"}
        return {"ok": True, "pending_count": len(pending)}

    def execute(self, payload: dict) -> dict:
        pending = self._pending(payload)
        targets = pending[:_MAX_PER_RUN]

        drafts = []
        updates = []
        notes = []
        for note in targets:
            res = cards.generate_cards(note)
            if not res["ok"]:
                continue
            drafts.extend(res["drafts"])
            updates.append({"rel_path": note.rel_path,
                            "fields": {"status": "已处理", "updated": core.today()}})
            notes.append(f"{note.filename} → {res['domain']}")

        if not drafts:
            raise contract.WorkflowError("未能生成任何卡片，请检查采集卡内容。")
        return {"drafts": drafts, "updates": updates, "note": "；".join(notes)}

    def verify(self, payload: dict, result: dict) -> dict:
        errors = []
        for d in result.get("drafts", []):
            if not d.get("rel_path") or not d.get("content"):
                errors.append("存在空草稿")
                break
            # 路径必须落在契约目录
            prefix = d["rel_path"].split("/")[0]
            if prefix not in ("04_Knowledge", "05_Frameworks"):
                errors.append("草稿路径未对齐契约目录: " + d["rel_path"])
        return {"ok": len(errors) == 0, "errors": errors}

    def build_report(self, payload: dict, result: dict) -> str:
        notes = result.get("note", "")
        n = len(result.get("drafts", []))
        return f"处理采集箱：{notes}，共生成 {n} 个文件草稿。"


def register():
    from ..engine import registry
    registry.register(WF01())
