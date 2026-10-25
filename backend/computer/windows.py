# -*- coding: utf-8 -*-
"""
computer/windows.py — Windows 本地观察桥（只读）
=================================================
除观察能力外，本模块还提供受动作层调用的受限 UI Automation 原语。

设计约束：
* 不执行程序，不移动鼠标，不发送按键，不修改剪贴板；
* 原生窗口信息使用标准库 ctypes，避免把 pywin32 绑定成硬依赖；
* UIA 控件树通过可选的 uiautomation 包读取，缺失时返回明确降级状态；
* 所有上限在服务端强制限制，防止一次遍历拖垮 localhost 后端；
* 写操作只能按已观察到的窗口和唯一控件选择器重新定位，绝不接受坐标、
  按键序列、任意程序路径或 Shell 命令。
"""
from __future__ import annotations

import ctypes
import difflib
import importlib.util
import logging
import os
import re
import time
import unicodedata
from pathlib import Path
from ctypes import wintypes

from .trace import control_metadata, current_execution_trace, window_metadata


DESC = "Windows 活动窗口与 UI Automation 控件树（只读）"
IS_WINDOWS = os.name == "nt"
UIA_PACKAGE = "uiautomation"
MAX_WINDOWS = 100
MAX_UI_DEPTH = 5
MAX_UI_NODES = 300
MAX_TEXT = 240
MAX_ACTION_UI_DEPTH = 8
MAX_ACTION_UI_NODES = 600
MAX_CONTROL_QUERY = 120
SAFE_WINDOW_SCORE = 700
WINDOW_AMBIGUITY_MARGIN = 60
FUZZY_DISCOVERY_THRESHOLD = 0.55
SAFE_CONTROL_SCORE = 700
CONTROL_AMBIGUITY_MARGIN = 60
POPUP_TIMEOUT_SECONDS = 2.0
POPUP_POLL_INTERVAL_SECONDS = 0.05

ERROR_CODES = {
    "WINDOW_NOT_FOUND",
    "WINDOW_AMBIGUOUS",
    "CONTROL_NOT_FOUND",
    "CONTROL_AMBIGUOUS",
    "CONTROL_NOT_WRITABLE",
    "UIA_PATTERN_UNAVAILABLE",
    "UIA_UNAVAILABLE",
    "PERMISSION_DENIED",
    "ACTION_TIMEOUT",
    "EXECUTION_FAILED",
    "VERIFICATION_FAILED",
}

LOGGER = logging.getLogger(__name__)

# 应用别名是窗口解析的唯一知识源。Action 层不得自行判断应用名称。
# process_names 使用可执行文件名，class_names 使用 Win32 ClassName；title_tokens
# 用于识别“项目名 - 应用名”这类动态标题，不把任意模糊标题直接当成应用。
APPLICATION_ALIASES = {
    "notepad": {
        "aliases": ("记事本", "Notepad", "notepad.exe"),
        "process_names": ("notepad.exe",),
        "class_names": ("Notepad",),
        "title_tokens": ("Notepad",),
    },
    "powershell": {
        "aliases": ("PowerShell", "Windows PowerShell", "pwsh", "powershell.exe"),
        "process_names": ("powershell.exe", "pwsh.exe"),
        "class_names": ("ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS"),
        "title_tokens": ("PowerShell", "Windows PowerShell"),
    },
    "edge": {
        "aliases": ("Edge", "Microsoft Edge", "msedge", "msedge.exe"),
        "process_names": ("msedge.exe",),
        "class_names": (),
        "title_tokens": ("Microsoft Edge",),
    },
    "chrome": {
        "aliases": ("Chrome", "Google Chrome", "chrome.exe"),
        "process_names": ("chrome.exe",),
        "class_names": (),
        "title_tokens": ("Google Chrome",),
    },
    "vscode": {
        "aliases": ("VS Code", "Visual Studio Code", "Code", "Code.exe"),
        "process_names": ("Code.exe",),
        "class_names": (),
        "title_tokens": ("Visual Studio Code", "VS Code"),
    },
    "obsidian": {
        "aliases": ("Obsidian", "obsidian.exe"),
        "process_names": ("obsidian.exe",),
        "class_names": (),
        "title_tokens": ("Obsidian",),
    },
    "wechat": {
        "aliases": ("微信", "WeChat", "Weixin"),
        "process_names": ("WeChat.exe", "Weixin.exe"),
        "class_names": (),
        "title_tokens": ("WeChat", "Weixin", "微信"),
    },
}

_CLASS_QUERY_RE = re.compile(r"^\s*classname\s*=\s*(.+?)\s*$", re.IGNORECASE)

# 第一批可点击控件刻意保持很小，避免把菜单容器、文档正文或任意画布当作目标。
CLICK_CONTROL_TYPES = frozenset({
    "ButtonControl", "HyperlinkControl", "MenuItemControl", "TabItemControl",
    "CheckBoxControl", "RadioButtonControl",
})
# 输入控件不是按 ControlType 静态判断可写性；DocumentControl 只有在运行时
# 探测到可靠的 UIA 写入 Pattern 后才会被接受。
INPUT_CONTROL_TYPES = frozenset({"EditControl", "DocumentControl"})
TEXT_CONTROL_TYPES = INPUT_CONTROL_TYPES

# 控件语义别名是 Control Resolver 的唯一知识源。别名本身不会绕过类型、
# 可见性、能力和歧义检查。
CONTROL_ALIASES = {
    "editor": {
        "aliases": ("编辑器", "文本编辑器", "正文", "正文区域", "输入框", "editor", "text editor"),
        "control_types": INPUT_CONTROL_TYPES,
        "class_names": ("RichEditD2DPT",),
        "capabilities": ("text_input",),
    },
    "save": {
        "aliases": ("保存", "Save"),
        "control_types": frozenset({"MenuItemControl", "ButtonControl"}),
        "capabilities": ("invoke",),
    },
    "save_as": {
        "aliases": ("另存为", "Save As"),
        "control_types": frozenset({"MenuItemControl", "ButtonControl"}),
        "capabilities": ("invoke",),
    },
    "file": {
        "aliases": ("文件", "File"),
        "control_types": frozenset({"MenuItemControl"}),
        "automation_ids": ("File",),
        "capabilities": ("expand",),
    },
}

# 受限的、可审计的 ControlPath recipe。这里描述能力路径，不在 click/input
# 函数中堆应用判断；未知路径不会由 LLM 动态生成。
CONTROL_PATH_RECIPES = {
    "notepad": {
        "save": {
            "recipe_id": "notepad.save",
            "steps": (
                {
                    "target": "文件",
                    "semantic_alias": "file",
                    "operation": "expand",
                    "scope": "window",
                    "selector": {
                        "name": "文件",
                        "control_type": "MenuItemControl",
                        "automation_id": "File",
                    },
                },
                {
                    "target": "保存",
                    "semantic_alias": "save",
                    "operation": "invoke",
                    "scope": "desktop_popup",
                    "selector": {
                        "name": "保存",
                        "control_type": "MenuItemControl",
                    },
                },
            ),
            "verify": {"kind": "save_command"},
        },
    },
}


def _unsupported(message: str = "电脑观察桥仅支持 Windows") -> dict:
    return {
        "ok": False,
        "available": False,
        "error_code": "UIA_UNAVAILABLE",
        "error": message,
    }


def _error(error_code: str, message: str, *, available: bool = True, **extra) -> dict:
    """创建统一的本机动作错误；不把异常堆栈暴露给前端。"""
    code = error_code if error_code in ERROR_CODES else "EXECUTION_FAILED"
    return {"ok": False, "available": available, "error_code": code,
            "error": message, **extra}


def normalize_window_text(value: object) -> str:
    """用于窗口定位的稳定规范化，不删除有意义的应用/文件名字符。"""
    if value is None:
        return ""
    value = unicodedata.normalize("NFKC", str(value)).strip().casefold()
    return re.sub(r"[\s\u3000]+", "", value)


def _process_basename(value: object) -> str:
    raw = str(value or "").replace("/", "\\")
    return raw.rsplit("\\", 1)[-1].strip()


def _class_query(value: str) -> str:
    match = _CLASS_QUERY_RE.match(value or "")
    return match.group(1).strip() if match else ""


def _alias_profile(value: str) -> tuple[str, dict] | None:
    class_name = _class_query(value)
    lookup = normalize_window_text(class_name or value)
    if not lookup:
        return None
    for alias_id, profile in APPLICATION_ALIASES.items():
        values = tuple(profile.get("aliases", ()))
        if class_name and any(normalize_window_text(item) == lookup
                              for item in profile.get("class_names", ())):
            return alias_id, profile
        if not class_name and any(normalize_window_text(item) == lookup for item in values):
            return alias_id, profile
    return None


