# -*- coding: utf-8 -*-
"""
engine/contract.py — 工作流 5 步契约 + 草稿状态机
=================================================
每个工作流 = 前置检查(precheck) → 执行产草稿(execute) → 完成验证(verify)
          → 日志(log) → 汇报(report)。

写入纪律：文件数 > 阈值 或 交互式工作流 一律先进草稿（DB 落盘），
用户在前端确认后才原子写 vault + 追加日志。确认前零写入，杜绝污染。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .. import core, db

# 文件数超过该值强制走草稿确认
DRAFT_THRESHOLD = 5


@dataclass
class DraftFile:
    rel_path: str
    content: str
    action: str = "create"          # create | update

    def to_dict(self) -> dict:
        return {"rel_path": self.rel_path, "content": self.content, "action": self.action}


class WorkflowError(Exception):
    pass


class WorkflowBase:
    """工作流基类：子类实现 precheck/execute/verify/build_log/build_report。

    - interactive=True：永远先产草稿，等用户确认（内容生成类工作流默认如此）。
    - 返回值约定：
        execute → {"drafts": [DraftFile...], "updates": [{rel_path, fields}...],
                   "note": str, "payload_out": dict}
        precheck → {"ok": True} 或 {"ok": False, "error": str}
        verify   → {"ok": True} 或 {"ok": False, "errors": [str]}
    """
    name: str = ""                 # 唯一 id，如 wf01
    title: str = ""
    desc: str = ""
    interactive: bool = True

    # ---------- 子类实现 ----------
    def precheck(self, payload: dict) -> dict:
        return {"ok": True}

    def execute(self, payload: dict) -> dict:
        raise NotImplementedError

    def verify(self, payload: dict, result: dict) -> dict:
        return {"ok": True}

    def build_log(self, payload: dict, result: dict) -> str:
        return f"[{self.name}] {self.title}"

    def build_report(self, payload: dict, result: dict) -> str:
        drafts = result.get("drafts", [])
        return f"{self.title}：产出 {len(drafts)} 个文件。"

    # ---------- 引擎调用的编排 ----------
    def run(self, payload: dict) -> dict:
        """执行 5 步契约，返回 run 记录 + 是否进入草稿。"""
        pre = self.precheck(payload)
        if not pre.get("ok"):
            raise WorkflowError(pre.get("error", "前置检查失败"))

        result = self.execute(payload)
        drafts = result.get("drafts", []) or []
        result["drafts"] = [d.to_dict() if isinstance(d, DraftFile) else d for d in drafts]

        ver = self.verify(payload, result)
        if not ver.get("ok"):
            raise WorkflowError("；".join(ver.get("errors", ["完成验证失败"])))

        needs_draft = self.interactive or len(result["drafts"]) > DRAFT_THRESHOLD
        return {
            "workflow": self.name,
            "title": self.title,
            "needs_draft": needs_draft,
            "drafts": result["drafts"],
            "updates": result.get("updates", []),
            "note": result.get("note", ""),
            "payload_out": result.get("payload_out", {}),
            "report": self.build_report(payload, result),
            "log_line": self.build_log(payload, result),
        }

    # ---------- 提交阶段（确认后执行）----------
    def commit(self, run_id: int) -> dict:
        """把草稿写入 vault，并以快照保证多文件提交失败可回滚。"""
        from ..vault import writer
        from ..vault import paths

        run = _get_run(run_id)
        if not run:
            return {"ok": False, "error": "run 不存在"}
        try:
            payload = json.loads(run["payload"] or "{}")
        except (TypeError, ValueError) as e:
            return {"ok": False, "error": f"run payload 损坏: {e}"}
        if not isinstance(payload, dict):
            return {"ok": False, "error": "run payload 格式非法"}
        # failed 允许用户重试；partial 表示回滚也未完全成功，需要人工检查。
        if run["status"] not in ("draft", "pending", "failed"):
            return {"ok": False, "error": "该 run 不处于可提交状态"}
        # 从 drafts 表读草稿文件
        con = db.schema.get_conn()
        try:
            rows = con.execute("SELECT files FROM drafts WHERE run_id=? AND confirmed=0", (run_id,)).fetchall()
        finally:
            con.close()

        operations: list[tuple[str, str, object]] = []
        targets: list[str] = []
        try:
            for row in rows:
                files = json.loads(row["files"] or "[]")
                if not isinstance(files, list):
                    raise WorkflowError("草稿文件列表格式非法")
                for f in files:
                    if not isinstance(f, dict) or not isinstance(f.get("rel_path"), str):
                        raise WorkflowError("草稿缺少合法 rel_path")
                    rel = f["rel_path"]
                    if not isinstance(f.get("content"), str):
                        raise WorkflowError(f"草稿内容不是文本: {rel}")
                    if core.resolve_in_vault(rel) is None:
                        raise WorkflowError(f"草稿路径越界或非法: {rel}")
                    operations.append(("file", rel, f["content"]))
                    targets.append(rel)

            updates = payload.get("_updates", []) or []
            if not isinstance(updates, list):
                raise WorkflowError("frontmatter 更新列表格式非法")
            for up in updates:
                if not isinstance(up, dict) or not isinstance(up.get("rel_path"), str):
                    raise WorkflowError("frontmatter 更新缺少合法 rel_path")
                rel = up["rel_path"]
                fields = up.get("fields")
                if not isinstance(fields, dict):
                    raise WorkflowError(f"frontmatter 字段格式非法: {rel}")
                if core.resolve_in_vault(rel) is None:
                    raise WorkflowError(f"frontmatter 路径越界或非法: {rel}")
                operations.append(("frontmatter", rel, fields))
                targets.append(rel)

            log_rel = paths.TRAINING_LOG
            if core.resolve_in_vault(log_rel) is None:
                raise WorkflowError("训练日志路径非法")
            targets.append(log_rel)
        except (TypeError, ValueError, WorkflowError) as e:
            message = f"提交前检查失败: {e}"
            _set_run_status(run_id, "failed")
            _publish_run(run, run_id, "failed", error=message)
            return {"ok": False, "status": "failed", "error": message, "written": []}

        written: list[str] = []
        snapshots: list[tuple[str, dict]] = []
        seen_targets: set[str] = set()

        try:
            # 同一文件可能同时出现在 drafts 和 _updates 中，只快照一次。
            for rel in targets:
                resolved = core.resolve_in_vault(rel)
                if resolved is None:
                    raise WorkflowError(f"提交路径越界或非法: {rel}")
                key = str(resolved).casefold()
                if key in seen_targets:
                    continue
                seen_targets.add(key)
                snapshot = writer.snapshot_file(rel)
                if not snapshot.get("ok"):
                    raise WorkflowError(f"无法创建文件快照 {rel}: {snapshot.get('error', '未知错误')}")
                snapshots.append((rel, snapshot))

            for kind, rel, value in operations:
                if kind == "file":
                    result = writer.write_file(rel, value)
                    label = rel
                else:
                    result = writer.update_frontmatter(rel, value)
                    label = rel + " (fm)"
                if not result.get("ok"):
                    raise WorkflowError(f"写入失败 {label}: {result.get('error', '未知错误')}")
                if label not in written:
                    written.append(label)

            log_line = payload.get("_log_line") or f"[{run['workflow']}] 完成"
            log_result = writer.append_log(f"{core.now_iso()} · {log_line}", log_rel=paths.TRAINING_LOG)
            if not log_result.get("ok"):
                raise WorkflowError(f"训练日志写入失败: {log_result.get('error', '未知错误')}")

            # 所有文件和日志都成功后，才更新 DB 状态。
            con = db.schema.get_conn()
            try:
                con.execute("UPDATE drafts SET confirmed=1 WHERE run_id=?", (run_id,))
                con.execute("UPDATE runs SET status='committed', updated_at=? WHERE id=?",
                            (core.now_iso(), run_id))
                con.commit()
            finally:
                con.close()
        except Exception as e:  # noqa: BLE001
            rollback_errors: list[str] = []
            for rel, snapshot in reversed(snapshots):
                try:
                    restored = writer.restore_file(rel, snapshot)
                    if not restored.get("ok"):
                        rollback_errors.append(f"{rel}: {restored.get('error', '未知错误')}")
                except Exception as rollback_error:  # noqa: BLE001
                    rollback_errors.append(f"{rel}: {rollback_error}")
            status = "partial" if rollback_errors else "failed"
            message = f"提交失败: {e}"
            if rollback_errors:
                message += "；回滚未完全成功: " + "；".join(rollback_errors)
            _set_run_status(run_id, status)
            _publish_run(run, run_id, status, error=message, written=len(written))
            return {"ok": False, "status": status, "error": message, "written": []}

        _publish_run(run, run_id, "committed", written=len(written))
        return {"ok": True, "status": "committed", "written": written}


def _get_run(run_id: int) -> dict | None:
    con = db.schema.get_conn()
    try:
        row = con.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


def _set_run_status(run_id: int, status: str) -> None:
    con = db.schema.get_conn()
    try:
        con.execute("UPDATE runs SET status=?, updated_at=? WHERE id=?",
                    (status, core.now_iso(), run_id))
        con.commit()
    finally:
        con.close()


def _publish_run(run: dict, run_id: int, status: str, **extra) -> None:
    """SSE 通知失败不能影响提交结果。"""
    try:
        from ..heart import notify
        payload = {"run_id": run_id, "status": status, "workflow": run["workflow"]}
        payload.update(extra)
        notify.publish("runs", payload)
    except Exception:  # noqa: BLE001
        pass


def create_run(workflow: str, trigger: str, payload: dict, result: dict) -> int:
    """建 run 记录；needs_draft 时把草稿文件存 drafts 表。"""
    con = db.schema.get_conn()
    try:
        now = core.now_iso()
        payload["_updates"] = result.get("updates", [])
        payload["_log_line"] = result.get("log_line", "")
        steps = [
            {"step": "precheck", "status": "done"},
            {"step": "execute", "status": "done", "note": result.get("note", "")},
            {"step": "verify", "status": "done"},
        ]
        if result["needs_draft"]:
            status = "draft"
        else:
            status = "pending"   # 待自动提交
        cur = con.execute(
            "INSERT INTO runs (workflow, trigger, status, steps, payload, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (workflow, trigger, status, json.dumps(steps, ensure_ascii=False),
             json.dumps(payload, ensure_ascii=False), now, now),
        )
        run_id = cur.lastrowid
        if result["drafts"]:
            con.execute("INSERT INTO drafts (run_id, files, confirmed, created_at) VALUES (?,?,0,?)",
                        (run_id, json.dumps(result["drafts"], ensure_ascii=False), now))
        con.commit()
        return run_id
    finally:
        con.close()


def list_runs(limit: int = 30, status: str | None = None) -> list[dict]:
    con = db.schema.get_conn()
    try:
        if status:
            rows = con.execute(
                "SELECT * FROM runs WHERE status=? ORDER BY id DESC LIMIT ?", (status, limit)
            ).fetchall()
        else:
            rows = con.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def get_draft_files(run_id: int) -> list[dict]:
    con = db.schema.get_conn()
    try:
        rows = con.execute("SELECT files FROM drafts WHERE run_id=? AND confirmed=0", (run_id,)).fetchall()
        out: list[dict] = []
        for row in rows:
            out.extend(json.loads(row["files"] or "[]"))
        return out
    finally:
        con.close()
