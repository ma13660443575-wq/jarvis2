# -*- coding: utf-8 -*-
"""
brain/classify.py — 问题分流
============================
- direct：寒暄/简单指令/无需检索——直接答。
- rag：需要知识库支撑的实质问题——走混合检索 + 上下文注入。
P1 用轻量规则（不调 LLM，保证无 key 也可分流）；后续可升级为 LLM 分类。
"""
from __future__ import annotations

_GREETINGS = {"你好", "您好", "hi", "hello", "嗨", "哈喽", "早上好", "晚上好", "在吗"}
_FAREWELLS = {"再见", "拜拜", "bye", "晚安"}
_THANKS = {"谢谢", "感谢", "多谢", "thanks"}
_STATUS_WORDS = {"你是谁", "你能做什么", "你的功能", "介绍一下你自己", "帮助", "help"}


def classify_question(question: str) -> str:
    q = (question or "").strip().lower()
    if not q:
        return "direct"
    for word in _GREETINGS | _FAREWELLS | _THANKS | _STATUS_WORDS:
        if word in q:
            return "direct"
    return "rag"
