# -*- coding: utf-8 -*-
"""
hands/wf07.py — 系统体检
========================
只读诊断：vault 状态、路径契约、向量索引、待办、到期复习、待处理采集。
"""
from __future__ import annotations

from .. import core
from .. import config
from ..engine import contract
from ..vault import paths, reader


class WF07(contract.WorkflowBase):
    name = "wf07"
    title = "系统体检"
    desc = "检查 JARVIS 各子系统健康状况。"
    interactive = False

    def execute(self, payload: dict) -> dict:
        stats = paths.vault_stats()
        contract_res = paths.check_contract()
        inbox = [n for n in reader.search_notes(prefix="I", folder="00_Inbox", limit=200)
                 if (n.fm.get("status") or "未处理") != "已处理"]
        today = core.today()
        due = [n for n in reader.search_notes(prefix="R", folder="04_Knowledge", limit=500)
               if (str(n.fm.get("due") or "").strip() and
                   str(n.fm.get("due") or "").strip() <= today)]
        today_due = sum(1 for n in due if str(n.fm.get("due") or "").strip() == today)

        report = (
            f"📊 JARVIS 系统体检\n"
            f"- vault 笔记总数：{stats.get('total_notes', 0)}\n"
            f"- 路径契约：{'✓ 完整' if contract_res['ok'] else '⚠ 缺失 ' + str(len(contract_res['missing']))}\n"
            f"- 待处理采集：{len(inbox)} 条\n"
            f"- 今日到期复习：{today_due} 张\n"
            f"- 向量索引：{'已启用（Qdrant 本地）' if config.VECTOR_ENABLED else '未启用（关键词检索）'}\n"
        )
        return {"drafts": [], "updates": [],
                "payload_out": {"report": report, "stats": stats,
                                "contract": contract_res, "inbox": len(inbox), "due_today": today_due},
                "note": "体检完成"}

    def verify(self, payload: dict, result: dict) -> dict:
        return {"ok": True}

    def build_report(self, payload: dict, result: dict) -> str:
        return result.get("payload_out", {}).get("report", "体检完成")


def register():
    from ..engine import registry
    registry.register(WF07())
