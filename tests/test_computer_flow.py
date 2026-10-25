# -*- coding: utf-8 -*-
"""Computer Action 与普通问答分流的终止链测试。"""
from __future__ import annotations

import json

import pytest

from backend.brain import answer
from backend.computer import actions
from backend.routers import command as command_router
from backend.routers import computer as computer_router
from backend.routers import wake as wake_router


@pytest.mark.parametrize("error_code", ["WINDOW_NOT_FOUND", "CONTROL_NOT_FOUND", "CONTROL_AMBIGUOUS"])
def test_failed_computer_action_never_enters_conversation_or_llm(monkeypatch, error_code):
    monkeypatch.setattr(answer, "register_hands", lambda: None)
    monkeypatch.setattr(answer.registry, "parse_command", lambda _text: {"type": "unknown"})
    monkeypatch.setattr(
        answer.db.ops, "get_or_create_conversation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Computer Action must not create a Conversation")
        ),
    )
    monkeypatch.setattr(
        answer.db.ops, "add_message",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Computer Action must not write conversation messages")
        ),
    )
    monkeypatch.setattr(
        answer.broker,
        "plan_command",
        lambda _text: {
            "plan_id": "plan-test",
            "mode": "command",
            "capability_id": "computer.activate_window",
            "action": "activate_window",
            "title": "激活窗口",
            "description": "测试电脑动作",
            "access": "write",
            "risk": "medium",
            "requires_confirmation": True,
            "steps": [],
            "status": "planned",
            "computer_action_owned": True,
        },
    )
    monkeypatch.setattr(
        answer.computer_actions,
        "create_pending_action",
        lambda *_args: {
            "ok": False,
            "status": "failed",
            "error_code": error_code,
            "error": "找不到窗口「记事本」",
        },
    )

    def must_not_run(*_args, **_kwargs):
        raise AssertionError("failed Computer Action must not enter conversation, RAG, memory or LLM")

    monkeypatch.setattr(answer.rag, "build_context", must_not_run)
    monkeypatch.setattr(answer.llm, "extract_memory_candidates", must_not_run)

    monkeypatch.setattr(answer.llm, "chat_stream_iter", must_not_run)

    events = list(answer.chat_stream("激活「记事本」"))

    done = next(evt for evt in events if evt.get("type") == "done")
    assert done["action_plan"]["status"] == "failed"
    assert done["action_plan"]["error_code"] == error_code
    result = next(evt for evt in events if evt.get("type") == "computer_action_result")
    assert result["status"] == "failed"
    assert result["computer_action_owned"] is True
    assert result["terminal"] is True
    assert not any(evt.get("type") == "sources" for evt in events)


