# -*- coding: utf-8 -*-
"""路径契约单测：rel() 必须返回带 .md 的相对路径（曾在此回归成无扩展名）。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.vault import paths


def test_rel_with_md():
    assert paths.rel("C", "主动回忆") == "05_Frameworks/主动回忆.md"


def test_rel_strips_input_md():
    assert paths.rel("C", "主动回忆.md") == "05_Frameworks/主动回忆.md"


def test_rel_dynamic_domain():
    assert paths.rel("U", "U-20260814-工作流验证采集卡", domain="技术知识库") == \
        "04_Knowledge/技术知识库/U-20260814-工作流验证采集卡.md"


def test_rel_sys_strips_md():
    assert paths.rel_sys("09_System/自动化流程/训练日志.md") == "09_System/自动化流程/训练日志"


if __name__ == "__main__":
    for fn in [test_rel_with_md, test_rel_strips_input_md, test_rel_dynamic_domain, test_rel_sys_strips_md]:
        fn()
        print("PASS", fn.__name__)
    print("全部通过")
