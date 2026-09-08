# -*- coding: utf-8 -*-
"""
engine/executor.py — 工作流执行器
=================================
命令台入口：解析 → 匹配工作流 → 走 5 步契约 → 草稿/直写 → 汇报。
支持管道顺序执行（前一步产出传递给下一步）。
"""
from __future__ import annotations

from .. import core, db
from . import contract, registry


def execute_command(text: str, payload: dict | None = None) -> dict:
    """执行一条命令/工作流。返回 {ok, type, run_id?, runs?, drafts?, report, status}。"""
    parsed = registry.parse_command(text)
    if parsed["type"] == "empty":
        return {"ok": False, "error": "命令为空"}
    if parsed["type"] == "unknown":
        return {"ok": False, "error": "无法识别命令。试试「处理采集箱」「知识提取」等。"}
    if parsed["type"] == "pipeline":
        return _run_pipeline(parsed["steps"], text, payload or {})
    # workflow / skill
    wf_name = parsed.get("name")
    wf = registry.get(wf_name)
    if wf is None:
        return {"ok": False, "error": f"工作流 {wf_name} 未注册"}
    return _run_one(wf, text, payload or {}, otype=parsed["type"])


def _run_one(wf: contract.WorkflowBase, trigger: str, payload: dict,
             otype: str = "workflow") -> dict:
    try:
        result = wf.run(payload)
    except contract.WorkflowError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"工作流执行异常: {e}"}

    run_id = contract.create_run(wf.name, trigger, payload, result)
    if not result["needs_draft"]:
        # 小规模非交互：直接提交
        commit = wf.commit(run_id)
        return {"ok": commit["ok"], "type": otype, "run_id": run_id,
                "status": commit.get("status", "committed"), "report": result["report"],
                "written": commit.get("written", []),
                "payload_out": result.get("payload_out", {}),
                "error": commit.get("error")}

    drafts = contract.get_draft_files(run_id)
    return {"ok": True, "type": otype, "run_id": run_id, "status": "draft",
            "drafts": drafts, "report": result["report"], "note": result["note"],
            "payload_out": result.get("payload_out", {})}


def _run_pipeline(steps: list[str], trigger: str, payload: dict) -> dict:
    """顺序执行多个工作流；产出 accumulate 进 payload 传给下一步。"""
    runs: list[dict] = []
    current_payload = dict(payload)
    for name in steps:
        wf = registry.get(name)
        if wf is None:
            return {"ok": False, "error": f"管道中断：工作流 {name} 未注册", "runs": runs}
        r = _run_one(wf, trigger, current_payload)
        if not r["ok"]:
            return {"ok": False, "error": r.get("error"), "runs": runs}
        runs.append(r)
        if r.get("status") == "draft":
            # 管道中某个环节需要确认：先停，等确认后继续（简化：本次先返回草稿）
            return {"ok": True, "type": "pipeline", "runs": runs, "pending_run": r,
                    "status": "draft", "drafts": r.get("drafts", [])}
        # 直写结果进 payload，供下一步读取
        current_payload.update(r.get("payload_out") or {})
        current_payload["_prev_output"] = r.get("written", [])
    return {"ok": True, "type": "pipeline", "runs": runs, "status": "committed"}


def list_runs(limit: int = 30, status: str | None = None) -> list[dict]:
    return contract.list_runs(limit=limit, status=status)


def get_draft_files(run_id: int) -> list[dict]:
    return contract.get_draft_files(run_id)


def confirm_run(run_id: int) -> dict:
    """用户确认草稿 → 原子写 + 日志 + 标记 committed。"""
    run = _get_run_by_id(run_id)
    if not run:
        return {"ok": False, "error": "run 不存在"}
    wf = registry.get(run["workflow"])
    if wf is None:
        return {"ok": False, "error": f"工作流 {run['workflow']} 未注册"}
    return wf.commit(run_id)


def reject_run(run_id: int) -> dict:
    con = db.schema.get_conn()
    try:
        cur = con.execute(
            "UPDATE runs SET status='rejected', updated_at=? WHERE id=? AND status='draft'",
            (core.now_iso(), run_id),
        )
        con.commit()
        if cur.rowcount == 0:
            return {"ok": False, "error": "run 不存在或非草稿状态"}
        return {"ok": True}
    finally:
        con.close()


def _get_run_by_id(run_id: int) -> dict | None:
    con = db.schema.get_conn()
    try:
        row = con.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return dict(row) if row else None
    finally:
        con.close()
