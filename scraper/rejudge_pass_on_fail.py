# -*- coding: utf-8 -*-
"""PASS_ON_FAIL 重判器 —— 在 LLM 可用时，对账本报废的候选重新判定。

背景：
    闸门 INGEST_MODE=shadow 时，若 LLM 不可用，候选会以「模型判断不可用」为由
    放行（action=PASS_ON_FAIL），绝大多数已直接入库。等模型恢复后，需要回头
    复核这批条目：如果当时 LLM 在线，它们会被 KEEP / REVIEW / DROP 哪一类？

设计要点（与 docs/proposals/2026-10-18_*.md 对齐）：
    1. **不追新**——只处理指定月份账本里的 PASS_ON_FAIL 存量记录。
    2. **不改库**——重判**不回写 events.json**，绝大多数候选本就已入库；只
       追加 REJUDGE_* 审计行到当月账本，绝不修改/删除原 PASS_ON_FAIL 行。
    3. **断点续跑**——以 url 为唯一键，已重判过的 url 自动跳过；写
       rejudge_checkpoint.json 作为运行中断点。
    4. **dry-run 优先**——默认不调 LLM、不写盘，只统计 + 打印将要处理
       的候选；`--apply` 才真正起跑。
    5. **限流友好**——单条串行调用 + sleep；失败 2 次放弃本条；429 时
       chat_raw 内部已换档，整轮失败优雅退出。

用法：
    # 预览（不调 LLM、不写盘）
    python scraper/rejudge_pass_on_fail.py --dry-run

    # 真跑（先跑 3 条探测 LLM 可用性，再处理存量，单轮默认 60 条预算）
    python scraper/rejudge_pass_on_fail.py --apply

    # 一批一批跑（每天跑一轮 60 条）
    python scraper/rejudge_pass_on_fail.py --apply --budget 60

    # 处理指定月份的账本
    python scraper/rejudge_pass_on_fail.py --apply --month 2026-10
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import value_judge as VJ  # noqa: E402

ROOT = Path(__file__).parent.parent
CANDIDATE_DIR = ROOT / "data" / "candidates"
LEDGER_GLOB = "candidate-decisions-{month}.jsonl"
CHECKPOINT_FILE = CANDIDATE_DIR / "rejudge_checkpoint.json"
REPORT_FILE_TPL = "rejudge_report_{date}.json"
ORPHAN_FILE = CANDIDATE_DIR / "rejudge_orphan.json"

BATCH_SLEEP = 2.5          # 批间 sleep（秒），略大于 judge_items 默认 2.0
PROBE_COUNT = 3            # 启动时 LLM 可用性探测条数
PROBE_REQUIRED = 3         # 3 条全部成功才视为可用
MAX_RETRY_PER_ITEM = 2     # 同条最多重试次数


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def load_ledger(month: str):
    """读取指定月份的账本，返回 (PASS_ON_FAIL 记录列表, 全部记录列表)。"""
    f = CANDIDATE_DIR / LEDGER_GLOB.format(month=month)
    if not f.exists():
        return [], []
    pass_on_fail, all_rows = [], []
    with open(f, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            all_rows.append(d)
            if d.get("action") == "PASS_ON_FAIL":
                pass_on_fail.append(d)
    return pass_on_fail, all_rows


def dedup_by_url(rows):
    """按 url 去重，保留最后一条（最近一次失败时间更新）。"""
    seen = {}
    for r in rows:
        url = (r.get("url") or "").strip()
        if not url:
            continue
        seen[url] = r  # 后写覆盖先写，等价于保留最新时间戳
    return list(seen.values())


def load_checkpoint():
    """读取断点：已重判完成的 url 集合。"""
    if not CHECKPOINT_FILE.exists():
        return set()
    try:
        data = json.loads(CHECKPOINT_FILE.read_text(encoding="utf-8"))
        return set(data.get("done_urls") or [])
    except Exception:
        return set()


def save_checkpoint(done_urls):
    """原子写断点（先 tmp 再 replace）。"""
    CANDIDATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CHECKPOINT_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({
        "saved_at": _now_iso(),
        "done_urls": sorted(done_urls),
        "count": len(done_urls),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CHECKPOINT_FILE)


def clear_checkpoint():
    if CHECKPOINT_FILE.exists():
        CHECKPOINT_FILE.unlink()


def ledger_candidate_to_item(rec):
    """把账本回填为 value_judge.judge_one 期待的 item 形状。"""
    return {
        "title": rec.get("title") or "",
        "date": rec.get("date") or "",
        "summary": "",  # 账本没存 summary，给空串让 prompt 走"无来源链接"分支
        "content": "",
        "category": rec.get("eventType") or "",
        "sourceUrl": rec.get("url") or "",
        "source": rec.get("source") or "",
    }


def append_ledger(month: str, row: dict):
    """追加一行到当月账本（**绝不修改/删除已有行**）。"""
    CANDIDATE_DIR.mkdir(parents=True, exist_ok=True)
    f = CANDIDATE_DIR / LEDGER_GLOB.format(month=month)
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def match_event_id(events, url):
    """按 url 在 events.json 中反查 event id（仅用于报告字段）。"""
    if not events or not url:
        return None
    for ev in events:
        for src in (ev.get("sources") or []):
            if (src.get("url") or "").strip() == url:
                return ev.get("id")
    return None


def probe_llm(sample_rows):
    """LLM 可用性探测：跑 3 条样本，全部成功才允许进入正式重判。"""
    if len(sample_rows) < PROBE_COUNT:
        return False, f"样本不足 {PROBE_COUNT} 条，无法探测"
    print(f"\n=== LLM 可用性探测（{PROBE_COUNT} 条）===")
    ok = 0
    for i, r in enumerate(sample_rows[:PROBE_COUNT]):
        item = ledger_candidate_to_item(r)
        try:
            res = VJ.judge_one(item)
        except Exception as ex:
            print(f"  [{i+1}/3] 异常 {type(ex).__name__}", flush=True)
            res = None
        if res:
            ok += 1
            print(f"  [{i+1}/3] OK decision={res.get('decision')} av={res.get('archiveValue')}", flush=True)
        else:
            print(f"  [{i+1}/3] FAIL（返回 None）", flush=True)
        time.sleep(BATCH_SLEEP)
    usable = ok >= PROBE_REQUIRED
    return usable, f"{ok}/{PROBE_COUNT} 成功"


def rejudge_one(rec, events):
    """对单条候选做重判。返回 (status, ledger_row)。

    status ∈ {"keep", "review", "drop", "failed"}
    ledger_row 可直接 append 到当月账本。
    """
    item = ledger_candidate_to_item(rec)
    url = (rec.get("url") or "").strip()
    last_err = None
    for attempt in range(MAX_RETRY_PER_ITEM):
        try:
            res = VJ.judge_one(item)
        except Exception as ex:
            last_err = f"{type(ex).__name__}: {ex}"
            res = None
        if res:
            break
        time.sleep(BATCH_SLEEP)
    else:
        # 全部重试失败
        return "failed", {
            "at": _now_iso(),
            "action": "REJUDGE_FAILED",
            "rejudge_of_url": url,
            "original_action": "PASS_ON_FAIL",
            "original_archive_value": rec.get("archiveValue"),
            "original_method": rec.get("method"),
            "original_date": rec.get("date"),
            "error": (last_err or "judge returned None")[:200],
        }

    new_dec = res.get("decision")
    matched_id = match_event_id(events, url)
    base = {
        "at": _now_iso(),
        "rejudge_of_url": url,
        "original_action": "PASS_ON_FAIL",
        "original_archive_value": rec.get("archiveValue"),
        "original_method": rec.get("method"),
        "original_date": rec.get("date"),
        "new_archive_value": res.get("archiveValue"),
        "new_method": res.get("method"),
        "new_decision": new_dec,
        "new_event_type": res.get("eventType"),
        "reason": (res.get("reason") or "")[:200],
        "matched_event_id": matched_id,
    }
    # 原装 PASS_ON_FAIL 的 decision 是 REVIEW（闸门里规则层结论），
    # 用 archiving 的 decision 与 LLM 新结论对比得到升级/确认/下沉。
    orig_dec = rec.get("decision") or "REVIEW"
    if new_dec == "KEEP" and orig_dec != "KEEP":
        action, status = "REJUDGE_UPGRADE_KEEP", "keep"
    elif new_dec == "DROP" and orig_dec != "DROP":
        action, status = "REJUDGE_DOWNGRADE_DROP", "drop"
    elif new_dec == "REVIEW":
        action, status = "REJUDGE_CONFIRM_REVIEW", "review"
    else:
        # 同值（少见路径，比如原账本里 decision 已是 KEEP/DROP）
        action = f"REJUDGE_CONFIRM_{new_dec}"
        status = new_dec.lower()
    base["action"] = action
    return status, base


def write_report(stats, month, date_str):
    f = CANDIDATE_DIR / REPORT_FILE_TPL.format(date=date_str)
    f.write_text(json.dumps({
        "run_at": _now_iso(),
        "month": month,
        **stats,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return f


def append_orphans(orphans):
    if not orphans:
        return
    existing = []
    if ORPHAN_FILE.exists():
        try:
            existing = json.loads(ORPHAN_FILE.read_text(encoding="utf-8"))
        except Exception:
            existing = []
    existing.extend(orphans)
    ORPHAN_FILE.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="PASS_ON_FAIL 候选重判（LLM 恢复后回头复核）")
    ap.add_argument("--month", default=time.strftime("%Y-%m"),
                    help="账本月份，如 2026-10")
    ap.add_argument("--budget", type=int, default=60,
                    help="单轮处理的候选上限（默认 60，与 llm_budget 节奏一致）")
    ap.add_argument("--apply", action="store_true",
                    help="真正调 LLM 并写账本；不加此参数 = dry-run 只预览")
    ap.add_argument("--probe-skip", action="store_true",
                    help="跳过 LLM 可用性探测（**不推荐**，仅调试用）")
    ap.add_argument("--limit-urls", type=int, default=0,
                    help="调试：只处理前 N 个唯一 URL")
    ap.add_argument("--load-events", action="store_true",
                    help="加载 events.json 用 url 反查 event id（报告字段更全）")
    args = ap.parse_args()

    print(f"=== PASS_ON_FAIL 重判 ===")
    print(f"  账本: {LEDGER_GLOB.format(month=args.month)}")
    print(f"  模式: {'APPLY（真跑）' if args.apply else 'DRY-RUN（只预览）'}")
    print(f"  预算: {args.budget} 条/轮\n")

    rows, _all = load_ledger(args.month)
    if not rows:
        print(f"❌ {args.month} 账本中没有 PASS_ON_FAIL 记录")
        return 0
    print(f"本月 PASS_ON_FAIL 原始记录: {len(rows)} 条")

    uniq = dedup_by_url(rows)
    print(f"按 url 去重后: {len(uniq)} 条")

    done = load_checkpoint()
    if done:
        print(f"断点已存在，已完成 {len(done)} 条，本轮将跳过")
    pending = [r for r in uniq if (r.get("url") or "").strip() not in done]
    if args.limit_urls:
        pending = pending[: args.limit_urls]
    batch = pending[: args.budget]
    print(f"待重判: {len(pending)} 条 | 本轮处理: {len(batch)} 条\n")

    if not args.apply:
        # DRY-RUN：打印前 5 条样本 + 字段概览
        print("--- 样本预览（前 5 条）---")
        for i, r in enumerate(batch[:5]):
            print(f"[{i+1}] {r.get('date')} | av={r.get('archiveValue')} | "
                  f"{(r.get('title') or '')[:60]}")
            print(f"    url={r.get('url')}")
        print(f"\n✅ dry-run 完成。如确认无误，运行加上 --apply 真跑本轮 {len(batch)} 条。")
        return 0

    # APPLY：探测 → 重判 → 写账本
    if not args.probe_skip:
        usable, msg = probe_llm(batch)
        if not usable:
            print(f"\n❌ LLM 不可用（{msg}），本轮取消。"
                  f"可使用 --probe-skip 强制（不推荐）。")
            return 2
        print(f"✅ LLM 可用（{msg}）\n")

    events = []
    if args.load_events:
        ev_path = ROOT / "data" / "events.json"
        if ev_path.exists():
            print("加载 events.json 用于 url 反查 ...", flush=True)
            events = json.loads(ev_path.read_text(encoding="utf-8"))
            print(f"  共 {len(events)} 条事件\n")

    stats = {
        "total_pass_on_fail_in_ledger": len(rows),
        "unique_urls": len(uniq),
        "done_before_this_run": len(done),
        "this_run_budget": args.budget,
        "this_run_attempted": len(batch),
        "this_run": {"keep": 0, "review": 0, "drop": 0, "failed": 0},
        "started_at": _now_iso(),
    }
    orphans = []
    t0 = time.time()
    completed_this_run = []
    try:
        for i, rec in enumerate(batch):
            status, ledger_row = rejudge_one(rec, events)
            append_ledger(args.month, ledger_row)
            stats["this_run"][status] = stats["this_run"].get(status, 0) + 1
            url = (rec.get("url") or "").strip()
            if status != "failed":
                completed_this_run.append(url)
                done.add(url)
            else:
                stats.setdefault("errors", []).append({
                    "url": url, "title": (rec.get("title") or "")[:80],
                })
            # 孤儿检测：重判后无 matched_event_id 且明确 KEEP 的，提示可能漏入库
            if status == "keep" and not ledger_row.get("matched_event_id"):
                orphans.append({
                    "url": url, "title": (rec.get("title") or "")[:120],
                    "date": rec.get("date"),
                    "new_archive_value": ledger_row.get("new_archive_value"),
                })
            if (i + 1) % 5 == 0:
                elapsed = time.time() - t0
                print(f"  [{i+1}/{len(batch)}] {status:6s} av={ledger_row.get('new_archive_value')} "
                      f"elapsed={elapsed:.0f}s", flush=True)
            time.sleep(BATCH_SLEEP)
    except KeyboardInterrupt:
        print("\n⚠️ 用户中断，保存断点并退出...")
    finally:
        done.update(completed_this_run)
        save_checkpoint(done)
        append_orphans(orphans)
        stats["finished_at"] = _now_iso()
        stats["wall_clock_seconds"] = round(time.time() - t0, 1)
        stats["done_total_after_this_run"] = len(done)
        stats["orphan_count"] = len(orphans)
        date_str = time.strftime("%Y-%m-%d")
        report_path = write_report(stats, args.month, date_str)
        print(f"\n=== 报告已写入 {report_path} ===")
        print(json.dumps(stats, ensure_ascii=False, indent=2))

    # 全部跑完则清断点
    if len(done) >= len(uniq):
        clear_checkpoint()
        print(f"\n🎉 全部 {len(uniq)} 条已重判完毕，断点已清理。")
    else:
        remain = len(uniq) - len(done)
        print(f"\n剩余 {remain} 条待下轮处理。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
