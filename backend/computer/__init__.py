# -*- coding: utf-8 -*-
"""本地电脑能力：Windows 观察桥 + 受确认保护的受限控件动作。"""

from . import actions
from .windows import (
    active_window, activate_window, capabilities, click_control, list_windows,
    prepare_click_target, prepare_text_target, resolve_window, set_control_value,
    status, ui_automation_available, ui_tree,
)

__all__ = [
    "actions", "active_window", "activate_window", "capabilities", "click_control",
    "list_windows", "prepare_click_target", "prepare_text_target", "resolve_window",
    "set_control_value", "status", "ui_automation_available", "ui_tree",
]