def _title_has_app_suffix(title: str, token: str) -> bool:
    """识别“文档名 - 应用名”标题，避免普通 title contains 造成误选。"""
    title_norm = normalize_window_text(title)
    token_norm = normalize_window_text(token)
    if not title_norm or not token_norm:
        return False
    return title_norm == token_norm or title_norm.endswith(f"-{token_norm}")


def _fuzzy_tokens(value: object) -> list[str]:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.findall(r"[a-z0-9\u4e00-\u9fff]+", text)


def _candidate_summary(candidate: dict) -> dict:
    row = candidate.get("window") or candidate
    return {
        "title": str(row.get("title") or ""),
        "class_name": str(row.get("class_name") or ""),
        "process_name": str(row.get("process_name") or ""),
        "process_id": int(row.get("process_id") or 0),
        "match_reason": candidate.get("match_reason", ""),
        "match_score": candidate.get("match_score", 0),
    }


def _log_resolution(*, raw_target: str, normalized_target: str,
                    resolved_alias: str | None, enumerated_window_count: int,
                    candidates: list[dict], selected: dict | None = None,
                    error_code: str | None = None) -> None:
    """只记录窗口元数据和匹配结果，不记录命令正文或输入内容。"""
    LOGGER.info(
        "desktop_action_resolve raw_target=%r normalized_target=%r "
        "resolved_alias=%r enumerated_window_count=%d candidates=%s "
        "selected_window=%s error_code=%s",
        raw_target, normalized_target, resolved_alias, enumerated_window_count,
        [_candidate_summary(item) for item in candidates],
        _candidate_summary(selected) if selected else None,
        error_code,
    )


def _api():
    if not IS_WINDOWS:
        return None, None
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    return user32, kernel32


def _hwnd_value(hwnd) -> int:
    if hwnd is None:
        return 0
    return int(getattr(hwnd, "value", hwnd) or 0)


def _window_text(user32, hwnd: int) -> str:
    if not hwnd:
        return ""
    try:
        h = ctypes.c_void_p(hwnd)
        user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
        user32.GetWindowTextLengthW.restype = ctypes.c_int
        length = max(0, min(int(user32.GetWindowTextLengthW(h)), 4096))
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW.argtypes = [ctypes.c_void_p, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.GetWindowTextW(h, buf, len(buf))
        return buf.value[:MAX_TEXT]
    except Exception:  # noqa: BLE001
        return ""


def _class_name(user32, hwnd: int) -> str:
    if not hwnd:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(512)
        user32.GetClassNameW.argtypes = [ctypes.c_void_p, wintypes.LPWSTR, ctypes.c_int]
        user32.GetClassNameW.restype = ctypes.c_int
        user32.GetClassNameW(ctypes.c_void_p(hwnd), buf, len(buf))
        return buf.value[:MAX_TEXT]
    except Exception:  # noqa: BLE001
        return ""


def _window_pid(user32, hwnd: int) -> int:
    if not hwnd:
        return 0
    try:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(pid))
        return int(pid.value)
    except Exception:  # noqa: BLE001
        return 0


def _process_name(kernel32, pid: int) -> str:
    """只返回进程文件名，不把完整本地路径暴露到前端。"""
    if not pid:
        return ""
    handle = None
    try:
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ""
        buf = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buf))
        kernel32.QueryFullProcessImageNameW.argtypes = [
            ctypes.c_void_p, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
        ]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return ""
        return Path(buf.value).name[:MAX_TEXT]
    except Exception:  # noqa: BLE001
        return ""
    finally:
        if handle:
            try:
                kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
                kernel32.CloseHandle.restype = wintypes.BOOL
                kernel32.CloseHandle(handle)
            except Exception:  # noqa: BLE001
                pass


def _rect(user32, hwnd: int) -> dict | None:
    try:
        rect = wintypes.RECT()
        user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL
        if not user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(rect)):
            return None
        return {"left": rect.left, "top": rect.top, "right": rect.right, "bottom": rect.bottom}
    except Exception:  # noqa: BLE001
        return None


def _window_record(user32, kernel32, hwnd: int, *, active: bool = False) -> dict:
    pid = _window_pid(user32, hwnd)
    return {
        "hwnd": hwnd,
        "title": _window_text(user32, hwnd),
        "class_name": _class_name(user32, hwnd),
        "process_id": pid,
        "process_name": _process_name(kernel32, pid),
        "bounds": _rect(user32, hwnd),
        "active": active,
    }


def active_window() -> dict:
    """返回当前前台窗口的非敏感元数据。"""
    user32, kernel32 = _api()
    if user32 is None:
        return _unsupported()
    try:
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        hwnd = _hwnd_value(user32.GetForegroundWindow())
        if not hwnd:
            return _error("WINDOW_NOT_FOUND", "当前没有活动窗口")
        return {"ok": True, "available": True,
                "window": _window_record(user32, kernel32, hwnd, active=True)}
    except Exception as e:  # noqa: BLE001
        return _error("EXECUTION_FAILED", f"读取活动窗口失败: {e}")


def list_windows(limit: int = 20) -> dict:
    """列出可见顶层窗口，默认最多 20 个。"""
    user32, kernel32 = _api()
    if user32 is None:
        return _unsupported()
    try:
        limit = max(1, min(int(limit), MAX_WINDOWS))
    except (TypeError, ValueError):
        limit = 20
    try:
        user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
        user32.IsWindowVisible.restype = wintypes.BOOL
        active = active_window().get("window", {}).get("hwnd")
        rows: list[dict] = []

        enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, ctypes.c_void_p, wintypes.LPARAM)

        @enum_proc
        def callback(hwnd, _lparam):
            value = _hwnd_value(hwnd)
            if value and user32.IsWindowVisible(ctypes.c_void_p(value)):
                title = _window_text(user32, value)
                if title.strip():
                    rows.append(_window_record(user32, kernel32, value, active=value == active))
            return bool(len(rows) < limit)

        user32.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
        user32.EnumWindows.restype = wintypes.BOOL
        user32.EnumWindows(callback, 0)
        return {"ok": True, "available": True, "windows": rows[:limit]}
    except Exception as e:  # noqa: BLE001
        return _error("EXECUTION_FAILED", f"枚举窗口失败: {e}")


def _uia_available() -> bool:
    if not IS_WINDOWS or importlib.util.find_spec(UIA_PACKAGE) is None:
        return False
    try:
        # 仅找到 distribution 还不够：comtypes/DLL 装载失败时应如实降级。
        __import__(UIA_PACKAGE)
        return True
    except Exception:  # noqa: BLE001
        return False


def ui_automation_available() -> bool:
    """公开 UIA 依赖状态，供能力目录如实标记可用性。"""
    return _uia_available()


def _control_value(control, name: str, default=""):
    try:
        value = getattr(control, name, default)
        if callable(value):
            value = value()
        return value if value is not None else default
    except Exception:  # noqa: BLE001
        return default


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _control_record(control, depth: int) -> dict:
    rect = _control_value(control, "BoundingRectangle", None)
    bounds = None
    if rect is not None:
        try:
            bounds = {"left": rect.left, "top": rect.top,
                      "right": rect.right, "bottom": rect.bottom}
        except Exception:  # noqa: BLE001
            bounds = None
    return {
        "depth": depth,
        "name": str(_control_value(control, "Name", ""))[:MAX_TEXT],
        "control_type": str(_control_value(control, "ControlTypeName", ""))[:MAX_TEXT],
        "automation_id": str(_control_value(control, "AutomationId", ""))[:MAX_TEXT],
        "class_name": str(_control_value(control, "ClassName", ""))[:MAX_TEXT],
        "process_id": _safe_int(_control_value(control, "ProcessId", 0)),
        "native_window_handle": _safe_int(_control_value(control, "NativeWindowHandle", 0)),
        "is_enabled": bool(_control_value(control, "IsEnabled", True)),
        "is_offscreen": bool(_control_value(control, "IsOffscreen", False)),
        "is_password": bool(_control_value(control, "IsPassword", False)),
        "bounds": bounds,
    }


