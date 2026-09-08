# -*- coding: utf-8 -*-
"""
backend/wake_agent.py — 全局后台唤醒 agent（独立常驻进程入口）
================================================================
与主应用解耦的轻量进程：常驻麦克风监听「JARVIS / 贾维斯」→ 录音 → 本地 ASR
识别命令 → 执行（后端在线直发，离线拉起主应用）→ 回复（HUD 未开时代读）。

状态机：off → listening → armed → recording → asr → executing → listening

HTTP 服务 127.0.0.1:8123：
  GET  /status         {state, enabled, paused, backend_port, model_ready, last_command, last_reply, t}
  POST /register       {backend_port}  主应用后端启动时上报实际端口
  POST /set_enabled    {enabled}       开/关常驻监听
  POST /pause /resume  PTT 录音期间暂停/恢复（防双麦冲突）

运行方式：
  dev：    python backend/wake_agent.py
  打包：   jarvis2-wake-agent.spec → jarvis2-wake-agent.exe
"""
from __future__ import annotations

import io
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

# ---- 使非 frozen 直接运行时能找到 backend 包 ----
if not getattr(sys, "frozen", False):
    _ROOT = Path(__file__).resolve().parent.parent
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))

from backend import config  # noqa: E402
from backend.voice import edge, kws, local  # noqa: E402

log = logging.getLogger("jarvis2.wake-agent")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ===== 音频/检测参数 =====
SAMPLE_RATE = 16000
BLOCK = int(0.1 * SAMPLE_RATE)          # 100ms
RING_CAP = int(0.8 * SAMPLE_RATE)       # 唤醒前滚动缓冲 800ms
ARMED_TIMEOUT = 5.0                     # 唤醒后 5s 无话回 listening
SPEECH_STREAK = 2                       # 连续 ≥2 帧确认说话
SPEECH_THRESH = 0.030                   # float32 RMS 阈值（≈1000/32768）
SPEECH_MIN_SAMPLES = int(0.25 * SAMPLE_RATE)
SILENCE_SAMPLES = int(0.7 * SAMPLE_RATE)
MAX_SAMPLES = int(15 * SAMPLE_RATE)


class _State:
    """agent 全局运行态（GIL 原子性足够：mode 为字符串、缓冲只由麦克风线程写）。"""

    def __init__(self) -> None:
        self.mode = "off"
        self.enabled = config.WAKE_ENABLED
        self.paused = False
        self.shutdown = False
        self.backend_port: int | None = None
        self.model_ready = False
        self.last_command: str | None = None
        self.last_reply: str | None = None
        # 麦克风线程缓冲
        self.ring: list = []
        self.ring_len = 0
        self.rec: list = []
        self.rec_len = 0
        self.speech_streak = 0
        self.armed_speech = 0
        self.silence = 0
        self.armed_at = 0.0
        self._rec_audio: np.ndarray | None = None

    def snapshot(self) -> dict:
        return {
            "state": self.mode,
            "enabled": self.enabled,
            "paused": self.paused,
            "backend_port": self.backend_port,
            "model_ready": self.model_ready,
            "last_command": self.last_command,
            "last_reply": self.last_reply,
            "t": time.strftime("%H:%M:%S"),
        }


state = _State()


# ==================== 状态机 ====================

def _push_ring(samples: np.ndarray) -> None:
    state.ring.append(samples)
    state.ring_len += samples.size
    while state.ring_len > RING_CAP:
        state.ring_len -= state.ring[0].size
        state.ring.pop(0)


def _reset_to_listening() -> None:
    state.mode = "listening"
    state.rec = []
    state.rec_len = 0
    state.speech_streak = 0
    state.armed_speech = 0
    state.silence = 0


def _is_speech(samples: np.ndarray) -> bool:
    if samples.size == 0:
        return False
    rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
    return rms > SPEECH_THRESH


def _merge(blocks: list) -> np.ndarray:
    total = sum(b.size for b in blocks)
    out = np.empty(total, dtype=np.float32)
    off = 0
    for b in blocks:
        out[off:off + b.size] = b
        off += b.size
    return out


