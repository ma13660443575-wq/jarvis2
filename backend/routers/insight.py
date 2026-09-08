# -*- coding: utf-8 -*-
"""
routers/insight.py — 运行洞察（健康度 + token 消耗 + 改进建议）
================================================================
GET /api/insight?days=7 → { evals, usage, suggestions }
数据底座：db/evals.py（evals 埋点表 + llm_usage 记账表）。
建议由规则化生成（不调 LLM），供「健康度卡片」「每日简报」复用。
"""
from __future__ import annotations

from fastapi import APIRouter

from ..db import evals as evals_db

router = APIRouter()

_KIND_LABEL = {"chat": "对话", "command": "命令", "retro": "复盘", "wake": "唤醒"}


def _suggestions(estats: dict) -> list[str]:
    """根据失败记录产出可执行的改进建议（规则化，不调 LLM）。"""
    out: list[str] = []
    by_kind = estats.get("by_kind", {})
    failures = estats.get("failures", [])

    for kind, label in _KIND_LABEL.items():
        b = by_kind.get(kind)
        if not b or b["total"] < 2:
            continue
        if b.get("ok_rate") is not None and b["ok_rate"] < 0.8:
            out.append(f"{label}成功率偏低（{b['ok']}/{b['total']}），建议检查相关链路日志。")
        if b.get("avg_latency_ms") and b["avg_latency_ms"] > 3000:
            out.append(f"{label}平均耗时 {b['avg_latency_ms']:.0f}ms 偏长，可关注性能。")

    if failures:
        recent = failures[0]
        out.append(f"最近一次失败：{recent['kind']} —— {recent['detail'] or '无详情'}（{recent['created_at']}）")

    if not out:
        out.append("各项运行正常，无需调整。")
    return out


@router.get("/api/insight")
def insight(days: int = 7):
    estats = evals_db.eval_stats(days=days)
    ustats = evals_db.usage_stats(days=days)
    return {"ok": True, "data": {
        "days": days,
        "evals": estats,
        "usage": ustats,
        "suggestions": _suggestions(estats),
    }}
