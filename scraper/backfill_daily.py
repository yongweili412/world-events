# -*- coding: utf-8 -*-
"""重点年份逐日存档回填：抓维基百科 Portal:Current_events 逐日页 → LLM 翻译 → 入库。
用法：python scraper/backfill_daily.py 2008-01 [2008-02 ...]  （年月，可多个）
      或 python scraper/backfill_daily.py auto N  （未完成的月份取前 N 个）
数据源：https://en.wikipedia.org/wiki/Portal:Current_events/2008_January_1（逐日页，wikitext）
说明：本机访问维基被墙，须在云端 GitHub Actions 运行。"""
import json, re, sys, time, os, datetime, requests

API = "https://en.wikipedia.org/w/index.php"
UA = {"User-Agent": "WorldEventsBot/1.0 (education archive; contact via github.com/yongweili412/world-events)"}
UA_MW = {"User-Agent": "WorldEventsBot/1.0 (education archive; contact via github.com/yongweili412/world-events)"}

PROGRESS_FILE = "data/backfill_daily_progress.json"
CATEGORIES = ["政治", "军事", "经济", "科技", "灾难", "社会", "文化", "体育", "国际关系"]

MONTHS = ["January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]


def load_progress():
    try:
        return json.load(open(PROGRESS_FILE, encoding="utf-8"))
    except Exception:
        return {"done_months": []}


def save_progress(p):
    os.makedirs(os.path.dirname(PROGRESS_FILE) or ".", exist_ok=True)
    json.dump(p, open(PROGRESS_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def wiki_raw(day_page_title):
    """取逐日页 wikitext"""
    r = requests.get(API, params={"title": day_page_title, "action": "raw"}, headers=UA_MW, timeout=25)
    if r.status_code != 200:
        return None
    r.encoding = "utf-8"
    t = r.text
    if "{{" not in t and len(t) < 100:
        return None
    return t


def clean_wiki(text):
    """wikitext → 纯文本"""
    t = text
    t = re.sub(r"<ref[^>]*>[\s\S]*?</ref>|<ref[^>]*/>", "", t)          # 引用
    t = re.sub(r"\{\{cite[^}]*\}\}", "", t, flags=re.I)                   # cite 模板
    t = re.sub(r"\{\{[^{}]*\}\}", "", t)                                   # 简单模板
    t = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]|]+)\]\]", r"\1", t)               # [[a|b]] → b
    t = re.sub(r"\[https?://\S+\s+([^\]]+)\]", r"\1", t)                  # [url text] → text
    t = re.sub(r"\[https?://\S+\]", "", t)                                 # 裸链接
    t = t.replace("'''", "").replace("''", "")
    t = re.sub(r"<[^>]+>", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def parse_day(wikitext):
    """解析单日页 → [{"text": en_text, "category_hint": 章节名}]"""
    if not wikitext:
        return []
    items = []
    # 剔除头部/尾部模板行与导航
    lines = wikitext.split("\n")
    cur_cat = ""
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        if s.startswith("{{") or s.startswith("}}") or s.startswith("|") or s.startswith("<!--"):
            continue
        if s.startswith("=="):
            continue
        # 章节标题（如 '''Armed conflicts and attacks''' 或 == Business ==）
        if (s.startswith("'''") and s.endswith("'''") and len(s) < 80) or (s.startswith("=") and s.endswith("=")):
            cur_cat = clean_wiki(s)
            continue
        if s.startswith("*"):
            body = clean_wiki(s.lstrip("*# ").strip())
            # 过滤导航/元信息残留
            if len(body) < 30:
                continue
            if re.search(r"^(See also|External links|References|Wikimedia|Wikipedia|This page|Portal:)", body, re.I):
                continue
            if re.search(r"https?://", body) and len(body) < 80:
                continue
            items.append({"text": body, "category_hint": cur_cat})
    return items


def fetch_month(year, month, max_days=31):
    """抓某年某月所有日子的条目"""
    out = []  # (date_str, category_hint, en_text)
    days_in_month = (datetime.date(year + (month == 12), (month % 12) + 1, 1) - datetime.timedelta(days=1)).day
    got_days = 0
    for day in range(1, min(days_in_month, max_days) + 1):
        title = f"Portal:Current_events/{year}_{MONTHS[month-1]}_{day}"
        try:
            raw = wiki_raw(title)
        except Exception as ex:
            print(f"    抓取异常 {title}: {type(ex).__name__}", flush=True)
            raw = None
        if not raw:
            continue
        items = parse_day(raw)
        if items:
            got_days += 1
            d = f"{year:04d}-{month:02d}-{day:02d}"
            for it in items:
                out.append((d, it["category_hint"], it["text"]))
        time.sleep(0.4)
    print(f"  📅 抓到 {got_days} 天 / {len(out)} 条（{year}-{month:02d}）", flush=True)
    return out


def call_llm(prompt, timeout=240):
    key = os.environ.get("LLM_API_KEY", "").strip()
    base = os.environ.get("LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4").rstrip("/")
    model = os.environ.get("LLM_MODEL", "glm-4.5-flash")
    return requests.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": model, "messages": [{"role": "user", "content": prompt}],
              "temperature": 0.2, "thinking": {"type": "disabled"}},
        timeout=timeout,
    )


