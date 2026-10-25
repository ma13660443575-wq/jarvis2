# -*- coding: utf-8 -*-
"""4A Desktop Task Runtime 的结构、状态与编排测试。"""
from __future__ import annotations

import copy
import json

import pytest

from backend.agent import broker, task_engine, task_planner
from backend.agent.task_models import StepState, TaskState
from backend.agent.task_state import TaskStateError, transition_step, transition_task


GOAL = "在记事本中输入「JARVIS多步骤任务测试」，然后保存。"


@pytest.fixture(autouse=True)
def reset_task_runtime():
    task_engine.reset_for_tests()
    yield
    task_engine.reset_for_tests()


def _prepared_runtime(monkeypatch):
    prepared: list[dict] = []
    confirmed: list[str] = []
    rejected: list[str] = []

    def fake_prepare(action_plan, command):
        action_id = f"action-{len(prepared) + 1}"
        prepared.append({"action_plan": action_plan, "command": command, "action_id": action_id})
        return {
            "ok": True,
            "action_id": action_id,
            "action": action_plan.get("action"),
            "capability_id": action_plan.get("capability_id"),
        }

    outcomes = [
        {"ok": True, "status": "success", "verified": True,
         "execution_state": "ACTION_EXECUTED"},
        {"ok": True, "status": "success", "verified": True,
         "execution_state": "ACTION_EXECUTED"},
        {"ok": True, "status": "success", "verified": True,
         "execution_state": "ACTION_EXECUTED", "business_goal_completed": False,
         "save_state": "SAVE_AS_REQUIRED", "verification": {"save_as_dialog": True}},
    ]

    def fake_confirm(action_id):
        confirmed.append(action_id)
        return outcomes[len(confirmed) - 1]

    def fake_reject(action_id):
        rejected.append(action_id)
        return {"ok": True, "status": "cancelled", "action_id": action_id}

    monkeypatch.setattr(task_engine.computer_actions, "create_pending_action", fake_prepare)
    monkeypatch.setattr(task_engine.computer_actions, "confirm_action", fake_confirm)
    monkeypatch.setattr(task_engine.computer_actions, "reject_action", fake_reject)
    return prepared, confirmed, rejected


def _start_task(monkeypatch, session_id="test-session"):
    prepared, confirmed, rejected = _prepared_runtime(monkeypatch)
    plan = broker.plan_command(GOAL)
    started = task_engine.start_task(plan, GOAL, session_id=session_id)
    return started, prepared, confirmed, rejected


def test_structured_planner_returns_bounded_safe_plan_without_input_body():
    plan = broker.plan_command(GOAL)

    assert broker.is_desktop_task_owned(plan) is True
    assert plan["task_type"] == "desktop_task"
    assert plan["max_steps"] == 5
    assert [step["action_type"] for step in plan["steps"]] == ["activate", "input", "click"]
    assert [step["order"] for step in plan["steps"]] == [1, 2, 3]
    assert "JARVIS多步骤任务测试" not in str(plan)


def test_structured_planner_rejects_unknown_action_and_more_than_five_steps():
    plan = broker.plan_command(GOAL)

    unknown_action = copy.deepcopy(plan)
    unknown_action["steps"][0]["action_type"] = "open_url"
    with pytest.raises(task_planner.TaskPlanError) as unknown_exc:
        task_planner.validate_public_plan(unknown_action)
    assert unknown_exc.value.code == "TASK_PLAN_INVALID"

    no_confirmation = copy.deepcopy(plan)
    no_confirmation["steps"][0]["requires_confirmation"] = False
    with pytest.raises(task_planner.TaskPlanError) as confirmation_exc:
        task_planner.validate_public_plan(no_confirmation)
    assert confirmation_exc.value.code == "TASK_PLAN_INVALID"

    too_many = copy.deepcopy(plan)
    too_many["steps"] = too_many["steps"] + [
        {
            "id": "step_4", "step_id": "step_4", "order": 4,
            "action_type": "activate", "window_target": "记事本",
            "requires_confirmation": True, "status": "PENDING", "title": "额外步骤",
        },
        {
            "id": "step_5", "step_id": "step_5", "order": 5,
            "action_type": "activate", "window_target": "记事本",
            "requires_confirmation": True, "status": "PENDING", "title": "额外步骤",
        },
        {
            "id": "step_6", "step_id": "step_6", "order": 6,
            "action_type": "activate", "window_target": "记事本",
            "requires_confirmation": True, "status": "PENDING", "title": "越界步骤",
        },
    ]
    with pytest.raises(task_planner.TaskPlanError) as length_exc:
        task_planner.validate_public_plan(too_many)
    assert length_exc.value.code == "TASK_PLAN_INVALID"


