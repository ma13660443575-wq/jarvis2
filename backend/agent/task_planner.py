# -*- coding: utf-8 -*-
"""4A 最小 Structured Planner。

当前只接受已批准的 Notepad 三步任务：activate → input → click(save)。
未支持的多步骤桌面目标不会降级成普通问答，也不会生成任意工具调用。
"""
from __future__ import annotations

import re
import uuid
from typing import Any

from .task_models import (
    ALLOWED_TASK_ACTION_TYPES,
    MAX_TASK_STEPS,
    StepState,
    TaskPlanModel,
    TaskStepModel,
    model_dump,
    model_validate,
    validate_plan_semantics,
)


class TaskPlanError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


_QUESTION_MARKERS = ("怎么", "如何", "为什么", "是什么", "能不能", "可以吗", "how", "why")
_INPUT_VERBS = ("输入", "填写", "填入")
_MULTI_STEP_MARKERS = ("然后", "并且", "再", "之后", "接着")
_QUOTED_RE = re.compile(r"「([^」]+)」|“([^”]+)”|\"([^\"]+)\"|'([^']+)'")
_TASK_HINTS = ("记事本", "notepad", "输入", "填写", "填入", "点击", "保存", "激活", "启动", "打开")
_UNSUPPORTED_EXTENSIONS = (
    "关闭", "另存为", "保存到", "浏览器", "excel", "word",
    "发送", "下载", "删除", "重命名", "拖动", "移动",
)


def is_task_candidate(text: str) -> bool:
    source = (text or "").strip()
    lowered = source.casefold()
    if not source or any(marker in lowered for marker in _QUESTION_MARKERS):
        return False
    return (
        any(marker in source for marker in _MULTI_STEP_MARKERS)
        and any(hint.casefold() in lowered for hint in _TASK_HINTS)
    )


def _quoted_values(text: str) -> list[str]:
    values = []
    for match in _QUOTED_RE.finditer(text or ""):
        value = next((part for part in match.groups() if part is not None), "").strip()
        if value:
            values.append(value)
    return values


def _validate_content(value: str) -> None:
    from ..computer.actions import MAX_TEXT_INPUT_LENGTH

    if not value:
        raise TaskPlanError("TASK_PLAN_INVALID", "输入内容不能为空")
    if len(value) > MAX_TEXT_INPUT_LENGTH:
        raise TaskPlanError(
            "TASK_PLAN_INVALID",
            f"输入内容过长（最多 {MAX_TEXT_INPUT_LENGTH} 个字符）",
        )
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        raise TaskPlanError("TASK_PLAN_INVALID", "输入内容不能包含控制字符")


def invalid_task_plan(code: str, message: str) -> dict[str, Any]:
    return {
        "plan_id": f"task-plan-{uuid.uuid4().hex[:16]}",
        "task_id": f"task-{uuid.uuid4().hex[:16]}",
        "task_type": "desktop_task",
        "mode": "command",
        "capability_id": "desktop.task",
        "title": "桌面任务能力不可用",
        "description": "当前 4A 只支持已打开记事本中的输入并触发保存。",
        "goal": "未生成执行计划",
        "access": "write",
        "risk": "high",
        "requires_confirmation": True,
        "confirmation_reason": "当前任务需要尚未开放的桌面能力。",
        "steps": [],
        "status": "invalid",
        "task_state": "FAILED",
        "desktop_task_owned": True,
        "error_code": code,
        "error": message,
        "source": "structured_planner",
    }


def _unsupported_plan(text: str) -> dict[str, Any]:
    del text
    return invalid_task_plan(
        "TASK_UNSUPPORTED_CAPABILITY",
        "当前 4A 仅支持：已打开记事本 → 输入指定内容 → 点击保存。",
    )


