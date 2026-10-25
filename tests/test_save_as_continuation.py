# -*- coding: utf-8 -*-
"""Phase-A WAITING_USER Save-As continuation and parser tests."""
from __future__ import annotations

import copy

import pytest

from backend.agent import broker, task_engine
from backend.agent.save_destination import SaveDestinationStatus, parse_save_destination
from backend.agent.task_models import TaskState
from backend.agent.task_store import store
from backend.brain import answer


GOAL = "在记事本中输入「Phase A Save-As 测试」，然后保存。"


@pytest.fixture(autouse=True)
def reset_task_runtime():
    task_engine.reset_for_tests()
    yield
    task_engine.reset_for_tests()


def _start_waiting_user_task(monkeypatch, session_id="session:42"):
    prepared: list[str] = []

    def fake_prepare(action_plan, command):
        action_id = f"old-action-{len(prepared) + 1}"
        prepared.append(action_id)
        return {
            "ok": True,
            "action_id": action_id,
            "action": action_plan.get("action"),
            "capability_id": action_plan.get("capability_id"),
        }

    outcomes = [
        {"ok": True, "status": "success", "verified": True},
        {"ok": True, "status": "success", "verified": True},
        {
            "ok": True,
            "status": "success",
            "verified": True,
            "business_goal_completed": False,
            "save_state": "SAVE_AS_REQUIRED",
            "verification": {"save_as_dialog": True},
        },
    ]

    def fake_confirm(action_id):
        return outcomes.pop(0)

    monkeypatch.setattr(task_engine.computer_actions, "create_pending_action", fake_prepare)
    monkeypatch.setattr(task_engine.computer_actions, "confirm_action", fake_confirm)
    plan = broker.plan_command(GOAL)
    started = task_engine.start_task(plan, GOAL, session_id=session_id)
    task_id = started["task_result"]["task_id"]
    for action_id in ("old-action-1", "old-action-2", "old-action-3"):
        task_engine.confirm_step(task_id, f"step_{action_id[-1]}", action_id)
    return task_id, prepared