def _encode_wav(samples: np.ndarray, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes((np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes())
    return buf.getvalue()


def _on_keyword(label: str) -> None:
    """KWS 检测到唤醒词（仅在 listening 态生效）。"""
    if state.mode != "listening" or state.paused:
        return
    log.info("[agent] 唤醒词：%s", label)
    state.mode = "armed"
    state.armed_at = time.time()
    state.rec = list(state.ring)  # 从滚动缓冲起录（含唤醒词尾巴）
    state.rec_len = sum(b.size for b in state.ring)
    state.speech_streak = 0
    state.armed_speech = 0
    state.silence = 0


def _on_armed_block(samples: np.ndarray) -> None:
    state.rec.append(samples)
    state.rec_len += samples.size
    if time.time() - state.armed_at > ARMED_TIMEOUT:
        _reset_to_listening()
        return
    if _is_speech(samples):
        state.speech_streak += 1
        state.armed_speech += samples.size
        if state.speech_streak >= SPEECH_STREAK and state.armed_speech >= SPEECH_MIN_SAMPLES:
            state.mode = "recording"
            state.silence = 0
    else:
        state.speech_streak = 0
        state.armed_speech = 0


def _on_recording_block(samples: np.ndarray) -> None:
    state.rec.append(samples)
    state.rec_len += samples.size
    if _is_speech(samples):
        state.silence = 0
    else:
        state.silence += samples.size
        if state.silence >= SILENCE_SAMPLES:
            _finalize()
            return
    if state.rec_len >= MAX_SAMPLES:
        _finalize()


def _finalize() -> None:
    state.mode = "asr"
    state._rec_audio = _merge(state.rec)
    state.rec = []
    state.rec_len = 0
    threading.Thread(target=_asr_and_execute, daemon=True).start()


def _process_block(samples: np.ndarray) -> None:
    m = state.mode
    if m == "listening":
        _push_ring(samples)
    elif m == "armed":
        _on_armed_block(samples)
    elif m == "recording":
        _on_recording_block(samples)
    # asr/executing/off：丢弃（用户已完成本轮指令）


# ==================== 麦克风循环 ====================

def _mic_listen_once() -> None:
    """打开麦克风并监听一轮，直到被禁用/关闭。"""
    import sounddevice as sd

    eng = kws.KwsEngine(on_keyword=_on_keyword)
    if not eng.open():
        state.model_ready = False
        log.warning("[agent] KWS 引擎未就绪，5s 后重试")
        time.sleep(5.0)
        return
    state.model_ready = True
    if state.mode == "off":
        state.mode = "listening"
    log.info("[agent] 常驻监听中…（JARVIS / 贾维斯）")
    try:
        with sd.InputStream(channels=1, dtype="float32", samplerate=SAMPLE_RATE) as s:
            while state.enabled and not state.shutdown:
                samples, _ = s.read(BLOCK)
                samples = samples.reshape(-1)
                if state.paused:
                    # PTT 录音期间：丢弃输入，避免误触发；同时把状态机归位
                    if state.mode in ("armed", "recording"):
                        _reset_to_listening()
                    continue
                if state.mode == "listening":
                    _push_ring(samples)
                    eng.feed(samples)
                else:
                    _process_block(samples)
    finally:
        eng.close()


def _mic_thread_main() -> None:
    while not state.shutdown:
        if not state.enabled:
            state.mode = "off"
            time.sleep(0.3)
            continue
        try:
            _mic_listen_once()
        except Exception:  # noqa: BLE001
            log.exception("[agent] 麦克风循环异常，3s 后重试")
            time.sleep(3.0)


# ==================== 执行 + 回复 ====================

def _get_backend() -> int | None:
    port = state.backend_port
    if port:
        try:
            import urllib.request
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=2).read()
            return port
        except Exception:  # noqa: BLE001
            state.backend_port = None
    return None


def _desktop_exe() -> str:
    if config.WAKE_DESKTOP_EXE:
        return config.WAKE_DESKTOP_EXE
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).parent / "jarvis2-desktop.exe")
    return str(Path(__file__).resolve().parent.parent / "desktop" / "src-tauri" / "target" / "release" / "jarvis2-desktop.exe")


def _launch_desktop() -> bool:
    exe = _desktop_exe()
    if not os.path.exists(exe):
        log.warning("[agent] 未找到主应用：%s", exe)
        return False
    log.info("[agent] 拉起主应用：%s", exe)
    try:
        subprocess.Popen([exe], close_fds=True)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("[agent] 拉起主应用失败: %s", e)
        return False


