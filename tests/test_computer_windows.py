# -*- coding: utf-8 -*-
"""Windows 电脑观察桥的跨平台降级与边界测试。"""
from __future__ import annotations

import logging

import pytest

from backend.computer import trace as desktop_trace
from backend.computer import windows


def _window(title: str, *, class_name: str = "", process_name: str = "", process_id: int = 1) -> dict:
    return {
        "hwnd": process_id * 10,
        "title": title,
        "class_name": class_name,
        "process_id": process_id,
        "process_name": process_name,
        "bounds": None,
        "active": False,
    }


class _FakeValuePattern:
    IsReadOnly = False

    def __init__(self, value: str = ""):
        self.Value = value
        self.values: list[str] = []

    def SetValue(self, value: str):
        self.values.append(value)
        self.Value = value


class _FakeControl:
    def __init__(self, name: str, control_type: str, *, class_name: str = "",
                 automation_id: str = "", process_id: int = 7,
                 children: tuple[object, ...] = (), value_pattern=None,
                 expand_pattern=None, invoke_pattern=None):
        self.Name = name
        self.ControlTypeName = control_type
        self.ClassName = class_name
        self.AutomationId = automation_id
        self.ProcessId = process_id
        self.IsEnabled = True
        self.IsOffscreen = False
        self.IsPassword = False
        self._children = children
        self._value_pattern = value_pattern
        self._expand_pattern = expand_pattern
        self._invoke_pattern = invoke_pattern

    def GetChildren(self):
        return list(self._children)

    def GetValuePattern(self):
        return self._value_pattern

    def GetExpandCollapsePattern(self):
        return self._expand_pattern

    def GetInvokePattern(self):
        return self._invoke_pattern


@pytest.mark.parametrize(
    ("query", "title", "process_name", "class_name"),
    [
        ("记事本", "无标题 - Notepad", "notepad.exe", "Notepad"),
        ("Notepad", "无标题 - Notepad", "notepad.exe", "Notepad"),
        ("记事本", "abc.txt - Notepad", "notepad.exe", "Notepad"),
        ("VS Code", "project - Visual Studio Code", "Code.exe", "Chrome_WidgetWin_1"),
    ],
)
def test_window_resolver_matches_aliases_and_dynamic_titles(
    monkeypatch, query, title, process_name, class_name,
):
    monkeypatch.setattr(windows, "IS_WINDOWS", True)
    monkeypatch.setattr(
        windows, "list_windows",
        lambda limit=20: {"ok": True, "available": True, "windows": [
            _window(title, process_name=process_name, class_name=class_name),
        ]},
    )

    result = windows.resolve_window(query)

    assert result["ok"] is True
    assert result["window"]["title"] == title
    assert result["resolution"]["resolved_alias"] in {"notepad", "vscode"}
    assert result["resolution"]["selected_window"]["match_score"] >= windows.SAFE_WINDOW_SCORE


def test_window_resolver_uses_exact_class_name_when_requested(monkeypatch):
    monkeypatch.setattr(windows, "IS_WINDOWS", True)
    target = _window("无标题", class_name="Notepad", process_name="notepad.exe")
    monkeypatch.setattr(
        windows, "list_windows",
        lambda limit=20: {"ok": True, "available": True, "windows": [target]},
    )

    result = windows.resolve_window("ClassName=Notepad")

    assert result["ok"] is True
    assert result["window"] == target
    assert "class_name" in result["resolution"]["selected_window"]["match_reason"]


def test_window_resolver_refuses_indistinguishable_alias_windows(monkeypatch):
    monkeypatch.setattr(windows, "IS_WINDOWS", True)
    rows = [
        _window("无标题 - Notepad", process_name="notepad.exe", class_name="Notepad", process_id=1),
        _window("abc.txt - Notepad", process_name="notepad.exe", class_name="Notepad", process_id=2),
    ]
    monkeypatch.setattr(
        windows, "list_windows",
        lambda limit=20: {"ok": True, "available": True, "windows": rows},
    )

    result = windows.resolve_window("记事本")

    assert result["ok"] is False
    assert result["error_code"] == "WINDOW_AMBIGUOUS"


def test_window_resolver_rejects_low_confidence_fuzzy_candidate(monkeypatch):
    monkeypatch.setattr(windows, "IS_WINDOWS", True)
    monkeypatch.setattr(
        windows, "list_windows",
        lambda limit=20: {"ok": True, "available": True, "windows": [
            _window("Notes", process_name="notes.exe", class_name="NotesWindow"),
        ]},
    )

    result = windows.resolve_window("Nte")

    assert result["ok"] is False
    assert result["error_code"] == "WINDOW_AMBIGUOUS"


