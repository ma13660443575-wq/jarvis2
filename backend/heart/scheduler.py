# -*- coding: utf-8 -*-
"""
heart/scheduler.py — 定时调度
==============================
每日简报：启动时懒生成当天简报；随后每天 BRIEF_TIME 左右重新生成。
用轻量 daemon 线程实现，失败静默降级，绝不影响主服务。
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime

from .. import config

log = logging.getLogger("jarvis2")
_stop = threading.Event()
_thread: threading.Thread | None = None


def _daily_brief_loop() -> None:
    """后台循环：启动即生成当天简报（懒生成由 get_today 兜底），然后每天 08:30 重生成。"""
    try:
        from . import briefing
        briefing.generate()
        log.info("[heart] 今日简报已生成")
    except Exception as e:  # noqa: BLE001
        log.warning("[heart] 简报生成失败: %s", e)

    target_hh, target_mm = (int(x) for x in (config.BRIEF_TIME or "08:30").split(":"))
    while not _stop.wait(60):
        now = datetime.now()
        if (now.hour, now.minute) == (target_hh, target_mm):
            try:
                from . import briefing
                briefing.generate()
                log.info("[heart] 每日简报已更新")
            except Exception as e:  # noqa: BLE001
                log.warning("[heart] 每日简报更新失败: %s", e)
            # 睡过这一分钟，避免同分钟重复触发
            _stop.wait(61)


def start() -> None:
    """启动调度器（幂等）。"""
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_daily_brief_loop, name="jarvis2-heart", daemon=True)
    _thread.start()


def stop() -> None:
    """停止调度器。"""
    _stop.set()
