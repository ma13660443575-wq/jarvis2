# -*- coding: utf-8 -*-
"""
computer/actions.py — 受确认保护的本地电脑动作
=================================================
动作层只接受由 Broker 生成的窄类型计划，并使用短时、一次性凭证执行。

当前允许的动作：
* 在默认浏览器中打开已校验的 HTTP(S) URL；
* 激活一个当前可见、唯一匹配的窗口；
* 点击一个由 UI Automation 唯一定位的允许控件；
* 通过 UIA ValuePattern 向非密码 EditControl 写入短文本。

明确不支持：Shell/PowerShell、任意程序路径、热键/按键模拟、剪贴板、坐标点击、
鼠标移动、密码输入框，以及未确认的后台执行。
"""
from __future__ import annotations

import logging
import re
import threading
import time
import uuid
import webbrowser
from urllib.parse import urlsplit

from ..heart import notify
from . import windows
from .trace import (
    ExecutionTrace,
    active_execution_trace,
    execution_trace_context,
    new_execution_trace,
    window_metadata,
    control_metadata,
)


MAX_URL_LENGTH = 2048
MAX_TEXT_INPUT_LENGTH = 500
ACTION_TTL_SECONDS = 120.0
COMPUTER_ACTION_RESULT_TYPE = "computer_action_result"
LOGGER = logging.getLogger(__name__)
_URL_RE = re.compile(r"https?://[^\s<>\u3000\"'“”‘’]+", re.IGNORECASE)
_TRAILING_PUNCTUATION = ".,!?;:)]}>，。！？；：、）》】」』"
_QUOTED_RE = re.compile(r'「([^」]+)」|“([^”]+)”|"([^"]+)"|\'([^\']+)\'')
_OPEN_VERBS = (
    "打开", "访问", "进入", "前往", "跳转到", "浏览",
    "open", "visit", "go to", "navigate to",
)
_ACTIVATE_VERBS = ("激活", "切换到", "切换至", "聚焦", "focus")
_CLICK_VERBS = ("点击", "单击", "点一下", "按下", "选择", "click")
_TEXT_VERBS = ("输入", "填写", "填入", "type")
_QUESTION_MARKERS = ("怎么", "如何", "为什么", "是什么", "能不能", "可以吗", "how to", "what is", "why ")

_ACTION_CAPABILITIES = {
    "open_url": "computer.open_url",
    "activate_window": "computer.activate_window",
    "click_control": "computer.click_control",
    "set_text": "computer.set_text",
}
_CONFIRMATION_REASONS = {
    "open_url": "该操作会在本机默认浏览器中打开指定网页，执行前需要你的确认。",
    "activate_window": "该操作会将指定本机窗口切到前台，执行前需要你的确认。",
    "click_control": "该操作会点击指定本机控件，可能触发应用内行为，执行前需要你的确认。",
    "set_text": "该操作会向指定非密码输入框写入文本，执行前需要你的确认。",
}
_CONFIRMATION_LABELS = {
    "open_url": "确认打开",
    "activate_window": "确认切换",
    "click_control": "确认点击",
    "set_text": "确认输入",
}

_LOCK = threading.RLock()
_EXECUTION_LOCK = threading.Lock()
_PENDING: dict[str, dict] = {}


def _action_error(result: dict | None, fallback_code: str, fallback_message: str) -> dict:
    """把底层错误保留在 Computer Action 链内，不转成普通对话。"""
    result = result or {}
    return {
        "ok": False,
        "status": "failed",
        "error_code": result.get("error_code", fallback_code),
        "error": result.get("error", fallback_message),
        **({"resolution": result["resolution"]} if result.get("resolution") else {}),
    }


def _debug_meta(result: dict | None = None, *, raw_target: str = "") -> dict:
    resolution = (result or {}).get("resolution") or {}
    return {
        "raw_target": raw_target,
        "normalized_target": resolution.get("normalized_target", ""),
        "resolved_alias": resolution.get("resolved_alias"),
        "enumerated_window_count": resolution.get("enumerated_window_count", 0),
        "selected_window": resolution.get("selected_window"),
        "control_candidates": (result or {}).get("control_candidates", []),
        "selected_control": (result or {}).get("control")
        or ((result or {}).get("prepare") or {}).get("trigger_control"),
        "control_match_reason": (result or {}).get("match_reason"),
        "control_match_score": (result or {}).get("match_score"),
    }


