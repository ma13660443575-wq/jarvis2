# -*- coding: utf-8 -*-
"""
heart/briefing.py — 每日简报生成
================================
把 JARVIS 当日状态聚合成人话简报（待办采集 / 到期复习 / 待确认记忆 / 任务 / 技能），
写入 briefings 表（date UNIQUE，同一天只生成一次），供 Dashboard 展示。

Dashboard 的「今日简报」卡片读取这里生成的内容；实时数据另由 /api/dashboard 提供。
"""
from __future__ import annotations

import json

from .. import core, db


def build_briefing() -> str:
    """聚合当日状态，返回一份可读的中文简报文本。"""
    from ..vault import paths, reader
    from ..review import sm2
    from ..db import ops as db_ops
    from ..engine import contract as contract_mod
    from ..skills import registry as skills_reg
    from .. import core as _core

    stats = paths.vault_stats()
    contract = paths.check_contract()
    today = _core.today()

    inbox_pending = len([n for n in reader.search_notes(prefix="I", folder="00_Inbox", limit=300)
                         if (n.fm.get("status") or "未处理") != "已处理"])
    due = sm2.due_cards(today)
    mem = db_ops.list_memory_candidates(status="pending", limit=20)
    tasks = db_ops.list_tasks(limit=12)
    skills = skills_reg.all_skills()

    lines: list[str] = []
    lines.append(f"JARVIS · 每日简报 · {today}")

    # 采集箱
    if inbox_pending:
        lines.append(f"- 📥 采集箱有 **{inbox_pending} 条**待处理，建议去命令台跑「处理采集箱」。")
    else:
        lines.append("- 📥 采集箱已清空。")

    # 复习
    if due:
        lines.append(f"- ↻ 今天有 **{len(due)} 张**回忆卡到期，花几分钟复习一下。")
    else:
        lines.append("- ↻ 今日无到期复习卡。")

    # 记忆
    if mem:
        lines.append(f"- 🧠 有 **{len(mem)} 条**记忆待确认，去对话页或右侧控制面板确认。")
    else:
        lines.append("- 🧠 暂无待确认记忆。")

    # 任务
    doing = [t for t in tasks if t.get("status") == "进行中"]
    todo = [t for t in tasks if t.get("status") == "待办"]
    if doing:
        lines.append("- ⚡ 进行中任务：%s。" % "、".join(t["title"] for t in doing[:3]))
    if todo:
        lines.append("- 🗒 待办 %d 条：%s。" % (len(todo), "、".join(t["title"] for t in todo[:3])))

    # 技能
    if skills:
        lines.append("- ⬡ 技能库 %d 个技能可用：%s。" % (len(skills), "、".join(s["name"] for s in skills[:5])))
    else:
        lines.append("- ⬡ 还没有技能卡，试试「技能化一条概念卡」。")

    # 契约
    if contract["ok"]:
        lines.append("- 🛰 路径契约完整，知识库写入与你的 vault 完全打通。")
    else:
        lines.append("- 🛰 路径契约有 %d 个目录缺失（不自动创建，避免污染）。" % len(contract["missing"]))

    # 健康度 + token 消耗（自触发系统体检：数据来自 evals/llm_usage 埋点）
    try:
        from ..db import evals as evals_db
        u = evals_db.usage_stats(days=1)
        e = evals_db.eval_stats(days=1)
        parts: list[str] = []
        if u.get("today_tokens"):
            parts.append(f"今日已消耗 {u['today_tokens']:,} token")
        by_kind = e.get("by_kind", {})
        for kind, label in (("chat", "对话"), ("command", "命令"), ("wake", "唤醒")):
            b = by_kind.get(kind)
            if b and b.get("ok_rate") is not None:
                parts.append(f"{label}成功率 {b['ok_rate'] * 100:.0f}%")
        if parts:
            lines.append("- 📊 健康度：" + " · ".join(parts) + "。")
        fails = e.get("failures", [])
        if fails:
            f0 = fails[0]
            lines.append(f"- 💡 体检建议：近1天有 {len(fails)} 次失败，最近一次「{f0['kind']}」——{f0['detail'] or '无详情'}。")
    except Exception:  # noqa: BLE001
        pass

    total = stats.get("total_notes", 0)
    lines.append(f"- 🗄 知识库共 {total} 篇笔记。")
    return "\n".join(lines)


def generate(day: str | None = None) -> dict:
    """生成（或覆盖）某天简报。返回 {ok, date, content}。"""
    day = day or core.today()
    content = build_briefing()
    con = db.schema.get_conn()
    try:
        con.execute(
            "INSERT OR REPLACE INTO briefings (date, content, raw, created_at) VALUES (?,?,?,?)",
            (day, content, json.dumps({"today": day}, ensure_ascii=False), core.now_iso()),
        )
        con.commit()
        # 简报已更新（SSE 实时推送）
        try:
            from ..heart import notify
            notify.publish("briefing", {"date": day})
        except Exception:  # noqa: BLE001
            pass
        return {"ok": True, "date": day, "content": content}
    finally:
        con.close()


def get_today(force: bool = False) -> dict:
    """取今日简报；没有则即时生成（懒生成，保证每天首次打开即有简报）。"""
    day = core.today()
    con = db.schema.get_conn()
    try:
        row = con.execute("SELECT content FROM briefings WHERE date=?", (day,)).fetchone()
    finally:
        con.close()
    if row is None or force:
        return generate(day)
    return {"ok": True, "date": day, "content": row[0], "cached": True}
