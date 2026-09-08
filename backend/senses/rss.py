# -*- coding: utf-8 -*-
"""
senses/rss.py — RSS 情报感官
=============================
抓取配置的 RSS 源（config.RSS_FEEDS），返回最新条目。
供「情报扫描」工作流 wf09 生成 I- 采集卡。失败优雅降级。
"""
from __future__ import annotations

import logging

from .. import config

log = logging.getLogger("jarvis2.senses.rss")

DESC = "RSS 情报源（config.RSS_FEEDS）"


def fetch(limit: int = 5, **kwargs) -> dict:
    if not config.RSS_FEEDS:
        return {"ok": False, "error": "未配置 RSS_FEEDS（.env 中以逗号分隔 URL）。"}
    items: list[dict] = []
    for feed_url in config.RSS_FEEDS:
        try:
            items.extend(_parse_feed(feed_url, limit=limit))
        except Exception as e:  # noqa: BLE001
            log.debug("[senses] rss 源 %s 失败: %s", feed_url, e)
    if not items:
        return {"ok": False, "error": "RSS 源均抓取失败或无可解析条目。"}
    items.sort(key=lambda x: x.get("published", ""), reverse=True)
    return {"ok": True, "items": items[:limit]}


def _parse_feed(feed_url: str, limit: int) -> list[dict]:
    import re
    import xml.etree.ElementTree as ET
    import requests

    r = requests.get(feed_url, timeout=10, headers={"User-Agent": "JARVIS2/0.2"})
    r.raise_for_status()
    root = ET.fromstring(r.content)
    out: list[dict] = []
    for item in root.iter("item"):
        title = _etext(item, "title")
        link = _etext(item, "link")
        pub = _etext(item, "pubDate")
        desc = _etext(item, "description")
        desc = re.sub(r"<[^>]+>", "", desc)[:300]
        if title:
            out.append({"title": title, "link": link, "published": pub,
                        "summary": desc, "source": feed_url})
        if len(out) >= limit:
            break
    return out


def _etext(item, tag: str) -> str:
    el = item.find(tag)
    return (el.text or "").strip() if el is not None else ""