def test_ui_tree_limits_are_clamped_without_dependency_call(monkeypatch):
    monkeypatch.setattr(windows, "IS_WINDOWS", False)

    result = windows.ui_tree(max_depth=999, max_nodes=9999)

    assert result["ok"] is False
    assert result["available"] is False


def test_computer_capabilities_are_explicit_about_availability(monkeypatch):
    monkeypatch.setattr(windows, "IS_WINDOWS", False)

    items = windows.capabilities()

    assert {item["id"] for item in items} == {
        "computer.active_window", "computer.windows", "computer.ui_tree",
    }
    assert all(item["access"] == "read" for item in items)
    assert all(item["requires_confirmation"] is False for item in items)
    assert all(item["available"] is False for item in items)


def test_click_selector_refuses_non_click_control_types():
    selector, error = windows._normalize_selector(
        {"name": "编辑器", "control_type": "EditControl"}, windows.CLICK_CONTROL_TYPES,
    )

    assert selector is None
    assert "允许" in error


@pytest.mark.parametrize("target", ["编辑器", "正文"])
def test_control_resolver_maps_editor_alias_to_document_control(monkeypatch, target):
    value_pattern = _FakeValuePattern()
    editor = _FakeControl(
        "文本编辑器", "DocumentControl", class_name="RichEditD2DPT",
        value_pattern=value_pattern,
    )
    root = _FakeControl("Notepad", "WindowControl", children=(editor,))
    monkeypatch.setattr(windows, "_uia_root", lambda *_args, **_kwargs: (root, None))

    result = windows._resolve_control_object(
        {"hwnd": 1, "process_id": 7},
        {"name": target, "raw_target": target},
        windows.TEXT_CONTROL_TYPES,
        action_type="input",
    )

    assert result["ok"] is True
    assert result["target_type"] == "DirectControlTarget"
    assert result["control"]["control_type"] == "DocumentControl"
    assert result["control"]["class_name"] == "RichEditD2DPT"
    assert result["control_target"]["semantic_alias"] == "editor"


def test_document_control_without_writable_pattern_returns_explicit_error(monkeypatch):
    editor = _FakeControl(
        "文本编辑器", "DocumentControl", class_name="RichEditD2DPT",
    )
    root = _FakeControl("Notepad", "WindowControl", children=(editor,))
    monkeypatch.setattr(windows, "_uia_root", lambda *_args, **_kwargs: (root, None))

    result = windows.set_control_value(
        {"hwnd": 1, "process_id": 7}, {"name": "编辑器", "raw_target": "编辑器"}, "测试文本",
    )

    assert result["ok"] is False
    assert result["error_code"] == "UIA_PATTERN_UNAVAILABLE"


def test_document_control_with_readonly_value_pattern_returns_not_writable(monkeypatch):
    class ReadOnlyPattern:
        IsReadOnly = True

    class ReadOnlyEditor(_FakeControl):
        def __init__(self):
            super().__init__("文本编辑器", "DocumentControl", class_name="RichEditD2DPT")

        def GetValuePattern(self):
            return ReadOnlyPattern()

    root = _FakeControl("Notepad", "WindowControl", children=(ReadOnlyEditor(),))
    monkeypatch.setattr(windows, "_uia_root", lambda *_args, **_kwargs: (root, None))

    result = windows.set_control_value(
        {"hwnd": 1, "process_id": 7}, {"name": "编辑器", "raw_target": "编辑器"}, "测试文本",
    )

    assert result["ok"] is False
    assert result["error_code"] == "CONTROL_NOT_WRITABLE"


def test_control_alias_type_alone_cannot_select_unrelated_control():
    unrelated_menu_item = _FakeControl("其他菜单项", "MenuItemControl")
    record = windows._control_record(unrelated_menu_item, 1)

    candidate = windows._control_candidate(
        {"name": "保存", "raw_target": "保存"},
        unrelated_menu_item,
        record,
        windows.CLICK_CONTROL_TYPES,
        action_type="click",
    )

    assert candidate is None


