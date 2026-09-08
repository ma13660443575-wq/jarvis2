# -*- coding: utf-8 -*-
"""4A Desktop Task Runtime。

Runtime 只编排已验证的有限 TaskPlan；桌面动作、确认、重新定位和 Verify 全部
继续委托给 backend.computer.actions，不在这里接触 UI Automation。
"""
from __future__ import annotations

import time
from typing import Any

from ..computer import actions as computer_actions
from . import broker
from . import task_planner
from .task_models import (
    StepResult,
    StepState,
    TaskContext,
    TaskPlanModel,
    TaskRecord,
    TaskResult,
    TaskState,
    TaskStateData,
    model_dump,
)
from .task_state import TaskStateError, transition_step, transition_task
from .task_store import store


class TaskRuntimeError(ValueError):
    def __init__(self, code: str, message: str, *, task_result: TaskResult | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.task_result = task_result


_ACTION_TYPES = {
    "activate": "activate_window",
    "input": "set_text",
    "click": "click_control",
}
_ACTIVE_CONFIRMATION_STATES = {TaskState.WAITING_CONFIRMATION}


def _session_id(value: object) -> str:
    return str(value).strip() if value is not None and str(value).strip() else "chat:default"


def ensure_desktop_execution_available(session_id: object = None) -> None:
    """在创建新的 Desktop Action 前检查同 session 的 Task ownership。

    这里只观察 Task Store，不创建 Task、不创建 pending action，也不影响普通
    Conversation。Task 自身的启动仍保留 TASK_ALREADY_ACTIVE 语义。
    """
    session = _session_id(session_id)
    with store.locked():
        active = store.active_for_session(session)
        if active is not None:
            raise TaskRuntimeError(
                "DESKTOP_EXECUTION_BUSY",
                f"当前 session 已有未结束的 Desktop Task：{active.plan.task_id}",
                task_result=_result(active),
            )


def _task_status_key(state: TaskState) -> str:
    return state.value.lower()


def _step_public(step, result: StepResult | None = None) -> dict[str, Any]:
    raw = model_dump(step)
    raw["id"] = step.step_id
    if result is not None:
        raw["status"] = result.status
        raw["error_code"] = result.error_code
        raw["verified"] = result.verified
        raw["action_id"] = result.action_id
        raw["save_state"] = result.save_state
    else:
        raw["status"] = step.status.value
    return {
        "id": raw.get("id"),
        "step_id": raw.get("step_id"),
        "order": raw.get("order"),
        "action_type": raw.get("action_type"),
        "window_target": raw.get("window_target"),
        "control_target": raw.get("control_target"),
        "content_ref": raw.get("content_ref"),
        "requires_confirmation": raw.get("requires_confirmation", True),
        "status": raw.get("status"),
        "title": raw.get("title", ""),
        "capability_id": {
            "activate": "computer.activate_window",
            "input": "computer.set_text",
            "click": "computer.click_control",
        }.get(raw.get("action_type"), ""),
        **({key: raw[key] for key in ("error_code", "verified", "action_id", "save_state")
            if key in raw and raw[key]}),
    }


def public_plan(record: TaskRecord) -> dict[str, Any]:
    """生成不含输入正文的前端计划。"""
    current = record.state.current_step_id
    steps = []
    for step in record.plan.steps:
        result = record.step_results.get(step.step_id)
        steps.append(_step_public(step, result))
    out = {
        "plan_id": record.plan.task_id,
        "task_id": record.plan.task_id,
        "task_type": record.plan.task_type,
        "mode": "command",
        "capability_id": "desktop.task",
        "title": "记事本多步骤任务",
        "description": "在已打开的记事本中输入指定内容并触发保存。",
        "goal": record.plan.goal,
        "access": "write",
        "risk": "high",
        "requires_confirmation": True,
        "confirmation_reason": "每个桌面动作执行前都需要你的确认。",
        "steps": steps,
        "max_steps": record.plan.max_steps,
        "status": _task_status_key(record.state.status),
        "task_state": record.state.status.value,
        "current_step_id": current,
        "pending_action_id": record.state.pending_action_id,
        "action_id": record.state.pending_action_id,
        "desktop_task_owned": True,
        "source": record.plan.source,
    }
    if record.state.reason:
        out["reason"] = record.state.reason
    if record.state.error_code:
        out["error_code"] = record.state.error_code
    out["business_goal_completed"] = record.state.business_goal_completed
    latest_result = next(
        (record.step_results[step.step_id] for step in reversed(record.plan.steps)
         if step.step_id in record.step_results),
        None,
    )
    if latest_result is not None and latest_result.save_state:
        out["save_state"] = latest_result.save_state
    return out


def _step_results(record: TaskRecord) -> list[dict[str, Any]]:
    return [record.step_results[step.step_id].to_dict()
            for step in record.plan.steps if step.step_id in record.step_results]


def _result(record: TaskRecord, *, action_result: dict[str, Any] | None = None,
            idempotent: bool = False) -> TaskResult:
    return TaskResult(
        task_id=record.plan.task_id,
        status=record.state.status,
        business_goal_completed=record.state.business_goal_completed,
        reason=record.state.reason,
        error_code=record.state.error_code,
        current_step_id=record.state.current_step_id,
        step_results=_step_results(record),
        action_result=action_result,
        idempotent=idempotent,
    )


def _transition_task(record: TaskRecord, target: TaskState) -> None:
    record.state.status = transition_task(record.state.status, target)


def _transition_step(step, target: StepState) -> None:
    step.status = transition_step(step.status, target)


def _find_step(record: TaskRecord, step_id: str):
    for step in record.plan.steps:
        if step.step_id == step_id:
            return step
    return None


def _skip_remaining(record: TaskRecord) -> None:
    for step in record.plan.steps:
        if step.status in {StepState.PENDING, StepState.READY, StepState.WAITING_CONFIRMATION}:
            _transition_step(step, StepState.SKIPPED)


def _build_action_command(step, content_refs: dict[str, str]) -> str:
    if step.action_type == "activate":
        return f"激活「{step.window_target}」"
    if step.action_type == "input":
        value = content_refs.get(step.content_ref or "")
        if value is None:
            raise TaskRuntimeError("TASK_CONTEXT_MISSING", "TaskContext 缺少输入内容引用")
        return f"在「{step.window_target}」中向「{step.control_target}」输入「{value}」"
    if step.action_type == "click":
        return f"在「{step.window_target}」中点击「{step.control_target}」"
    raise TaskRuntimeError("TASK_UNSUPPORTED_CAPABILITY", "TaskStep action_type 不在允许列表")


def _prepare_current(record: TaskRecord) -> None:
    """只准备当前一步的 pending action，不提前授权未来步骤。"""
    step = _find_step(record, record.state.current_step_id or "")
    if step is None:
        raise TaskRuntimeError("TASK_STEP_NOT_FOUND", "当前 TaskStep 不存在")
    if step.status == StepState.PENDING:
        _transition_step(step, StepState.READY)
    if step.status != StepState.READY:
        raise TaskRuntimeError("STEP_INVALID_STATE", "当前 Step 不处于可准备状态")

    command = _build_action_command(step, record.context.content_refs)
    action_plan = broker.plan_command(command)
    expected_action = _ACTION_TYPES.get(step.action_type)
    if not expected_action or action_plan.get("action") != expected_action:
        _transition_step(step, StepState.FAILED)
        record.state.error_code = "TASK_UNSUPPORTED_CAPABILITY"
        record.state.reason = "Step 未能映射到现有 Desktop Action"
        _transition_task(record, TaskState.FAILED)
        _skip_remaining(record)
        raise TaskRuntimeError(record.state.error_code, record.state.reason,
                               task_result=_result(record))

    trace_context = {
        "source": "desktop_task_step",
        "task_id": record.plan.task_id,
        "step_id": step.step_id,
        "action_type": action_plan.get("action"),
    }
    with computer_actions.execution_trace_context(trace_context):
        pending = computer_actions.create_pending_action(action_plan, command)
    if not pending.get("ok"):
        _transition_step(step, StepState.FAILED)
        record.state.error_code = pending.get("error_code", "TASK_ACTION_PREPARE_FAILED")
        record.state.reason = pending.get("error", "桌面动作准备失败")
        _transition_task(record, TaskState.FAILED)
        _skip_remaining(record)
        raise TaskRuntimeError(record.state.error_code, record.state.reason,
                               task_result=_result(record))

    _transition_step(step, StepState.WAITING_CONFIRMATION)
    action_id = str(pending.get("action_id") or "")
    if not action_id:
        _transition_step(step, StepState.FAILED)
        record.state.error_code = "TASK_ACTION_PREPARE_FAILED"
        record.state.reason = "桌面动作未返回 action_id"
        _transition_task(record, TaskState.FAILED)
        _skip_remaining(record)
        raise TaskRuntimeError(record.state.error_code, record.state.reason,
                               task_result=_result(record))
    record.state.pending_action_id = action_id
    store.bind_action(action_id, record.plan.task_id, step.step_id)
    _transition_task(record, TaskState.WAITING_CONFIRMATION)


def start_task(plan_payload: dict[str, Any], goal: str, *, session_id: object = None) -> dict[str, Any]:
    """创建并准备第一步，返回安全的 Task Plan + Task Result。"""
    try:
        plan = task_planner.validate_public_plan(plan_payload)
        content_refs = task_planner.context_for_goal(plan, goal)
    except task_planner.TaskPlanError as exc:
        raise TaskRuntimeError(exc.code, exc.message) from exc
    except Exception as exc:
        raise TaskRuntimeError("TASK_PLAN_INVALID", f"TaskPlan 校验失败：{exc}") from exc

    session = _session_id(session_id)
    with store.locked():
        active = store.active_for_session(session)
        if active is not None:
            raise TaskRuntimeError(
                "TASK_ALREADY_ACTIVE",
                f"当前 session 已有未结束任务：{active.plan.task_id}",
                task_result=_result(active),
            )
        record = TaskRecord(
            plan=plan,
            context=TaskContext(content_refs=content_refs, session_id=session),
            state=TaskStateData(
                status=TaskState.PLANNING,
                current_step_id=plan.steps[0].step_id,
            ),
        )
        store.create(record)
        _transition_task(record, TaskState.READY)
        try:
            _prepare_current(record)
        except TaskRuntimeError:
            raise
        return {"task_plan": public_plan(record), "task_result": _result(record).to_dict()}


def get_task(task_id: str) -> dict[str, Any]:
    with store.locked():
        record = store.get(task_id)
        if record is None:
            raise TaskRuntimeError("TASK_NOT_FOUND", "Task 不存在")
        return {"task_plan": public_plan(record), "task_result": _result(record).to_dict()}


def _validate_action_binding(record: TaskRecord, step_id: str, action_id: str):
    if record.state.status not in _ACTIVE_CONFIRMATION_STATES:
        raise TaskRuntimeError("TASK_NOT_WAITING_CONFIRMATION", "Task 当前不在等待确认状态")
    if record.state.current_step_id != step_id:
        raise TaskRuntimeError("TASK_STEP_MISMATCH", "请求 Step 不是当前待执行 Step")
    if record.state.pending_action_id != action_id:
        raise TaskRuntimeError("TASK_ACTION_MISMATCH", "action_id 不属于当前 TaskStep")
    binding = store.action_binding(action_id)
    if binding != (record.plan.task_id, step_id):
        raise TaskRuntimeError("TASK_ACTION_MISMATCH", "action_id 未绑定到指定 Task/Step")
    step = _find_step(record, step_id)
    if step is None:
        raise TaskRuntimeError("TASK_STEP_NOT_FOUND", "TaskStep 不存在")
    if step.status != StepState.WAITING_CONFIRMATION:
        raise TaskRuntimeError("STEP_NOT_WAITING_CONFIRMATION", "Step 当前不在等待确认状态")
    return step


def _save_step_result(record: TaskRecord, step, action_id: str, action_result: dict[str, Any],
                      started: float) -> StepResult:
    verified = bool(action_result.get("verified"))
    action_status = str(action_result.get("status") or "failed")
    step_status = StepState.SUCCEEDED if action_status == "success" and verified else StepState.FAILED
    if step.status == StepState.EXECUTING:
        _transition_step(step, StepState.VERIFYING)
    _transition_step(step, step_status)
    safe_action_result = _safe_action_result(action_result)
    result = StepResult(
        step_id=step.step_id,
        action_id=action_id,
        status="SUCCEEDED" if step_status == StepState.SUCCEEDED else "FAILED",
        verified=verified,
        error_code=str(action_result.get("error_code") or ""),
        execution_state=str(action_result.get("execution_state") or ""),
        business_goal_completed=action_result.get("business_goal_completed"),
        save_state=str(action_result.get("save_state") or ""),
        duration_ms=int((time.monotonic() - started) * 1000),
        action_result=safe_action_result,
    )
    record.step_results[step.step_id] = result
    return result


def _safe_action_result(action_result: dict[str, Any] | None) -> dict[str, Any]:
    """只保留 Task Result 所需的动作结果字段，不回显旧 action payload。"""
    source = action_result or {}
    return {
        key: source[key]
        for key in ("status", "verified", "error_code", "execution_state",
                    "business_goal_completed", "save_state", "verification")
        if key in source
    }


def _finish_after_success(record: TaskRecord, step, result: StepResult,
                          action_result: dict[str, Any]) -> None:
    record.state.pending_action_id = None
    store.unbind_action(result.action_id)
    business_goal_completed = action_result.get("business_goal_completed")
    save_state = action_result.get("save_state")
    if save_state == "SAVE_AS_REQUIRED" or business_goal_completed is False:
        record.state.business_goal_completed = False
        record.state.reason = "SAVE_AS_REQUIRED" if save_state == "SAVE_AS_REQUIRED" else "USER_INPUT_REQUIRED"
        _transition_task(record, TaskState.WAITING_USER)
        return

    next_step = next((candidate for candidate in record.plan.steps
                      if candidate.status == StepState.PENDING), None)
    if next_step is None:
        record.state.business_goal_completed = True
        record.state.reason = ""
        _transition_task(record, TaskState.SUCCEEDED)
        return

    record.state.current_step_id = next_step.step_id
    _prepare_current(record)


def confirm_step(task_id: str, step_id: str, action_id: str) -> dict[str, Any]:
    """确认当前 TaskStep；同一 action 在 Runtime 内只会调用一次。"""
    with store.locked():
        record = store.get(task_id)
        if record is None:
            raise TaskRuntimeError("TASK_NOT_FOUND", "Task 不存在")
        existing = record.step_results.get(step_id)
        if existing is not None and existing.action_id == action_id:
            return {
                "task_plan": public_plan(record),
                "task_result": _result(
                    record,
                    action_result=existing.action_result,
                    idempotent=True,
                ).to_dict(),
            }
        step = _validate_action_binding(record, step_id, action_id)
        _transition_task(record, TaskState.RUNNING)
        _transition_step(step, StepState.EXECUTING)
        started = time.monotonic()
        try:
            action_result = computer_actions.confirm_action(action_id)
        except Exception as exc:  # noqa: BLE001
            action_result = {
                "ok": False,
                "status": "failed",
                "verified": False,
                "error_code": "TASK_ACTION_EXECUTION_FAILED",
                "error": f"桌面动作执行异常：{exc}",
            }
        result = _save_step_result(record, step, action_id, action_result, started)
        record.state.pending_action_id = None
        store.unbind_action(action_id)

        if result.status != "SUCCEEDED":
            record.state.error_code = result.error_code or "TASK_ACTION_FAILED"
            record.state.reason = str(action_result.get("error") or "当前 Step 执行或验证失败")
            _transition_task(record, TaskState.FAILED)
            _skip_remaining(record)
        else:
            _finish_after_success(record, step, result, action_result)
        return {
            "task_plan": public_plan(record),
            "task_result": _result(record, action_result=result.action_result).to_dict(),
        }


def reject_step(task_id: str, step_id: str, action_id: str) -> dict[str, Any]:
    """拒绝当前 Step，不执行动作，并终止 Task。"""
    with store.locked():
        record = store.get(task_id)
        if record is None:
            raise TaskRuntimeError("TASK_NOT_FOUND", "Task 不存在")
        existing = record.step_results.get(step_id)
        if existing is not None and existing.action_id == action_id:
            return {
                "task_plan": public_plan(record),
                "task_result": _result(
                    record,
                    action_result=existing.action_result,
                    idempotent=True,
                ).to_dict(),
            }
        step = _validate_action_binding(record, step_id, action_id)
        action_result = computer_actions.reject_action(action_id)
        _transition_step(step, StepState.FAILED)
        safe_action_result = _safe_action_result(action_result)
        result = StepResult(
            step_id=step_id,
            action_id=action_id,
            status="CANCELLED",
            verified=False,
            error_code="ACTION_CANCELLED",
            action_result=safe_action_result or {"status": "cancelled"},
        )
        record.step_results[step_id] = result
        record.state.pending_action_id = None
        record.state.error_code = "ACTION_CANCELLED"
        record.state.reason = "用户拒绝当前桌面动作"
        store.unbind_action(action_id)
        _transition_task(record, TaskState.CANCELLED)
        _skip_remaining(record)
        return {
            "task_plan": public_plan(record),
            "task_result": _result(record, action_result=result.action_result).to_dict(),
        }


def action_binding(action_id: str) -> tuple[str, str] | None:
    """供旧 Computer Action 路由阻止绕过 Task Runtime 的确认。"""
    return store.action_binding(action_id)


def reset_for_tests() -> None:
    store.reset_for_tests()
