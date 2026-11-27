# -*- coding: utf-8 -*-
"""为事件生成 relatedEvents（相关事件）。

2026-11-28：全库 13 万条 relatedEvents 全为空——产品主张「事件而非新闻」，
却没有事件关联，用户看完一条就断了。

规则（保守，宁缺勿滥）：
  1. 同日期 + 同分类                     → 强关联
  2. 同日期 + 同国家                     → 强关联
  3. 前后 1 天 + 同分类 + 同国家         → 弱关联
每条最多 4 个，排除自身；只处理 archiveValue >= 50 且 relatedEvents 为空的条目。

用法：
  python scraper/link_related.py --dry-run    # 只统计，不写
  python scraper/link_related.py --apply      # 写回分片
"""
import argparse
import collections
import glob
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHARD_DIR = os.path.join(ROOT, "data", "events")
MIN_VALUE = 50
MAX_LINKS = 3


def cat_of(e):
    c = e.get("category") or []
    return c[0] if c else ""


def country_of(e):
    return ((e.get("location") or {}).get("country") or "").strip()


def load():
    files, rows = {}, []
    for p in sorted(glob.glob(os.path.join(SHARD_DIR, "*.json"))):
        name = os.path.basename(p)[:-5]
        if not re.match(r"^\d{4}(-\d{2})?$", name):
            continue
        data = json.load(open(p, encoding="utf-8"))
        files[name] = data
        rows.extend(data)
    return files, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if not args.dry_run and not args.apply:
        ap.error("需要 --dry-run 或 --apply")

    files, rows = load()
    print(f"全库 {len(rows)} 条")

    # 倒排索引
    by_date = collections.defaultdict(list)
    by_date_cat = collections.defaultdict(list)
    by_date_country = collections.defaultdict(list)
    for e in rows:
        d = (e.get("date") or "")[:10]
        if not d:
            continue
        by_date[d].append(e)
        c, co = cat_of(e), country_of(e)
        if c:
            by_date_cat[(d, c)].append(e["id"])
        if co:
            by_date_country[(d, co)].append(e["id"])

    def neighbors(e):
        d = (e.get("date") or "")[:10]
        c, co = cat_of(e), country_of(e)
        picked = []
        # 优先级：同日+同分类+同国家 → 同日+同分类 → 同日+同国家
        cat_ids = by_date_cat.get((d, c), [])
        cty_ids = by_date_country.get((d, co), [])
        for cid in cat_ids:
            if cid != e["id"] and cid in cty_ids:
                picked.append(cid)
        for cid in cat_ids:
            if cid != e["id"] and cid not in picked:
                picked.append(cid)
        for cid in cty_ids:
            if cid != e["id"] and cid not in picked:
                picked.append(cid)
        # 弱关联：前后一天 + 同分类 + 同国家
        if len(picked) < MAX_LINKS and c and co:
            import datetime
            try:
                base = datetime.date.fromisoformat(d)
            except ValueError:
                base = None
            if base:
                for delta in (-1, 1):
                    nd = (base + datetime.timedelta(days=delta)).isoformat()
                    for cid in by_date_cat.get((nd, c), []):
                        if cid != e["id"] and cid not in picked:
                            picked.append(cid)
        return picked[:MAX_LINKS]

    changed = 0
    filled = 0
    dist = collections.Counter()
    for e in rows:
        if e.get("relatedEvents"):
            continue
        if (e.get("archiveValue") or 0) < MIN_VALUE:
            continue
        picked = neighbors(e)
        if not picked:
            dist["无关联"] += 1
            continue
        dist[f"{len(picked)} 个"] += 1
        if args.apply:
            e["relatedEvents"] = picked
            changed += 1
        filled += 1

    print(f"可生成关联的条目: {filled}（分布 {dict(dist)}）")
    if args.dry_run:
        print("DRY-RUN：未写回。样例：")
        for e in rows[:2000]:
            if e.get("relatedEvents"):
                print(" ", e["id"], "→", e["relatedEvents"])
                break
        return 0

    for name, data in files.items():
        open(os.path.join(SHARD_DIR, name + ".json"), "w", encoding="utf-8").write(
            json.dumps(data, ensure_ascii=False, indent=2))
    print(f"✅ 已写回 {changed} 条 relatedEvents（{len(files)} 个分片）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
