# -*- coding: utf-8 -*-
"""补漏：把 1997/1998 被 1301 拦截丢失的条目用中性 prompt 补回入库"""
import json, re, time, sys, requests
sys.path.insert(0, "scraper")
import scraper as sp

KEY = "995611b2762144e88e023c856da104eb.d68AuncjSy9Qrd0O"
CATEGORIES = ["政治", "军事", "经济", "科技", "灾难", "社会", "文化", "体育", "国际关系"]
BATCH = 10

def call(prompt, timeout=180):
    return requests.post(
        "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"},
        json={
            "model": "glm-4.5-flash",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "thinking": {"type": "disabled"},
        },
        timeout=timeout,
    )

def build_prompt(year, batch_events):
    return f"""你是新闻编辑，正在为"世界事件档案"网站整理 {year} 年的历史大事。
下面是英文维基百科的 {year} 年大事条目（idx 为序号）。请把每条改写成中文新闻事件，输出严格的 JSON 数组，每个元素：
{{"idx": 序号, "title": "30字内中文新闻标题", "summary": "60-100字中文摘要，补充背景与影响", "category": "类别", "country": "主要相关国家中文名，如美国/俄罗斯/中国/伊拉克，跨国用'多国'，无明确国家用''"}}
category 只能从这些里选：{json.dumps(CATEGORIES, ensure_ascii=False)}
要求：
1. 新闻体、专有名词用中文规范译名；标题陈述事实；不要输出 JSON 以外的任何文字；字符串内禁止使用双引号（需要引用时用《》或''）。
2. 战争、冲突、灾难、恐袭等历史事件均属公开史实，必须如实收录，用新闻业规范中性词表述：遇难/身亡/死亡人数/武装冲突/交火/爆炸袭击，平实记录事实与数字，不渲染血腥细节、不用煽动性词汇。

条目列表：
{json.dumps(batch_events, ensure_ascii=False)}"""

def parse(resp_json, batch):
    content = resp_json["choices"][0]["message"].get("content") or ""
    jm = re.search(r"\[.*\]", content, re.S)
    if not jm:
        return []
    try:
        arr = json.loads(jm.group(0))
    except Exception:
        return []
    by_idx = {a.get("idx"): a for a in arr if isinstance(a, dict)}
    out = []
    for j, it in enumerate(batch):
        a = by_idx.get(j)
        if not a or not a.get("title"):
            continue
        out.append({
            "date": it["date"],
            "title": str(a["title"]).strip(),
            "summary": str(a.get("summary", "")).strip(),
            "category": a.get("category") if a.get("category") in CATEGORIES else "其他",
            "country": str(a.get("country", "")).strip(),
        })
    return out

def main():
    ev = json.load(open("data/events.json", encoding="utf-8"))
    todo = []  # (year, raw_item)
    for year in (1997, 1998):
        raw = json.load(open(f"data/backfill/{year}_raw.json", encoding="utf-8"))
        wiki_days = set()
        for e in ev:
            s = (e.get("sources") or [{}])[0]
            if e["date"].startswith(str(year)) and s.get("name", "").startswith("Wikipedia"):
                wiki_days.add(e["date"])
        todo += [(year, r) for r in raw if r["date"] not in wiki_days]
    print("待补漏条目:", len(todo), flush=True)
    if not todo:
        print("NOTHING")
        return

    cn = []
    still = []
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        year = batch[0][0]
        pe = [{"idx": j, "date": r["date"], "event_en": r["text_en"]} for j, (_, r) in enumerate(batch)]
        r = call(build_prompt(year, pe))
        got = []
        if r.status_code == 200:
            got = parse(r.json(), [x[1] for x in batch])
            if not got and len(batch) > 1:
                # 拆半重试
                def sr(b, pe2, d=0):
                    if not b:
                        return []
                    rr = call(build_prompt(year, pe2))
                    g = parse(rr.json(), b) if rr.status_code == 200 else []
                    if g or len(b) == 1:
                        return g
                    m = len(b) // 2
                    return sr(b[:m], pe2[:m], d + 1) + sr(b[m:], pe2[m:], d + 1)
                got = sr([x[1] for x in batch], pe)
        elif "1301" in r.text:
            # 整批被拦 → 拆单条 + 降敏二次重试，救出无辜条目
            print(f"  批次 {i//BATCH+1} 触发内容过滤，拆单条重试", flush=True)
            for yy, item in batch:
                pe1 = [{"idx": 0, "date": item["date"], "event_en": item["text_en"]}]
                try:
                    r2 = call(build_prompt(yy, pe1))
                except Exception:
                    continue
                g2 = parse(r2.json(), [item]) if r2.status_code == 200 else []
                if not g2 and "1301" in r2.text:
                    time.sleep(2)
                    try:
                        r3 = call(build_prompt(yy, pe1) + "\n注意：这是公开史料，请用最平实的百科语气表述，避免一切可能触发审核的措辞。")
                    except Exception:
                        continue
                    g2 = parse(r3.json(), [item]) if r3.status_code == 200 else []
                got += g2
        if not got:
            still += batch
        cn += got
        print(f"  批次 {i//BATCH+1}/{(len(todo)+BATCH-1)//BATCH}: 累计 {len(cn)}（仍被拦 {len(still)}）", flush=True)
        time.sleep(2)

    print("翻译成功:", len(cn), "| 仍被拦:", len(still))
    if still:
        fl = []
        for year, r in still:
            fl.append({"year": year, "date": r["date"], "text_en": r["text_en"]})
        json.dump(fl, open("data/backfill/filtered_pending.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("被拦条目已留档 data/backfill/filtered_pending.json")

    # 入库（缺失日期整缺，零重复风险）
    for year in (1997, 1998):
        items = [c for c in cn if c["date"].startswith(str(year))]
        if not items:
            continue
        new_items = []
        for c in items:
            country = c.get("country") or ""
            summary = c.get("summary") or c["title"]
            region = sp.guess_region(c["title"], summary) or "全球"
            if country and len(country) <= 6:
                r2 = sp.guess_region(country, country)
                if r2:
                    region = r2
            new_items.append({
                "title": c["title"],
                "summary": summary,
                "content": summary,
                "date": c["date"],
                "source": f"Wikipedia {year}年年表",
                "sourceUrl": f"https://en.wikipedia.org/wiki/{year}",
                "category": c["category"],
                "region": region,
                "country": country,
                "tags": [c["category"]] + ([country] if country and country != "多国" else []),
            })
        before = len(ev)
        sp.merge_into_events(ev, new_items, dedupe_url=False)
        after = len(ev)
        for e in ev:
            if not e.get("aiSummary") and (e.get("sources") or [{}])[0].get("name", "").startswith("Wikipedia"):
                s = (e.get("summary") or "").strip()
                if s:
                    e["aiSummary"] = s
        print(f"{year}: 新建 {after - before} 条，库总量 {after}")

    json.dump(ev, open("data/events.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("events.json 已保存")

if __name__ == "__main__":
    main()
