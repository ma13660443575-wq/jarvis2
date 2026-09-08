# -*- coding: utf-8 -*-
"""
vault/reader.py — 只读扫描：遍历 vault、解析 frontmatter、按条件筛选
设计：只读不动文件；扫描带 mtime 缓存；frontmatter 解析失败退化裸读，绝不中断。
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .. import config

_FM_RE = re.compile(r"^﻿?---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_TITLE_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)

# 索引缓存
_lock = threading.Lock()
_cache: list["Note"] | None = None
_cache_key: tuple | None = None

_SKIP_DIRS = {".obsidian", ".trash", ".git", ".playwright-mcp", "node_modules", ".claude", ".claudian"}


@dataclass
class Note:
    rel_path: str          # 相对 vault 的 posix 路径
    folder: str            # 顶层文件夹，如 04_Knowledge
    filename: str
    title: str
    fm: dict
    body: str
    raw: str
    mtime: float

    @property
    def prefix(self) -> str:
        """从文件名推断前缀（I-/U-/C-...）。MOC/SOP/S 需在单字母前匹配。"""
        name = self.filename[:-3] if self.filename.endswith(".md") else self.filename
        for p in ("MOC", "SOP", "S", "I", "U", "C", "R", "A", "L", "T", "P"):
            if name.startswith(p + "-"):
                return p
        return ""

    @property
    def wikilink(self) -> str:
        return "[[%s]]" % self.rel_path[:-3]


def parse_frontmatter(text: str) -> tuple[dict, str]:
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    raw_fm = m.group(1)
    try:
        fm = yaml.safe_load(raw_fm) or {}
        if not isinstance(fm, dict):
            fm = {}
    except Exception:
        fm = {}
    body = text[m.end():]
    return fm, body


def _first_title(filename: str, body: str) -> str:
    m = _TITLE_RE.search(body)
    if m:
        return m.group(1).strip()
    return filename.replace(".md", "")


def _build_notes(base: Path, files: list[Path]) -> list[Note]:
    notes: list[Note] = []
    for p in files:
        try:
            rel = p.relative_to(base)
            parts = rel.parts
            if any(seg in _SKIP_DIRS or seg.startswith(".") for seg in parts):
                continue
            raw = p.read_text(encoding="utf-8", errors="replace")
            fm, body = parse_frontmatter(raw)
            notes.append(Note(
                rel_path=rel.as_posix(),
                folder=parts[0] if len(parts) > 1 else "",
                filename=p.name,
                title=_first_title(p.name, body),
                fm=fm,
                body=body,
                raw=raw,
                mtime=p.stat().st_mtime,
            ))
        except Exception:
            continue
    return notes


def scan_vault(force: bool = False) -> list[Note]:
    """全量扫描 vault（带 mtime 缓存）。返回 Note 列表。"""
    global _cache, _cache_key
    base = Path(config.VAULT_PATH)
    try:
        mtimes = [(p.stat().st_mtime_ns, p) for p in base.rglob("*.md")]
        mtimes.sort()
    except OSError:
        return _cache or []
    key = tuple(m[0] for m in mtimes)
    with _lock:
        if not force and _cache is not None and _cache_key == key:
            return _cache
        notes = _build_notes(base, [p for _, p in mtimes])
        _cache = notes
        _cache_key = key
        return notes


def get_note(rel_path: str) -> Note | None:
    """按相对路径取单条（安全校验，拒绝越界）。"""
    from .. import core
    safe = core.resolve_in_vault(rel_path)
    if safe is None or not safe.is_file():
        return None
    raw = safe.read_text(encoding="utf-8", errors="replace")
    fm, body = parse_frontmatter(raw)
    rel = safe.relative_to(config.VAULT_PATH).as_posix()
    return Note(rel_path=rel, folder=rel.split("/")[0], filename=safe.name,
                title=_first_title(safe.name, body), fm=fm, body=body,
                raw=raw, mtime=safe.stat().st_mtime)


def find_by_ref(ref) -> Note | None:
    """按 wikilink 引用找笔记：兼容 [[C-主动回忆]]、[[05_Frameworks/C-主动回忆]]、带/不带 .md。"""
    if not ref:
        return None
    ref = str(ref).strip().strip("[]")
    if not ref.endswith(".md"):
        ref += ".md"
    n = get_note(ref)
    if n is not None:
        return n
    target_name = ref.rsplit("/", 1)[-1]
    for note in scan_vault():
        if note.filename == target_name:
            return note
    return None


def search_notes(q: str | None = None, prefix: str | None = None,
                 folder: str | None = None, status: str | None = None,
                 limit: int = 100) -> list[Note]:
    """按元数据筛选。"""
    notes = scan_vault()
    ql = (q or "").lower()
    out = []
    for n in notes:
        if folder and not n.folder.startswith(folder):
            continue
        if prefix and n.prefix.lower() != prefix.lower():
            continue
        if status and n.fm.get("status") != status:
            continue
        if ql and ql not in n.title.lower() and ql not in n.rel_path.lower():
            continue
        out.append(n)
    out.sort(key=lambda x: x.mtime, reverse=True)
    return out[:limit]
