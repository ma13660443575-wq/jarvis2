# -*- coding: utf-8 -*-
"""
skills/generate.py — 「技能化」工作流 wf08
==========================================
把一条已掌握的概念（C-卡，可选配套 A-应用卡）沉淀成可调用的 S-技能卡：
概念 + 应用 → LLM 抽取 → 06_Capability/S-*.md 草稿 → 用户确认落库 → registry 重扫。

落库后自动刷新技能注册表，命令台即可用触发词调用该技能。
"""
from __future__ import annotations

import json
import re

from .. import core
from ..brain import llm
from ..engine import contract
from ..vault import paths, reader, templates


def _lines(v, fallback: str) -> str:
    """把 str 或 list 统一成多行文本（list → 编号行）。"""
    if isinstance(v, list):
        return "\n".join("%d. %s" % (i + 1, str(x).strip()) for i, x in enumerate(v) if str(x).strip())
    s = str(v or "").strip()
    return s or fallback


def _json_from_llm(text: str) -> dict:
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    cand = m.group(1) if m else text
    try:
        return json.loads(cand)
    except Exception:
        m2 = re.search(r"(\{.*\})", cand, re.DOTALL)
        if m2:
            try:
                return json.loads(m2.group(1))
            except Exception:
                pass
    return {}


class SkillGenWorkflow(contract.WorkflowBase):
    name = "wf08"
    title = "技能化"
    desc = "把已验证的概念/应用沉淀成可调用的技能卡（S-）。"
    interactive = True

    def _pick(self, payload: dict):
        ref = (payload or {}).get("concept")
        if ref:
            n = reader.find_by_ref(ref)
            if n is not None:
                return n
        cs = reader.search_notes(prefix="C", folder="05_Frameworks", limit=50)
        return cs[0] if cs else None

    def _pick_app(self, concept_note):
        """找关联同一概念的应用卡（source 含概念引用）；没有就不带，绝不抓无关卡。"""
        if concept_note:
            refs = [concept_note.wikilink, concept_note.title]
            apps = reader.search_notes(prefix="A", folder="04_Knowledge", limit=50)
            for a in apps:
                src = a.fm.get("source") or ""
                if src and any(r in src for r in refs):
                    return a
        return None

    def precheck(self, payload: dict) -> dict:
        if self._pick(payload) is None:
            return {"ok": False, "error": "还没有可技能化的概念卡（C-）。先用工作流把知识沉淀成概念卡。"}
        return {"ok": True}

    def execute(self, payload: dict) -> dict:
        c = self._pick(payload)
        app = self._pick_app(c)
        today = core.today()
        cname = c.title
        sname = cname.replace("C-", "").strip() or "技能"
        src = c.wikilink
        if app:
            src += " + " + app.wikilink

        if llm.is_enabled():
            prompt = (
                "把下面的概念卡（+可选应用卡）沉淀成一张「个人技能卡」。"
                "只输出 JSON（键：name, trigger, one_liner, input, flow, output, criteria）。\n"
                "要求：name 去掉 C- 前缀；trigger 给 3-5 个『用户自然会说』的触发短语（覆盖含技能名、"
                "含动作的说法，如『整理会议纪要』『会议纪要』『帮我沉淀纪要』）；flow 是 3-6 步可执行步骤"
                "（每步一个短句）；criteria 是 2-3 条评估标准。\n\n"
                f"概念卡：{c.title}\n{c.body[:1200]}\n"
                + (f"\n应用卡：{app.title}\n{app.body[:800]}" if app else "")
            )
            data = _json_from_llm(llm.chat_once([{"role": "user", "content": prompt}],
                                                temperature=0.4, max_tokens=1200))
        else:
            data = {
                "name": sname,
                "trigger": [sname, cname],
                "one_liner": next((l.strip() for l in c.body.splitlines()
                                   if l.strip() and not l.startswith("#")), cname),
                "input": "一句话描述本次要处理的输入/场景。",
                "flow": f"1. 明确目标与输入。\n2. 套用「{cname}」的核心方法分析。\n3. 形成可执行结论。\n4. 对照评估标准检查。",
                "output": "结构化结论 / 行动清单 / 建议。",
                "criteria": "结论可落地；有明确依据；可复用于同类场景。",
            }

        name = str(data.get("name") or sname).replace("S-", "").strip() or "技能"
        triggers = data.get("trigger") or [name, cname]
        if isinstance(triggers, str):
            triggers = [x.strip() for x in re.split(r"[,，]", triggers) if x.strip()]
        safe_name = core.slugify(name, 24) or "技能"
        s_rel = paths.rel("S", "S-" + safe_name)
        content = templates.skill_card(
            "S-" + name, name=name,
            one_liner=str(data.get("one_liner") or name),
            trigger=triggers,
            input_desc=_lines(data.get("input"), "一句话描述本次要处理的输入/场景。"),
            flow=_lines(data.get("flow"), "1. 明确目标。"),
            output=_lines(data.get("output"), "结构化结论 / 行动清单 / 建议。"),
            criteria=_lines(data.get("criteria"), "可落地；有依据；可复用。"),
            links="- " + c.wikilink + (("\n- " + app.wikilink) if app else ""),
            source=src, confidence="中", created=today,
        )
        return {
            "drafts": [{"rel_path": s_rel, "content": content, "action": "create"}],
            "updates": [],
            "payload_out": {"skill_rel": s_rel},
            "note": f"为 {cname} 生成技能卡草稿",
        }

    def verify(self, payload: dict, result: dict) -> dict:
        for d in result.get("drafts", []):
            if not d["rel_path"].startswith("06_Capability/"):
                return {"ok": False, "errors": ["技能卡必须落在 06_Capability/"]}
        return {"ok": True}

    def build_report(self, payload: dict, result: dict) -> str:
        return f"技能化：{result.get('note', '')}，确认后即可在命令台用触发词调用。"

    def commit(self, run_id: int) -> dict:
        """落库后重扫技能注册表，让新技能立即可调用。"""
        r = super().commit(run_id)
        if r.get("ok"):
            try:
                from . import registry
                registry.refresh()
            except Exception:  # noqa: BLE001
                pass
        return r


def register():
    from ..engine import registry
    registry.register(SkillGenWorkflow())