def _wait_backend(timeout: float = 60.0) -> int | None:
    """等待主应用后端启动并注册端口（启动后 backend_entry 会上报）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        b = _get_backend()
        if b:
            return b
        time.sleep(0.5)
    return None


def _execute(cleaned: str) -> str:
    """执行命令：后端在线直发，离线拉起主应用再发。返回回复文本。"""
    import urllib.request

    backend = _get_backend()
    if backend is None:
        if _launch_desktop():
            backend = _wait_backend(60)
        if backend is None:
            return "主应用没有起来，请稍后手动打开 JARVIS 再试。"
    body = json_dumps({"text": cleaned, "source": "wake"})
    req = urllib.request.Request(
        f"http://127.0.0.1:{backend}/api/wake/command",
        data=body.encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json_loads(resp.read())
        if data.get("ok"):
            return (data["data"].get("reply") or "好的，已处理。").strip()
        return data.get("detail") or "执行失败。"
    except Exception as e:  # noqa: BLE001
        return f"执行出错：{e}"


def _mci_play(mp3: str) -> None:
    """用 Windows MCI 播放 mp3（免额外依赖，适合 onefile exe）。"""
    import ctypes

    winmm = ctypes.windll.winmm
    ALIAS = "jarvis_tts"
    out = ctypes.create_unicode_buffer(256)
    winmm.mciSendStringW(f'open "{mp3}" type mpegvideo alias {ALIAS}', out, 256, None)
    winmm.mciSendStringW(f"play {ALIAS} wait", out, 256, None)
    winmm.mciSendStringW(f"close {ALIAS}", out, 256, None)


def _play_tts(text: str) -> None:
    if not text or not text.strip():
        return
    try:
        audio = edge.tts_synthesize(text)
    except Exception as e:  # noqa: BLE001
        log.warning("[agent] 语音合成失败: %s", e)
        return
    if not audio:
        return
    fd, path = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    try:
        with open(path, "wb") as f:
            f.write(audio)
        _mci_play(path)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _speak_if_needed(reply: str) -> None:
    """HUD 打开时前端会收到 wake SSE 自己朗读，agent 不代读；否则 agent 朗读。"""
    if not reply:
        return
    try:
        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:{state.backend_port}/api/wake/hud", timeout=2) as resp:
            data = json_loads(resp.read())
        if data.get("data", {}).get("open"):
            return
    except Exception:  # noqa: BLE001
        pass
    _play_tts(reply)


def _asr_and_execute() -> None:
    try:
        wav = _encode_wav(state._rec_audio, SAMPLE_RATE)
        text = local.recognize_wav(wav)
    except Exception as e:  # noqa: BLE001
        log.warning("[agent] ASR 失败: %s", e)
        text = ""
    text = (text or "").strip()
    if not text:
        log.info("[agent] 没听清，回到待命")
        _reset_to_listening()
        return
    state.last_command = text
    state.mode = "executing"
    log.info("[agent] 识别指令：%s", text)
    cleaned = re.sub(r"^\s*(jarvis|贾维斯)[，,\s]*", "", text, flags=re.I).strip()
    reply = _execute(cleaned or text)
    state.last_reply = reply
    log.info("[agent] 回复：%s", reply[:120])
    _speak_if_needed(reply)
    _reset_to_listening()


# ==================== HTTP 服务 ====================

def json_dumps(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)


def json_loads(raw) -> dict:
    import json
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    return json.loads(raw)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 静音访问日志
        pass

    def _send(self, obj: dict, code: int = 200) -> None:
        body = json_dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/status":
            self._send({"ok": True, "data": state.snapshot()})
        else:
            self._send({"ok": False, "detail": "not found"}, 404)

    def do_POST(self) -> None:
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
            body = json_loads(self.rfile.read(n)) if n else {}
        except Exception:  # noqa: BLE001
            body = {}
        p = self.path
        if p == "/register":
            if body.get("backend_port"):
                state.backend_port = int(body["backend_port"])
                log.info("[agent] 后端注册：端口 %s", state.backend_port)
            self._send({"ok": True, "data": {"registered": True}})
        elif p == "/set_enabled":
            state.enabled = bool(body.get("enabled", True))
            if not state.enabled:
                state.mode = "off"
            self._send({"ok": True, "data": {"enabled": state.enabled}})
        elif p == "/pause":
            state.paused = True
            if state.mode in ("armed", "recording"):
                _reset_to_listening()
            self._send({"ok": True})
        elif p == "/resume":
            state.paused = False
            self._send({"ok": True})
        else:
            self._send({"ok": False, "detail": "not found"}, 404)


# ==================== 自启 ====================

def _register_autostart() -> None:
    """首启写 HKCU Run 自启（仅 frozen；dev 不注册）。幂等。"""
    if not config.WAKE_AGENT_AUTOSTART or not getattr(sys, "frozen", False):
        return
    import winreg
    exe = f'"{sys.executable}"'
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_SET_VALUE,
        ) as k:
            winreg.SetValueEx(k, "JARVIS2-WakeAgent", 0, winreg.REG_SZ, exe)
        log.info("[agent] 已注册开机自启")
    except OSError as e:
        log.warning("[agent] 自启注册失败: %s", e)


# ==================== 入口 ====================

def main() -> None:
    _register_autostart()
    # 桌面环境无控制台时，stdout 可能为 None（agent 也走 --noconsole）
    if sys.stdout is None:
        try:
            sys.stdout = os.fdopen(1, "w", encoding="utf-8", errors="replace")
        except OSError:
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", config.WAKE_AGENT_PORT), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    log.info("[agent] HTTP 服务：127.0.0.1:%d", config.WAKE_AGENT_PORT)

    threading.Thread(target=_mic_thread_main, daemon=True).start()
    log.info("[agent] wake-agent 已启动（enabled=%s）", state.enabled)

    try:
        while not state.shutdown:
            time.sleep(1.0)
    except KeyboardInterrupt:
        state.shutdown = True


if __name__ == "__main__":
    main()
