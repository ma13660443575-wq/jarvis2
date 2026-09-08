# -*- coding: utf-8 -*-
"""
routers/voice.py — 语音端点
============================
- POST /api/voice/tts           文本 → mp3（DashScope sambert 优先 → Edge 降级）
- POST /api/voice/asr           WAV → 文本（本地 sherpa → 火山；VOICE_ENGINE=dashscope 时走 DashScope 一次性识别）
- WS   /api/voice/asr/stream    流式 ASR（DashScope qwen-audio 全双工，前端直传 PCM）

降级链（阿里云优先 + 自动降级）：
  TTS：DashScope sambert → Edge 神经语音 → 前端浏览器语音
  ASR：DashScope qwen-audio（流式/一次性）→ 本地 sherpa-onnx → 火山兜底
任何未配置/失败返回明确错误，绝不 500。
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, JSONResponse
from pydantic import BaseModel

from .. import config
from ..db.settings import resolve_asr_engine, resolve_tts_engine
from ..voice import edge, local, postprocess, volcano, dashscope

log = logging.getLogger("jarvis2.voice")
router = APIRouter()


class TtsIn(BaseModel):
    text: str
    voice: str | None = None   # sambert 音色（如 sambert-zhide-v1）；None = 默认


@router.post("/api/voice/tts")
async def tts(body: TtsIn):
    if not body.text.strip():
        raise HTTPException(400, "待合成文本不能为空")
    # 朗读前先清洗 markdown 噪声（**加粗** / [[链接]] / 表格 / 代码块）
    text = edge.clean_for_tts(body.text)

    # 1) DashScope sambert（阿里云优先；settings 显式选 edge 时跳过）
    tts_engine = resolve_tts_engine()
    if tts_engine in ("auto", "dashscope") and config.is_dashscope_enabled():
        try:
            audio = await dashscope.dashscope_tts(text, body.voice)
            return Response(content=audio, media_type="audio/mpeg")
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except Exception as e:  # noqa: BLE001
            log.warning("[voice/tts] DashScope 合成失败: %s", e)
            if tts_engine == "dashscope":
                # 显式选择 DashScope → 失败直接报错，不回退 Edge
                raise HTTPException(502, f"DashScope 语音合成失败：{e}") from e
            # auto → 降级 Edge

    # 2) Edge 神经语音（免费无需 key）
    if not config.is_tts_enabled():
        return JSONResponse(
            status_code=503,
            content={"ok": False, "detail": "语音合成已关闭：TTS_EDGE_ENABLED=false。前端已自动切换为本地浏览器语音。"},
        )
    try:
        # edge.tts_synthesize 内部用 asyncio.run()（同步函数自建事件循环）；
        # 本路由是 async def（DashScope 分支需要 await），直接在事件循环里调用会
        # 抛 "asyncio.run() cannot be called from a running event loop"。
        # → 丢到线程池执行，各自有独立事件循环，避免冲突。
        audio = await asyncio.to_thread(edge.tts_synthesize, text)
        return Response(content=audio, media_type="audio/mpeg")
    except Exception as e:  # noqa: BLE001
        log.warning("[voice/tts] Edge 合成失败: %s", e)
        raise HTTPException(502, f"语音合成失败：{e}") from e


@router.post("/api/voice/asr")
async def asr(audio: UploadFile = File(...)):
    try:
        wav_bytes = await audio.read()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"读取音频失败：{e}") from e
    if not wav_bytes:
        raise HTTPException(400, "音频为空")

    engine = resolve_asr_engine()

    # ---- DashScope 引擎（一次性识别，阿里云优先）----
    if engine == "dashscope":
        if not dashscope.is_available():
            return JSONResponse(
                status_code=503,
                content={"ok": False, "detail": "DashScope 未配置：请在 .env 填写 DASHSCOPE_API_KEY"},
            )
        try:
            text = await dashscope.asr_recognize_once(wav_bytes)
            text = postprocess.refine_asr(text)  # 语气词过滤 + 个人词库纠错
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except Exception as e:  # noqa: BLE001
            log.warning("[voice/asr] DashScope 识别失败: %s", e)
            raise HTTPException(502, f"语音识别失败：{e}") from e
        return {"ok": True, "data": {"text": text}}

    # ---- 本地 sherpa-onnx 优先（离线、快、准、免 key）----
    if engine == "local":
        if not local.asr_available():
            return JSONResponse(
                status_code=503,
                content={
                    "ok": False,
                    "detail": "本地语音引擎模型未就绪（首启需联网自动下载一次，或设 VOICE_ENGINE=volcano 走火山）",
                },
            )
        try:
            text = local.recognize_wav(wav_bytes)
            text = postprocess.refine_asr(text)  # 语气词过滤 + 个人词库纠错
        except Exception as e:  # noqa: BLE001
            log.warning("[voice/asr] 本地识别失败: %s", e)
            raise HTTPException(502, f"语音识别失败：{e}") from e
        return {"ok": True, "data": {"text": text}}

    # ---- 火山兜底（VOICE_ENGINE=volcano）----
    if not config.is_voice_enabled():
        return JSONResponse(
            status_code=400,
            content={"ok": False, "detail": "语音识别未配置：请在 .env 填写 VOLCANO_API_KEY（或 AppId+AccessKey）"},
        )
    try:
        text = await volcano.asr_recognize(wav_bytes)
        text = postprocess.refine_asr(text)  # 语气词过滤 + 个人词库纠错（不耗额度）
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        log.warning("[voice/asr] 识别失败: %s", e)
        raise HTTPException(502, f"语音识别失败：{e}") from e
    return {"ok": True, "data": {"text": text}}


@router.websocket("/api/voice/asr/stream")
async def asr_stream(ws: WebSocket):
    """流式 ASR：前端 → (config) → 二进制 PCM → DashScope → 整句回传。

    前端协议：
      - 可选 {type:"config", sample_rate:16000}（可跳过，默认 16k）
      - 之后发二进制 PCM 帧（16kHz mono 16bit，任意块大小）
      - {type:"stop"} 结束并返回剩余整句
    后端回传：{type:"final", text} / {type:"done", text} / {type:"error", message}
    """
    await ws.accept()
    if not dashscope.is_available():
        await ws.send_text(json.dumps({"type": "error", "message": "DashScope ASR 未配置（DASHSCOPE_API_KEY 缺失）"}))
        await ws.close()
        return

    sample_rate = 16000
    sess = dashscope.DashScopeASR(sample_rate=sample_rate)
    try:
        await sess.connect()
    except Exception as e:  # noqa: BLE001
        log.warning("[voice/asr/stream] 会话启动失败: %s", e)
        await ws.send_text(json.dumps({"type": "error", "message": str(e)}))
        await ws.close()
        return

    async def forwarder():
        """把 DashScope 事件转发给前端。"""
        try:
            while True:
                kind, payload = await sess.events.get()
                if kind == "final":
                    await ws.send_text(json.dumps({"type": "final", "text": payload}))
                elif kind == "error":
                    await ws.send_text(json.dumps({"type": "error", "message": payload}))
                elif kind == "finished":
                    break
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass

    fwd = asyncio.ensure_future(forwarder())
    try:
        while True:
            try:
                msg = await ws.receive()
            except WebSocketDisconnect:
                break
            except Exception:  # noqa: BLE001
                break
            mtype = msg.get("type") if isinstance(msg, dict) else ""
            if mtype == "websocket.disconnect":
                break
            if mtype == "websocket.receive" and msg.get("text") is not None:
                try:
                    data = json.loads(msg["text"])
                except ValueError:
                    continue
                if data.get("type") == "config" and data.get("sample_rate"):
                    sample_rate = int(data["sample_rate"])
                elif data.get("type") == "stop":
                    final = await sess.finalize()
                    try:
                        await ws.send_text(json.dumps({"type": "done", "text": "".join(final)}))
                    except Exception:  # noqa: BLE001
                        pass
                    break
            elif mtype == "websocket.receive" and msg.get("bytes") is not None:
                try:
                    await sess.send_audio(msg["bytes"])
                except Exception as e:  # noqa: BLE001
                    await ws.send_text(json.dumps({"type": "error", "message": str(e)}))
                    break
    finally:
        if not fwd.done():
            fwd.cancel()
        await sess.close()
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass
