# -*- coding: utf-8 -*-
"""
memory/manager.py — 记忆落库（确认候选 → 写入 用户模型.md 对应分区）
==================================================================
- 用户模型.md 是动态记忆单一文件，按五分区组织（偏好/决策/项目=表格，学习/生活=列表）。
- 确认候选时把一行记录追加到对应分区；成功后再向量 upsert，保持检索镜像同步。
- 写入走 vault/writer 原子写；文件缺失时自动创建最小骨架。
"""
from __future__ import annotations

import re

from .. import core
from ..db import ops as db_ops
from ..vault import paths, reader, writer
from . import vector

# kind → (分区标题前缀, 类型: table|list)
_KIND_MAP = {
    "preference": ("## 一、偏好记忆", "table"),
    "decision":   ("## 二、决策记忆", "table"),
    "project":    ("## 三、项目记忆", "table"),
    "learning":   ("## 四、学习记忆", "list"),
    "life":       ("## 五、生活记忆", "list"),
}

_SKELETON = """---
type: 用户模型
created: {today}
updated: {today}
tags: [系统, 用户模型, 记忆]
---

# 用户模型（动态记忆）

> 由 JARVIS 记忆沉淀自动维护；分区写入，vault 文件为真相。

## 一、偏好记忆 Preference

| 日期 | 偏好 | 证据 / 说明 |
| --- | --- | --- |

## 二、决策记忆 Decision

| 日期 | 决策 | 理由 / 背景 |
| --- | --- | --- |

## 三、项目记忆 Project

| 项目 | 状态 | 关键上下文 |
| --- | --- | --- |

## 四、学习记忆 Learning

- （暂无）

## 五、生活记忆 Life

- （暂无）
"""


def _clean(s: str) -> str:
    s = s.replace("|", "／").replace("\n", " ").replace("\r", " ").strip()
    return s or "（无）"


def _ensure_file() -> str:
    """确保用户模型.md 存在，返回其相对路径。"""
    rel = paths.USER_MODEL
    if writer.read_text(rel) is None:
        writer.write_file(rel, _SKELETON.format(today=core.today()))
    return rel


def _insert(body: str, section: str, new_line: str) -> str:
    """把 new_line 插入 body 中 section 分区末尾（下一个 ## 之前）。"""
    idx = body.find(section)
    if idx == -1:
        return body  # 分区缺失：跳过（骨架应已覆盖）
    seg = body[idx:]
    nxt = len(seg)
    for marker in ("\n## ", "\n---"):
        m = seg.find(marker)
        if m != -1:
            nxt = min(nxt, m)
    before = body[:idx]
    block = seg[:nxt]
    after = seg[nxt:]
    block = block.rstrip("\n") + "\n" + new_line + "\n"
    return before + block + after


def confirm_candidate(cid: int) -> dict:
    """确认一条记忆候选：写入用户模型.md 对应分区。返回 {ok, rel, kind}。"""
    res = db_ops.set_memory_candidate_status(cid, "confirmed")
    if not res["ok"]:
        return res
    row = res["row"]
    kind = row["kind"]
    summary = _clean(row["summary"])
    detail = _clean(row.get("detail") or summary)
    today = core.today()

    if kind not in _KIND_MAP:
        return {"ok": False, "error": "未知记忆类型: %s" % kind}
    section, fmt = _KIND_MAP[kind]

    rel = _ensure_file()
    raw = writer.read_text(rel) or ""
    if fmt == "table":
        new_line = f"| {today} | {summary} | {detail} |"
    else:
        new_line = f"- {summary}（{detail}）" if detail != summary else f"- {summary}"
    body = _insert(raw, section, new_line)
    if body == raw:
        return {"ok": False, "error": "分区未找到: %s" % section}

    w = writer.write_file(rel, body)
    if not w["ok"]:
        return w
    writer.update_frontmatter(rel, {"updated": today})

    # 同步向量镜像（失败静默）
    vector.upsert_note(rel)
    # 实时推送（SSE）
    try:
        from ..heart import notify
        notify.publish("memory", {"action": "confirmed", "cid": cid, "kind": kind, "rel": rel})
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "rel": rel, "kind": kind, "summary": summary}


def reject_candidate(cid: int) -> dict:
    r = db_ops.set_memory_candidate_status(cid, "rejected")
    if r["ok"]:
        try:
            from ..heart import notify
            notify.publish("memory", {"action": "rejected", "cid": cid})
        except Exception:  # noqa: BLE001
            pass
    return r


def pending_candidates() -> list[dict]:
    return db_ops.list_memory_candidates("pending")
