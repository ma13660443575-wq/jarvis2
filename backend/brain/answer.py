# -*- coding: utf-8 -*-
"""
brain/answer.py — 问答编排（一条对话数据流的核心）
=================================================
流程：分流 → （RAG）检索 → 人格三层注入 → LLM 流式 → 记忆候选自动抽取 → 入库。
以事件生成器形式暴露，供 router 转 NDJSON 流。
"""
from __future__ import annotations

import time
from typing import Iterator

from .. import db
from ..memory import persona
from ..engine import registry, executor
from ..agent import broker
from ..computer import actions as computer_actions
from ..hands import register_all as register_hands
from . import classify, llm, rag

# 含这些词的句子即便命中命令触发词，也判定为「提问」而非「执行指令」
_ASK_WORDS = (
    "怎么", "如何", "为什么", "什么是", "是什么", "啥是", "啥叫",
    "解释", "原理", "意思", "告诉我", "说说", "讲讲", "介绍一下",
    "能不能", "可以吗", "怎么用", "帮我看看",
)


def _looks_like_chat(question: str) -> bool:
    """「怎么处理采集箱」「处理采集箱的流程是什么」→ 走对话，不误执行工作流。"""
    q = question.strip()
    return any(w in q for w in _ASK_WORDS)


def _step(kind: str, text: str) -> dict:
    """Agent 思考流事件（供前端终端命令行打字机展示）。"""
    return {"type": "step", "kind": kind, "text": text, "t": time.time()}


def _computer_action_stream(question: str, plan: dict,
                            session_id: object = "session:default") -> Iterator[dict]:
    """Computer Action 专属 terminal stream；不创建 Conversation 或记忆消息。"""
    from ..agent import task_engine

    try:
        task_engine.ensure_desktop_execution_available(session_id)
        pending = computer_actions.create_pending_action(plan, question)
    except task_engine.TaskRuntimeError as exc:
        pending = {
            "ok": False,
            "status": "failed",
            "error_code": exc.code,
            "error": exc.message,
        }
        if exc.task_result is not None:
            pending["blocking_task_id"] = exc.task_result.task_id
    if not pending.get("ok"):
        plan = {
            **plan,
            "status": "failed",
            "error": pending.get("error", "电脑动作准备失败"),
            "error_code": pending.get("error_code", "EXECUTION_FAILED"),
            **({"resolution": pending["resolution"]} if pending.get("resolution") else {}),
        }
    else:
        plan = {**plan, **pending, "status": "waiting_confirmation"}

    yield {
        "type": "start", "question": question, "mode": "command",
        "computer_action_owned": True,
    }
    yield {"type": "plan", "plan": plan}
    yield _step("command", f"识别能力：{plan.get('title') or '本机动作'}")
    if plan.get("status") == "failed":
        result = computer_actions.build_terminal_failure_result(plan, plan)
        yield _step("exec", "动作准备失败")
        yield result
        yield {"type": "delta", "text": f"这条电脑动作没准备好：{result['error']}"}
        yield {
            "type": "done", "action_plan": plan, "action_result": result,
            "terminal": True,
            "computer_action_owned": True, "sources": [], "candidates": [],
            "candidate_ids": [], "token_usage": None,
        }
        return

    yield _step("confirm", "本机动作已准备，等待你的确认")
    yield {"type": "delta", "text": "我已准备好本机动作，请核对目标后确认执行。"}
    yield {
        "type": "done", "action_plan": plan,
        "computer_action_owned": True, "sources": [], "candidates": [],
        "candidate_ids": [], "token_usage": None,
    }


def _desktop_task_stream(question: str, plan: dict,
                         conversation_id: int | None = None) -> Iterator[dict]:
    """Desktop Task terminal stream；不创建 Conversation、RAG、Memory 或 LLM。"""
    from ..agent import task_engine

    yield {
        "type": "start",
        "mode": "task",
        "desktop_task_owned": True,
        "task_id": plan.get("task_id"),
    }
    try:
        started = task_engine.start_task(
            plan, question,
            session_id=f"session:{conversation_id}" if conversation_id is not None else "session:default",
        )
        task_plan = started["task_plan"]
        task_result = started["task_result"]
        yield {"type": "plan", "plan": task_plan}
        yield _step("command", "已生成受约束任务计划，当前步骤等待确认")
        yield {"type": "delta", "text": "任务已准备好，请确认当前步骤后继续。"}
        yield {
            "type": "done",
            "action_plan": task_plan,
            "task_result": task_result,
            "terminal": True,
            "desktop_task_owned": True,
            "sources": [],
            "candidates": [],
            "candidate_ids": [],
            "token_usage": None,
        }
    except task_engine.TaskRuntimeError as exc:
        task_result = exc.task_result.to_dict() if exc.task_result else {
            "task_id": plan.get("task_id", ""),
            "status": "FAILED",
            "business_goal_completed": False,
            "reason": exc.message,
            "error_code": exc.code,
            "current_step_id": None,
            "step_results": [],
            "action_result": None,
            "idempotent": False,
        }
        failed_plan = {
            **plan,
            "status": "failed",
            "task_state": "FAILED",
            "error_code": exc.code,
            "error": exc.message,
            "desktop_task_owned": True,
        }
        yield {"type": "plan", "plan": failed_plan}
        yield _step("exec", "任务未启动")
        yield {"type": "delta", "text": f"任务无法开始：[{exc.code}] {exc.message}"}
        yield {
            "type": "done",
            "action_plan": failed_plan,
            "task_result": task_result,
            "terminal": True,
            "desktop_task_owned": True,
            "sources": [],
            "candidates": [],
            "candidate_ids": [],
            "token_usage": None,
        }


