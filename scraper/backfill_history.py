# -*- coding: utf-8 -*-
"""30 年历史大事回填（维基百科年表 → 中文事件库）

两阶段解耦：
  阶段1 抓取解析（无需 API key）：抓 en.wikipedia.org/wiki/{YYYY} 的 Events 章节，
        存 data/backfill/{YYYY}_raw.json
  阶段2 LLM 翻译+入库（需 LLM_API_KEY）：OpenAI 兼容接口批量翻译成中文，
        复用 scraper.merge_into_events 合并进事件库

用法：
  python scraper/backfill_history.py auto          # 自动取下 2 个未完成年份
  python scraper/backfill_history.py 2003 2004     # 处理指定年份
  python scraper/backfill_history.py status        # 查看进度
"""
import json
import os
import re
import sys
import io
import time
import datetime
import requests
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent))

SCRAPER_DIR = Path(__file__).parent
BACKFILL_DIR = SCRAPER_DIR.parent / "data" / "backfill"
PROGRESS_FILE = SCRAPER_DIR / "backfill_progress.json"
RAW_FILE = str(BACKFILL_DIR / "{year}_raw.json")

ALL_YEARS = list(range(1997, 2026))  # 1997-2025 共 29 年（2026 年已有逐日数据）
YEARS_PER_RUN = 2                    # auto 模式每次处理的年份数

WIKI_UA = {"User-Agent": "WorldEventsBot/1.0 (education archive; contact via github.com/yongweili412/world-events)"}

MONTHS = {
    "January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
    "July": 7, "August": 8, "September": 9, "October": 10, "November": 11, "December": 12,
}

CATEGORIES = ["国际政治", "军事冲突", "经济金融", "科技", "灾难事故", "社会", "体育", "文化", "其他"]

# ---------- 进度 ----------
def load_progress():
    if PROGRESS_FILE.exists():
        return json.load(open(PROGRESS_FILE, encoding="utf-8"))
    return {"fetched": [], "merged": []}


