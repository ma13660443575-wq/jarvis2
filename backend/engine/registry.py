# -*- coding: utf-8 -*-
"""
engine/registry.py — 工作流 + 技能统一注册表
============================================
命令台解析输入 → 先匹配技能 trigger（P3）→ 再匹配内置工作流。
每个工作流一个 name（wf01..wf07），支持管道（如 01→02→03）。
"""
from __future__ import annotations

from . import contract

_WORKFLOWS: dict[str, contract.WorkflowBase] = {}

# 命令触发词 → 工作流 name（供 parse_command 使用）
_COMMANDS: dict[str, str] = {
    "处理采集箱": "wf01",
    "知识提取": "wf02",
    "生成回忆": "wf03",
    "复习会话": "wf04",
    "应用建议": "wf05",
    "复盘迭代": "wf06",
    "系统体检": "wf07",
    "技能化": "wf08",
    "情报扫描": "wf09",
}

# 管道定义：一次执行多个工作流
_PIPELINES: dict[str, list[str]] = {
    "消化积压": ["wf01", "wf02", "wf03"],
    "知识回炉": ["wf02", "wf03"],
    "复习+复盘": ["wf04", "wf06"],
}


def register(wf: contract.WorkflowBase) -> contract.WorkflowBase:
    _WORKFLOWS[wf.name] = wf
    return wf


def unregister(name: str) -> None:
    _WORKFLOWS.pop(name, None)


def get(name: str) -> contract.WorkflowBase | None:
    return _WORKFLOWS.get(name)


def all_workflows() -> list[dict]:
    return [{"name": w.name, "title": w.title, "desc": w.desc, "interactive": w.interactive}
            for w in _WORKFLOWS.values()]


def parse_command(text: str) -> dict:
    """把用户输入解析成 {type: workflow|pipeline, name, params}。"""
    t = (text or "").strip()
    if not t:
        return {"type": "empty"}
    # 管道
    for pipe, steps in _PIPELINES.items():
        if pipe in t:
            return {"type": "pipeline", "name": pipe, "steps": steps, "params": {}}
    # 单工作流
    for cmd, wf_name in _COMMANDS.items():
        if cmd in t:
            return {"type": "workflow", "name": wf_name, "params": {}}
    # 技能 trigger（P3 注入）
    try:
        from ..skills.registry import match_skill
        sk = match_skill(t)
        if sk:
            return {"type": "skill", "name": sk["name"], "skill": sk, "params": {}}
    except Exception:
        pass
    return {"type": "unknown", "text": t}
