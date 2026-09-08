# -*- coding: utf-8 -*-
"""
db/settings.py — settings 表（key/value）读写封装
===================================================
- value 统一 JSON 序列化（支持 str/int/bool/list/dict/None）。
- 进程内加锁 + INSERT OR REPLACE，保证并发写安全。
- 惰性 import schema（模块顶层不连库），避免循环依赖。
"""
from __future__ import annotations

import json
import threading

_lock = threading.Lock()

_KEYS: dict[str, object] = {}


def _serialize(value) -> str:
    if value is None:
        return json.dumps(None)
    if isinstance(value, str):
        # 已序列化形态（前端传来的 string 直接存）
        return value
    return json.dumps(value, ensure_ascii=False)


def _deserialize(raw: str, default):
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return raw


def get_setting(key: str, default=None):
    """读单个设置（先内存缓存，DB 兜底）。"""
    with _lock:
        if key in _KEYS:
            return _KEYS[key]
    from . import schema  # 惰性 import，避免循环
    con = schema.get_conn()
    try:
        row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    finally:
        con.close()
    if row is None:
        return default
    val = _deserialize(row["value"], default)
    with _lock:
        _KEYS[key] = val
    return val


def set_setting(key: str, value) -> None:
    """写单个设置：内存缓存 + INSERT OR REPLACE 落库。"""
    raw = _serialize(value)
    from . import schema  # 惰性 import，避免循环
    with _lock:
        _KEYS[key] = value
        con = schema.get_conn()
        try:
            con.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)",
                (key, raw),
            )
            con.commit()
        finally:
            con.close()


def get_all_settings() -> dict:
    """全量设置（DB 优先，未覆盖内存中最新值）。"""
    from . import schema  # 惰性 import，避免循环
    con = schema.get_conn()
    try:
        rows = con.execute("SELECT key, value FROM settings").fetchall()
    finally:
        con.close()
    out: dict = {}
    for r in rows:
        k = r["key"]
        raw = r["value"]
        out[k] = _deserialize(raw, None)
    with _lock:
        _KEYS.update(out)
    return out


def clear_cache() -> None:
    """清空内存缓存（settings 表外改场景下强制刷新）。"""
    with _lock:
        _KEYS.clear()


# ============================================================
# 引擎解析（settings 覆盖 env；供 voice.py / status.py / settings 路由复用）
# ============================================================

def resolve_asr_engine() -> str:
    """当前 ASR 引擎：dashscope / local / volcano。

    - settings `asr_engine` 显式选择 → 优先（dashscope 需 key，否则回退 local）
    - auto（默认）→ DashScope key 在位走 dashscope，否则 env VOICE_ENGINE
    """
    from .. import config  # 惰性 import，避免循环
    choice = get_setting("asr_engine", "auto")
    if choice == "dashscope":
        return "dashscope" if config.is_dashscope_enabled() else "local"
    if choice in ("local", "volcano"):
        return choice
    # auto
    if config.is_dashscope_enabled():
        return "dashscope"
    return config.VOICE_ENGINE


def resolve_tts_engine() -> str:
    """当前 TTS 引擎：dashscope / edge / auto（dashscope 优先，edge 兜底）。"""
    choice = get_setting("tts_engine", "auto")
    if choice in ("dashscope", "edge"):
        return choice
    return "auto"
