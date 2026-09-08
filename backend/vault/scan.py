# -*- coding: utf-8 -*-
"""
vault/scan.py — 便捷扫描门面（面向业务的筛选入口）
==================================================
在 reader 全量扫描之上提供语义化查询，供 hands / routers 使用：
scan_inbox / scan_understand / scan_concepts / scan_due_recall / scan_recent。
全部只读；空结果返回 []，绝不抛异常。
"""
from __future__ import annotations

from .. import core
from . import reader


def scan_inbox(status: str | None = None, limit: int = 300) -> list:
    """采集箱 I- 卡。status: 未处理/已处理；None=全部。"""
    notes = reader.search_notes(prefix="I", folder="00_Inbox", limit=limit)
    if status:
        notes = [n for n in notes if (n.fm.get("status") or "未处理") == status]
    return notes


def scan_understand(status: str | None = None, limit: int = 300) -> list:
    """U- 理解卡（04_Knowledge/<域>）。"""
    notes = reader.search_notes(prefix="U", folder="04_Knowledge", limit=limit)
    if status:
        notes = [n for n in notes if n.fm.get("status") == status]
    return notes


def scan_concepts(limit: int = 300) -> list:
    """C- 概念卡（05_Frameworks）。"""
    return reader.search_notes(prefix="C", folder="05_Frameworks", limit=limit)


def scan_applications(limit: int = 100) -> list:
    """A- 应用卡（04_Knowledge/通用知识）。"""
    return reader.search_notes(prefix="A", folder="04_Knowledge", limit=limit)


def scan_retros(limit: int = 30) -> list:
    """L- 复盘卡（02_Areas/个人成长/复盘），按 mtime 倒序。"""
    return reader.search_notes(prefix="L", folder="02_Areas", limit=limit)


def scan_due_recall(limit: int = 500) -> list:
    """今日到期（含过期）的 R- 回忆卡。"""
    today = core.today()
    rs = reader.search_notes(prefix="R", folder="04_Knowledge", limit=limit)
    out = [n for n in rs if str(n.fm.get("due") or "") and str(n.fm.get("due") or "") <= today]
    return sorted(out, key=lambda n: str(n.fm.get("due", "")))


def scan_recent(folder: str | None = None, limit: int = 20) -> list:
    """最近修改的笔记（可限定顶层目录）。"""
    notes = reader.scan_vault()
    if folder:
        notes = [n for n in notes if n.folder.startswith(folder)]
    return sorted(notes, key=lambda n: n.mtime, reverse=True)[:limit]
