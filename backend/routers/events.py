# -*- coding: utf-8 -*-
"""
routers/events.py — SSE 实时事件（P4：简报/记忆/任务/运行 实时推送）
====================================================================
GET /api/events  → Server-Sent Events 长连接
GET /api/events/recent → 最近 N 条事件（便于页面初次加载补状态）
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from ..heart import notify

router = APIRouter()


def _sse(event: dict) -> str:
    """格式化 SSE 帧：data: {...}\n\n"""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.get("/api/events")
async def sse():
    """SSE 长连接：前端 EventSource('/api/events') 订阅实时事件。"""
    q = notify.subscribe()

    async def gen():
        try:
            # 首次连接先发一个 hello，确认链路
            yield _sse({"channel": "hello", "payload": {"status": "connected"}})
            while True:
                try:
                    evt = await asyncio.to_thread(q.get, True, 15)
                    yield _sse(evt)
                except Exception:
                    # 队列超时（15s 无事件）：发心跳保活
                    yield ": keep-alive\n\n"
                    continue
        finally:
            notify.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    })


@router.get("/api/events/recent")
def events_recent(channel: str | None = None, limit: int = 10):
    return {"ok": True, "data": notify.recent(channel=channel, limit=limit)}
