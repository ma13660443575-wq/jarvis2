# -*- coding: utf-8 -*-
"""
vault/writer.py — 唯一写入口（Obsidian 并发安全）
- 全部写操作收敛到此模块；按文件粒度 threading.Lock。
- 原子写：先写同目录 .tmp 再 os.replace，避免 Obsidian 读到半截文件。
- frontmatter 更新用行级正则定点替换（不整体 YAML dump，保留格式与引号）。
- 写后 300ms 冷却，给 Obsidian 文件监听留缓冲。
"""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path

from .. import core

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(rel_path: str) -> threading.Lock:
    with _locks_guard:
        if rel_path not in _locks:
            _locks[rel_path] = threading.Lock()
        return _locks[rel_path]


def _resolve(rel_path: str) -> Path | None:
    return core.resolve_in_vault(rel_path)


def write_file(rel_path: str, content: str) -> dict:
    """原子写入（覆盖）。返回 {ok, rel_path, size}。"""
    p = _resolve(rel_path)
    if p is None:
        return {"ok": False, "error": "路径越界或非法"}
    p.parent.mkdir(parents=True, exist_ok=True)
    lock = _lock_for(rel_path)
    with lock:
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(p)
        time.sleep(0.3)
    return {"ok": True, "rel_path": rel_path, "size": len(content)}


def read_text(rel_path: str) -> str | None:
    p = _resolve(rel_path)
    if p is None or not p.exists():
        return None
    return p.read_text(encoding="utf-8", errors="replace")


def snapshot_file(rel_path: str) -> dict:
    """读取文件当前状态，供多文件提交失败时回滚。

    snapshot 只在进程内使用，content 保留为 bytes，避免换行符或编码在回滚时
    被重新解释。不存在的文件也要记录，便于回滚时删除新建文件。
    """
    p = _resolve(rel_path)
    if p is None:
        return {"ok": False, "error": "路径越界或非法"}
    lock = _lock_for(rel_path)
    with lock:
        if not p.exists():
            return {"ok": True, "rel_path": rel_path, "exists": False, "content": b""}
        if not p.is_file():
            return {"ok": False, "error": "目标不是普通文件"}
        try:
            content = p.read_bytes()
        except OSError as e:
            return {"ok": False, "error": f"读取快照失败: {e}"}
    return {"ok": True, "rel_path": rel_path, "exists": True, "content": content}


def restore_file(rel_path: str, snapshot: dict) -> dict:
    """恢复 snapshot_file 生成的文件状态。"""
    p = _resolve(rel_path)
    if p is None or not isinstance(snapshot, dict) or not snapshot.get("ok"):
        return {"ok": False, "error": "恢复目标或快照非法"}
    lock = _lock_for(rel_path)
    with lock:
        try:
            if snapshot.get("exists"):
                content = snapshot.get("content", b"")
                if not isinstance(content, bytes):
                    return {"ok": False, "error": "快照内容格式非法"}
                p.parent.mkdir(parents=True, exist_ok=True)
                tmp = p.with_suffix(p.suffix + ".rollback.tmp")
                tmp.write_bytes(content)
                tmp.replace(p)
            elif p.exists():
                if not p.is_file():
                    return {"ok": False, "error": "回滚目标不是普通文件"}
                p.unlink()
            time.sleep(0.3)
        except OSError as e:
            return {"ok": False, "error": f"回滚失败: {e}"}
    return {"ok": True, "rel_path": rel_path}


def _fm_block_end(raw: str) -> tuple[str, int, int]:
    """返回 (frontmatter 块内文本, 内容起始字节偏移, 闭合 --- 行起始字节偏移)。

    无 frontmatter 返回 ('', 0, 0)。end 指向闭合 `---` 行开头，供 raw[end:] 拼接。
    """
    lines = raw.splitlines(keepends=True)
    if not lines:
        return "", 0, 0
    if lines[0].lstrip("﻿").strip() != "---":
        return "", 0, 0
    start = len(lines[0])
    pos = start
    content: list[str] = []
    for line in lines[1:]:
        if line.strip() == "---":
            return "".join(content), start, pos
        content.append(line)
        pos += len(line)
    return "", 0, 0


def update_frontmatter(rel_path: str, fields: dict) -> dict:
    """定点更新 frontmatter 指定键。新增键插到块尾。"""
    p = _resolve(rel_path)
    if p is None or not p.exists():
        return {"ok": False, "error": "文件不存在或路径越界"}
    lock = _lock_for(rel_path)
    with lock:
        raw = p.read_text(encoding="utf-8", errors="replace")
        fm_text, start, end = _fm_block_end(raw)
        if start == 0:
            return {"ok": False, "error": "无 frontmatter 块"}
        lines = fm_text.splitlines()
        keys = set(fields.keys())
        for i, line in enumerate(lines):
            m = re.match(r"^([A-Za-z_][\w]*)\s*:", line)
            if m and m.group(1) in keys:
                lines[i] = _fmt_fm_line(m.group(1), fields[m.group(1)])
                keys.discard(m.group(1))
        for key in keys:
            lines.append(_fmt_fm_line(key, fields[key]))
        new_fm = "\n".join(lines)
        new_raw = raw[:start] + new_fm + "\n" + raw[end:]
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(new_raw, encoding="utf-8")
        tmp.replace(p)
        time.sleep(0.3)
    return {"ok": True, "rel_path": rel_path, "updated": list(fields.keys())}


def _fmt_fm_line(key: str, value) -> str:
    if isinstance(value, list):
        inner = ", ".join(_quote(v) for v in value)
        return "%s: [%s]" % (key, inner)
    if isinstance(value, bool):
        return "%s: %s" % (key, "true" if value else "false")
    if isinstance(value, (int, float)):
        return "%s: %s" % (key, value)
    s = str(value)
    if s in ("", "null"):
        return "%s: " % key
    return "%s: %s" % (key, _quote(s))


def _quote(s: str) -> str:
    if s.startswith("[[") and s.endswith("]]"):
        return '"%s"' % s
    if re.fullmatch(r"[\w\s\-\./]+", s, flags=re.UNICODE) and " " not in s.strip():
        return s
    return '"%s"' % s.replace('"', "'")


def append_log(line: str, log_rel: str | None = None) -> dict:
    """向训练日志追加一行（读-追加-写，锁内完成）。"""
    from . import paths
    rel = log_rel or paths.TRAINING_LOG
    p = _resolve(rel)
    if p is None:
        return {"ok": False, "error": "日志路径非法"}
    p.parent.mkdir(parents=True, exist_ok=True)
    lock = _lock_for(rel)
    with lock:
        raw = p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
        if not raw.endswith("\n"):
            raw += "\n"
        raw += line + "\n"
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(raw, encoding="utf-8")
        tmp.replace(p)
        time.sleep(0.3)
    return {"ok": True, "rel_path": rel}