def chat_stream(question: str, conversation_id: int | None = None,
                max_history_turns: int = 10,
                planned_plan: dict | None = None) -> Iterator[dict]:
    # ===== 意图路由层：功能隐藏到 Agent 层 =====
    # 先完成能力注册与 Broker 归属判断；Computer Action 在此之前不得创建
    # Conversation、写入消息或进入后续 Memory/RAG/LLM 编排。
    register_hands()  # 幂等：注册 wf01..wf09 + 技能
    plan = planned_plan if isinstance(planned_plan, dict) else broker.plan_command(question)
    owner = broker.execution_owner(plan)
    if owner == "desktop_task":
        yield from _desktop_task_stream(question, plan, conversation_id)
        return
    if owner == "computer_action":
        session_id = f"session:{conversation_id}" if conversation_id is not None else "session:default"
        yield from _computer_action_stream(question, plan, session_id)
        return

    conv_id = db.ops.get_or_create_conversation(conversation_id, title=question[:24])
    db.ops.add_message(conv_id, "user", question)
    parsed = registry.parse_command(question)
    is_registered_command = parsed["type"] in ("workflow", "pipeline", "skill")
    if is_registered_command and not _looks_like_chat(question):
        if plan.get("requires_confirmation"):
            yield _step("command", "该能力会写入 Obsidian，完成草稿后等待确认")
        yield _step("exec", "正在执行…")
        result = executor.execute_command(question, {"_plan_id": plan.get("plan_id")})
        if not result.get("ok"):
            plan = broker.finalize_plan(plan, result)
            text = f"这条命令没跑起来：{result.get('error')}"
            yield _step("exec", "执行失败")
            yield {"type": "delta", "text": text}
            db.ops.add_message(conv_id, "assistant", text)
            yield {"type": "done", "conversation_id": conv_id, "message_id": None,
                   "action_plan": plan,
                   "candidates": [], "candidate_ids": [], "sources": [],
                   "token_usage": None}
            return
        report = result.get("report") or ""
        written = list(result.get("written") or [])
        runs = result.get("runs") or []
        if not written and runs:
            for r in runs:
                written.extend(r.get("written", []))
        if not report:
            report = f"已执行 {len(runs) if runs else 1} 个工作流。"
            if written:
                report += f" 写入 {len(written)} 个文件。"
        yield _step("exec", f"完成 · 写入 {len(written)} 个文件" if written else "完成")
        plan = broker.finalize_plan(plan, result)
        yield {"type": "delta", "text": report}
        db.ops.add_message(conv_id, "assistant", report)
        yield {"type": "done", "conversation_id": conv_id, "message_id": None,
               "command": {
                   "run_id": result.get("run_id") or (result.get("pending_run") or {}).get("run_id"),
                   "status": result.get("status"),
                   "drafts": result.get("drafts", []),
                   "written": written,
               },
               "action_plan": plan,
               "candidates": [], "candidate_ids": [], "sources": [],
               "token_usage": None}
        return

    mode = classify.classify_question(question)

    yield {"type": "start", "conversation_id": conv_id, "question": question}
    yield _step("system", "JARVIS System 在线")
    yield _step("cognitive", "正在解析你的意图…")

    context = ""
    sources: list[str] = []
    if mode == "rag":
        try:
            ctx = rag.build_context(question)
            context, sources = ctx["context"], ctx["sources"]
        except Exception:
            context, sources = "", []
        if context:
            yield _step("retrieval", f"检索知识库：匹配到 {len(sources)} 个上下文")
            yield _step("memory", "已载入相关记忆")
        else:
            yield _step("direct", "未命中知识库，直接作答")
        yield {"type": "sources", "sources": sources, "mode": context and "rag" or "direct"}
    else:
        yield _step("direct", "直接作答")

    sys_prompt = persona.build_system_prompt()
    messages = [{"role": "system", "content": sys_prompt}]
    # 历史（P1 简化：只带最近的 user/assistant）
    try:
        history = db.ops.history_messages(conv_id, max_turns=max_history_turns)
        messages.extend(history[:-1])  # 排除刚插入的当前 question
    except Exception:
        pass
    if context:
        messages.append({"role": "system", "content": context})
    messages.append({"role": "user", "content": question})

    usage_box: dict = {}
    full = []
    for chunk in llm.chat_stream_iter(messages, usage_box=usage_box):
        full.append(chunk)
        yield {"type": "delta", "text": chunk}
    answer = "".join(full)

    # 记忆候选自动抽取 + 入库
    cands = llm.extract_memory_candidates(question, answer)
    cand_ids = db.ops.add_memory_candidates(cands)
    msg_id = db.ops.add_message(conv_id, "assistant", answer, sources=sources)

    yield {"type": "done", "conversation_id": conv_id, "message_id": msg_id,
           "candidates": cands, "candidate_ids": cand_ids, "sources": sources,
           "token_usage": usage_box.get("usage")}
