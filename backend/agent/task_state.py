# -*- coding: utf-8 -*-
"""Desktop Task 与 TaskStep 的集中状态转换。"""
from __future__ import annotations

from .task_models import StepState, TaskState


class TaskStateError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


TASK_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.PLANNING: frozenset({TaskState.READY, TaskState.FAILED}),
    TaskState.READY: frozenset({TaskState.WAITING_CONFIRMATION, TaskState.FAILED, TaskState.CANCELLED}),
    TaskState.WAITING_CONFIRMATION: frozenset({TaskState.RUNNING, TaskState.FAILED, TaskState.CANCELLED}),
    TaskState.RUNNING: frozenset({TaskState.WAITING_CONFIRMATION, TaskState.WAITING_USER,
                                  TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELLED}),
    TaskState.WAITING_USER: frozenset({TaskState.PAUSED, TaskState.CANCELLED, TaskState.FAILED}),
    TaskState.PAUSED: frozenset({TaskState.READY, TaskState.CANCELLED, TaskState.FAILED}),
    TaskState.SUCCEEDED: frozenset(),
    TaskState.FAILED: frozenset(),
    TaskState.CANCELLED: frozenset(),
}


STEP_TRANSITIONS: dict[StepState, frozenset[StepState]] = {
    StepState.PENDING: frozenset({StepState.READY, StepState.SKIPPED}),
    StepState.READY: frozenset({StepState.WAITING_CONFIRMATION, StepState.FAILED, StepState.SKIPPED}),
    StepState.WAITING_CONFIRMATION: frozenset({StepState.EXECUTING, StepState.FAILED, StepState.SKIPPED}),
    StepState.EXECUTING: frozenset({StepState.VERIFYING, StepState.FAILED}),
    StepState.VERIFYING: frozenset({StepState.SUCCEEDED, StepState.FAILED}),
    StepState.SUCCEEDED: frozenset(),
    StepState.FAILED: frozenset(),
    StepState.SKIPPED: frozenset(),
}


def transition_task(current: TaskState, target: TaskState) -> TaskState:
    if target not in TASK_TRANSITIONS.get(current, frozenset()):
        raise TaskStateError(
            "TASK_INVALID_TRANSITION",
            f"非法 Task 状态转换：{current.value} → {target.value}",
        )
    return target


def transition_step(current: StepState, target: StepState) -> StepState:
    if target not in STEP_TRANSITIONS.get(current, frozenset()):
        raise TaskStateError(
            "STEP_INVALID_TRANSITION",
            f"非法 Step 状态转换：{current.value} → {target.value}",
        )
    return target


def is_active_task_state(status: TaskState) -> bool:
    return status in {
        TaskState.READY,
        TaskState.RUNNING,
        TaskState.WAITING_CONFIRMATION,
        TaskState.WAITING_USER,
        TaskState.PAUSED,
    }