def _open_url_terminal_action(monkeypatch, *, outcome=None):
    from backend.computer import actions

    actions._reset_for_tests()
    monkeypatch.setattr(actions, "_audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(actions.notify, "publish", lambda *args, **kwargs: None)
    planned = actions.create_open_url_plan("https://example.com")
    monkeypatch.setattr(
        actions, "_execute_pending",
        lambda _item: outcome or {"ok": True, "verified": True},
    )
    return planned, actions


def test_computer_action_success_is_terminal_result_without_conversation_chain(monkeypatch):
    planned, actions = _open_url_terminal_action(monkeypatch)

    result = actions.confirm_action(planned["action_id"])

    assert result == {
        "ok": True,
        "type": "computer_action_result",
        "computer_action_owned": True,
        "terminal": True,
        "action": "open_url",
        "action_id": planned["action_id"],
        "status": "success",
        "verified": True,
        "target": "https://example.com",
        "url": "https://example.com",
    }


def test_computer_action_failed_is_terminal_result(monkeypatch):
    planned, actions = _open_url_terminal_action(
        monkeypatch,
        outcome={"ok": False, "verified": False,
                 "error_code": "EXECUTION_FAILED", "error": "测试失败"},
    )

    result = actions.confirm_action(planned["action_id"])

    assert result["type"] == "computer_action_result"
    assert result["computer_action_owned"] is True
    assert result["status"] == "failed"
    assert result["error_code"] == "EXECUTION_FAILED"


def test_computer_action_cancelled_is_terminal_result(monkeypatch):
    planned, actions = _open_url_terminal_action(monkeypatch)

    result = actions.reject_action(planned["action_id"])

    assert result["type"] == "computer_action_result"
    assert result["computer_action_owned"] is True
    assert result["status"] == "cancelled"
    assert result["verified"] is False


@pytest.mark.parametrize("terminal_status", ["success", "failed", "cancelled"])
def test_computer_action_router_result_does_not_enter_conversation_stack(
    monkeypatch, terminal_status,
):
    forbidden = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("terminal Computer Action must not enter Conversation/RAG/Memory/LLM")
    )
    monkeypatch.setattr(answer.db.ops, "get_or_create_conversation", forbidden)
    monkeypatch.setattr(answer.db.ops, "add_message", forbidden)
    monkeypatch.setattr(answer.rag, "build_context", forbidden)
    monkeypatch.setattr(answer.llm, "chat_stream_iter", forbidden)
    monkeypatch.setattr(answer.llm, "extract_memory_candidates", forbidden)

    terminal = {
        "ok": terminal_status != "failed",
        "type": "computer_action_result",
        "computer_action_owned": True,
        "terminal": True,
        "action": "activate_window",
        "action_id": "computer-action-test",
        "status": terminal_status,
        "verified": terminal_status == "success",
    }
    if terminal_status == "cancelled":
        monkeypatch.setattr(computer_router.actions, "reject_action", lambda _id: terminal)
        response = computer_router.reject_computer_action("computer-action-test")
    else:
        monkeypatch.setattr(computer_router.actions, "confirm_action", lambda _id: terminal)
        response = computer_router.confirm_computer_action("computer-action-test")

    payload = response if isinstance(response, dict) else json.loads(response.body)
    assert payload["data"]["type"] == "computer_action_result"
    assert payload["data"]["computer_action_owned"] is True
    assert payload["data"]["terminal"] is True
    assert payload["data"]["status"] == terminal_status


def test_command_router_preserves_prepare_failure_as_terminal_result(monkeypatch):
    monkeypatch.setattr(command_router, "register_all", lambda: None)
    monkeypatch.setattr(
        command_router.broker, "plan_command",
        lambda *_args, **_kwargs: {
            "mode": "command", "capability_id": "computer.activate_window",
            "action": "activate_window", "computer_action_owned": True,
        },
    )
    monkeypatch.setattr(
        command_router.computer_actions, "create_pending_action",
        lambda *_args: {"ok": False, "error_code": "WINDOW_NOT_FOUND", "error": "找不到窗口"},
    )

    response = command_router.execute(command_router.CmdIn(text="激活「记事本」"))
    payload = json.loads(response.body)

    assert payload["data"]["type"] == "computer_action_result"
    assert payload["data"]["computer_action_owned"] is True
    assert payload["data"]["action_result"]["status"] == "failed"


def test_wake_router_preserves_computer_action_terminal_result(monkeypatch):
    action_plan = {
        "mode": "command", "capability_id": "computer.activate_window",
        "action": "activate_window", "computer_action_owned": True,
        "status": "failed",
    }
    action_result = {
        "type": "computer_action_result", "computer_action_owned": True,
        "terminal": True, "action": "activate_window", "status": "failed",
        "verified": False, "error_code": "WINDOW_NOT_FOUND",
    }
    published: list[dict] = []
    monkeypatch.setattr(wake_router.broker, "plan_command", lambda *_args: action_plan)
    monkeypatch.setattr(
        wake_router.answer, "chat_stream",
        lambda *_args, **_kwargs: iter([
            {"type": "delta", "text": "动作失败"},
            {"type": "done", "action_plan": action_plan, "action_result": action_result},
        ]),
    )
    monkeypatch.setattr(wake_router.notify, "publish", lambda _channel, payload: published.append(payload))
    from backend.db import evals as evals_db
    monkeypatch.setattr(evals_db, "record_eval", lambda *args, **kwargs: None)

    response = wake_router.wake_command(wake_router.WakeCmdIn(text="激活「记事本」"))

    assert response["data"]["action_result"] == action_result
    assert published[-1]["action_result"] == action_result


