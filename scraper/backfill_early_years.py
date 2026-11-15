# -*- coding: utf-8 -*-
"""1997-2001 早年事件正式回填（首批小批试跑）。

数据源（2026-11-15 云端只读探测实测可用）：
  A. BBC On This Day —— 按「月日」组织，366 个日期页覆盖全部年份，1997-2001 约 534 条
  B. 维基 YYYY_in_science / YYYY_in_sports 领域年表 —— 带 "Month Day –" 日期前缀，
     不收 music / television（唱片发行、榜单类档案价值低）

范围约束（用户 2026-11-15 确认）：首批小批试跑，默认最多入库 200 条；
只收 BBC + science/sports，过价值闸门（DROP 剔除）后入库，英文条目经 LLM 翻译。
只存来源 URL，不下载图片。

账货一致性纪律（沿用 backfill_daily 的两次事故教训）：
  真实新建数 / 尝试数 < 50% → 不落库、不记账、整批留待重跑（防「假完成」）；
  save_events → shard_events.py split 成功 → 才写进度文件；
  与既有 422 条按「日期 + 中文标题」粗筛去重，避免重复消耗 LLM。

用法：
  python scraper/backfill_early_years.py --limit 200
  python scraper/backfill_early_years.py --limit 200 --dry-run   # 只抓候选、不调 LLM、不写库
须在 GitHub Actions 云端运行（本机代理把 BBC / 维基一律挡成 502）。
"""
import argparse
import calendar
import datetime
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROGRESS_FILE = os.path.join(ROOT, "data", "backfill_early_progress.json")
UA = {"User-Agent": "WorldEventsBot/1.0 (education archive; github.com/yongweili412/world-events)"}

MONTH_NAMES = ["january", "february", "march", "april", "may", "june",
               "july", "august", "september", "october", "november", "december"]
MONTH_NUM = {m.capitalize(): i + 1 for i, m in enumerate(MONTH_NAMES)}
MONTH_WORDS = "|".join(m.capitalize() for m in MONTH_NAMES)
EARLY_YEARS = range(1997, 2002)
WIKI_TOPICS = ("science", "sports")   # 用户确认：不收 music / television

BBC_URL = "http://news.bbc.co.uk/onthisday/hi/dates/stories/{m}/{d}/default.stm"
WIKI_API = ("https://en.wikipedia.org/w/api.php?action=parse&page={page}"
            "&prop=wikitext&format=json&formatversion=2")

CATEGORIES = ["自然灾害", "政治", "经济", "体育", "环境", "社会", "军事", "科技", "文化", "其他"]
MIN_OUTPUT_RATIO = 0.5

def wiki_pages():
    """维基领域年表页名。`_in_` 不可省：曾误拼成 1997_science，10 页全空。"""
    return [(y, f"{y}_in_{t}") for y in EARLY_YEARS for t in WIKI_TOPICS]


BBC_ITEM = re.compile(r"\b(199[7-9]|200[01])\s*[:：]\s*([^<\n]{5,180})")
HREF = re.compile(r'href\s*=\s*["\']([^"\']+)["\']', re.I)
WIKI_LINE = re.compile(
    r"^\*\s*\[{0,2}(?P<mon>" + MONTH_WORDS + r")\s+(?P<day>\d{1,2})\]{0,2}"
    r"\s*(?:&ndash;|&mdash;|[–—\-:：]\s*)?(?P<text>.+)$", re.I)


# ---------------------------------------------------------------- 只读抓取

def _fetch(url, timeout=25, retries=3):
    """带指数退避的只读抓取（维基 API 连续请求会间歇 429/HTTPError）。"""
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


def _cand(date, text_en, source, source_url, kind, page):
    norm = re.sub(r"[^a-z0-9]+", "", text_en.lower())[:70]
    return {
        "key": hashlib.md5(f"{date}|{norm}".encode("utf-8")).hexdigest()[:16],
        "date": date, "text_en": text_en,
        "source": source, "sourceUrl": source_url,
        "kind": kind, "page": page,
    }


