# -*- coding: utf-8 -*-
"""本地安全边界与提交回滚回归测试。"""
from __future__ import annotations

import io
import tarfile

import pytest

from backend import config, core, db
from backend.engine import contract
from backend.memory.vector import _stable_point_id
from backend.senses import terminal
from backend.vault import writer
from backend.voice.archive import safe_extract


def test_resolve_under_rejects_traversal_and_sibling_prefix(tmp_path):
    root = tmp_path / "vault"
    sibling = tmp_path / "vault-old"
    root.mkdir()
    sibling.mkdir()

    assert core.resolve_under(root, "note.md") == (root / "note.md").resolve()
    assert core.resolve_under(root, "../vault-old/secret.md") is None
    assert core.resolve_under(root, str(sibling / "secret.md")) is None
    assert core.resolve_under(root, "C:/outside/secret.md") is None


def test_terminal_rejects_shell_control_chars(monkeypatch):
    monkeypatch.setattr(config, "SHELL_ALLOWLIST", ["echo"])

    result = terminal.fetch("echo hello")
    assert result["ok"] is True
    assert result["stdout"] == "hello\n"

    blocked = terminal.fetch("echo hello & whoami")
    assert blocked["ok"] is False


def test_terminal_uses_argv_without_shell(monkeypatch):
    monkeypatch.setattr(config, "SHELL_ALLOWLIST", ["git status"])
    captured = {}

    class Result:
        returncode = 0
        stdout = "clean"
        stderr = ""

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return Result()

    monkeypatch.setattr(terminal.subprocess, "run", fake_run)
    result = terminal.fetch("git status --short")

    assert result["ok"] is True
    assert captured["argv"] == ["git", "status", "--short"]
    assert captured["kwargs"]["shell"] is False


def test_safe_extract_rejects_path_escape(tmp_path):
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as tf:
        info = tarfile.TarInfo("../escape.txt")
        info.size = 1
        tf.addfile(info, io.BytesIO(b"x"))
    archive.seek(0)

    with tarfile.open(fileobj=archive, mode="r") as tf:
        with pytest.raises(ValueError, match="越界"):
            safe_extract(tf, tmp_path / "models")
    assert not (tmp_path / "escape.txt").exists()


def test_stable_vector_id_is_process_independent_shape():
    point_id = _stable_point_id("04_Knowledge/AI知识库/U-demo.md")
    assert point_id > 0
    assert point_id < 2**63
    assert point_id == _stable_point_id("04_Knowledge/AI知识库/U-demo.md")


def test_workflow_commit_rolls_back_partial_writes(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "a.md").write_text("original", encoding="utf-8")
    log_path = vault / "09_System" / "自动化流程" / "训练日志.md"
    log_path.parent.mkdir(parents=True)
    log_path.write_text("old log\n", encoding="utf-8")
    monkeypatch.setattr(config, "VAULT_PATH", vault)
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setattr(writer.time, "sleep", lambda _seconds: None)
    db.init_db()

    class DummyWorkflow(contract.WorkflowBase):
        name = "test-transaction"

    workflow = DummyWorkflow()
    result = {
        "needs_draft": True,
        "drafts": [
            {"rel_path": "a.md", "content": "changed"},
            {"rel_path": "b.md", "content": "new"},
        ],
        "updates": [],
        "log_line": "transaction test",
    }
    run_id = contract.create_run("test-transaction", "test", {}, result)

    real_write = writer.write_file

    def fail_second(rel_path, content):
        if rel_path == "b.md":
            return {"ok": False, "error": "injected failure"}
        return real_write(rel_path, content)

    monkeypatch.setattr(writer, "write_file", fail_second)
    committed = workflow.commit(run_id)

    assert committed["ok"] is False
    assert committed["status"] == "failed"
    assert (vault / "a.md").read_text(encoding="utf-8") == "original"
    assert not (vault / "b.md").exists()
    assert log_path.read_text(encoding="utf-8") == "old log\n"
    con = db.schema.get_conn()
    try:
        row = con.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
        draft = con.execute("SELECT confirmed FROM drafts WHERE run_id=?", (run_id,)).fetchone()
    finally:
        con.close()
    assert row["status"] == "failed"
    assert draft["confirmed"] == 0
