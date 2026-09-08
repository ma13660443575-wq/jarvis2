# -*- coding: utf-8 -*-
"""
volcano.py — 火山引擎「豆包语音」客户端（兼容新版/旧版控制台凭证）
=======================================
- TTS：POST openspeech.bytedance.com/api/v3/tts/unidirectional（HTTP NDJSON，base64 音频块拼接）
- ASR：wss openspeech.bytedance.com/api/v3/sauc/bigmodel_async（WebSocket，compact 帧协议，负载 gzip）

鉴权二选一（勿混用）：
  新版：X-Api-Key: <key>
  旧版（本机在用）：
    TTS：X-Api-App-Id + X-Api-Access-Key + X-Api-Resource-Id
    ASR：X-Api-App-Key(=AppId) + X-Api-Access-Key(=AccessToken) + X-Api-Resource-Id
         + X-Api-Request-Id + X-Api-Connect-Id
  注意：旧版 AccessToken 直接作为 X-Api-Access-Key 的值，**不要**拼任何后缀。

音频约定：ASR 要求 PCM 16kHz 单声道 16bit；TTS 输出 mp3（默认）。
未配置 key 时调用方应先经 config.is_voice_enabled() 短路，本模块假定已配置。

ASR compact 帧协议（参考已验证实现 talky / archibate）：
  byte0 = 0x11（protocol_version=1, header_size=1）
  byte1 = message_type<<4 | flags（type: 1=full-client, 2=audio-only; flags: 0b0010=last）
  byte2 = serialization<<4 | compression（0x1=JSON, gzip）
  byte3 = 0x00
  随后 4 字节大端负载长度 + gzip 压缩负载。
"""
from __future__ import annotations

import base64
import gzip
import json
import logging
import os
import re
import struct
import time
import wave
from io import BytesIO

from .. import config

log = logging.getLogger("jarvis2.voice")

TTS_URL = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
ASR_URL = config.VOLCANO_ASR_ENDPOINT

# 单次 TTS 文本上限（火山限制，按标点分句后逐句合成）
_TTS_MAX_CHARS = 180

# ============================================================
# 鉴权 headers
# ============================================================


def _req_id() -> str:
    return f"jarvis2-{int(time.time() * 1000)}"


def _auth_headers() -> dict[str, str]:
    """TTS 鉴权头：新版用 X-Api-Key，旧版用 X-Api-App-Id + X-Api-Access-Key。"""
    headers = {
        "X-Api-Resource-Id": config.VOLCANO_TTS_RESOURCE_ID,
        "X-Api-Request-Id": _req_id(),
    }
    if config.VOLCANO_API_KEY:
        headers["X-Api-Key"] = config.VOLCANO_API_KEY
    else:
        headers["X-Api-App-Id"] = config.VOLCANO_APP_ID
        headers["X-Api-Access-Key"] = config.VOLCANO_ACCESS_TOKEN
    return headers


def _asr_auth_headers() -> dict[str, str]:
    """ASR 鉴权头：新版仅 X-Api-Key（勿混旧版头），旧版用 X-Api-App-Key + X-Api-Access-Key。"""
    common = {
        "X-Api-Resource-Id": config.VOLCANO_ASR_RESOURCE_ID,
        "X-Api-Request-Id": _req_id(),
        "X-Api-Connect-Id": f"jarvis2-conn-{int(time.time() * 1000)}",
    }
    if config.VOLCANO_API_KEY:
        return {"X-Api-Key": config.VOLCANO_API_KEY, **common}
    return {"X-Api-App-Key": config.VOLCANO_APP_ID,
            "X-Api-Access-Key": config.VOLCANO_ACCESS_TOKEN, **common}


# ============================================================
# TTS — HTTP NDJSON 单向合成
# ============================================================


