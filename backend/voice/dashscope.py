# -*- coding: utf-8 -*-
"""
voice/dashscope.py — 阿里云百炼 DashScope 语音（sambert TTS + qwen-audio 流式 ASR）
===================================================================================
- 协议：wss://dashscope.aliyuncs.com/api-ws/v1/inference，header `Authorization: Bearer <key>`
- TTS（Sambert）：run-task(streaming=out, 文本一次性在 input.text) → task-started → 收二进制音频 → task-finished
  ※ Sambert 不支持 continue-task / finish-task，model 即音色。
- ASR（qwen-audio）：run-task(streaming=duplex) → task-started → 发二进制 PCM → 收 result-generated → finish-task → task-finished
- 设计原则：无 key 时 is_available()=False 短路；调用失败抛 RuntimeError（路由层 502/降级）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid

from .. import config
from .edge import clean_for_tts

log = logging.getLogger("jarvis2.voice.dashscope")

_WS_URL = config.DASHSCOPE_WS_URL
_TEXT_TYPE = "PlainText"  # Sambert 文本类型（官方固定）

# ASR 后台接收空闲超时：超过说明用户已长时间无声，自动结束会话
_ASR_IDLE_TIMEOUT = 120


def is_available() -> bool:
    """DashScope 语音是否可用：key 在位。"""
    return config.is_dashscope_enabled()


def _ws_headers() -> dict:
    return {"Authorization": f"Bearer {config.DASHSCOPE_API_KEY}"}


def _new_task_id() -> str:
    return uuid.uuid4().hex


def _event_name(msg: dict) -> str:
    """DashScope 服务端事件名：header.event（主）/ header.action（兼容）。"""
    h = msg.get("header", {}) or {}
    return h.get("event") or h.get("action") or ""


def _first_error(msg: dict) -> str:
    """从 DashScope 错误消息里提取可读文案。"""
    try:
        header = msg.get("header", {}) or {}
        em = header.get("error_message")
        if em:
            return str(em)[:240]
        payload = msg.get("payload", {}) or {}
        message = payload.get("message")
        if message:
            if isinstance(message, dict):
                return json.dumps(message, ensure_ascii=False)[:240]
            return str(message)[:240]
        events = payload.get("events")
        if events and isinstance(events, list):
            return json.dumps(events, ensure_ascii=False)[:240]
    except Exception:  # noqa: BLE001
        pass
    return json.dumps(msg, ensure_ascii=False)[:240]


async def _recv_json(ws, timeout: float = 30.0) -> dict:
    """收一条文本消息 → dict。超时抛 TimeoutError。"""
    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
    if isinstance(raw, bytes):
        return {}
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return {}


# ============================================================
# TTS — sambert 合成
# ============================================================

async def dashscope_tts(text: str, voice: str | None = None, *,
                        format: str = "mp3", sample_rate: int = 48000,
                        timeout: float = 30.0) -> bytes:
    """Sambert 语音合成 → mp3 bytes。失败抛 RuntimeError/ValueError。

    Sambert 特性：streaming=out（非流式输入），文本在 run-task 的 input.text 一次性发送，
    不支持 continue-task / finish-task；model 即音色（parameters 不再传 voice）。
    """
    import websockets

    voice = voice or config.DASHSCOPE_TTS_MODEL
    if voice not in config.DASHSCOPE_TTS_VOICES:
        raise ValueError(f"未知音色：{voice}")
    text = clean_for_tts(text)
    if not text.strip():
        raise ValueError("待合成文本为空")

    async with websockets.connect(
        _WS_URL, additional_headers=_ws_headers(), ping_interval=None, max_size=None
    ) as ws:
        task_id = _new_task_id()
        run_task = {
            "header": {"action": "run-task", "task_id": task_id, "streaming": "out"},
            "payload": {
                "task_group": "audio",
                "task": "tts",
                "function": "SpeechSynthesizer",
                "model": voice,
                "input": {"text": text},
                "parameters": {
                    "text_type": _TEXT_TYPE,
                    "format": format,
                    "sample_rate": sample_rate,
                    "volume": 50,
                    "rate": 1.0,
                    "pitch": 1.0,
                },
            },
        }
        await ws.send(json.dumps(run_task, ensure_ascii=False))

        msg = await _recv_json(ws, timeout=15)
        if _event_name(msg) != "task-started":
            raise RuntimeError(f"DashScope TTS 启动失败：{_first_error(msg) or '无响应'}")

        # 收音频直到 task-finished
        audio = bytearray()
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            if isinstance(raw, str):
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                action = _event_name(msg)
                if action == "task-finished":
                    break
                if action == "task-failed":
                    raise RuntimeError(f"DashScope TTS 合成失败：{_first_error(msg)}")
            else:
                audio.extend(raw)
        if not audio:
            raise RuntimeError("DashScope TTS 未返回有效音频")
        return bytes(audio)


# ============================================================
# ASR — qwen-audio 流式识别会话
# ============================================================

class DashScopeASR:
    """一次 DashScope ASR 会话（全双工）。

    - connect(): 建立 WS + run-task + 等 task-started，并启动后台接收任务。
    - send_audio(pcm): 发二进制 PCM 帧（16kHz 单声道 16bit，块大小任意）。
    - events: asyncio.Queue，产出 ("final", text) / ("error", msg) / ("finished", None)。
    - finalize(): 发送 finish-task，等待收尾，返回已识别整句列表；随后会话自动关闭。
    - close(): 强制关闭。
    """

    def __init__(self, *, model: str | None = None, sample_rate: int = 16000,
                 max_sentence_silence: int = 1300):
        self.model = model or config.DASHSCOPE_ASR_MODEL
        self.sample_rate = sample_rate
        self.max_sentence_silence = max_sentence_silence
        self.events: asyncio.Queue = asyncio.Queue()
        self._ws = None
        self._task_id: str | None = None
        self._drain_task: asyncio.Task | None = None
        self.final_sentences: list[str] = []

    async def connect(self) -> None:
        import websockets
        self._ws = await websockets.connect(
            _WS_URL, additional_headers=_ws_headers(), ping_interval=None, max_size=None
        )
        self._task_id = _new_task_id()
        run_task = {
            "header": {"action": "run-task", "task_id": self._task_id, "streaming": "duplex"},
            "payload": {
                "task_group": "audio",
                "task": "asr",
                "function": "recognition",
                "model": self.model,
                "parameters": {
                    "format": "pcm",
                    "sample_rate": self.sample_rate,
                    "max_sentence_silence": self.max_sentence_silence,
                    "heartbeat": True,
                },
                "input": {},
            },
        }
        await self._ws.send(json.dumps(run_task, ensure_ascii=False))
        msg = await _recv_json(self._ws, timeout=15)
        if _event_name(msg) != "task-started":
            await self.close()
            raise RuntimeError(f"DashScope ASR 启动失败：{_first_error(msg) or '无响应'}")
        self._drain_task = asyncio.ensure_future(self._drain())

    async def send_audio(self, pcm: bytes) -> None:
        if self._ws is None:
            raise RuntimeError("ASR 会话未连接")
        await self._ws.send(pcm)

    async def _drain(self) -> None:
        """后台接收：把 result-generated / task-finished / 错误 写入 events。"""
        try:
            while True:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=_ASR_IDLE_TIMEOUT)
                if isinstance(raw, bytes):
                    continue
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                action = _event_name(msg)
                if action == "result-generated":
                    sentence = msg.get("payload", {}).get("output", {}).get("sentence", {}) or {}
                    text = (sentence.get("text") or "").strip()
                    end = bool(sentence.get("sentence_end"))
                    # 只提交整句（sentence_end=true）：部分结果忽略，避免连续对话重复回显
                    if not text or not end:
                        continue
                    if self.final_sentences and self.final_sentences[-1] == text:
                        continue
                    self.final_sentences.append(text)
                    await self.events.put(("final", text))
                elif action == "task-finished":
                    await self.events.put(("finished", None))
                    break
                elif action == "task-failed":
                    err = _first_error(msg)
                    log.warning("[dashscope/asr] task-failed: %s", err)
                    await self.events.put(("error", err))
                    break
        except asyncio.CancelledError:
            pass
        except Exception as e:  # noqa: BLE001
            log.warning("[dashscope/asr] 后台接收结束: %s", e)
            await self.events.put(("error", str(e)))

    async def finalize(self) -> list[str]:
        """发 finish-task 收尾，返回已识别整句列表。"""
        if self._ws is not None and self._task_id:
            try:
                await self._ws.send(json.dumps({
                    "header": {"action": "finish-task", "task_id": self._task_id,
                               "streaming": "duplex"},
                    "payload": {"input": {}},
                }, ensure_ascii=False))
            except Exception:  # noqa: BLE001
                pass
            if self._drain_task and not self._drain_task.done():
                try:
                    await asyncio.wait_for(self._drain_task, timeout=10)
                except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                    self._drain_task.cancel()
        await self.close()
        return list(self.final_sentences)

    async def close(self) -> None:
        if self._drain_task and not self._drain_task.done():
            self._drain_task.cancel()
            try:
                await self._drain_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._drain_task = None
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001
                pass
            self._ws = None


async def asr_recognize_once(wav_bytes: bytes, timeout: float = 30.0) -> str:
    """一次性识别完整 WAV（16kHz 单声道 16bit PCM）→ 文本。失败抛 RuntimeError/ValueError。

    用于 /api/voice/asr 的 dashscope 引擎分支（非流式场景）。
    """
    import io
    import wave

    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            rate = wf.getframerate()
            ch = wf.getnchannels()
            swidth = wf.getsampwidth()
            pcm = wf.readframes(wf.getnframes())
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"音频格式无法解析（需 16kHz 单声道 WAV）: {e}") from e
    if rate != 16000 or ch != 1 or swidth != 2:
        raise ValueError(f"DashScope ASR 需 PCM 16kHz/单声道/16bit，收到 {rate}Hz/{ch}ch/{swidth * 8}bit")

    sess = DashScopeASR()
    await sess.connect()
    try:
        # 100ms @16kHz mono 16bit = 3200B，分块发送
        for i in range(0, len(pcm), 3200):
            await sess.send_audio(pcm[i:i + 3200])
        final = await sess.finalize()
    finally:
        await sess.close()
    text = "".join(final).strip()
    if not text:
        raise RuntimeError("DashScope ASR 未识别到内容")
    return text
