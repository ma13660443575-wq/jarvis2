# -*- coding: utf-8 -*-
"""
postprocess.py — ASR 识别结果后处理（参考 MyType-voice-input 的思路）
====================================================================
火山 ASR 识别出的文本常带两类「噪音」，本模块在不耗任何额度的前提下把它磨干净：
  1. 语气词 / 填充词（嗯、啊、就是说、然后、对吧…）→ 去除，尤其首尾
  2. 个人词库纠错（同音 / 口语误识别）→ 校正为规范词，便于命令触发与后续 RAG

用法（在 voice.py 的 ASR 路由里，火山返回文本后调用一次）：
    text = postprocess.refine_asr(text)

规则设计原则：宁可少删，不可误删——
  - 语气短语分两档：
      「宽松」= 话语连接词（就是/然后/对吧…），后面直接跟正文也剥（"就是帮我处理…"）
      「严格」= 指代词（这个/那个…），必须后随标点/空白/结尾才剥（"这个项目" 不误删）
  - 词库替换按「最长优先」逐条替换，避免长词被短词抢先
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("jarvis2.voice")

# ---- 单字语气词：出现在首尾时剥离 ----
_FILLER_CHARS = "嗯啊呃哦唔额恩"

# ---- 语气短语：宽松档（话语连接词，直接跟正文也剥）----
_FILLER_LOOSE = sorted([
    "就是说", "所以说", "就这样", "然后呢", "那么",
    "就是", "然后", "对吧", "是吧", "来着", "反正", "嗯哼",
], key=len, reverse=True)

# ---- 语气短语：严格档（指代词，必须跟标点/空白/结尾才剥，防误删"这个项目"）----
_FILLER_STRICT = sorted([
    "那个啥", "那啥", "那个", "这个",
], key=len, reverse=True)

# 分隔符集合（中文标点 + 英文标点 + 空白）
_PUNC = "，。！？；、,.!?;: \t\n"

# ---- 确认词（对对对 / 好好好）：边界连续重复时移除 ----
_CONFIRM_CHARS = "对是好行"

# ---- 个人词库：口语 / 同音误识别 → 规范词（按 key 长度降序替换） ----
_LEXICON = {
    # JARVIS 命令（语音常被误识别）
    "处理才吉乡": "处理采集箱", "处理菜鸡箱": "处理采集箱", "处理踩机箱": "处理采集箱",
    "才吉乡": "采集箱", "菜鸡箱": "采集箱", "踩机箱": "采集箱", "采集乡": "采集箱",
    "知识提去": "知识提取", "只是提取": "知识提取", "知识踢取": "知识提取",
    "消化挤呀": "消化积压", "消化积呀": "消化积压",
    # 领域词
    "工艺功法": "工艺工法", "公益功法": "工艺工法", "绿建小爱": "绿建小艾",
    # 品牌 / 工具名（口语发音）
    "贾维斯": "JARVIS", "扎维斯": "JARVIS", "家维斯": "JARVIS",
    "奥西迪安": "Obsidian", "地普西克": "DeepSeek", "帝普西克": "DeepSeek",
}

_LEXICON_KEYS = sorted(_LEXICON, key=len, reverse=True)


def _strip_fillers(text: str) -> str:
    """去除首尾及边界语气词。返回清理后文本（正文不受影响）。"""
    # 1) 折叠连续重复语气字：嗯嗯嗯 → 嗯
    text = re.sub(r"([嗯啊呃哦唔额恩])\1+", r"\1", text)

    # 2) 首尾循环剥离（直到稳定）：单字语气词 ↔ 标点 ↔ 短语 交替出现也能剥干净
    while True:
        orig = text
        # 剥边界单字语气词 + 标点
        text = text.strip(_FILLER_CHARS).strip(_PUNC)
        # 剥边界连续确认词：开头需后随标点/结尾（"好好做"不误删）；结尾直接剥
        text = re.sub(rf"^([{_CONFIRM_CHARS}])\1{{1,}}(?=[{_PUNC}]|$)", "", text)
        text = re.sub(rf"([{_CONFIRM_CHARS}])\1{{1,}}$", "", text)
        # 宽松短语（直接跟正文也剥，如"就是帮我…"）
        for p in _FILLER_LOOSE:
            if text.startswith(p):
                text = text[len(p):].strip(_PUNC)
            if text.endswith(p):
                text = text[:-len(p)].strip(_PUNC)
        # 严格短语（必须跟标点/空白/结尾，"这个项目"保留）
        for p in _FILLER_STRICT:
            if text.startswith(p) and (len(text) == len(p) or text[len(p)] in _PUNC):
                text = text[len(p):].strip(_PUNC)
            if text.endswith(p) and (len(text) == len(p) or text[-len(p) - 1] in _PUNC):
                text = text[:-len(p)].strip(_PUNC)
        if text == orig:
            break
    return text.strip(_PUNC)


def _apply_lexicon(text: str) -> str:
    """个人词库纠错：按 key 长度降序逐条替换（最长优先，避免短词抢先）。"""
    out = text
    for key in _LEXICON_KEYS:
        if key in out:
            out = out.replace(key, _LEXICON[key])
    return out


def refine_asr(text: str) -> str:
    """完整后处理：语气词过滤 → 词库纠错 → 空白归并。空串原样返回。"""
    if not text:
        return text
    out = _strip_fillers(text)
    out = _apply_lexicon(out)
    out = re.sub(r"\s+", " ", out).strip()
    if out != text:
        log.info("[asr/refine] %r → %r", text, out)
    return out


if __name__ == "__main__":  # 自测：python -m backend.voice.postprocess
    cases = [
        ("嗯，就是帮我处理才吉乡吧", "帮我处理采集箱吧"),
        ("那个，这个项目怎么样", "这个项目怎么样"),
        ("然后然后然后就是知识提去", "知识提取"),
        ("对对对，工艺功法的消化挤呀", "工艺工法的消化积压"),
        ("贾维斯，情报扫描", "JARVIS，情报扫描"),
        ("嗯嗯，啊那个啥来着", ""),
        ("这个项目很关键", "这个项目很关键"),          # 防误删
        ("意思是就是说我们要上线", "意思是就是说我们要上线"),  # 句中"就是说"不删
        ("好好做，帮我处理采集箱", "好好做，帮我处理采集箱"),  # 确认词后跟正文不删
        ("帮我复习会话", "帮我复习会话"),
        ("我要做知识回炉", "我要做知识回炉"),
        ("系统体检", "系统体检"),
    ]
    failed = 0
    for i, (raw, expect) in enumerate(cases, 1):
        got = refine_asr(raw)
        ok = got == expect
        failed += 0 if ok else 1
        print(f"[{i}] {'PASS' if ok else 'FAIL'} raw={raw!r}")
        if not ok:
            print(f"     expect={expect!r}")
            print(f"     got   ={got!r}")
    print(f"\n== {len(cases) - failed}/{len(cases)} passed ==")
    raise SystemExit(1 if failed else 0)
