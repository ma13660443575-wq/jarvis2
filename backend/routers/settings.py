# -*- coding: utf-8 -*-
"""
routers/settings.py — 模型/设置管理端点
=======================================
- GET  /api/settings            全量设置（key 脱敏）
- PUT  /api/settings            批量保存（写 settings 表 + 清 llm 客户端缓存）
- GET  /api/models              三类模型列表（LLM / TTS / ASR）
- POST /api/settings/tts/preview  试听指定 sambert 音色 → mp3
- POST /api/settings/recommend    按场景返回 AI 推荐模型

settings 表（key/value）持久化运行时偏好；env 提供默认值，DB 覆盖。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from .. import config
from ..brain import llm
from ..db import settings as settings_db
from ..voice import dashscope

router = APIRouter()

# 场景清单（供 AI 推荐展示）
SCENES = ["chat", "briefing", "memory", "review", "task", "retro"]

_VALID_BACKENDS = ("auto", "aliyun", "deepseek", "mock")
_VALID_TTS_ENGINE = ("auto", "dashscope", "edge")
_VALID_ASR_ENGINE = ("auto", "dashscope", "local", "volcano")


def _mask_key(k: str) -> str:
    if not k:
        return ""
    if len(k) <= 8:
        return "***"
    return f"{k[:3]}***{k[-4:]}"


def _llm_models() -> list[dict]:
    return [
        {"backend": "aliyun", "models": list(config.DASHSCOPE_LLM_MODELS),
         "default": config.DASHSCOPE_DEFAULT_LLM_MODEL},
        {"backend": "deepseek", "models": [config.DEEPSEEK_MODEL],
         "default": config.DEEPSEEK_MODEL},
    ]


@router.get("/api/settings")
def get_settings():
    data = settings_db.get_all_settings()
    return {
        "ok": True,
        "data": {
            "keys": {
                "dashscope": {"has": config.is_dashscope_enabled(),
                              "masked": _mask_key(config.DASHSCOPE_API_KEY)},
                "deepseek": {"has": bool(config.DEEPSEEK_API_KEY),
                             "masked": _mask_key(config.DEEPSEEK_API_KEY)},
                "volcano": {"has": config.is_voice_enabled(),
                            "masked": _mask_key(config.VOLCANO_API_KEY or config.VOLCANO_ACCESS_TOKEN)},
            },
            "llm": {
                "manual_backend": data.get("llm_backend", "auto"),
                "manual_model": data.get("llm_model", ""),
                "backend": llm.resolve_backend(),
                "model": llm.resolve_model("chat"),
                "models": _llm_models(),
            },
            "tts": {
                "engine": settings_db.resolve_tts_engine(),
                "voice": data.get("tts_voice", config.DASHSCOPE_TTS_MODEL),
                "voices": list(config.DASHSCOPE_TTS_VOICES),
                "default": config.DASHSCOPE_TTS_MODEL,
                "edge_enabled": config.TTS_EDGE_ENABLED,
            },
            "asr": {
                "engine": settings_db.resolve_asr_engine(),
                "models": [config.DASHSCOPE_ASR_MODEL],
            },
            "voice": {
                "continuous_talk": bool(data.get("continuous_talk", False)),
            },
        },
    }


class SettingsIn(BaseModel):
    llm_backend: str | None = None
    llm_model: str | None = None
    tts_engine: str | None = None
    tts_voice: str | None = None
    asr_engine: str | None = None
    continuous_talk: bool | None = None


@router.put("/api/settings")
def put_settings(body: SettingsIn):
    saved: dict = {}
    if body.llm_backend is not None:
        if body.llm_backend not in _VALID_BACKENDS:
            raise HTTPException(400, f"非法 LLM 后端：{body.llm_backend}")
        settings_db.set_setting("llm_backend", body.llm_backend)
        saved["llm_backend"] = body.llm_backend
    if body.llm_model is not None:
        model = body.llm_model.strip()
        if model and model not in config.DASHSCOPE_LLM_MODELS:
            raise HTTPException(400, f"非法 qwen 模型：{model}")
        settings_db.set_setting("llm_model", model)
        saved["llm_model"] = model
    if body.tts_engine is not None:
        if body.tts_engine not in _VALID_TTS_ENGINE:
            raise HTTPException(400, f"非法 TTS 引擎：{body.tts_engine}")
        settings_db.set_setting("tts_engine", body.tts_engine)
        saved["tts_engine"] = body.tts_engine
    if body.tts_voice is not None:
        if body.tts_voice not in config.DASHSCOPE_TTS_VOICES:
            raise HTTPException(400, f"未知音色：{body.tts_voice}")
        settings_db.set_setting("tts_voice", body.tts_voice)
        saved["tts_voice"] = body.tts_voice
    if body.asr_engine is not None:
        if body.asr_engine not in _VALID_ASR_ENGINE:
            raise HTTPException(400, f"非法 ASR 引擎：{body.asr_engine}")
        settings_db.set_setting("asr_engine", body.asr_engine)
        saved["asr_engine"] = body.asr_engine
    if body.continuous_talk is not None:
        settings_db.set_setting("continuous_talk", bool(body.continuous_talk))
        saved["continuous_talk"] = bool(body.continuous_talk)

    # 切后端/key → 清客户端缓存，下次调用取新配置
    llm.clear_clients()

    return {"ok": True, "data": {"saved": saved, **get_settings()["data"]}}


@router.get("/api/models")
def get_models():
    return {
        "ok": True,
        "data": {
            "llm": _llm_models(),
            "tts": {
                "voices": list(config.DASHSCOPE_TTS_VOICES),
                "default": config.DASHSCOPE_TTS_MODEL,
            },
            "asr": {
                "models": [config.DASHSCOPE_ASR_MODEL],
                "default": config.DASHSCOPE_ASR_MODEL,
            },
        },
    }


class TtsPreviewIn(BaseModel):
    voice: str
    text: str = "你好，我是你的 AI 助手 JARVIS。这是一段语音试听。"


@router.post("/api/settings/tts/preview")
async def tts_preview(body: TtsPreviewIn):
    if not config.is_dashscope_enabled():
        raise HTTPException(503, "DashScope 未配置（DASHSCOPE_API_KEY 缺失），无法试听 sambert 音色")
    try:
        audio = await dashscope.dashscope_tts(body.text or "你好", body.voice)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"语音试听失败：{e}") from e
    return Response(content=audio, media_type="audio/mpeg")


class RecommendIn(BaseModel):
    scene: str | None = None  # chat/briefing/... 或 __all__


@router.post("/api/settings/recommend")
def recommend(body: RecommendIn | None = None):
    scene = (body.scene if body else None) or "chat"
    if scene == "__all__":
        return {"ok": True, "data": {"scenes": {s: llm.recommend_model(s) for s in SCENES}}}
    if scene not in SCENES:
        scene = "chat"
    return {"ok": True, "data": {"scene": llm.recommend_model(scene)}}