def save_progress(p):
    json.dump(p, open(PROGRESS_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


# ---------- 阶段 1：抓取解析 ----------
def _parse_events_html(html: str, year: int) -> list:
    """从维基年表页 HTML 解析 Events 章节 → [{date, text_en}]（独立函数，便于测试）"""
    # 截取 Events 章节：正常在下一个同级 h2 章节（Births/Deaths 等）截断；
    # 另设 id 哨兵保险——无论 Births/Deaths 等挂在哪级标题都能截断
    m = re.search(r'id="Events"', html)
    if not m:
        return []
    seg = html[m.start():]
    cuts = []
    m2 = re.search(r"<h2[\s>]", seg[100:])
    if m2:
        cuts.append(100 + m2.start())
    m3 = re.search(r'id="(?:Births|Deaths|Nobel[^"]*|Holidays|References|See_also|External_links)"', seg[100:])
    if m3:
        cuts.append(100 + m3.start())
    if cuts:
        seg = seg[:min(cuts)]

    # 手写配对扫描提取顶层 <li>（非贪婪正则在嵌套 <ul><li> 处会提前截断，
    # 全局删除嵌套 ul 又会误伤最外层列表，故按 <ul>/</ul> 深度找配对的 </li>）
    TOK = re.compile(r"<ul>|</ul>|<li[^>]*>|</li>")
    top_lis = []
    pos = 0
    while True:
        s = seg.find("<li", pos)
        while s != -1 and s + 3 < len(seg) and seg[s + 3] not in (">", " "):
            s = seg.find("<li", s + 3)
        if s == -1:
            break
        depth = 0
        j = s
        end = None
        while True:
            tk = TOK.search(seg, j)
            if not tk:
                end = len(seg)
                break
            tok = tk.group(0)
            if tok == "<ul>":
                depth += 1
            elif tok == "</ul>":
                depth -= 1
            elif tok.startswith("</li") and depth == 0:
                end = tk.start()
                break
            j = tk.end()
        top_lis.append(seg[s:end])
        pos = end if end is not None else len(seg)

    items = []
    for li in top_lis:
        # 去嵌套子列表与其中的标签
        li = re.sub(r"<ul>.*?</ul>", "", li, flags=re.S)
        text = re.sub(r"<[^>]+>", "", li)
        text = re.sub(r"\[[0-9]+\]", "", text)          # 引用标记
        text = re.sub(r"\s+", " ", text).strip()
        # 日期模式："January 1 – ..." / "January 1 - ..." / "January 1: ..."
        dm = re.match(r"([A-Z][a-z]+)\s+(\d{1,2})\s*[–\-—:]\s*(.+)", text)
        if not dm:
            continue
        mon, day, body = dm.group(1), int(dm.group(2)), dm.group(3).strip()
        if mon not in MONTHS or not (1 <= day <= 31) or len(body) < 25:
            continue
        try:
            date = f"{year}-{MONTHS[mon]:02d}-{day:02d}"
        except Exception:
            continue
        items.append({"date": date, "text_en": body[:600]})
    # 去重
    seen, out = set(), []
    for it in items:
        k = it["text_en"][:80]
        if k not in seen:
            seen.add(k)
            out.append(it)
    return out


def fetch_year(year: int) -> list:
    """抓取维基年表页并解析 Events 章节"""
    url = f"https://en.wikipedia.org/wiki/{year}"
    r = requests.get(url, headers=WIKI_UA, timeout=60)
    r.raise_for_status()
    return _parse_events_html(r.text, year)


def stage_fetch(years):
    BACKFILL_DIR.mkdir(parents=True, exist_ok=True)
    p = load_progress()
    for y in years:
        if y in p["fetched"]:
            print(f"✓ {y} 已抓取，跳过")
            continue
        print(f"📥 抓取 {y} 年大事…")
        try:
            items = fetch_year(y)
        except Exception as e:
            print(f"  ❌ 抓取失败: {str(e)[:80]}")
            continue
        json.dump(items, open(RAW_FILE.format(year=y), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        p["fetched"].append(y)
        save_progress(p)
        print(f"  ✅ {y}: {len(items)} 条大事 → {y}_raw.json")
        time.sleep(2)


# ---------- 阶段 2：LLM 翻译 + 合并 ----------
def llm_translate(year: int, items: list) -> list:
    """批量翻译：输入 [{date, text_en}]，输出 [{date, title, summary, category, country}]"""
    api_key = os.environ.get("LLM_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("未配置 LLM_API_KEY，跳过翻译阶段")
    base = os.environ.get("LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4").rstrip("/")
    model = os.environ.get("LLM_MODEL", "glm-4.5-flash")

    out = []
    BATCH = 10
    for i in range(0, len(items), BATCH):
        batch = items[i:i + BATCH]
        payload_events = [{"idx": j, "date": it["date"], "event_en": it["text_en"]} for j, it in enumerate(batch)]
        prompt = f"""你是新闻编辑，正在为"世界事件档案"网站整理 {year} 年的历史大事。
下面是英文维基百科的 {year} 年大事条目（idx 为序号）。请把每条改写成中文新闻事件，输出严格的 JSON 数组，每个元素：
{{"idx": 序号, "title": "30字内中文新闻标题", "summary": "60-100字中文摘要，补充背景与影响", "category": "类别", "country": "主要相关国家中文名，如美国/俄罗斯/中国/伊拉克，跨国用'多国'，无明确国家用''"}}
category 只能从这些里选：{json.dumps(CATEGORIES, ensure_ascii=False)}
要求：新闻体、专有名词用中文规范译名；标题陈述事实；不要输出 JSON 以外的任何文字。

条目列表：
{json.dumps(payload_events, ensure_ascii=False)}"""
        resp = None
        content = None
        for attempt in (1, 2):  # 失败重试 1 次
            resp = requests.post(
                f"{base}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "thinking": {"type": "disabled"},
                },
                timeout=300,
            )
            if resp.status_code == 200:
                break
            print(f"  ⚠️ 批次请求 HTTP {resp.status_code}（第 {attempt} 次）: {resp.text[:200]}")
            time.sleep(5)
        if resp is None or resp.status_code != 200:
            print(f"  ❌ 第 {i//BATCH+1} 批请求失败，跳过 {len(batch)} 条")
            continue
        content = resp.json()["choices"][0]["message"].get("content") or ""
        # 容错解析 JSON
        jm = re.search(r"\[.*\]", content, re.S)
        if not jm:
            print(f"  ⚠️ 第 {i//BATCH+1} 批 JSON 解析失败，跳过 {len(batch)} 条 | content头: {content[:120]!r}")
            continue
        try:
            arr = json.loads(jm.group(0))
        except Exception:
            arr = []
            print(f"  ⚠️ 第 {i//BATCH+1} 批 JSON 无效，跳过")
            continue
        by_idx = {a.get("idx"): a for a in arr if isinstance(a, dict)}
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
        print(f"  ✈️ 批次 {i//BATCH+1}/{(len(items)+BATCH-1)//BATCH}: 累计翻译 {len(out)} 条")
        time.sleep(2)
    return out


def stage_merge(years):
    import scraper as sp  # 复用合并逻辑与地区推断
    p = load_progress()
    events = sp.load_events()
    for y in years:
        raw_file = Path(RAW_FILE.format(year=y))
        if y in p["merged"]:
            print(f"✓ {y} 已合并入库，跳过")
            continue
        if not raw_file.exists():
            print(f"⚠️ {y} 原始数据不存在（先完成抓取阶段）")
            continue
        items = json.load(open(raw_file, encoding="utf-8"))
        if not items:
            p["merged"].append(y)
            save_progress(p)
            continue
        if not os.environ.get("LLM_API_KEY", "").strip():
            print(f"⏸ 未配置 LLM_API_KEY，{y} 暂不翻译（原始数据已就绪）")
            break
        print(f"🌐 {y}: LLM 翻译 {len(items)} 条…")
        try:
            cn = llm_translate(y, items)
        except Exception as e:
            print(f"  ❌ 翻译失败: {str(e)[:100]}")
            continue
        wiki_url = f"https://en.wikipedia.org/wiki/{y}"
        new_items = []
        for c in cn:
            country = c.get("country") or ""
            summary = c.get("summary") or c["title"]
            region = sp.guess_region(c["title"], summary) or "全球"
            if country and len(country) <= 6:
                r2 = sp.guess_region(country, country)
                if r2:
                    region = r2
            new_items.append({
                "title": c["title"],
                "summary": c.get("summary") or c["title"],
                "content": c.get("summary") or c["title"],
                "date": c["date"],
                "source": f"Wikipedia {y}年年表",
                "sourceUrl": wiki_url,
                "category": c["category"],
                "region": region,
                "country": country,
                "tags": [c["category"]] + ([country] if country and country != "多国" else []),
            })
        before = len(events)
        sp.merge_into_events(events, new_items)
        sp.save_events(events)
        after = len(events)
        p["merged"].append(y)
        save_progress(p)
        print(f"  ✅ {y}: 翻译 {len(cn)} 条 → 新建 {after - before} / 并入 {len(cn) - (after - before)} | 库总量 {after}")
        time.sleep(2)


def main():
    args = sys.argv[1:]
    p = load_progress()
    if args and args[0] == "status":
        print(f"共 {len(ALL_YEARS)} 年（1997-2025）")
        print(f"已抓取 {len(p['fetched'])} 年: {p['fetched']}")
        print(f"已入库 {len(p['merged'])} 年: {p['merged']}")
        left = [y for y in ALL_YEARS if y not in p['fetched']]
        print(f"未抓取: {left}")
        return
    if args and args[0] == "auto":
        todo = [y for y in ALL_YEARS if y not in p["fetched"]]
        years = todo[:YEARS_PER_RUN] if todo else [y for y in ALL_YEARS if y not in p["merged"]][:2]
        if not todo:
            stage_merge(years)
        else:
            stage_fetch(years)
            # 抓取完成后若 key 可用，顺带合并本轮抓的年份
            stage_merge([y for y in years if y in load_progress()["fetched"]])
        return
    years = [int(a) for a in args if a.isdigit()]
    stage_fetch(years)
    stage_merge(years)


if __name__ == "__main__":
    main()
