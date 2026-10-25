# -*- coding: utf-8 -*-
"""
routers/chat.py — 对话 + 记忆候选
=================================
- POST /api/chat/stream       NDJSON 流式问答（事件：start/sources/delta/done）
- GET  /api/memory/candidates 待确认记忆候选
- POST /api/memory/candidates/{id}/confirm|reject
- GET  /api/conversations       会话列表
- GET  /api/conversations/{id}/messages
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..brain import answer
from ..heart import notify
from ..memory import manager as mem
from ..memory import vector
from ..db import ops as db_ops

router = APIRouter()


class ChatIn(BaseModel):
    question: str
    conversation_id: int | None = None


@router.post("/api/chat/stream")
def chat_stream(body: ChatIn):
    if not body.question.strip():
        raise HTTPException(400, "问题不能为空")
    def gen():
        import time
        from ..db import evals as evals_db
        # 桌宠联动：开始/结束广播到 SSE /api/events（channel=chat）
        notify.publish("chat", {"state": "start", "conversation_id": body.conversation_id})
        text_parts: list[str] = []
        _t0 = time.time()
        try:
            for evt in answer.chat_stream(
                    body.question, body.conversation_id):
                if evt.get("type") == "delta":
                    text_parts.append(str(evt.get("text", "")))
                yield json.dumps(evt, ensure_ascii=False) + "\n"
        finally:
            notify.publish("chat", {"state": "done", "text": "".join(text_parts)})
            # 运行健康埋点：是否有回复 + 整流耗时（token 消耗已在 llm.py 统一记账）
            try:
                evals_db.record_eval(
                    "chat", ok=bool(text_parts),
                    latency_ms=int((time.time() - _t0) * 1000),
                )
            except Exception:  # noqa: BLE001
                pass

    return StreamingResponse(gen(), media_type="application/x-ndjson")


# ===== 记忆候选 =====

@router.get("/api/memory/nodes")
def list_memory_nodes(limit: int = 120):
    """记忆星图节点（向量库滚动拉取）。空索引返回空数组。"""
    return {"ok": True, "data": vector.list_nodes(limit=limit)}


@router.get("/api/memory/candidates")
def list_candidates():
    return {"ok": True, "data": mem.pending_candidates()}


@router.post("/api/memory/candidates/{cid}/confirm")
def confirm_candidate(cid: int):
    r = mem.confirm_candidate(cid)
    if not r["ok"]:
        raise HTTPException(400, r.get("error", "确认失败"))
    return {"ok": True, "data": r}


@router.post("/api/memory/candidates/{cid}/reject")
def reject_candidate(cid: int):
    r = mem.reject_candidate(cid)
    if not r["ok"]:
        raise HTTPException(400, r.get("error", "拒绝失败"))
    return {"ok": True, "data": r}


# ===== 会话 =====

@router.get("/api/conversations")
def conversations():
    return {"ok": True, "data": db_ops.list_conversations()}


@router.get("/api/conversations/{conv_id}/messages")
def conv_messages(conv_id: int):
    return {"ok": True, "data": db_ops.list_messages(conv_id)}
