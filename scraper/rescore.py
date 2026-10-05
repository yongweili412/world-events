# -*- coding: utf-8 -*-
"""档案价值评分补跑：给尚未评分的事件补上七维分 / archiveValue / decision。

为什么需要：
  云端每日抓取会不断写入新事件，但抓取流程本身不做价值评分，
  导致新事件缺 archiveValue / decision（评分覆盖总是落后于数据量）。

设计要点（重要）：
  1. 只"补分"，【不做隔离、不重建站点】——随时可安全重跑，绝不删数据。
  2. 单条事件评分异常只跳过该条，不影响整库，脚本永不抛异常中断流水线。
  3. 默认只处理缺评分的事件；--all 才全量重算。

用法：
  python scraper/rescore.py          # 只补未评分的（默认，推荐日常用）
  python scraper/rescore.py --all    # 全量重算（含已评分的）
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import event_schema as ES  # noqa: E402
import value_rules as VR  # noqa: E402

ROOT = Path(__file__).parent.parent
EVENTS_FILE = ROOT / "data" / "events.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="全量重算（默认只补未评分的）")
    args = ap.parse_args()

    try:
        with open(EVENTS_FILE, "r", encoding="utf-8") as f:
            events = json.load(f)
    except Exception as ex:
        print(f"❌ 读取 events.json 失败: {type(ex).__name__}: {ex}")
        return 1

    before = sum(1 for e in events if e.get("archiveValue") is not None)
    done = 0
    failed = 0
    for e in events:
        try:
            if args.all or e.get("archiveValue") is None:
                r = VR.score_event(e)
                ES.apply_scores(
                    e,
                    r["scores"],
                    r["eventType"],
                    method="rules",
                    confidence=r.get("confidence"),
                    reason=r["reason"],
                )
                done += 1
        except Exception as ex:  # 单条失败不影响整库
            failed += 1
            if failed <= 5:
                print(f"  ⚠️ 评分异常 {e.get('id', '?')}: {type(ex).__name__}: {ex}")

    # 原子写回
    try:
        tmp = EVENTS_FILE.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(events, f, ensure_ascii=False)
        tmp.replace(EVENTS_FILE)
    except Exception as ex:
        print(f"❌ 写回 events.json 失败: {type(ex).__name__}: {ex}")
        return 1

    after = sum(1 for e in events if e.get("archiveValue") is not None)
    dec = Counter(e.get("decision") for e in events)
    print(f"✅ 评分补跑完成：本次处理 {done} 条（异常跳过 {failed} 条）")
    print(f"   评分覆盖：{before} → {after} / 共 {len(events)} 条")
    print(f"   decision 分布：{dict(dec)}")
    print("   （本脚本只补分，未隔离任何事件、未重建站点）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