def _log_action_phase(item: dict, phase: str, result: dict | None = None,
                      *, error_code: str | None = None) -> None:
    """动作日志只含目标元数据和阶段状态，不含命令正文或输入值。"""
    result = result or {}
    debug = item.get("debug") or {}
    resolution = result.get("resolution") or {}
    selected = result.get("window") or debug.get("selected_window")
    selected_control = result.get("control") or debug.get("selected_control")
    prepare_result = result.get("prepare_result")
    execute_result = result.get("execute_result")
    verify_result = result.get("verify_result")
    if phase in {"prepare", "confirm"} and prepare_result is None:
        prepare_result = result.get("ok")
    if phase == "execute" and execute_result is None:
        execute_result = result.get("ok")
    if phase == "verify" and verify_result is None:
        verify_result = result.get("verified")
    LOGGER.info(
        "desktop_action phase=%s action_type=%s raw_target=%r "
        "normalized_target=%r resolved_alias=%r enumerated_window_count=%s "
        "selected_window=%s prepare_result=%s execute_result=%s "
        "verify_result=%s control_candidates=%s selected_control=%s "
        "control_match_reason=%s control_match_score=%s error_code=%s",
        phase, item.get("action"), debug.get("raw_target", ""),
        resolution.get("normalized_target", debug.get("normalized_target", "")),
        resolution.get("resolved_alias", debug.get("resolved_alias")),
        resolution.get("enumerated_window_count", debug.get("enumerated_window_count", 0)),
        selected, prepare_result, execute_result, verify_result,
        result.get("control_candidates", debug.get("control_candidates", [])),
        selected_control, result.get("match_reason", debug.get("control_match_reason")),
        result.get("match_score", debug.get("control_match_score")),
        error_code or result.get("error_code"),
    )


def normalize_url(value: str) -> tuple[str | None, str | None]:
    """校验 URL，只允许无用户凭据的 HTTP(S) 地址。"""
    raw = (value or "").strip().rstrip(_TRAILING_PUNCTUATION)
    if not raw:
        return None, "URL 不能为空"
    if len(raw) > MAX_URL_LENGTH:
        return None, f"URL 过长（最多 {MAX_URL_LENGTH} 个字符）"
    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in raw):
        return None, "URL 不能包含空白或控制字符"
    try:
        parts = urlsplit(raw)
        scheme = parts.scheme.lower()
        hostname = parts.hostname
        _ = parts.port
    except ValueError:
        return None, "URL 格式无效"
    if scheme not in {"http", "https"}:
        return None, "只允许打开 http 或 https URL"
    if not parts.netloc or not hostname:
        return None, "URL 缺少有效主机名"
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        return None, "URL 不允许包含用户名或密码"
    return raw, None


def extract_url(text: str) -> str | None:
    """从自然语言中提取第一个合法 HTTP(S) URL。"""
    for match in _URL_RE.finditer(text or ""):
        normalized, _error = normalize_url(match.group(0))
        if normalized:
            return normalized
    return None


def is_open_url_request(text: str) -> bool:
    """判断输入是否明确要求打开网页，避免普通 URL 问题被当作动作。"""
    if not extract_url(text):
        return False
    lowered = (text or "").strip().casefold()
    return any(verb in lowered for verb in _OPEN_VERBS)


def _quoted_values(text: str) -> list[str]:
    values: list[str] = []
    for match in _QUOTED_RE.finditer(text or ""):
        value = next((part for part in match.groups() if part is not None), "").strip()
        if value:
            values.append(value)
    return values


def _contains_any(text: str, verbs: tuple[str, ...]) -> bool:
    return any(verb in text for verb in verbs)


