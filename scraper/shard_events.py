# -*- coding: utf-8 -*-
"""事件库分片：data/events.json（工作副本） ⇄ data/events/YYYY.json（入库分片）。

为什么分片：
  data/events.json 是 79MB 的**单行无换行** JSON：
    1. 任何改动＝整文件重写，git 历史里每 commit 塞进一个 79MB blob，仓库急剧膨胀；
    2. 单行文件无法行级三方合并，本地与云端 diverge 时只能按 id 并集手工救火。
  改成按年份分片后：不同年份的改动互不冲突，单文件只有 1-6MB，可 diff、可合并。

设计取舍（重要）：
  采用「入库分片 + 工作副本」而非「所有脚本直读分片」——
  scraper/ 下 19 个脚本都读写 data/events.json，全部改造成分片读写风险极高。
  这里让 events.json 继续作为唯一工作副本（且加入 .gitignore 不入库），
  只在入库/出库两个时刻做 split / merge，现有脚本零改动。

用法：
  python scraper/shard_events.py split    # events.json → data/events/YYYY.json（入库前）
  python scraper/shard_events.py merge    # data/events/YYYY.json → events.json（出库后）
  python scraper/shard_events.py status   # 查看分片概况与工作副本是否落后
  python scraper/shard_events.py ensure   # 仅当 events.json 缺失时自动 merge（供脚本调用）

幂等性：split → merge → split 结果一致（分组时保持原数组相对顺序，不重排）。
"""
import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
EVENTS_FILE = ROOT / "data" / "events.json"
SHARD_DIR = ROOT / "data" / "events"
INDEX_FILE = SHARD_DIR / "index.json"
UNKNOWN = "unknown"
# 单年事件数超过这个阈值就再按月细分（缩进后单文件控制在 1-3MB，便于 diff 与合并）
MONTHLY_THRESHOLD = 1200


def _now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _year_of(ev):
    d = (ev.get("date") or "").strip()
    return d[:4] if len(d) >= 4 and d[:4].isdigit() else UNKNOWN


def _shard_key(ev, monthly_years):
    """分片键：大年份用 YYYY-MM，小年份用 YYYY。"""
    d = (ev.get("date") or "").strip()
    if len(d) >= 4 and d[:4].isdigit():
        return d[:7] if d[:4] in monthly_years and len(d) >= 7 else d[:4]
    return UNKNOWN


def _atomic_write_json(path: Path, data, indent=2):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)
    tmp.replace(path)


def load_events():
    if not EVENTS_FILE.exists():
        return None
    with open(EVENTS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def split(events=None):
    """events.json → 按年份分片。保持原数组相对顺序，保证幂等。"""
    if events is None:
        events = load_events()
    if events is None:
        print("❌ 找不到 data/events.json，无法 split")
        return 1
    # 先按年统计，决定哪些年份需要再按月细分
    by_year = Counter(_year_of(e) for e in events)
    monthly_years = {y for y, n in by_year.items() if n > MONTHLY_THRESHOLD}

    buckets = {}
    for e in events:
        buckets.setdefault(_shard_key(e, monthly_years), []).append(e)

    # 清理上一次的旧分片，避免"年分片"与"月分片"并存造成 merge 重复
    for p in SHARD_DIR.glob("*.json"):
        if p.name != "index.json":
            p.unlink()

    SHARD_DIR.mkdir(parents=True, exist_ok=True)
    total_bytes = 0
    for key, items in sorted(buckets.items()):
        p = SHARD_DIR / f"{key}.json"
        _atomic_write_json(p, items)
        total_bytes += p.stat().st_size
        print(f"  {key}.json  {len(items):>6} 条  {p.stat().st_size/1024/1024:.2f} MB")

    _atomic_write_json(INDEX_FILE, {
        "version": "events-shards-v1",
        "generatedAt": _now(),
        "totalEvents": len(events),
        "shards": {k: len(buckets[k]) for k in sorted(buckets)},
        "years": {y: n for y, n in sorted(by_year.items())},
        "monthlyYears": sorted(monthly_years),
        "note": "data/events/<key>.json 为入库真源；data/events.json 是不入库的工作副本，"
                "由 merge 从分片重建。改数据后须先 split 再提交。",
    })
    print(f"✅ split 完成：{len(events)} 条 → {len(buckets)} 个分片，"
          f"合计 {total_bytes/1024/1024:.1f} MB")
    return 0


def merge():
    """分片 → events.json。按年份升序拼接，块内保持原顺序。"""
    if not INDEX_FILE.exists():
        print("❌ 找不到 data/events/index.json，无法 merge")
        return 1
    idx = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    shards = idx.get("shards") or {y: n for y, n in (idx.get("years") or {}).items()}
    keys = sorted(shards.keys())
    all_events = []
    seen = set()
    dup = 0
    for k in keys:
        p = SHARD_DIR / f"{k}.json"
        if not p.exists():
            print(f"  ⚠️ 缺失分片 {p.name}，跳过")
            continue
        items = json.loads(p.read_text(encoding="utf-8"))
        for e in items:
            eid = e.get("id")
            if eid in seen:  # 跨分片重复 id 兜底，绝不产生脏库
                dup += 1
                continue
            seen.add(eid)
            all_events.append(e)

    _atomic_write_json(EVENTS_FILE, all_events, indent=None)
    print(f"✅ merge 完成：{len(all_events)} 条（去重 {dup} 条）→ data/events.json "
          f"({EVENTS_FILE.stat().st_size/1024/1024:.1f} MB)")
    return 0


def status():
    ev = load_events()
    print(f"工作副本 data/events.json: "
          f"{'存在 / %d 条 / %.1f MB' % (len(ev), EVENTS_FILE.stat().st_size/1024/1024) if ev else '缺失'}")
    if INDEX_FILE.exists():
        idx = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
        print(f"分片索引 generatedAt: {idx.get('generatedAt')}  总条数 {idx.get('totalEvents')}")
        shards = idx.get("shards") or idx.get("years") or {}
        tot = 0
        for k in shards:
            p = SHARD_DIR / f"{k}.json"
            if p.exists():
                tot += p.stat().st_size
        biggest = sorted(((SHARD_DIR / f"{k}.json").stat().st_size for k in shards
                          if (SHARD_DIR / f"{k}.json").exists()), reverse=True)[:3]
        print(f"分片文件: {len(shards)} 个，合计 {tot/1024/1024:.1f} MB，"
              f"最大三个 {[round(b/1024/1024, 2) for b in biggest]} MB")
        if ev and idx.get("totalEvents") != len(ev):
            print(f"⚠️ 条数不一致：分片 {idx.get('totalEvents')} vs 工作副本 {len(ev)}（建议 merge 或 split）")
    else:
        print("分片尚未生成（运行 split 创建）")
    return 0


def ensure():
    """仅在 events.json 缺失时从分片重建——供其他脚本开头调用，绝不覆盖已有数据。"""
    if EVENTS_FILE.exists():
        return 0
    print("ℹ️ data/events.json 缺失，从分片重建…")
    return merge()


def main():
    ap = argparse.ArgumentParser(description="事件库按年份分片 / 合并")
    ap.add_argument("action", choices=["split", "merge", "status", "ensure"])
    args = ap.parse_args()
    try:
        return {"split": split, "merge": merge, "status": status, "ensure": ensure}[args.action]()
    except Exception as ex:
        print(f"❌ {args.action} 失败: {type(ex).__name__}: {ex}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
