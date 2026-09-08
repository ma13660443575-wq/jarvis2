# -*- coding: utf-8 -*-
"""
hands/wf05.py — 应用建议
========================
挑一个最近的概念卡（C-），生成 A- 应用卡草稿：如何把它用进真实场景。
"""
from __future__ import annotations

from .. import core
from ..brain import llm
from ..engine import contract
from ..vault import paths, reader


class WF05(contract.WorkflowBase):
    name = "wf05"
    title = "应用建议"
    desc = "基于概念卡生成可落地的应用建议卡。"
    interactive = True

    def precheck(self, payload: dict) -> dict:
        n = self._pick()
        if n is None:
            return {"ok": False, "error": "还没有可用的概念卡（C-）。"}
        return {"ok": True}

    def _pick(self):
        cs = reader.search_notes(prefix="C", folder="05_Frameworks", limit=100)
        return cs[0] if cs else None

    def execute(self, payload: dict) -> dict:
        c = self._pick()
        today = core.today()
        if llm.is_enabled():
            resp = llm.chat_once([
                {"role": "system", "content": "你擅长把抽象概念转化成可执行的应用建议。输出简练的要点式中文。"},
                {"role": "user", "content": f"概念：{c.title}\n正文：{c.body[:800]}\n\n"
                                            "请给 3 条具体、可落地的应用建议（每条含场景、动作、预期效果）。"},
            ], temperature=0.5, max_tokens=800)
            suggestion = resp.strip()
        else:
            body = next((l.strip() for l in c.body.splitlines() if l.strip() and not l.startswith("#")), "")
            suggestion = f"1. 场景：日常工作/项目  ·  动作：把「{c.title}」的方法套用到当前任务  ·  预期：提升效率。\n（Mock 模式，填入 DeepSeek key 可获得更具体建议）"

        a_name = "A-%s-%s" % (today.replace("-", ""), core.slugify(c.title, 24))
        a_rel = paths.rel("A", a_name)
        a_content = (
            f"---\ntype: 应用\nstatus: 计划中\nsource: \"{c.wikilink}\"\ncreated: {today}\ntags: []\n---\n\n"
            f"# {a_name}\n\n> 基于 [[{c.title}]] 的应用计划\n\n## 应用建议\n\n{suggestion}\n\n"
            f"## 状态追踪\n- [ ] 尝试应用\n- [ ] 验证效果\n- [ ] 沉淀复盘\n"
        )
        return {"drafts": [{"rel_path": a_rel, "content": a_content, "action": "create"}],
                "updates": [], "note": f"为 {c.title} 生成应用卡"}

    def verify(self, payload: dict, result: dict) -> dict:
        return {"ok": True}

    def build_report(self, payload: dict, result: dict) -> str:
        return f"应用建议：已为 {result.get('note','')} 生成 1 张应用卡草稿。"


def register():
    from ..engine import registry
    registry.register(WF05())