def ui_tree(max_depth: int = 2, max_nodes: int = 80) -> dict:
    """读取当前活动窗口的 UIA 控件树，只返回可定位所需的元数据。"""
    if not IS_WINDOWS:
        return _unsupported()
    if not _uia_available():
        return {
            "ok": False,
            "available": False,
            "error": "uiautomation 依赖未安装，暂时只能观察活动窗口元数据。",
            "install": "pip install uiautomation",
        }
    try:
        max_depth = max(0, min(int(max_depth), MAX_UI_DEPTH))
        max_nodes = max(1, min(int(max_nodes), MAX_UI_NODES))
    except (TypeError, ValueError):
        max_depth, max_nodes = 2, 80

    current = active_window()
    hwnd = (current.get("window") or {}).get("hwnd")
    if not hwnd:
        return {"ok": False, "available": True, "error": "没有可检查的活动窗口"}

    try:
        import uiautomation as auto

        root = auto.ControlFromHandle(int(hwnd))
        if root is None:
            return {"ok": False, "available": True, "error": "UI Automation 无法获取活动窗口"}
        nodes: list[dict] = []

        def walk(control, depth: int) -> None:
            if len(nodes) >= max_nodes:
                return
            nodes.append(_control_record(control, depth))
            if depth >= max_depth:
                return
            try:
                children = control.GetChildren()
            except Exception:  # noqa: BLE001
                return
            for child in children or []:
                walk(child, depth + 1)
                if len(nodes) >= max_nodes:
                    break

        walk(root, 0)
        return {
            "ok": True,
            "available": True,
            "window": current.get("window"),
            "max_depth": max_depth,
            "truncated": len(nodes) >= max_nodes,
            "nodes": nodes,
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "available": True, "error": f"读取 UI Automation 控件树失败: {e}"}


def _clean_query(value, *, label: str) -> tuple[str | None, str | None]:
    """校验窗口/控件名称；这些值只用于精确定位，不接受控制字符。"""
    if not isinstance(value, str):
        return None, f"{label}必须是文本"
    result = value.strip()
    if not result:
        return None, f"{label}不能为空"
    if len(result) > MAX_CONTROL_QUERY:
        return None, f"{label}过长（最多 {MAX_CONTROL_QUERY} 个字符）"
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in result):
        return None, f"{label}不能包含控制字符"
    return result, None


def _score_window(query: str, normalized_query: str, alias: tuple[str, dict] | None,
                  row: dict) -> dict | None:
    """为一个窗口生成最高质量匹配；每个候选只保留最高等级证据。"""
    title = str(row.get("title") or "").strip()
    class_name = str(row.get("class_name") or "").strip()
    process_name = _process_basename(row.get("process_name"))
    title_folded = title.casefold()
    title_normalized = normalize_window_text(title)
    class_normalized = normalize_window_text(class_name)
    process_normalized = normalize_window_text(process_name)
    class_query = _class_query(query)
    scores: list[tuple[int, str]] = []

    if not class_query and title_normalized == normalized_query:
        scores.append((1000, "exact_normalized_title"))

    if alias:
        _alias_id, profile = alias
        process_names = {
            normalize_window_text(item) for item in profile.get("process_names", ())
        }
        class_names = {
            normalize_window_text(item) for item in profile.get("class_names", ())
        }
        if not class_query and process_normalized in process_names:
            scores.append((960, "application_alias:process_executable"))
        if class_normalized in class_names:
            scores.append((955, "application_alias:exact_class_name"))
        if not class_query and any(_title_has_app_suffix(title, token)
               for token in profile.get("title_tokens", ())):
            scores.append((930, "application_alias:dynamic_title"))

    if not class_query and process_normalized == normalized_query:
        scores.append((900, "process_executable"))

    expected_class = normalize_window_text(class_query or query)
    if class_normalized == expected_class and expected_class:
        scores.append((850, "exact_class_name"))

    if not class_query and query.casefold() in title_folded:
        scores.append((760, "title_contains"))
    if not class_query and normalized_query and normalized_query in title_normalized:
        scores.append((720, "normalized_contains"))

    # Fuzzy 只负责发现候选。只有高分、且与第二候选有足够差距时，外层才会采用。
    if not class_query and normalized_query:
        tokens = _fuzzy_tokens(title) + _fuzzy_tokens(class_name) + _fuzzy_tokens(process_name)
        ratios = [
            difflib.SequenceMatcher(None, normalized_query, normalize_window_text(token)).ratio()
            for token in tokens if token
        ]
        ratio = max(ratios, default=0.0)
        if ratio >= FUZZY_DISCOVERY_THRESHOLD:
            scores.append((int(450 + ratio * 300), "fuzzy"))

    if not scores:
        return None
    score, reason = max(scores, key=lambda item: item[0])
    return {"window": row, "match_reason": reason, "match_score": score}


def resolve_window(query: str | None = None) -> dict:
    """安全、确定地解析窗口；无法区分时绝不静默选择。"""
    if not IS_WINDOWS:
        return _unsupported()
    if not query:
        current = active_window()
        if not current.get("ok"):
            return _error(
                current.get("error_code", "WINDOW_NOT_FOUND"),
                current.get("error", "没有可用的活动窗口"),
            )
        return {"ok": True, "available": True, "window": current.get("window")}

    cleaned, error = _clean_query(query, label="窗口标题")
    if error:
        return _error("WINDOW_NOT_FOUND", error)
    listed = list_windows(limit=MAX_WINDOWS)
    if not listed.get("ok"):
        return _error(
            listed.get("error_code", "EXECUTION_FAILED"),
            listed.get("error", "无法枚举可见窗口"),
        )
    rows = listed.get("windows") or []
    normalized = normalize_window_text(cleaned)
    alias = _alias_profile(cleaned)
    resolved_alias = alias[0] if alias else None
    candidates = [
        candidate for row in rows
        if (candidate := _score_window(cleaned, normalized, alias, row)) is not None
    ]
    candidates.sort(key=lambda item: item["match_score"], reverse=True)
    meta = {
        "raw_target": cleaned,
        "normalized_target": normalized,
        "resolved_alias": resolved_alias,
        "enumerated_window_count": len(rows),
        "candidates": [_candidate_summary(item) for item in candidates],
    }
    if not candidates:
        _log_resolution(**meta, selected=None, error_code="WINDOW_NOT_FOUND")
        return _error(
            "WINDOW_NOT_FOUND", f"找不到窗口「{cleaned}」", resolution=meta,
        )

    best = candidates[0]
    second = candidates[1] if len(candidates) > 1 else None
    too_low = best["match_score"] < SAFE_WINDOW_SCORE
    too_close = bool(
        second and best["match_score"] - second["match_score"] < WINDOW_AMBIGUITY_MARGIN
    )
    if too_low or too_close:
        _log_resolution(**meta, selected=None, error_code="WINDOW_AMBIGUOUS")
        return _error(
            "WINDOW_AMBIGUOUS",
            f"窗口「{cleaned}」无法安全确定唯一目标，请使用更完整的窗口标题",
            resolution=meta,
        )

    meta["selected_window"] = _candidate_summary(best)
    _log_resolution(
        raw_target=meta["raw_target"],
        normalized_target=meta["normalized_target"],
        resolved_alias=meta["resolved_alias"],
        enumerated_window_count=meta["enumerated_window_count"],
        candidates=candidates,
        selected=best,
        error_code=None,
    )
    return {
        "ok": True,
        "available": True,
        "window": best["window"],
        "resolution": meta,
    }


def _safe_foreground_record(user32, kernel32, hwnd: int) -> dict:
    """为 trace 读取前台窗口元数据；诊断读取失败不能影响动作结果。"""
    if not hwnd:
        return {}
    try:
        return _window_record(user32, kernel32, hwnd, active=True)
    except Exception:  # noqa: BLE001
        return {}


def _trace_foreground(trace, stage: str, hwnd: int, record: dict) -> None:
    try:
        _trace_emit(
            trace,
            stage,
            foreground_hwnd=hwnd,
            foreground_title=record.get("title"),
            foreground_class=record.get("class_name"),
            foreground_process_id=record.get("process_id"),
        )
    except Exception:  # noqa: BLE001
        # Trace 是诊断旁路，不能改变 Windows 动作的原有异常/返回语义。
        pass


def _trace_emit(trace, stage: str, **fields) -> None:
    """尽力写入诊断事件；任何诊断异常都不能影响原动作。"""
    if trace is None:
        return
    try:
        trace.emit(stage, **fields)
    except Exception:  # noqa: BLE001
        pass


def _safe_foreground_hwnd(user32) -> int:
    """读取埋点用前台句柄；失败时返回空值并继续原有动作。"""
    try:
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        return _hwnd_value(user32.GetForegroundWindow())
    except Exception:  # noqa: BLE001
        return 0


