# -*- coding: utf-8 -*-
"""
brain/classify.py — 问题分流
============================
- direct：寒暄/简单指令/无需检索——直接答。
- rag：需要知识库支撑的实质问题——走混合检索 + 上下文注入。
P1 用轻量规则（不调 LLM，保证无 key 也可分流）；后续可升级为 LLM 分类。
"""
from __future__ import annotations

from enum import Enum

_GREETINGS = {"你好", "您好", "hi", "hello", "嗨", "哈喽", "早上好", "晚上好", "在吗"}
_FAREWELLS = {"再见", "拜拜", "bye", "晚安"}
_THANKS = {"谢谢", "感谢", "多谢", "thanks"}
_STATUS_WORDS = {"你是谁", "你能做什么", "你的功能", "介绍一下你自己", "帮助", "help"}


class LightweightInteraction(str, Enum):
    ACK = "ack"
    THANKS = "thanks"
    NONE = "none"


_LIGHTWEIGHT_ACKS = frozenset({
    "好", "好的", "嗯", "收到", "知道了", "明白", "明白了", "ok",
})
_LIGHTWEIGHT_THANKS = frozenset({
    "谢谢", "谢了", "thanks", "thank you",
})


def classify_lightweight_interaction(text: object) -> LightweightInteraction:
    """匹配完整、低歧义的 acknowledgement，不调用 LLM 或模糊匹配。"""
    if not isinstance(text, str):
        return LightweightInteraction.NONE
    normalized = " ".join(text.strip().split()).casefold()
    if normalized in _LIGHTWEIGHT_ACKS or normalized == "确认":
        return LightweightInteraction.ACK
    if normalized in _LIGHTWEIGHT_THANKS:
        return LightweightInteraction.THANKS
    return LightweightInteraction.NONE


def classify_question(question: str) -> str:
    q = (question or "").strip().lower()
    if not q:
        return "direct"
    for word in _GREETINGS | _FAREWELLS | _THANKS | _STATUS_WORDS:
        if word in q:
            return "direct"
    return "rag"