def test_prepare_save_uses_safe_notepad_control_path(monkeypatch):
    file_menu = _FakeControl("文件", "MenuItemControl", automation_id="File")
    window = {"hwnd": 1, "process_id": 7, "title": "无标题 - Notepad",
              "process_name": "notepad.exe", "class_name": "Notepad"}
    monkeypatch.setattr(
        windows, "resolve_window",
        lambda *_args: {
            "ok": True, "available": True, "window": window,
            "resolution": {"resolved_alias": "notepad", "normalized_target": "记事本",
                            "enumerated_window_count": 1,
                            "selected_window": {"title": window["title"]}},
        },
    )

    calls: list[str] = []

    def resolve(window_arg, selector, *_args, **_kwargs):
        assert window_arg == window
        calls.append(selector["name"])
        if len(calls) == 1:
            return {"ok": False, "error_code": "CONTROL_NOT_FOUND", "error": "找不到保存"}
        assert selector["automation_id"] == "File"
        return {
            "ok": True, "available": True, "window": window,
            "control": {"name": "文件", "control_type": "MenuItemControl"},
            "selector": {"name": "文件", "control_type": "MenuItemControl",
                          "automation_id": "File"},
            "_control": file_menu,
        }

    monkeypatch.setattr(windows, "_resolve_control_object", resolve)

    result = windows.prepare_click_target("记事本", "保存")

    assert result["ok"] is True
    assert calls == ["保存", "文件"]
    assert result["target_type"] == "ControlPathTarget"
    assert [step["target"] for step in result["control_path"]] == ["文件", "保存"]
    assert result["control_target"]["recipe_id"] == "notepad.save"


def test_control_path_expands_file_re_scans_popup_and_invokes_save(monkeypatch):
    class ExpandPattern:
        def __init__(self):
            self.expanded = False

        def Expand(self):
            self.expanded = True

    class InvokePattern:
        def __init__(self):
            self.invoked = False

        def Invoke(self):
            self.invoked = True

    expand = ExpandPattern()
    invoke = InvokePattern()
    file_menu = _FakeControl("文件", "MenuItemControl", automation_id="File",
                             expand_pattern=expand)
    save_menu = _FakeControl("保存", "MenuItemControl", invoke_pattern=invoke)
    window = {"hwnd": 1, "process_id": 7, "title": "无标题 - Notepad",
              "process_name": "notepad.exe", "class_name": "Notepad"}
    recipe_target = {
        "target_type": "ControlPathTarget",
        "recipe_id": "notepad.save",
        "application_alias": "notepad",
    }
    trigger_result = {
        "ok": True, "available": True, "window": window,
        "control": {"name": "文件", "control_type": "MenuItemControl"},
        "_control": file_menu,
    }
    monkeypatch.setattr(windows, "_resolve_control_object", lambda *_args, **_kwargs: trigger_result)
    scan_results = [
        {"ok": True, "available": True, "popups": []},
        {"ok": True, "available": True, "popups": [{
            "identity": ("hwnd", 88),
            "record": {"class_name": "MenuFlyout", "process_id": 7},
            "menu_candidates": [{
                "control": save_menu,
                "record": {"name": "保存", "control_type": "MenuItemControl"},
            }],
        }]},
    ]
    monkeypatch.setattr(windows, "_scan_desktop_popups", lambda *_args, **_kwargs: scan_results.pop(0))
    monkeypatch.setattr(windows, "_document_modified_state", lambda *_args: None)
    monkeypatch.setattr(
        windows, "_find_save_as_dialog",
        lambda **_kwargs: {"ok": True, "available": True,
                            "dialog": {"record": {"name": "另存为"}}},
    )

    trace = desktop_trace.new_execution_trace({
        "source": "standalone_action",
        "action_type": "click_control",
        "action_id": "trace-save-action",
    })
    with desktop_trace.active_execution_trace(trace):
        result = windows.execute_control_path(window, recipe_target)

    assert result["ok"] is True
    assert result["verified"] is True
    assert result["save_state"] == "SAVE_AS_REQUIRED"
    assert result["business_goal_completed"] is False
    assert expand.expanded is True
    assert invoke.invoked is True


