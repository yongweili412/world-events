# -*- coding: utf-8 -*-
"""逐日存档验收：检查目标年份各月产出条数是否达标。

背景（2026-09-15 事故）：免费 flash 账户限流导致翻译大面积静默失败，
run 显示 success 但每月只剩 9-20 条（正常 100-500 条）。
本脚本用于每次回填后自动验收，不达标即报警（退出码 1），
可接入 workflow 步骤实现"产出不达标即失败告警"。

用法：python scraper/verify_daily_backfill.py [最低条数阈值，默认 50]
"""
import collections
import json
import sys

MIN_PER_MONTH = int(sys.argv[1]) if len(sys.argv) > 1 else 50


def main():
    ev = json.load(open("data/events.json", encoding="utf-8"))
    daily = [e for e in ev if any("当日新闻存档" in (s.get("name") or "") for s in (e.get("sources") or []))]
    cnt = collections.Counter(e["date"][:7] for e in daily)

    try:
        cfg = json.load(open("data/backfill_daily_targets.json", encoding="utf-8"))
        years = [str(y) for y in cfg.get("years", [])]
    except Exception:
        years = []
    try:
        prog = json.load(open("data/backfill_daily_progress.json", encoding="utf-8"))
        done = set(prog.get("done_months", []))
    except Exception:
        done = set()

    print(f"验收范围: {years} | 已完成月份: {len(done)} | 逐日事件合计: {len(daily)}")
    problems = []
    for y in years:
        for m in range(1, 13):
            ym = f"{y}-{m:02d}"
            if ym not in done:
                continue  # 未标记完成的不算问题（可能还没跑）
            n = cnt.get(ym, 0)
            if n < MIN_PER_MONTH:
                problems.append((ym, n))

    if problems:
        print(f"\n⚠️ {len(problems)} 个已标记完成的月份产出异常（<{MIN_PER_MONTH} 条）:")
        for ym, n in problems:
            print(f"  {ym}: {n} 条")
        print("\n可能原因：API 限流导致翻译静默失败 / 抓取失败。请查日志并按需重跑（从 progress 移除该月）。")
        sys.exit(1)
    print(f"\n✅ 所有已完成月份产出达标（≥{MIN_PER_MONTH} 条/月）")


if __name__ == "__main__":
    main()
