# -*- coding: utf-8 -*-
"""
config.py — JARVIS2 运行配置（环境变量驱动，纯标准库 .env 加载）
- 全部敏感信息（DeepSeek key / 通知通道）只从 .env 读取，绝不硬编码。
- 未配置时自动降级：LLM→mock，感官→提示不可用，任何端点不 500。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    # ===== PyInstaller 打包态：数据目录移到可写持久区，UI 从 bundle 读取 =====
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    _appdata = Path(os.getenv("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    ROOT = Path(os.getenv("JARVIS2_DATA_DIR", str(_appdata / "JARVIS2")))
    DATA_DIR = ROOT / "data"
    DRAFTS_DIR = DATA_DIR / "drafts"
    FRONTEND_DIST = BUNDLE_DIR / "frontend_dist"
    # 首启：把打包的默认 .env 种子复制到用户可编辑位置
    try:
        _src_env = BUNDLE_DIR / ".env"
        _dst_env = ROOT / ".env"
        if _src_env.exists() and not _dst_env.exists():
            ROOT.mkdir(parents=True, exist_ok=True)
            _dst_env.write_text(_src_env.read_text(encoding="utf-8"), encoding="utf-8")
    except OSError:
        pass
else:
    ROOT = Path(__file__).resolve().parent.parent
    DATA_DIR = ROOT / "data"
    DRAFTS_DIR = DATA_DIR / "drafts"
    FRONTEND_DIST = ROOT / "frontend" / "dist"


def _load_dotenv() -> None:
    """轻量 .env 加载：ROOT/.env → os.environ。环境变量优先级更高。"""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()

# ===== vault（知识底座）=====
VAULT_PATH = Path(os.getenv("VAULT_PATH", str(Path.home() / "Obsidian")))

# ===== LLM（DeepSeek，OpenAI 兼容协议）=====
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_BASE = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# ===== 阿里云百炼 DashScope（LLM 首选后端 + sambert TTS + qwen-audio ASR）=====
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
DASHSCOPE_BASE_URL = os.getenv(
    "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
)
DASHSCOPE_WS_URL = os.getenv(
    "DASHSCOPE_WS_URL", "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
)
# LLM：qwen 系列（OpenAI 兼容协议，复用 openai SDK）
DASHSCOPE_LLM_MODELS = [
    m.strip() for m in os.getenv("DASHSCOPE_LLM_MODELS", "qwen-max,qwen-plus,qwen-turbo,qwen-long").split(",") if m.strip()
]
DASHSCOPE_DEFAULT_LLM_MODEL = os.getenv("DASHSCOPE_DEFAULT_LLM_MODEL", "qwen-plus")
# TTS：sambert 全音色（zhide/zhida/zhishu/zhiyue/eva）
DASHSCOPE_TTS_VOICES = [
    m.strip() for m in os.getenv(
        "DASHSCOPE_TTS_VOICES",
        "sambert-zhide-v1,sambert-zhida-v1,sambert-zhishu-v1,sambert-zhiyue-v1,sambert-eva-v1",
    ).split(",") if m.strip()
]
DASHSCOPE_TTS_MODEL = os.getenv("DASHSCOPE_TTS_MODEL", "sambert-zhide-v1")
# ASR：qwen-audio 流式（DashScope WebSocket 实时转写）
DASHSCOPE_ASR_MODEL = os.getenv("DASHSCOPE_ASR_MODEL", "qwen-audio-3.0-asr-flash-streaming")

# ===== 服务 =====
PORT = int(os.getenv("PORT", "8100"))
HOST = os.getenv("HOST", "127.0.0.1")
DB_PATH = os.getenv("DB_PATH", str(DATA_DIR / "app.db"))

# ===== 感官 =====
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY", "")
RSS_FEEDS = [u for u in os.getenv("RSS_FEEDS", "").split(",") if u.strip()]
ALLOWED_DIRS = [str(VAULT_PATH), str(DATA_DIR)]
SHELL_ALLOWLIST = os.getenv(
    "SHELL_ALLOWLIST",
    "git status|git log|git diff|pip list|python --version|dir|echo",
).split("|")
PLAYWRIGHT_ENABLED = os.getenv("PLAYWRIGHT_ENABLED", "false").lower() == "true"

# ===== 手机通知桥（预留）=====
NOTIFY_NTFY_TOPIC = os.getenv("NOTIFY_NTFY_TOPIC", "")
NOTIFY_SERVERCHAN_KEY = os.getenv("NOTIFY_SERVERCHAN_KEY", "")
NOTIFY_TG_BOT_TOKEN = os.getenv("NOTIFY_TG_BOT_TOKEN", "")
NOTIFY_TG_CHAT_ID = os.getenv("NOTIFY_TG_CHAT_ID", "")

# ===== 心 =====
BRIEF_TIME = os.getenv("BRIEF_TIME", "08:30")

# ===== 记忆层 =====
VECTOR_ENABLED = os.getenv("VECTOR_ENABLED", "true").lower() == "true"
QDRANT_PATH = os.getenv("QDRANT_PATH", str(DATA_DIR / "qdrant"))
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
MEMORY_CANDIDATE_LIMIT = int(os.getenv("MEMORY_CANDIDATE_LIMIT", "20"))

# ===== 火山引擎 · 豆包语音（ASR + TTS）=====
# 鉴权二选一，勿混用：
#   新版：VOLCANO_API_KEY → X-Api-Key
#   旧版：VOLCANO_APP_ID + VOLCANO_ACCESS_TOKEN（→ X-Api-Access-Key，无需拼任何后缀）
#   VOLCANO_SECRET_KEY 为旧版应用的 SecretKey，部分 HTTP 端点签名时用，这里仅存不启用。
# VOLCANO_ACCESS_KEY 为旧变量名，兼容读取；推荐用 VOLCANO_ACCESS_TOKEN。
VOLCANO_API_KEY = os.getenv("VOLCANO_API_KEY", "")
VOLCANO_APP_ID = os.getenv("VOLCANO_APP_ID", "")
VOLCANO_ACCESS_KEY = os.getenv("VOLCANO_ACCESS_KEY", "")
VOLCANO_ACCESS_TOKEN = os.getenv("VOLCANO_ACCESS_TOKEN", VOLCANO_ACCESS_KEY)
VOLCANO_SECRET_KEY = os.getenv("VOLCANO_SECRET_KEY", "")
VOLCANO_TTS_RESOURCE_ID = os.getenv("VOLCANO_TTS_RESOURCE_ID", "seed-tts-2.0")
VOLCANO_ASR_RESOURCE_ID = os.getenv("VOLCANO_ASR_RESOURCE_ID", "volc.seedasr.sauc.duration")
VOLCANO_ASR_ENDPOINT = os.getenv(
    "VOLCANO_ASR_ENDPOINT",
    "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async",
)
VOLCANO_TTS_SPEAKER = os.getenv("VOLCANO_TTS_SPEAKER", "zh_female_xiaohe_uranus_bigtts")
VOLCANO_TTS_FORMAT = os.getenv("VOLCANO_TTS_FORMAT", "mp3")

# ===== Edge 神经语音（JARVIS 双语音，免费，无需 key；TTS 唯一后端方案）=====
TTS_VOICE_EN = os.getenv("TTS_VOICE_EN", "en-GB-RyanNeural")   # 英文 → 电影 JARVIS 腔调
TTS_VOICE_ZH = os.getenv("TTS_VOICE_ZH", "zh-CN-YunjianNeural")  # 中文 → 沉稳男声
TTS_RATE = os.getenv("TTS_RATE", "-8%")                          # 稍慢、更沉稳
TTS_RETRIES = int(os.getenv("TTS_RETRIES", "3"))
# TTS_EDGE_ENABLED=false 可关闭 Edge TTS；关闭后 /api/voice/tts 返回 503 明确提示。
TTS_EDGE_ENABLED = os.getenv("TTS_EDGE_ENABLED", "true").lower() == "true"

# ===== 本地语音引擎（sherpa-onnx 离线 ASR）=====
# VOICE_ENGINE=local → 用 sherpa-onnx 本地识别（默认，首启自动下载模型，无需任何 key）
# VOICE_ENGINE=volcano → 回退火山 bigmodel 在线识别（需火山 key）
VOICE_ENGINE = os.getenv("VOICE_ENGINE", "local")
SHERPA_MODEL_DIR = os.getenv("SHERPA_MODEL_DIR", str(ROOT / "models"))
ASR_MODEL_NAME = os.getenv("ASR_MODEL_NAME", "sherpa-onnx-paraformer-zh-small-2024-03-09")

# ===== 全局后台唤醒 agent（jarvis2-wake-agent，独立常驻进程）=====
WAKE_ENABLED = os.getenv("WAKE_ENABLED", "true").lower() == "true"
WAKE_WORDS = os.getenv("WAKE_WORDS", "jarvis,贾维斯")
WAKE_AGENT_PORT = int(os.getenv("WAKE_AGENT_PORT", "8123"))
WAKE_AGENT_AUTOSTART = os.getenv("WAKE_AGENT_AUTOSTART", "true").lower() == "true"
# KWS 本地唤醒模型（3M zipformer，中文+英文，仅 ~30MB）
KWS_MODEL_NAME = os.getenv("KWS_MODEL_NAME", "sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20")
# 唤醒 agent 需要拉起主应用时用的桌宠可执行文件路径
# frozen(agent) → 与 agent.exe 同目录的 jarvis2-desktop.exe；dev 可覆盖
WAKE_DESKTOP_EXE = os.getenv("WAKE_DESKTOP_EXE", "")

# ===== 唤醒词（Picovoice Porcupine · 浏览器本地离线检测，已废弃，保留兼容）=====
PICOVOICE_ACCESS_KEY = os.getenv("PICOVOICE_ACCESS_KEY", "")
WAKE_WORD = os.getenv("WAKE_WORD", "jarvis")


def is_dashscope_enabled() -> bool:
    """全局判定：是否配置了阿里云百炼 DashScope key。"""
    return bool(DASHSCOPE_API_KEY)


def is_llm_enabled() -> bool:
    """全局判定：是否配置了任一 LLM 后端 key（DashScope 或 DeepSeek）。"""
    return bool(DASHSCOPE_API_KEY) or bool(DEEPSEEK_API_KEY)


def ai_mode() -> str:
    """当前 LLM 模式：aliyun（DashScope key 在位）→ deepseek → mock。"""
    if DASHSCOPE_API_KEY:
        return "aliyun"
    if DEEPSEEK_API_KEY:
        return "deepseek"
    return "mock"


def is_voice_enabled() -> bool:
    """全局判定：语音识别是否启用。
    - VOICE_ENGINE=local：本地 sherpa-onnx（默认、无需 key）；模型是否就绪由 voice/local.py 判断。
    - VOICE_ENGINE=volcano：需火山 key（新版 key 或旧版 AppId+AccessToken 至少一套有效）。
    仅用于 ASR；TTS 已改用 Edge 神经语音（见 is_tts_enabled）。
    """
    if VOICE_ENGINE == "local":
        return True
    return bool(VOLCANO_API_KEY) or (bool(VOLCANO_APP_ID) and bool(VOLCANO_ACCESS_TOKEN))


def is_tts_enabled() -> bool:
    """全局判定：TTS 是否启用。默认走 Edge 神经语音（免费、无需 key、不耗额度）；
    显式设 TTS_EDGE_ENABLED=false 才关闭。"""
    return TTS_EDGE_ENABLED


def is_wake_enabled() -> bool:
    """全局判定：是否配置了 Picovoice AccessKey（否则前端唤醒开关置灰）。"""
    return bool(PICOVOICE_ACCESS_KEY)
