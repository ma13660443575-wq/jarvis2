# -*- coding: utf-8 -*-
"""每日简报单测：build_briefing 必须产出含日期与核心维度的多行文本。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import core
from backend.heart import briefing


def test_build_briefing_contains_today():
    text = briefing.build_briefing()
    assert isinstance(text, str) and text.strip()
    assert core.today() in text


def test_build_briefing_has_core_dimensions():
    text = briefing.build_briefing()
    # 采集箱 / 复习 / 记忆 / 技能 / 契约 / 知识库总数 都要出现
    for keyword in ["采集箱", "回忆卡", "技能", "知识库"]:
        assert keyword in text, f"简报缺少维度: {keyword}"


def test_generate_then_get_today():
    r = briefing.generate()
    assert r["ok"] and r["content"]
    d = briefing.get_today()
    assert d["date"] == core.today()
    assert d["content"] == r["content"]


if __name__ == "__main__":
    for fn in [test_build_briefing_contains_today, test_build_briefing_has_core_dimensions,
               test_generate_then_get_today]:
        fn()
        print("PASS", fn.__name__)
    print("全部通过")
