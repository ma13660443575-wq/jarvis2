# -*- coding: utf-8 -*-
"""
voice/local.py — sherpa-onnx 本地离线 ASR
===========================================
- `recognize_wav(wav_bytes)`：16kHz 单声道 PCM WAV → 识别文本（paraformer-zh-small int8）。
- `ensure_models()`：首启从 ghfast.top 镜像下载模型到
    frozen → %APPDATA%\\JARVIS2\\models\\<model>；dev → <ROOT>\\models\\<model>。
- `asr_available()`：sherpa_onnx 可导入 + 模型在位 → 供 health / voice.py 判断可用性。
- 设计原则：sherpa_onnx 与 numpy 均**惰性导入**；模型缺失/下载失败时只返回 503/降级，绝不 500。

模型：sherpa-onnx-paraformer-zh-small-2024-03-09（约 78MB，中文命令识别足够，int8 体积小）。
"""
from __future__ import annotations

import io
import logging
import tarfile
import urllib.request
import wave
from pathlib import Path

from .. import config
from .archive import safe_extract

log = logging.getLogger("jarvis2.voice.local")

# ghfast.top 镜像（已验证国内可达）；直连 GitHub 超时不可靠
_MODEL_BASE = (
    "https://ghfast.top/https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "asr-models/{name}.tar.bz2"
)

_local_asr = None  # 进程内单例


def model_dir() -> Path:
    return Path(config.SHERPA_MODEL_DIR) / config.ASR_MODEL_NAME


def _engine_importable() -> bool:
    try:
        import sherpa_onnx  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _model_onnx() -> Path | None:
    d = model_dir()
    for name in ("model.int8.onnx", "model.onnx"):
        p = d / name
        if p.exists():
            return p
    return None


def asr_available() -> bool:
    """本地引擎是否就绪：sherpa_onnx 可导入 + 模型文件在位。"""
    return _engine_importable() and _model_onnx() is not None


def ensure_models(force: bool = False) -> Path:
    """确保 ASR 模型已下载解压到本地；返回模型目录。下载失败抛 RuntimeError。"""
    d = model_dir()
    if asr_available() and not force:
        return d
    url = _MODEL_BASE.format(name=config.ASR_MODEL_NAME)
    d.parent.mkdir(parents=True, exist_ok=True)
    tarball = d.parent / f"{config.ASR_MODEL_NAME}.tar.bz2"
    log.info("[voice/local] 下载 ASR 模型：%s", url)
    try:
        urllib.request.urlretrieve(url, tarball)
    except Exception as e:  # noqa: BLE001
        tarball.unlink(missing_ok=True)
        raise RuntimeError(f"ASR 模型下载失败（可稍后重试，或设 VOICE_ENGINE=volcano）：{e}") from e
    log.info("[voice/local] 解压模型…")
    try:
        with tarfile.open(tarball, "r:bz2") as tf:
            safe_extract(tf, d.parent)
    finally:
        tarball.unlink(missing_ok=True)
    if not asr_available():
        raise RuntimeError(f"模型解压后仍缺关键文件：{d}")
    return d


def _load_recognizer():
    """惰性加载离线识别器（进程内单例）。"""
    global _local_asr
    if _local_asr is not None:
        return _local_asr
    import sherpa_onnx  # noqa: F401  (惰性：未安装时 asr_available()=False，不会走到这)

    d = ensure_models()
    onnx = _model_onnx()
    tokens = d / "tokens.txt"
    if not tokens.exists():
        raise RuntimeError(f"模型缺 tokens.txt：{tokens}")
    _local_asr = sherpa_onnx.OfflineRecognizer.from_paraformer(
        paraformer=str(onnx),
        tokens=str(tokens),
        num_threads=2,
        sample_rate=16000,
        feature_dim=80,
        decoding_method="greedy_search",
        debug=False,
    )
    return _local_asr


def _resample_pcm(samples, src_rate: int, dst_rate: int = 16000):
    """线性插值重采样（numpy）。"""
    import numpy as np

    if src_rate == dst_rate:
        return samples
    n = int(len(samples) * dst_rate / src_rate)
    x_old = np.linspace(0, 1, num=len(samples), endpoint=False)
    x_new = np.linspace(0, 1, num=n, endpoint=False)
    return np.interp(x_new, x_old, samples).astype(np.float32)


def recognize_wav(wav_bytes: bytes) -> str:
    """输入 16kHz 单声道 PCM WAV 字节 → 识别文本（空串 = 没听清/无人声）。"""
    import numpy as np

    rec = _load_recognizer()
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        rate = wf.getframerate()
        ch = wf.getnchannels()
        raw = wf.readframes(wf.getnframes())
    if ch != 1:
        raise ValueError(f"仅支持单声道音频，收到 {ch} 声道")
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if rate != 16000:
        samples = _resample_pcm(samples, rate, 16000)

    stream = rec.create_stream()
    stream.accept_waveform(16000, samples)
    rec.decode_stream(stream)
    return stream.result.text.strip()
