# -*- coding: utf-8 -*-
"""Lightweight Interaction Gate 的确定性路由测试。"""
from __future__ import annotations

import pytest

from backend.agent import task_engine
from backend.brain import answer, classify
from backend.computer import actions
from backend.routers import wake as wake_router


@pytest.fixture(autouse=True)
def reset_execution_state():
    actions._reset_for_tests()
    task_engine.reset_for_tests()
    yield
    actions._reset_for_tests()
    task_engine.reset_for_tests()


@pytest.mark.parametrize("text", [
    "好", "好的", "嗯", "收到", "知道了", "明白", "明白了", "OK", "ok", "确认",
])
def test_acknowledgement_contract_is_exact(text):
    assert classify.classify_lightweight_interaction(text) == classify.LightweightInteraction.ACK


@pytest.mark.parametrize("text", ["谢谢", "谢了", "thanks", "thank you", " THANKS "])
def test_thanks_contract_is_exact_and_case_insensitive(text):
    assert classify.classify_lightweight_interaction(text) == classify.LightweightInteraction.THANKS


@pytest.mark.parametrize("text", [
    "可以", "行", "继续", "我想确认一下这个设计", "确认执行一下",
    "我确认后再告诉你", "好的，那你继续分析", "嗯，我还有一个问题",
    "谢谢，顺便帮我看看这个", "可以开始下一步", "行，那继续做",
])
def test_ambiguous_or_substantive_text_is_not_consumed(text):
    assert classify.classify_lightweight_interaction(text) == classify.LightweightInteraction.NONE


@pytest.mark.parametrize(
    ("text", "expected"),
    [("好的", "好的。"), ("谢谢", "不客气。"), ("确认", "收到。")],
)
def test_lightweight_hit_is_terminal_and_skips_all_heavy_paths(monkeypatch, text, expected):
    forbidden = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("lightweight interaction entered a heavy path")
    )
    monkeypatch.setattr(answer, "register_hands", forbidden)
    monkeypatch.setattr(answer.broker, "plan_command", forbidden)
    monkeypatch.setattr(answer.classify, "classify_question", forbidden)
    monkeypatch.setattr(answer.rag, "build_context", forbidden)
    monkeypatch.setattr(answer.persona, "build_system_prompt", forbidden)
    monkeypatch.setattr(answer.db.ops, "get_or_create_conversation", forbidden)
    monkeypatch.setattr(answer.db.ops, "add_message", forbidden)
    monkeypatch.setattr(answer.db.ops, "history_messages", forbidden)
    monkeypatch.setattr(answer.db.ops, "add_memory_candidates", forbidden)
    monkeypatch.setattr(answer.llm, "chat_stream_iter", forbidden)
    monkeypatch.setattr(answer.llm, "extract_memory_candidates", forbidden)

    events = list(answer.chat_stream(text))

    assert [evt["type"] for evt in events] == ["start", "delta", "done"]
    assert events[0]["mode"] == "lightweight"
    assert events[0]["lightweight"] is True
    assert events[1]["text"] == expected
    assert events[2]["terminal"] is True
    assert events[2]["lightweight"] is True
    assert not any(evt.get("kind") in {"cognitive", "retrieval", "memory"} for evt in events)


def test_substantive_input_keeps_normal_conversation_routing(monkeypatch):
    calls: list[str] = []
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
    monkeypatch.setattr(answer.llm, "extract_memory_candidates", lambda *_args: [])
    monkeypatch.setattr(answer.db.ops, "add_memory_candidates", lambda *_args: [])

    events = list(answer.chat_stream("请解释这个架构"))

    assert "plan" in calls
    assert "conversation" in calls
    assert "llm" in calls
    assert any(evt.get("type") == "delta" and evt.get("text") == "普通回答" for evt in events)


def test_wake_route_reuses_the_same_lightweight_answer_gate(monkeypatch):
    monkeypatch.setattr(wake_router.notify, "publish", lambda *_args, **_kwargs: None)
    from backend.db import evals as evals_db
    monkeypatch.setattr(evals_db, "record_eval", lambda *_args, **_kwargs: None)

    response = wake_router.wake_command(wake_router.WakeCmdIn(text="好的"))

    assert response["ok"] is True
    assert response["data"]["reply"] == "好的。"
    assert response["data"]["action_plan"] is None
    assert response["data"]["action_result"] is None