def _clean_wikitext(t):
    """wikitext → 纯文本（与 backfill_daily.clean_wiki 同口径，内联以免耦合）。"""
    t = re.sub(r"<ref[^>]*>[\s\S]*?</ref>|<ref[^>]*/>", "", t)
    t = re.sub(r"\{\{[^{}]*\}\}", "", t)
    t = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]|]+)\]\]", r"\1", t)
    t = re.sub(r"\[https?://\S+\s+([^\]]+)\]", r"\1", t)
    t = re.sub(r"\[https?://\S+\]", "", t)
    t = t.replace("'''", "").replace("''", "")
    t = re.sub(r"<[^>]+>", "", t)
    return re.sub(r"\s+", " ", t).strip()


def _extract_refs(line):
    urls = []
    for u in re.findall(r"https?://[^\s|\]}<]+", line):
        u = u.rstrip(".,);")
        if "wikipedia.org" in u or "web.archive.org" in u or "archive.today" in u:
            continue
        if u not in urls:
            urls.append(u)
    return urls[:2]


def parse_bbc_html(raw_html, page_url, month, day):
    """BBC 日期页 → 1997-2001 候选（年份来自条目前缀，月日来自页面 URL）。"""
    out = []
    for m in BBC_ITEM.finditer(raw_html):
        year = m.group(1)
        title = re.sub(r"\s+", " ", html.unescape(m.group(2))).strip()
        title = title.split("|")[0].strip()
        if len(title) < 8:
            continue
        prefix = raw_html[max(0, m.start() - 700):m.start()]
        hrefs = HREF.findall(prefix)
        url = page_url
        if hrefs:
            cand = urllib.parse.urljoin(page_url, hrefs[-1])
            if "bbc.co.uk" in cand or "onthisday" in cand:
                url = cand
        out.append(_cand(f"{year}-{month:02d}-{day:02d}", title,
                         "BBC On This Day", url, "bbc", f"{month:02d}-{day:02d}"))
    return out


def parse_wiki_wikitext(wikitext, year, page):
    """维基领域年表 wikitext → 带具体日期的候选。"""
    out = []
    for raw_line in (wikitext or "").splitlines():
        m = WIKI_LINE.match(raw_line.strip())
        if not m:
            continue
        mon = MONTH_NUM.get(m.group("mon").capitalize())
        day = int(m.group("day"))
        if not mon or not (1 <= day <= 31):
            continue
        refs = _extract_refs(raw_line)
        text = _clean_wikitext(m.group("text"))
        if len(text) < 12:
            continue
        url = refs[0] if refs else f"https://en.wikipedia.org/wiki/{page}"
        out.append(_cand(f"{year}-{mon:02d}-{day:02d}", text[:400],
                         f"Wikipedia {page.replace('_', ' ')}", url, "wiki", page))
    return out


def collect_candidates(max_bbc_pages=366, skip_bbc=False):
    """抓两类源并去重。BBC 全量日期页 + 维基 science/sports 年表。"""
    bbc, wiki = [], []
    seen = set()
    pages_ok = pages_fail = 0
    if skip_bbc:
        print("  （调试模式：跳过 BBC 抓取，只验证维基年表解析）", flush=True)
        max_bbc_pages = 0

    for month in range(1, 13):
        for day in range(1, calendar.monthrange(2000, month)[1] + 1):
            if pages_ok + pages_fail >= max_bbc_pages:
                break
            url = BBC_URL.format(m=MONTH_NAMES[month - 1], d=day)
            try:
                raw = _fetch(url)
                pages_ok += 1
            except Exception as e:
                pages_fail += 1
                if pages_fail <= 3:
                    print(f"  BBC {month:02d}-{day:02d} 抓取失败: {type(e).__name__}", flush=True)
                continue
            for c in parse_bbc_html(raw, url, month, day):
                if c["key"] not in seen:
                    seen.add(c["key"])
                    bbc.append(c)
            time.sleep(0.2)
        if pages_ok + pages_fail >= max_bbc_pages:
            break
    print(f"  BBC: {pages_ok} 页成功 / {pages_fail} 页失败 → {len(bbc)} 条候选", flush=True)

    for year, page in wiki_pages():
        try:
            data = json.loads(_fetch(WIKI_API.format(page=page), timeout=30))
            text = (data.get("parse") or {}).get("wikitext") or ""
            got = parse_wiki_wikitext(text, year, page)
            for c in got:
                if c["key"] not in seen:
                    seen.add(c["key"])
                    wiki.append(c)
            if got:
                print(f"  维基 {page}: {len(got)} 条", flush=True)
            else:
                star = [l.strip()[:150] for l in text.splitlines()
                        if l.strip().startswith("*")][:3]
                print(f"  维基 {page}: 0 条 | response keys={list(data.keys())} | "
                      f"wikitext {len(text)} 字符 | 星号行样例 {star}", flush=True)
        except Exception as e:
            print(f"  维基 {page} 失败: {type(e).__name__} {str(e)[:70]}", flush=True)
        time.sleep(0.8)
    print(f"  维基合计: {len(wiki)} 条候选", flush=True)
    return bbc, wiki


