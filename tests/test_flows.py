# -*- coding: utf-8 -*-
"""关键流程回归测试（隔离临时 vault + 临时 DB，不碰真实 vault/DB）。

覆盖：
- wf01 工作流：草稿路径对齐契约 → 确认后落盘 → 原卡标记已处理 → 训练日志
- 技能化 wf08：C- 概念卡 → S- 技能卡草稿 → 确认落盘 → 注册表刷新 → 触发词命中
- 记忆落库：确认候选 → 追加到 用户模型.md 正确分区 → frontmatter updated
- 混合检索降级：VECTOR_ENABLED=false 时关键词兜底，绝不抛异常
- 复盘聚合 / 事件总线 / 技能库目录自动创建
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from backend import config, db
from backend.engine import executor, registry
from backend.hands import register_all
from backend.vault import paths, reader, writer

# 统一 fixture：隔离 vault + DB，强制 Mock LLM + 关向量，保证测试快而稳
@pytest.fixture()
def iso(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    for rel in [
        "00_Inbox",
        "04_Knowledge/技术知识库",
        "04_Knowledge/回忆卡",
        "04_Knowledge/通用知识",
        "05_Frameworks",
        "02_Areas/个人成长/复盘",
        "02_Areas/认知与思想",
        "09_System/自动化流程",
        "01_Identity",
    ]:
        (vault / rel).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(config, "VAULT_PATH", vault)
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "")      # 强制 Mock LLM
    monkeypatch.setattr(config, "VECTOR_ENABLED", False)      # 关键词兜底路径
    db.init_db()
    register_all()
    # 清掉既有索引缓存，确保扫描基于新 vault
    reader.scan_vault(force=True)
    return vault


def _write(vault, rel: str, content: str) -> None:
    """直接往临时 vault 写一个文件并强制刷新扫描缓存。"""
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    reader.scan_vault(force=True)


def _I(rel: str = "00_Inbox/I-20260814-001-测试采集卡.md") -> str:
    return (
        "---\ntype: 采集\nstatus: 未处理\ncreated: 2026-08-14\n"
        "source: test\ntags: [测试]\n---\n\n"
        "# I-20260814-001-测试采集卡\n\n"
        "关于 SM-2 间隔重复的核心机制：每次复习按记忆质量打分，"
        "依据分数调整间隔与难易度，到期才再次复习。\n"
    )


def _C(rel: str = "05_Frameworks/C-主动回忆.md") -> str:
    return (
        "---\ntype: 概念\nstatus: 新建\ncreated: 2026-08-14\n"
        "source: \"[[00_Inbox/I-20260814-001-测试采集卡]]\"\ntags: []\n---\n\n"
        "# C-主动回忆\n\n"
        "**一句话版**：主动回忆是通过提取练习增强长期记忆的方法。\n\n"
        "**正文**：先检索再复习比反复重读有效，遗忘后再回忆能巩固记忆痕迹。\n\n"
        "**边界 / 反例**：对全新陌生内容直接回忆会挫败，需先有初步理解。\n"
    )


# ============ wf01 工作流 ============

def test_wf01_draft_paths_follow_contract(iso):
    _write(iso, "00_Inbox/I-20260814-001-测试采集卡.md", _I())
    r = executor.execute_command("处理采集箱")
    assert r["ok"], r.get("error")
    assert r["status"] == "draft", "交互式工作流必须先进草稿"
    assert r["drafts"], "必须产出草稿"
    for d in r["drafts"]:
        prefix = d["rel_path"].split("/")[0]
        assert prefix in ("04_Knowledge", "05_Frameworks"), d["rel_path"]
    # 确认前零写入：草稿文件尚未落盘
    for d in r["drafts"]:
        assert not (iso / d["rel_path"]).exists(), "确认前不应写入文件: " + d["rel_path"]


def test_wf01_confirm_writes_and_marks_processed(iso):
    rel = "00_Inbox/I-20260814-001-测试采集卡.md"
    _write(iso, rel, _I())
    r = executor.execute_command("处理采集箱", {"inbox_path": rel})
    assert r["ok"] and r["status"] == "draft"

    cr = executor.confirm_run(r["run_id"])
    assert cr["ok"], cr.get("error")
    # 草稿全部落盘
    for d in r["drafts"]:
        assert (iso / d["rel_path"]).is_file(), "确认后应落盘: " + d["rel_path"]
    # 原卡标记已处理
    note = reader.get_note(rel)
    assert note is not None and note.fm.get("status") == "已处理"
    # 训练日志已追加
    log_p = iso / paths.TRAINING_LOG
    assert log_p.is_file(), "应追加训练日志"
    assert "wf01" in log_p.read_text(encoding="utf-8")


# ============ 技能化 wf08 ============

def test_skillization_flow(iso):
    _write(iso, "05_Frameworks/C-主动回忆.md", _C())
    r = executor.execute_command("技能化", {"concept": "05_Frameworks/C-主动回忆.md"})
    assert r["ok"], r.get("error")
    assert r["status"] == "draft"
    s_rel = r["drafts"][0]["rel_path"]
    assert s_rel.startswith("06_Capability/"), s_rel

    cr = executor.confirm_run(r["run_id"])
    assert cr["ok"], cr.get("error")
    assert (iso / s_rel).is_file(), "技能卡应落盘: " + s_rel

    # 注册表刷新后触发词可命中
    from backend.skills import registry as skills_reg
    skills_reg.refresh()
    m = skills_reg.match_skill("帮我主动回忆一下")
    assert m, "新技能应可被自然说法触发"


# ============ 记忆落库 ============

def test_memory_confirm_appends_project_section(iso):
    from backend.db import ops as db_ops
    from backend.memory import manager

    ids = db_ops.add_memory_candidates([{
        "kind": "project",
        "summary": "绿建项目进入施工阶段",
        "detail": "需收集 8 类申报材料",
    }])
    assert ids, "应插入记忆候选"
    r = manager.confirm_candidate(ids[0])
    assert r["ok"], r.get("error")

    model_path = iso / paths.USER_MODEL
    text = model_path.read_text(encoding="utf-8")
    assert "## 三、项目记忆" in text
    # 项目分区出现该行（日期+摘要+详情）
    assert "绿建项目进入施工阶段" in text
    assert "需收集 8 类申报材料" in text
    # 确认后候选状态翻转
    cands = db_ops.list_memory_candidates(status="pending")
    assert all(c["id"] != ids[0] for c in cands)


# ============ 混合检索降级 ============

def test_hybrid_search_keyword_fallback(iso):
    _write(iso, "00_Inbox/I-20260814-001-测试采集卡.md", _I())
    from backend.brain import search as search_mod
    sr = search_mod.hybrid_search("SM-2 间隔重复", limit=5)
    assert sr["ok"]
    assert sr["mode"] == "keyword", "VECTOR_ENABLED=false 时应走关键词兜底"
    assert sr["results"], "关键词检索应命中种子卡"
    assert sr["results"][0]["rel_path"].startswith("00_Inbox/")
    # 无查询不报错
    assert search_mod.hybrid_search("")["ok"]


# ============ 复盘聚合 ============

def test_retro_overview_aggregates(iso):
    _write(iso, "05_Frameworks/C-主动回忆.md", _C())
    from backend.routers import retro as retro_router
    out = retro_router.retro_overview()
    assert out["ok"]
    data = out["data"]
    for key in ("today", "recent_retros", "skillizable", "review_stats",
                "recent_runs", "skills_count", "inbox_pending"):
        assert key in data, f"复盘聚合缺字段: {key}"
    # 种子 C-卡应出现在可技能化列表（尚无 S-卡对应）
    assert any("C-主动回忆" in c["title"] for c in data["skillizable"])


# ============ 事件总线 ============

def test_notify_pubsub_replay(iso):
    from backend.heart import notify
    q = notify.subscribe()
    # 清空回放的历史事件（环形缓冲跨测试共享）
    while True:
        try:
            q.get_nowait()
        except Exception:
            break
    notify.publish("tasks", {"action": "created", "task": {"id": 1}})
    evt = q.get(timeout=1)
    assert evt["channel"] == "tasks"
    assert evt["payload"]["action"] == "created"
    # recent 环形缓冲可回放
    recent = notify.recent(channel="tasks", limit=5)
    assert recent and recent[-1]["payload"]["task"]["id"] == 1
    notify.unsubscribe(q)


# ============ 技能库目录自动创建 ============

def test_capability_dir_auto_created(iso):
    p = paths.ensure_capability_dir()
    assert p.is_dir()
    assert p.name == "06_Capability"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