def activate_window(hwnd: int, *, expected_process_id: int = 0) -> dict:
    """将一个已验证的可见窗口置前；不启动程序、不模拟快捷键。"""
    user32, kernel32 = _api()
    if user32 is None:
        return _unsupported()
    try:
        hwnd = int(hwnd)
    except (TypeError, ValueError):
        return _error("WINDOW_NOT_FOUND", "窗口句柄无效")
    if hwnd <= 0:
        return _error("WINDOW_NOT_FOUND", "窗口句柄无效")
    try:
        user32.IsWindow.argtypes = [ctypes.c_void_p]
        user32.IsWindow.restype = wintypes.BOOL
        user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
        user32.IsWindowVisible.restype = wintypes.BOOL
        handle = ctypes.c_void_p(hwnd)
        if not user32.IsWindow(handle) or not user32.IsWindowVisible(handle):
            return _error("WINDOW_NOT_FOUND", "目标窗口已关闭或不可见")
        actual_pid = _window_pid(user32, hwnd)
        if expected_process_id and actual_pid != int(expected_process_id):
            return _error("WINDOW_NOT_FOUND", "目标窗口所属进程已变化")

        user32.IsIconic.argtypes = [ctypes.c_void_p]
        user32.IsIconic.restype = wintypes.BOOL
        trace = current_execution_trace()
        if user32.IsIconic(handle):
            user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
            user32.ShowWindow.restype = wintypes.BOOL
            if trace is not None:
                _trace_emit(trace, "ACTIVATE_METHOD_START", method_name="ShowWindow")
                show_return = user32.ShowWindow(handle, 9)  # SW_RESTORE
                _trace_emit(
                    trace,
                    "ACTIVATE_METHOD_RESULT", method_name="ShowWindow",
                    return_value=bool(show_return),
                )
            else:
                user32.ShowWindow(handle, 9)  # SW_RESTORE
        user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
        user32.SetForegroundWindow.restype = wintypes.BOOL
        if trace is not None:
            before_foreground = _safe_foreground_hwnd(user32)
            before_record = _safe_foreground_record(user32, kernel32, before_foreground)
            _trace_foreground(trace, "FOREGROUND_BEFORE", before_foreground, before_record)
            _trace_emit(trace, "ACTIVATE_METHOD_START", method_name="SetForegroundWindow")
            _trace_emit(trace, "ACTIVATE_CALLED", method_name="SetForegroundWindow")
        focused = bool(user32.SetForegroundWindow(handle))
        if trace is not None:
            _trace_emit(
                trace,
                "ACTIVATE_RETURNED", method_name="SetForegroundWindow",
                return_value=focused, activate_return_value=focused,
            )
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        now_foreground = _hwnd_value(user32.GetForegroundWindow())
        if trace is not None:
            foreground_record = _safe_foreground_record(user32, kernel32, now_foreground)
            _trace_foreground(trace, "FOREGROUND_IMMEDIATE", now_foreground, foreground_record)
            _trace_emit(
                trace,
                "ACTIVATE_METHOD_RESULT", method_name="SetForegroundWindow",
                return_value=focused,
            )
        if trace is not None:
            try:
                trace.set_result_fields(
                    expected_hwnd=hwnd,
                    actual_foreground_hwnd=now_foreground,
                    expected_process_id=expected_process_id,
                    actual_foreground_process_id=foreground_record.get("process_id"),
                )
            except Exception:  # noqa: BLE001
                pass
            _trace_emit(
                trace, "VERIFY_START", expected_hwnd=hwnd,
                actual_foreground_hwnd=now_foreground,
                expected_process_id=expected_process_id,
                actual_foreground_process_id=foreground_record.get("process_id"),
            )
            _trace_foreground(trace, "FOREGROUND_VERIFY", now_foreground, foreground_record)
        if not focused and now_foreground != hwnd:
            return _error(
                "PERMISSION_DENIED",
                "Windows 拒绝切换前台窗口，请手动选中目标窗口后重试",
                verification={"foreground_hwnd": now_foreground, "expected_hwnd": hwnd},
            )
        if now_foreground != hwnd:
            return _error(
                "VERIFICATION_FAILED",
                "窗口激活后未成为当前前台窗口",
                verification={"foreground_hwnd": now_foreground, "expected_hwnd": hwnd},
            )
        return {
            "ok": True,
            "available": True,
            "window": _window_record(user32, kernel32, hwnd, active=True),
            "verified": True,
            "verification": {"foreground_hwnd": now_foreground, "expected_hwnd": hwnd},
        }
    except Exception as exc:  # noqa: BLE001
        return _error("EXECUTION_FAILED", f"激活窗口失败: {exc}")


def _selector_from_record(record: dict) -> dict:
    """生成用于执行阶段二次定位的最小稳定选择器。"""
    selector = {
        "name": record.get("name", ""),
        "control_type": record.get("control_type", ""),
    }
    for key in ("automation_id", "class_name", "process_id"):
        value = record.get(key)
        if value not in (None, "", 0):
            selector[key] = value
    return selector


def _normalize_selector(selector: dict, allowed_types: frozenset[str]) -> tuple[dict | None, str | None]:
    if not isinstance(selector, dict):
        return None, "控件选择器无效"
    name, error = _clean_query(selector.get("name"), label="控件名称")
    if error:
        return None, error
    normalized: dict = {"name": name}
    for key in ("control_type", "automation_id", "class_name"):
        value = selector.get(key)
        if value in (None, ""):
            continue
        cleaned, error = _clean_query(value, label="控件选择器")
        if error:
            return None, error
        normalized[key] = cleaned
    control_type = normalized.get("control_type")
    if control_type and control_type not in allowed_types:
        return None, "该控件类型不在允许的动作范围内"
    process_id = selector.get("process_id")
    if process_id not in (None, "", 0):
        try:
            normalized["process_id"] = int(process_id)
        except (TypeError, ValueError):
            return None, "控件进程标识无效"
    return normalized, None


def _uia_root(hwnd: int, *, expected_process_id: int = 0):
    if not _uia_available():
        return None, "uiautomation 依赖未安装，暂时不能执行控件动作"
    user32, _kernel32 = _api()
    if user32 is None:
        return None, "电脑控制仅支持 Windows"
    try:
        user32.IsWindow.argtypes = [ctypes.c_void_p]
        user32.IsWindow.restype = wintypes.BOOL
        user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
        user32.IsWindowVisible.restype = wintypes.BOOL
        handle = ctypes.c_void_p(int(hwnd))
        if not user32.IsWindow(handle) or not user32.IsWindowVisible(handle):
            return None, "目标窗口已关闭或不可见"
        if expected_process_id and _window_pid(user32, int(hwnd)) != int(expected_process_id):
            return None, "目标窗口所属进程已变化"
        import uiautomation as auto

        root = auto.ControlFromHandle(int(hwnd))
        if root is None:
            return None, "UI Automation 无法获取目标窗口"
        return root, None
    except Exception as exc:  # noqa: BLE001
        return None, f"读取目标窗口控件失败: {exc}"


def _walk_controls(root, *, max_depth: int = MAX_ACTION_UI_DEPTH,
                   max_nodes: int = MAX_ACTION_UI_NODES) -> list[tuple[object, int]]:
    rows: list[tuple[object, int]] = []

    def walk(control, depth: int) -> None:
        if len(rows) >= max_nodes:
            return
        rows.append((control, depth))
        if depth >= max_depth:
            return
        try:
            children = control.GetChildren()
        except Exception:  # noqa: BLE001
            return
        for child in children or []:
            walk(child, depth + 1)
            if len(rows) >= max_nodes:
                break

    walk(root, 0)
    return rows


def _interactable_error(control, record: dict) -> str | None:
    if not bool(_control_value(control, "IsEnabled", True)):
        return "目标控件当前不可用"
    if bool(_control_value(control, "IsOffscreen", False)):
        return "目标控件当前不在可见区域"
    bounds = record.get("bounds")
    if bounds and (bounds["right"] <= bounds["left"] or bounds["bottom"] <= bounds["top"]):
        return "目标控件没有可交互的可见区域"
    return None


def _control_alias_profile(value: object) -> tuple[str, dict] | None:
    lookup = normalize_window_text(value)
    if not lookup:
        return None
    for alias_id, profile in CONTROL_ALIASES.items():
        if any(normalize_window_text(item) == lookup
               for item in profile.get("aliases", ())):
            return alias_id, profile
    return None