# ---------------------------------------------------------------- 翻译 / 闸门

def build_prompt(batch):
    return f"""你是新闻编辑，正在为「世界事件档案」整理 1997-2001 年的历史事件
（来源：BBC On This Day / 维基百科领域年表）。下面是 {len(batch)} 条英文史料，idx 为序号。
请逐条改写成中文新闻事件，输出严格 JSON 数组：
{{"idx": 序号, "title": "30字内中文新闻标题", "summary": "50-90字中文摘要", "category": "类别", "country": "主要相关国家中文名，跨国用'多国'，无明确国家用''"}}
category 只能从这些里选：{json.dumps(CATEGORIES, ensure_ascii=False)}
要求：
1. 新闻体、专有名词用中文规范译名；标题陈述事实；不要输出 JSON 以外文字；字符串内禁止双引号（用《》或''）。
2. 战争、冲突、灾难等属公开史实必须如实收录，用中性词（遇难/身亡/武装冲突/交火/爆炸袭击），平实记录，不渲染。
3. 不要编造原文没有的数字、人名、地点；信息不足就写得更短，不要扩写。
4. 每条都必须给出 idx，缺条会破坏后续对齐。
条目列表：
{json.dumps(batch, ensure_ascii=False)}"""


def _parse_llm(resp, batch):
    if resp is None or getattr(resp, "status_code", None) != 200:
        return []
    try:
        content = resp.json()["choices"][0]["message"].get("content") or ""
    except Exception:
        return []
    m = re.search(r"\[.*\]", content, re.S)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
    except Exception:
        return []
    by_idx = {a.get("idx"): a for a in arr if isinstance(a, dict)}
    out = []
    for j, b in enumerate(batch):
        a = by_idx.get(j)
        if not a or not a.get("title"):
            continue
        cat = a.get("category")
        out.append((j, str(a["title"]).strip()[:60],
                    str(a.get("summary", "")).strip()[:220],
                    cat if cat in CATEGORIES else "其他",
                    str(a.get("country", "")).strip()[:30]))
    return out


def translate(items, batch_size=10):
    """串行分批翻译。chat_raw 自动处理限免档 429 → 付费兜底档切换。"""
    sys.path.insert(0, os.path.join(ROOT, "scraper"))
    from llm_guard import chat_raw
    key = os.environ.get("LLM_API_KEY", "").strip()
    out = []
    total = (len(items) + batch_size - 1) // batch_size
    for i in range(0, len(items), batch_size):
        chunk = items[i:i + batch_size]
        payload = [{"idx": j, "date": c["date"], "text": c["text_en"][:400]}
                   for j, c in enumerate(chunk)]
        try:
            resp = chat_raw(build_prompt(payload), key=key, timeout=180,
                            temperature=0.2, max_tokens=4000)
        except Exception as e:
            print(f"  批次异常 {type(e).__name__}", flush=True)
            resp = None
        got = _parse_llm(resp, payload)
        returned = {j for j, *_ in got}
        for j, title, summary, cat, country in got:
            c = dict(chunk[j])
            c.update({"title": title, "summary": summary,
                      "category": cat, "country": country})
            out.append(c)
        # 单条重试：模型返回 JSON 常缺个别 idx，静默丢掉会永久少条目。
        # 首轮实测 200 条只翻出 140 条，这个补漏能把翻译率拉回来。
        for j, c in enumerate(chunk):
            if j in returned:
                continue
            one = [{"idx": 0, "date": c["date"], "text": c["text_en"][:400]}]
            try:
                rr = chat_raw(build_prompt(one), key=key, timeout=120,
                              temperature=0.2, max_tokens=800)
            except Exception:
                rr = None
            one_got = _parse_llm(rr, one)
            if one_got:
                _, title, summary, cat, country = one_got[0]
                nc = dict(c)
                nc.update({"title": title, "summary": summary,
                           "category": cat, "country": country})
                out.append(nc)
            time.sleep(0.8)
        n = i // batch_size + 1
        if n % 5 == 0 or n == total:
            print(f"  翻译进度 {n}/{total} 批，成功 {len(out)} 条", flush=True)
        time.sleep(1.5)
    return out


