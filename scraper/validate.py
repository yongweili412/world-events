# -*- coding: utf-8 -*-
"""数据体检：校验 data/events.json 的不变量，机器化拦住字段/类目/日期问题。

分两级，避免"一有 warning 就发不出站"：

  errors（默认阻断，退出码 1）——结构性错误，任何一条都不该进分片：
    · JSON 合法、事件数组非空
    · id 唯一且符合 evt_YYYYMMDD_NNN / EVT-XXXXXXXX
    · title / date / category 必备；date 形如 YYYY-MM-DD 且不晚于明天
    · category 只能是 taxonomy.STANDARD_CATEGORIES 里的 10 类
    · sources 非空，且每项都有 url 与 name

  warnings（默认只报，--strict 才阻断）——内容质量，交给流水线慢慢修：
    · 缺 aiSummary / summary 为空 / summaryFull 类型异常
    · 中文红线：title/summary/description/sources/timeline/aiSummary 出现整段英文
    · tags 超过 12 个，或 tags 里混入了来源名 / 类目名
    · 评分类：缺 archiveValue；decision=KEEP 但分值 < 50；decision=DROP 但分值 ≥ 70

  为什么评分不一致只是 warning：decision 由证据层与置信度决定（rules_keep /
  weak_evidence / hard_drop…），不是 archiveValue 的纯函数；页面文案"按分值分三层"
  是简化说法。真正的硬错误是"结构坏了"，评分口径偏差用 warning + 页面澄清处理。

用法：
    python scraper/validate.py              # 阻断 errors
    python scraper/validate.py --strict     # warnings 也阻断（周期性审计用）
    python scraper/validate.py --quiet      # 只输出汇总
    python scraper/validate.py --data x.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from taxonomy import KEEP_LINE, STANDARD_CATEGORIES  # noqa: E402

ROOT = Path(__file__).parent.parent
DEFAULT_DATA = Path(os.environ.get("EVENTS_PATH") or (ROOT / "data" / "events.json"))
ID_RE = re.compile(r"evt_\d{8}_\d{3}|EVT-[0-9A-F]{8}")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 允许保留英文原文的事件（敏感条目/专有名词）。每次新增都要写清原因。
ALLOW_EN = {"evt_20260911_101", "evt_20201107_018", "evt_20261005_060",
            "evt_20260906_084", "evt_20260905_014", "evt_20260930_035",
            "evt_20261007_031"}

MAX_TAGS = 12


def is_en_text(s: str) -> bool:
    if not s:
        return False
    cn = len(re.findall(r"[\u4e00-\u9fff]", s))
    en = len(re.findall(r"[A-Za-z]", s))
    return en > 20 and cn < en * 0.3


def _sample(items, n=5):
    return items[:n]


def collect(events, *, today: date | None = None):
    """返回 (errors, warnings, stats)。纯函数，便于测试。"""
    errors: list[str] = []
    warnings: list[str] = []
    stats = Counter()
    today = today or date.today()
    tomorrow = today + timedelta(days=1)

    if not events:
        return ["事件数组为空"], [], stats

    stats["total"] = len(events)
    ids = [e.get("id") for e in events]
    dup = [i for i, c in Counter(ids).items() if c > 1]
    if dup:
        errors.append(f"重复 id {len(dup)} 个：{_sample(sorted(map(str, dup)))}")
    bad_id = [i for i in ids if not i or not ID_RE.fullmatch(str(i))]
    if bad_id:
        errors.append(f"非法/缺失 id {len(bad_id)} 条：{_sample(bad_id)}")

    no_title, no_date, bad_date, future_date = [], [], [], []
    no_cat, bad_cat, no_src, bad_src = [], [], [], []
    no_sum, bad_sf, bad_tags = [], [], []
    en_hits, tag_pollution = {}, []
    keep_low, drop_high, unscored = [], [], []

    for e in events:
        eid = e.get("id")
        if not (e.get("title") or "").strip():
            no_title.append(eid)

        d = (e.get("date") or "").strip()
        if not d:
            no_date.append(eid)
        elif not DATE_RE.match(d):
            bad_date.append(eid)
        else:
            try:
                if datetime.strptime(d, "%Y-%m-%d").date() > tomorrow:
                    future_date.append(eid)
            except ValueError:
                bad_date.append(eid)

        cats = e.get("category") or []
        if isinstance(cats, str):
            cats = [cats]
        if not cats:
            no_cat.append(eid)
        elif any(c not in STANDARD_CATEGORIES for c in cats):
            bad_cat.append((eid, [c for c in cats if c not in STANDARD_CATEGORIES]))

        srcs = e.get("sources") or []
        if not srcs:
            no_src.append(eid)
        elif any(not (s.get("url") and s.get("name")) for s in srcs):
            bad_src.append(eid)

        if not e.get("aiSummary"):
            no_sum.append(eid)
        sf = e.get("summaryFull")
        if not isinstance(sf, (bool, type(None))):
            bad_sf.append((eid, type(sf).__name__))
        tags = e.get("tags") or []
        if len(tags) > MAX_TAGS:
            bad_tags.append(eid)
        if tags:
            names = {str(s.get("name")).strip() for s in srcs if s.get("name")}
            dirty = [t for t in tags if t in names or t in STANDARD_CATEGORIES]
            if dirty:
                tag_pollution.append((eid, dirty[:3]))

        val = e.get("archiveValue")
        dec = e.get("decision")
        if val is None:
            unscored.append(eid)
        else:
            try:
                v = float(val)
                if dec == "KEEP" and v < 50:
                    keep_low.append((eid, v))
                if dec == "DROP" and v >= KEEP_LINE:
                    drop_high.append((eid, v))
            except (TypeError, ValueError):
                unscored.append(eid)

        if eid in ALLOW_EN:
            continue
        fields = [("title", e.get("title")), ("summary", e.get("summary")),
                  ("description", e.get("description")), ("aiSummary", e.get("aiSummary"))]
        fields += [("sources.title", s.get("title")) for s in srcs]
        fields += [("sources.snippet", s.get("snippet")) for s in srcs]
        fields += [("timeline.text", re.sub(r"^.*?[｜|]", "", t.get("text") or ""))
                   for t in (e.get("timeline") or [])]
        hits = {k for k, v in fields if is_en_text(v)}
        if hits:
            en_hits[eid] = sorted(hits)

    def err(msg, items):
        if items:
            errors.append(f"{msg} {len(items)} 条：{_sample(items)}")

    err("缺 title", no_title)
    err("缺 date", no_date)
    err("date 格式非法", bad_date)
    err("date 晚于明天", future_date)
    err("缺 category", no_cat)
    if bad_cat:
        errors.append(f"非标准类目 {len(bad_cat)} 条：{_sample(bad_cat)}")
    err("缺 sources", no_src)
    err("sources 结构缺失（url/name）", bad_src)

    if no_sum:
        warnings.append(f"缺 aiSummary {len(no_sum)} 条：{_sample(no_sum)}")
    if bad_sf:
        warnings.append(f"summaryFull 类型异常 {len(bad_sf)} 条：{_sample(bad_sf)}")
    if bad_tags:
        warnings.append(f"tags 超过 {MAX_TAGS} 个 {len(bad_tags)} 条：{_sample(bad_tags)}")
    if tag_pollution:
        warnings.append(f"tags 混入来源名/类目名 {len(tag_pollution)} 条：{_sample(tag_pollution)}")
    if en_hits:
        warnings.append(f"英文残留 {len(en_hits)} 条：{_sample(list(en_hits.items()))}")
    if unscored:
        warnings.append(f"缺 archiveValue {len(unscored)} 条：{_sample(unscored)}")
    if keep_low:
        warnings.append(f"decision=KEEP 但分值<50 {len(keep_low)} 条：{_sample(keep_low)}")
    if drop_high:
        warnings.append(f"decision=DROP 但分值≥{KEEP_LINE} {len(drop_high)} 条：{_sample(drop_high)}")

    stats["errors"] = len(errors)
    stats["warnings"] = len(warnings)
    stats["keep"] = sum(1 for e in events if e.get("decision") == "KEEP")
    stats["review"] = sum(1 for e in events if e.get("decision") == "REVIEW")
    stats["drop"] = sum(1 for e in events if e.get("decision") == "DROP")
    return errors, warnings, stats


def main() -> int:
    ap = argparse.ArgumentParser(description="事件库数据体检")
    ap.add_argument("--data", default=str(DEFAULT_DATA))
    ap.add_argument("--strict", action="store_true", help="warnings 也阻断")
    ap.add_argument("--quiet", action="store_true", help="只输出汇总")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    args = ap.parse_args()

    path = Path(args.data)
    if not path.exists():
        print(f"✗ 找不到 {path}（先用 shard_events.py ensure 从分片重建）")
        return 2
    with open(path, "r", encoding="utf-8") as f:
        events = json.load(f)
    if not isinstance(events, list):
        print(f"✗ {path} 顶层不是数组")
        return 2

    errors, warnings, stats = collect(events)
    if args.json:
        print(json.dumps({"stats": dict(stats), "errors": errors, "warnings": warnings},
                         ensure_ascii=False, indent=2))
    else:
        print(f"事件总数 {stats['total']} | KEEP {stats['keep']} / REVIEW {stats['review']} "
              f"/ DROP {stats['drop']} | errors {len(errors)} | warnings {len(warnings)}")
        if not args.quiet:
            for e in errors:
                print("  ✗", e)
            for w in warnings:
                print("  ⚠", w)

    if errors or (args.strict and warnings):
        print("❌ 校验未通过" + ("（--strict：warnings 也算失败）"
                                if args.strict and warnings and not errors else ""))
        return 1
    print("✅ 校验通过：id 唯一、类目合法、日期合规、来源结构完整")
    return 0


if __name__ == "__main__":
    sys.exit(main())
