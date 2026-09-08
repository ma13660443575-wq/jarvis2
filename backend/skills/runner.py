# -*- coding: utf-8 -*-
"""
skills/runner.py — 通用技能执行器
=================================
把一张 S-卡包装成一个标准工作流（复用 5 步契约 + 草稿确认）：
- output: report → 非交互，执行完直接返回报告（run 记 committed）。
- output: draft  → 交互，产出文件草稿，用户确认后才落盘。

执行 = 读 S-卡 → 收集输入（payload.input / 关联概念卡）→ LLM 按处理流程
跑一遍 → 输出。无 key 时降级为「读取处理流程 + 输入摘要」的 Mock 报告。
"""
from __future__ import annotations

from .. import core
from ..brain import llm
from ..engine import contract
from ..vault import paths, reader
from . import parser as skill_parser

_BUF = 1200


class SkillWorkflow(contract.WorkflowBase):
    def __init__(self, skill: skill_parser.Skill):
        self.skill = skill
        self.name = skill.slug                      # skill:xxx
        self.title = "技能 · " + skill.name
        self.desc = skill.one_liner or skill.name
        self.interactive = skill.output == "draft"  # 产出文件才需确认

    # ---------- 输入收集 ----------
    def _input(self, payload: dict) -> str:
        parts: list[str] = []
        if payload.get("input"):
            parts.append("本次输入：\n" + str(payload["input"]))
        src = self.skill.source_card or ""
        if src:
            n = reader.find_by_ref(src)
            if n is not None:
                parts.append(f"关联概念卡「{n.title}」：\n{n.body[:_BUF]}")
        return "\n\n".join(parts)

    # ---------- 5 步契约 ----------
    def precheck(self, payload: dict) -> dict:
        if not self.skill.flow:
            return {"ok": False, "error": f"技能「{self.skill.name}」缺少「处理流程」定义。"}
        return {"ok": True}

    def execute(self, payload: dict) -> dict:
        s = self.skill
        context = self._input(payload)
        prompt = (
            f"你在执行一个已沉淀的个人技能「{s.name}」。\n"
            f"技能说明：{s.one_liner}\n"
            f"技能工具：{'、'.join(s.tools) if s.tools else '无（纯思维）'}\n"
            f"## 处理流程\n{s.flow}\n"
            f"## 输出要求\n{s.out_req}\n"
            + ("## 参考输入\n" + context + "\n" if context else "")
            + "请按处理流程逐步执行并给出结果。"
        )
        result = llm.chat_once([{"role": "user", "content": prompt}],
                               temperature=0.5, max_tokens=1500)

        report = f"技能「{s.name}」执行完成\n\n{result}"
        if s.output == "draft":
            today = core.today()
            a_name = "A-%s-%s" % (today.replace("-", ""), core.slugify(s.name, 24))
            a_rel = paths.rel("A", a_name)
            content = (
                f"---\ntype: 应用\nstatus: 计划中\nsource: \"[[{s.rel_path[:-3]}]]\"\n"
                f"created: {today}\ntags: [技能]\n---\n\n"
                f"# {a_name}\n\n> 由技能「{s.name}」产出\n\n## 结果\n\n{result}\n"
            )
            return {"drafts": [{"rel_path": a_rel, "content": content, "action": "create"}],
                    "updates": [], "payload_out": {"report": report}, "note": f"技能「{s.name}」产出应用草稿"}
        return {"drafts": [], "updates": [],
                "payload_out": {"report": report}, "note": f"技能「{s.name}」执行完成"}

    def verify(self, payload: dict, result: dict) -> dict:
        return {"ok": True}

    def build_report(self, payload: dict, result: dict) -> str:
        return result.get("payload_out", {}).get("report", f"技能「{self.skill.name}」执行完成")

    def build_log(self, payload: dict, result: dict) -> str:
        return f"[{self.name}] 技能调用「{self.skill.name}」"
