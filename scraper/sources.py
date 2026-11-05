# -*- coding: utf-8 -*-
"""来源 URL 的规范化：把"能修的死链"修好，把跟踪参数清掉。

为什么需要：
  历史回填里大量引用 Wikipedia 当月事件门户页，URL 写成 `.../2020_March`。
  实测这个格式在 en.wikipedia.org 上一律 404，而 `.../March_2020` 返回 200
  （2020-03、2021-01、2022-06 三个分片共 2,742 处引用，100% 是坏格式）。
  一个"永久存档"如果引用的是 404，等于没有来源。

这里只做**可判定的**改写，不做猜测：
  · Wikipedia 月度门户页 `YYYY_Month` → `Month_YYYY`（已验证存在）
  · 去掉 utm_* / at_* / fbclid / gclid 等跟踪参数（不影响可访问性）
  · 同一事件内 URL 去重（URL 就是来源身份）

不做的事：
  · 不把月度页猜成某一天的日页——事件日期与门户页日期不一定一致，
    宁可给一个月级但真实的页面，也不给一个看似精确的错页面。
  · 不改 http→https（部分站点只有 http）。
"""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# https://en.wikipedia.org/wiki/Portal:Current_events/2020_March
WIKI_PORTAL_RE = re.compile(
    r"^(?P<prefix>https?://(?P<host>[a-z.]*wikipedia\.org)/wiki/Portal:Current_events/)"
    r"(?P<year>\d{4})_(?P<month>[A-Z][a-z]+)$"
)

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "at_medium", "at_campaign", "at_custom1", "at_custom2",
    "fbclid", "gclid", "mc_cid", "mc_eid", "maca", "igshid", "spm",
}


def canonicalize_source_url(url: str) -> str:
    """返回规范化后的 URL；无法解析时原样返回（绝不丢数据）。"""
    raw = (url or "").strip()
    if not raw:
        return raw

    m = WIKI_PORTAL_RE.match(raw)
    if m:
        # 2020_March → March_2020（实测这个格式才存在）
        raw = f"{m.group('prefix')}{m.group('month')}_{m.group('year')}"

    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw
    if not parts.scheme or not parts.netloc:
        return raw

    # 去掉跟踪参数，保留其余查询串（有些站点靠 query 定位文章）
    if parts.query:
        kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                if k.lower() not in TRACKING_PARAMS]
        if len(kept) != len(parse_qsl(parts.query, keep_blank_values=True)):
            raw = urlunsplit((parts.scheme, parts.netloc, parts.path,
                              urlencode(kept, doseq=True), parts.fragment))
    return raw


def canonicalize_event_sources(ev) -> int:
    """就地规范化一个事件的全部来源，返回改动处数（含去重）。"""
    srcs = ev.get("sources") or []
    if not srcs:
        return 0
    changed = 0
    seen = set()
    kept = []
    for s in srcs:
        if not isinstance(s, dict):
            continue
        old = s.get("url") or ""
        new = canonicalize_source_url(old)
        if new != old:
            s["url"] = new
            changed += 1
        key = s.get("url") or ""
        if key and key in seen:
            changed += 1
            continue
        seen.add(key)
        kept.append(s)
    if len(kept) != len(srcs):
        ev["sources"] = kept
    return changed
