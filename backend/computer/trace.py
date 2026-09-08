# -*- coding: utf-8 -*-
"""Desktop Action 执行追踪。

追踪只记录可定位问题所需的结构化元数据，不记录输入正文、剪贴板、密码或
动作命令。它通过 ContextVar 在 Task Adapter 与现有 Computer Action 之间传递
source/task/step 归属，因此不改变动作 API 和执行顺序。
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Iterator


LOGGER = logging.getLogger("jarvis2.desktop_execution_trace")

TRACE_STAGES = frozenset({
    "PREPARE_START",
    "WINDOW_RESOLVED",
    "CONTROL_PATH_SELECTED",
    "CONFIRMATION_CREATED",
    "CONFIRM_RECEIVED",
    "EXECUTE_START",
    "WINDOW_RELOCATED",
    "FILE_CONTROL_RESOLVED",
    "BEFORE_EXPAND",
    "EXPAND_RETURNED",
    "POPUP_BASELINE_CAPTURED",
    "POPUP_POLL_START",
    "POPUP_CANDIDATE_FOUND",
    "SAVE_ITEM_RESOLVED",
    "SAVE_INVOKED",
    "VERIFY_START",
    "RESULT",
})

_TRACE_CONTEXT: ContextVar[dict | None] = ContextVar(
    "jarvis2_desktop_trace_context", default=None,
)
_ACTIVE_TRACE: ContextVar[object | None] = ContextVar(
    "jarvis2_active_execution_trace", default=None,
)

_ALLOWED_FIELDS = frozenset({
    "source", "task_id", "step_id", "action_id", "action_type",
    "window_target", "control_target", "window", "control", "candidate",
    "candidate_reason", "popup_baseline_count", "popup_current_count",
    "popup_candidate_count", "selected_popup", "save_item_found",
    "timeout_ms", "error_code", "status", "verified", "ok", "changed",
    "invoke_method", "selected_control", "popup_identity",
})


def _safe_scalar(value: object) -> object:
    if isinstance(value, (str, int, float, bool)) or value is None:
        if isinstance(value, str):
            return value[:240]
        return value
    return str(value)[:240]


def _safe_record(record: object) -> dict:
    if not isinstance(record, dict):
        return {}
    allowed = (
        "title", "name", "control_type", "automation_id", "class_name",
        "process_id", "native_hwnd", "native_window_handle", "is_enabled",
        "is_offscreen", "expand_collapse_state", "candidate_reason",
    )
    output = {}
    for key in allowed:
        if key in record and record[key] is not None:
            output[key] = _safe_scalar(record[key])
    if "native_hwnd" not in output and "native_window_handle" in output:
        output["native_hwnd"] = output["native_window_handle"]
    output.pop("native_window_handle", None)
    return output


def window_metadata(window: object) -> dict:
    """提取允许进入 trace 的窗口元数据。"""
    if not isinstance(window, dict):
        return {}
    return _safe_record({
        "title": window.get("title"),
        "class_name": window.get("class_name"),
        "process_id": window.get("process_id"),
        "native_hwnd": window.get("hwnd") or window.get("native_window_handle"),
    })


def control_metadata(control: object, *, candidate_reason: str | None = None) -> dict:
    """提取允许进入 trace 的控件元数据，不带 Value/Text 等内容字段。"""
    result = _safe_record(control)
    if candidate_reason:
        result["candidate_reason"] = str(candidate_reason)[:240]
    return result


@contextmanager
def execution_trace_context(context: dict | None) -> Iterator[None]:
    """在当前同步调用链内传递 trace 归属，不改变现有动作函数签名。"""
    token = _TRACE_CONTEXT.set(dict(context or {}))
    try:
        yield
    finally:
        _TRACE_CONTEXT.reset(token)


def current_execution_trace_context() -> dict:
    return dict(_TRACE_CONTEXT.get() or {})


@contextmanager
def active_execution_trace(trace: "ExecutionTrace | None") -> Iterator[None]:
    """让已有 Windows helper 在不改变函数签名的情况下取得当前 trace。"""
    token = _ACTIVE_TRACE.set(trace)
    try:
        yield
    finally:
        _ACTIVE_TRACE.reset(token)


def current_execution_trace() -> "ExecutionTrace | None":
    trace = _ACTIVE_TRACE.get()
    return trace if isinstance(trace, ExecutionTrace) else None


class ExecutionTrace:
    def __init__(self, context: dict | None = None):
        context = dict(context or {})
        self.execution_trace_id = str(
            context.get("execution_trace_id") or f"execution-trace-{uuid.uuid4().hex[:16]}"
        )
        self._metadata = {
            "source": str(context.get("source") or "standalone_action"),
            "task_id": context.get("task_id"),
            "step_id": context.get("step_id"),
            "action_id": context.get("action_id"),
            "action_type": context.get("action_type"),
        }
        self._started = time.monotonic()

    @property
    def trace_id(self) -> str:
        return self.execution_trace_id

    def bind(self, **fields: object) -> None:
        for key in ("source", "task_id", "step_id", "action_id", "action_type"):
            value = fields.get(key)
            if value is not None and value != "":
                self._metadata[key] = str(value)[:240]

    def emit(self, stage: str, **fields: object) -> None:
        if stage not in TRACE_STAGES:
            return
        payload = {
            "execution_trace_id": self.execution_trace_id,
            "stage": stage,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "monotonic_elapsed_ms": int((time.monotonic() - self._started) * 1000),
            "thread_id": threading.get_ident(),
            "thread_name": threading.current_thread().name,
            "event_loop_context": "sync",
        }
        for key, value in self._metadata.items():
            if value is not None and value != "":
                payload[key] = _safe_scalar(value)
        for key, value in fields.items():
            if key not in _ALLOWED_FIELDS or value is None:
                continue
            if key in {"window", "control", "candidate", "selected_control"}:
                payload[key] = _safe_record(value)
            elif key == "selected_popup":
                payload[key] = bool(value)
            else:
                payload[key] = _safe_scalar(value)
        LOGGER.info("desktop_execution_trace %s", json.dumps(
            payload, ensure_ascii=False, sort_keys=True,
        ))


def new_execution_trace(context: dict | None = None) -> ExecutionTrace:
    inherited = current_execution_trace_context()
    if context:
        inherited.update(context)
    return ExecutionTrace(inherited)
