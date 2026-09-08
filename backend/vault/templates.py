# -*- coding: utf-8 -*-
"""
vault/templates.py — 知识卡片内容模板（统一产出物格式）
======================================================
把 U-/C-/R-/A-/S- 卡的 Markdown 模板集中在此，供 hands 与 skills 复用。
所有模板只做「字符串拼装」，LLM/规则生成的结构化字段由调用方传入。
"""
from __future__ import annotations

from . import paths


def _fm(**kwargs) -> str:
    """生成 frontmatter 块。值类型自动序列化（list→[...]，str→保留原样）。"""
    lines = ["---"]
    for k, v in kwargs.items():
        if v is None:
            continue
        if isinstance(v, list):
            items = ', '.join('"%s"' % str(x) for x in v)
            lines.append(f"{k}: [{items}]")
        elif isinstance(v, bool):
            lines.append(f"{k}: {'true' if v else 'false'}")
        elif isinstance(v, (int, float)):
            lines.append(f"{k}: {v}")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines)


def understand_card(u_name: str, *, domain: str, summary: str, content: str,
                    source_wl: str, created: str, concept_wl: str) -> str:
    """U- 理解卡：一句话 + 正文 + 关联。"""
    rel = paths.rel("U", u_name, domain=domain)
    fm = _fm(type="理解", status="处理中", domain=domain, source=f'"{source_wl}"',
             created=created, tags=[])
    return (
        f"{fm}\n\n# {u_name}\n\n"
        f"> 一句话：{summary}\n\n"
        f"## 正文\n\n{content}\n\n"
        f"## 关联\n- 概念：{concept_wl}\n- 回忆：待生成\n"
    )


def concept_card(c_name: str, *, summary: str, body: str, edge: str,
                 source_wl: str, created: str) -> str:
    """C- 概念卡：一句话版 + 正文（含边界/反例）。"""
    fm = _fm(type="概念", status="新建", source=f'"{source_wl}"', created=created, tags=[])
    return (
        f"{fm}\n\n# {c_name}\n\n"
        f"**一句话版**：{summary}\n\n"
        f"**正文**：{body}\n\n"
        f"**边界 / 反例**：{edge}\n"
    )


def recall_card(r_name: str, *, question: str, answer: str, linked_card: str,
                source_wl: str, created: str, interval: float = 0.0,
                easiness: float = 2.5, due: str | None = None) -> str:
    """R- 回忆卡：问/答 + SM-2 调度字段。"""
    due = due or created
    fm = _fm(type="回忆", status="新建", source=f'"{source_wl}"', created=created,
             interval=interval, easiness=easiness, due=due,
             linked_card=f'"{linked_card}"', tags=[])
    return (
        f"{fm}\n\n# {r_name}\n\n"
        f"问：{question}\n\n答：{answer}\n"
    )


def apply_card(a_name: str, *, summary: str, suggestion: str, source_wl: str,
               created: str, tags: list[str] | None = None) -> str:
    """A- 应用卡：基于某概念的应用计划 + 状态追踪。"""
    fm = _fm(type="应用", status="计划中", source=f'"{source_wl}"', created=created,
             tags=tags or [])
    return (
        f"{fm}\n\n# {a_name}\n\n"
        f"> {summary}\n\n"
        f"## 应用建议\n\n{suggestion}\n\n"
        f"## 状态追踪\n- [ ] 尝试应用\n- [ ] 验证效果\n- [ ] 沉淀复盘\n"
    )


def skill_card(s_name: str, *, name: str, one_liner: str, trigger: list[str],
               flow: str, output: str, criteria: str, input_desc: str,
               links: str, source: str, confidence: str, created: str) -> str:
    """S- 技能卡（06_Capability/S-*），供技能化工作流使用。"""
    fm = _fm(type="技能", status="可用", confidence=confidence, source=f'"{source}"',
             created=created, updated=created,
             trigger=trigger, tools=[], output=output)
    return (
        f"{fm}\n\n# S-{name}\n\n"
        f"## 一句话\n{one_liner}\n\n"
        f"## 输入\n{input_desc}\n\n"
        f"## 处理流程\n{flow}\n\n"
        f"## 输出\n{output}\n\n"
        f"## 评估标准\n{criteria}\n\n"
        f"## 与概念连接\n{links}\n"
    )
