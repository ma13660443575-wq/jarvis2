# -*- coding: utf-8 -*-
"""
paths.py — vault 路径契约【单一事实源】
======================================
JARVIS2 所有对 vault 的读写，只能通过本模块解析路径。禁止在业务代码里散落
硬编码目录名（旧版项目正是因为散落硬编码才与真实 vault 彻底割裂）。

默认 vault 结构（由 VAULT_PATH 配置，PARA + 前缀命名）：
    00_Inbox / 01_Identity / 02_Areas / 03_Projects / 04_Knowledge /
    05_Frameworks / 06_Agents / 07_Resources / 08_Templates / 09_System / 99_Archive

设计原则：
- 所有相对路径统一 posix（正斜杠）。
- `resolve(prefix, name, ...)` 返回相对路径；`abs(prefix, name, ...)` 返回绝对路径。
- 启动时 `check_contract()` 自检：对照真实 vault 输出缺失目录警告，**绝不自动建目录**。
"""
from __future__ import annotations

import os
from pathlib import Path

from .. import config

# ============================================================
# 一、前缀 → 目录映射（动态部分用 <域> / <项目名> 占位）
# ============================================================

# 理解/图谱笔记所在的"域"枚举（对应 04_Knowledge 下的子库 / 02_Areas 下的领域）
KNOWLEDGE_DOMAINS = [
    "AI知识库",
    "产品知识库",
    "技术知识库",
    "管理知识库",
    "行业知识库",
    "通用知识",
]
AREA_DOMAINS = [
    "AI人工智能",
    "产品经理",
    "软件工程",
    "建筑数字化",
    "商业认知",
    "认知与思想",
    "个人成长",
]

# 前缀 → 目标目录（相对 vault 根）。动态部分 resolve 时替换。
PREFIX_DIR = {
    "I":   "00_Inbox",
    "U":   "04_Knowledge/<域>",
    "C":   "05_Frameworks",
    "R":   "04_Knowledge/回忆卡",
    "A":   "04_Knowledge/通用知识",
    "L":   "02_Areas/个人成长/复盘",
    "T":   "02_Areas/认知与思想",
    "MOC": "02_Areas/<域>",
    "P":   "03_Projects/<项目名>",
    "S":   "06_Capability",
}

# 需要指定动态参数的动态前缀
DYNAMIC_PREFIXES = {"U", "MOC", "P"}

# ============================================================
# 二、特殊系统路径（相对 vault 根）
# ============================================================
TRAINING_LOG = "09_System/自动化流程/训练日志.md"
USER_PROFILE = "01_Identity/个人档案.md"     # 静态画像
USER_MODEL = "01_Identity/用户模型.md"       # 动态记忆（单文件+分区）
MY_VIEWS = "02_Areas/认知与思想/我的观点.md"   # 立场库
INBOX_DIR = "00_Inbox"
CAPABILITY_DIR = "06_Capability"             # 技能库（新增）

# ============================================================
# 三、解析函数
# ============================================================


def _fill(template: str, domain: str | None = None, project: str | None = None) -> str:
    """填充目录模板中的动态占位符。"""
    out = template
    if "<域>" in out:
        if not domain:
            raise ValueError("该前缀需要指定 domain（域），可选值: %s" % "/".join(KNOWLEDGE_DOMAINS + AREA_DOMAINS))
        out = out.replace("<域>", domain)
    if "<项目名>" in out:
        if not project:
            raise ValueError("该前缀需要指定 project（项目名）")
        out = out.replace("<项目名>", project)
    return out


def dir_for(prefix: str, domain: str | None = None, project: str | None = None) -> str:
    """返回某个前缀对应的目标目录（相对 vault 根，无尾斜杠）。"""
    if prefix not in PREFIX_DIR:
        raise KeyError("未知前缀: %s（合法: %s）" % (prefix, ",".join(sorted(PREFIX_DIR))))
    return _fill(PREFIX_DIR[prefix], domain=domain, project=project)


def rel(prefix: str, name: str, domain: str | None = None, project: str | None = None) -> str:
    """构造笔记相对路径，如 rel('C', '主动回忆') → '05_Frameworks/主动回忆.md'。

    - name 可带/可不带 .md 后缀。
    - name 若已含目录路径（如 'AI知识库/U-xxx'），则以 name 为准直接返回（兼容外部引用）。
    """
    if "/" in name:
        return name if name.endswith(".md") else name + ".md"
    d = dir_for(prefix, domain=domain, project=project)
    n = name[:-3] if name.endswith(".md") else name
    return f"{d}/{n}.md"


def abs(prefix: str, name: str, domain: str | None = None, project: str | None = None) -> Path:
    """构造笔记绝对路径。"""
    r = rel(prefix, name, domain=domain, project=project)
    return (config.VAULT_PATH / r).resolve()


def rel_sys(rel_path: str) -> str:
    """特殊系统路径：去掉 .md 后缀返回可链接的 posix 路径。"""
    return rel_path[:-3] if rel_path.endswith(".md") else rel_path


# ============================================================
# 四、契约自检（启动时调用，输出缺失目录警告，不自动建目录）
# ============================================================


def contract_dirs() -> list[str]:
    """全部契约目录（相对路径，已解析动态部分）。用于启动自检。"""
    dirs: list[str] = []
    for prefix, template in PREFIX_DIR.items():
        if prefix == "U":
            dirs += [_fill(template, domain=d) for d in KNOWLEDGE_DOMAINS]
        elif prefix == "MOC":
            dirs += [_fill(template, domain=d) for d in AREA_DOMAINS]
        elif prefix == "P":
            continue  # 项目目录按实际项目动态存在，不静态校验
        else:
            dirs.append(template)
    dirs += [
        str(Path(TRAINING_LOG).parent),
        "01_Identity",
        "02_Areas/认知与思想",
        "06_Capability",
        "09_System/自动化流程",
    ]
    # 去重保序
    seen: set[str] = set()
    out: list[str] = []
    for d in dirs:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


def check_contract() -> dict:
    """对照真实 vault 自检。返回 { ok, missing: [相对路径...] }。

    注意：06_Capability 技能库属于"应用新增层"，缺失时由应用负责创建（唯一允许自动建目录）。
    """
    missing = []
    for rel_d in contract_dirs():
        if rel_d == "06_Capability":
            continue  # 技能库由应用创建
        if not (config.VAULT_PATH / rel_d).is_dir():
            missing.append(rel_d)
    return {"ok": len(missing) == 0, "missing": missing}


def ensure_capability_dir() -> Path:
    """确保技能库目录存在（应用唯一允许自动创建的目录）。返回绝对路径。"""
    p = config.VAULT_PATH / CAPABILITY_DIR
    p.mkdir(parents=True, exist_ok=True)
    return p


def vault_stats() -> dict:
    """快速统计 vault 顶层节点数（按目录）。供 status/dashboard 使用。"""
    base = config.VAULT_PATH
    stats = {}
    if not base.is_dir():
        return {"ok": False, "error": "vault 不存在: %s" % base}
    for entry in sorted(base.iterdir()):
        if entry.name.startswith(".") or not entry.is_dir():
            continue
        n_md = sum(1 for _ in entry.rglob("*.md"))
        stats[entry.name] = n_md
    return {"ok": True, "vault": str(base), "dirs": stats,
            "total_notes": sum(stats.values())}
