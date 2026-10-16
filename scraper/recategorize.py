# -*- coding: utf-8 -*-
"""存量类目补全：给 category 为空/『其他』的事件按关键词自动分类。

为什么需要：
  类目自动分类（guess_category）2026-09 下旬才上线，此前入库的事件类目大量为
  『其他』（截至 2026-10-16 共 3,940 条，9/10 两个月占 3,100+），导致：
    1. categoryPriors 拿默认分 → 档案价值评分系统性偏低；
    2. 页面筛选器里"其他"成了一个无效大类。
  本脚本只处理 category 为空/『其他』的事件，**绝不覆盖来源已有的明确类目**。

用法：
  python scraper/recategorize.py            # dry-run：只统计与预览，不改数据
  python scraper/recategorize.py --apply    # 写回 events.json（不入库真源，需再 split）

之后请运行：python scraper/rescore.py --all（类目变化会影响 categoryPriors → 评分）
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

ROOT = Path(__file__).parent.parent
EVENTS_FILE = ROOT / "data" / "events.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="写回（默认 dry-run）")
    args = ap.parse_args()

    # 延迟导入：scraper 模块较大
    from scraper import guess_category  # noqa

    events = json.loads(EVENTS_FILE.read_text(encoding="utf-8"))
    targets = [e for e in events if not e.get("category")
               or e.get("category") in (["其他"], [""])]
    print(f"待补分类 {len(targets)} / {len(events)} 条")

    changed = 0
    preview = Counter()
    for e in targets:
        cat = guess_category(e.get("title") or "", e.get("summary") or "")
        if cat and cat != "其他":
            e["category"] = [cat]
            changed += 1
            preview[cat] += 1
        else:
            e["category"] = ["其他"]
    print(f"成功分类 {changed} 条，分布: {dict(preview.most_common())}")
    print(f"仍为『其他』: {len(targets) - changed} 条（词库未覆盖，保留原样）")

    if args.apply and changed:
        tmp = EVENTS_FILE.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(events, f, ensure_ascii=False)
        tmp.replace(EVENTS_FILE)
        print(f"✅ 已写回 {EVENTS_FILE.name}（{changed} 条类目更新）")
        print("⚠️ 下一步请运行：python scraper/rescore.py --all（类目影响评分先验）")
    elif not args.apply:
        print("（dry-run，未写回；确认无误后加 --apply）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
