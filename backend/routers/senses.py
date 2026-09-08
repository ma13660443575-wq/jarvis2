# -*- coding: utf-8 -*-
"""
routers/senses.py — 感官接口
=============================
GET /api/senses            → 可用感官清单
GET /api/senses/{name}     → 调用某感官（weather / rss / files / terminal）
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..senses import fetch as senses_fetch

router = APIRouter()


@router.get("/api/senses")
def senses_list():
    from ..senses import available
    return {"ok": True, "data": available()}


@router.get("/api/senses/{name}")
def senses_call(name: str, path: str = "", limit: int = 8, city: str | None = None,
                command: str | None = None):
    r = senses_fetch(name, path=path, limit=limit, city=city, command=command)
    if not r["ok"]:
        # 感官失败是正常降级，不视为 HTTP 错误（前端友好提示）
        return {"ok": True, "data": r}
    return {"ok": True, "data": r}