def _forbid_heavy_paths(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("WAITING_USER continuation entered a heavy path")

    monkeypatch.setattr(answer, "register_hands", forbidden)
    monkeypatch.setattr(answer.broker, "plan_command", forbidden)
    monkeypatch.setattr(answer.classify, "classify_question", forbidden)
    monkeypatch.setattr(answer.rag, "build_context", forbidden)
    monkeypatch.setattr(answer.persona, "build_system_prompt", forbidden)
    monkeypatch.setattr(answer.llm, "chat_stream_iter", forbidden)
    monkeypatch.setattr(answer.llm, "extract_memory_candidates", forbidden)
    monkeypatch.setattr(answer.db.ops, "get_or_create_conversation", forbidden)
    monkeypatch.setattr(answer.db.ops, "add_message", forbidden)


@pytest.mark.parametrize(
    ("text", "status", "directory", "filename", "source_kind"),
    [
        ("保存到桌面，文件名叫 test.txt", SaveDestinationStatus.PARSED, "desktop", "test.txt", "known_folder"),
        ("保存到桌面，叫 test.txt", SaveDestinationStatus.PARSED, "desktop", "test.txt", "known_folder"),
        ("保存到文档，文件名叫 test.txt", SaveDestinationStatus.PARSED, "documents", "test.txt", "known_folder"),
        (r"保存到 D:\renimis\文档", SaveDestinationStatus.INCOMPLETE, r"D:\renimis\文档", None, "absolute_path"),
        (r"保存到 D:\renimis\文档\test.txt", SaveDestinationStatus.PARSED, r"D:\renimis\文档", "test.txt", "absolute_path"),
        ("文件名叫 test.txt", SaveDestinationStatus.PARSED, None, "test.txt", "filename_only"),
        ("就保存到当前目录", SaveDestinationStatus.INCOMPLETE, "current_dialog_directory", None, "current_dialog_directory"),
        ("保存到桌面", SaveDestinationStatus.INCOMPLETE, "desktop", None, "known_folder"),
    ],
)
def test_save_destination_parser_contract(text, status, directory, filename, source_kind):
    result = parse_save_destination(text)

    assert result.status == status
    assert result.destination is not None
    assert result.destination.directory == directory
    assert result.destination.filename == filename
    assert result.destination.source_kind == source_kind
    assert result.destination.overwrite_policy == "deny_or_confirm"


def test_save_destination_parser_rejects_irrelevant_and_unsafe_inputs():
    assert parse_save_destination("我想讨论一下怎么保存文件").status == SaveDestinationStatus.NOT_RELEVANT
    assert parse_save_destination("保存到云盘，文件名叫 test.txt").status == SaveDestinationStatus.INVALID
    assert parse_save_destination("文件名叫 bad/name.txt").status == SaveDestinationStatus.INVALID
    assert parse_save_destination("文件名叫 CON.txt").status == SaveDestinationStatus.INVALID


def test_waiting_user_lookup_is_exact_session_and_fail_closed(monkeypatch):
    task_id, _prepared = _start_waiting_user_task(monkeypatch, session_id="session:42")

    owner = task_engine.waiting_user_for_session("session:42")
    assert owner["ok"] is True
    assert owner["task_id"] == task_id
    assert task_engine.waiting_user_for_session("session:43") is None

    duplicate = copy.deepcopy(store.get(task_id))
    duplicate.plan.task_id = "duplicate-waiting-task"
    store._tasks[duplicate.plan.task_id] = duplicate
    ambiguous = task_engine.waiting_user_for_session("session:42")
    assert ambiguous["ok"] is False
    assert ambiguous["error_code"] == "TASK_WAITING_USER_AMBIGUOUS"


def test_waiting_user_destination_is_task_owned_and_skips_all_heavy_paths(monkeypatch):
    task_id, prepared = _start_waiting_user_task(monkeypatch, session_id="session:42")
    _forbid_heavy_paths(monkeypatch)

    events = list(answer.chat_stream("保存到桌面，文件名叫 test.txt", conversation_id=42))

    done = next(evt for evt in events if evt.get("type") == "done")
    plan = done["action_plan"]
    result = done["task_result"]
    assert done["desktop_task_owned"] is True
    assert done["terminal"] is True
    assert result["task_id"] == task_id
    assert result["status"] == "WAITING_USER"
    assert result["business_goal_completed"] is False
    assert plan["waiting_user_context"]["save_destination"]["directory"] == "desktop"
    assert plan["waiting_user_context"]["save_destination"]["filename"] == "test.txt"
    assert "已记录保存目标：桌面\\test.txt" in next(
        evt["text"] for evt in events if evt.get("type") == "delta"
    )
    assert prepared == ["old-action-1", "old-action-2", "old-action-3"]
    assert plan["action_id"] is None


def test_waiting_user_confirmation_is_not_lightweight_ack(monkeypatch):
    task_id, _prepared = _start_waiting_user_task(monkeypatch, session_id="session:42")
    _forbid_heavy_paths(monkeypatch)
    monkeypatch.setattr(
        answer.classify,
        "classify_lightweight_interaction",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not enter ACK gate")),
    )

    events = list(answer.chat_stream("确认", conversation_id=42))

    assert any(evt.get("type") == "delta" and "保存位置或文件名" in evt["text"] for evt in events)
    done = next(evt for evt in events if evt.get("type") == "done")
    assert done["task_result"]["task_id"] == task_id
    assert done["task_result"]["status"] == "WAITING_USER"


def test_waiting_user_cancel_is_terminal_without_reusing_old_action(monkeypatch):
    task_id, prepared = _start_waiting_user_task(monkeypatch, session_id="session:42")
    _forbid_heavy_paths(monkeypatch)

    events = list(answer.chat_stream("取消保存", conversation_id=42))

    done = next(evt for evt in events if evt.get("type") == "done")
    assert done["task_result"]["task_id"] == task_id
    assert done["task_result"]["status"] == "CANCELLED"
    assert done["task_result"]["business_goal_completed"] is False
    assert done["task_result"]["reason"] == "SAVE_CANCELLED"
    assert prepared == ["old-action-1", "old-action-2", "old-action-3"]
    assert task_engine.waiting_user_for_session("session:42") is None


def test_waiting_user_does_not_cross_session_or_change_task_state(monkeypatch):
    task_id, _prepared = _start_waiting_user_task(monkeypatch, session_id="session:42")
    assert answer._continuation_dispatch("保存到桌面，文件名叫 test.txt", "session:43") is None

    task = task_engine.get_task(task_id)
    assert task["task_result"]["status"] == TaskState.WAITING_USER.value
    assert task["task_result"]["business_goal_completed"] is False


def test_without_waiting_user_task_save_text_keeps_normal_routing(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(answer, "register_hands", lambda: calls.append("register"))
    monkeypatch.setattr(
        answer.broker,
        "plan_command",
        lambda _text: calls.append("broker") or {"mode": "conversation", "capability_id": "chat.answer"},
    )
    monkeypatch.setattr(answer.db.ops, "get_or_create_conversation", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(answer.db.ops, "add_message", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(answer.registry, "parse_command", lambda _text: {"type": "unknown"})
    monkeypatch.setattr(answer.classify, "classify_question", lambda _text: "direct")
    monkeypatch.setattr(answer.rag, "build_context", lambda _text: {"context": "", "sources": []})
    monkeypatch.setattr(answer.persona, "build_system_prompt", lambda: "system")
    monkeypatch.setattr(answer.db.ops, "history_messages", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(answer.llm, "chat_stream_iter", lambda *_args, **_kwargs: iter(["普通回答"]))
    monkeypatch.setattr(answer.llm, "extract_memory_candidates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(answer.db.ops, "add_memory_candidates", lambda *_args, **_kwargs: [])

    events = list(answer.chat_stream("保存到桌面，文件名叫 test.txt", conversation_id=99))

    assert "broker" in calls
    assert any(evt.get("type") == "delta" and evt.get("text") == "普通回答" for evt in events)


def test_waiting_user_can_complete_a_partial_directory_with_filename(monkeypatch):
    task_id, _prepared = _start_waiting_user_task(monkeypatch, session_id="session:42")

    first = task_engine.handle_waiting_user_input(task_id, "session:42", "保存到桌面")
    assert first["parse_status"] == SaveDestinationStatus.INCOMPLETE.value
    second = task_engine.handle_waiting_user_input(task_id, "session:42", "文件名叫 test.txt")

    assert second["parse_status"] == SaveDestinationStatus.PARSED.value
    destination = second["task_plan"]["waiting_user_context"]["save_destination"]
    assert destination["directory"] == "desktop"
    assert destination["filename"] == "test.txt"
    assert second["task_result"]["status"] == TaskState.WAITING_USER.value


def test_waiting_user_keeps_active_task_mutex(monkeypatch):
    _task_id, _prepared = _start_waiting_user_task(monkeypatch, session_id="session:42")

    with pytest.raises(task_engine.TaskRuntimeError) as task_error:
        task_engine.start_task(broker.plan_command(GOAL), GOAL, session_id="session:42")
    assert task_error.value.code == "TASK_ALREADY_ACTIVE"

    with pytest.raises(task_engine.TaskRuntimeError) as action_error:
        task_engine.ensure_desktop_execution_available("session:42")
    assert action_error.value.code == "DESKTOP_EXECUTION_BUSY"
