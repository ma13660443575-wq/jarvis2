# -*- coding: utf-8 -*-
"""
routers/command.py — 命令台
===========================
- GET  /api/workflows            列出全部工作流
- POST /api/command/execute      执行命令（工作流/管道/技能）
- GET  /api/runs                 运行记录
- GET  /api/runs/{id}/drafts     取草稿文件
- POST /api/runs/{id}/confirm    确认草稿（原子写 + 日志）
- POST /api/runs/{id}/reject     拒绝草稿
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..engine import executor, registry
from ..agent import broker
from ..computer import actions as computer_actions
from ..hands import register_all

router = APIRouter()


class CmdIn(BaseModel):
    text: str
    payload: dict = {}


@router.get("/api/workflows")
def workflows():
    register_all()
    return {"ok": True, "data": registry.all_workflows()}


@router.post("/api/command/execute")
def execute(body: CmdIn):
    import time
    from ..db import evals as evals_db
    register_all()
    if not body.text.strip():
        raise HTTPException(400, "命令不能为空")
    _t0 = time.time()
    plan = broker.plan_command(body.text, body.payload or {})
    owner = broker.execution_owner(plan)
    if owner == "desktop_task":
        from ..agent import task_engine

        try:
            started = task_engine.start_task(plan, body.text, session_id="session:default")
        except task_engine.TaskRuntimeError as exc:
            data = {"error_code": exc.code, "error": exc.message}
            if exc.task_result is not None:
                data["task_result"] = exc.task_result.to_dict()
            status = 409 if exc.code == "TASK_ALREADY_ACTIVE" else 400
            return JSONResponse(status_code=status, content={"ok": False, "data": data})
        return {"ok": True, "data": {
            "type": "desktop_task",
            "status": started["task_result"]["status"].lower(),
            "task_plan": started["task_plan"],
            "task_result": started["task_result"],
        }}
    if owner == "computer_action":
        from ..agent import task_engine

        try:
            task_engine.ensure_desktop_execution_available("session:default")
        except task_engine.TaskRuntimeError as exc:
            action_result = computer_actions.build_terminal_failure_result(
                plan, {"error_code": exc.code, "error": exc.message},
            )
            return JSONResponse(
                status_code=409,
                content={"ok": False, "data": {
                    "type": "computer_action_result",
                    "status": "failed",
                    "computer_action_owned": True,
                    "error_code": exc.code,
                    "error": exc.message,
                    "action_result": action_result,
                    "action_plan": {
                        **plan,
                        "status": "failed",
                        "error_code": exc.code,
                        "error": exc.message,
                    },
                }},
            )
        pending = computer_actions.create_pending_action(plan, body.text)
        if not pending.get("ok"):
            action_result = computer_actions.build_terminal_failure_result(plan, pending)
            return JSONResponse(
                status_code=400,
                content={"ok": False, "data": {
                    "type": "computer_action_result",
                    "status": "failed",
                    "computer_action_owned": True,
                    "action_result": action_result,
                    "action_plan": {
                        **plan,
                        **pending,
                        "status": "failed",
                    },
                }},
            )
        action_plan = {**plan, **pending, "status": "waiting_confirmation"}
        try:
            evals_db.record_eval(
                "command", ok=True,
                latency_ms=int((time.time() - _t0) * 1000),
                meta={"action": plan.get("action") or "computer", "status": "waiting_confirmation"},
            )
        except Exception:  # noqa: BLE001
            pass
        return {"ok": True, "data": {
            "ok": True,
            "type": "computer_action",
            "status": "waiting_confirmation",
            "action_plan": action_plan,
        }}
    exec_payload = dict(body.payload or {})
    # 计划 ID 写入现有 runs.payload，和工作流审计记录关联；不把输入内容复制到计划里。
    exec_payload["_plan_id"] = plan.get("plan_id")
    r = executor.execute_command(body.text, exec_payload)
    r["action_plan"] = broker.finalize_plan(plan, r)
    try:
        evals_db.record_eval(
            "command", ok=bool(r.get("ok")),
            latency_ms=int((time.time() - _t0) * 1000),
            meta={"workflow": r.get("type") or "", "status": r.get("status") or ""},
        )
    except Exception:  # noqa: BLE001
        pass
    if not r["ok"]:
        raise HTTPException(400, r.get("error", "执行失败"))
    return {"ok": True, "data": r}


@router.get("/api/runs")
def runs(status: str | None = None):
    return {"ok": True, "data": executor.list_runs(status=status)}


@router.get("/api/runs/{run_id}/drafts")
def run_drafts(run_id: int):
    return {"ok": True, "data": executor.get_draft_files(run_id)}


@router.post("/api/runs/{run_id}/confirm")
def confirm(run_id: int):
    r = executor.confirm_run(run_id)
    if not r["ok"]:
        raise HTTPException(400, r.get("error", "确认失败"))
    return {"ok": True, "data": r}


@router.post("/api/runs/{run_id}/reject")
def reject(run_id: int):
    r = executor.reject_run(run_id)
    if not r["ok"]:
        raise HTTPException(400, r.get("error", "拒绝失败"))
    return {"ok": True, "data": r}
