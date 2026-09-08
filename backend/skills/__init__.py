# -*- coding: utf-8 -*-
"""skills — 技能层：S-卡解析 / 注册 / 通用执行 / 技能化生成。"""


def register_all() -> None:
    from . import registry
    registry.register_all()