def parse_computer_request(text: str) -> dict | None:
    """从严格、可读的指令格式中解析电脑动作，解析本身绝不触发系统操作。

    支持示例：
    * 打开 https://example.com
    * 激活「记事本」
    * 在「记事本」中点击「保存」
    * 在「记事本」中向「编辑器」输入「今天完成了测试」

    不带引号的控件/窗口名称不会被猜测成系统动作，以避免语音识别误触。
    """
    source = (text or "").strip()
    if not source:
        return None
    # 命令台也能直接执行计划，因此不能只依赖对话层的“提问”判断。
    # 碰到明显问句时宁可交给普通问答，也不把它当作桌面动作。
    if _contains_any(source.casefold(), _QUESTION_MARKERS):
        return None
    if is_open_url_request(source):
        url = extract_url(source)
        return {"action": "open_url", "url": url} if url else None

    lowered = source.casefold()
    quoted = _quoted_values(source)
    if _contains_any(lowered, _TEXT_VERBS):
        # 两段引号表示当前窗口的“控件名 + 文本”；三段表示“窗口名 + 控件名 + 文本”。
        if len(quoted) == 2:
            return {
                "action": "set_text",
                "window_query": "",
                "control_name": quoted[0],
                "value": quoted[1],
            }
        if len(quoted) == 3:
            return {
                "action": "set_text",
                "window_query": quoted[0],
                "control_name": quoted[1],
                "value": quoted[2],
            }
        return None
    if _contains_any(lowered, _CLICK_VERBS):
        if len(quoted) == 1:
            return {
                "action": "click_control",
                "window_query": "",
                "control_name": quoted[0],
            }
        if len(quoted) == 2:
            return {
                "action": "click_control",
                "window_query": quoted[0],
                "control_name": quoted[1],
            }
        return None
    if _contains_any(lowered, _ACTIVATE_VERBS) and len(quoted) == 1:
        return {"action": "activate_window", "window_query": quoted[0]}
    return None


def action_capability_id(action: str) -> str:
    return _ACTION_CAPABILITIES.get(action or "", "")


def is_computer_action_plan(plan: dict | None) -> bool:
    if not isinstance(plan, dict):
        return False
    action = str(plan.get("action") or "")
    return bool(action_capability_id(action)) and plan.get("capability_id") == action_capability_id(action)


def capability_available(capability_id: str) -> bool:
    """返回真实运行时可用性，能力目录据此避免展示假功能。"""
    if capability_id == "computer.open_url":
        return True
    if capability_id == "computer.activate_window":
        return bool(windows.IS_WINDOWS)
    if capability_id in {"computer.click_control", "computer.set_text"}:
        return windows.ui_automation_available()
    return False


def action_target(request: dict) -> str:
    """生成可展示的目标摘要；输入文本本身绝不回显到动作计划。"""
    action = request.get("action")
    if action == "open_url":
        return str(request.get("url") or "")
    window = str(request.get("window_query") or "").strip()
    window_label = f"窗口「{window}」" if window else "当前活动窗口"
    if action == "activate_window":
        return window_label
    control = str(request.get("control_name") or "").strip()
    if action == "click_control":
        return f"{window_label} · 控件「{control}」"
    if action == "set_text":
        value = str(request.get("value") or "")
        return f"{window_label} · 输入框「{control}」· 写入 {len(value)} 个字符"
    return ""


def confirmation_reason(action: str) -> str:
    return _CONFIRMATION_REASONS.get(action or "", "该本机操作需要你的确认。")


def confirmation_label(action: str) -> str:
    return _CONFIRMATION_LABELS.get(action or "", "确认执行")


def _normalize_text_value(value: object) -> tuple[str | None, str | None]:
    if not isinstance(value, str):
        return None, "输入内容必须是文本"
    if not value:
        return None, "输入内容不能为空"
    if len(value) > MAX_TEXT_INPUT_LENGTH:
        return None, f"输入内容过长（最多 {MAX_TEXT_INPUT_LENGTH} 个字符）"
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        return None, "输入内容不能包含换行、制表符或其他控制字符"
    return value, None