def _split_sentences(text: str) -> list[str]:
    """按中文标点分句，单句不超过 _TTS_MAX_CHARS。"""
    parts = re.split(r"(?<=[。！？；\n])", text.strip())
    out: list[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        while len(part) > _TTS_MAX_CHARS:
            out.append(part[:_TTS_MAX_CHARS])
            part = part[_TTS_MAX_CHARS:]
        if part:
            out.append(part)
    return out or [text.strip()]


def _tts_one(text: str, timeout: float = 15.0) -> bytes:
    """合成单句 → mp3 bytes。响应为逐行 JSON：code==0 的 data 是 base64 音频块，code==20000000 结束。"""
    body = {
        "user": {"uid": "jarvis2"},
        "req_params": {
            "text": text,
            "speaker": config.VOLCANO_TTS_SPEAKER,
            "sample_rate": 24000,
            "audio_params": {
                "format": config.VOLCANO_TTS_FORMAT,
                "bit_rate": 64000,
                "speech_rate": 0,
                "loudness_rate": 0,
            },
            "additions": '{"disable_markdown_filter":true}',
        },
    }
    import requests

    resp = requests.post(
        TTS_URL,
        headers={**_auth_headers(), "Content-Type": "application/json"},
        json=body,
        timeout=timeout,
        stream=True,
    )
    if resp.status_code != 200:
        # 提取火山返回的可读错误（resource not granted 等），而非裸 HTTP 状态码
        msg = ""
        try:
            err = resp.json()
            msg = (err.get("header") or {}).get("message") or err.get("message") or ""
        except Exception:  # noqa: BLE001
            msg = ""
        if not msg:
            msg = resp.text[:160] if resp.text else f"HTTP {resp.status_code}"
        raise RuntimeError(f"{msg}")

    chunks: list[bytes] = []
    for line in resp.iter_lines(decode_unicode=False):
        if not line:
            continue
        try:
            obj = json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        code = obj.get("code")
        if code == 0 and obj.get("data"):
            chunks.append(base64.b64decode(obj["data"]))
        elif code == 20000000:  # 结束标记（不是 0，勿提前退出）
            break
        elif code is not None and code != 0:
            log.warning("[tts] 火山返回异常 code=%s msg=%s", code, obj.get("message"))
            break
    return b"".join(chunks)


def tts_synthesize(text: str) -> bytes:
    """全文合成：分句逐句合成后拼接。返回完整 mp3 bytes。"""
    if not text or not text.strip():
        raise ValueError("待合成文本为空")
    audio = b"".join(_tts_one(seg) for seg in _split_sentences(text))
    if not audio:
        raise RuntimeError("火山 TTS 未返回有效音频")
    return audio


# ============================================================
# ASR — WebSocket 一次性识别（compact 帧协议，负载 gzip）
# ============================================================

# 帧头常量（compact 协议，byte1 = message_type<<4 | flags）
_MSG_FULL_CLIENT = 0x10  # 全量请求（JSON 配置）
_MSG_AUDIO = 0x20        # 仅音频
_FLAG_LAST = 0b0010      # 最后一帧标记

_HDR_FULL_CLIENT = bytes([0x11, _MSG_FULL_CLIENT, 0x11, 0x00])  # JSON + gzip
_HDR_AUDIO = bytes([0x11, _MSG_AUDIO, 0x01, 0x00])              # raw + gzip
_HDR_AUDIO_LAST = bytes([0x11, _MSG_AUDIO | _FLAG_LAST, 0x01, 0x00])

# 100ms @16kHz mono 16bit = 3200 B
_ASR_CHUNK = 3200


def _build_frame(header: bytes, payload: bytes) -> bytes:
    """构造 compact 帧：4B 头 + 4B 大端负载长度 + gzip 负载。"""
    body = gzip.compress(payload)
    return header + struct.pack(">I", len(body)) + body


def _parse_server_frame(frame: bytes) -> tuple[int, int, dict]:
    """解析服务器帧 → (msg_type, flags, payload)。payload 为 dict（含 _flags / 可能的 _error）。"""
    if len(frame) < 4:
        return 0, 0, {"_flags": 0}
    msg_type = frame[1] >> 4
    flags = frame[1] & 0x0F
    compressed = (frame[2] & 0x0F) == 0x01
    off = 4

    def _body(off: int) -> bytes:
        size = struct.unpack(">I", frame[off:off + 4])[0]
        return frame[off + 4: off + 4 + size]

    if msg_type == 0x0F:  # 错误帧：code + size + body
        try:
            code = struct.unpack(">I", frame[off:off + 4])[0]
            off += 4
        except struct.error:
            code = -1
        body = _body(off)
        if compressed or body[:2] == b"\x1f\x8b":
            try:
                body = gzip.decompress(body)
            except Exception:  # noqa: BLE001
                pass
        return msg_type, flags, {
            "_error": code,
            "message": body.decode("utf-8", "replace"),
            "_flags": flags,
        }

    if flags & 0x01:  # 带序号帧
        off += 4
    body = _body(off)
    if compressed or body[:2] == b"\x1f\x8b":
        try:
            body = gzip.decompress(body)
        except Exception:  # noqa: BLE001
            pass
    try:
        obj = json.loads(body) if body else {}
    except (ValueError, UnicodeDecodeError):
        obj = {}
    obj["_flags"] = flags
    return msg_type, flags, obj


async def _recv_frame(ws, timeout: float) -> dict | None:
    """收一帧 → dict。超时/断连返回 None。"""
    try:
        frame = await asyncio_wait_recv(ws, timeout)
    except TimeoutError:
        log.warning("[asr] 识别超时，返回已收集文本")
        return None
    if not frame:
        return None
    if isinstance(frame, str):
        frame = frame.encode("latin-1")
    _mt, _fl, payload = _parse_server_frame(frame)
    return payload


async def asr_recognize(wav_bytes: bytes, timeout: float = 30.0) -> str:
    """WAV bytes → 文本。内部用 wave 解出 PCM（16kHz mono），WebSocket 一次性识别。"""
    import websockets

    # 1. 解析 WAV → 校验 16kHz mono → 取原始 PCM
    try:
        with wave.open(BytesIO(wav_bytes), "rb") as w:
            rate, channels, swidth = w.getframerate(), w.getnchannels(), w.getsampwidth()
            pcm = w.readframes(w.getnframes())
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"音频格式无法解析（需 16kHz 单声道 WAV）: {e}") from e
    if rate != 16000 or channels != 1 or swidth != 2:
        raise ValueError(f"火山 ASR 需 PCM 16kHz/单声道/16bit，收到 {rate}Hz/{channels}ch/{swidth*8}bit")

    try:
        async with websockets.connect(ASR_URL, additional_headers=_asr_auth_headers(),
                                      ping_interval=None, max_size=None) as ws:
            return await _asr_session(ws, pcm, rate, channels, timeout)
    except websockets.exceptions.InvalidStatus as e:
        # 握手失败（如 resource not granted / 鉴权被拒）→ 透出可读错误
        body = ""
        try:
            body = e.response.body.decode("utf-8", "replace") if e.response.body else ""
        except Exception:  # noqa: BLE001
            body = ""
        raise RuntimeError(body or f"火山 ASR 握手失败: HTTP {e.response.status_code}") from e


