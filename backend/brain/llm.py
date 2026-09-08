# -*- coding: utf-8 -*-
"""
brain/llm.py — LLM 统一出口（阿里云 DashScope / DeepSeek / Mock 三级降级）
=========================================================================
- 全项目只通过本模块调用大模型；未配置 key 时自动降级 Mock，任何端点不 500。
- 多后端：aliyun（qwen 系列）→ deepseek → mock。用户可在设置页手动选择后端/模型，
  或由 AI 按场景推荐（recommend_model，纯规则不耗额度）。
- chat_stream_iter：流式；chat_once：一次性。
- 切 key/后端后调用 clear_clients() 清空客户端缓存。
"""
from __future__ import annotations

from typing import Iterator

from .. import config

_aliyun_client = None
_deepseek_client = None

# ============================================================
# 场景 → 模型 推荐表（qwen 后端；DeepSeek 后端固定 deepseek-chat）
# ============================================================
SCENE_MODEL_MAP: dict[str, str] = {
    "chat": "qwen-plus",      # 日常对话：平衡速度与质量
    "briefing": "qwen-long",  # 简报要读大量 vault 上下文
    "memory": "qwen-turbo",   # 记忆抽取：结构化小任务
    "review": "qwen-turbo",   # 复习卡片生成
    "task": "qwen-turbo",     # 任务拆解
    "retro": "qwen-plus",     # 复盘分析
}

_SCENE_REASONS: dict[str, str] = {
    "chat": "日常对话：qwen-plus 在速度与回答质量间最均衡",
    "briefing": "例行简报需读取大量 vault 上下文：qwen-long 长上下文不丢信息",
    "memory": "记忆抽取是结构化小任务：qwen-turbo 响应快、省额度",
    "review": "复习卡片生成规则明确：qwen-turbo 足够且最快",
    "task": "任务拆解结构简单：qwen-turbo 快速完成",
    "retro": "复盘分析需要深度推理：qwen-plus 质量更高",
}


# ============================================================
# 后端 / 模型解析
# ============================================================

def _get_setting(key: str, default=None):
    """惰性读 settings 表（避免启动时连库）。"""
    try:
        from ..db import settings as settings_db
        return settings_db.get_setting(key, default)
    except Exception:  # noqa: BLE001
        return default


def resolve_backend() -> str:
    """当前生效后端：aliyun / deepseek / mock。

    优先级：用户手动选择（llm_backend 设置）→ 自动（DashScope key → DeepSeek key → Mock）。
    """
    choice = _get_setting("llm_backend", "auto")
    if choice == "aliyun" and config.is_dashscope_enabled():
        return "aliyun"
    if choice == "deepseek" and config.DEEPSEEK_API_KEY:
        return "deepseek"
    if choice == "mock":
        return "mock"
    # auto / 选择不可用 → 按优先级
    if config.is_dashscope_enabled():
        return "aliyun"
    if config.DEEPSEEK_API_KEY:
        return "deepseek"
    return "mock"


def resolve_model(scene: str = "chat") -> str:
    """当前生效模型名。aliyun 后端：手动模型优先 → 场景表 → 默认；deepseek：固定 deepseek-chat。"""
    backend = resolve_backend()
    if backend == "deepseek":
        return config.DEEPSEEK_MODEL
    if backend == "aliyun":
        manual = _get_setting("llm_model", "")
        if manual in config.DASHSCOPE_LLM_MODELS:
            return manual
        return SCENE_MODEL_MAP.get(scene, config.DASHSCOPE_DEFAULT_LLM_MODEL)
    return ""  # mock 不需要模型


def _fallback_chain(backend: str) -> list[str]:
    """按当前后端构造降级链（末尾恒为 mock）。"""
    if backend == "aliyun":
        chain = ["aliyun"]
        if config.DEEPSEEK_API_KEY:
            chain.append("deepseek")
    elif backend == "deepseek":
        chain = ["deepseek"]
    else:
        chain = []
    chain.append("mock")
    return chain


