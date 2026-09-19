# -*- coding: utf-8 -*-
"""可追溯性回填：为已有的"基于全文"速览补上原文片段（sourceExcerpt），不改动速览本身。

背景：2026-09-19 审计发现，早期的全文速览没有留档原文依据，事后无法核验数字/事实，
其中还抓到 1 条编造（evt_20260911_080）。新生成的速览已自动留档（见 full_summary_batch.py），
本脚本负责把历史条目尽量补齐。

用法：
    python scraper/attach_excerpts.py 20     # 本轮尝试 20 条（抓取成功率约 30-40%，失败跳过不写）
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from full_summary_batch import fetch_fulltext  # 复用同一套正文抽取

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "events.json")


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    ev = json.load(open(DATA, encoding="utf-8"))
    targets = [e for e in ev
               if e.get("summaryFull") is True and not e.get("sourceExcerpt")
               and (e.get("sources") or [{}])[0].get("url", "").startswith("http")]
    targets.sort(key=lambda e: e.get("date", ""), reverse=True)
    targets = targets[:limit]
    print(f"待补留档: {len(targets)} 条（全库缺留档 {sum(1 for e in ev if e.get('summaryFull') is True and not e.get('sourceExcerpt'))} 条）")
    ok = fail = 0
    for i, e in enumerate(targets, 1):
        url = e["sources"][0]["url"]
        text, _img = fetch_fulltext(url)
        if not text:
            fail += 1
            print(f"  [{i}/{len(targets)}] 抓取失败: {e['title'][:36]}", flush=True)
            time.sleep(1.0)
            continue
        e["sourceExcerpt"] = re.sub(r"\s+", " ", text)[:400]
        e["summaryBasedOn"] = url
        ok += 1
        print(f"  [{i}/{len(targets)}] ✓ 已留档 {len(e['sourceExcerpt'])} 字: {e['title'][:32]}", flush=True)
        time.sleep(1.0)
    json.dump(ev, open(DATA, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    total = sum(1 for e in ev if e.get("sourceExcerpt"))
    print(f"DONE: 成功 {ok}，失败 {fail}，全库带留档 {total} 条")


if __name__ == "__main__":
    main()
