# -*- coding: utf-8 -*-
"""
skills/registry.py — 技能注册与匹配
====================================
- register_all()：注册「技能化」工作流 wf08 + 扫描 06_Capability 注册全部技能。
- refresh()：重扫 06_Capability，把每张 S-卡注册成 skill:<name> 工作流 + 同步 DB。
- match_skill(text)：触发词命中 → 返回技能；供命令台 parse_command 调用。
- 技能与内置工作流同处一个注册表，可被命令台统一执行。
"""
from __future__ import annotations

import json
import threading

from .. import core, db
from ..engine import registry as engine_registry
from . import generate, parser as skill_parser, runner

_lock = threading.Lock()
_skills: dict[str, skill_parser.Skill] = {}   # slug -> Skill
_by_trigger: list[tuple[str, str]] = []       # [(trigger_phrase, slug)]

# 技能名常见的动词/类别后缀，匹配时剥掉以命中用户自然说法（如「会议纪要沉淀」→「会议纪要」）
_SKILL_SUFFIXES = ["沉淀", "生成", "整理", "提取", "分析", "方法", "技巧",
                   "指南", "手册", "模板", "清单", "流程", "复盘", "设计", "法"]


def _name_keywords(name: str) -> list[str]:
    """技能名的可匹配关键词：全名 + 剥掉动词后缀的词干。"""
    kws = [name]
    stem = name
    for suf in sorted(_SKILL_SUFFIXES, key=len, reverse=True):
        if stem.endswith(suf) and len(stem) - len(suf) >= 2:
            stem = stem[: -len(suf)]
            break
    if stem != name and len(stem) >= 2:
        kws.append(stem)
    return kws


def register_all() -> None:
    """命令台入口：注册 wf08 技能化工作流 + 扫描技能库。"""
    generate.register()
    refresh()


def refresh() -> list[dict]:
    """重扫 06_Capability：注册每个技能为 skill:<slug> 工作流，反注册已删除的，同步 DB。"""
    with _lock:
        found = skill_parser.scan_skills_dir()
        current = {sk.slug for sk in found}
        # 反注册已删除的技能卡
        for slug in list(_skills.keys()):
            if slug not in current:
                engine_registry.unregister(slug)
                _skills.pop(slug, None)
        _by_trigger.clear()
        for sk in found:
            engine_registry.register(runner.SkillWorkflow(sk))
            _skills[sk.slug] = sk
            for trig in sk.trigger:
                if trig:
                    _by_trigger.append((trig, sk.slug))
        _sync_db(found)
        try:
            from ..heart import notify
            notify.publish("skills", {"count": len(found), "skills": [sk.to_dict() for sk in found]})
        except Exception:  # noqa: BLE001
            pass
        return [sk.to_dict() for sk in _skills.values()]


def match_skill(text: str) -> dict | None:
    """技能命中：先精确触发词，再用技能名/词干兜底（覆盖用户自然说法）。"""
    t = (text or "").strip()
    if not t:
        return None
    if _by_trigger:
        for trig, slug in sorted(_by_trigger, key=lambda x: len(x[0]), reverse=True):
            if trig and trig in t:
                sk = _skills.get(slug)
                if sk:
                    return _as_match(slug, sk)
    # 技能名 / 词干兜底（如「会议纪要沉淀」→「会议纪要」）
    for slug, sk in _skills.items():
        for kw in _name_keywords(sk.name):
            if len(kw) >= 2 and kw in t:
                return _as_match(slug, sk)
    return None


def _as_match(slug: str, sk: skill_parser.Skill) -> dict:
    """组装 parse_command 结果：name 必须是注册名（slug），供 registry.get 查找。"""
    d = sk.to_dict()
    d["name"] = slug
    return d


def get_skill(slug: str) -> skill_parser.Skill | None:
    return _skills.get(slug)


def all_skills() -> list[dict]:
    return [sk.to_dict() for sk in _skills.values()]


def _sync_db(found: list[skill_parser.Skill]) -> None:
    con = db.schema.get_conn()
    try:
        now = core.now_iso()
        for sk in found:
            con.execute(
                "INSERT OR REPLACE INTO skills "
                "(rel_path, name, trigger, tools, source_card, status, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (sk.rel_path, sk.name, json.dumps(sk.trigger, ensure_ascii=False),
                 json.dumps(sk.tools, ensure_ascii=False), sk.source_card, sk.status,
                 sk.created or now, sk.updated or now),
            )
        con.commit()
    finally:
        con.close()
