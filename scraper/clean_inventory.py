# -*- coding: utf-8 -*-
"""存量清洗（inventory cleaning）—— 把价值评分体系落到已有事件上，并隔离低价值事件。

世界事件档案定位：新闻是来源，事件才是数据；世界影响力决定重要性，时代记忆决定
是否值得留下。本脚本把已上线的「档案价值评分体系 v1」应用到存量 events.json，
给每条事件写回七维分 / archiveValue / eventType / decision，并隔离真正低价值的事件。

用法：
  # 试点：先只跑 2026 年，dry-run 看清楚会隔离什么（绝对不碰 events.json / events.js）
  python clean_inventory.py --year 2026 --dry-run

  # 确认无误后真正执行（隔离 DROP 事件 + 写回评分 + 重建站点）
  python clean_inventory.py --year 2026 --apply

  # 全库（谨慎，建议逐年后扩）
  python clean_inventory.py --apply

设计要点：
  1. 评分只增字段，不删旧字段；任何异常事件原样保留（绝不丢数据）。
  2. 只有「规则明确低价值 rules_drop → DROP」的事件才被隔离；
     boundary / weak_evidence 一律保持 REVIEW（保守，宁可留不许错删）。
  3. 隔离不是删除：DROP 事件整体搬进 data/dropped_events_<year>.json，可复核、可回滚。
  4. 支持 --dry-run：只出报告 + 预览文件，绝不碰 events.json / events.js。
"""
import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import event_schema as ES
import value_rules as VR

ROOT = Path(__file__).parent.parent
EVENTS_FILE = ROOT / "data" / "events.json"
DATA_DIR = ROOT / "data"


def load_events():
    with open(EVENTS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def assess(ev):
    """对单条事件做规则评分并写回字段。返回 (layer, decision, result)。"""
    try:
        r = VR.score_event(ev)
        ES.apply_scores(
            ev,
            r["scores"],
            r["eventType"],
            method="rules",
            confidence=r.get("confidence"),
            reason=r["reason"],
        )
        return r["layer"], ev.get("decision"), r
    except Exception as ex:  # 任何异常都不影响整库，原样保留
        print(f"  ⚠️ 评分异常 {ev.get('id','?')}: {type(ex).__name__}: {ex}", flush=True)
        return "error", None, None


def run(year=None, dry_run=True):
    events = load_events()
    if year:
        scope = [e for e in events if (e.get("date") or "")[:4] == year]
    else:
        scope = list(events)
    print(f"📊 目标范围：{'全库' if not year else year + ' 年'}，共 {len(scope)} 条事件", flush=True)

    layer_counts = Counter()
    decision_counts = Counter()
    type_counts = Counter()
    dropped = []
    t0 = time.time()
    for i, ev in enumerate(scope):
        layer, decision, r = assess(ev)
        layer_counts[layer] += 1
        decision_counts[decision] += 1
        if r:
            type_counts[r["eventType"]] += 1
        if decision == "DROP" and ev.get("id"):
            dropped.append({
                "id": ev.get("id"),
                "date": ev.get("date"),
                "title": ev.get("title"),
                "category": ev.get("category"),
                "eventType": r["eventType"],
                "archiveValue": r["archiveValue"],
                "reason": r["reason"],
            })
        if (i + 1) % 1000 == 0:
            print(f"  …已处理 {i + 1}/{len(scope)}", flush=True)

    dt = time.time() - t0
    total = len(scope)
    n_drop = len(dropped)
    print(f"\n=== 清洗报告（{'DRY-RUN 仅预览' if dry_run else '已应用'}）===")
    print(f"  范围: {year or '全库'} | 事件 {total} | 耗时 {dt:.1f}s")
    print(f"  分层: " + "  ".join(f"{k}={v}" for k, v in layer_counts.most_common()))
    print(f"  结论: " + "  ".join(f"{k}={v}" for k, v in decision_counts.most_common()))
    print(f"  类型: " + "  ".join(f"{k}={v}" for k, v in type_counts.most_common()))
    print(f"  拟隔离(DROP): {n_drop} 条（占比 {100.0 * n_drop / total:.1f}%）")
    print("=====================================")

    # 预览文件（含前 200 条 DROP 明细，供人工复核）
    preview = {
        "year": year,
        "dry_run": dry_run,
        "total": total,
        "layerCounts": dict(layer_counts),
        "decisionCounts": dict(decision_counts),
        "typeCounts": dict(type_counts),
        "droppedCount": n_drop,
        "droppedRate": round(100.0 * n_drop / total, 2) if total else 0,
        "droppedSample": dropped[:200],
    }
    DATA_DIR.mkdir(exist_ok=True)
    pf = DATA_DIR / f"clean_preview_{year or 'all'}.json"
    pf.write_text(json.dumps(preview, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  📝 预览写入 {pf.name}（含前 200 条 DROP 明细）")

    if dry_run:
        print("\n--- DROP 样本（前 15 条）---")
        for d in dropped[:15]:
            print(f"  [{d['date']}] {d['title']}  (av={d['archiveValue']}, {d['reason']})")
        return

    # ---- 真正应用 ----
    dropped_ids = {d["id"] for d in dropped}
    kept = [e for e in events if e.get("id") not in dropped_ids]
    print(f"\n  隔离 {len(dropped_ids)} 条 → 保留 {len(kept)} 条（原 {len(events)}）")

    # 原子写回 events.json
    tmp = EVENTS_FILE.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(kept, f, ensure_ascii=False)
    tmp.replace(EVENTS_FILE)
    print(f"  ✅ events.json 已写回（{len(kept)} 条）")

    # 隔离文件（保留完整事件对象，含刚写回的评分字段，便于复核/回滚）
    drop_file = DATA_DIR / f"dropped_events_{year or 'all'}.json"
    dropped_full = [e for e in events if e.get("id") in dropped_ids]
    with open(drop_file, "w", encoding="utf-8") as f:
        json.dump(dropped_full, f, ensure_ascii=False)
    print(f"  🗃️  DROP 事件已隔离至 {drop_file.name}（可复核/回滚）")

    # 重建站点（events.js + 归档页 + index 等）
    rebuild_site()


def rebuild_site():
    print("  🔧 重建站点（events.js + 归档页 + index）…", flush=True)
    try:
        import build
        build.main()
        print("  ✅ 站点重建完成")
    except Exception as ex:
        print(f"  ⚠️ 站点重建失败（数据已写回，可稍后手动 `python scraper/build.py`）: "
              f"{type(ex).__name__}: {ex}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", default=None, help="只处理该年份，如 2026（不填=全库）")
    ap.add_argument("--apply", action="store_true", help="真正隔离并写回（默认仅 dry-run）")
    args = ap.parse_args()
    if args.apply and not args.year:
        print("⚠️ 正在对【全库】执行隔离，请确保已逐月验证过 dry-run 结果。", flush=True)
        time.sleep(2)
    run(year=args.year, dry_run=not args.apply)


if __name__ == "__main__":
    main()
