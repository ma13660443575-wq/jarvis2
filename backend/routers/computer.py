# -*- coding: utf-8 -*-
"""localhost 电脑观察接口 + 受确认保护的本机动作。"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..computer import actions, windows

router = APIRouter()


@router.get("/api/computer/status")
def computer_status():
    return {"ok": True, "data": windows.status()}


@router.get("/api/computer/windows")
def computer_windows(limit: int = 20):
    return {"ok": True, "data": windows.list_windows(limit=limit)}


@router.get("/api/computer/ui")
def computer_ui(max_depth: int = 2, max_nodes: int = 80):
    return {"ok": True, "data": windows.ui_tree(max_depth=max_depth, max_nodes=max_nodes)}


@router.post("/api/computer/actions/{action_id}/confirm")
def confirm_computer_action(action_id: str):
    """确认并执行一个短时有效的一次性电脑动作。"""
    from ..agent import task_engine

    if task_engine.action_binding(action_id) is not None:
        return JSONResponse(status_code=409, content={
            "ok": False,
            "data": {
                "error_code": "TASK_CONFIRMATION_REQUIRED",
                "error": "该 action 属于 Desktop Task，请通过 Task Step 确认接口执行",
            },
        })
    result = actions.confirm_action(action_id)
    if not result.get("ok"):
        return JSONResponse(status_code=400, content={"ok": False, "data": result})
    return {"ok": True, "data": result}


@router.post("/api/computer/actions/{action_id}/reject")
def reject_computer_action(action_id: str):
    """拒绝并销毁一个待确认电脑动作。"""
    from ..agent import task_engine

    if task_engine.action_binding(action_id) is not None:
        return JSONResponse(status_code=409, content={
            "ok": False,
            "data": {
                "error_code": "TASK_CONFIRMATION_REQUIRED",
                "error": "该 action 属于 Desktop Task，请通过 Task Step 拒绝接口执行",
            },
        })
    result = actions.reject_action(action_id)
    if not result.get("ok"):
        return JSONResponse(status_code=400, content={"ok": False, "data": result})
    return {"ok": True, "data": result}
