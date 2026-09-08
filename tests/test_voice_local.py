# -*- coding: utf-8 -*-
"""
test_voice_local.py — 本地 sherpa-onnx 离线 ASR 集成测试
=========================================================
覆盖阶段 A 验收「本地识别快且准」：
  - /api/voice/asr 走本地引擎（VOICE_ENGINE=local）时，recognize_wav 能识别出正确中文。
  - 模型缺失 / sherpa_onnx 未安装时自动 skip（首次联网下载由 main.py 启动后台完成）。

样本：tests/data/sample_zh.wav（复制自 paraformer-zh-small 模型自带 test_wavs/0.wav）。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from backend import config
from backend.voice import local

DATA_DIR = Path(__file__).resolve().parent / "data"
SAMPLE_WAV = DATA_DIR / "sample_zh.wav"


def _require_voice_engine() -> bool:
    """本地引擎可用性前置：sherpa_onnx 可导入 + 模型在位，缺任一则跳过。"""
    if config.VOICE_ENGINE != "local":
        pytest.skip("VOICE_ENGINE != local，本测试只覆盖本地引擎")
    if importlib.util.find_spec("sherpa_onnx") is None:
        pytest.skip("sherpa_onnx 未安装")
    if not local.asr_available():
        pytest.skip("本地 ASR 模型未就绪（首启联网自动下载）")


def test_asr_recognizes_chinese_command():
    """样本 WAV → 应识别出含『研究』的中文句子（模型自带样本的真实转写）。"""
    _require_voice_engine()
    assert SAMPLE_WAV.exists(), f"样本缺失: {SAMPLE_WAV}"
    text = local.recognize_wav(SAMPLE_WAV.read_bytes())
    assert text, "识别结果为空"
    assert "研究" in text, f"识别结果未命中预期词，得到: {text}"


def test_asr_engine_availability_flag():
    """health 依赖的 asr_available() 在模型就绪时应为 True。"""
    if importlib.util.find_spec("sherpa_onnx") is None:
        pytest.skip("sherpa_onnx 未安装")
    assert local.asr_available() is True
