# -*- coding: utf-8 -*-
"""
routers/wake.py — 全局后台唤醒 agent 联动
==========================================
主应用后端与 jarvis2-wake-agent（127.0.0.1:8123）对接的端点：
- GET  /api/wake/status        转发 agent 状态（离线返回 state=offline）
- POST /api/wake/command       接收 agent 唤醒指令 → 复用 chat_stream 全流程 → 返回回复文本
- POST /api/wake/register      后端启动时向 agent 上报实际端口（agent 离线静默）
- POST /api/wake/toggle        开/关 agent 常驻监听
- POST /api/wake/pause|resume  PTT 录音期间暂停/恢复 agent 监听（防双麦冲突）
- GET/POST /api/wake/hud       HUD 窗口可见状态（供 agent 决定是否自行朗读回复）
"""
from __future__ import annotations

import logging
from threading import Lock

import requests
from fastapi import APIRouter
from pydantic import BaseModel

from .. import config
from ..agent import broker
from ..brain import answer
from ..heart import notify

log = logging.getLogger("jarvis2.wake")
router = APIRouter()

_AGENT_BASE = f"http://127.0.0.1:{config.WAKE_AGENT_PORT}"

# HUD 窗口可见状态（前端 App 挂载时通过 visibilitychange 上报）
_hud_open = False
_hud_lock = Lock()


class WakeCmdIn(BaseModel):
    text: str
    source: str = "wake"


class WakeRegIn(BaseModel):
    backend_port: int
    app_version: str = ""


class WakeToggleIn(BaseModel):
    enabled: bool


def agent_online() -> bool:
    try:
        r = requests.get(f"{_AGENT_BASE}/status", timeout=1.5)
        return r.ok
    except Exception:  # noqa: BLE001
        return False


def _agent_get(path: str, timeout: float = 1.5) -> dict | None:
    try:
        r = requests.get(f"{_AGENT_BASE}{path}", timeout=timeout)
        return r.json() if r.ok else None
    except Exception:  # noqa: BLE001
        return None


def _agent_post(path: str, data: dict | None = None, timeout: float = 1.5) -> dict | None:
    try:
        r = requests.post(f"{_AGENT_BASE}{path}", json=data or {}, timeout=timeout)
        return r.json() if r.ok else None
    except Exception:  # noqa: BLE001
        return None


@router.get("/api/wake/status")
def wake_status():
    """转发 agent 状态；agent 离线时返回 offline。"""
    st = _agent_get("/status")
    if st is None:
        return {"ok": True, "data": {
            "state": "offline", "enabled": False, "agent_online": False,
            "backend_port": None, "model_ready": False, "last_command": None,
            "hud_open": _hud_open,
        }}
    data = st.get("data", st)
    data["agent_online"] = True
    data["hud_open"] = _hud_open
    return {"ok": True, "data": data}


@router.post("/api/wake/command")
def wake_command(body: WakeCmdIn):
    """agent 唤醒后的指令入口：与 HUD 手动发送走同一条 chat_stream 全流程。

    返回 {ok, reply, command, action_plan}：reply 为完整回复文本（供 agent/HUD 朗读），
    action_plan 用于让 localhost HUD 展示并确认需要用户授权的电脑动作。
    同时向 SSE 发布 wake 事件（HUD 打开时前端收到后朗读回复）。
    """
    import time
    from ..db import evals as evals_db
    text = (body.text or "").strip()
    if not text:
        return {"ok": False, "detail": "指令为空"}

    reply_parts: list[str] = []
    command: dict | None = None
    action_plan: dict | None = None
    action_result: dict | None = None
    task_result: dict | None = None
    _t0 = time.time()
    try:
        for evt in answer.chat_stream(text, None):
            et = evt.get("type")
            if et == "delta":
                reply_parts.append(str(evt.get("text", "")))
            elif et == "done":
                command = evt.get("command")
                action_plan = evt.get("action_plan")
                action_result = evt.get("action_result")
                task_result = evt.get("task_result")
    except Exception as e:  # noqa: BLE001
        log.exception("[wake/command] 处理失败")
        try:
            evals_db.record_eval("wake", False, latency_ms=int((time.time() - _t0) * 1000),
                                 detail=f"处理失败: {e}", meta={"source": body.source})
        except Exception:  # noqa: BLE001
            pass
        return {"ok": False, "detail": f"指令处理失败：{e}"}

    reply = "".join(reply_parts).strip() or "好的，已处理。"
    # 广播到 SSE：HUD 打开时前端据此朗读；agent 自己判断 hud_open 决定是否代读
    notify.publish("wake", {
        "state": "reply", "text": reply, "source": body.source,
        "action_plan": action_plan,
        "action_result": action_result,
        "task_result": task_result,
    })
    try:
        evals_db.record_eval("wake", True, latency_ms=int((time.time() - _t0) * 1000),
                             meta={"source": body.source})
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "data": {
        "reply": reply, "command": command, "action_plan": action_plan,
        "action_result": action_result, "task_result": task_result,
    }}


@router.post("/api/wake/register")
def wake_register(body: WakeRegIn):
    """后端启动时向 agent 上报实际端口（agent 离线则静默，下次启动再注册）。"""
    if not config.WAKE_ENABLED:
        return {"ok": True, "data": {"registered": False, "reason": "wake disabled"}}
    r = _agent_post("/register", {"backend_port": body.backend_port, "app_version": body.app_version})
    return {"ok": True, "data": {"registered": r is not None}}


@router.post("/api/wake/toggle")
def wake_toggle(body: WakeToggleIn):
    """开/关 agent 常驻监听。"""
    r = _agent_post("/set_enabled", {"enabled": body.enabled})
    if r is None:
        return {"ok": False, "detail": "唤醒 agent 未运行（未安装或未启动）"}
    return {"ok": True, "data": r.get("data", r)}


@router.post("/api/wake/pause")
def wake_pause():
    """PTT 录音期间暂停 agent 监听，避免两路麦克风处理冲突。"""
    _agent_post("/pause")
    return {"ok": True}


@router.post("/api/wake/resume")
def wake_resume():
    """PTT 结束恢复 agent 监听。"""
    _agent_post("/resume")
    return {"ok": True}


@router.get("/api/wake/hud")
def hud_status():
    return {"ok": True, "data": {"open": _hud_open}}


@router.post("/api/wake/hud")
def hud_set(body: dict):
    global _hud_open
    with _hud_lock:
        _hud_open = bool(body.get("open", False))
    return {"ok": True, "data": {"open": _hud_open}}