def _window_application_alias(window: dict) -> str | None:
    """从已经解析的窗口元数据推断应用别名，只用于查找静态 recipe。"""
    process_name = normalize_window_text(_process_basename(window.get("process_name")))
    class_name = normalize_window_text(window.get("class_name"))
    title = str(window.get("title") or "")
    for alias_id, profile in APPLICATION_ALIASES.items():
        if process_name and process_name in {
            normalize_window_text(item) for item in profile.get("process_names", ())
        }:
            return alias_id
        if class_name and class_name in {
            normalize_window_text(item) for item in profile.get("class_names", ())
        }:
            return alias_id
        if any(_title_has_app_suffix(title, token)
               for token in profile.get("title_tokens", ())):
            return alias_id
    return None


def _get_pattern(control, method_name: str):
    getter = getattr(control, method_name, None)
    if not callable(getter):
        return None
    try:
        return getter()
    except Exception:  # noqa: BLE001
        return None


def _control_capabilities(control) -> dict:
    """探测当前 Control 的 UIA 能力；不读取或记录控件正文。"""
    value_pattern = _get_pattern(control, "GetValuePattern")
    readonly = None
    if value_pattern is not None:
        raw_readonly = _control_value(value_pattern, "IsReadOnly", None)
        readonly = None if raw_readonly is None else bool(raw_readonly)
    return {
        "invoke": _get_pattern(control, "GetInvokePattern") is not None,
        "selection": _get_pattern(control, "GetSelectionItemPattern") is not None,
        "expand": _get_pattern(control, "GetExpandCollapsePattern") is not None,
        "value_pattern": value_pattern is not None,
        "value_read_only": readonly,
        "text_writable": value_pattern is not None and readonly is False,
    }


def _control_candidate(selector: dict, control, record: dict,
                       allowed_types: frozenset[str], action_type: str = "") -> dict | None:
    if record.get("control_type") not in allowed_types:
        return None
    for key in ("control_type", "automation_id", "class_name"):
        expected = selector.get(key)
        if expected is not None and normalize_window_text(record.get(key)) != normalize_window_text(expected):
            return None
    expected_pid = selector.get("process_id")
    if expected_pid is not None and int(record.get("process_id") or 0) != expected_pid:
        return None

    raw_target = str(selector.get("raw_target") or selector.get("name") or "").strip()
    query_normalized = normalize_window_text(raw_target)
    name_normalized = normalize_window_text(record.get("name"))
    alias_id = str(selector.get("semantic_alias") or "")
    alias = CONTROL_ALIASES.get(alias_id)
    if alias is None:
        resolved = _control_alias_profile(raw_target)
        if resolved:
            alias_id, alias = resolved
    scores: list[tuple[int, str]] = []
    if query_normalized and name_normalized == query_normalized:
        scores.append((1000, "exact_normalized_name"))
    if alias:
        alias_names = {
            normalize_window_text(item) for item in alias.get("aliases", ())
        }
        alias_types = set(alias.get("control_types", ()))
        alias_classes = {
            normalize_window_text(item) for item in alias.get("class_names", ())
        }
        alias_automation_ids = {
            normalize_window_text(item) for item in alias.get("automation_ids", ())
        }
        if name_normalized in alias_names:
            scores.append((950, f"semantic_alias:{alias_id}:name"))
        if normalize_window_text(record.get("automation_id")) in alias_automation_ids:
            scores.append((940, f"semantic_alias:{alias_id}:automation_id"))
        if normalize_window_text(record.get("class_name")) in alias_classes:
            scores.append((930, f"semantic_alias:{alias_id}:class_name"))
        # ControlType 只能作为身份锚点之后的辅助证据，不能单独把同类型
        # 控件变成候选；否则“保存”会把菜单中的任意 MenuItemControl 都纳入。
        if scores and record.get("control_type") in alias_types:
            scores.append((max(score for score, _reason in scores) + 10,
                           f"semantic_alias:{alias_id}:control_type"))
    if not scores:
        return None

    capabilities = _control_capabilities(control)
    if action_type == "input":
        if capabilities["text_writable"]:
            scores.append((max(score for score, _reason in scores) + 25,
                           "capability:text_input"))
    elif action_type == "click":
        if capabilities["invoke"] or capabilities["selection"]:
            scores.append((max(score for score, _reason in scores) + 20,
                           "capability:click"))
    elif action_type == "expand" and capabilities["expand"]:
        scores.append((max(score for score, _reason in scores) + 20,
                       "capability:expand"))
    score, reason = max(scores, key=lambda item: item[0])
    return {
        "control": control,
        "record": record,
        "match_score": score,
        "match_reason": reason,
        "semantic_alias": alias_id or None,
        "capabilities": capabilities,
    }


def _control_candidate_summary(candidate: dict) -> dict:
    record = candidate.get("record") or {}
    return {
        "name": str(record.get("name") or ""),
        "control_type": str(record.get("control_type") or ""),
        "class_name": str(record.get("class_name") or ""),
        "automation_id": str(record.get("automation_id") or ""),
        "process_id": _safe_int(record.get("process_id")),
        "match_reason": candidate.get("match_reason", ""),
        "match_score": candidate.get("match_score", 0),
    }


def _collect_control_candidates(root, selector: dict,
                                allowed_types: frozenset[str],
                                action_type: str = "") -> list[dict]:
    candidates: list[dict] = []
    for control, depth in _walk_controls(root):
        record = _control_record(control, depth)
        candidate = _control_candidate(selector, control, record, allowed_types, action_type)
        if candidate is not None:
            candidates.append(candidate)
    candidates.sort(key=lambda item: item["match_score"], reverse=True)
    return candidates


def _select_control_candidate(candidates: list[dict], *, action_type: str = "") -> dict:
    if not candidates:
        return _error("CONTROL_NOT_FOUND", "找不到目标控件", control_candidates=[])

    # 先筛掉存在但不具备当前动作能力的候选；若全部不具备能力，返回明确原因。
    if action_type == "input":
        capable = [item for item in candidates if item["capabilities"].get("text_writable")]
        if capable:
            candidates = capable
        elif any(item["capabilities"].get("value_read_only") is True for item in candidates):
            return _error(
                "CONTROL_NOT_WRITABLE", "目标控件存在，但当前不可写",
                control_candidates=[_control_candidate_summary(item) for item in candidates],
            )
        else:
            return _error(
                "UIA_PATTERN_UNAVAILABLE", "目标控件没有可用的 UIA 文本写入 Pattern",
                control_candidates=[_control_candidate_summary(item) for item in candidates],
            )
    elif action_type == "click":
        capable = [item for item in candidates if (
            item["capabilities"].get("invoke")
            or item["capabilities"].get("selection")
        )]
        if capable:
            candidates = capable
        else:
            return _error(
                "UIA_PATTERN_UNAVAILABLE", "目标控件没有可用的 UIA 点击 Pattern",
                control_candidates=[_control_candidate_summary(item) for item in candidates],
            )
    elif action_type == "expand":
        capable = [item for item in candidates if item["capabilities"].get("expand")]
        if capable:
            candidates = capable
        else:
            return _error(
                "UIA_PATTERN_UNAVAILABLE", "目标控件没有可用的 UIA 展开 Pattern",
                control_candidates=[_control_candidate_summary(item) for item in candidates],
            )

    best = candidates[0]
    if best["match_score"] < SAFE_CONTROL_SCORE:
        return _error(
            "CONTROL_NOT_FOUND", "没有达到安全阈值的目标控件",
            control_candidates=[_control_candidate_summary(item) for item in candidates],
        )
    if len(candidates) > 1 and best["match_score"] - candidates[1]["match_score"] < CONTROL_AMBIGUITY_MARGIN:
        return _error(
            "CONTROL_AMBIGUOUS", "目标控件无法安全确定唯一结果",
            control_candidates=[_control_candidate_summary(item) for item in candidates],
        )
    return {
        "ok": True,
        "available": True,
        **best,
        "control_candidates": [_control_candidate_summary(item) for item in candidates],
    }


def _target_descriptor(raw_target: str, alias_id: str | None) -> dict:
    return {
        "target_type": "DirectControlTarget",
        "raw_target": raw_target,
        "semantic_alias": alias_id,
    }


