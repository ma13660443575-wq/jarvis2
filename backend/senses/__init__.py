# -*- coding: utf-8 -*-
"""senses — 感官：外部世界信息入口。
每个感官模块实现 fetch()，返回 {ok, ...}，失败一律优雅降级（ok=False + 原因），
绝不抛异常。路由层负责把感官结果暴露给前端 /api/senses/{name}。
"""

from . import files, rss, terminal, weather

_SENSES = {
    "weather": weather,
    "rss": rss,
    "files": files,
    "terminal": terminal,
}


def available() -> list[dict]:
    return [{"name": n, "desc": getattr(m, "DESC", "")} for n, m in _SENSES.items()]


def fetch(name: str, **kwargs) -> dict:
    mod = _SENSES.get(name)
    if mod is None:
        return {"ok": False, "error": f"未知感官: {name}（可用: {', '.join(_SENSES)}）"}
    try:
        return mod.fetch(**kwargs)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{name} 感官异常: {e}"}
