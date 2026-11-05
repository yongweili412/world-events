# -*- coding: utf-8 -*-
"""来源链接体检：抽样检测 events.json 里的来源 URL，输出失效报告。

为什么需要：
  "永久存档"的引用如果是 404，等于没有来源。2026-10 回填的 Wikipedia 月度门户页
  就整批 404（已由 normalize_data 的 URL 规范化修掉），但外部媒体链接会持续腐烂，
  需要定期体检，而不是等读者点开发现。

设计取舍：
  · 只报告，不改数据 —— 避免给 5 万条事件的每个来源加 check 字段导致分片暴涨。
  · 只把 404/410/451 判为 dead；401/403/429 是"反爬/限流"，单独记为 blocked，
    不能被当成失效（否则 Reuters 这类站点会整批误报）。
  · 同一 URL 只测一次；按 --limit 分批，可重复运行覆盖全库。
  · 失效链接会尝试 Wayback 快照，给出可用的存档地址。

用法：
    python scraper/check_links.py --limit 500
    python scraper/check_links.py --limit 2000 --workers 8 --out data/links-report.json
"""
from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).parent.parent
DEFAULT_DATA = ROOT / "data" / "events.json"
DEFAULT_OUT = ROOT / "data" / "links-report.json"
UA = "Mozilla/5.0 (compatible; world-events-linkcheck/1.0; +https://yongweili412.github.io/world-events/)"

DEAD_CODES = {404, 410, 451}
BLOCKED_CODES = {401, 403, 429, 999}
SSL_CTX = ssl.create_default_context()


def classify(code: int | None, error: str = "") -> str:
    if code is None:
        return "error"
    if 200 <= code < 400:
        return "ok"
    if code in DEAD_CODES:
        return "dead"
    if code in BLOCKED_CODES:
        return "blocked"
    return "error"


def probe(url: str, timeout: float = 10.0) -> tuple[str, int | None]:
    """返回 (status, http_code)。HEAD 被拒时回退到 GET（只取首字节）。"""
    for method in ("HEAD", "GET"):
        try:
            headers = {"User-Agent": UA, "Accept": "*/*"}
            if method == "GET":
                headers["Range"] = "bytes=0-0"
            req = urllib.request.Request(url, method=method, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as r:
                return classify(r.status), r.status
        except urllib.error.HTTPError as e:
            code = e.code
            # 405/501：服务器不支持 HEAD，换 GET 再试
            if method == "HEAD" and code in (405, 501):
                continue
            return classify(code), code
        except urllib.error.URLError as e:
            reason = str(getattr(e, "reason", e))
            if "CERTIFICATE" in reason.upper():
                return "blocked", None
            continue
        except Exception:
            continue
    return "error", None


def wayback(url: str) -> str:
    """查 Wayback 是否有快照；有则返回可用存档 URL。"""
    try:
        q = ("https://archive.org/wayback/available?url="
             + urllib.parse.quote(url, safe=""))
        with urllib.request.urlopen(q, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
        snap = (data.get("archived_snapshots") or {}).get("closest") or {}
        if snap.get("available") and snap.get("url"):
            return snap["url"]
    except Exception:
        pass
    return ""


def collect_urls(events, limit: int, skip: set[str]):
    """按事件顺序收集去重 URL；同一 URL 只测一次。"""
    urls, owners = [], defaultdict(list)
    for ev in events:
        for s in ev.get("sources") or []:
            u = (s.get("url") or "").strip()
            if not u or u in skip or u in owners:
                continue
            if not u.startswith(("http://", "https://")):
                continue
            owners[u].append(ev.get("id"))
            urls.append(u)
            if len(urls) >= limit:
                return urls, owners
    return urls, owners


def load_previous(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {r["url"]: r for r in data.get("checked") or []}
    except Exception:
        return {}


def main() -> int:
    ap = argparse.ArgumentParser(description="来源链接体检（只报告，不改数据）")
    ap.add_argument("--data", default=str(DEFAULT_DATA))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--timeout", type=float, default=10.0)
    ap.add_argument("--recheck-days", type=int, default=30,
                    help="跳过最近 N 天已测过的 URL；0 表示不跳过")
    ap.add_argument("--no-wayback", action="store_true")
    args = ap.parse_args()

    data_path = Path(args.data)
    if not data_path.exists():
        print(f"✗ 找不到 {data_path}")
        return 2
    events = json.loads(data_path.read_text(encoding="utf-8"))

    out_path = Path(args.out)
    previous = load_previous(out_path)
    cutoff = time.time() - args.recheck_days * 86400 if args.recheck_days else 0
    skip = {u for u, r in previous.items()
            if cutoff and _parse_ts(r.get("checkedAt", "")) >= cutoff}

    urls, owners = collect_urls(events, args.limit, skip)
    print(f"待检测 {len(urls)} 个 URL（已跳过 {len(skip)} 个近期测过的）")

    results = []
    counts = Counter()
    dead_by_domain = Counter()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for i, (u, (status, code)) in enumerate(
                zip(urls, pool.map(lambda x: probe(x, args.timeout), urls)), 1):
            counts[status] += 1
            if status == "dead":
                dead_by_domain[urlsplit(u).netloc.replace("www.", "")] += 1
            results.append({"url": u, "status": status, "code": code,
                            "checkedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                            "events": owners.get(u, [])[:3]})
            if i % 100 == 0:
                print(f"  …{i}/{len(urls)}")

    if not args.no_wayback:
        dead = [r for r in results if r["status"] == "dead"][:50]
        for r in dead:
            arch = wayback(r["url"])
            if arch:
                r["archiveUrl"] = arch

    merged = dict(previous)
    for r in results:
        merged[r["url"]] = r
    report = {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "checked": list(merged.values()),
        "summary": {
            "totalChecked": len(merged),
            "thisRun": {
                "count": len(results),
                "ok": counts.get("ok", 0),
                "dead": counts.get("dead", 0),
                "blocked": counts.get("blocked", 0),
                "error": counts.get("error", 0),
                "deadRatio": round(counts.get("dead", 0) / max(len(results), 1), 4),
            },
            "deadByDomain": dead_by_domain.most_common(20),
        },
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"结果：ok {counts.get('ok',0)} / dead {counts.get('dead',0)} / "
          f"blocked {counts.get('blocked',0)} / error {counts.get('error',0)}")
    if dead_by_domain:
        print("失效最多的域名：" + "，".join(f"{d}({n})" for d, n in dead_by_domain.most_common(5)))
    print(f"报告已写入 {out_path}")
    return 0


def _parse_ts(s: str) -> float:
    try:
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return 0.0


if __name__ == "__main__":
    sys.exit(main())