def _resolve_control_object(window: dict, selector: dict,
                            allowed_types: frozenset[str],
                            *, action_type: str = "") -> dict:
    if not isinstance(window, dict):
        return _error("WINDOW_NOT_FOUND", "目标窗口无效", available=False)
    try:
        hwnd = int(window.get("hwnd") or 0)
    except (TypeError, ValueError):
        hwnd = 0
    if not hwnd:
        return _error("WINDOW_NOT_FOUND", "目标窗口句柄无效")
    normalized, error = _normalize_selector(selector, allowed_types)
    if error:
        return _error("CONTROL_NOT_FOUND", error)
    root, error = _uia_root(hwnd, expected_process_id=int(window.get("process_id") or 0))
    if error:
        if not _uia_available():
            code = "UIA_UNAVAILABLE"
        elif "窗口" in error or "进程" in error:
            code = "WINDOW_NOT_FOUND"
        else:
            code = "EXECUTION_FAILED"
        return _error(code, error, available=_uia_available())

    selector = {**normalized, **{
        key: selector[key] for key in ("raw_target", "semantic_alias") if key in selector
    }}
    candidates = _collect_control_candidates(root, selector, allowed_types, action_type)
    selected = _select_control_candidate(candidates, action_type=action_type)
    if not selected.get("ok"):
        if selected.get("error_code") == "CONTROL_NOT_FOUND" and not candidates:
            selected["error"] = "找不到唯一匹配的目标控件"
        return selected
    control = selected["control"]
    record = selected["record"]
    error = _interactable_error(control, record)
    if error:
        return _error("EXECUTION_FAILED", error)
    return {
        "ok": True,
        "available": True,
        "target_type": "DirectControlTarget",
        "window": window,
        "control": record,
        "selector": _selector_from_record(record),
        "control_target": _target_descriptor(
            str(selector.get("raw_target") or selector.get("name") or ""),
            selected.get("semantic_alias"),
        ),
        "match_reason": selected["match_reason"],
        "match_score": selected["match_score"],
        "control_candidates": selected.get("control_candidates", []),
        "capabilities": selected["capabilities"],
        "_control": control,
    }


def _public_control_result(result: dict) -> dict:
    return {key: value for key, value in result.items() if key != "_control"}


def _resolve_known_control_path(window: dict, control_name: str,
                                action_type: str, application_alias: str | None) -> dict:
    alias = _control_alias_profile(control_name)
    if not alias:
        return _error("CONTROL_NOT_FOUND", "找不到目标控件")
    alias_id, _profile = alias
    if action_type != "click":
        return _error("CONTROL_NOT_FOUND", "当前动作没有可用的安全 ControlPath")
    app_id = application_alias or _window_application_alias(window)
    recipe = (CONTROL_PATH_RECIPES.get(app_id or "", {}).get(alias_id))
    if not recipe:
        return _error("CONTROL_NOT_FOUND", "当前应用没有可用的安全 ControlPath")
    first_step = recipe["steps"][0]
    trigger = _resolve_control_object(
        window, {**first_step["selector"], "semantic_alias": first_step.get("semantic_alias")},
        CLICK_CONTROL_TYPES, action_type="expand",
    )
    if not trigger.get("ok"):
        return trigger
    public_steps = [
        {**step, "selector": dict(step.get("selector") or {})}
        for step in recipe["steps"]
    ]
    return {
        "ok": True,
        "available": True,
        "target_type": "ControlPathTarget",
        "control_target": {
            "target_type": "ControlPathTarget",
            "recipe_id": recipe["recipe_id"],
            "application_alias": app_id,
            "semantic_alias": alias_id,
            "action_type": action_type,
            "steps": public_steps,
            "verify": dict(recipe.get("verify") or {}),
        },
        "control_path": public_steps,
        "application_alias": app_id,
        "control": {
            "name": control_name,
            "control_type": "ControlPathTarget",
            "process_id": int(window.get("process_id") or 0),
        },
        "selector": {"name": control_name},
        "prepare": {"trigger_control": trigger.get("control")},
        "control_candidates": trigger.get("control_candidates", []),
        "match_reason": trigger.get("match_reason"),
        "match_score": trigger.get("match_score"),
    }


def _prepare_control_target(window_query: str | None, control_name: str,
                            allowed_types: frozenset[str], *, action_type: str = "") -> dict:
    target_window = resolve_window(window_query)
    if not target_window.get("ok"):
        return target_window
    window = target_window.get("window") or {}
    direct = _resolve_control_object(
        window, {"name": control_name, "raw_target": control_name},
        allowed_types, action_type=action_type,
    )
    prepared = direct
    if (not direct.get("ok") and direct.get("error_code") == "CONTROL_NOT_FOUND"
            and action_type == "click"):
        prepared = _resolve_known_control_path(
            window, control_name, action_type,
            (target_window.get("resolution") or {}).get("resolved_alias"),
        )
    result = _public_control_result(prepared)
    if target_window.get("resolution"):
        result["resolution"] = target_window["resolution"]
    return result


def prepare_click_target(window_query: str | None, control_name: str) -> dict:
    """规划阶段解析 DirectControlTarget 或受限 ControlPathTarget，不执行动作。"""
    return _prepare_control_target(
        window_query, control_name, CLICK_CONTROL_TYPES, action_type="click",
    )


def prepare_text_target(window_query: str | None, control_name: str) -> dict:
    """规划阶段解析可写 Edit/Document 控件；不写入任何文本。"""
    result = _prepare_control_target(
        window_query, control_name, TEXT_CONTROL_TYPES, action_type="input",
    )
    if result.get("ok") and bool((result.get("control") or {}).get("is_password")):
        return _error("PERMISSION_DENIED", "不允许向密码输入框写入文本")
    return result


def _resolve_execution_control(window: dict, target: dict,
                               allowed_types: frozenset[str],
                               action_type: str) -> dict:
    """执行时按语义目标重新解析；不复用 Prepare 阶段的 UIA 对象。"""
    if isinstance(target, dict) and target.get("target_type") == "DirectControlTarget":
        selector = {
            "name": target.get("raw_target") or "",
            "raw_target": target.get("raw_target") or "",
            "semantic_alias": target.get("semantic_alias"),
        }
    else:
        selector = target
    return _resolve_control_object(
        window, selector, allowed_types, action_type=action_type,
    )


def _invoke_control(control) -> dict:
    """只使用 UIA Invoke/Selection Pattern，不使用坐标、鼠标或按键模拟。"""
    try:
        pattern = control.GetInvokePattern()
    except Exception:  # noqa: BLE001
        pattern = None
    if pattern is not None:
        try:
            result = pattern.Invoke()
            if result is False:
                return _error("EXECUTION_FAILED", "UI Automation InvokePattern 执行失败")
            return {"ok": True, "method": "InvokePattern"}
        except Exception as exc:  # noqa: BLE001
            return _error("EXECUTION_FAILED", f"UI Automation InvokePattern 执行失败: {exc}")

    try:
        pattern = control.GetSelectionItemPattern()
    except Exception:  # noqa: BLE001
        pattern = None
    if pattern is not None:
        try:
            result = pattern.Select()
            if result is False:
                return _error("EXECUTION_FAILED", "UI Automation SelectionItemPattern 执行失败")
            return {"ok": True, "method": "SelectionItemPattern"}
        except Exception as exc:  # noqa: BLE001
            return _error("EXECUTION_FAILED", f"UI Automation SelectionItemPattern 执行失败: {exc}")

    return _error("UIA_PATTERN_UNAVAILABLE", "目标控件没有可用的 UIA Invoke/Selection Pattern")


def _verify_click(window: dict, selector: dict, method: str) -> dict:
    """重新读取控件并验证 UIA 执行模式/选择状态，而不是只看异常。"""
    observed = _resolve_execution_control(window, selector, CLICK_CONTROL_TYPES, "click")
    if not observed.get("ok"):
        return _error(
            "VERIFICATION_FAILED",
            "控件动作执行后无法重新定位目标控件",
            verification={"control_present": False, "method": method},
        )
    control = observed.get("_control")
    verification = {"control_present": True, "method": method}
    if method == "SelectionItemPattern":
        try:
            pattern = control.GetSelectionItemPattern()
            selected = bool(pattern.IsSelected) if pattern is not None else False
        except Exception:
            selected = False
        verification["selected"] = selected
        if not selected:
            return _error("VERIFICATION_FAILED", "选择动作执行后目标控件未处于选中状态",
                          verification=verification)
    return {"ok": True, "verified": True, "verification": verification,
            "window": observed.get("window"), "control": observed.get("control"),
            "selector": observed.get("selector"), "_control": control}


def click_control(window: dict, selector: dict) -> dict:
    """执行阶段按选择器重新定位后调用 UIA，并验证结果。"""
    result = _resolve_execution_control(window, selector, CLICK_CONTROL_TYPES, "click")
    if not result.get("ok"):
        return _public_control_result(result)
    invoked = _invoke_control(result["_control"])
    if not invoked.get("ok"):
        return _public_control_result(invoked)
    verified = _verify_click(result["window"], result["selector"], invoked["method"])
    return _public_control_result(verified)


