# -*- coding: utf-8 -*-
"""
senses/weather.py — 天气感官
=============================
用 wttr.in（无 key）取城市天气；失败优雅降级（返回空天气 + 原因）。
未配置 CITY 时默认取北京。结果可被每日简报/控制面板展示。
"""
from __future__ import annotations

import logging

from .. import config

log = logging.getLogger("jarvis2.senses.weather")

DESC = "天气（wttr.in，免费无 key）"


def fetch(city: str | None = None, **kwargs) -> dict:
    city = city or "北京"
    try:
        import requests
        r = requests.get(
            f"https://wttr.in/{city}",
            params={"format": "j1", "lang": "zh"},
            timeout=8,
        )
        r.raise_for_status()
        data = r.json()
        cur = (data.get("current_condition") or [{}])[0]
        today = (data.get("weather") or [{}])[0]
        return {
            "ok": True,
            "city": city,
            "temp": cur.get("temp_C"),
            "feels": cur.get("FeelsLikeC"),
            "humidity": cur.get("humidity"),
            "desc": (cur.get("lang_zh") or [{}])[0].get("value") if cur.get("lang_zh") else cur.get("weatherDesc", [{}])[0].get("value", ""),
            "max": today.get("maxtempC"),
            "min": today.get("mintempC"),
        }
    except Exception as e:  # noqa: BLE001
        log.debug("[senses] weather 降级: %s", e)
        return {"ok": False, "error": f"天气获取失败（{e}）。可后续重试或检查网络。"}
