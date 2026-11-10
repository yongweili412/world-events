# -*- coding: utf-8 -*-
"""站点体检（流式）：不把 12 万条全库载入内存，逐分片累加统计。

为什么需要它（2026-11-10）：
  全库涨到 12 万条后 data/events.json 有 237MB，Python 对象化需要数 GB 内存，
  在本机（可用约 4GB）直接 MemoryError，导致 rescore/build 这类维护脚本跑不动、
  排障只能开云端日志。本脚本按分片逐个读、读完即丢，内存占用只与单个分片有关。

用法：
  python scraper/audit.py            # 完整体检
  python scraper/audit.py --brief    # 只看核心几行

输出：数据规模 / 年份分布 / 质量短板 / 发布物体积与文件数 / 可能的重复标题。
只读，不修改任何数据。
"""
import glob
import gzip
import json
import os
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{int(n)}B"
        n /= 1024


def _pct(a, b):
    return f"{a / b * 100:.1f}%" if b else "-"


def iter_events():
    """逐分片产出事件（生成器，内存只占单片）。"""
    for p in sorted(glob.glob(os.path.join(ROOT, "data", "events", "*.json"))):
        if os.path.basename(p) == "index.json":
            continue
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if isinstance(data, list):
            for ev in data:
                yield ev


def audit(brief=False):
    n = 0
    by_year = Counter()
    single = no_src = img = scored = country = short_desc = 0
    titles = Counter()
    cats = Counter()

    for ev in iter_events():
        n += 1
        srcs = ev.get("sources") or []
        if len(srcs) <= 1:
            single += 1
        if not srcs:
            no_src += 1
        if str(ev.get("image") or "").startswith("http"):
            img += 1
        if isinstance(ev.get("archiveValue"), (int, float)):
            scored += 1
        if (ev.get("location") or {}).get("country"):
            country += 1
        if len((ev.get("description") or "").strip()) < 60:
            short_desc += 1
        d = (ev.get("date") or "")[:4]
        if d:
            by_year[d] += 1
        t = (ev.get("title") or "").strip()
        if t:
            titles[t] += 1
        for c in (ev.get("category") or []):
            cats[c] += 1

    print(f"事件总数: {n:,}")
    if not n:
        print("没有读到事件，检查 data/events/*.json")
        return 1

    early = sum(v for k, v in by_year.items() if k < "2020")
    print(f"年份跨度: {min(by_year)} → {max(by_year)} | 2020 年前 {early:,} ({_pct(early, n)})")
    if not brief:
        print("年份分布:", dict(sorted(by_year.items())))

    print("\n质量指标:")
    print(f"  单来源/无来源 {single:,} ({_pct(single, n)}) | 完全无来源 {no_src:,}")
    print(f"  含封面图     {img:,} ({_pct(img, n)})")
    print(f"  已评分       {scored:,} ({_pct(scored, n)})")
    print(f"  有国家归属   {country:,} ({_pct(country, n)})")
    print(f"  描述<60字    {short_desc:,} ({_pct(short_desc, n)})")
    print(f"  分类数 {len(cats)}:", dict(cats.most_common(10)))

    dup = [(k, v) for k, v in titles.items() if v > 1]
    print(f"\n重复标题: {len(dup)} 组 / 涉及 {sum(v for _, v in dup)} 条")
    for k, v in sorted(dup, key=lambda x: -x[1])[:5]:
        print(f"  {v} 次: {k[:46]}")

    # 发布物体积与文件数（GitHub Pages 部署超时的两个直接因素）
    print("\n发布物（dist 组成）:")
    total_bytes = total_files = 0
    parts = [
        ("e/ 静态详情页", "e/*.html"),
        ("data/m/ 月分片", "data/m/*.json"),
        ("archive-*.html", "archive-*.html"),
    ]
    for label, pattern in parts:
        files = glob.glob(os.path.join(ROOT, pattern))
        b = sum(os.path.getsize(f) for f in files)
        total_bytes += b
        total_files += len(files)
        print(f"  {label:16} {len(files):>6} 个文件  {_human(b):>9}")
    for single_file in ("data/index-lite.json", "index.html", "app.js", "style.css"):
        p = os.path.join(ROOT, single_file)
        if os.path.exists(p):
            b = os.path.getsize(p)
            total_bytes += b
            total_files += 1
            with open(p, "rb") as f:
                gz = len(gzip.compress(f.read(), 6))
            print(f"  {single_file:16} {1:>6} 个文件  {_human(b):>9}  (gzip {_human(gz)})")
    print(f"  {'合计':16} {total_files:>6} 个文件  {_human(total_bytes):>9}")
    return 0


if __name__ == "__main__":
    sys.exit(audit(brief="--brief" in sys.argv))
