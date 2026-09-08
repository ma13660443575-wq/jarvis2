# -*- coding: utf-8 -*-
"""
senses/files.py — 文件感官
===========================
在允许的目录范围内（vault / data）列出文件。安全校验：拒绝越界。
"""
from __future__ import annotations

import logging
from pathlib import Path

from .. import config, core

log = logging.getLogger("jarvis2.senses.files")

DESC = "文件系统（限 vault / data 目录）"


def _safe(base: Path, rel: str) -> Path | None:
    return core.resolve_under(base, rel)


def fetch(path: str = "", limit: int = 30, **kwargs) -> dict:
    base = Path(config.VAULT_PATH).resolve()
    try:
        limit = max(1, min(int(limit), 200))
    except (TypeError, ValueError):
        limit = 30
    p = _safe(base, path or "")
    if p is None:
        return {"ok": False, "error": "路径越界（仅允许访问 vault 目录）。"}
    if not p.exists():
        return {"ok": False, "error": f"路径不存在: {path}"}
    entries = []
    try:
        for child in sorted(p.iterdir())[:limit]:
            if child.name.startswith(".") or child.is_symlink():
                continue
            rel = child.relative_to(base).as_posix()
            entries.append({
                "name": child.name,
                "path": rel,
                "is_dir": child.is_dir(),
                "size": child.stat().st_size if child.is_file() else None,
            })
    except OSError as e:
        return {"ok": False, "error": f"读取失败: {e}"}
    return {"ok": True, "path": path or ".", "entries": entries}
