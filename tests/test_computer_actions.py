# -*- coding: utf-8 -*-
"""本地电脑动作的协议、确认和一次性凭证测试。"""
from __future__ import annotations

import logging
import json

from backend.agent import broker
from backend.computer import actions


def test_only_http_url_can_be_planned():
    assert actions.normalize_url("https://example.com/a") == ("https://example.com/a", None)
    assert actions.normalize_url("file:///C:/secret.txt")[0] is None
    assert actions.normalize_url("javascript:alert(1)")[0] is None
    assert actions.normalize_url("https://user:pass@example.com")[0] is None


def test_open_url_request_is_explicit_and_plan_has_confirmation():
    assert actions.is_open_url_request("打开 https://example.com") is True
    assert actions.is_open_url_request("这个链接是什么：https://example.com") is False

    plan = broker.plan_command("打开 https://example.com")

    assert plan["mode"] == "command"
    assert plan["capability_id"] == "computer.open_url"
    assert plan["requires_confirmation"] is True
    assert plan["computer_action_owned"] is True
    assert plan["target"] == "https://example.com"
    assert "action_id" not in plan


def test_confirmation_is_the_only_path_that_calls_browser(monkeypatch):
    actions._reset_for_tests()
    monkeypatch.setattr(actions, "_audit", lambda *args, **kwargs: None)
    opened: list[tuple[str, int, bool]] = []

    def fake_open(url, new=0, autoraise=True):
        opened.append((url, new, autoraise))
        return True

    monkeypatch.setattr(actions.webbrowser, "open", fake_open)
    planned = actions.create_open_url_plan("https://example.com")

    assert planned["ok"] is True
    assert opened == []

    result = actions.confirm_open_url(planned["action_id"])

    assert result["ok"] is True
    assert result["type"] == "computer_action_result"
    assert result["computer_action_owned"] is True
    assert result["status"] == "success"
    assert result["verified"] is True
    assert opened == [("https://example.com", 2, True)]
    assert actions.confirm_open_url(planned["action_id"])["ok"] is False


def test_reject_destroys_pending_action_without_browser(monkeypatch):
    actions._reset_for_tests()
    monkeypatch.setattr(actions, "_audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(actions.webbrowser, "open", lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("rejected action must not open browser")
    ))
    planned = actions.create_open_url_plan("https://example.com")

    result = actions.reject_open_url(planned["action_id"])

    assert result["ok"] is True
    assert result["type"] == "computer_action_result"
    assert result["computer_action_owned"] is True
    assert result["status"] == "cancelled"
    assert result["verified"] is False
    assert actions.confirm_open_url(planned["action_id"])["ok"] is False


def test_reject_cannot_race_with_execution():
    actions._reset_for_tests()
    planned = actions.create_open_url_plan("https://example.com")
    actions._PENDING[planned["action_id"]]["status"] = "executing"

    result = actions.reject_open_url(planned["action_id"])

    assert result["ok"] is False
    assert "正在处理" in result["error"]
    actions._reset_for_tests()


def test_strict_selector_command_formats_are_parsed_without_guessing():
    assert actions.parse_computer_request("激活「记事本」") == {
        "action": "activate_window", "window_query": "记事本",
    }
    assert actions.parse_computer_request("在「记事本」中点击「保存」") == {
        "action": "click_control", "window_query": "记事本", "control_name": "保存",
    }
    assert actions.parse_computer_request("在「记事本」中向「编辑器」输入「测试内容」") == {
        "action": "set_text", "window_query": "记事本", "control_name": "编辑器", "value": "测试内容",
    }
    assert actions.parse_computer_request("点击保存") is None
    assert actions.parse_computer_request("在记事本输入测试内容") is None
    assert actions.parse_computer_request("如何在「记事本」中点击「保存」") is None