def recommend_model(scene: str = "chat") -> dict:
    """按场景推荐 {scene, backend, model, reason}。纯规则、不调 LLM、不耗额度。

    - DashScope 在位 → 推荐 qwen 系列对应模型
    - 无 DashScope 但有 DeepSeek → 降级 deepseek-chat
    - 都无 → mock
    """
    scene = (scene or "chat").lower()
    reason = _SCENE_REASONS.get(scene, "通用场景：qwen-plus 在速度与回答质量间最均衡")
    model = SCENE_MODEL_MAP.get(scene, config.DASHSCOPE_DEFAULT_LLM_MODEL)
    if not config.is_dashscope_enabled():
        if config.DEEPSEEK_API_KEY:
            return {"scene": scene, "backend": "deepseek", "model": config.DEEPSEEK_MODEL,
                    "reason": "DashScope 未配置 key，已降级 DeepSeek：" + reason}
        return {"scene": scene, "backend": "mock", "model": "",
                "reason": "未配置任何 LLM key，当前为 Mock 模式"}
    return {"scene": scene, "backend": "aliyun", "model": model, "reason": reason}


# ============================================================
# 客户端
# ============================================================

def get_client(backend: str | None = None):
    """按后端返回 openai 兼容客户端；backend=None 时按当前后端解析。"""
    global _aliyun_client, _deepseek_client
    backend = backend or resolve_backend()
    if backend == "aliyun":
        if _aliyun_client is None:
            from openai import OpenAI
            _aliyun_client = OpenAI(api_key=config.DASHSCOPE_API_KEY,
                                    base_url=config.DASHSCOPE_BASE_URL)
        return _aliyun_client
    if backend == "deepseek":
        if _deepseek_client is None:
            from openai import OpenAI
            _deepseek_client = OpenAI(api_key=config.DEEPSEEK_API_KEY,
                                      base_url=config.DEEPSEEK_API_BASE)
        return _deepseek_client
    return None


def clear_clients() -> None:
    """清空客户端缓存（切 key / 后端 / 模型后调用，确保下次取新配置）。"""
    global _aliyun_client, _deepseek_client
    _aliyun_client = None
    _deepseek_client = None


def is_enabled() -> bool:
    return config.is_llm_enabled()


# ============================================================
# Mock
# ============================================================

def _mock_reply(messages: list[dict]) -> str:
    """无 key 时的模拟回复：基于最后一条用户消息 + 系统提示里的上下文。"""
    sys_text = ""
    last_user = ""
    for m in messages:
        if m.get("role") == "system":
            sys_text += m.get("content", "")
        elif m.get("role") == "user":
            last_user = m.get("content", "")
    q = last_user.strip() or "（空）"
    if "以下是参考资料" in sys_text:
        return (
            "[Mock 模式 · 未配置 LLM key]\n"
            f"已检索知识库并围绕你的问题「{q}」组织回复。\n\n"
            "你可以在 .env 填入 DASHSCOPE_API_KEY（阿里云百炼）或 DEEPSEEK_API_KEY 获得真实智能回答；"
            "当前 Mock 回复用于验证完整链路（检索→上下文→人格注入→流式→记忆候选）。"
        )
    return (
        "[Mock 模式 · 未配置 LLM key]\n"
        f"你问的是：{q}\n\n"
        "这是降级回答。填入阿里云百炼 DASHSCOPE_API_KEY 后，JARVIS 会基于你的知识库给出带来源的回答。"
    )


# ============================================================
# usage 记账
# ============================================================

def _record_usage(scene: str, model: str, usage) -> None:
    """把一次 LLM 调用的 token 消耗写入 llm_usage 表（失败静默，绝不影响主流程）。"""
    try:
        from ..db import evals as evals_db
        evals_db.record_usage(
            scene, model,
            getattr(usage, "prompt_tokens", None) or (usage.get("prompt_tokens") if isinstance(usage, dict) else 0),
            getattr(usage, "completion_tokens", None) or (usage.get("completion_tokens") if isinstance(usage, dict) else 0),
            getattr(usage, "total_tokens", None) or (usage.get("total_tokens") if isinstance(usage, dict) else 0),
        )
    except Exception:  # noqa: BLE001
        pass


def _usage_to_dict(usage) -> dict | None:
    """OpenAI SDK usage 对象 / dict → 统一 dict；无则 None。"""
    if usage is None:
        return None
    try:
        return {
            "prompt_tokens": getattr(usage, "prompt_tokens", None) or (usage.get("prompt_tokens") if isinstance(usage, dict) else 0),
            "completion_tokens": getattr(usage, "completion_tokens", None) or (usage.get("completion_tokens") if isinstance(usage, dict) else 0),
            "total_tokens": getattr(usage, "total_tokens", None) or (usage.get("total_tokens") if isinstance(usage, dict) else 0),
        }
    except Exception:  # noqa: BLE001
        return None


