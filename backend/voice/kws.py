# -*- coding: utf-8 -*-
"""
voice/kws.py — sherpa-onnx 本地关键词唤醒（KeywordSpotter）
=============================================================
- `ensure_models()`：首启从 ghfast.top 镜像下载 KWS 模型到
    frozen → %APPDATA%\\JARVIS2\\models\\<KWS_MODEL_NAME>；dev → <ROOT>\\models\\<KWS_MODEL_NAME>。
- `build_keywords_file()`：按内置映射生成 keywords 文件（当前支持「贾维斯」「jarvis」；
    其它词需自行按模型词表提供 token 序列，见模块内 KEYWORD_TOKENS）。
- `KwsEngine`：常驻监听包装 —— 逐块喂 16kHz 音频，检测到唤醒词回调。

设计原则：sherpa_onnx 惰性导入；模型缺失/下载失败时仅降级（agent 报 model_ready=False），绝不崩。
"""
from __future__ import annotations

import logging
import tarfile
import urllib.request
from pathlib import Path

from .. import config
from .archive import safe_extract

log = logging.getLogger("jarvis2.voice.kws")

_MODEL_BASE = (
    "https://ghfast.top/https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "kws-models/{name}.tar.bz2"
)

# 内置唤醒词 → 模型词表 token 序列（空格分隔；中文=带声调拼音音节，英文=ARPABET 音素）
# 例：`j iǎ w éi s ī` = 贾维斯；`JH AA1 R V IH0 S` = JARVIS（ARPABET，重音标注）
KEYWORD_TOKENS: dict[str, str] = {
    "贾维斯": "j iǎ w éi s ī",
    "jarvis": "JH AA1 R V IH0 S",
    "JARVIS": "JH AA1 R V IH0 S",
}

_spotter = None  # 进程内单例（agent 常驻）
_kws_stream = None


def model_dir() -> Path:
    return Path(config.SHERPA_MODEL_DIR) / config.KWS_MODEL_NAME


def _engine_importable() -> bool:
    try:
        import sherpa_onnx  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def kws_available() -> bool:
    """KWS 引擎是否就绪：sherpa_onnx 可导入 + 三件套模型文件在位。"""
    if not _engine_importable():
        return False
    d = model_dir()
    return all((d / f).exists() for f in (
        "tokens.txt",
        "encoder-epoch-13-avg-2-chunk-16-left-64.onnx",
        "decoder-epoch-13-avg-2-chunk-16-left-64.onnx",
        "joiner-epoch-13-avg-2-chunk-16-left-64.onnx",
    ))


def ensure_models(force: bool = False) -> Path:
    """确保 KWS 模型已下载解压；返回模型目录。失败抛 RuntimeError。"""
    d = model_dir()
    if kws_available() and not force:
        return d
    url = _MODEL_BASE.format(name=config.KWS_MODEL_NAME)
    d.parent.mkdir(parents=True, exist_ok=True)
    tarball = d.parent / f"{config.KWS_MODEL_NAME}.tar.bz2"
    log.info("[voice/kws] 下载 KWS 模型：%s", url)
    try:
        urllib.request.urlretrieve(url, tarball)
    except Exception as e:  # noqa: BLE001
        tarball.unlink(missing_ok=True)
        raise RuntimeError(f"KWS 模型下载失败（可稍后重试）：{e}") from e
    log.info("[voice/kws] 解压模型…")
    try:
        with tarfile.open(tarball, "r:bz2") as tf:
            safe_extract(tf, d.parent)
    finally:
        tarball.unlink(missing_ok=True)
    if not kws_available():
        raise RuntimeError(f"KWS 模型解压后仍缺关键文件：{d}")
    return d


def build_keywords_file() -> Path:
    """生成/刷新 keywords 文件（agent 启动时调用）。按 WAKE_WORDS 逐词映射 token。

    仅支持 KEYWORD_TOKENS 中收录的词；未收录的词会被忽略并打日志。
    """
    d = model_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / "keywords.txt"
    lines: list[str] = []
    seen: set[str] = set()
    for raw in config.WAKE_WORDS.split(","):
        word = raw.strip()
        if not word or word in seen:
            continue
        seen.add(word)
        tokens = KEYWORD_TOKENS.get(word)
        if not tokens:
            log.warning("[voice/kws] 唤醒词「%s」未收录 token 映射，跳过。可用：%s",
                        word, ", ".join(KEYWORD_TOKENS))
            continue
        lines.append(f"{tokens} @{word}")
    if not lines:
        # 兜底：至少保留默认词，避免空文件导致 spotter 构建失败
        lines.append("j iǎ w éi s ī @贾维斯")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.info("[voice/kws] keywords 文件已生成：%s（%d 词）", path, len(lines))
    return path


def _load_spotter():
    """惰性加载 KeywordSpotter（进程内单例）。依赖 keywords 文件已生成。"""
    global _spotter
    if _spotter is not None:
        return _spotter
    import sherpa_onnx  # noqa: F401  (惰性)

    d = ensure_models()
    kw_file = build_keywords_file()
    _spotter = sherpa_onnx.KeywordSpotter(
        tokens=str(d / "tokens.txt"),
        encoder=str(d / "encoder-epoch-13-avg-2-chunk-16-left-64.onnx"),
        decoder=str(d / "decoder-epoch-13-avg-2-chunk-16-left-64.onnx"),
        joiner=str(d / "joiner-epoch-13-avg-2-chunk-16-left-64.onnx"),
        keywords_file=str(kw_file),
        num_threads=2,
        sample_rate=16000,
        feature_dim=80,
        max_active_paths=4,
        provider="cpu",
    )
    return _spotter


def reset_stream():
    """供 agent 在检测到唤醒词后调用，重置流以继续监听下一轮。"""
    global _kws_stream
    if _spotter is not None and _kws_stream is not None:
        _spotter.reset_stream(_kws_stream)


class KwsEngine:
    """常驻关键词监听引擎。

    用法（agent 麦克风线程）::

        eng = KwsEngine(on_keyword=lambda label: ...)
        eng.open()              # 构建 spotter + 流
        while eng.active:
            eng.feed(samples)   # 16kHz float32 1D 数组
        eng.close()
    """

    def __init__(self, on_keyword) -> None:
        self.on_keyword = on_keyword
        self._spotter = None
        self._stream = None
        self.active = False

    def open(self) -> bool:
        """加载引擎。返回是否就绪。"""
        if not kws_available():
            try:
                ensure_models()
            except RuntimeError as e:
                log.warning("[voice/kws] 引擎未就绪: %s", e)
                return False
        global _spotter, _kws_stream
        self._spotter = _load_spotter()
        self._stream = self._spotter.create_stream()
        _spotter = self._spotter
        _kws_stream = self._stream
        self.active = True
        return True

    def feed(self, samples):
        """喂一段 16kHz float32 音频（约 100ms）；内部完成解码与关键词判定。"""
        if not self.active or self._spotter is None or self._stream is None:
            return
        self._stream.accept_waveform(16000, samples)
        while self._spotter.is_ready(self._stream):
            self._spotter.decode_stream(self._stream)
            r = self._spotter.get_result(self._stream)
            if r:
                # r 形如 "JARVIS"（@ 后的标签）
                label = str(r)
                self._spotter.reset_stream(self._stream)
                try:
                    self.on_keyword(label)
                except Exception:  # noqa: BLE001
                    log.exception("[voice/kws] on_keyword 异常")

    def close(self):
        self.active = False