def value_gate(translated):
    """过价值闸门：规则判定 DROP 的不入库（不调 LLM，规则零成本可复现）。"""
    sys.path.insert(0, os.path.join(ROOT, "scraper"))
    import candidate_pipeline as CP
    kept = []
    for c in translated:
        item = {"title": c["title"], "summary": c["summary"], "content": c["summary"],
                "date": c["date"], "category": c["category"],
                "source": c["source"], "sourceUrl": c["sourceUrl"]}
        try:
            r = CP.classify(item, use_llm=False)
        except Exception:
            r = {"decision": "KEEP", "archiveValue": 0}
        if r.get("decision") == "DROP":
            continue
        c["archiveValue"] = r.get("archiveValue", 0)
        kept.append(c)
    return kept


# ---------------------------------------------------------------- 进度 / 落库

def keys_to_record(translated):
    """只把「成功翻译」的候选记账。

    首轮曾把整个 pick（200 个 key）都写进 processed_keys，但只有 140 条翻译成功，
    剩下 60 条被永久跳过、再也不会重试。记账必须以 translated 为准。
    """
    return [c["key"] for c in translated]


def load_progress():
    try:
        return json.load(open(PROGRESS_FILE, encoding="utf-8"))
    except Exception:
        return {"processed_keys": [], "created_total": 0, "runs": []}


def save_progress(p):
    os.makedirs(os.path.dirname(PROGRESS_FILE), exist_ok=True)
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(p, f, ensure_ascii=False, indent=1)


