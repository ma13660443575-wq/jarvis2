# -*- coding: utf-8 -*-
"""
routers/app.py — 认知 OS 数据接口
=================================
- GET  /api/dashboard            仪表盘聚合
- GET  /api/skills               技能列表
- GET  /api/skills/{slug}        单个技能详情
- GET  /api/review/today         今日到期复习卡
- POST /api/review/grade         复习打分（SM-2 调度）
- GET  /api/tasks / POST /api/tasks / PATCH /api/tasks/{id} / DELETE /api/tasks/{id}
- GET  /api/knowledge/tree       vault 知识库目录树
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import core, db
from ..db import ops as db_ops
from ..review import sm2
from ..skills import registry as skills_reg
from ..vault import paths, reader

router = APIRouter()


# ============ 仪表盘 ============

@router.get("/api/dashboard")
def dashboard():
    from ..hands import register_all
    register_all()
    stats = paths.vault_stats()
    contract = paths.check_contract()
    today = core.today()

    inbox_pending = len([n for n in reader.search_notes(prefix="I", folder="00_Inbox", limit=300)
                         if (n.fm.get("status") or "未处理") != "已处理"])
    due = sm2.due_cards(today)
    mem = db_ops.list_memory_candidates(status="pending", limit=20)
    tasks = db_ops.list_tasks(limit=12)
    from ..engine import contract as contract_mod
    runs = contract_mod.list_runs(limit=8)
    skills = skills_reg.all_skills()

    return {
        "ok": True,
        "data": {
            "vault": stats,
            "contract": contract,
            "inbox_pending": inbox_pending,
            "due_today": len(due),
            "due_cards": due[:8],
            "memory_pending": len(mem),
            "memory_candidates": mem[:8],
            "tasks": tasks,
            "recent_runs": runs,
            "skills": skills,
            "today": today,
        },
    }


# ============ 简报 ============

@router.get("/api/briefing")
def briefing():
    from ..heart import briefing as briefing_mod
    return {"ok": True, "data": briefing_mod.get_today()}


# ============ 技能 ============

@router.get("/api/skills")
def skills_list():
    from ..hands import register_all
    register_all()
    return {"ok": True, "data": skills_reg.all_skills()}


@router.get("/api/skills/{slug}")
def skill_detail(slug: str):
    from ..hands import register_all
    register_all()
    sk = skills_reg.get_skill(slug)
    if sk is None:
        raise HTTPException(404, "技能不存在")
    return {"ok": True, "data": {
        **sk.to_dict(),
        "rel_path": sk.rel_path, "body": sk.body,
        "flow": sk.flow, "out_req": sk.out_req, "one_liner": sk.one_liner,
    }}


# ============ 复习 ============

@router.get("/api/review/today")
def review_today():
    return {"ok": True, "data": sm2.due_cards()}


class GradeIn(BaseModel):
    rel_path: str
    quality: int


@router.post("/api/review/grade")
def review_grade(body: GradeIn):
    r = sm2.grade_card(body.rel_path, body.quality)
    if not r["ok"]:
        raise HTTPException(400, r.get("error", "打分失败"))
    return {"ok": True, "data": r}


# ============ 任务 ============

@router.get("/api/tasks")
def tasks_list(status: str | None = None):
    return {"ok": True, "data": db_ops.list_tasks(status=status)}


class TaskIn(BaseModel):
    title: str
    priority: str = "中"
    project: str | None = None


@router.post("/api/tasks")
def tasks_create(body: TaskIn):
    if not body.title.strip():
        raise HTTPException(400, "任务标题不能为空")
    r = db_ops.add_task(body.title.strip(), body.priority, body.project)
    return {"ok": True, "data": r}


class TaskStatusIn(BaseModel):
    status: str


@router.patch("/api/tasks/{tid}")
def tasks_update(tid: int, body: TaskStatusIn):
    r = db_ops.set_task_status(tid, body.status)
    if not r["ok"]:
        raise HTTPException(400, r.get("error", "更新失败"))
    return {"ok": True, "data": r}


@router.delete("/api/tasks/{tid}")
def tasks_delete(tid: int):
    r = db_ops.delete_task(tid)
    if not r["ok"]:
        raise HTTPException(404, "任务不存在")
    return {"ok": True}


# ============ 知识库 ============

@router.get("/api/knowledge/tree")
def knowledge_tree():
    """vault 目录树（两级）+ 每节点笔记数与前缀分布。"""
    notes = reader.scan_vault()
    tree: dict[str, dict] = {}
    for n in notes:
        parts = n.rel_path.split("/")
        if len(parts) < 2:
            continue  # 跳过 vault 根目录的散落文件（如 CLAUDE.md）
        top = parts[0]
        if top not in tree:
            tree[top] = {"dirs": {}, "count": 0, "prefixes": {}}
        tree[top]["count"] += 1
        tree[top]["prefixes"][n.prefix or "-"] = tree[top]["prefixes"].get(n.prefix or "-", 0) + 1
        if len(parts) > 2:
            sub = parts[1]
            d = tree[top]["dirs"].setdefault(sub, {"count": 0})
            d["count"] += 1
        elif len(parts) == 2:
            sub = "（根）"
            d = tree[top]["dirs"].setdefault(sub, {"count": 0})
            d["count"] += 1
    return {"ok": True, "data": tree}