def build_prompt(batch, year, month):
    return f"""你是新闻编辑，正在为"世界事件档案"网站整理 {year} 年 {month} 月的历史事件（来源：维基百科当日新闻存档）。
下面是 {len(batch)} 条英文事件（idx 为序号）。请逐条改写成中文新闻事件，输出严格 JSON 数组：
{{"idx": 序号, "title": "30字内中文新闻标题", "summary": "50-90字中文摘要", "category": "类别", "country": "主要相关国家中文名，跨国用'多国'，无明确国家用''"}}
category 只能从这些里选：{json.dumps(CATEGORIES, ensure_ascii=False)}
要求：
1. 新闻体、专有名词用中文规范译名；标题陈述事实；不要输出 JSON 以外文字；字符串内禁止双引号（用《》或''）。
2. 战争、冲突、灾难、恐袭等属公开史实必须如实收录，用中性词（遇难/身亡/死亡人数/武装冲突/交火/爆炸袭击），平实记录，不渲染。

条目列表：
{json.dumps(batch, ensure_ascii=False)}"""


def translate_batch(batch):
    """batch: [{"idx":i,"date":..,"text":en}] → [(idx, title, summary, category, country)]"""
    if not batch:
        return []
    r = call_llm(build_prompt(batch, "", ""))
    if r.status_code != 200:
        if "1301" in r.text and len(batch) > 1:
            # 拆单条
            out = []
            for b in batch:
                rr = call_llm(build_prompt([b], "", ""), timeout=120)
                if rr.status_code == 200:
                    out += _parse(rr.json(), [b])
                time.sleep(1.5)
            return out
        return []
    return _parse(r.json(), batch)


def _parse(resp_json, batch):
    content = resp_json["choices"][0]["message"].get("content") or ""
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
        out.append((j, str(a["title"]).strip(), str(a.get("summary", "")).strip(),
                    a.get("category") if a.get("category") in CATEGORIES else "其他",
                    str(a.get("country", "")).strip()))
    return out


def main():
    args = sys.argv[1:]
    if not args:
        print("用法: backfill_daily.py 2008-01 [2008-02 ...] 或 auto N")
        return
    p = load_progress()
    months = []
    if args[0] == "auto":
        n = int(args[1]) if len(args) > 1 else 1
        years = list(range(1997, 2026))
        try:
            cfg = json.load(open("data/backfill_daily_targets.json", encoding="utf-8"))
            if cfg.get("years"):
                years = [int(y) for y in cfg["years"]]
        except Exception:
            pass
        print("auto 目标年份:", years, flush=True)
        allm = [f"{y}-{m:02d}" for y in years for m in range(1, 13)]
        months = [m for m in allm if m not in p["done_months"]][:n]
    else:
        months = args
    print("待处理月份:", months, flush=True)

    import scraper as sp
    ev = sp.load_events()

    for ym in months:
        y, mo = int(ym[:4]), int(ym[5:7])
        print(f"🌐 {ym}: 抓取逐日存档…", flush=True)
        rows = fetch_month(y, mo)
        if not rows:
            print(f"  ⚠️ {ym} 无数据，跳过", flush=True)
            p["done_months"].append(ym)
            save_progress(p)
            continue

        # 批量翻译（10 条/批）
        batch_size = 10
        cn = []
        for i in range(0, len(rows), batch_size):
            chunk = rows[i:i + batch_size]
            payload = [{"idx": j, "date": c[0], "text": c[2]} for j, c in enumerate(chunk)]
            got = translate_batch(payload)
            for j, title, summary, category, country in got:
                cn.append((chunk[j][0], title, summary, category, country))
            if i % (batch_size * 5) == 0:
                print(f"    翻译进度 {i//batch_size+1}/{(len(rows)+batch_size-1)//batch_size}: {len(cn)}", flush=True)
            time.sleep(2)

        # 入库
        new_items = []
        for date, title, summary, category, country in cn:
            region = sp.guess_region(title, summary) or "全球"
            new_items.append({
                "title": title, "summary": summary, "content": summary, "date": date,
                "source": f"Wikipedia 当日新闻存档 {ym}",
                "sourceUrl": f"https://en.wikipedia.org/wiki/Portal:Current_events/{y}_{MONTHS[mo-1]}",
                "category": category, "region": region, "country": country,
                "tags": [category] + ([country] if country and country != "多国" else []),
            })
        before = len(ev)
        sp.merge_into_events(ev, new_items, dedupe_url=False, use_fp=False)
        after = len(ev)
        for e in ev:
            if not e.get("aiSummary") and (e.get("sources") or [{}])[0].get("name", "").startswith("Wikipedia"):
                s = (e.get("summary") or "").strip()
                if s:
                    e["aiSummary"] = s
        sp.save_events(ev)
        p["done_months"].append(ym)
        save_progress(p)
        print(f"  ✅ {ym}: 翻译 {len(cn)}/{len(rows)} 条 → 新建 {after - before} | 库总量 {after}", flush=True)
        time.sleep(2)

    print("DONE")


if __name__ == "__main__":
    sys.path.insert(0, "scraper")
    main()
