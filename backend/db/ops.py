# -*- coding: utf-8 -*-
"""
db/ops.py — 应用运行态 CRUD（会话 / 消息 / 记忆候选）
====================================================
vault 文件是真相，DB 只存运行态（会话记录、消息、待确认记忆候选）。
"""
from __future__ import annotations

import json

from .. import core
from . import schema


# ============ 会话 ============

def get_or_create_conversation(conv_id: int | None = None, title: str = "新对话") -> int:
    con = schema.get_conn()
    try:
        if conv_id:
            row = con.execute("SELECT id FROM conversations WHERE id=?", (conv_id,)).fetchone()
            if row:
                con.execute("UPDATE conversations SET updated_at=? WHERE id=?", (core.now_iso(), conv_id))
                con.commit()
                return conv_id
        cur = con.execute(
            "INSERT INTO conversations (title, created_at, updated_at) VALUES (?,?,?)",
            (title, core.now_iso(), core.now_iso()),
        )
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def list_conversations(limit: int = 50) -> list[dict]:
    con = schema.get_conn()
    try:
        rows = con.execute(
            "SELECT * FROM conversations ORDER BY updated_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def add_message(conversation_id: int, role: str, content: str,
                sources: list[str] | None = None) -> int:
    con = schema.get_conn()
    try:
        cur = con.execute(
            "INSERT INTO messages (conversation_id, role, content, sources, created_at) "
            "VALUES (?,?,?,?,?)",
            (conversation_id, role, content,
             json.dumps(sources or [], ensure_ascii=False), core.now_iso()),
        )
        con.execute("UPDATE conversations SET updated_at=? WHERE id=?", (core.now_iso(), conversation_id))
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def list_messages(conversation_id: int, limit: int = 50) -> list[dict]:
    con = schema.get_conn()
    try:
        rows = con.execute(
            "SELECT * FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT ?",
            (conversation_id, limit),
        ).fetchall()
        out = []
        for r in reversed(rows):
            d = dict(r)
            try:
                d["sources"] = json.loads(d["sources"] or "[]")
            except Exception:
                d["sources"] = []
            out.append(d)
        return out
    finally:
        con.close()


def history_messages(conversation_id: int, max_turns: int = 10) -> list[dict]:
    """转成 LLM messages（含系统外的历史 user/assistant 对话）。"""
    msgs = list_messages(conversation_id, limit=max_turns * 2)
    return [{"role": m["role"], "content": m["content"]} for m in msgs]


# ============ 记忆候选 ============

def add_memory_candidates(cands: list[dict], source_note: str | None = None) -> list[int]:
    if not cands:
        return []
    con = schema.get_conn()
    ids = []
    try:
        now = core.now_iso()
        for c in cands[:5]:
            cur = con.execute(
                "INSERT INTO memory_candidates (kind, summary, detail, status, source_note, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (c.get("kind", "preference"), c.get("summary", ""), c.get("detail", ""),
                 "pending", source_note, now),
            )
            ids.append(cur.lastrowid)
        con.commit()
        return ids
    finally:
        con.close()


def list_memory_candidates(status: str = "pending", limit: int = 50) -> list[dict]:
    con = schema.get_conn()
    try:
        rows = con.execute(
            "SELECT * FROM memory_candidates WHERE status=? ORDER BY id DESC LIMIT ?",
            (status, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def set_memory_candidate_status(cid: int, status: str) -> dict:
    """status: confirmed / rejected。返回 {ok, row?}。"""
    con = schema.get_conn()
    try:
        cur = con.execute(
            "UPDATE memory_candidates SET status=?, confirmed_at=? WHERE id=? AND status='pending'",
            (status, core.now_iso() if status == "confirmed" else None, cid),
        )
        con.commit()
        if cur.rowcount == 0:
            return {"ok": False, "error": "候选不存在或已处理"}
        row = con.execute("SELECT * FROM memory_candidates WHERE id=?", (cid,)).fetchone()
        return {"ok": True, "row": dict(row)}
    finally:
        con.close()


# ============ 任务 ============

def _publish_tasks(action: str, task: dict | None = None) -> None:
    """广播任务看板变化（SSE），失败静默。"""
    try:
        from ..heart import notify
        notify.publish("tasks", {"action": action, "task": task})
    except Exception:  # noqa: BLE001
        pass


def list_tasks(status: str | None = None, limit: int = 50) -> list[dict]:
    con = schema.get_conn()
    try:
        if status:
            rows = con.execute(
                "SELECT * FROM tasks WHERE status=? ORDER BY priority DESC, id DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM tasks ORDER BY status='已完成' ASC, priority DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def add_task(title: str, priority: str = "中", project: str | None = None) -> dict:
    con = schema.get_conn()
    try:
        cur = con.execute(
            "INSERT INTO tasks (title, status, priority, project, created_at) VALUES (?,?,?,?,?)",
            (title, "待办", priority, project, core.now_iso()),
        )
        con.commit()
        row = con.execute("SELECT * FROM tasks WHERE id=?", (cur.lastrowid,)).fetchone()
        task = dict(row)
        _publish_tasks("created", task)
        return {"ok": True, "task": task}
    finally:
        con.close()


def set_task_status(task_id: int, status: str) -> dict:
    if status not in ("待办", "进行中", "已完成"):
        return {"ok": False, "error": "非法状态"}
    con = schema.get_conn()
    try:
        cur = con.execute("UPDATE tasks SET status=? WHERE id=?", (status, task_id))
        con.commit()
        if cur.rowcount == 0:
            return {"ok": False, "error": "任务不存在"}
        row = con.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        task = dict(row)
        _publish_tasks("updated", task)
        return {"ok": True, "task": task}
    finally:
        con.close()


def delete_task(task_id: int) -> dict:
    con = schema.get_conn()
    try:
        cur = con.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        con.commit()
        if cur.rowcount > 0:
            _publish_tasks("deleted", {"id": task_id})
        return {"ok": cur.rowcount > 0}
    finally:
        con.close()
