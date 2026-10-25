# -*- coding: utf-8 -*-
"""One-shot, read-only UIA evidence collector for the current Save-As dialog."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
import re
import sys
from typing import Any

import uiautomation as auto


USER32 = ctypes.WinDLL("user32", use_last_error=True)
KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
MAX_TREE_DEPTH = 6
MAX_TREE_NODES = 1200


def _text(value: Any) -> str:
    return str(value or "")


def _safe_value(obj: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(obj, name)
    except Exception:
        return default


def _process_name(pid: int) -> str:
    if not pid:
        return ""
    handle = KERNEL32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if KERNEL32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return buffer.value
    except Exception:
        pass
    finally:
        KERNEL32.CloseHandle(handle)
    return ""


def _top_level_windows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    USER32.IsWindowVisible.argtypes = [wintypes.HWND]
    USER32.IsWindowVisible.restype = wintypes.BOOL
    USER32.IsWindowEnabled.argtypes = [wintypes.HWND]
    USER32.IsWindowEnabled.restype = wintypes.BOOL
    USER32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    USER32.GetWindowTextLengthW.restype = ctypes.c_int
    USER32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    USER32.GetWindowTextW.restype = ctypes.c_int
    USER32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    USER32.GetClassNameW.restype = ctypes.c_int
    USER32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    USER32.GetWindowThreadProcessId.restype = wintypes.DWORD

    def callback(hwnd: int, _lparam: int) -> bool:
        if not USER32.IsWindowVisible(hwnd):
            return True
        title_length = USER32.GetWindowTextLengthW(hwnd)
        title_buffer = ctypes.create_unicode_buffer(max(title_length + 1, 1))
        USER32.GetWindowTextW(hwnd, title_buffer, len(title_buffer))
        title = title_buffer.value
        class_buffer = ctypes.create_unicode_buffer(512)
        USER32.GetClassNameW(hwnd, class_buffer, len(class_buffer))
        pid = wintypes.DWORD(0)
        USER32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        rows.append({
            "hwnd": int(hwnd),
            "title": title,
            "class_name": class_buffer.value,
            "process_id": int(pid.value),
            "process_path": _process_name(int(pid.value)),
            "is_enabled": bool(USER32.IsWindowEnabled(hwnd)),
        })
        return True

    USER32.EnumWindows(enum_proc(callback), 0)
    foreground = int(USER32.GetForegroundWindow())
    for row in rows:
        row["is_foreground"] = row["hwnd"] == foreground
    return rows


PATTERN_GETTERS = {
    "ValuePattern": "GetValuePattern",
    "InvokePattern": "GetInvokePattern",
    "SelectionItemPattern": "GetSelectionItemPattern",
    "ExpandCollapsePattern": "GetExpandCollapsePattern",
    "LegacyIAccessiblePattern": "GetLegacyIAccessiblePattern",
    "TextPattern": "GetTextPattern",
    "RangeValuePattern": "GetRangeValuePattern",
    "SelectionPattern": "GetSelectionPattern",
    "ScrollPattern": "GetScrollPattern",
}


def _pattern_info(control: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for label, getter_name in PATTERN_GETTERS.items():
        getter = getattr(control, getter_name, None)
        if not callable(getter):
            result[label] = False
            continue
        try:
            pattern = getter()
            result[label] = pattern is not None
        except Exception as exc:
            result[label] = {"available": False, "error": type(exc).__name__}
    value_getter = getattr(control, "GetValuePattern", None)
    if callable(value_getter):
        try:
            pattern = value_getter()
            if pattern is not None:
                value = _safe_value(pattern, "Value", None)
                readonly = _safe_value(pattern, "IsReadOnly", None)
                result["ValuePattern_details"] = {
                    "value": _text(value)[:500],
                    "is_read_only": readonly,
                }
        except Exception as exc:
            result["ValuePattern_details"] = {"error": type(exc).__name__}
    return result


def _control_record(control: Any, depth: int, path: list[str], index: int) -> dict[str, Any]:
    name = _text(_safe_value(control, "Name", ""))
    control_type = _text(_safe_value(control, "ControlTypeName", ""))
    hwnd = int(_safe_value(control, "NativeWindowHandle", 0) or 0)
    record = {
        "index": index,
        "depth": depth,
        "path": path + [f"{control_type}:{name}"],
        "name": name,
        "control_type": control_type,
        "automation_id": _text(_safe_value(control, "AutomationId", "")),
        "class_name": _text(_safe_value(control, "ClassName", "")),
        "hwnd": hwnd,
        "process_id": int(_safe_value(control, "ProcessId", 0) or 0),
        "is_enabled": bool(_safe_value(control, "IsEnabled", True)),
        "is_offscreen": bool(_safe_value(control, "IsOffscreen", False)),
        "patterns": _pattern_info(control),
    }
    if record["control_type"] in {"EditControl", "DocumentControl", "ComboBoxControl"}:
        record["current_value"] = record["patterns"].get("ValuePattern_details", {}).get("value", "")
    return record


def _tree(root: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    def walk(control: Any, depth: int, path: list[str]) -> None:
        if len(records) >= MAX_TREE_NODES:
            return
        index = len(records)
        record = _control_record(control, depth, path, index)
        records.append(record)
        if depth >= MAX_TREE_DEPTH:
            return
        try:
            children = control.GetChildren() or []
        except Exception:
            children = []
        for child in children:
            walk(child, depth + 1, record["path"])
            if len(records) >= MAX_TREE_NODES:
                break

    walk(root, 0, [])
    return records


def _candidate_summary(nodes: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    def matches(node: dict[str, Any], names: tuple[str, ...], control_types: tuple[str, ...] = ()) -> bool:
        name = node["name"].casefold()
        if names and not any(token.casefold() in name for token in names):
            return False
        return not control_types or node["control_type"] in control_types

    def compact(node: dict[str, Any]) -> dict[str, Any]:
        return {
            key: node[key]
            for key in (
                "index", "depth", "path", "name", "control_type", "automation_id",
                "class_name", "hwnd", "process_id", "is_enabled", "is_offscreen", "patterns",
                "current_value",
            )
            if key in node
        }

    filename = [
        compact(node) for node in nodes
        if node["control_type"] in {"EditControl", "DocumentControl"}
        and (
            any(token in node["name"].casefold() for token in ("文件名", "file name", "filename"))
            or node["patterns"].get("ValuePattern") is True
        )
    ]
    save = [compact(node) for node in nodes if matches(node, ("保存", "save"), ("ButtonControl",))]
    cancel = [compact(node) for node in nodes if matches(node, ("取消", "cancel"), ("ButtonControl",))]
    combos = [compact(node) for node in nodes if node["control_type"] == "ComboBoxControl"]
    address = [
        compact(node) for node in nodes
        if node["control_type"] in {"EditControl", "ComboBoxControl", "ToolBarControl", "PaneControl", "CustomControl"}
        and any(token in node["name"].casefold() for token in (
            "地址", "路径", "address", "location", "文件夹", "folder", "breadcrumb",
        ))
    ]
    encoding = [compact(node) for node in nodes if any(
        token in node["name"].casefold() for token in ("编码", "encoding", "utf-8", "unicode")
    )]
    return {
        "filename_edit_candidates": filename,
        "save_button_candidates": save,
        "cancel_button_candidates": cancel,
        "address_candidates": address,
        "file_type_combo_candidates": combos,
        "encoding_candidates": encoding,
    }


def main() -> int:
    windows = _top_level_windows()
    save_windows = [
        row for row in windows
        if re.search(r"另存为|save\s*as", row["title"], flags=re.IGNORECASE)
    ]
    probe_windows = [
        row for row in windows
        if (
            "notepad" in row["title"].casefold()
            or re.search(r"另存为|save|dialog", row["title"], flags=re.IGNORECASE)
            or row["class_name"] in {"#32770", "ApplicationFrameWindow", "Windows.UI.Core.CoreWindow"}
        )
    ]
    result: dict[str, Any] = {
        "python": sys.executable,
        "uiautomation": getattr(auto, "VERSION", "uiautomation"),
        "foreground_hwnd": int(USER32.GetForegroundWindow()),
        "top_level_windows": windows,
        "save_as_candidates": [],
        "nested_save_as_hits": [],
        "read_only": True,
        "actions_performed": [],
    }
    for window in save_windows:
        try:
            root = auto.ControlFromHandle(window["hwnd"])
            if root is None:
                result["save_as_candidates"].append({"window": window, "error": "UIA root unavailable"})
                continue
            nodes = _tree(root)
            result["save_as_candidates"].append({
                "window": window,
                "uia_root": _control_record(root, 0, [], -1),
                "tree_truncated": len(nodes) >= MAX_TREE_NODES,
                "tree": nodes,
                "candidates": _candidate_summary(nodes),
            })
        except Exception as exc:
            result["save_as_candidates"].append({
                "window": window,
                "error": f"{type(exc).__name__}: {exc}",
            })
    for window in probe_windows:
        try:
            root = auto.ControlFromHandle(window["hwnd"])
            if root is None:
                continue
            nodes = _tree(root)
            hits = [
                node for node in nodes
                if re.search(r"另存为|save\s*as", node["name"], flags=re.IGNORECASE)
            ]
            if hits:
                result["nested_save_as_hits"].append({"window": window, "hits": hits})
        except Exception as exc:
            result["nested_save_as_hits"].append({
                "window": window,
                "error": f"{type(exc).__name__}: {exc}",
            })
    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    sys.stdout.buffer.write(payload.encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