def test_save_trace_contains_popup_and_verify_stages_without_changing_result(monkeypatch, caplog):
    class ExpandPattern:
        def Expand(self):
            return True

    class InvokePattern:
        def Invoke(self):
            return True

    file_menu = _FakeControl(
        "文件", "MenuItemControl", automation_id="File", expand_pattern=ExpandPattern(),
    )
    save_menu = _FakeControl("保存", "MenuItemControl", invoke_pattern=InvokePattern())
    window = {"hwnd": 1, "process_id": 7, "title": "无标题 - Notepad",
              "process_name": "notepad.exe", "class_name": "Notepad"}
    recipe_target = {
        "target_type": "ControlPathTarget", "recipe_id": "notepad.save",
        "application_alias": "notepad",
    }
    trigger_result = {
        "ok": True, "available": True, "window": window,
        "control": {"name": "文件", "control_type": "MenuItemControl", "process_id": 7},
        "_control": file_menu,
    }
    monkeypatch.setattr(windows, "_resolve_control_object", lambda *_args, **_kwargs: trigger_result)
    scan_results = [
        {"ok": True, "available": True, "popups": []},
        {"ok": True, "available": True, "popups": [{
            "identity": ("hwnd", 88),
            "record": {"class_name": "MenuFlyout", "process_id": 7},
            "menu_candidates": [{
                "control": save_menu,
                "record": {"name": "保存", "control_type": "MenuItemControl", "process_id": 7},
                "match_reason": "semantic_alias:save",
            }],
        }]},
    ]
    monkeypatch.setattr(windows, "_scan_desktop_popups", lambda *_args, **_kwargs: scan_results.pop(0))
    monkeypatch.setattr(windows, "_document_modified_state", lambda *_args: None)
    monkeypatch.setattr(
        windows, "_find_save_as_dialog",
        lambda **_kwargs: {"ok": True, "available": True,
                            "dialog": {"record": {"name": "另存为"}}},
    )

    trace = desktop_trace.new_execution_trace({
        "source": "desktop_task_step", "task_id": "task-trace",
        "step_id": "step_3", "action_type": "click_control", "action_id": "action-trace",
    })
    with caplog.at_level(logging.INFO, logger="jarvis2.desktop_execution_trace"):
        with desktop_trace.active_execution_trace(trace):
            result = windows.execute_control_path(window, recipe_target)

    stages = {
        record.message.split('"stage": "', 1)[1].split('"', 1)[0]
        for record in caplog.records
        if record.name == "jarvis2.desktop_execution_trace"
        and '"stage": "' in record.message
    }
    assert result["ok"] is True
    assert {
        "FILE_CONTROL_RESOLVED", "BEFORE_EXPAND", "EXPAND_RETURNED",
        "POPUP_BASELINE_CAPTURED", "POPUP_POLL_START", "POPUP_CANDIDATE_FOUND",
        "SAVE_ITEM_RESOLVED", "SAVE_INVOKED", "VERIFY_START",
    }.issubset(stages)
    assert all("输入正文" not in record.message for record in caplog.records
               if record.name == "jarvis2.desktop_execution_trace")


def test_menu_popup_resolution_refuses_two_new_flyouts(monkeypatch):
    save = {"control": object(), "record": {"name": "保存", "control_type": "MenuItemControl"}}
    monkeypatch.setattr(
        windows, "_scan_desktop_popups",
        lambda *_args, **_kwargs: {"ok": True, "available": True, "popups": [
            {"identity": ("hwnd", 1), "record": {"process_id": 7}, "menu_candidates": [save]},
            {"identity": ("hwnd", 2), "record": {"process_id": 7}, "menu_candidates": [save]},
        ]},
    )

    result = windows._wait_for_menu_popup(
        {"ok": True, "popups": []}, target_process_id=7,
        selector={"name": "保存", "control_type": "MenuItemControl"}, timeout=0.1,
    )

    assert result["ok"] is False
    assert result["error_code"] == "CONTROL_AMBIGUOUS"


def test_menu_popup_resolution_refuses_popup_from_other_process(monkeypatch):
    save = {"control": object(), "record": {"name": "保存", "control_type": "MenuItemControl"}}
    monkeypatch.setattr(
        windows, "_scan_desktop_popups",
        lambda *_args, **_kwargs: {"ok": True, "available": True, "popups": [
            {"identity": ("hwnd", 3), "record": {"process_id": 99}, "menu_candidates": [save]},
        ]},
    )

    result = windows._wait_for_menu_popup(
        {"ok": True, "popups": []}, target_process_id=7,
        selector={"name": "保存", "control_type": "MenuItemControl"}, timeout=0.1,
    )

    assert result["ok"] is False
    assert result["error_code"] == "CONTROL_AMBIGUOUS"


