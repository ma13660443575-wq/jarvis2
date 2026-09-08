# -*- coding: utf-8 -*-
"""
schema.py — JARVIS2 全新 SQLite schema + 连接管理
=================================================
原则：vault 文件是真相，DB 只做应用运行态（会话/草稿/复习/记忆候选/技能/任务/简报）的检索镜像，
避免与 vault 双写漂移。表结构用 WAL + 行级操作。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .. import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id),
    role            TEXT NOT NULL CHECK(role IN ('user','assistant')),
    content         TEXT NOT NULL,
    sources         TEXT DEFAULT NULL,   -- JSON array of vault rel_paths
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow    TEXT NOT NULL,
    trigger     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',   -- pending/draft/committed/rejected/failed/partial
    steps       TEXT NOT NULL DEFAULT '[]',        -- JSON array of {step,status,detail}
    payload     TEXT NOT NULL DEFAULT '{}',        -- JSON（含可选 _plan_id 审计关联）
    report      TEXT DEFAULT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS drafts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL REFERENCES runs(id),
    files       TEXT NOT NULL DEFAULT '[]',        -- JSON array of {rel_path, content, action:create|update}
    confirmed   INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT DEFAULT NULL,
    cards       TEXT NOT NULL DEFAULT '[]'         -- JSON array of {rel_path, linked_card, status}
);

CREATE TABLE IF NOT EXISTS review_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    card_rel    TEXT NOT NULL,
    quality     INTEGER NOT NULL,                  -- SM-2 self-grade 0-5
    interval    REAL NOT NULL,
    easiness    REAL NOT NULL,
    due         TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_candidates (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,                     -- preference/decision/project/learning/life
    summary     TEXT NOT NULL,
    detail      TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'pending',   -- pending/confirmed/rejected
    source_note TEXT DEFAULT NULL,
    created_at  TEXT NOT NULL,
    confirmed_at TEXT DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS skills (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    rel_path    TEXT NOT NULL UNIQUE,              -- 06_Capability/S-*.md
    name        TEXT NOT NULL,
    trigger     TEXT DEFAULT NULL,                 -- JSON array of trigger phrases
    tools       TEXT NOT NULL DEFAULT '[]',        -- JSON array
    source_card TEXT DEFAULT NULL,                 -- [[C-xxx]]
    status      TEXT NOT NULL DEFAULT '可用',       -- 可用/草稿
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT '待办',       -- 待办/进行中/已完成
    priority    TEXT NOT NULL DEFAULT '中',
    project     TEXT DEFAULT NULL,
    created_at  TEXT NOT NULL,
    due_date    TEXT DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS reminders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    message     TEXT NOT NULL,
    remind_at   TEXT NOT NULL,
    fired       INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS briefings (
    date        TEXT PRIMARY KEY,                  -- YYYY-MM-DD
    content     TEXT NOT NULL DEFAULT '[]',        -- JSON array of {title, source, relevance, action, priority}
    raw         TEXT DEFAULT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,               -- chat/command/retro/wake
    ok          INTEGER NOT NULL,            -- 1=成功 0=失败
    latency_ms  INTEGER DEFAULT NULL,
    detail      TEXT NOT NULL DEFAULT '',
    meta        TEXT NOT NULL DEFAULT '{}',  -- JSON: {workflow,source,error,...}
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evals_kind_time ON evals(kind, created_at);

CREATE TABLE IF NOT EXISTS llm_usage (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    scene             TEXT NOT NULL DEFAULT '',   -- chat/retro/briefing/...
    model             TEXT NOT NULL DEFAULT '',
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens      INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_usage_time ON llm_usage(created_at);
"""


def get_conn(db_path: str | None = None) -> sqlite3.Connection:
    p = Path(db_path or config.DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p), check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA foreign_keys=ON;")
    return con


def init_db(db_path: str | None = None) -> None:
    con = get_conn(db_path)
    try:
        con.executescript(_SCHEMA)
        con.commit()
    finally:
        con.close()
