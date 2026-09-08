# -*- coding: utf-8 -*-
"""
heart/notify.py — 应用内事件总线（SSE 数据源）
==============================================
轻量发布/订阅：业务模块 publish(channel, payload)，前端经 /api/events (SSE)
订阅。内存实现，重启即失，符合「DB 只做运行态镜像」的定位。

channel 约定：
  - "briefing"   每日简报更新
  - "memory"     记忆候选新增 / 确认 / 拒绝
  - "tasks"      任务看板变化
  - "runs"       工作流 / 技能运行状态变化
  - "skills"     技能库扫描 / 新增
  - "status"     系统状态变化（供前端主动拉取）
"""
from __future__ import annotations

import queue
import threading
import time

# 订阅者集合（queue.Queue 以线程安全传递事件）
_SUBSCRIBERS: set[queue.Queue] = set()
_guard = threading.Lock()
# 最近事件环形缓冲，新订阅者补发（max 8）
_RECENT: list[dict] = []
_RECENT_MAX = 8


def publish(channel: str, payload: dict | None = None) -> None:
    """向所有订阅者广播事件。业务模块调用，绝不抛异常。"""
    evt = {
        "channel": channel,
        "payload": payload or {},
        "ts": time.strftime("%H:%M:%S"),
    }
    with _guard:
        _RECENT.append(evt)
        if len(_RECENT) > _RECENT_MAX:
            _RECENT.pop(0)
        subs = list(_SUBSCRIBERS)
    for q in subs:
        try:
            q.put(evt, timeout=0.1)
        except Exception:  # noqa: BLE001
            _drop(q)


def subscribe() -> queue.Queue:
    """注册订阅者，返回事件队列。"""
    q: queue.Queue = queue.Queue(maxsize=200)
    with _guard:
        _SUBSCRIBERS.add(q)
        # 补发最近事件，让新连接立即有数据
        for evt in _RECENT:
            try:
                q.put(evt, timeout=0.1)
            except Exception:  # noqa: BLE001
                break
    return q


def unsubscribe(q: queue.Queue) -> None:
    with _guard:
        _SUBSCRIBERS.discard(q)


def _drop(q: queue.Queue) -> None:
    with _guard:
        _SUBSCRIBERS.discard(q)


def recent(channel: str | None = None, limit: int = 8) -> list[dict]:
    with _guard:
        items = _RECENT if channel is None else [e for e in _RECENT if e["channel"] == channel]
        return items[-limit:]
