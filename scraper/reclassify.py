#!/usr/bin/env python3
"""一次性工具：按最新分类规则重新给已有事件分类（不重新抓取）"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from scraper import EVENTS_FILE, guess_category, guess_region


def main():
    with open(EVENTS_FILE, "r", encoding="utf-8") as f:
        events = json.load(f)

    changed = 0
    for ev in events:
        # 手动录入的示例事件（evt- 开头）不重新分类
        if ev.get("id", "").startswith("evt-"):
            continue
        new_cat = guess_category(ev["title"], ev.get("summary", "") or ev.get("content", ""))
        new_reg = guess_region(ev["title"], ev.get("summary", "") or ev.get("content", ""))
        if new_cat != ev.get("category") or new_reg != ev.get("region"):
            changed += 1
            ev["category"] = new_cat
            ev["region"] = new_reg

    with open(EVENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False, indent=2)

    # 统计分布
    cats = {}
    for ev in events:
        cats[ev["category"]] = cats.get(ev["category"], 0) + 1
    print(f"✅ 重新分类完成，调整了 {changed} 条事件")
    print("当前分类分布：")
    for cat, n in sorted(cats.items(), key=lambda x: -x[1]):
        print(f"   {cat}: {n} 条")


if __name__ == "__main__":
    main()
