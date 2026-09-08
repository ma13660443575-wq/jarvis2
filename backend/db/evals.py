# -*- coding: utf-8 -*-
"""
db/evals.py — 运行健康评估埋点 + LLM token 记账
===============================================
两张运行态表：
- evals     每次关键动作的结果（chat/command/retro/wake）——成功/失败/耗时/元数据
- llm_usage 每次 LLM 调用的 token 消耗（统一在 brain/llm.py 记录）

vault 文件仍是真相；这里只存应用自身运行态，供「健康度卡片」「系统体检」「每日简报」读取。
"""
from __future__ import annotations

import functools
import json
import time as _time

from .. import core
from . import schema


# ============ evals（动作埋点） ============

def record_eval(kind: str, ok: bool, latency_ms: int | None = None,
                detail: str = "", meta: dict | None = None) -> int:
    """记录一次动作结果。kind ∈ chat/command/retro/wake。失败静默不抛。"""
    con = schema.get_conn()
    try:
        cur = con.execute(
            "INSERT INTO evals (kind, ok, latency_ms, detail, meta, created_at) VALUES (?,?,?,?,?,?)",
            (kind, 1 if ok else 0, latency_ms, detail or "",
             json.dumps(meta or {}, ensure_ascii=False), core.now_iso()),
        )
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def _try_json(s: str) -> dict:
    try:
        return json.loads(s or "{}")
    except Exception:  # noqa: BLE001
        return {}


def recorded(kind: str):
    """路由埋点装饰器：自动记录成功/失败 + 耗时到 evals 表（失败不影响原异常）。"""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            _t0 = _time.time()
            try:
                r = fn(*args, **kwargs)
                record_eval(kind, True, latency_ms=int((_time.time() - _t0) * 1000))
                return r
            except Exception as e:  # noqa: BLE001
                record_eval(kind, False, latency_ms=int((_time.time() - _t0) * 1000),
                            detail=str(e))
                raise
        return wrapper
    return deco


def eval_stats(days: int = 7) -> dict:
    """近 N 天各类动作成功率/平均耗时 + 最近失败明细（供体检建议用）。"""
    con = schema.get_conn()
    try:
        rows = con.execute(
            "SELECT kind, ok, latency_ms, detail, meta, created_at FROM evals "
            "WHERE created_at >= datetime('now', ?, 'localtime') ORDER BY id",
            (f"-{days} days",),
        ).fetchall()
    finally:
        con.close()

    by_kind: dict[str, dict] = {}
    failures: list[dict] = []
    for r in rows:
        k = r["kind"] or "?"
        b = by_kind.setdefault(k, {
            "total": 0, "ok": 0, "fail": 0, "ok_rate": None,
            "latency_list": [], "avg_latency_ms": None,
        })
        b["total"] += 1
        if r["ok"]:
            b["ok"] += 1
        else:
            b["fail"] += 1
            failures.append({
                "kind": k, "detail": r["detail"], "meta": _try_json(r["meta"]),
                "created_at": r["created_at"],
            })
        if r["latency_ms"] is not None:
            b["latency_list"].append(r["latency_ms"])

    out_by_kind: dict[str, dict] = {}
    for k, b in by_kind.items():
        out_by_kind[k] = {
            "total": b["total"], "ok": b["ok"], "fail": b["fail"],
            "ok_rate": round(b["ok"] / b["total"], 3) if b["total"] else None,
            "avg_latency_ms": (round(sum(b["latency_list"]) / len(b["latency_list"]), 1)
                               if b["latency_list"] else None),
        }
    return {
        "days": days,
        "by_kind": out_by_kind,
        "total": len(rows),
        "failures": failures[-10:][::-1],
    }


# ============ llm_usage（token 记账） ============

def record_usage(scene: str, model: str, prompt_tokens: int,
                 completion_tokens: int, total_tokens: int) -> int:
    """记录一次 LLM 调用的 token 消耗。token 缺失时传 0。"""
    con = schema.get_conn()
    try:
        cur = con.execute(
            "INSERT INTO llm_usage (scene, model, prompt_tokens, completion_tokens, total_tokens, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (scene or "", model or "", int(prompt_tokens or 0),
             int(completion_tokens or 0), int(total_tokens or 0), core.now_iso()),
        )
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def usage_stats(days: int = 7, day: str | None = None) -> dict:
    """近 N 天 token 消耗汇总；传 day=YYYY-MM-DD 时只统计当天。"""
    con = schema.get_conn()
    try:
        if day:
            rows = con.execute(
                "SELECT scene, model, prompt_tokens, completion_tokens, total_tokens, created_at "
                "FROM llm_usage WHERE date(created_at)=? ORDER BY id", (day,),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT scene, model, prompt_tokens, completion_tokens, total_tokens, created_at "
                "FROM llm_usage WHERE created_at >= datetime('now', ?, 'localtime') ORDER BY id",
                (f"-{days} days",),
            ).fetchall()
    finally:
        con.close()

    by_scene: dict[str, dict] = {}
    total = 0
    today = 0
    today_str = core.today()
    for r in rows:
        s = r["scene"] or "chat"
        d = by_scene.setdefault(s, {"calls": 0, "total_tokens": 0})
        d["calls"] += 1
        d["total_tokens"] += r["total_tokens"]
        total += r["total_tokens"]
        if str(r["created_at"] or "")[:10] == today_str:
            today += r["total_tokens"]
    return {
        "days": days,
        "calls": len(rows),
        "total_tokens": total,
        "today_tokens": today,
        "by_scene": by_scene,
    }