def test_regular_question_still_enters_conversation_rag_and_llm(monkeypatch):
    from backend.brain import answer as answer_module

    calls: list[str] = []
    monkeypatch.setattr(answer_module, "register_hands", lambda: calls.append("register"))
    monkeypatch.setattr(answer_module.registry, "parse_command", lambda _text: {"type": "unknown"})
    monkeypatch.setattr(
        answer_module.broker, "plan_command",
        lambda _text: {"mode": "conversation", "capability_id": "chat.answer"},
    )
    monkeypatch.setattr(
        answer_module.db.ops, "get_or_create_conversation",
        lambda *_args, **_kwargs: calls.append("conversation") or 1,
    )
    monkeypatch.setattr(
        answer_module.db.ops, "add_message",
        lambda *_args, **_kwargs: calls.append("message") or None,
    )
    monkeypatch.setattr(answer_module.classify, "classify_question", lambda _text: "rag")
    monkeypatch.setattr(
        answer_module.rag, "build_context",
        lambda _text: {"context": "知识证据", "sources": ["note.md"]},
    )
    monkeypatch.setattr(answer_module.persona, "build_system_prompt", lambda: "system")
    monkeypatch.setattr(answer_module.db.ops, "history_messages", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        answer_module.llm, "chat_stream_iter",
        lambda *_args, **_kwargs: calls.append("llm") or iter(["普通回答"]),
    )
    monkeypatch.setattr(
        answer_module.llm, "extract_memory_candidates",
        lambda *_args, **_kwargs: calls.append("memory") or [],
    )
    monkeypatch.setattr(answer_module.db.ops, "add_memory_candidates", lambda *_args: [])
    monkeypatch.setattr(
        answer_module.db.ops, "add_message",
        lambda *args, **kwargs: calls.append("message") or 2,
    )

    events = list(answer_module.chat_stream("我最近的项目进展如何？"))

    assert "conversation" in calls
    assert "memory" in calls
    assert "llm" in calls
    assert any(evt.get("type") == "sources" for evt in events)
    assert any(evt.get("type") == "delta" and evt.get("text") == "普通回答" for evt in events)


def _prepare_open_url_for_session(session_id: str):
    actions._reset_for_tests()
    return_context = actions.execution_trace_context({"session_id": session_id})
    with return_context:
        return actions.create_open_url_plan("https://example.com")


def _stub_conversation_path(monkeypatch, calls):
    monkeypatch.setattr(answer, "register_hands", lambda: calls.append("register"))
    monkeypatch.setattr(
        answer.broker, "plan_command",
        lambda _text: calls.append("plan") or {
            "mode": "conversation", "capability_id": "chat.answer",
        },
    )
    monkeypatch.setattr(
        answer.db.ops, "get_or_create_conversation",
        lambda *_args, **_kwargs: calls.append("conversation") or 1,
    )
    monkeypatch.setattr(answer.db.ops, "add_message", lambda *_args, **_kwargs: 2)
    monkeypatch.setattr(answer.registry, "parse_command", lambda _text: {"type": "unknown"})
    monkeypatch.setattr(answer.classify, "classify_question", lambda _text: "direct")
    monkeypatch.setattr(answer.persona, "build_system_prompt", lambda: "system")
    monkeypatch.setattr(answer.db.ops, "history_messages", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        answer.llm, "chat_stream_iter",
        lambda *_args, **_kwargs: calls.append("llm") or iter(["普通回答"]),
    )
    monkeypatch.setattr(
        answer.llm, "extract_memory_candidates",
        lambda *_args, **_kwargs: calls.append("memory") or [],
    )
    monkeypatch.setattr(answer.db.ops, "add_memory_candidates", lambda *_args: [])


