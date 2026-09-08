# -*- coding: utf-8 -*-
"""
review/sm2.py — 轻量 SM-2 复习调度
====================================
对 R- 回忆卡打分（0-5）→ 更新 interval/easiness/due → 写回 vault frontmatter
→ 追加 review_log 记录。公式对齐 vault 系统说明的轻量 SM-2。
"""
from __future__ import annotations

import math
from datetime import date, timedelta

from .. import core, db
from ..vault import reader, writer


def _next_schedule(interval: float, easiness: float, quality: int) -> tuple[float, float, int]:
    """返回 (new_interval_days, new_easiness, new_interval_in_days)。quality 0-5。"""
    easiness = max(1.3, easiness + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)))
    if quality < 3:
        return 1, easiness, 1
    if interval == 0:
        nxt = 1
    elif interval == 1:
        nxt = 6
    else:
        nxt = max(1, round(interval * easiness))
    return nxt, easiness, nxt


def grade_card(rel_path: str, quality: int) -> dict:
    """给一张 R- 卡打分并调度。quality 0-5（0=全忘，5=完美）。"""
    note = reader.get_note(rel_path)
    if note is None:
        return {"ok": False, "error": "卡片不存在: %s" % rel_path}
    if note.prefix != "R":
        return {"ok": False, "error": "只能给回忆卡（R-）打分"}
    if quality not in range(6):
        return {"ok": False, "error": "评分需在 0-5 之间"}

    try:
        interval = float(note.fm.get("interval") or 0)
    except (TypeError, ValueError):
        interval = 0.0
    try:
        easiness = float(note.fm.get("easiness") or 2.5)
    except (TypeError, ValueError):
        easiness = 2.5

    nxt, easiness, _ = _next_schedule(interval, easiness, quality)
    due = (date.fromisoformat(core.today()) + timedelta(days=nxt)).isoformat()

    # 写回 vault frontmatter
    w = writer.update_frontmatter(rel_path, {
        "interval": nxt, "easiness": round(easiness, 2), "due": due,
        "updated": core.today(),
    })
    if not w.get("ok"):
        return {"ok": False, "error": w.get("error", "写入失败")}

    # 记 review_log
    con = db.schema.get_conn()
    try:
        con.execute(
            "INSERT INTO review_log (card_rel, quality, interval, easiness, due, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (rel_path, quality, nxt, round(easiness, 2), due, core.now_iso()),
        )
        con.commit()
    finally:
        con.close()

    return {"ok": True, "rel_path": rel_path, "interval": nxt,
            "easiness": round(easiness, 2), "due": due}


def due_cards(today: str | None = None) -> list[dict]:
    """今日到期（含过期）的回忆卡清单。"""
    today = today or core.today()
    rs = reader.search_notes(prefix="R", folder="04_Knowledge", limit=500)
    out = []
    for n in rs:
        due = str(n.fm.get("due") or "")
        if due and due <= today:
            out.append({
                "rel_path": n.rel_path,
                "question": next((l for l in n.body.splitlines() if l.strip().startswith("问：")), "") or n.title,
                "answer": next((l for l in n.body.splitlines() if l.strip().startswith("答：")), ""),
                "linked_card": n.fm.get("linked_card") or "",
                "interval": n.fm.get("interval") or 0,
                "due": due,
            })
    out.sort(key=lambda x: x["due"])
    return out
