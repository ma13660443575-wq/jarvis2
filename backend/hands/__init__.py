# -*- coding: utf-8 -*-
"""hands — 手脚（工作流）。register_all() 一次性注册全部工作流到 registry。"""


def register_all() -> None:
    from . import wf01, wf02, wf03, wf04, wf05, wf06, wf07, wf09
    for mod in (wf01, wf02, wf03, wf04, wf05, wf06, wf07, wf09):
        mod.register()
    # 技能层：技能化工作流 wf08 + 06_Capability 技能扫描
    from ..skills import register_all as register_skills
    register_skills()