def _purge_expired(now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    expired = [
        action_id for action_id, item in _PENDING.items()
        if item.get("expires_mono", 0.0) <= now and item.get("status") == "pending"
    ]
    for action_id in expired:
        _PENDING.pop(action_id, None)


def _audit(status: str, *, action: str, detail: str = "", action_id: str = "") -> None:
    """动作审计失败不能影响用户看到的动作结果；不记录输入文本。"""
    try:
        from ..db import evals as evals_db

        evals_db.record_eval(
            "computer",
            ok=status == "completed",
            detail=detail,
            meta={"action": action, "action_id": action_id, "status": status},
        )
    except Exception:  # noqa: BLE001
        pass


def _public_pending(item: dict) -> dict:
    result = {
        "ok": True,
        "action_id": item["action_id"],
        "action": item["action"],
        "target": item["target"],
        "expires_at": item["expires_at"],
        "status": "waiting_confirmation",
        "confirmation_label": confirmation_label(item["action"]),
    }
    if item["action"] == "open_url":
        result["url"] = item["payload"]["url"]
    return result


def _create_pending(action: str, payload: dict, *, target: str,
                    requested_plan_id: str | None = None, debug: dict | None = None,
                    trace: ExecutionTrace | None = None) -> dict:
    now_wall = time.time()
    now_mono = time.monotonic()
    action_id = f"computer-action-{uuid.uuid4().hex[:16]}"
    item = {
        "action_id": action_id,
        "action": action,
        "payload": dict(payload),
        "target": target,
        "requested_plan_id": requested_plan_id or "",
        "created_at": now_wall,
        "expires_at": now_wall + ACTION_TTL_SECONDS,
        "expires_mono": now_mono + ACTION_TTL_SECONDS,
        "status": "pending",
        "debug": dict(debug or {}),
        "trace": trace,
    }
    with _LOCK:
        _purge_expired(now_mono)
        _PENDING[action_id] = item
    if trace is not None:
        trace.bind(action_id=action_id, action_type=action)
        trace.emit("CONFIRMATION_CREATED")
    return _public_pending(item)


def create_open_url_plan(url: str, requested_plan_id: str | None = None, *,
                         trace: ExecutionTrace | None = None) -> dict:
    """创建打开网页的一次性凭证；此函数不会打开浏览器。"""
    if trace is None:
        trace = new_execution_trace({"source": "standalone_action", "action_type": "open_url"})
        trace.emit("PREPARE_START", action_type="open_url")
    normalized, error = normalize_url(url)
    if not normalized:
        trace.emit("RESULT", status="failed", ok=False, error_code="EXECUTION_FAILED")
        return _action_error(None, "EXECUTION_FAILED", error or "URL 无效")
    # URL 本身不写入动作日志，避免把 query/path 中可能存在的敏感信息带入日志。
    debug = {
        "raw_target": "<http-url>",
        "normalized_target": "<http-url>",
        "resolved_alias": None,
        "enumerated_window_count": 0,
        "selected_window": None,
    }
    pending = _create_pending(
        "open_url", {"url": normalized}, target=normalized,
        requested_plan_id=requested_plan_id, debug=debug, trace=trace,
    )
    _log_action_phase({"action": "open_url", "debug": debug}, "prepare", {"ok": True})
    _log_action_phase(
        {"action": "open_url", "debug": debug}, "confirm", {"ok": True},
    )
    return pending


def _window_target(window: dict) -> str:
    title = str((window or {}).get("title") or "").strip()
    return f"窗口「{title}」" if title else "已定位窗口"


def _control_target(window: dict, control: dict, *, action: str, value_length: int = 0) -> str:
    base = _window_target(window)
    control_type = str((control or {}).get("control_type") or "控件")
    control_name = str((control or {}).get("name") or "").strip()
    if control_type == "ControlPathTarget":
        return f"{base} · ControlPath「文件 → {control_name}」"
    if action == "set_text":
        return f"{base} · {control_type}「{control_name}」· 写入 {value_length} 个字符"
    return f"{base} · {control_type}「{control_name}」"


def create_pending_action(plan: dict, text: str) -> dict:
    """按 Broker 计划登记动作，并在登记前以只读方式二次定位目标。

    文本不会放进 plan 或审计记录；对于输入动作，仅保存在短时内存凭证的 payload 中。
    """
    request = parse_computer_request(text)
    if not request:
        return _action_error(
            None, "EXECUTION_FAILED", "电脑动作格式无效，请使用带引号的窗口和控件名称",
        )
    action = request.get("action")
    expected_capability = action_capability_id(action)
    if not expected_capability or not isinstance(plan, dict):
        return _action_error(None, "EXECUTION_FAILED", "电脑动作计划无效")
    if plan.get("action") != action or plan.get("capability_id") != expected_capability:
        return _action_error(None, "EXECUTION_FAILED", "电脑动作计划与当前指令不一致")
    requested_plan_id = plan.get("plan_id")
    trace = new_execution_trace({"action_type": action})
    trace.emit(
        "PREPARE_START",
        action_type=action,
        window_target=request.get("window_query") or "",
        control_target=request.get("control_name") or "",
    )

    if action == "open_url":
        return create_open_url_plan(
            request.get("url") or "", requested_plan_id=requested_plan_id, trace=trace,
        )
    if action == "activate_window":
        resolved = windows.resolve_window(request.get("window_query"))
        trace.emit("WINDOW_RESOLVED", window=window_metadata(resolved.get("window")))
        debug = _debug_meta(resolved, raw_target=request.get("window_query", ""))
        _log_action_phase({"action": action, "debug": debug}, "prepare", resolved)
        if not resolved.get("ok"):
            trace.emit("RESULT", status="failed", ok=False,
                       error_code=resolved.get("error_code") or "WINDOW_NOT_FOUND")
            return _action_error(resolved, "WINDOW_NOT_FOUND", "无法定位目标窗口")
        window = resolved.get("window") or {}
        pending = _create_pending(
            action, {"window_query": request.get("window_query") or ""},
            target=_window_target(window), requested_plan_id=requested_plan_id,
            debug=debug, trace=trace,
        )
        _log_action_phase(pending | {"action": action, "debug": debug}, "confirm", resolved)
        return pending
    if action == "click_control":
        prepared = windows.prepare_click_target(
            request.get("window_query") or None, request.get("control_name") or "",
        )
        trace.emit("WINDOW_RESOLVED", window=window_metadata(prepared.get("window")))
        trace.emit(
            "CONTROL_PATH_SELECTED",
            control_target=request.get("control_name") or "",
            selected_control=control_metadata(prepared.get("control")),
        )
        debug = _debug_meta(prepared, raw_target=request.get("window_query", ""))
        _log_action_phase({"action": action, "debug": debug}, "prepare", prepared)
        if not prepared.get("ok"):
            trace.emit("RESULT", status="failed", ok=False,
                       error_code=prepared.get("error_code") or "CONTROL_NOT_FOUND")
            return _action_error(prepared, "CONTROL_NOT_FOUND", "无法定位目标控件")
        window = prepared.get("window") or {}
        control = prepared.get("control") or {}
        pending = _create_pending(
            action,
            {"window_query": request.get("window_query") or "",
             "selector": prepared.get("selector") or {},
             "control_target": prepared.get("control_target") or {}},
            target=_control_target(window, control, action=action),
            requested_plan_id=requested_plan_id, debug=debug, trace=trace,
        )
        _log_action_phase(pending | {"action": action, "debug": debug}, "confirm", prepared)
        return pending
    if action == "set_text":
        value, error = _normalize_text_value(request.get("value"))
        if error:
            return _action_error(None, "EXECUTION_FAILED", error)
        prepared = windows.prepare_text_target(
            request.get("window_query") or None, request.get("control_name") or "",
        )
        trace.emit("WINDOW_RESOLVED", window=window_metadata(prepared.get("window")))
        trace.emit(
            "CONTROL_PATH_SELECTED",
            control_target=request.get("control_name") or "",
            selected_control=control_metadata(prepared.get("control")),
        )
        debug = _debug_meta(prepared, raw_target=request.get("window_query", ""))
        _log_action_phase({"action": action, "debug": debug}, "prepare", prepared)
        if not prepared.get("ok"):
            trace.emit("RESULT", status="failed", ok=False,
                       error_code=prepared.get("error_code") or "CONTROL_NOT_FOUND")
            return _action_error(prepared, "CONTROL_NOT_FOUND", "无法定位目标输入框")
        window = prepared.get("window") or {}
        control = prepared.get("control") or {}
        pending = _create_pending(
            action,
            {"window_query": request.get("window_query") or "",
             "selector": prepared.get("selector") or {},
             "control_target": prepared.get("control_target") or {},
             "value": value},
            target=_control_target(window, control, action=action, value_length=len(value)),
            requested_plan_id=requested_plan_id, debug=debug, trace=trace,
        )
        _log_action_phase(pending | {"action": action, "debug": debug}, "confirm", prepared)
        return pending
    return _action_error(None, "EXECUTION_FAILED", "不支持的电脑动作")


def _execute_pending(item: dict) -> dict:
    action = item.get("action")
    payload = item.get("payload") or {}
    trace = item.get("trace")
    if action == "open_url":
        try:
            opened = bool(webbrowser.open(payload["url"], new=2, autoraise=True))
        except Exception as exc:  # noqa: BLE001
            outcome = {"ok": False, "error_code": "EXECUTION_FAILED",
                       "error": f"调用默认浏览器失败: {exc}"}
            _log_action_phase(item, "execute", outcome)
            return outcome
        outcome = {
            "ok": opened, "verified": opened,
            "error_code": "" if opened else "EXECUTION_FAILED",
            "error": "" if opened else "系统没有报告成功打开默认浏览器",
        }
        _log_action_phase(item, "execute", outcome)
        return outcome

    # 确认期间不保存 UIA Control 或旧 hwnd；这里重新枚举并解析窗口。
    window_query = payload.get("window_query") or None
    resolved = windows.resolve_window(window_query)
    if trace is not None:
        trace.emit("WINDOW_RELOCATED", window=window_metadata(resolved.get("window")))
    _log_action_phase(item, "re-locate", resolved)
    if not resolved.get("ok"):
        return _action_error(resolved, "WINDOW_NOT_FOUND", "无法重新定位目标窗口")
    window = resolved.get("window") or {}
    if action == "activate_window":
        outcome = windows.activate_window(
            window.get("hwnd"), expected_process_id=int(window.get("process_id") or 0),
        )
        _log_action_phase(item, "execute", outcome)
        return outcome
    if action == "click_control":
        control_target = payload.get("control_target") or payload.get("selector") or {}
        if control_target.get("target_type") == "ControlPathTarget":
            if trace is not None:
                with active_execution_trace(trace):
                    outcome = windows.execute_control_path(window, control_target)
            else:
                outcome = windows.execute_control_path(window, control_target)
        else:
            outcome = windows.click_control(window, control_target)
        _log_action_phase(item, "execute", outcome)
        return outcome
    if action == "set_text":
        outcome = windows.set_control_value(
            window, payload.get("control_target") or payload.get("selector") or {},
            payload.get("value") or "",
        )
        _log_action_phase(item, "execute", outcome)
        return outcome
    return {"ok": False, "error_code": "EXECUTION_FAILED", "error": "不支持的电脑动作"}


def _public_result(item: dict, *, ok: bool, status: str, error: str = "") -> dict:
    result = {
        "ok": ok,
        "action": item.get("action"),
        "action_id": item.get("action_id"),
        "status": status,
        "target": item.get("target", ""),
    }
    if item.get("action") == "open_url":
        result["url"] = (item.get("payload") or {}).get("url", "")
    if item.get("error_code"):
        result["error_code"] = item["error_code"]
    if item.get("verification") is not None:
        result["verification"] = item["verification"]
    if error:
        result["error"] = error
    return result


def _terminal_result(*, action: str = "", action_id: str = "", ok: bool,
                     status: str, error_code: str = "", error: str = "",
                     verified: bool = False, **extra) -> dict:
    """创建 Computer Action 的统一 terminal result，不进入普通对话链。"""
    result = {
        "ok": ok,
        "type": COMPUTER_ACTION_RESULT_TYPE,
        "computer_action_owned": True,
        "terminal": True,
        "action": action,
        "action_id": action_id,
        "status": status,
        "verified": verified,
    }
    if error_code:
        result["error_code"] = error_code
    if error:
        result["error"] = error
    result.update(extra)
    return result


def build_terminal_failure_result(plan: dict, failure: dict) -> dict:
    """把 Prepare 阶段失败转换为统一 terminal result。"""
    return _terminal_result(
        action=str((plan or {}).get("action") or ""),
        ok=False,
        status="failed",
        error_code=str((failure or {}).get("error_code") or "EXECUTION_FAILED"),
        error=str((failure or {}).get("error") or "电脑动作准备失败"),
        verified=False,
    )


def confirm_action(action_id: str) -> dict:
    """确认并执行短时一次性电脑动作；同一动作永远只能执行一次。"""
    key = (action_id or "").strip()
    with _LOCK:
        _purge_expired()
        item = _PENDING.get(key)
        if not item:
            return _terminal_result(
                ok=False, status="failed", error_code="ACTION_TIMEOUT",
                error="动作不存在或已过期",
            )
        if item.get("status") != "pending":
            return _terminal_result(
                action=item.get("action", ""), action_id=key, ok=False,
                status="failed", error_code="EXECUTION_FAILED",
                error="动作已处理，不能重复执行",
            )
        item["status"] = "executing"
        snapshot = {**item, "payload": dict(item.get("payload") or {})}

    trace = snapshot.get("trace")
    if trace is not None:
        trace.emit("CONFIRM_RECEIVED", action_id=key)

    try:
        # UIA 库不适合作为任意并发执行器；串行化也避免两个确认动作抢前台窗口。
        with _EXECUTION_LOCK:
            if trace is not None:
                trace.emit("EXECUTE_START", action_id=key)
            outcome = _execute_pending(snapshot)
    except Exception as exc:  # noqa: BLE001
        outcome = {"ok": False, "error_code": "EXECUTION_FAILED",
                   "error": f"执行本机动作失败: {exc}"}

    verified = bool(outcome.get("verified"))
    ok = bool(outcome.get("ok")) and verified
    if bool(outcome.get("ok")) and not verified:
        outcome = {
            **outcome,
            "ok": False,
            "error_code": "VERIFICATION_FAILED",
            "error": "动作执行后未通过结果验证",
        }
    status = "completed" if ok else "failed"
    error = "" if ok else str(outcome.get("error") or "电脑动作执行失败")
    final_code = "" if ok else str(outcome.get("error_code") or "EXECUTION_FAILED")
    with _LOCK:
        _PENDING.pop(key, None)
    _log_action_phase(snapshot, "verify", outcome, error_code=final_code or None)
    _audit(status, action=snapshot["action"], detail=error, action_id=key)
    result = _terminal_result(
        action=snapshot.get("action", ""), action_id=key, ok=ok,
        status="success" if ok else "failed", error_code=final_code,
        error=error, verified=verified,
        target=snapshot.get("target", ""),
    )
    if trace is not None:
        trace.emit(
            "RESULT", action_id=key, status=result.get("status"), ok=ok,
            verified=verified, error_code=final_code or None,
        )
    if snapshot.get("action") == "open_url":
        result["url"] = (snapshot.get("payload") or {}).get("url", "")
    if final_code:
        result["error_code"] = final_code
    if outcome.get("verification") is not None:
        result["verification"] = outcome["verification"]
    for key in ("execution_state", "business_goal_completed", "save_state"):
        if key in outcome:
            result[key] = outcome[key]
    try:
        notify.publish("computer", {
            "type": COMPUTER_ACTION_RESULT_TYPE,
            "computer_action_owned": True,
            "terminal": True,
            "action": snapshot["action"],
            "status": result["status"],
            "action_id": key,
            "verified": verified,
        })
    except Exception:  # noqa: BLE001
        pass
    return result


def reject_action(action_id: str) -> dict:
    """拒绝并销毁一次待确认动作；执行中的动作不能被并发撤销。"""
    key = (action_id or "").strip()
    with _LOCK:
        _purge_expired()
        item = _PENDING.get(key)
        if item and item.get("status") != "pending":
            return _terminal_result(
                action=item.get("action", ""), action_id=key, ok=False,
                status="failed", error_code="EXECUTION_FAILED",
                error="动作正在处理，不能再拒绝",
            )
        if item:
            _PENDING.pop(key, None)
    if not item:
        return _terminal_result(
            action_id=key, ok=False, status="failed", error_code="ACTION_TIMEOUT",
            error="动作不存在或已过期",
        )
    trace = item.get("trace")
    if trace is not None:
        trace.emit("RESULT", action_id=key, status="cancelled", ok=True, verified=False)
    _audit("rejected", action=item["action"], detail="用户拒绝动作", action_id=key)
    try:
        notify.publish("computer", {
            "action": item["action"], "status": "rejected", "action_id": key,
        })
    except Exception:  # noqa: BLE001
        pass
    return _terminal_result(
        action=item.get("action", ""), action_id=key, ok=True,
        status="cancelled", verified=False, target=item.get("target", ""),
    )


# 保留第一阶段公开函数，避免现有调用方和测试失效；路由改用通用入口。
def confirm_open_url(action_id: str) -> dict:
    return confirm_action(action_id)


def reject_open_url(action_id: str) -> dict:
    return reject_action(action_id)


def _reset_for_tests() -> None:
    """测试专用：清空内存动作，不供业务路由调用。"""
    with _LOCK:
        _PENDING.clear()
