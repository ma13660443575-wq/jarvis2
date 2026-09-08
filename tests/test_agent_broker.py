# -*- coding: utf-8 -*-
"""Agent 能力目录与计划契约测试。"""
from __future__ import annotations

from backend.agent import broker
from backend.engine import contract, registry


class DummyWorkflow(contract.WorkflowBase):
    name = "test-agent-workflow"
    title = "测试写入能力"
    desc = "用于验证计划元数据。"
    interactive = True


def test_conversation_plan_is_read_only():
    plan = broker.plan_command("我最近在做什么？")

    assert plan["mode"] == "conversation"
    assert plan["capability_id"] == "chat.answer"
    assert plan["access"] == "read"
    assert plan["requires_confirmation"] is False


def test_workflow_plan_exposes_confirmation(monkeypatch):
    old = registry.get(DummyWorkflow.name)
    registry.register(DummyWorkflow())
    monkeypatch.setitem(registry._COMMANDS, "测试写入", DummyWorkflow.name)
    try:
        plan = broker.plan_command("测试写入")
    finally:
        if old is None:
            registry.unregister(DummyWorkflow.name)
        else:
            registry.register(old)
        registry._COMMANDS.pop("测试写入", None)

    assert plan["mode"] == "command"
    assert plan["capability_id"] == f"workflow:{DummyWorkflow.name}"
    assert plan["access"] == "write"
    assert plan["requires_confirmation"] is True
    assert plan["steps"][0]["capability_id"] == f"workflow:{DummyWorkflow.name}"


def test_finalize_plan_maps_draft_and_failure():
    base = broker.plan_command("随便聊聊")

    waiting = broker.finalize_plan(base, {"ok": True, "status": "draft", "run_id": 7})
    failed = broker.finalize_plan(base, {"ok": False, "status": "failed"})

    assert waiting["status"] == "waiting_confirmation"
    assert waiting["run_id"] == 7
    assert failed["status"] == "failed"


def test_computer_control_plans_are_explicit_and_require_confirmation():
    click = broker.plan_command("在「记事本」中点击「保存」")
    text = broker.plan_command("在「记事本」中向「编辑器」输入「测试内容」")

    assert click["mode"] == "command"
    assert click["capability_id"] == "computer.click_control"
    assert click["action"] == "click_control"
    assert click["requires_confirmation"] is True
    assert click["risk"] == "high"
    assert "保存" in click["target"]

    assert text["capability_id"] == "computer.set_text"
    assert text["action"] == "set_text"
    assert text["requires_confirmation"] is True
    assert "测试内容" not in text["target"]
    assert "写入 4 个字符" in text["target"]


def test_computer_action_ownership_requires_broker_marker():
    plan = broker.plan_command("激活「记事本」")

    assert broker.is_computer_action_owned(plan) is True
    assert broker.is_computer_action_owned({**plan, "computer_action_owned": False}) is False
    assert broker.is_computer_action_owned({k: v for k, v in plan.items()
                                            if k != "computer_action_owned"}) is False


def test_execution_owner_is_mutually_exclusive_and_prioritizes_desktop_task():
    assert broker.execution_owner({"mode": "conversation"}) == "conversation"
    assert broker.execution_owner({"computer_action_owned": True}) == "computer_action"
    assert broker.execution_owner({"desktop_task_owned": True}) == "desktop_task"
    assert broker.execution_owner({
        "desktop_task_owned": True, "computer_action_owned": True,
    }) == "desktop_task"