def test_control_click_is_prepared_then_runs_only_after_confirmation(monkeypatch):
    actions._reset_for_tests()
    monkeypatch.setattr(actions, "_audit", lambda *args, **kwargs: None)
    prepared = {
        "ok": True,
        "window": {"hwnd": 101, "title": "记事本", "process_id": 7},
        "control": {"name": "保存", "control_type": "ButtonControl"},
        "selector": {"name": "保存", "control_type": "ButtonControl", "process_id": 7},
    }
    monkeypatch.setattr(actions.windows, "prepare_click_target", lambda *args: prepared)
    monkeypatch.setattr(
        actions.windows, "resolve_window",
        lambda *_args: {"ok": True, "available": True, "window": prepared["window"]},
    )
    called: list[tuple[dict, dict]] = []
    monkeypatch.setattr(
        actions.windows, "click_control",
        lambda window, selector: called.append((window, selector)) or {
            "ok": True, "verified": True, "verification": {"control_present": True},
        },
    )
    text = "在「记事本」中点击「保存」"
    plan = broker.plan_command(text)

    pending = actions.create_pending_action(plan, text)

    assert pending["ok"] is True
    assert pending["action"] == "click_control"
    assert "记事本" in pending["target"]
    assert called == []
    result = actions.confirm_action(pending["action_id"])
    assert result["status"] == "success"
    assert result["type"] == "computer_action_result"
    assert called == [(prepared["window"], prepared["selector"])]


def test_control_path_is_executed_by_unified_path_executor(monkeypatch):
    actions._reset_for_tests()
    monkeypatch.setattr(actions, "_audit", lambda *args, **kwargs: None)
    prepared = {
        "ok": True,
        "window": {"hwnd": 101, "title": "无标题 - Notepad", "process_id": 7},
        "control": {"name": "保存", "control_type": "ControlPathTarget"},
        "selector": {"name": "保存"},
        "control_target": {
            "target_type": "ControlPathTarget",
            "recipe_id": "notepad.save",
            "application_alias": "notepad",
        },
    }
    monkeypatch.setattr(actions.windows, "prepare_click_target", lambda *_args: prepared)
    monkeypatch.setattr(
        actions.windows, "resolve_window",
        lambda *_args: {"ok": True, "available": True, "window": prepared["window"]},
    )
    path_calls: list[tuple[dict, dict]] = []
    monkeypatch.setattr(
        actions.windows, "execute_control_path",
        lambda window, target: path_calls.append((window, target)) or {
            "ok": True,
            "verified": True,
            "save_state": "SAVE_AS_REQUIRED",
            "execution_state": "ACTION_EXECUTED",
            "business_goal_completed": False,
            "verification": {"save_as_dialog": True},
        },
    )

    text = "在「记事本」中点击「保存」"
    pending = actions.create_pending_action(broker.plan_command(text), text)
    result = actions.confirm_action(pending["action_id"])

    assert result["ok"] is True
    assert result["save_state"] == "SAVE_AS_REQUIRED"
    assert result["business_goal_completed"] is False
    assert path_calls == [(prepared["window"], prepared["control_target"])]


def test_standalone_action_trace_is_structured_and_excludes_input_body(monkeypatch, caplog):
    actions._reset_for_tests()
    monkeypatch.setattr(actions, "_audit", lambda *args, **kwargs: None)
    prepared = {
        "ok": True,
        "window": {"hwnd": 101, "title": "记事本", "class_name": "Notepad", "process_id": 7},
        "control": {"name": "保存", "control_type": "ButtonControl", "process_id": 7},
        "selector": {"name": "保存", "control_type": "ButtonControl", "process_id": 7},
    }
    monkeypatch.setattr(actions.windows, "prepare_click_target", lambda *_args: prepared)
    monkeypatch.setattr(
        actions.windows, "resolve_window",
        lambda *_args: {"ok": True, "available": True, "window": prepared["window"]},
    )
    monkeypatch.setattr(
        actions.windows, "click_control",
        lambda *_args: {"ok": True, "verified": True},
    )
    text = "在「记事本」中点击「保存」"

    with caplog.at_level(logging.INFO, logger="jarvis2.desktop_execution_trace"):
        pending = actions.create_pending_action(broker.plan_command(text), text)
        result = actions.confirm_action(pending["action_id"])

    trace_lines = [record.message for record in caplog.records
                   if record.name == "jarvis2.desktop_execution_trace"]
    joined = "\n".join(trace_lines)
    trace_payloads = [json.loads(line.split("desktop_execution_trace ", 1)[1])
                      for line in trace_lines]
    assert result["status"] == "success"
    assert len({payload["execution_trace_id"] for payload in trace_payloads}) == 1
    assert '"source": "standalone_action"' in joined
    assert '"stage": "PREPARE_START"' in joined
    assert '"stage": "CONFIRMATION_CREATED"' in joined
    assert '"stage": "RESULT"' in joined
    assert "输入正文" not in joined


