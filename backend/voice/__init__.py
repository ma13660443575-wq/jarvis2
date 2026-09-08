# -*- coding: utf-8 -*-
"""
voice — 语音能力包（火山引擎 · 豆包语音）
- TTS：HTTP NDJSON 单向合成 → mp3 字节
- ASR：WebSocket 双向流式（enable_nonstream 一次性）→ 文本
任何环节失败抛异常，由 router 统一降级为 JSON 错误，绝不 500。
"""
