# -*- coding: utf-8 -*-
"""
core.py — 通用工具：日期、slug、vault 内路径安全校验。
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path, PureWindowsPath

from . import config


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def slugify(name: str, max_len: int = 40) -> str:
    """把任意名称转成安全的文件名片段（保留中英文/数字/连字符）。"""
    s = re.sub(r"[^\w一-鿿\-]+", "-", name.strip(), flags=re.UNICODE)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s[:max_len] or "untitled"


def resolve_under(root: str | Path, rel_path: str) -> Path | None:
    """安全解析某个根目录下的相对路径。

    不能用字符串 ``startswith`` 判断目录包含关系：例如 ``vault-old``
    会被误判为 ``vault`` 的子目录。这里统一在 ``resolve`` 后用
    ``relative_to`` 判断，并额外拒绝 Windows drive path、NUL 和绝对路径。
    """
    if not isinstance(rel_path, str):
        return None
    raw = rel_path.strip().replace("\\", "/")
    if not raw or "\x00" in raw:
        return None
    if raw.startswith("/") or PureWindowsPath(raw).drive:
        return None

    try:
        root_path = Path(root).expanduser().resolve()
        abs_path = (root_path / Path(raw)).resolve()
        abs_path.relative_to(root_path)
    except (OSError, RuntimeError, ValueError):
        return None
    return abs_path


def resolve_in_vault(rel_path: str) -> Path | None:
    """把 vault 相对路径解析为绝对路径；越界或非法路径返回 None。

    约定：外部传入的相对路径统一正斜杠 / ；内部存 frontmatter/wikilink 也用正斜杠。
    """
    return resolve_under(config.VAULT_PATH, rel_path)


def wl(rel_path: str) -> str:
    """转 wikilink（去掉 .md 扩展名，正斜杠）。"""
    p = rel_path.replace("\\", "/")
    if p.lower().endswith(".md"):
        p = p[:-3]
    return "[[%s]]" % p