def test_menu_popup_timeout_is_explicit(monkeypatch):
    monkeypatch.setattr(
        windows, "_scan_desktop_popups",
        lambda *_args, **_kwargs: {"ok": True, "available": True, "popups": []},
    )

    result = windows._wait_for_menu_popup(
        {"ok": True, "popups": []}, target_process_id=7,
        selector={"name": "保存", "control_type": "MenuItemControl"}, timeout=0,
    )

    assert result["ok"] is False
    assert result["error_code"] == "ACTION_TIMEOUT"


def test_prepare_text_target_refuses_password_control(monkeypatch):
    monkeypatch.setattr(
        windows,
        "_prepare_control_target",
        lambda *_args, **_kwargs: {
            "ok": True,
            "window": {"hwnd": 1},
            "control": {"name": "密码", "is_password": True},
            "selector": {"name": "密码", "control_type": "EditControl"},
        },
    )

    result = windows.prepare_text_target("示例窗口", "密码")

    assert result["ok"] is False
    assert "密码" in result["error"]


def test_click_control_uses_resolved_uia_control_not_coordinates(monkeypatch):
    class FakePattern:
        def __init__(self, control):
            self.control = control

        def Invoke(self):
            self.control.clicked += 1
            return True

    class FakeControl:
        def __init__(self):
            self.clicked = 0

        def GetInvokePattern(self):
            return FakePattern(self)

    control = FakeControl()
    monkeypatch.setattr(
        windows,
        "_resolve_control_object",
        lambda *_args, **_kwargs: {
            "ok": True,
            "available": True,
            "window": {"hwnd": 1},
            "control": {"name": "保存", "control_type": "ButtonControl"},
            "selector": {"name": "保存", "control_type": "ButtonControl"},
            "_control": control,
        },
    )

    result = windows.click_control({"hwnd": 1}, {"name": "保存"})

    assert result["ok"] is True
    assert control.clicked == 1


def test_click_control_reports_uia_invoke_failure(monkeypatch):
    class FakePattern:
        def Invoke(self):
            return False

    class FakeControl:
        def GetInvokePattern(self):
            return FakePattern()

    control = FakeControl()
    monkeypatch.setattr(
        windows,
        "_resolve_control_object",
        lambda *_args, **_kwargs: {
            "ok": True,
            "available": True,
            "window": {"hwnd": 1},
            "control": {"name": "保存", "control_type": "ButtonControl"},
            "selector": {"name": "保存", "control_type": "ButtonControl"},
            "_control": control,
        },
    )

    result = windows.click_control({"hwnd": 1}, {"name": "保存"})

    assert result["ok"] is False
    assert result["error_code"] == "EXECUTION_FAILED"


def test_set_control_value_uses_value_pattern_and_never_keyboard(monkeypatch):
    class FakePattern:
        IsReadOnly = False

        def __init__(self):
            self.values: list[str] = []

        def SetValue(self, value):
            self.values.append(value)

        @property
        def Value(self):
            return self.values[-1] if self.values else ""

    class FakeControl:
        IsPassword = False

        def __init__(self, pattern):
            self.pattern = pattern

        def GetValuePattern(self):
            return self.pattern

    pattern = FakePattern()
    control = FakeControl(pattern)
    monkeypatch.setattr(
        windows,
        "_resolve_control_object",
        lambda *_args, **_kwargs: {
            "ok": True,
            "available": True,
            "window": {"hwnd": 1},
            "control": {"name": "编辑器", "control_type": "EditControl"},
            "selector": {"name": "编辑器", "control_type": "EditControl"},
            "_control": control,
        },
    )

    result = windows.set_control_value({"hwnd": 1}, {"name": "编辑器"}, "测试文本")

    assert result["ok"] is True
    assert pattern.values == ["测试文本"]


def test_set_control_value_requires_value_pattern_readback(monkeypatch):
    class FakePattern:
        IsReadOnly = False

        def SetValue(self, _value):
            pass

        @property
        def Value(self):
            return "旧值"

    class FakeControl:
        IsPassword = False

        def GetValuePattern(self):
            return FakePattern()

    monkeypatch.setattr(
        windows,
        "_resolve_control_object",
        lambda *_args, **_kwargs: {
            "ok": True,
            "available": True,
            "window": {"hwnd": 1},
            "control": {"name": "编辑器", "control_type": "EditControl"},
            "selector": {"name": "编辑器", "control_type": "EditControl"},
            "_control": FakeControl(),
        },
    )

    result = windows.set_control_value({"hwnd": 1}, {"name": "编辑器"}, "新值")

    assert result["ok"] is False
    assert result["error_code"] == "VERIFICATION_FAILED"
