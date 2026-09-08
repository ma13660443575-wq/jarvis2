# -*- coding: utf-8 -*-
"""
routers/retro.py — 复盘页数据聚合
==================================
GET /api/retro → 复盘总览：
  - recent_retros  近期 L- 复盘卡
  - skillizable    可技能化的概念卡（C- 且尚未沉淀 S- 技能）
  - review_stats   复习趋势（近 7 天 / 今日）
  - recent_runs    近期工作流运行
"""
from __future__ import annotations

import json

from fastapi import APIRouter

from .. import core, db
from ..db import evals as evals_db
from ..skills import registry as skills_reg
from ..vault import reader, scan

router = APIRouter()


@router.get("/api/retro")
@evals_db.recorded("retro")
def retro_overview():
    retros = scan.scan_retros(limit=12)

    # 可技能化的概念卡：C- 卡中尚无对应 S- 技能（按 source 匹配）
    skills = skills_reg.all_skills()
    skill_sources = {str(s.get("source_card") or "") for s in skills}
    skill_names = {str(s.get("name") or "") for s in skills}
    concepts = scan.scan_concepts(limit=200)
    skillizable = []
    for c in concepts:
        if c.wikilink in skill_sources:
            continue
        name = c.title.replace("C-", "").strip()
        if name in skill_names:
            continue
        skillizable.append({"rel_path": c.rel_path, "title": c.title,
                            "summary": c.body[:80].replace("\n", " ").strip(),
                            "created": str(c.fm.get("created") or "")})
    skillizable = skillizable[:20]

    # 复习统计
    today = core.today()
    con = db.schema.get_conn()
    try:
        rows = con.execute(
            "SELECT card_rel, quality, due, created_at FROM review_log "
            "ORDER BY id DESC LIMIT 50"
        ).fetchall()
        total_graded = len(rows)
        today_graded = sum(1 for r in rows if (r["created_at"] or "").startswith(today))
        avg_quality = round(sum(r["quality"] for r in rows) / total_graded, 2) if total_graded else None
        due_today = con.execute(
            "SELECT COUNT(*) AS c FROM review_log WHERE date(due)=?", (today,)
        ).fetchone()["c"]
    finally:
        con.close()

    # 近期运行
    from ..engine import contract
    runs = contract.list_runs(limit=8)

    return {"ok": True, "data": {
        "today": today,
        "recent_retros": [
            {"rel_path": n.rel_path, "title": n.title,
             "summary": n.body[:120].replace("\n", " ").strip(),
             "created": str(n.fm.get("created") or "")}
            for n in retros
        ],
        "skillizable": skillizable,
        "review_stats": {
            "total_graded": total_graded,
            "today_graded": today_graded,
            "avg_quality": avg_quality,
            "due_today": due_today,
        },
        "recent_runs": runs,
        "skills_count": len(skills),
        "inbox_pending": len([n for n in scan.scan_inbox() if (n.fm.get("status") or "未处理") != "已处理"]),
    }}
