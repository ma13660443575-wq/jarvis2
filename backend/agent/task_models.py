# -*- coding: utf-8 -*-
"""Desktop Task Runtime 的受约束数据模型。

TaskPlan 是公开、可审计的结构化计划；用户输入正文只存在于 TaskContext 的
进程内运行时内存中，绝不出现在计划、步骤结果或事件摘要里。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

try:
    from pydantic import ConfigDict
except ImportError:  # Pydantic v1
    ConfigDict = None


MAX_TASK_STEPS = 5
ALLOWED_TASK_ACTION_TYPES = frozenset({"activate", "input", "click"})


class TaskState(str, Enum):
    PLANNING = "PLANNING"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    WAITING_USER = "WAITING_USER"
    PAUSED = "PAUSED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class StepState(str, Enum):
    PENDING = "PENDING"
    READY = "READY"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


if ConfigDict is not None:
    class _StrictModel(BaseModel):
        """Pydantic v2 严格基础模型。"""

        model_config = ConfigDict(extra="forbid")
else:
    class _StrictModel(BaseModel):
        """Pydantic v1 严格基础模型。"""

        class Config:
            extra = "forbid"


class TaskStepModel(_StrictModel):
    step_id: str = Field(min_length=1, max_length=64)
    order: int = Field(ge=1, le=MAX_TASK_STEPS)
    action_type: Literal["activate", "input", "click"]
    window_target: str = Field(min_length=1, max_length=120)
    control_target: str | None = Field(default=None, max_length=120)
    content_ref: str | None = Field(default=None, max_length=120)
    requires_confirmation: bool = True
    status: StepState = StepState.PENDING
    title: str = Field(default="", max_length=120)


class TaskPlanModel(_StrictModel):
    task_id: str = Field(min_length=1, max_length=80)
    task_type: Literal["desktop_task"] = "desktop_task"
    goal: str = Field(min_length=1, max_length=240)
    steps: list[TaskStepModel] = Field(min_length=1, max_length=MAX_TASK_STEPS)
    max_steps: int = Field(default=MAX_TASK_STEPS, ge=1, le=MAX_TASK_STEPS)
    source: str = Field(default="structured_planner", max_length=80)


@dataclass
class TaskContext:
    """Task 运行时上下文；content_refs 是敏感正文，不能公开或持久化。"""

    content_refs: dict[str, str] = field(default_factory=dict)
    session_id: str = "default"
    runtime_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class StepResult:
    step_id: str
    action_id: str = ""
    status: str = "FAILED"
    verified: bool = False
    error_code: str = ""
    execution_state: str = ""
    business_goal_completed: bool | None = None
    save_state: str = ""
    attempt: int = 1
    duration_ms: int | None = None
    action_result: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """返回安全摘要，不复制 action payload 或输入正文。"""
        return {
            "step_id": self.step_id,
            "action_id": self.action_id,
            "status": self.status,
            "verified": self.verified,
            "error_code": self.error_code,
            "execution_state": self.execution_state,
            "business_goal_completed": self.business_goal_completed,
            "save_state": self.save_state,
            "attempt": self.attempt,
            "duration_ms": self.duration_ms,
        }


@dataclass
class TaskStateData:
    status: TaskState = TaskState.PLANNING
    current_step_id: str | None = None
    pending_action_id: str | None = None
    reason: str = ""
    error_code: str = ""
    business_goal_completed: bool = False


@dataclass
class TaskResult:
    task_id: str
    status: TaskState
    business_goal_completed: bool
    reason: str = ""
    error_code: str = ""
    current_step_id: str | None = None
    step_results: list[dict[str, Any]] = field(default_factory=list)
    action_result: dict[str, Any] | None = None
    idempotent: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status.value,
            "business_goal_completed": self.business_goal_completed,
            "reason": self.reason,
            "error_code": self.error_code,
            "current_step_id": self.current_step_id,
            "step_results": self.step_results,
            "action_result": self.action_result,
            "idempotent": self.idempotent,
        }


@dataclass
class TaskRecord:
    plan: TaskPlanModel
    context: TaskContext
    state: TaskStateData = field(default_factory=TaskStateData)
    step_results: dict[str, StepResult] = field(default_factory=dict)
    created_at: float = 0.0


def model_validate(model_cls, payload: dict[str, Any]):
    """调用 Pydantic v2/v1 对应的结构校验入口。"""
    validator = getattr(model_cls, "model_validate", None)
    if validator is not None:
        return validator(payload)
    return model_cls.parse_obj(payload)


def model_dump(model: BaseModel) -> dict[str, Any]:
    dumper = getattr(model, "model_dump", None)
    if dumper is not None:
        return dumper(mode="json")
    return model.dict()


def validate_plan_semantics(plan: TaskPlanModel) -> None:
    """校验 Pydantic 无法表达的跨字段动作约束。"""
    if len(plan.steps) > plan.max_steps or len(plan.steps) > MAX_TASK_STEPS:
        raise ValueError(f"任务步骤超过上限 {MAX_TASK_STEPS}")
    expected_orders = list(range(1, len(plan.steps) + 1))
    if [step.order for step in plan.steps] != expected_orders:
        raise ValueError("任务步骤 order 必须从 1 连续递增")
    expected_ids = [f"step_{order}" for order in expected_orders]
    if [step.step_id for step in plan.steps] != expected_ids:
        raise ValueError("任务步骤 id 必须与顺序一致")

    for step in plan.steps:
        if not step.requires_confirmation:
            raise ValueError("Desktop Task 的每个 Step 都必须要求确认")
        if step.action_type == "activate":
            if step.control_target or step.content_ref:
                raise ValueError("activate 步骤不能包含 control_target/content_ref")
        elif step.action_type == "input":
            if not step.control_target or not step.content_ref:
                raise ValueError("input 步骤必须包含 control_target/content_ref")
        elif step.action_type == "click":
            if not step.control_target or step.content_ref:
                raise ValueError("click 步骤必须只有 control_target")
