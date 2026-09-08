# -*- coding: utf-8 -*-
"""
routers/status.py — 健康检查 / 状态 / 聚合仪表盘数据
"""
from __future__ import annotations

from fastapi import APIRouter

from .. import config
from ..brain import llm
from ..db.settings import resolve_asr_engine
from ..vault import paths
from ..voice import dashscope, local

router = APIRouter()


@router.get("/api/ping")
def ping():
    return {"ok": True, "data": {"pong": True}}


@router.get("/api/health")
def health():
    asr_engine = resolve_asr_engine()
    if asr_engine == "dashscope":
        asr_enabled = dashscope.is_available()
    elif asr_engine == "local":
        asr_enabled = local.asr_available()
    else:
        asr_enabled = config.is_voice_enabled()
    return {
        "ok": True,
        "data": {
            "ai_mode": config.ai_mode(),
            "llm_backend": llm.resolve_backend(),
            "llm_enabled": config.is_llm_enabled(),
            "vector_enabled": config.VECTOR_ENABLED,
            # TTS：DashScope sambert 优先 → Edge 兜底；ASR 引擎随 settings 解析
            "voice_enabled": config.is_tts_enabled(),
            "asr_engine": asr_engine,
            "asr_enabled": asr_enabled,
            "vault": str(config.VAULT_PATH),
            "status": "ONLINE",
        },
    }


@router.get("/api/config/public")
def config_public():
    """前端公开配置：全局唤醒服务总闸。

    唤醒已从浏览器本地 Porcupine 迁移到独立后台 agent 进程（jarvis2-wake-agent），
    开关/状态由前端轮询 /api/wake/status 驱动，此处仅保留总闸开关。
    """
    return {
        "ok": True,
        "data": {
            "wake_enabled": config.WAKE_ENABLED,
        },
    }


@router.get("/api/status")
def status():
    """运行状态：vault 统计 + 路径契约自检。"""
    stats = paths.vault_stats()
    contract = paths.check_contract()
    return {
        "ok": True,
        "data": {
            "vault": stats,
            "contract": contract,
            "ai_mode": config.ai_mode(),
        },
    }