# ============================================================
# 一次性调用
# ============================================================

def chat_once(messages: list[dict], *, temperature: float = 0.6,
              max_tokens: int = 1024, scene: str = "chat") -> str:
    """一次性调用。沿降级链尝试（aliyun→deepseek→mock），绝不抛异常。"""
    chain = _fallback_chain(resolve_backend())
    for backend in chain:
        if backend == "mock":
            return _mock_reply(messages)
        model = resolve_model(scene) if backend == "aliyun" else config.DEEPSEEK_MODEL
        try:
            client = get_client(backend)
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            text = resp.choices[0].message.content or ""
            try:
                if getattr(resp, "usage", None):
                    _record_usage(scene, model, resp.usage)
            except Exception:  # noqa: BLE001
                pass
            return text
        except Exception:  # noqa: BLE001
            continue
    return _mock_reply(messages)


def chat_stream_iter(messages: list[dict], *, temperature: float = 0.6,
                     max_tokens: int = 1024,
                     usage_box: dict | None = None,
                     scene: str = "chat") -> Iterator[str]:
    """流式调用，逐 chunk yield 文本。沿降级链尝试，绝不抛异常。

    usage_box: 若传入 dict，流结束后写入 {"usage": {...}} 或 {"usage": None}。
    scene: 记账场景名（chat/briefing/...），写入 llm_usage 表。
    """
    chain = _fallback_chain(resolve_backend())
    for backend in chain:
        if backend == "mock":
            if usage_box is not None:
                usage_box["usage"] = None
            yield _mock_reply(messages)
            return
        model = resolve_model(scene) if backend == "aliyun" else config.DEEPSEEK_MODEL
        started = False
        try:
            client = get_client(backend)
            stream = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
                stream_options={"include_usage": True},
            )
            usage = None
            for chunk in stream:
                if chunk.usage:
                    usage = _usage_to_dict(chunk.usage)
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    started = True
                    yield delta.content
            if usage_box is not None:
                usage_box["usage"] = usage
            if usage:
                _record_usage(scene, model, usage)
            return
        except Exception:  # noqa: BLE001
            if started:
                # 已产出内容后异常：不打断已有输出，直接结束（下次再走降级）
                return
            continue
    # 理论上到不了这（链尾恒为 mock）
    if usage_box is not None:
        usage_box["usage"] = None
    yield _mock_reply(messages)


def summarize(text: str, max_len: int = 120) -> str:
    """轻量本地摘要（不调 LLM）：截断 + 去重换行。"""
    text = " ".join(text.split())
    return text[:max_len] + ("…" if len(text) > max_len else "")


def extract_memory_candidates(question: str, answer: str) -> list[dict]:
    """规则化抽取记忆候选（P1 不依赖 LLM，保证无 key 也可闭环）。

    返回 [{kind, summary, detail}]，kind ∈ preference/decision/project/learning/life。
    命中规则：
      - 「我喜欢/我偏好/我习惯/我希望/我不喜欢/我不想」 → preference
      - 「我决定/我选择/以后用/改用/放弃」              → decision
      - 「我在做/我在负责/我手上/我最近在做/项目」       → project
    """
    text = (question + "\n" + answer).strip()
    if not text:
        return []
    lines = text.splitlines()
    out: list[dict] = []
    pref_pats = ["我喜欢", "我偏好", "我习惯", "我希望", "我不喜欢", "我不想", "我更愿意", "我倾向"]
    dec_pats = ["我决定", "我选择", "以后用", "改用", "放弃", "换用"]
    proj_pats = ["我在做", "我在负责", "我手上", "我最近在做", "这个项目", "项目名称"]

    for line in lines:
        line = line.strip()
        if not line or len(line) > 200:
            continue
        for pat in pref_pats:
            if pat in line:
                out.append({"kind": "preference", "summary": line, "detail": line})
                break
        else:
            for pat in dec_pats:
                if pat in line:
                    out.append({"kind": "decision", "summary": line, "detail": line})
                    break
            else:
                for pat in proj_pats:
                    if pat in line:
                        out.append({"kind": "project", "summary": line, "detail": line})
                        break
    # 去重
    seen: set[str] = set()
    uniq = []
    for c in out:
        if c["summary"] not in seen:
            seen.add(c["summary"])
            uniq.append(c)
    return uniq[:5]