def test_chat_confirmation_continuation_confirms_same_session_without_llm(monkeypatch):
    pending = _prepare_open_url_for_session("session:7")
    calls: list[str] = []
    monkeypatch.setattr(answer.broker, "plan_command", lambda *_args: calls.append("plan"))
    monkeypatch.setattr(answer.db.ops, "get_or_create_conversation", lambda *_args, **_kwargs: calls.append("conversation"))
    monkeypatch.setattr(answer.llm, "chat_stream_iter", lambda *_args, **_kwargs: calls.append("llm"))
    monkeypatch.setattr(answer.llm, "extract_memory_candidates", lambda *_args, **_kwargs: calls.append("memory"))
    monkeypatch.setattr(actions, "_execute_pending", lambda _item: {"ok": True, "verified": True})
    monkeypatch.setattr(actions, "_audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(actions.notify, "publish", lambda *args, **kwargs: None)

    events = list(answer.chat_stream("确认", conversation_id=7))

    result = next(evt for evt in events if evt.get("type") == "computer_action_result")
    assert result["status"] == "success"
    assert result["action_id"] == pending["action_id"]
    assert "conversation" not in calls
    assert "llm" not in calls
    assert "memory" not in calls
    assert "plan" not in calls
    assert next(evt for evt in events if evt.get("type") == "done")["terminal"] is True


def test_chat_confirmation_cannot_cross_session(monkeypatch):
    pending = _prepare_open_url_for_session("session:8")
    calls: list[str] = []
    _stub_conversation_path(monkeypatch, calls)
    events = list(answer.chat_stream("确认", conversation_id=9))

    assert any(evt.get("type") == "delta" and evt.get("text") == "收到。" for evt in events)
    assert actions.get_pending_action_for_session("session:8")["action_id"] == pending["action_id"]
    assert "conversation" not in calls
    assert "llm" not in calls


def test_chat_rejection_continuation_cancels_same_session_without_llm(monkeypatch):
    pending = _prepare_open_url_for_session("session:11")
    calls: list[str] = []
    monkeypatch.setattr(answer.broker, "plan_command", lambda *_args: calls.append("plan"))
    monkeypatch.setattr(answer.db.ops, "get_or_create_conversation", lambda *_args, **_kwargs: calls.append("conversation"))
    monkeypatch.setattr(answer.llm, "chat_stream_iter", lambda *_args, **_kwargs: calls.append("llm"))
    monkeypatch.setattr(answer.llm, "extract_memory_candidates", lambda *_args, **_kwargs: calls.append("memory"))
    monkeypatch.setattr(actions, "_audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(actions.notify, "publish", lambda *args, **kwargs: None)

    events = list(answer.chat_stream("取消", conversation_id=11))

    result = next(evt for evt in events if evt.get("type") == "computer_action_result")
    assert result["status"] == "cancelled"
    assert result["action_id"] == pending["action_id"]
    assert "conversation" not in calls
    assert "llm" not in calls
    assert "memory" not in calls
    assert "plan" not in calls


def test_confirmation_without_pending_action_uses_lightweight_gate(monkeypatch):
    calls: list[str] = []
    _stub_conversation_path(monkeypatch, calls)

    events = list(answer.chat_stream("确认"))

    assert any(evt.get("type") == "delta" and evt.get("text") == "收到。" for evt in events)
    assert "conversation" not in calls
    assert "llm" not in calls


def test_pending_action_and_natural_language_confirmation_do_not_execute(monkeypatch):
    pending = _prepare_open_url_for_session("session:10")
    calls: list[str] = []
    _stub_conversation_path(monkeypatch, calls)
    monkeypatch.setattr(
        actions, "confirm_action",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not confirm")),
    )

    events = list(answer.chat_stream("我想确认一下这个设计", conversation_id=10))

    assert any(evt.get("type") == "delta" and evt.get("text") == "普通回答" for evt in events)
    assert actions.get_pending_action_for_session("session:10")["action_id"] == pending["action_id"]


def test_wake_confirmation_uses_the_same_continuation_dispatch(monkeypatch):
    pending = _prepare_open_url_for_session("session:default")
    monkeypatch.setattr(actions, "_execute_pending", lambda _item: {"ok": True, "verified": True})
    monkeypatch.setattr(actions, "_audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(actions.notify, "publish", lambda *args, **kwargs: None)
    monkeypatch.setattr(wake_router.notify, "publish", lambda *args, **kwargs: None)
    from backend.db import evals as evals_db
    monkeypatch.setattr(evals_db, "record_eval", lambda *args, **kwargs: None)

    response = wake_router.wake_command(wake_router.WakeCmdIn(text="确认"))

    assert response["ok"] is True
    assert response["data"]["action_result"]["status"] == "success"
    assert response["data"]["action_result"]["action_id"] == pending["action_id"]