def test_text_action_keeps_value_out_of_public_plan_and_runs_after_confirmation(monkeypatch):
    actions._reset_for_tests()
    monkeypatch.setattr(actions, "_audit", lambda *args, **kwargs: None)
    prepared = {
        "ok": True,
        "window": {"hwnd": 202, "title": "记事本", "process_id": 9},
        "control": {"name": "编辑器", "control_type": "EditControl", "is_password": False},
        "selector": {"name": "编辑器", "control_type": "EditControl", "process_id": 9},
    }
    monkeypatch.setattr(actions.windows, "prepare_text_target", lambda *args: prepared)
    monkeypatch.setattr(
        actions.windows, "resolve_window",
        lambda *_args: {"ok": True, "available": True, "window": prepared["window"]},
    )
    received: list[str] = []
    monkeypatch.setattr(
        actions.windows, "set_control_value",
        lambda _window, _selector, value: received.append(value) or {
            "ok": True, "verified": True, "verification": {"value_readable": True},
        },
    )
    text = "在「记事本」中向「编辑器」输入「不应出现在动作卡的文本」"
    plan = broker.plan_command(text)

    pending = actions.create_pending_action(plan, text)

    assert pending["ok"] is True
    assert pending["action"] == "set_text"
    assert "不应出现在动作卡的文本" not in str(pending)
    assert "写入 11 个字符" in pending["target"]
    assert received == []
    assert actions.confirm_action(pending["action_id"])["ok"] is True
    assert received == ["不应出现在动作卡的文本"]


def test_text_action_logs_metadata_without_input_body(monkeypatch, caplog):
    actions._reset_for_tests()
    monkeypatch.setattr(actions, "_audit", lambda *args, **kwargs: None)
    prepared = {
        "ok": True,
        "window": {"hwnd": 202, "title": "记事本", "process_id": 9},
        "control": {"name": "编辑器", "control_type": "EditControl", "is_password": False},
        "selector": {"name": "编辑器", "control_type": "EditControl", "process_id": 9},
        "resolution": {
            "raw_target": "记事本",
            "normalized_target": "记事本",
            "resolved_alias": "notepad",
            "enumerated_window_count": 1,
            "selected_window": {
                "title": "记事本",
                "class_name": "Notepad",
                "process_name": "notepad.exe",
                "process_id": 9,
                "match_reason": "application_alias:exact_class_name",
                "match_score": 955,
            },
        },
    }
    monkeypatch.setattr(actions.windows, "prepare_text_target", lambda *_args: prepared)
    monkeypatch.setattr(
        actions.windows, "resolve_window",
        lambda *_args: {"ok": True, "available": True, "window": prepared["window"]},
    )
    monkeypatch.setattr(
        actions.windows, "set_control_value",
        lambda *_args: {"ok": True, "verified": True,
                         "verification": {"value_readable": True}},
    )

    secret = "绝不应写入日志的输入正文"
    with caplog.at_level(logging.INFO):
        pending = actions.create_pending_action(
            broker.plan_command(f"在「记事本」中向「编辑器」输入「{secret}」"),
            f"在「记事本」中向「编辑器」输入「{secret}」",
        )
        actions.confirm_action(pending["action_id"])

    assert secret not in caplog.text
    assert "desktop_action" in caplog.text
    assert "resolved_alias" in caplog.text


def test_confirmation_relocates_window_before_activation(monkeypatch):
    actions._reset_for_tests()
    monkeypatch.setattr(actions, "_audit", lambda *args, **kwargs: None)
    text = "激活「记事本」"
    plan = broker.plan_command(text)
    calls: list[dict] = []
    first = {"hwnd": 101, "title": "无标题 - Notepad", "process_id": 7}
    second = {"hwnd": 202, "title": "abc.txt - Notepad", "process_id": 8}

    def resolve(_query):
        window = first if not calls else second
        calls.append(window)
        return {"ok": True, "available": True, "window": window}

    activated: list[dict] = []
    monkeypatch.setattr(actions.windows, "resolve_window", resolve)
    monkeypatch.setattr(
        actions.windows, "activate_window",
        lambda hwnd, expected_process_id=0: activated.append({
            "hwnd": hwnd, "process_id": expected_process_id,
        }) or {"ok": True, "verified": True, "verification": {"foreground": hwnd}},
    )

    pending = actions.create_pending_action(plan, text)
    result = actions.confirm_action(pending["action_id"])

    assert result["ok"] is True
    assert calls == [first, second]
    assert activated == [{"hwnd": 202, "process_id": 8}]
