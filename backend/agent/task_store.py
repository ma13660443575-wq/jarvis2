# -*- coding: utf-8 -*-
"""进程内 Desktop Task Store。

4A 明确选择 process-local store，以支持跨 HTTP 请求的确认生命周期；后端重启后
未完成任务会丢失，后续阶段再替换为持久化实现。
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager

from .task_models import TaskRecord
from .task_state import is_active_task_state


class TaskStore:
    def __init__(self):
        self._lock = threading.RLock()
        self._tasks: dict[str, TaskRecord] = {}
        self._action_bindings: dict[str, tuple[str, str]] = {}

    @contextmanager
    def locked(self):
        with self._lock:
            yield

    def create(self, record: TaskRecord) -> None:
        with self._lock:
            if record.plan.task_id in self._tasks:
                raise ValueError(f"Task 已存在：{record.plan.task_id}")
            if not record.created_at:
                record.created_at = time.time()
            self._tasks[record.plan.task_id] = record

    def get(self, task_id: str) -> TaskRecord | None:
        with self._lock:
            return self._tasks.get(task_id)

    def active_for_session(self, session_id: str) -> TaskRecord | None:
        with self._lock:
            for record in reversed(list(self._tasks.values())):
                if (record.context.session_id == session_id
                        and is_active_task_state(record.state.status)):
                    return record
        return None

    def bind_action(self, action_id: str, task_id: str, step_id: str) -> None:
        with self._lock:
            self._action_bindings[action_id] = (task_id, step_id)

    def unbind_action(self, action_id: str | None) -> None:
        if not action_id:
            return
        with self._lock:
            self._action_bindings.pop(action_id, None)

    def action_binding(self, action_id: str) -> tuple[str, str] | None:
        with self._lock:
            return self._action_bindings.get(action_id)

    def reset_for_tests(self) -> None:
        with self._lock:
            self._tasks.clear()
            self._action_bindings.clear()


store = TaskStore()