@pytest.mark.parametrize("text", [
    "打开记事本，然后输入「测试」，然后保存。",
    "在记事本中输入「测试」，然后保存并关闭记事本。",
    "在记事本中输入「测试」，然后保存到「文件.txt」。",
])
def test_structured_planner_does_not_silently_ignore_unsupported_extensions(text):
    plan = broker.plan_command(text)

    assert plan["desktop_task_owned"] is True
    assert plan["status"] == "invalid"
    assert plan["error_code"] == "TASK_UNSUPPORTED_CAPABILITY"


def test_task_and_step_transition_tables_reject_illegal_edges():
    assert transition_task(TaskState.PLANNING, TaskState.READY) is TaskState.READY
    assert transition_step(StepState.PENDING, StepState.READY) is StepState.READY
    with pytest.raises(TaskStateError) as task_exc:
        transition_task(TaskState.SUCCEEDED, TaskState.RUNNING)
    assert task_exc.value.code == "TASK_INVALID_TRANSITION"
    with pytest.raises(TaskStateError) as step_exc:
        transition_step(StepState.SUCCEEDED, StepState.EXECUTING)
    assert step_exc.value.code == "STEP_INVALID_TRANSITION"


def test_task_runtime_requires_three_part_binding_and_blocks_out_of_order(monkeypatch):
    started, _prepared, confirmed, _rejected = _start_task(monkeypatch)
    task_id = started["task_plan"]["task_id"]
    action_id = started["task_plan"]["action_id"]

    with pytest.raises(task_engine.TaskRuntimeError) as step_exc:
        task_engine.confirm_step(task_id, "step_2", action_id)
    assert step_exc.value.code == "TASK_STEP_MISMATCH"
    with pytest.raises(task_engine.TaskRuntimeError) as action_exc:
        task_engine.confirm_step(task_id, "step_1", "wrong-action")
    assert action_exc.value.code == "TASK_ACTION_MISMATCH"
    assert confirmed == []


def test_task_runtime_progresses_one_step_at_a_time_and_waits_after_save(monkeypatch):
    started, prepared, confirmed, _rejected = _start_task(monkeypatch)
    task_id = started["task_plan"]["task_id"]

    first = task_engine.confirm_step(task_id, "step_1", "action-1")
    assert first["task_result"]["status"] == "WAITING_CONFIRMATION"
    assert first["task_plan"]["current_step_id"] == "step_2"
    assert first["task_plan"]["action_id"] == "action-2"

    second = task_engine.confirm_step(task_id, "step_2", "action-2")
    assert second["task_result"]["status"] == "WAITING_CONFIRMATION"
    assert second["task_plan"]["current_step_id"] == "step_3"
    assert second["task_plan"]["action_id"] == "action-3"

    third = task_engine.confirm_step(task_id, "step_3", "action-3")
    assert third["task_result"]["status"] == "WAITING_USER"
    assert third["task_result"]["business_goal_completed"] is False
    assert third["task_plan"]["task_state"] == "WAITING_USER"
    assert third["task_plan"]["save_state"] == "SAVE_AS_REQUIRED"
    assert third["task_plan"]["steps"][2]["status"] == "SUCCEEDED"
    assert [item["action_id"] for item in prepared] == ["action-1", "action-2", "action-3"]
    assert confirmed == ["action-1", "action-2", "action-3"]


def test_task_runtime_confirm_is_idempotent_for_same_binding(monkeypatch):
    started, _prepared, confirmed, _rejected = _start_task(monkeypatch)
    task_id = started["task_plan"]["task_id"]

    first = task_engine.confirm_step(task_id, "step_1", "action-1")
    duplicate = task_engine.confirm_step(task_id, "step_1", "action-1")

    assert first["task_result"]["status"] == "WAITING_CONFIRMATION"
    assert duplicate["task_result"]["idempotent"] is True
    assert duplicate["task_result"]["action_result"] == first["task_result"]["action_result"]
    assert confirmed == ["action-1"]


def test_task_runtime_reject_terminates_task_and_is_idempotent(monkeypatch):
    started, _prepared, _confirmed, rejected = _start_task(monkeypatch)
    task_id = started["task_plan"]["task_id"]

    rejected_result = task_engine.reject_step(task_id, "step_1", "action-1")
    duplicate = task_engine.reject_step(task_id, "step_1", "action-1")

    assert rejected_result["task_result"]["status"] == "CANCELLED"
    assert rejected_result["task_plan"]["task_state"] == "CANCELLED"
    assert duplicate["task_result"]["idempotent"] is True
    assert rejected == ["action-1"]


