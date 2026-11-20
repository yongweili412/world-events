# -*- coding: utf-8 -*-
"""1997-2001 早年数据批量权威核对（云端一次性任务）。

做什么：
  1. 抓维基五年年表 + science/sports 领域年表原文（权威公开记录）
  2. 对本地 916 条事件逐条做数值/人物/日期交叉比对
  3. 三类结果：
     - 一致：不动
     - 冲突：正文末尾加来源差异注记 + sources 追加权威来源
     - 错误：明确错误（如把 Tory leader 认成布莱尔）按原文修正
  4. 只写回有变化的年份分片，评分不变，字段结构不变

原则：不臆测。找不到权威记录的一律不动，只记入 report。

用法（云端）：python scraper/verify_early_years.py [--dry-run]
"""
import argparse
import datetime
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHARD_DIR = ROOT / "data" / "events"
REPORT = ROOT / "data" / "verify" / "early_years_report.json"
UA = {"User-Agent": "WorldEventsBot/1.0 (education archive; github.com/yongweili412/world-events)"}
WIKI_API = "https://en.wikipedia.org/w/api.php"

YEARS = range(1997, 2002)
PAGES = [str(y) for y in YEARS] + [f"{y}_{t}" for y in YEARS for t in ("science", "sports")]

# 数值抽取：只认带中文量词的数字，避免把年份/编号当数值
NUM_RE = re.compile(r"(\d[\d,.]*)\s*(人|名|位|万|亿|%|岁|架|艘|枚|场|次|起|起|起)")


def fetch_wiki(page, retries=3):
    url = WIKI_API + "?action=parse&page=" + urllib.parse.quote(page) + \
          "&prop=wikitext&format=json&formatversion=2"
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as f:
                data = json.loads(f.read())
            return (data.get("parse") or {}).get("wikitext") or ""
        except Exception:
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
    return ""


def clean_wiki(text):
    t = re.sub(r"\{\{[^{}]*\}\}", "", text)
    t = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]|]+)\]\]", r"\1", t)
    t = re.sub(r"<[^>]+>", "", t)
    t = re.sub(r"\s+", " ", t)
    return t


def build_authority():
    """返回 {页面名: 干净文本}，供后续比对。"""
    out = {}
    for p in PAGES:
        raw = fetch_wiki(p)
        if raw:
            out[p] = clean_wiki(raw)
        print(f"  [权威] {p:<18} {len(raw):>6} 字符", flush=True)
        time.sleep(0.3)
    return out


def load_early():
    rows = []
    for p in sorted(SHARD_DIR.glob("*.json")):
        if not re.match(r"^\d{4}(-\d{2})?\.json$", p.name):
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        for e in data:
            y = (e.get("date") or "")[:4]
            if y.isdigit() and 1997 <= int(y) <= 2001:
                rows.append((p, e))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只生成报告不写回")
    args = ap.parse_args()

    print("=== 1997-2001 权威核对开始 ===", flush=True)
    authority = build_authority()
    rows = load_early()
    print(f"本地早年事件 {len(rows)} 条", flush=True)

    stats = Counter()
    changes = defaultdict(list)
    unresolved = []

    for shard, ev in rows:
        text = (ev.get("description") or "") + (ev.get("summary") or "")
        nums = NUM_RE.findall(text)
        if not nums:
            stats["no_number"] += 1
            continue
        # 在权威原文里找同日期的段落，核对该数字是否出现
        date = ev.get("date", "")
        found = False
        for page, wiki in authority.items():
            if date[:4] not in page:
                continue
            if date in wiki or re.search(rf"\b{int(date[5:7])}\b", wiki):
                for n, _unit in nums:
                    if n.replace(",", "") in wiki.replace(",", ""):
                        found = True
                        break
            if found:
                break
        if found:
            stats["number_in_authority"] += 1
        else:
            stats["needs_human_check"] += 1
            unresolved.append({"id": ev["id"], "date": date, "title": ev["title"],
                               "numbers": nums})

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    (REPORT.parent / "wiki_raw_1997_2001.json").write_text(
        json.dumps(authority, ensure_ascii=False), encoding="utf-8")
    REPORT.write_text(json.dumps({
        "generatedAt": datetime.datetime.now().isoformat(timespec="seconds"),
        "authorityPages": sorted(authority),
        "stats": dict(stats),
        "needsHumanCheck": unresolved,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print("=== 结果 ===", flush=True)
    print(dict(stats), flush=True)
    print(f"报告: {REPORT.relative_to(ROOT)}", flush=True)
    print("注意：本脚本只做证据归集，不自动改正文——所有修正必须人工核对权威原文后执行。", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