def save_and_record(events, created, keys, prog, stats):
    """先落库、后记账：split 失败绝不写进度，避免「账有货无」。"""
    sys.path.insert(0, os.path.join(ROOT, "scraper"))
    import scraper as sp
    sp.save_events(events)
    r = subprocess.run([sys.executable, "scraper/shard_events.py", "split"],
                       cwd=ROOT, capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        print("  ❌ split 失败，本次不记账（工作副本已写但未落分片，可重跑）", flush=True)
        print((r.stderr or "")[-500:], flush=True)
        return False
    prog["processed_keys"] = sorted(set(prog.get("processed_keys", [])) | set(keys))
    prog["created_total"] = int(prog.get("created_total", 0)) + created
    prog.setdefault("runs", []).append({
        "at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        **stats, "created": created,
    })
    save_progress(prog)
    print(f"  💾 已落库并记账：新建 {created} 条", flush=True)
    return True


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=200, help="本次最多入库条数")
    ap.add_argument("--max-bbc-pages", type=int, default=366, help="BBC 日期页抓取上限")
    ap.add_argument("--dry-run", action="store_true", help="只抓候选，不调 LLM、不写库")
    ap.add_argument("--skip-bbc", action="store_true", help="调试用：只验证维基年表解析")
    ap.add_argument("--ignore-processed", action="store_true",
                    help="忽略 processed_keys 重扫候选（补回首轮翻译失败条目，靠库内去重防重复）")
    args = ap.parse_args()
    os.chdir(ROOT)

    print("=== 早年回填 1997-2001（BBC + 维基 science/sports）===", flush=True)
    bbc, wiki = collect_candidates(args.max_bbc_pages, skip_bbc=args.skip_bbc)
    total_candidates = len(bbc) + len(wiki)
    print(f"候选合计 {total_candidates} 条（BBC {len(bbc)} / 维基 {len(wiki)}）", flush=True)
    if total_candidates == 0:
        print("❌ 候选为 0，疑似解析或网络故障；不入库、不记账", flush=True)
        return 1
    if args.dry_run:
        print("DRY-RUN：只抓候选，未调用 LLM、未写库", flush=True)
        print("样例:", json.dumps((bbc + wiki)[:3], ensure_ascii=False)[:600], flush=True)
        return 0

    # 两源配额：BBC 是编辑筛选过的「这一天的大事」，占 70%；维基领域年表占 30%，
    # 保证首批两个源都被验证到，而不是全被 BBC 占满。
    bbc_quota = max(1, int(args.limit * 0.7))
    wiki_quota = args.limit - bbc_quota
    prog = load_progress()
    processed = set() if args.ignore_processed else set(prog.get("processed_keys", []))
    if args.ignore_processed:
        print("  （重扫模式：忽略 processed_keys，靠库内去重防重复入库）", flush=True)
    pick = [c for c in bbc if c["key"] not in processed][:bbc_quota]
    pick += [c for c in wiki if c["key"] not in processed][:wiki_quota]
    print(f"本批选取 {len(pick)} 条（BBC {min(bbc_quota, len(bbc))} / "
          f"维基配额 {wiki_quota}），跳过已处理 {len(processed)} 条", flush=True)
    if not pick:
        print("DONE: 候选已全部处理完，无新增", flush=True)
        return 0

    translated = translate(pick)
    print(f"翻译完成 {len(translated)}/{len(pick)} 条", flush=True)
    if not translated:
        print("❌ 翻译 0 条（模型限流或返回格式异常）；不入库、不记账，本批留待重跑", flush=True)
        return 1

    kept = value_gate(translated)
    print(f"价值闸门放行 {len(kept)}/{len(translated)} 条", flush=True)
    if not kept:
        print("❌ 闸门全部拦下；不入库、不记账", flush=True)
        return 1

    sys.path.insert(0, os.path.join(ROOT, "scraper"))
    import scraper as sp
    ev = sp.load_events()
    existing = set()
    for e in ev:
        d = (e.get("date") or "")[:10]
        t = re.sub(r"\W+", "", e.get("title") or "")[:12]
        if d and t:
            existing.add((d, t))

    items = []
    for c in kept:
        k = (c["date"], re.sub(r"\W+", "", c["title"])[:12])
        if k in existing:
            continue
        existing.add(k)
        items.append({
            "title": c["title"], "summary": c["summary"], "content": c["summary"],
            "date": c["date"], "source": c["source"], "sourceUrl": c["sourceUrl"],
            "category": c["category"], "country": c["country"],
            "region": sp.guess_region(c["title"], c["summary"]) or "全球",
            "tags": [c["category"]] + ([c["country"]] if c["country"] and c["country"] != "多国" else []),
        })
    print(f"库内去重后尝试入库 {len(items)} 条", flush=True)
    if not items:
        print("DONE: 全部与既有事件重复，无新增", flush=True)
        return 0

    before = len(ev)
    sp.merge_into_events(ev, items, dedupe_url=False, use_fp=False)
    created = len(ev) - before
    ratio = created / len(items)
    print(f"实际新建 {created}/{len(items)} 条（{ratio:.0%}）", flush=True)
    if ratio < MIN_OUTPUT_RATIO:
        print(f"⚠️ 低于 {MIN_OUTPUT_RATIO:.0%} 闸门线：不落库、不记账，本批留待重跑", flush=True)
        return 1

    stats = {"limit": args.limit, "candidates": total_candidates,
             "picked": len(pick), "translated": len(translated),
             "gate_kept": len(kept), "attempted": len(items)}
    ok = save_and_record(ev, created, keys_to_record(translated), prog, stats)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
