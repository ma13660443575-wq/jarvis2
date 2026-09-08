# -*- coding: utf-8 -*-
"""
hands/wf06.py — 复盘迭代
========================
读取最近的日记/项目记录，生成 L- 复盘卡草稿（收获/教训/下一步）。
"""
from __future__ import annotations

from .. import core
from ..brain import llm
from ..engine import contract
from ..vault import paths, reader


class WF06(contract.WorkflowBase):
    name = "wf06"
    title = "复盘迭代"
    desc = "基于近期日记/项目生成复盘卡。"
    interactive = True

    def precheck(self, payload: dict) -> dict:
        src = self._source()
        if src is None:
            return {"ok": False, "error": "没有可复盘的近期日记/项目记录。"}
        return {"ok": True}

    def _source(self):
        # 最近的日记
        journals = reader.search_notes(folder="02_Areas/个人成长", limit=100)
        year_prefix = core.today()[:4] + "-"
        for n in journals:
            if n.filename.startswith(year_prefix) and "日记" in n.rel_path:
                return n
        # 退而求其次：最近的复盘
        for n in reader.search_notes(prefix="L", folder="02_Areas", limit=50):
            return n
        # 最近项目总览
        for n in reader.search_notes(folder="03_Projects", limit=50):
            if n.filename.startswith("00_"):
                return n
        return None

    def execute(self, payload: dict) -> dict:
        src = self._source()
        today = core.today()
        if llm.is_enabled():
            resp = llm.chat_once([
                {"role": "system", "content": "你是复盘教练。把输入整理成简洁的复盘：收获、不足/教训、下一步行动。"},
                {"role": "user", "content": f"素材（{src.wikilink}）：\n{src.body[:2000]}"},
            ], temperature=0.5, max_tokens=900)
            body = resp.strip()
        else:
            body = f"**收获**：来自 {src.wikilink} 的实践记录。\n**不足**：待结合上下文补充。\n**下一步**：持续迭代。\n（Mock 模式）"

        l_name = "L-%s-%s" % (today.replace("-", ""), core.slugify(src.title, 20))
        l_rel = paths.rel("L", l_name)
        l_content = (
            f"---\ntype: 复盘\nsource: \"{src.wikilink}\"\ncreated: {today}\ntags: []\n---\n\n"
            f"# {l_name}\n\n{body}\n"
        )
        return {"drafts": [{"rel_path": l_rel, "content": l_content, "action": "create"}],
                "updates": [], "note": f"基于 {src.filename} 复盘"}

    def verify(self, payload: dict, result: dict) -> dict:
        return {"ok": True}

    def build_report(self, payload: dict, result: dict) -> str:
        return f"复盘迭代：{result.get('note','')}，生成 1 张复盘卡草稿。"


def register():
    from ..engine import registry
    registry.register(WF06())
