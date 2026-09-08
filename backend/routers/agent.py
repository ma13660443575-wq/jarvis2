# -*- coding: utf-8 -*-
"""Agent 能力目录与计划预览接口（只读规划，不绕过现有执行器）。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..agent import broker
from ..hands import register_all

router = APIRouter()


class PlanIn(BaseModel):
    text: str
    payload: dict = {}


class TaskActionIn(BaseModel):
    action_id: str


@router.get("/api/capabilities")
def capabilities():
    """当前真正注册并可被 Agent 选择的能力。"""
    register_all()
    return {"ok": True, "data": broker.list_capabilities()}


@router.post("/api/agent/plan")
def plan(body: PlanIn):
    """预览输入会走哪条能力链；不会执行命令或写入 vault。"""
    if not body.text.strip():
        raise HTTPException(400, "计划输入不能为空")
    register_all()
    return {"ok": True, "data": broker.plan_command(body.text, body.payload or {})}


def _task_error(exc):
    status = 400
    if exc.code == "TASK_NOT_FOUND":
        status = 404
    elif exc.code in {"TASK_ALREADY_ACTIVE", "TASK_STEP_MISMATCH", "TASK_ACTION_MISMATCH",
                      "TASK_NOT_WAITING_CONFIRMATION", "STEP_NOT_WAITING_CONFIRMATION"}:
        status = 409
    data = {"error_code": exc.code, "error": exc.message}
    if exc.task_result is not None:
        data["task_result"] = exc.task_result.to_dict()
    return JSONResponse(status_code=status, content={"ok": False, "data": data})


@router.get("/api/agent/tasks/{task_id}")
def get_task(task_id: str):
    """读取一个进程内 Desktop Task 的安全状态摘要。"""
    from ..agent import task_engine

    try:
        return {"ok": True, "data": task_engine.get_task(task_id)}
    except task_engine.TaskRuntimeError as exc:
        return _task_error(exc)


@router.post("/api/agent/tasks/{task_id}/steps/{step_id}/confirm")
def confirm_task_step(task_id: str, step_id: str, body: TaskActionIn):
    """按 task_id + step_id + action_id 三元绑定确认当前 TaskStep。"""
    from ..agent import task_engine

    try:
        data = task_engine.confirm_step(task_id, step_id, body.action_id)
        return {"ok": True, "data": data}
    except task_engine.TaskRuntimeError as exc:
        return _task_error(exc)


@router.post("/api/agent/tasks/{task_id}/steps/{step_id}/reject")
def reject_task_step(task_id: str, step_id: str, body: TaskActionIn):
    """按 task_id + step_id + action_id 三元绑定拒绝当前 TaskStep。"""
    from ..agent import task_engine

    try:
        data = task_engine.reject_step(task_id, step_id, body.action_id)
        return {"ok": True, "data": data}
    except task_engine.TaskRuntimeError as exc:
        return _task_error(exc)
