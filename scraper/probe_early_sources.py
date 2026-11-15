# -*- coding: utf-8 -*-
"""早年数据源只读探测（1997-2001）。

背景（2026-11-15 体检）：
  维基 Portal:Current_events 逐日页 2002 年起才有（1997 年门户只是重定向，
  正文写明 "calendars for each month since 2002"），所以 1997-2001 五年
  全库只有 422 条，且来源 100% 是 en.wikipedia.org 单一页面族。

本脚本在 GitHub Actions 云端运行（本机代理把 BBC / 维基一律挡成 502），
只读探测两类候选源的可用性与条目量，**不写库、不改任何数据**：

  A. BBC On This Day：按「月日」而非按年组织，366 个日期页即覆盖全部年份，
     抽样 24 页统计 1997-2001 命中数，判断值不值得全量抓。
  B. 维基领域年表 YYYY_in_*：独立于通用年度页，条目带 "Month Day –" 前缀，
     用 API 取 wikitext 统计带日期的事件行数。

输出 JSON 摘要到 stdout，供人工判断值不值得写正式回填脚本。
用法：python scraper/probe_early_sources.py
"""
import calendar
import collections
import json
import re
import time
import urllib.error
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (compatible; world-events-probe/1.0)"}
EARLY_YEARS = tuple(str(y) for y in range(1997, 2002))
MONTHS = tuple(m.lower() for m in calendar.month_name if m)
DATE_WORDS = "|".join(MONTHS)

BBC_URL = "http://news.bbc.co.uk/onthisday/hi/dates/stories/{m}/{d}/default.stm"
WIKI_API = ("https://en.wikipedia.org/w/api.php?action=parse&page={page}"
            "&prop=wikitext&format=json&formatversion=2")
WIKI_PAGES = [
    f"{y}_in_{topic}"
    for y in EARLY_YEARS
    for topic in ("science", "sports", "television", "music")
]


def fetch(url, timeout=20, retries=3):
    """带退避重试的只读抓取。

    2026-11-15 首次探测：维基 API 连续请求到第 11 页起全部 HTTPError（限流），
    20 页只成功 10 页，条目量被低估。这里加指数退避 + 页间间隔。
    """
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as f:
                return f.read().decode("utf-8", "replace")
        except Exception as e:
            last = e
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
    raise last


def probe_bbc():
    """抽样每月 1 日与 15 日；BBC 一页是「历史上的这一天」，含多个年份。"""
    ok = fail = 0
    hits = collections.Counter()
    samples = []
    for month in MONTHS:
        for day in (1, 15):
            url = BBC_URL.format(m=month, d=day)
            try:
                html = fetch(url)
                ok += 1
            except Exception as e:
                fail += 1
                print(f"  BBC {month}/{day} 失败: {type(e).__name__} {str(e)[:70]}", flush=True)
                continue
            time.sleep(0.3)
            # 形如 "1999: Super-cyclone wreaks havoc in India"
            for m in re.finditer(r"\b(199[7-9]|200[01])\s*[:：]\s*([^<\n]{5,140})", html):
                yr = m.group(1)
                hits[yr] += 1
                if len(samples) < 8:
                    samples.append({"date": f"{month}/{day}", "year": yr,
                                    "title": re.sub(r"\s+", " ", m.group(2)).strip()[:100]})
    return {"pages_ok": ok, "pages_failed": fail,
            "early_hits_by_year": dict(sorted(hits.items())),
            "early_hits_total": sum(hits.values()), "samples": samples,
            "note": "抽样 24/366 页；全量估算 = early_hits_total * 366/24"}


def probe_wikipedia():
    ok, fail = 0, 0
    per_page = {}
    samples = []
    dated_line = re.compile(
        r"^\*\s*\[{0,2}(?:" + DATE_WORDS + r")\b", re.IGNORECASE)
    for page in WIKI_PAGES:
        try:
            raw = fetch(WIKI_API.format(page=page), timeout=25)
            d = json.loads(raw)
            text = (d.get("parse") or {}).get("wikitext") or ""
            if not text:
                fail += 1
                per_page[page] = "EMPTY"
                continue
            ok += 1
            n = sum(1 for line in text.splitlines() if dated_line.match(line))
            per_page[page] = n
            if n and len(samples) < 5:
                first = next(l for l in text.splitlines() if dated_line.match(l))
                samples.append({"page": page, "line": first[:120]})
        except Exception as e:
            fail += 1
            per_page[page] = f"ERR:{type(e).__name__}"
        time.sleep(0.8)
    return {"pages_ok": ok, "pages_failed": fail,
            "dated_events_total": sum(v for v in per_page.values() if isinstance(v, int)),
            "per_page": per_page, "samples": samples,
            "note": "每个领域年表独立于 1997/1998 等通用年度页，理论上是净增量"}


def main():
    print("=== 早年数据源只读探测（不写库）===", flush=True)
    print("[A] BBC On This Day 抽样 24 页 ...", flush=True)
    bbc = probe_bbc()
    print("[B] 维基领域年表 20 页 ...", flush=True)
    wiki = probe_wikipedia()
    report = {
        "scope": "1997-2001",
        "bbc": bbc,
        "wikipedia": wiki,
        "estimate": {
            "bbc_full_scan_projected": round(bbc["early_hits_total"] * 366 / 24),
            "wiki_dated_events_found": wiki["dated_events_total"],
            "current_library_in_1997_2001": 422,
        },
    }
    print("\n=== PROBE_RESULT_JSON ===")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
