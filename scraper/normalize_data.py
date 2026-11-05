# -*- coding: utf-8 -*-
"""入库前的数据自愈（幂等）：把"产出侧漏出来的脏字段"在写分片之前统一修好。

为什么需要它：
  2026-10-30 把分类从 13 类收敛到 10 类，10-31 又漏回 13 类——因为收敛只做了
  存量迁移，没有拦住"新写入"。单靠 validate 只能事后报错，靠人记得跑迁移脚本
  也不可靠。本脚本放在 build 之前、每次自动运行，让脏字段无法进入分片。

它修什么（只修可判定的结构性脏数据，不做价值判断）：
  1. category：逐项走 taxonomy.normalize_category，去空/去重/保序，全空回落「其他」
  2. tags：去掉与"本条事件的来源名"或"10 类标准类目名"重复的标签
     （标签位不该承载来源和类目——来源已有 sources 字段，类目已有 category 字段）
  3. 来源 URL：Wikipedia 月度门户页 `YYYY_Month`（实测 404）→ `Month_YYYY`，
     清掉 utm_*/at_*/fbclid 等跟踪参数，同一事件内 URL 去重（见 sources.py）
  4. 地区：别名归一、城市/州 → 国家、region 缺失时回落国家名（见 regions.py；
     不做"关键词猜国家"——实测 91.7% 准确率，错归属比无归属更糟）
  5. 字符串字段两端空白（title/summary/date/time/location.*）
  6. date 里的全角/斜杠写法（2026/10/30、2026.10.30 → 2026-10-30）

它刻意不修什么：
  - decision / archiveValue：decision 由证据层与置信度决定（rules_keep / weak_evidence /
    hard_drop …），不是 archiveValue 的纯函数，脚本无权改写评分结论。
  - 缺失的 aiSummary / 英文残留：属于内容质量，交给 validate 报warning + 翻译流水线。

用法：
    python scraper/normalize_data.py            # 修 data/events.json
    python scraper/normalize_data.py --dry-run  # 只报告，不写回
    python scraper/normalize_data.py --data path/to/events.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from taxonomy import STANDARD_CATEGORIES, normalize_categories  # noqa: E402
from sources import canonicalize_event_sources  # noqa: E402
from regions import normalize_location  # noqa: E402

ROOT = Path(__file__).parent.parent
DEFAULT_DATA = ROOT / "data" / "events.json"

_DATE_SEP = re.compile(r"[./]")


def _clean_date(value: str) -> str:
    """2026/10/30、2026.10.30 → 2026-10-30；只处理形如日期的字符串。"""
    s = (value or "").strip()
    if not s:
        return s
    s = _DATE_SEP.sub("-", s)
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return s


def _clean_tags(tags, source_names: set[str]) -> list[str]:
    out: list[str] = []
    for t in tags or []:
        if not isinstance(t, str):
            continue
        s = t.strip().lstrip("#")
        if not s:
            continue
        if s in STANDARD_CATEGORIES or s in source_names:
            continue
        if s not in out:
            out.append(s)
    return out


def normalize_events(events, *, clean_tags: bool = True):
    """返回 (events, stats)。原地修改传入的列表并给出变更计数。"""
    stats = Counter()
    for ev in events:
        # 1) category
        raw_cats = ev.get("category")
        if isinstance(raw_cats, str):
            raw_cats = [raw_cats]
        new_cats = normalize_categories(raw_cats)
        if new_cats != (raw_cats or []):
            if [c for c in (raw_cats or []) if c not in STANDARD_CATEGORIES]:
                stats["category_fixed"] += 1
            ev["category"] = new_cats

        # 2) tags
        if clean_tags:
            srcs = ev.get("sources") or []
            names = {str(s.get("name")).strip() for s in srcs if s.get("name")}
            old_tags = ev.get("tags") or []
            new_tags = _clean_tags(old_tags, names)
            if new_tags != old_tags:
                stats["tags_fixed"] += 1
                stats["tags_removed"] += max(0, len(old_tags) - len(new_tags))
                ev["tags"] = new_tags

        # 3) 来源 URL：修 Wikipedia 月度门户死链、去跟踪参数、同一事件内去重
        fixed_urls = canonicalize_event_sources(ev)
        if fixed_urls:
            stats["sources_fixed"] += fixed_urls

        # 4) trim
        for key in ("title", "summary", "time"):
            v = ev.get(key)
            if isinstance(v, str) and v != v.strip():
                ev[key] = v.strip()
                stats["trimmed"] += 1

        # 4) date
        d = ev.get("date")
        if isinstance(d, str):
            nd = _clean_date(d)
            if nd != d:
                ev["date"] = nd
                stats["date_fixed"] += 1

        loc = ev.get("location")
        if isinstance(loc, dict):
            for key in ("country", "region", "city"):
                v = loc.get(key)
                if isinstance(v, str) and v != v.strip():
                    loc[key] = v.strip()
            fixed_loc = normalize_location(loc)
            if fixed_loc:
                stats["location_fixed"] += fixed_loc
    return events, stats


def _load(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise SystemExit(f"{path} 顶层不是数组，拒绝处理")
    return data


def _atomic_write(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def main() -> int:
    ap = argparse.ArgumentParser(description="入库前数据自愈（幂等）")
    ap.add_argument("--data", default=str(DEFAULT_DATA), help="events.json 路径")
    ap.add_argument("--dry-run", action="store_true", help="只报告，不写回")
    ap.add_argument("--keep-tags", action="store_true", help="不清理 tags 里的来源名/类目名")
    args = ap.parse_args()

    path = Path(args.data)
    if not path.exists():
        print(f"✗ 找不到 {path}（先用 shard_events.py ensure 从分片重建）")
        return 2

    events = _load(path)
    _, stats = normalize_events(events, clean_tags=not args.keep_tags)

    changed = (stats["category_fixed"] + stats["tags_fixed"] + stats["date_fixed"]
               + stats["sources_fixed"] + stats["location_fixed"])
    print(
        f"归一化 {len(events)} 条：类目修正 {stats['category_fixed']} · "
        f"标签清理 {stats['tags_fixed']} 条（移除 {stats['tags_removed']} 个）· "
        f"来源修正 {stats['sources_fixed']} 处 · "
        f"地区修正 {stats['location_fixed']} 处 · "
        f"日期修正 {stats['date_fixed']} · 去空白 {stats['trimmed']}"
    )
    if changed == 0:
        print("✓ 已是干净状态（幂等，无写入）")
        return 0
    if args.dry_run:
        print("（--dry-run：不写回）")
        return 0
    _atomic_write(path, events)
    print(f"✓ 已写回 {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
