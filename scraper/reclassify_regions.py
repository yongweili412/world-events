# -*- coding: utf-8 -*-
"""按最新 guess_region 规则重新计算所有事件的地区字段"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from scraper import guess_region

ROOT = Path(__file__).parent.parent
DATA = ROOT / "data" / "events.json"

events = json.loads(DATA.read_text(encoding="utf-8"))
changed = 0
for e in events:
    text = f"{e.get('title','')} {e.get('summary','')} {(e.get('content') or '')[:600]}"
    new_region = guess_region(e.get("title", ""), e.get("summary", "") + " " + (e.get("content") or "")[:600])
    if e.get("region") != new_region:
        e["region"] = new_region
        changed += 1

DATA.write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")

import collections
dist = collections.Counter(e["region"] for e in events)
print(f"✅ 重算地区完成：更新 {changed} / {len(events)} 条")
for k, v in dist.most_common():
    print(f"  {k}: {v}")