def test_same_session_cannot_start_a_second_active_task(monkeypatch):
    first, _prepared, _confirmed, _rejected = _start_task(monkeypatch, session_id="same-session")
    plan = broker.plan_command(GOAL)

    with pytest.raises(task_engine.TaskRuntimeError) as exc:
        task_engine.start_task(plan, GOAL, session_id="same-session")

    assert exc.value.code == "TASK_ALREADY_ACTIVE"
    assert exc.value.task_result is not None
    assert exc.value.task_result.task_id == first["task_result"]["task_id"]


def test_active_task_blocks_new_standalone_computer_action(monkeypatch):
    from backend.brain import answer

    first, prepared, _confirmed, _rejected = _start_task(
        monkeypatch, session_id="session:default",
    )
    action_plan = {
        "plan_id": "standalone-plan",
        "mode": "command",
        "capability_id": "computer.activate_window",
        "action": "activate_window",
        "title": "激活窗口",
        "requires_confirmation": True,
        "computer_action_owned": True,
    }
    monkeypatch.setattr(answer, "register_hands", lambda: None)
    monkeypatch.setattr(answer.broker, "plan_command", lambda _text: action_plan)
    monkeypatch.setattr(
        answer.db.ops, "get_or_create_conversation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("busy standalone action must not enter Conversation")
        ),
    )

    events = list(answer.chat_stream("激活「记事本」"))

    done = next(evt for evt in events if evt.get("type") == "done")
    result = done["action_result"]
    assert result["status"] == "failed"
    assert result["error_code"] == "DESKTOP_EXECUTION_BUSY"
    assert result["computer_action_owned"] is True
    assert len(prepared) == 1
    assert first["task_result"]["status"] == "WAITING_CONFIRMATION"


def test_active_task_blocks_command_router_standalone_action(monkeypatch):
    from backend.routers import command as command_router

    _start_task(monkeypatch, session_id="session:default")
    action_plan = {
        "plan_id": "standalone-command-plan",
        "mode": "command",
        "capability_id": "computer.activate_window",
        "action": "activate_window",
        "computer_action_owned": True,
    }
    monkeypatch.setattr(command_router, "register_all", lambda: None)
    monkeypatch.setattr(command_router.broker, "plan_command", lambda *_args, **_kwargs: action_plan)

    response = command_router.execute(command_router.CmdIn(text="激活「记事本」"))
    payload = json.loads(response.body)

    assert response.status_code == 409
    assert payload["data"]["action_result"]["error_code"] == "DESKTOP_EXECUTION_BUSY"
    assert payload["data"]["action_result"]["terminal"] is True


def test_active_task_does_not_block_normal_chat(monkeypatch):
    from backend.brain import answer

    _start_task(monkeypatch, session_id="session:default")
    calls: list[str] = []
    monkeypatch.setattr(answer, "register_hands", lambda: None)
    monkeypatch.setattr(
        answer.broker, "plan_command",
        lambda _text: {"mode": "conversation", "capability_id": "chat.answer"},
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

    events = list(answer.chat_stream("这个任务的进展如何？"))

    assert "conversation" in calls
    assert "llm" in calls
    assert "memory" in calls
    assert any(evt.get("type") == "delta" and evt.get("text") == "普通回答"
               for evt in events)


def test_terminal_task_releases_session_for_new_task(monkeypatch):
    first, prepared, _confirmed, _rejected = _start_task(
        monkeypatch, session_id="releasable-session",
    )
    task_engine.reject_step(
        first["task_result"]["task_id"], "step_1", first["task_plan"]["action_id"],
    )

    second = task_engine.start_task(
        broker.plan_command(GOAL), GOAL, session_id="releasable-session",
    )

    assert second["task_result"]["status"] == "WAITING_CONFIRMATION"
    assert second["task_result"]["task_id"] != first["task_result"]["task_id"]
    assert len(prepared) == 2


def test_task_adapter_passes_trace_ownership_without_input_body(monkeypatch):
    from backend.computer import trace as desktop_trace

    seen: dict = {}

    def fake_prepare(action_plan, command):
        seen.update(desktop_trace.current_execution_trace_context())
        return {
            "ok": True,
            "action_id": "trace-action",
            "action": action_plan.get("action"),
        }

    monkeypatch.setattr(task_engine.computer_actions, "create_pending_action", fake_prepare)
    plan = broker.plan_command(GOAL)
    task_engine.start_task(plan, GOAL, session_id="trace-session")

    assert seen["source"] == "desktop_task_step"
    assert seen["task_id"] == plan["task_id"]
    assert seen["step_id"] == "step_1"
    assert GOAL not in str(seen)


def test_task_step_http_entrypoint_keeps_binding_and_blocks_legacy_bypass(monkeypatch):
    started, _prepared, confirmed, _rejected = _start_task(monkeypatch)
    task_id = started["task_plan"]["task_id"]
    action_id = started["task_plan"]["action_id"]

    from backend.routers import agent as agent_router
    from backend.routers import computer as computer_router

    response = agent_router.confirm_task_step(
        task_id, "step_1", agent_router.TaskActionIn(action_id=action_id),
    )
    assert response["ok"] is True
    assert response["data"]["task_result"]["status"] == "WAITING_CONFIRMATION"
    assert confirmed == [action_id]

    legacy_response = computer_router.confirm_computer_action("action-2")
    payload = json.loads(legacy_response.body)
    assert legacy_response.status_code == 409
    assert payload["data"]["error_code"] == "TASK_CONFIRMATION_REQUIRED"


def test_task_text_confirmation_uses_task_owner_and_three_part_binding(monkeypatch):
    from backend.brain import answer

    started, _prepared, confirmed, _rejected = _start_task(
        monkeypatch, session_id="session:default",
    )
    task_id = started["task_plan"]["task_id"]
    action_id = started["task_plan"]["action_id"]
    monkeypatch.setattr(answer, "register_hands", lambda: None)
    planned_commands: list[str] = []
    original_plan_command = answer.broker.plan_command
    monkeypatch.setattr(
        answer.broker, "plan_command",
        lambda text, *args, **kwargs: planned_commands.append(text)
        or original_plan_command(text, *args, **kwargs),
    )
    forbidden = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("Task confirmation must not enter Conversation/RAG/Memory/LLM")
    )
    monkeypatch.setattr(answer.db.ops, "get_or_create_conversation", forbidden)
    monkeypatch.setattr(answer.rag, "build_context", forbidden)
    monkeypatch.setattr(answer.llm, "chat_stream_iter", forbidden)
    monkeypatch.setattr(answer.llm, "extract_memory_candidates", forbidden)

    events = list(answer.chat_stream("确认"))

    done = next(evt for evt in events if evt.get("type") == "done")
    assert done["desktop_task_owned"] is True
    assert done["task_result"]["task_id"] == task_id
    assert confirmed == [action_id]
    assert "确认" not in planned_commands


