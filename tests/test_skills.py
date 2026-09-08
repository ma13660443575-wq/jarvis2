# -*- coding: utf-8 -*-
"""技能层回归测试：S-卡解析、词干匹配、生成格式化。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.skills import generate, parser, registry

_SAMPLE = """---
type: 技能
status: 可用
confidence: 中
source: "[[05_Frameworks/C-会议纪要沉淀]]"
created: 2026-08-14
updated: 2026-08-14
trigger: ["整理会议纪要", "会议纪要", "帮我沉淀纪要"]
tools: []
output: report
---

# S-会议纪要沉淀

## 一句话
把散乱会议记录转成结论-行动-责任人-时限四要素。

## 处理流程
1. 提取结论
2. 列行动项并指定责任人
"""


def test_parse_skill():
    sk = parser.parse_skill("06_Capability/S-会议纪要沉淀.md", _SAMPLE)
    assert sk.name == "会议纪要沉淀"
    assert sk.slug == "skill:会议纪要沉淀"
    assert "整理会议纪要" in sk.trigger
    assert sk.output == "report"
    assert sk.one_liner
    assert sk.flow


def test_name_keywords_strips_suffix():
    kws = registry._name_keywords("会议纪要沉淀")
    assert "会议纪要沉淀" in kws
    assert "会议纪要" in kws          # 剥掉「沉淀」后的词干
    kws2 = registry._name_keywords("竞品分析")
    assert "竞品" in kws2


def test_parse_output_draft():
    raw = _SAMPLE.replace("output: report", "output: draft")
    assert parser.parse_skill("06_Capability/S-x.md", raw).output == "draft"


def test_lines_format():
    assert generate._lines(["a", "b"], "f") == "1. a\n2. b"
    assert generate._lines("x", "f") == "x"
    assert generate._lines(None, "f") == "f"


def test_match_skill_natural_phrasing():
    sk = parser.parse_skill("06_Capability/S-会议纪要沉淀.md", _SAMPLE)
    registry._skills[sk.slug] = sk
    registry._by_trigger = [(t, sk.slug) for t in sk.trigger]
    try:
        # 触发词精确命中
        m = registry.match_skill("帮我整理会议纪要")
        assert m and m["name"] == sk.slug
        # 技能名词干兜底命中（「会议纪要」在文本中）
        m2 = registry.match_skill("这份会议纪要需要处理一下")
        assert m2 and m2["name"] == sk.slug
    finally:
        registry._skills.clear()
        registry._by_trigger.clear()


if __name__ == "__main__":
    fns = [test_parse_skill, test_name_keywords_strips_suffix, test_parse_output_draft,
           test_lines_format, test_match_skill_natural_phrasing]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
    print("全部通过")
