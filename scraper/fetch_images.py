# -*- coding: utf-8 -*-
"""封面图抓取：给「无图但有来源链接」的条目补 og:image 外链。

背景（2026-11-10）：12 万条里只有 1.7% 有封面图，列表页视觉贫乏。
来源页的 og:image 是现成的、指向原媒体的图，本站只存 URL、不下载不转存，
既避免版权风险，也不增加仓库体积。

策略：
  · 只处理 archiveValue >= MIN_VALUE 且尚无 image 的条目（价值优先，额度有限）；
  · 单次最多 --limit 条（默认 60），每条之间 sleep，避免把目标站打出 429；
  · 失败/超时一律跳过，不写入脏值；结果写回 data/events/*.json 真源；
  · 进度记在 data/image_fetch_progress.json，已试过的 id 不再重试（除非 --retry-failed）。

用法：
  python scraper/fetch_images.py --auto 60
  python scraper/fetch_images.py --auto 60 --dry-run
"""
import json
import os
import re
import sys
import time
import urllib.parse

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scraper"))

PROGRESS = os.path.join(ROOT, "data", "image_fetch_progress.json")
MIN_VALUE = 75          # 与静态页阈值一致：先照顾能进静态页的条目
UA = {"User-Agent": "Mozilla/5.0 (compatible; world-events-archive/1.0)"}
TIMEOUT = 12

OG_PATTERNS = [
    r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
    r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
]


def load_progress():
    try:
        with open(PROGRESS, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        d = {}
    d.setdefault("tried", [])
    d.setdefault("ok", 0)
    d.setdefault("fail", 0)
    return d


def save_progress(p):
    os.makedirs(os.path.dirname(PROGRESS) or ".", exist_ok=True)
    with open(PROGRESS, "w", encoding="utf-8") as f:
        json.dump(p, f, ensure_ascii=False, indent=1)


def og_image(url):
    """抓页面 HTML 里的 og:image；失败返回 None。"""
    try:
        r = requests.get(url, headers=UA, timeout=TIMEOUT, allow_redirects=True)
    except Exception:
        return None
    if r.status_code != 200 or not r.text:
        return None
    r.encoding = r.apparent_encoding or "utf-8"
    head = r.text[:200000]     # 只扫前 200KB，og 标签都在 <head>
    for pat in OG_PATTERNS:
        m = re.search(pat, head, re.I)
        if m:
            u = m.group(1).strip()
            if u.startswith("//"):
                u = "https:" + u
            if u.startswith("http"):
                return u
    return None


def main():
    args = sys.argv[1:]
    dry = "--dry-run" in args
    retry_failed = "--retry-failed" in args
    limit = 60
    if "--auto" in args:
        i = args.index("--auto")
        if i + 1 < len(args) and args[i + 1].isdigit():
            limit = int(args[i + 1])

    import scraper as sp
    events = sp.load_events()
    prog = load_progress()
    tried = set() if retry_failed else set(prog["tried"])

    # 候选：高价值 + 无图 + 有来源 URL
    cand = []
    for ev in events:
        if ev.get("id") in tried:
            continue
        if str(ev.get("image") or "").startswith("http"):
            continue
        if (ev.get("archiveValue") or 0) < MIN_VALUE:
            continue
        srcs = ev.get("sources") or []
        url = next((s.get("url") for s in srcs if str(s.get("url") or "").startswith("http")), None)
        if url:
            cand.append((ev, url))
    cand.sort(key=lambda x: -(x[0].get("archiveValue") or 0))
    batch = cand[:limit]
    print(f"待抓 {len(cand):,} 条，本次处理 {len(batch)} 条 dry-run={dry}", flush=True)

    ok = fail = 0
    for ev, url in batch:
        img = og_image(url)
        prog["tried"].append(ev["id"])
        if img:
            ev["image"] = img
            ok += 1
            print(f"  ✅ {ev['id']} {ev.get('title','')[:26]} → {img[:70]}", flush=True)
        else:
            fail += 1
        time.sleep(1.0)

    prog["ok"] += ok
    prog["fail"] += fail
    print(f"\n本次：成功 {ok} / 失败 {fail}（累计成功 {prog['ok']}）", flush=True)

    if not dry and ok:
        # 工作副本 data/events.json 不入库，真源是 data/events/*.json 分片，
        # 必须 split 后才算落库（否则云端机器一销毁就丢，参考 2026-11-07 事故）。
        sp.save_events(events)
        import subprocess
        r = subprocess.run([sys.executable, "scraper/shard_events.py", "split"],
                           cwd=ROOT, capture_output=True, text=True, timeout=900)
        if r.returncode != 0:
            print("  ❌ split 失败，本次不记账，避免数据丢失", flush=True)
            return 1
        print("  💾 已落盘到分片真源", flush=True)
    if not dry:
        save_progress(prog)
        print(f"  进度已记录到 {os.path.relpath(PROGRESS, ROOT)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
