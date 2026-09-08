# -*- coding: utf-8 -*-
"""
senses/terminal.py — 终端感官
==============================
只允许执行白名单内的只读命令（config.SHELL_ALLOWLIST），
防止任意代码执行。默认白名单：git status/log/diff、pip list、python --version、dir、echo。
"""
from __future__ import annotations

import logging
import shlex
import subprocess
import sys
from pathlib import Path

from .. import config

log = logging.getLogger("jarvis2.senses.terminal")

DESC = "终端（仅白名单只读命令）"

_SHELL_CONTROL_CHARS = set("&|;<>`\r\n")
_GIT_READ_ONLY = {"status", "log", "diff"}
_GIT_BLOCKED_ARGS = ("--output", "--ext-diff", "--textconv")


def _split(value: str) -> list[str] | None:
    """把命令拆成 argv；不接受 shell 控制字符。"""
    if any(ch in value for ch in _SHELL_CONTROL_CHARS):
        return None
    try:
        return shlex.split(value, posix=True)
    except ValueError:
        return None


def _allowed(argv: list[str]) -> bool:
    """按 token 前缀匹配白名单，避免字符串前缀绕过。"""
    for raw_prefix in config.SHELL_ALLOWLIST:
        prefix = _split(raw_prefix.strip())
        if prefix and len(argv) >= len(prefix) and argv[:len(prefix)] == prefix:
            return True
    return False


def _builtin(argv: list[str]) -> tuple[str, int] | None:
    """实现 Windows shell 内置命令的只读子集，避免重新启用 shell=True。"""
    name = argv[0].lower()
    if name == "echo":
        return " ".join(argv[1:]) + "\n", 0
    if name == "dir":
        if len(argv) != 1:
            return "dir 感官不接受额外参数。", 2
        base = Path(config.VAULT_PATH)
        try:
            lines = []
            for child in sorted(base.iterdir(), key=lambda p: p.name.lower()):
                if child.name.startswith("."):
                    continue
                suffix = "<DIR>" if child.is_dir() else str(child.stat().st_size)
                lines.append(f"{child.name}\t{suffix}")
            return "\n".join(lines) + ("\n" if lines else ""), 0
        except OSError as e:
            return f"读取目录失败: {e}", 1
    return None


def _safe_argv(argv: list[str]) -> list[str] | None:
    """限制默认白名单中的高风险命令只能执行固定只读形式。"""
    if not argv:
        return None
    name = argv[0].lower()
    if name == "python" and argv != ["python", "--version"]:
        return None
    if name == "pip" and argv != ["pip", "list"]:
        return None
    if name == "git":
        if len(argv) < 2 or argv[1] not in _GIT_READ_ONLY:
            return None
        if any(any(arg.lower().startswith(blocked) for blocked in _GIT_BLOCKED_ARGS)
               for arg in argv[2:]):
            return None
    return argv


def fetch(command: str | None = None, **kwargs) -> dict:
    cmd = (command or "").strip()
    if not cmd:
        return {"ok": False, "error": "缺少命令。", "allowlist": config.SHELL_ALLOWLIST}
    argv = _split(cmd)
    if not argv:
        return {"ok": False, "error": "命令包含不允许的 shell 控制字符或引号格式错误。"}
    if not _allowed(argv):
        return {"ok": False, "error": f"命令不在白名单内。允许的前缀: {', '.join(config.SHELL_ALLOWLIST)}"}
    argv = _safe_argv(argv)
    if not argv:
        return {"ok": False, "error": "该命令只允许执行固定的只读形式。"}

    builtin = _builtin(argv)
    if builtin is not None:
        out, code = builtin
        return {"ok": code == 0, "command": cmd, "stdout": out[:2000],
                "stderr": "" if code == 0 else out[:500], "exit_code": code}

    if argv[0].lower() == "python":
        argv = [sys.executable, "--version"]
    elif argv[0].lower() == "pip":
        argv = [sys.executable, "-m", "pip", "list"]
    try:
        r = subprocess.run(
            argv, shell=False, capture_output=True, text=True, timeout=15,
            cwd=str(config.VAULT_PATH),
        )
        out = (r.stdout or "")[:2000]
        err = (r.stderr or "")[:500]
        return {"ok": r.returncode == 0, "command": cmd,
                "stdout": out, "stderr": err,
                "exit_code": r.returncode}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"执行失败: {e}"}
