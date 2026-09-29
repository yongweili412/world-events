# -*- coding: utf-8 -*-
"""为弱证据事件补抓独立来源（证据修复）。

背景：
  84.7%（26,136 条）历史事件的 sources 只有维基月度存档页，证据等级被判 weak，
  导致近 7 成事件只能判 REVIEW —— 不是它们没价值，而是没有证据能验证价值。
  根因是 backfill_daily.py 的 clean_wiki 会把 <ref> 引用整段删掉（已修复，但只对新增生效）。

做法：
  回到维基逐日页重新解析（用修复后的 parse_day，能拿到 <ref> 里的真实报道链接），
  再把中文事件对回英文 bullet，把报道链接补进 sources，证据等级随之升级。

⚠️ 必须在云端运行：本机访问不了 en.wikipedia.org（SSL 被拦，实测 SSLEOFError）。
⚠️ 匹配是启发式的（同一天 + 数字/拉丁词重合），只接受高置信匹配；
   匹配不上的保持原样，绝不瞎猜——宁可不补，也不补错。

用法：
  python enrich_sources.py --months 2020-03 2021-01   # 指定月份
  python enrich_sources.py --auto 3                    # 从未处理月份里取 3 个
  python enrich_sources.py --months 2020-03 --dry-run  # 只统计不写库
"""
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scraper"))

import scraper as sp
import event_schema as ES
import backfill_daily as bd  # 复用 wiki_raw / parse_day（已修复：parse_day 会返回 refs）

PROGRESS_FILE = ROOT / "data" / "enrich_sources_progress.json"
MIN_SCORE = 2          # 匹配最低分（低于此不补，避免错配）
MAX_PER_RUN = 4        # 单次最多处理月份数


# ---------- 匹配特征 ----------

def nums(text):
    """数字特征：跨语言最稳的锚点（如 185 号航班、8 人遇难）。"""
    return set(re.findall(r"\d+", text or ""))


def latin(text):
    """拉丁词特征（长度≥4，去掉常见停用词），用于匹配专有名词音译/原文。"""
    stop = {"the", "and", "for", "with", "that", "from", "this", "have", "been", "were", "said"}
    return {w.lower() for w in re.findall(r"[A-Za-z]{4,}", text or "") if w.lower() not in stop}


def match_score(ev, bullet_text):
    """中文事件 vs 英文 bullet 的匹配分。数字重合权重高于拉丁词。"""
    ev_text = f"{ev.get('title','')} {ev.get('summary','')} {ev.get('description','')}"
    bn, bl = nums(bullet_text), latin(bullet_text)
    en, el = nums(ev_text), latin(ev_text)
    # 数字：跨语言稳定，权重 2
    s = 2 * len(bn & en)
    # 拉丁词：翻译后大多丢失，权重 1
    s += 1 * len(bl & el)
    # 长度接近度小幅加权（避免长条目吃掉短事件的匹配）
    a, b = len(ev_text), len(bullet_text)
    if a and b:
        ratio = min(a, b) / max(a, b)
        if ratio > 0.4:
            s += 1
    return s


def assign(events, bullets):
    """同一天内做一对一贪心匹配：event_id -> refs。"""
    cand = []
    for ev in events:
        for bi, bt in enumerate(bullets):
            sc = match_score(ev, bt["text"])
            if sc >= MIN_SCORE:
                cand.append((sc, ev["id"], bi))
    cand.sort(key=lambda x: -x[0])
    used_ev, used_b, out = set(), set(), {}
    for sc, eid, bi in cand:
        if eid in used_ev or bi in used_b:
            continue
        used_ev.add(eid)
        used_b.add(bi)
        out[eid] = bullets[bi].get("refs") or []
    return out


# ---------- 主流程 ----------

def is_weak(ev):
    """是否所有来源都是聚合/存档页（即证据 weak，需要补证）。"""
    srcs = ev.get("sources") or []
    if not srcs:
        return True
    return all(ES._is_archive_url((s.get("url") or "").strip()) for s in srcs)


def load_progress():
    if PROGRESS_FILE.exists():
        try:
            return json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"done_months": []}


def save_progress(p):
    PROGRESS_FILE.write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8")


def enrich_month(ym, events, dry_run=False):
    y, mo = int(ym[:4]), int(ym[5:7])
    # 按日期聚合弱证据事件
    by_date = {}
    for ev in events:
        if not is_weak(ev):
            continue
        by_date.setdefault(ev.get("date", ""), []).append(ev)
    if not by_date:
        return 0, 0, 0

    n_match = n_event = 0
    for date in sorted(by_date):
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
            continue
        yy, mm, dd = date.split("-")
        title = f"Portal:Current_events/{yy}_{bd.MONTHS[int(mm) - 1]}_{int(dd)}"
        try:
            raw = bd.wiki_raw(title)
        except Exception:
            raw = None
        if not raw:
            continue
        bullets = [b for b in bd.parse_day(raw) if b.get("refs")]
        if not bullets:
            continue
        hits = assign(by_date[date], bullets)
        for ev in by_date[date]:
            n_event += 1
            refs = hits.get(ev["id"])
            if not refs:
                continue
            n_match += 1
            if dry_run:
                continue
            for u in refs:
                if any((s.get("url") or "") == u for s in (ev.get("sources") or [])):
                    continue
                ev.setdefault("sources", []).append({
                    "name": bd.domain_of(u) or "Wikipedia 引用来源",
                    "url": u,
                    "date": ev.get("date", ""),
                    "title": ev.get("title", ""),
                    "snippet": "",
                })
        time.sleep(0.3)
    return n_event, n_match, len(by_date)


def main():
    args = sys.argv[1:]
    dry = "--dry-run" in args
    args = [a for a in args if a != "--dry-run"]
    if not args:
        print(__doc__)
        return

    p = load_progress()
    if args[0] == "--auto":
        n = int(args[1]) if len(args) > 1 else 1
        months = [m for m in _all_months() if m not in p["done_months"]][:n]
    elif args[0] == "--months":
        months = args[1:]
    else:
        months = args
    months = months[:MAX_PER_RUN]
    print("待补证月份:", months, " dry-run:", dry, flush=True)

    events = sp.load_events()
    total_ev = total_match = 0
    for ym in months:
        ne, nm, nd = enrich_month(ym, events, dry)
        total_ev += ne
        total_match += nm
        print(f"  {ym}: 弱证据日期 {nd} 个 / 弱证据事件 {ne} 条 → 成功补证 {nm} 条", flush=True)
        if not dry:
            sp.save_events(events)
            print("    💾 已增量保存", flush=True)
        if ym not in p["done_months"]:
            p["done_months"].append(ym)
            save_progress(p)

    rate = (total_match / total_ev * 100) if total_ev else 0
    print(f"\n合计：弱证据 {total_ev} 条，补证成功 {total_match} 条（命中率 {rate:.1f}%）", flush=True)


def _all_months():
    targets = ROOT / "data" / "backfill_daily_targets.json"
    try:
        years = [int(y) for y in json.loads(targets.read_text(encoding="utf-8"))["years"]]
    except Exception:
        years = list(range(1997, 2026))
    return [f"{y}-{m:02d}" for y in years for m in range(1, 13)]


if __name__ == "__main__":
    main()