async def _asr_session(ws, pcm: bytes, rate: int, channels: int, timeout: float) -> str:
    """WebSocket 已连上后的识别会话：发配置→发音频→收结果。"""
    meta = {
        "user": {"uid": "jarvis2", "platform": "windows"},
        "audio": {"format": "pcm", "codec": "raw", "rate": rate, "bits": 16, "channel": channels},
        "request": {
            "model_name": "bigmodel",
            "enable_itn": True,
            "enable_punc": True,
            "enable_ddc": False,
            "show_utterances": False,
            "result_type": "single",
        },
    }

    # 1. 配置帧 + ack
    await ws.send(_build_frame(_HDR_FULL_CLIENT, json.dumps(meta).encode("utf-8")))
    ack = await _recv_frame(ws, timeout)
    if ack is None:
        raise RuntimeError("火山 ASR 未返回配置确认")
    if "_error" in ack:
        raise RuntimeError(f"火山 ASR 配置被拒: {ack.get('message')}")

    # 2. 音频帧（分 100ms = 3200B 块，最后一块带 last 标记）
    if not pcm:
        await ws.send(_build_frame(_HDR_AUDIO_LAST, b"\x00\x00"))
    else:
        for i in range(0, len(pcm), _ASR_CHUNK):
            payload = pcm[i:i + _ASR_CHUNK]
            is_last = (i + _ASR_CHUNK) >= len(pcm)
            await ws.send(_build_frame(_HDR_AUDIO_LAST if is_last else _HDR_AUDIO, payload))

    # 3. 收结果直到 last 帧
    texts: list[str] = []
    while True:
        obj = await _recv_frame(ws, timeout)
        if obj is None:
            break
        if "_error" in obj:
            raise RuntimeError(f"火山 ASR 错误: {obj.get('message')}")
        res = obj.get("result")
        if res:
            if isinstance(res, dict):
                t = res.get("text", "")
            else:
                t = res[0].get("text", "") if res else ""
            if t:
                texts.append(t)
        if obj.get("_flags", 0) & _FLAG_LAST:
            break

    text = "".join(texts).strip()
    if not text:
        raise RuntimeError("火山 ASR 未识别到内容")
    return text


async def asyncio_wait_recv(ws, timeout: float):
    import asyncio
    return await asyncio.wait_for(ws.recv(), timeout=timeout)
