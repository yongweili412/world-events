# -*- coding: utf-8 -*-
"""从中新网按日期归档补录 2026-01-01 至今的国际大事（中文源，无需翻译）
用法: python backfill_2026.py
"""
import hashlib
import json
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from scraper import guess_category, guess_region  # 复用分类/地区判断

ROOT = Path(__file__).parent.parent
DATA = ROOT / "data" / "events.json"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
START = date(2026, 1, 1)
END = date(2026, 9, 3)
MAX_PER_DAY = 6
# "大事"关键词过滤（标题命中任一才收录）
BIG = [
    "战争", "袭击", "爆炸", "地震", "洪水", "台风", "飓风", "山火", "野火", "海啸",
    "火山", "干旱", "泥石流", "龙卷风", "选举", "当选", "投票", "峰会", "会晤",
    "签署", "制裁", "停火", "协议", "条约", "坠机", "空难", "沉船", "疫情", "病毒",
    "疫苗", "核", "航天", "火箭", "卫星", "登月", "联合国", "安理会", "G20", "APEC",
    "北约", "欧盟", "政变", "戒严", "抗议", "罢工", "刺杀", "暗杀", "遇难", "冲突",
    "导弹", "无人机", "军演", "军事演习", "通胀", "加息", "降息", "破产", "历史性",
    "首次", "重大", "紧急状态", "宣言", "独立", "公投", "禁令", "关税", "领导人",
]
ITEM_RE = re.compile(
    r'<div class="dd_lm">\[<a href=[^>]*>([^<]+)</a>\]</div>\s*'
    r'<div class="dd_bt"><a href="([^"]+)">([^<]+)</a></div>'
    r'<div class="dd_time">([^<]+)</div>'
)
PARA_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.S)


def norm_title(t: str) -> str:
    return re.sub(r"[\s【】\[\]｜|｜：:（(）)「」“”\"']", "", t)


def load_existing_keys():
    events = json.loads(DATA.read_text(encoding="utf-8"))
    keys = {norm_title(e["title"]) for e in events}
    return events, keys


def clean_text(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    return re.sub(r"\s+", " ", s).strip()


def fetch(session, url, tries=2):
    for i in range(tries):
        try:
            r = session.get(url, timeout=15)
            if r.status_code == 200 and len(r.content) > 1000:
                # 中新网归档页为 UTF-8，但响应头缺失 charset 导致 requests 误判为 ISO-8859-1
                if r.encoding in (None, "ISO-8859-1"):
                    r.encoding = r.apparent_encoding or "utf-8"
                return r.text
        except Exception:
            pass
        time.sleep(1.5)
    return None


def parse_article(session, url):
    """抓文章页，返回 (summary, content)"""
    html = fetch(session, url)
    if not html:
        return "", ""
    m = re.search(r'<div class="left_zw">(.*?)</div>\s*<', html, re.S)
    if not m:
        m = re.search(r'<div class="left_zw">(.*)', html, re.S)
        if not m:
            return "", ""
    paras = [clean_text(p) for p in PARA_RE.findall(m.group(1))]
    paras = [p for p in paras if len(p) > 15]
    if not paras:
        paras = [clean_text(m.group(1))]
    paras = [p.strip("　") for p in paras if p.strip("　")]
    summary = paras[0][:160] if paras else ""
    content = "\n\n".join(paras[:4])
    return summary, content


def main():
    events, keys = load_existing_keys()
    start_count = len(events)
    session = requests.Session()
    session.headers.update(UA)
    added = 0
    day = START
    while day <= END:
        mmdd = day.strftime("%m%d")
        url = f"https://www.chinanews.com.cn/scroll-news/{day.year}/{mmdd}/news.shtml"
        html = fetch(session, url)
        if html:
            picked = 0
            for cat, href, title, ttime in ITEM_RE.findall(html):
                if cat.strip() != "国际" or picked >= MAX_PER_DAY:
                    continue
                title = clean_text(title)
                if len(title) < 8 or norm_title(title) in keys:
                    continue
                if not any(k in title for k in BIG):
                    continue
                full_url = "https://www.chinanews.com.cn" + href if href.startswith("/") else href
                summary, content = parse_article(session, full_url)
                if not summary:
                    summary = title
                if not content:
                    content = title
                date_str = day.isoformat()
                eid = "EVT-" + hashlib.md5(f"{date_str}|{title}".encode()).hexdigest()[:8].upper()
                events.append({
                    "id": eid,
                    "title": title,
                    "date": date_str,
                    "category": guess_category(title, summary),
                    "country": "",
                    "region": guess_region(title, summary),
                    "summary": summary,
                    "content": content,
                    "image": "",
                    "videoUrl": "",
                    "source": "中国新闻网",
                    "sourceUrl": full_url,
                    "tags": ["国际", "中新网", "2026存档"],
                    "translated": True,
                })
                keys.add(norm_title(title))
                picked += 1
                added += 1
                time.sleep(0.2)
        else:
            print(f"  ! 抓取失败 {url}")
        if (day.day % 15) == 0:
            DATA.write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"进度 {day} 累计新增 {added}")
        day += timedelta(days=1)
        time.sleep(0.25)

    DATA.write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ 补录完成：新增 {added} 条，总数 {start_count} -> {len(events)}")


if __name__ == "__main__":
    main()
