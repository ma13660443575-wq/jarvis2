# -*- coding: utf-8 -*-
"""
agent/broker.py — localhost 能力中枢（第一阶段）
================================================
这里只负责“描述能力”和“生成计划”，不直接执行任意系统操作。

执行仍由现有的 senses / engine / vault 写入口完成；这样聊天、命令台和
未来的语音入口可以共享同一套能力元数据，而不会各自再实现一套权限规则。
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, replace
from typing import Any

from ..engine import registry
from ..senses import available as senses_available
from ..computer import actions as computer_actions
from ..computer import windows as computer_windows


@dataclass(frozen=True)
class CapabilitySpec:
    """一个可被 Agent 选择的能力描述。"""

    id: str
    title: str
    description: str
    domain: str
    access: str = "read"                 # read | write | destructive
    risk: str = "low"                    # low | medium | high
    requires_confirmation: bool = False
    reversible: bool = True
    available: bool = True
    executor: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


_STATIC_CAPABILITIES: tuple[CapabilitySpec, ...] = (
    CapabilitySpec(
        id="chat.answer",
        title="知识问答",
        description="结合当前对话和 Obsidian 检索结果回答问题。",
        domain="cognition",
        executor="brain.answer.chat_stream",
    ),
    CapabilitySpec(
        id="memory.retrieve",
        title="检索个人记忆",
        description="从 Obsidian 知识库和向量索引中检索相关记忆。",
        domain="memory",
        executor="brain.search.hybrid_search",
    ),
    CapabilitySpec(
        id="vault.search",
        title="搜索 Obsidian",
        description="按关键词查找 vault 笔记，不修改文件。",
        domain="vault",
        executor="routers.search",
    ),
    CapabilitySpec(
        id="vault.read",
        title="读取 Obsidian 笔记",
        description="读取指定笔记内容，用于回答和预览。",
        domain="vault",
        executor="vault.reader.get_note",
    ),
    CapabilitySpec(
        id="computer.open_url",
        title="打开网页",
        description="在本机默认浏览器中打开一个明确指定的 HTTP(S) URL。",
        domain="computer",
        access="write",
        risk="low",
        requires_confirmation=True,
        reversible=False,
        executor="computer.actions.open_url",
    ),
    CapabilitySpec(
        id="computer.activate_window",
        title="激活窗口",
        description="将一个当前可见、唯一匹配的本机窗口切换到前台。",
        domain="computer",
        access="write",
        risk="medium",
        requires_confirmation=True,
        executor="computer.actions.activate_window",
    ),
    CapabilitySpec(
        id="computer.click_control",
        title="点击本机控件",
        description="按 UI Automation 唯一定位后点击允许类型的本机控件。",
        domain="computer",
        access="write",
        risk="high",
        requires_confirmation=True,
        reversible=False,
        executor="computer.actions.click_control",
    ),
    CapabilitySpec(
        id="computer.set_text",
        title="写入本机输入框",
        description="通过 UI Automation 向唯一匹配的非密码输入框写入短文本。",
        domain="computer",
        access="write",
        risk="high",
        requires_confirmation=True,
        reversible=False,
        executor="computer.actions.set_text",
    ),
)


def _workflow_spec(name: str, title: str, desc: str, interactive: bool) -> CapabilitySpec:
    """由现有工作流元数据生成能力描述。

    interactive 工作流会产生需要用户确认的 vault 草稿；非 interactive 工作流
    仍只能调用现有工作流实现，不代表开放了任意系统写入。
    """
    return CapabilitySpec(
        id=f"workflow:{name}",
        title=title or name,
        description=desc or "已注册工作流",
        domain="workflow",
        access="write" if interactive else "read",
        risk="medium" if interactive else "low",
        requires_confirmation=interactive,
        reversible=interactive,
        executor=f"engine.executor.{name}",
    )


def _sense_specs() -> list[CapabilitySpec]:
    specs: list[CapabilitySpec] = []
    for sense in senses_available():
        name = sense.get("name", "")
        if not name:
            continue
        specs.append(CapabilitySpec(
            id=f"sense:{name}",
            title=f"感官 · {name}",
            description=sense.get("desc", "只读感官"),
            domain="sense",
            executor=f"senses.{name}.fetch",
        ))
    return specs


def list_capabilities() -> list[dict]:
    """返回当前实际注册的能力，不把尚未实现的桌面控制伪装成可用。"""
    specs = [
        replace(spec, available=computer_actions.capability_available(spec.id))
        if spec.domain == "computer" else spec
        for spec in _STATIC_CAPABILITIES
    ] + _sense_specs()
    for item in computer_windows.capabilities():
        specs.append(CapabilitySpec(
            id=item["id"], title=item["title"], description=item["description"],
            domain="computer", access=item.get("access", "read"),
            risk=item.get("risk", "low"),
            requires_confirmation=bool(item.get("requires_confirmation", False)),
            reversible=bool(item.get("reversible", True)),
            available=bool(item.get("available", False)),
            executor=f"computer.windows.{item['id'].split('.', 1)[-1]}",
        ))
    for item in registry.all_workflows():
        specs.append(_workflow_spec(
            item["name"], item.get("title", ""), item.get("desc", ""),
            bool(item.get("interactive", True)),
        ))
    seen: set[str] = set()
    out: list[dict] = []
    for spec in specs:
        if spec.id in seen:
            continue
        seen.add(spec.id)
        out.append(spec.to_dict())
    return out


def _plan_id(text: str, parsed: dict) -> str:
    seed = "|".join([
        (text or "").strip(),
        str(parsed.get("type", "")),
        str(parsed.get("name", "")),
    ])
    digest = hashlib.blake2b(seed.encode("utf-8"), digest_size=8).hexdigest()
    return f"plan-{digest}"


def _step_from_spec(spec: CapabilitySpec) -> dict:
    return {
        "capability_id": spec.id,
        "title": spec.title,
        "description": spec.description,
        "access": spec.access,
        "risk": spec.risk,
        "requires_confirmation": spec.requires_confirmation,
    }


def _plan_base(text: str, parsed: dict, *, mode: str, title: str,
               description: str, steps: list[dict], access: str = "read",
               risk: str = "low", requires_confirmation: bool = False,
               capability_id: str = "", confirmation_reason: str | None = None,
               source: str = "engine.registry", extra: dict | None = None) -> dict:
    out = {
        "plan_id": _plan_id(text, parsed),
        "mode": mode,                         # command | conversation | invalid
        "capability_id": capability_id,
        "title": title,
        "description": description,
        "access": access,
        "risk": risk,
        "requires_confirmation": requires_confirmation,
        "confirmation_reason": (
            "该计划会写入 Obsidian 草稿，提交前需要你的确认。"
            if requires_confirmation else "只读执行，不会修改 Obsidian 文件。"
        ),
        "steps": steps,
        "status": "planned",
        "source": source,
    }
    if confirmation_reason:
        out["confirmation_reason"] = confirmation_reason
    if extra:
        out.update(extra)
    return out


def plan_command(text: str, payload: dict[str, Any] | None = None) -> dict:
    """把一条用户输入转换成前端可展示的结构化计划。

    注意：这个函数不执行工作流、不调用 LLM、不读取 payload 内容；执行仍由
    现有 executor 和草稿确认状态机负责。
    """
    del payload  # 计划阶段不把输入内容回显到审计/前端
    parsed = registry.parse_command(text)
    kind = parsed.get("type")

    if kind == "empty":
        return {
            "plan_id": _plan_id(text, parsed),
            "mode": "invalid",
            "title": "空指令",
            "description": "没有可执行的用户输入。",
            "access": "read",
            "risk": "low",
            "requires_confirmation": False,
            "steps": [],
            "status": "invalid",
            "error": "命令为空",
            "source": "engine.registry",
        }

    # 4A Desktop Task 只在 Agent 层识别；Planner 输出经过 schema 校验的有限
    # TaskPlan，不能降级成普通问答，也不允许生成任意工具调用。
    try:
        from . import task_planner
        if task_planner.is_task_candidate(text):
            try:
                task_plan = task_planner.plan_task(text)
            except task_planner.TaskPlanError as exc:
                return task_planner.invalid_task_plan(exc.code, exc.message)
            if task_plan:
                return task_plan
    except Exception:
        # 仅当输入确实是 4A 候选时才暴露 Planner 错误；普通请求继续走既有链路。
        source = text or ""
        is_task_shape = (
            any(marker in source for marker in ("然后", "并且", "再", "之后", "接着"))
            and any(hint.casefold() in source.casefold()
                    for hint in ("记事本", "notepad", "输入", "填写", "填入", "保存", "点击"))
        )
        if is_task_shape:
            return {
                "plan_id": _plan_id(text, {"type": "desktop_task"}),
                "mode": "invalid",
                "title": "桌面任务规划失败",
                "description": "当前桌面任务无法生成受约束计划。",
                "access": "write",
                "risk": "high",
                "requires_confirmation": True,
                "steps": [],
                "status": "invalid",
                "task_type": "desktop_task",
                "desktop_task_owned": True,
                "error_code": "TASK_PLAN_INVALID",
                "error": "桌面任务计划校验失败",
                "source": "structured_planner",
            }

    # 电脑动作必须命中严格的自然语言格式；这里只生成计划，不登记 action_id，
    # 更不会触碰浏览器、窗口或控件。
    computer_request = computer_actions.parse_computer_request(text)
    if computer_request:
        action = computer_request["action"]
        capability_id = computer_actions.action_capability_id(action)
        spec = next(s for s in _STATIC_CAPABILITIES if s.id == capability_id)
        return _plan_base(
            text, parsed, mode="command", title=spec.title,
            description=spec.description, steps=[_step_from_spec(spec)],
            access=spec.access, risk=spec.risk,
            requires_confirmation=spec.requires_confirmation,
            capability_id=spec.id,
            confirmation_reason=computer_actions.confirmation_reason(action),
            source="computer.actions",
            extra={
                "action": action,
                "computer_action_owned": True,
                "target": computer_actions.action_target(computer_request),
                "available": computer_actions.capability_available(spec.id),
                "confirmation_label": computer_actions.confirmation_label(action),
            },
        )

    if kind == "workflow" or kind == "skill":
        name = parsed.get("name", "")
        wf = registry.get(name)
        if wf is None:
            return {
                "plan_id": _plan_id(text, parsed),
                "mode": "invalid",
                "title": "能力不可用",
                "description": f"工作流 {name} 未注册。",
                "access": "read",
                "risk": "low",
                "requires_confirmation": False,
                "steps": [],
                "status": "invalid",
                "error": f"工作流 {name} 未注册",
                "source": "engine.registry",
            }
        spec = _workflow_spec(name, getattr(wf, "title", ""), getattr(wf, "desc", ""),
                              bool(getattr(wf, "interactive", True)))
        return _plan_base(
            text, parsed, mode="command", title=spec.title,
            description=spec.description, steps=[_step_from_spec(spec)],
            access=spec.access, risk=spec.risk,
            requires_confirmation=spec.requires_confirmation,
            capability_id=spec.id,
        )

    if kind == "pipeline":
        specs: list[CapabilitySpec] = []
        missing: list[str] = []
        for name in parsed.get("steps", []):
            wf = registry.get(name)
            if wf is None:
                missing.append(name)
                continue
            specs.append(_workflow_spec(
                name, getattr(wf, "title", ""), getattr(wf, "desc", ""),
                bool(getattr(wf, "interactive", True)),
            ))
        if missing:
            return {
                "plan_id": _plan_id(text, parsed),
                "mode": "invalid",
                "title": "管道不可用",
                "description": "管道中存在未注册工作流。",
                "access": "read",
                "risk": "low",
                "requires_confirmation": False,
                "steps": [],
                "status": "invalid",
                "error": "管道中工作流未注册: " + ", ".join(missing),
                "source": "engine.registry",
            }
        needs_confirmation = any(s.requires_confirmation for s in specs)
        access = "write" if needs_confirmation else "read"
        return _plan_base(
            text, parsed, mode="command", title=f"管道 · {parsed.get('name', '')}",
            description="按顺序执行多个已注册工作流。",
            steps=[_step_from_spec(s) for s in specs], access=access,
            risk="medium" if needs_confirmation else "low",
            requires_confirmation=needs_confirmation,
            capability_id=f"pipeline:{parsed.get('name', '')}",
        )

    # 未命中命令时仍是一个真实能力：普通知识问答/RAG，不把它误报成系统操作。
    spec = next(s for s in _STATIC_CAPABILITIES if s.id == "chat.answer")
    return _plan_base(
        text, parsed, mode="conversation", title=spec.title,
        description=spec.description, steps=[_step_from_spec(spec)],
        access=spec.access, risk=spec.risk, capability_id=spec.id,
    )


def is_computer_action_owned(plan: dict | None) -> bool:
    """判断请求是否已被 Broker 明确交给 Computer Action terminal flow。"""
    return bool(
        isinstance(plan, dict)
        and plan.get("computer_action_owned") is True
        and computer_actions.is_computer_action_plan(plan)
    )


def is_desktop_task_owned(plan: dict | None) -> bool:
    """判断请求是否由 4A Desktop Task Runtime 负责。"""
    return bool(
        isinstance(plan, dict)
        and plan.get("desktop_task_owned") is True
        and plan.get("task_type") == "desktop_task"
    )


def execution_owner(plan: dict | None) -> str:
    """返回本次请求的唯一执行归属。

    归属标记由 Broker 产生，路由层只按这里的结果分流。Desktop Task
    优先级高于 Computer Action，避免一个计划同时带有两个 owner 标记时
    被重复执行或落入普通 Conversation 链。
    """
    if isinstance(plan, dict) and plan.get("desktop_task_owned") is True:
        return "desktop_task"
    if isinstance(plan, dict) and plan.get("computer_action_owned") is True:
        return "computer_action"
    return "conversation"


def finalize_plan(plan: dict, result: dict | None = None) -> dict:
    """根据现有 executor 结果给计划补上可展示的最终状态。"""
    out = dict(plan or {})
    result = result or {}
    status = result.get("status")
    if not result.get("ok", True):
        out["status"] = "failed"
    elif status in ("draft", "waiting_confirmation"):
        out["status"] = "waiting_confirmation"
    elif status in ("failed", "partial"):
        out["status"] = "failed"
    elif status == "rejected":
        out["status"] = "rejected"
    else:
        out["status"] = "completed"
    if result.get("run_id") is not None:
        out["run_id"] = result["run_id"]
    if result.get("pending_run") and result["pending_run"].get("run_id") is not None:
        out["run_id"] = result["pending_run"]["run_id"]
    return out
