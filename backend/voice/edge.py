# -*- coding: utf-8 -*-
"""
edge.py — 微软 Edge 神经语音（edge-tts）· JARVIS 双语音
========================================================
- 英文内容 → en-GB-RyanNeural（英式沉稳男声，最接近电影 JARVIS 腔调）
- 中文内容 → zh-CN-YunjianNeural（中文沉稳男声，JARVIS 感中文）
- 按语种分段，逐段合成后拼接 mp3。全程免费、无需 key、不耗额度。
- 网络抖动：内置重试（默认 3 次）；路由层仍会回退火山豆包。
"""
from __future__ import annotations

import asyncio
import logging
import re

import edge_tts

from .. import config

log = logging.getLogger("jarvis2.voice")

VOICE_EN = config.TTS_VOICE_EN or "en-GB-RyanNeural"
VOICE_ZH = config.TTS_VOICE_ZH or "zh-CN-YunjianNeural"
RATE = config.TTS_RATE or "-8%"
RETRIES = config.TTS_RETRIES

_CJK = re.compile(r"[一-鿿㐀-䶿]")
_ASCII = re.compile(r"[A-Za-z0-9]")

# ---- 朗读前轻量 markdown 清洗（消除 **加粗** / [[wikilink]] / 表格 / 代码块等噪声）----
_MD_STEPS = [
    (re.compile(r"```.*?```", re.S), " "),            # 整段代码围栏 → 空格
    (re.compile(r"`([^`]*)`"), r"\1"),                 # 行内代码 → 文本
    (re.compile(r"!\[([^\]]*)\]\([^)]*\)"), r"\1"),    # ![alt](url) → alt
    (re.compile(r"\[([^\]]*)\]\([^)]*\)"), r"\1"),     # [text](url) → text
    (re.compile(r"\[\[([^\]|]*)(?:\|[^\]]*)?\]\]"), r"\1"),  # [[a|b]] → a
    (re.compile(r"^#+\s*", re.M), ""),                 # 标题 # → 去掉
    (re.compile(r"^\s*\|?[:\-|\s]+\|?\s*$", re.M), ""),  # 表格分隔行 |---|---|
    (re.compile(r"\|"), "，"),                          # 表格竖线 → 逗号
    (re.compile(r"\*\*([^*]+)\*\*"), r"\1"),           # **加粗** → 加粗
    (re.compile(r"\*([^*\n]+)\*"), r"\1"),             # *斜体* → 斜体
    (re.compile(r">\s*"), ""),                          # 引用 >
    (re.compile(r"[_]{2,}"), " "),                      # 下划线噪声
    (re.compile(r"\n{3,}"), "\n\n"),                    # 压缩多余空行
]


def clean_for_tts(text: str) -> str:
    """消除 markdown 语法噪声，保留可读正文；空串返回原文。"""
    if not text:
        return text
    out = text
    for pat, repl in _MD_STEPS:
        out = pat.sub(repl, out)
    return out.strip()


def _lang(ch: str):
    """字符语种：zh / en / None（中性，跟随当前段）。"""
    if _CJK.match(ch):
        return "zh"
    if _ASCII.match(ch):
        return "en"
    return None


def _runs(text: str) -> list[tuple[str, str]]:
    """把文本切成连续同语言片段；中性字符（标点/数字/空白）跟随当前段。

    例：'我用 API 对接' → [('zh','我用 '), ('en','API'), ('zh',' 对接')]
    """
    runs: list[tuple[str, str]] = []
    cur = ""
    curlang: str | None = None
    for ch in text:
        l = _lang(ch)
        if l is None:
            cur += ch
            continue
        if curlang is None:
            curlang = l
        if l != curlang:
            if cur.strip():
                runs.append((curlang, cur))
            cur = ch
            curlang = l
        else:
            cur += ch
    if cur.strip():
        runs.append((curlang or "en", cur))
    return runs


async def _synth(text: str, voice: str) -> bytes:
    """单段合成 → mp3 bytes。"""
    buf = bytearray()
    com = edge_tts.Communicate(text, voice=voice, rate=RATE)
    async for chunk in com.stream():
        if chunk["type"] == "audio":
            buf.extend(chunk["data"])
    return bytes(buf)


async def _synth_runs(runs: list[tuple[str, str]]) -> bytes:
    """逐段用对应音色合成，拼接为一个 mp3 字节流。"""
    out = bytearray()
    for lang, text in runs:
        voice = VOICE_EN if lang == "en" else VOICE_ZH
        out.extend(await _synth(text, voice))
    return bytes(out)


def tts_synthesize(text: str) -> bytes:
    """全文合成（带重试）。返回完整 mp3 bytes；失败抛 RuntimeError。"""
    text = clean_for_tts(text)
    if not text or not text.strip():
        raise ValueError("待合成文本为空")
    runs = _runs(text)
    if not runs:
        raise ValueError("待合成文本为空")
    last_exc: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        try:
            audio = asyncio.run(_synth_runs(runs))
            if audio:
                return audio
        except Exception as e:  # noqa: BLE001
            last_exc = e
            log.warning("[edge-tts] 第 %d/%d 次合成失败: %s", attempt, RETRIES, e)
            if attempt < RETRIES:
                asyncio.run(asyncio.sleep(0.6 * attempt))
    raise RuntimeError(f"edge-tts 合成失败: {last_exc}")