def plan_task(text: str) -> dict[str, Any] | None:
    """返回安全公开计划；正文只放在内部 context_for() 的运行时映射中。"""
    if not is_task_candidate(text):
        return None

    source = (text or "").strip().rstrip("。.!！")
    lowered = source.casefold()
    supported = (
        "记事本" in source or "notepad" in lowered
    ) and any(verb in source for verb in _INPUT_VERBS) and "保存" in source
    input_pos = min((source.find(verb) for verb in _INPUT_VERBS if verb in source), default=-1)
    save_pos = source.find("保存")
    has_unsupported_extension = any(marker.casefold() in lowered
                                    for marker in _UNSUPPORTED_EXTENSIONS)
    has_launch_request = bool(re.search(r"(?:^|[，,；;。\s])(?:请)?(?:打开|启动)\s*记事本", source))
    if (not supported or has_unsupported_extension or has_launch_request
            or input_pos < 0 or save_pos <= input_pos):
        return _unsupported_plan(text)

    quoted = _quoted_values(source)
    if len(quoted) != 1:
        raise TaskPlanError("TASK_PLAN_INVALID", "请明确使用引号提供一次输入正文")
    content = quoted[0]
    _validate_content(content)

    task_id = f"task-{uuid.uuid4().hex[:16]}"
    steps = [
        TaskStepModel(
            step_id="step_1", order=1, action_type="activate",
            window_target="记事本", title="激活记事本",
        ),
        TaskStepModel(
            step_id="step_2", order=2, action_type="input",
            window_target="记事本", control_target="编辑器",
            content_ref="user_content_1", title="向编辑器输入指定内容",
        ),
        TaskStepModel(
            step_id="step_3", order=3, action_type="click",
            window_target="记事本", control_target="保存", title="点击保存",
        ),
    ]
    plan = TaskPlanModel(
        task_id=task_id,
        goal=f"在已打开的记事本中输入指定内容（{len(content)} 个字符），然后保存。",
        steps=steps,
        max_steps=MAX_TASK_STEPS,
    )
    try:
        validate_plan_semantics(plan)
    except ValueError as exc:
        raise TaskPlanError("TASK_PLAN_INVALID", str(exc)) from exc

    return {
        "plan_id": f"task-plan-{uuid.uuid4().hex[:16]}",
        "task_id": plan.task_id,
        "task_type": plan.task_type,
        "mode": "command",
        "capability_id": "desktop.task",
        "title": "记事本多步骤任务",
        "description": "在已打开的记事本中输入指定内容并触发保存。",
        "goal": plan.goal,
        "access": "write",
        "risk": "high",
        "requires_confirmation": True,
        "confirmation_reason": "每个桌面动作执行前都需要你的确认。",
        "steps": [
            {
                **model_dump(step),
                "id": step.step_id,
                "capability_id": {
                    "activate": "computer.activate_window",
                    "input": "computer.set_text",
                    "click": "computer.click_control",
                }[step.action_type],
            }
            for step in steps
        ],
        "max_steps": MAX_TASK_STEPS,
        "status": "ready",
        "task_state": "READY",
        "desktop_task_owned": True,
        "source": "structured_planner",
    }


def validate_public_plan(payload: dict[str, Any]) -> TaskPlanModel:
    """对进入 Runtime 的计划再次做 schema + 语义校验。"""
    try:
        steps = []
        for raw in payload.get("steps") or []:
            steps.append({
                "step_id": raw.get("step_id") or raw.get("id"),
                "order": raw.get("order"),
                "action_type": raw.get("action_type"),
                "window_target": raw.get("window_target"),
                "control_target": raw.get("control_target"),
                "content_ref": raw.get("content_ref"),
                "requires_confirmation": raw.get("requires_confirmation", True),
                "status": raw.get("status", StepState.PENDING.value),
                "title": raw.get("title", ""),
            })
        normalized = {
            "task_id": payload.get("task_id"),
            "task_type": payload.get("task_type"),
            "goal": payload.get("goal"),
            "steps": steps,
            "max_steps": payload.get("max_steps", MAX_TASK_STEPS),
            "source": payload.get("source", "structured_planner"),
        }
        plan = model_validate(TaskPlanModel, normalized)
        validate_plan_semantics(plan)
        if any(step.action_type not in ALLOWED_TASK_ACTION_TYPES for step in plan.steps):
            raise ValueError("计划包含未允许的 action_type")
        return plan
    except TaskPlanError:
        raise
    except Exception as exc:
        raise TaskPlanError("TASK_PLAN_INVALID", f"TaskPlan schema 校验失败：{exc}") from exc


def context_for_goal(plan: TaskPlanModel, goal: str) -> dict[str, str]:
    """从原始请求提取 runtime-only content_refs，不让正文进入 TaskPlan。"""
    quoted = _quoted_values(goal)
    if len(quoted) != 1:
        raise TaskPlanError("TASK_PLAN_INVALID", "无法恢复输入内容引用")
    content = quoted[0]
    _validate_content(content)
    refs = {step.content_ref for step in plan.steps if step.content_ref}
    if refs != {"user_content_1"}:
        raise TaskPlanError("TASK_PLAN_INVALID", "TaskPlan content_ref 不符合 4A 约束")
    return {"user_content_1": content}