def _read_value_pattern(pattern) -> tuple[bool, str]:
    try:
        value = getattr(pattern, "Value")
        if callable(value):
            value = value()
        return True, "" if value is None else str(value)
    except Exception:  # noqa: BLE001
        return False, ""


def set_control_value(window: dict, selector: dict, value: str) -> dict:
    """执行阶段通过 UIA ValuePattern 写入文本，绝不模拟按键或剪贴板。"""
    result = _resolve_execution_control(window, selector, TEXT_CONTROL_TYPES, "input")
    if not result.get("ok"):
        return _public_control_result(result)
    control = result["_control"]
    if bool(_control_value(control, "IsPassword", False)):
        return _error("PERMISSION_DENIED", "不允许向密码输入框写入文本")
    try:
        pattern = control.GetValuePattern()
        if pattern is None:
            return _error("UIA_PATTERN_UNAVAILABLE", "目标控件没有可用的 UIA 文本写入 Pattern")
        readonly = _control_value(pattern, "IsReadOnly", None)
        if readonly is True:
            return _error("CONTROL_NOT_WRITABLE", "目标控件存在，但当前不可写")
        if readonly is not False:
            return _error("UIA_PATTERN_UNAVAILABLE", "无法确认目标控件的 UIA 写入状态")
        changed = pattern.SetValue(value)
        if changed is False:
            return _error("EXECUTION_FAILED", "UI Automation ValuePattern 写入失败")
        readable, observed_value = _read_value_pattern(pattern)
        if not readable or observed_value != value:
            return _error(
                "VERIFICATION_FAILED",
                "输入动作执行后未能验证目标控件的实际值",
                verification={"value_readable": readable, "value_length": len(observed_value)},
            )
        result["verified"] = True
        result["verification"] = {"value_readable": True, "value_length": len(observed_value)}
        return _public_control_result(result)
    except Exception as exc:  # noqa: BLE001
        return _error("EXECUTION_FAILED", f"写入目标输入框失败: {exc}")


def _desktop_root():
    if not _uia_available():
        return None, "uiautomation 依赖未安装，暂时不能执行动态控件动作"
    try:
        import uiautomation as auto

        root = auto.GetRootControl()
        if root is None:
            return None, "UI Automation 无法获取 Desktop Root"
        return root, None
    except Exception as exc:  # noqa: BLE001
        return None, f"读取 Desktop Root 失败: {exc}"


def _control_identity(record: dict) -> tuple:
    native_handle = int(record.get("native_window_handle") or 0)
    if native_handle:
        return ("hwnd", native_handle)
    bounds = record.get("bounds") or {}
    return (
        "metadata",
        int(record.get("process_id") or 0),
        normalize_window_text(record.get("class_name")),
        normalize_window_text(record.get("automation_id")),
        normalize_window_text(record.get("name")),
        bounds.get("left"), bounds.get("top"), bounds.get("right"), bounds.get("bottom"),
    )


def _is_popup_record(record: dict) -> bool:
    return (
        record.get("control_type") == "MenuControl"
        or normalize_window_text(record.get("class_name")) == "menuflyout"
    )


def _scan_desktop_popups(selector: dict | None = None) -> dict:
    root, error = _desktop_root()
    if error:
        return _error("UIA_UNAVAILABLE", error, available=_uia_available())
    selector = selector or {
        "name": "保存",
        "semantic_alias": "save",
        "control_type": "MenuItemControl",
    }
    popups: list[dict] = []
    for popup, depth in _walk_controls(root, max_depth=6, max_nodes=MAX_ACTION_UI_NODES):
        record = _control_record(popup, depth)
        if not _is_popup_record(record):
            continue
        if not bool(record.get("is_enabled", True)) or bool(record.get("is_offscreen", False)):
            continue
        matches = _collect_control_candidates(
            popup, selector, CLICK_CONTROL_TYPES, action_type="click",
        )
        popups.append({
            "control": popup,
            "record": record,
            "identity": _control_identity(record),
            "menu_candidates": matches,
        })
    return {"ok": True, "available": True, "popups": popups}


def _wait_for_menu_popup(before: dict, *, target_process_id: int,
                         selector: dict, timeout: float = POPUP_TIMEOUT_SECONDS) -> dict:
    trace = current_execution_trace()
    before_ids = {
        popup["identity"] for popup in (before.get("popups") or [])
    }
    deadline = time.monotonic() + timeout
    saw_new_popup = False
    if trace is not None:
        trace.emit(
            "POPUP_POLL_START",
            popup_baseline_count=len(before.get("popups") or []),
            timeout_ms=int(timeout * 1000),
        )
    while time.monotonic() <= deadline:
        current = _scan_desktop_popups(selector)
        if not current.get("ok"):
            return current
        new_popups = [
            popup for popup in current.get("popups", [])
            if popup["identity"] not in before_ids
        ]
        if new_popups:
            saw_new_popup = True
            if trace is not None:
                trace.emit(
                    "POPUP_CANDIDATE_FOUND",
                    popup_current_count=len(current.get("popups") or []),
                    popup_candidate_count=len(new_popups),
                    candidate=control_metadata(
                        (new_popups[0].get("record") if new_popups else None),
                    ),
                    candidate_reason="new_popup",
                )
            process_matches = [
                popup for popup in new_popups
                if target_process_id and int(popup["record"].get("process_id") or 0) == target_process_id
            ]
            if target_process_id:
                if process_matches:
                    scoped = process_matches
                elif len(new_popups) == 1 and not int(new_popups[0]["record"].get("process_id") or 0):
                    # 个别 UIA Popup 不暴露 ProcessId；唯一且刚出现的 Popup
                    # 可以保守接受，否则不能把其他进程的 Popup 绑定给目标窗口。
                    scoped = new_popups
                else:
                    return _error("CONTROL_AMBIGUOUS", "新 MenuFlyout 无法关联目标窗口进程")
            else:
                scoped = new_popups
            if len(scoped) > 1:
                return _error("CONTROL_AMBIGUOUS", "检测到多个无法安全关联的 MenuFlyout")
            popup = scoped[0]
            candidates = popup.get("menu_candidates") or []
            if len(candidates) > 1:
                if trace is not None:
                    trace.emit("SAVE_ITEM_RESOLVED", save_item_found=False,
                               error_code="CONTROL_AMBIGUOUS")
                return _error("CONTROL_AMBIGUOUS", "目标 MenuFlyout 中存在多个匹配的保存项")
            if len(candidates) == 1:
                candidate = candidates[0]
                if trace is not None:
                    trace.emit(
                        "SAVE_ITEM_RESOLVED", save_item_found=True,
                        selected_popup=True,
                        candidate=control_metadata(
                            candidate.get("record"),
                            candidate_reason=candidate.get("match_reason"),
                        ),
                        candidate_reason=candidate.get("match_reason"),
                    )
                return {"ok": True, "available": True, "popup": popup,
                        "menu": candidate}
        time.sleep(POPUP_POLL_INTERVAL_SECONDS)
    if saw_new_popup:
        if trace is not None:
            trace.emit("SAVE_ITEM_RESOLVED", save_item_found=False,
                       error_code="CONTROL_NOT_FOUND")
        return _error("CONTROL_NOT_FOUND", "MenuFlyout 已出现，但找不到唯一的保存项")
    if trace is not None:
        trace.emit("SAVE_ITEM_RESOLVED", save_item_found=False,
                   error_code="ACTION_TIMEOUT")
    return _error("ACTION_TIMEOUT", "等待 MenuFlyout 出现超时")


def _document_modified_state(window: dict) -> bool | None:
    """只读取可选的 dirty 状态；读取不到时返回 None，不做推断。"""
    try:
        hwnd = int(window.get("hwnd") or 0)
    except (TypeError, ValueError):
        return None
    root, error = _uia_root(hwnd, expected_process_id=int(window.get("process_id") or 0))
    if error:
        return None
    for control, _depth in _walk_controls(root):
        record = _control_record(control, 0)
        if record.get("control_type") not in INPUT_CONTROL_TYPES:
            continue
        for name in ("IsModified", "IsDirty"):
            value = _control_value(control, name, None)
            if isinstance(value, bool):
                return value
    return None