def test_task_text_rejection_uses_task_cancellation_owner(monkeypatch):
    from backend.brain import answer

    started, _prepared, _confirmed, rejected = _start_task(
        monkeypatch, session_id="session:default",
    )
    task_id = started["task_plan"]["task_id"]
    action_id = started["task_plan"]["action_id"]
    monkeypatch.setattr(answer, "register_hands", lambda: None)
    monkeypatch.setattr(
        answer.broker, "plan_command",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Task rejection must not be semantically replanned")
        ),
    )
    forbidden = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("Task rejection must not enter Conversation/RAG/Memory/LLM")
    )
    monkeypatch.setattr(answer.db.ops, "get_or_create_conversation", forbidden)
    monkeypatch.setattr(answer.rag, "build_context", forbidden)
    monkeypatch.setattr(answer.llm, "chat_stream_iter", forbidden)

    events = list(answer.chat_stream("取消"))

    done = next(evt for evt in events if evt.get("type") == "done")
    assert done["desktop_task_owned"] is True
    assert done["task_result"]["task_id"] == task_id
    assert done["task_result"]["status"] == "CANCELLED"
    assert rejected == [action_id]



def test_desktop_task_stream_is_terminal_before_conversation_stack(monkeypatch):
    from backend.brain import answer

    plan = broker.plan_command(GOAL)
    calls: list[str] = []
    monkeypatch.setattr(answer, "register_hands", lambda: calls.append("register"))
    monkeypatch.setattr(answer.broker, "plan_command", lambda _text: plan)
    monkeypatch.setattr(
        task_engine,
        "start_task",
        lambda *_args, **_kwargs: {
            "task_plan": {**plan, "status": "waiting_confirmation", "action_id": "action-test",
                           "current_step_id": "step_1"},
            "task_result": {"task_id": plan["task_id"], "status": "WAITING_CONFIRMATION",
                            "business_goal_completed": False, "step_results": []},
        },
    )

    forbidden = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("Desktop Task must not enter Conversation/RAG/Memory/LLM")
    )
    monkeypatch.setattr(answer.db.ops, "get_or_create_conversation", forbidden)
    monkeypatch.setattr(answer.db.ops, "add_message", forbidden)
    monkeypatch.setattr(answer.rag, "build_context", forbidden)
    monkeypatch.setattr(answer.llm, "chat_stream_iter", forbidden)
    monkeypatch.setattr(answer.llm, "extract_memory_candidates", forbidden)

    events = list(answer.chat_stream(GOAL))

    assert calls == ["register"]
    assert any(evt.get("type") == "plan" and evt["plan"]["desktop_task_owned"] for evt in events)
    assert next(evt for evt in events if evt.get("type") == "done")["terminal"] is True