def _find_save_as_dialog(*, target_process_id: int) -> dict:
    root, error = _desktop_root()
    if error:
        return _error("UIA_UNAVAILABLE", error, available=_uia_available())
    title_tokens = ("另存为", "saveas", "save as")
    matches: list[dict] = []
    for control, depth in _walk_controls(root, max_depth=3, max_nodes=MAX_ACTION_UI_NODES):
        record = _control_record(control, depth)
        if record.get("control_type") != "WindowControl":
            continue
        process_id = int(record.get("process_id") or 0)
        if target_process_id and process_id and process_id != target_process_id:
            continue
        title = normalize_window_text(record.get("name"))
        if any(normalize_window_text(token) in title for token in title_tokens):
            matches.append({"control": control, "record": record})
    if len(matches) > 1:
        return _error("VERIFICATION_FAILED", "检测到多个无法区分的另存为对话框")
    if matches:
        return {"ok": True, "available": True, "dialog": matches[0]}
    return {"ok": True, "available": True, "dialog": None}


def _verify_save_command(window: dict, before_modified: bool | None,
                         *, target_process_id: int) -> dict:
    deadline = time.monotonic() + POPUP_TIMEOUT_SECONDS
    while time.monotonic() <= deadline:
        dialog = _find_save_as_dialog(target_process_id=target_process_id)
        if not dialog.get("ok"):
            return dialog
        if dialog.get("dialog"):
            return {
                "ok": True,
                "verified": True,
                "execution_state": "ACTION_EXECUTED",
                "business_goal_completed": False,
                "save_state": "SAVE_AS_REQUIRED",
                "verification": {"save_as_dialog": True},
            }
        if before_modified is True and _document_modified_state(window) is False:
            return {
                "ok": True,
                "verified": True,
                "execution_state": "ACTION_EXECUTED",
                "business_goal_completed": True,
                "save_state": "CLEAN",
                "verification": {"dirty_to_clean": True},
            }
        time.sleep(POPUP_POLL_INTERVAL_SECONDS)
    return _error(
        "VERIFICATION_FAILED",
        "保存命令已调用，但未观察到另存为对话框或明确保存状态变化",
    )


def execute_control_path(window: dict, target: dict) -> dict:
    """逐步执行受限 ControlPath；每个动态步骤都在使用前重新扫描。"""
    trace = current_execution_trace()
    if not isinstance(target, dict) or target.get("target_type") != "ControlPathTarget":
        return _error("CONTROL_NOT_FOUND", "ControlPathTarget 无效")
    recipe_id = target.get("recipe_id")
    recipe = None
    recipe_app_id = None
    for app_id, app_recipes in CONTROL_PATH_RECIPES.items():
        for candidate in app_recipes.values():
            if candidate.get("recipe_id") == recipe_id:
                recipe = candidate
                recipe_app_id = app_id
                break
        if recipe:
            break
    if (not recipe or not recipe_app_id
            or target.get("application_alias") != recipe_app_id):
        return _error("CONTROL_NOT_FOUND", "ControlPath recipe 不存在或不可用")
    steps = recipe.get("steps") or ()
    if not steps:
        return _error("EXECUTION_FAILED", "ControlPath recipe 不符合安全执行约束")
    before_popups = None
    before_modified = None
    menu = None
    popup = None
    invoked = None
    for step in steps:
        operation = step.get("operation")
        scope = step.get("scope")
        selector = step.get("selector") or {}
        if scope == "window":
            resolved = _resolve_control_object(
                window, {**selector, "semantic_alias": step.get("semantic_alias")},
                CLICK_CONTROL_TYPES,
                action_type="expand" if operation == "expand" else "click",
            )
            if not resolved.get("ok"):
                return resolved
            if operation == "expand":
                if trace is not None:
                    trace.emit(
                        "FILE_CONTROL_RESOLVED",
                        control=control_metadata(resolved.get("control")),
                        control_target=step.get("target") or "文件",
                    )
                before_popups = _scan_desktop_popups(selector)
                if not before_popups.get("ok"):
                    return before_popups
                if trace is not None:
                    trace.emit(
                        "POPUP_BASELINE_CAPTURED",
                        popup_baseline_count=len(before_popups.get("popups") or []),
                    )
                before_modified = _document_modified_state(window)
                expanded = _get_pattern(resolved["_control"], "GetExpandCollapsePattern")
                if expanded is None:
                    return _error("UIA_PATTERN_UNAVAILABLE", "目标控件没有可用的 ExpandCollapsePattern")
                try:
                    control_trace = control_metadata(resolved.get("control"))
                    expand_state = _control_value(expanded, "ExpandCollapseState", None)
                    if expand_state is not None:
                        control_trace["expand_collapse_state"] = str(expand_state)[:240]
                    if trace is not None:
                        trace.emit(
                            "BEFORE_EXPAND",
                            control=control_trace,
                        )
                    changed = expanded.Expand()
                    if trace is not None:
                        trace.emit("EXPAND_RETURNED", changed=changed)
                    if changed is False:
                        return _error("EXECUTION_FAILED", "ControlPath Expand 执行失败")
                except Exception as exc:  # noqa: BLE001
                    return _error("EXECUTION_FAILED", f"ControlPath Expand 执行失败: {exc}")
            elif operation == "invoke":
                invoked = _invoke_control(resolved["_control"])
                if not invoked.get("ok"):
                    return invoked
            else:
                return _error("EXECUTION_FAILED", "ControlPath 包含不允许的窗口操作")
        elif scope == "desktop_popup" and operation == "invoke":
            if before_popups is None:
                return _error("EXECUTION_FAILED", "动态 Popup 缺少前置 Expand 步骤")
            popup = _wait_for_menu_popup(
                before_popups,
                target_process_id=int(window.get("process_id") or 0),
                selector=selector,
            )
            if not popup.get("ok"):
                return popup
            menu = popup["menu"]
            invoked = _invoke_control(menu["control"])
            if trace is not None:
                trace.emit(
                    "SAVE_INVOKED", ok=bool(invoked.get("ok")),
                    invoke_method=invoked.get("method"),
                    selected_control=control_metadata(
                        menu.get("record"),
                    ),
                )
            if not invoked.get("ok"):
                return invoked
        else:
            return _error("EXECUTION_FAILED", "ControlPath 包含不允许的动态控件操作")

    if menu is None or invoked is None:
        return _error("EXECUTION_FAILED", "ControlPath 没有完成目标控件操作")
    verify_spec = recipe.get("verify") or {}
    if verify_spec.get("kind") == "save_command":
        if trace is not None:
            trace.emit("VERIFY_START", control_target="保存")
        verified = _verify_save_command(
            window, before_modified,
            target_process_id=int(window.get("process_id") or 0),
        )
        if not verified.get("ok"):
            return verified
    else:
        return _error("EXECUTION_FAILED", "ControlPath 缺少明确的 Verify 规则")
    return {
        **verified,
        "control": menu["record"],
        "control_candidates": [
            _control_candidate_summary(item)
            for item in (popup.get("menu_candidates") or [])
        ],
        "match_reason": menu.get("match_reason"),
        "match_score": menu.get("match_score"),
        "verification": {
            **(verified.get("verification") or {}),
            "invoke_method": invoked.get("method"),
            "popup_class_name": (popup["popup"]["record"].get("class_name")
                                  if popup else ""),
        },
    }


def status() -> dict:
    """供 HUD 轮询的电脑桥状态。"""
    current = active_window()
    return {
        "ok": current.get("ok", False),
        "platform": "windows" if IS_WINDOWS else os.name,
        "available": IS_WINDOWS,
        "active_window": current.get("window"),
        "ui_automation": {
            "available": _uia_available(),
            "package": UIA_PACKAGE,
        },
        "error": current.get("error"),
    }


def capabilities() -> list[dict]:
    """返回能力层可用性元数据，不包含任何写操作。"""
    state = status()
    return [
        {
            "id": "computer.active_window",
            "title": "读取活动窗口",
            "description": "读取前台窗口标题、进程名、窗口类名和边界。",
            "access": "read",
            "risk": "low",
            "available": bool(state.get("available")),
            "requires_confirmation": False,
            "reversible": True,
        },
        {
            "id": "computer.windows",
            "title": "枚举可见窗口",
            "description": "列出当前桌面可见的顶层窗口。",
            "access": "read",
            "risk": "low",
            "available": bool(state.get("available")),
            "requires_confirmation": False,
            "reversible": True,
        },
        {
            "id": "computer.ui_tree",
            "title": "读取 UI 控件树",
            "description": "读取活动窗口的 UI Automation 控件元数据，用于后续可靠定位。",
            "access": "read",
            "risk": "low",
            "available": bool(state.get("ui_automation", {}).get("available")),
            "requires_confirmation": False,
            "reversible": True,
        },
    ]
