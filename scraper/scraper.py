#!/usr/bin/env python3
"""
全球事件自动抓取脚本 - scraper.py
从多个 RSS 新闻源自动抓取全球大事，追加到 events.json

依赖安装：
  pip install feedparser requests

运行方式：
  python scraper.py

定时运行（每天自动执行）：
  Windows:  任务计划程序 添加每天运行本脚本的任务
  macOS/Linux: crontab -e 添加：0 8 * * * python /path/to/scraper.py
"""

import json
import os
import re
import time
import hashlib
import datetime
import email.utils
from pathlib import Path

try:
    import feedparser
    import requests
except ImportError:
    print("缺少依赖，请先运行：pip install feedparser requests")
    exit(1)

# ===== 配置区 =====
EVENTS_FILE = Path(__file__).parent.parent / "data" / "events.json"

# RSS 新闻源配置（可自由增删）
RSS_SOURCES = [
    {
        "name": "BBC World News",
        "url": "https://feeds.bbci.co.uk/news/world/rss.xml",
        "category": "国际政治",
        "region": "全球",
        "country": "英国",
        "lang": "en",
    },
    {
        "name": "The Guardian World",
        "url": "https://www.theguardian.com/world/rss",
        "category": "国际政治",
        "region": "全球",
        "country": "英国",
        "lang": "en",
    },
    {
        "name": "Al Jazeera",
        "url": "https://www.aljazeera.com/xml/rss/all.xml",
        "category": "国际政治",
        "region": "全球",
        "country": "卡塔尔",
        "lang": "en",
    },
    {
        "name": "NASA News",
        "url": "https://www.nasa.gov/rss/dyn/breaking_news.rss",
        "category": "科技",
        "region": "北美洲",
        "country": "美国",
        "lang": "en",
    },
    {
        "name": "UN News",
        "url": "https://news.un.org/feed/subscribe/en/news/all/rss.xml",
        "category": "国际政治",
        "region": "全球",
        "country": "美国",
        "lang": "en",
    },
    {
        "name": "SciTech Daily",
        "url": "https://scitechdaily.com/feed/",
        "category": "科技",
        "region": "全球",
        "country": "美国",
        "lang": "en",
    },
    # ↓↓↓ 国内可直接访问的中文源（lang=zh，无需翻译）↓↓↓
    {
        "name": "人民网国际",
        "url": "http://www.people.com.cn/rss/world.xml",
        "category": "国际政治",
        "region": "全球",
        "country": "中国",
        "lang": "zh",
    },
    {
        "name": "中新网即时新闻",
        "url": "http://www.chinanews.com.cn/rss/scroll-news.xml",
        "category": "社会",
        "region": "亚洲",
        "country": "中国",
        "lang": "zh",
    },
]

# 中文新闻源（需要能访问对应的 RSS）
CHINESE_SOURCES = [
    {
        "name": "新华社",
        "url": "http://www.xinhuanet.com/world/rss.xml",
        "category": "国际政治",
        "region": "亚洲",
        "country": "中国",
        "lang": "zh",
    },
    # 以下 RSS 地址仅供参考，实际可用性需要测试
    # {"name": "BBC中文", "url": "https://www.bbc.com/zhongwen/simp/index.xml",
    #  "category": "国际政治", "region": "全球", "country": "英国", "lang": "zh"},
]

# 关键词 → 分类映射（用于自动分类）
# 英文关键词用词边界匹配（避免 tech 误匹配 technology news 之外的词）
# 中文关键词用子串匹配
CATEGORY_KEYWORDS = {
    "自然灾害": ["earthquake", "seismic", "typhoon", "hurricane", "cyclone", "flood",
                "wildfire", "tsunami", "volcano", "landslide", "drought",
                "地震", "台风", "飓风", "洪水", "山火", "海啸", "火山", "山体滑坡", "旱灾"],
    "科技": ["AI", "artificial intelligence", "space telescope", "spacecraft", "satellite",
             "rocket", "NASA", "ISRO", "ESA", "robot", "quantum", "semiconductor",
             "chip", "algorithm", "smartphone", "software", "neurons", "brain scan",
             "太空", "航天", "发射", "卫星", "火箭", "机器人", "量子", "芯片", "算法", "科技"],
    "环境": ["climate", "carbon", "emission", "biodiversity", "deforestation",
             "renewable energy", "global warming", "sea level",
             "气候", "碳排放", "排放", "可再生", "全球变暖", "海平面", "生物多样性"],
    "经济": ["economy", "inflation", "stock market", "GDP", "tariff", "trade deal",
             "central bank", "interest rate", "recession",
             "经济", "通胀", "股市", "贸易", "利率", "关税", "央行", "衰退"],
    "体育": ["Olympics", "World Cup", "football", "soccer", "basketball", "tennis",
             "cricket", "tournament", "league", "Messi", "Ronaldo",
             "奥运", "世界杯", "足球", "篮球", "网球", "联赛", "冠军"],
    "冲突": ["war", "airstrike", "strike", "military", "missile", "troops", "ceasefire",
             "sanctions", "offensive", "hostage", "protest", "clash",
             "战争", "空袭", "军事", "导弹", "停火", "制裁", "抗议", "袭击", "冲突"],
    "国际政治": ["election", "president", "parliament", "diplomat", "summit", "treaty",
                 "minister", "chancellor", "prime minister", "vote", "senate",
                 "大选", "总统", "议会", "外交", "峰会", "条约", "首相"],
    "社会": ["school", "hospital", "police", "court", "trial", "migrant", "refugee",
             "protesters", "strike action", "workers",
             "学校", "医院", "警方", "法院", "移民", "难民", "罢工"],
}

# User-Agent（部分 RSS 源需要）
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; WorldEventsBot/1.0)"
}

# ===== 自动翻译配置 =====
# 使用 MyMemory 免费翻译接口（无需注册、国内可直连）
# 匿名额度约每天 5000 字符；填邮箱可提升到 50000 字符/天
TRANSLATE_ENABLED = True
TRANSLATE_TARGET = "zh-CN"
TRANSLATE_EMAIL = ""          # 可选：填入邮箱可提升免费额度
TRANSLATE_CACHE_FILE = Path(__file__).parent / "translate_cache.json"
TRANSLATE_MAX_CALLS = 200     # 单次运行最多调用次数，防止超额

_translate_cache = {}
_translate_calls = 0


# ===== 翻译工具 =====
def _load_translate_cache():
    global _translate_cache
    if TRANSLATE_CACHE_FILE.exists():
        try:
            with open(TRANSLATE_CACHE_FILE, "r", encoding="utf-8") as f:
                _translate_cache = json.load(f)
        except Exception:
            _translate_cache = {}


def _save_translate_cache():
    try:
        with open(TRANSLATE_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_translate_cache, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def translate_text(text: str, src: str = "en", tgt: str = None) -> str:
    """把文本翻译成目标语言，失败则原样返回（不会中断抓取）"""
    global _translate_calls
    tgt = tgt or TRANSLATE_TARGET
    if not TRANSLATE_ENABLED or not text or src == tgt:
        return text

    key = hashlib.md5(f"{src}|{tgt}|{text}".encode("utf-8")).hexdigest()
    if key in _translate_cache:
        return _translate_cache[key]
    if _translate_calls >= TRANSLATE_MAX_CALLS:
        return text

    try:
        params = {"q": text[:480], "langpair": f"{src}|{tgt}"}
        if TRANSLATE_EMAIL:
            params["de"] = TRANSLATE_EMAIL
        resp = requests.get(
            "https://api.mymemory.translated.net/get",
            params=params, headers=HEADERS, timeout=20
        )
        data = resp.json()
        _translate_calls += 1
        out = data.get("responseData", {}).get("translatedText", "")
        status = str(data.get("responseStatus", ""))
        # 接口超额或异常时返回原文，不破坏原有内容
        if out and status == "200" and "MYMEMORY WARNING" not in out.upper():
            _translate_cache[key] = out
            return out
    except Exception as e:
        print(f"   ⚠️ 翻译失败（保留原文）: {str(e)[:50]}")
    finally:
        time.sleep(0.35)  # 轻微限速，避免被封
    return text


# ===== 工具函数 =====
def make_id(title: str) -> str:
    """根据标题生成唯一 ID"""
    h = hashlib.md5(title.encode("utf-8")).hexdigest()[:8].upper()
    return f"EVT-{h}"


def guess_category(title: str, summary: str) -> str:
    """根据关键词自动判断分类（英文用词边界匹配，中文用于子串匹配）"""
    import re as _re
    text = (title + " " + summary)
    text_lower = text.lower()
    for cat, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if _is_chinese(kw):
                if kw in text:
                    return cat
            else:
                # 英文：词边界匹配，避免 tech 误匹配到无关词
                pattern = r"\b" + _re.escape(kw.lower()) + r"\b"
                if _re.search(pattern, text_lower):
                    return cat
    return "其他"


def _is_chinese(s: str) -> bool:
    """判断字符串是否包含中文字符"""
    return any('\u4e00' <= ch <= '\u9fff' for ch in s)


def guess_region(title: str, summary: str) -> str:
    """简单判断地区（英文用词边界匹配，可扩展）"""
    text = (title + " " + summary).lower()
    regions = [
        ("亚洲", ["china", "beijing", "shanghai", "japan", "tokyo", "korea", "india",
                  "delhi", "pakistan", "taipei", "seoul", "bangkok", "singapore",
                  "中国", "日本", "东京", "韩国", "印度", "巴基斯坦", "亚洲"]),
        ("欧洲", ["europe", "germany", "berlin", "france", "paris", "uk", "britain",
                  "london", "italy", "rome", "spain", "madrid", "brussels", "eu ",
                  "欧洲", "德国", "法国", "英国", "伦敦", "柏林", "巴黎"]),
        ("北美洲", ["usa", "america", "washington", "new york", "california", "canada",
                    "toronto", "mexico", "u.s.", "us ",
                    "美国", "华盛顿", "纽约", "加拿大", "墨西哥", "北美洲"]),
        ("非洲", ["africa", "nigeria", "egypt", "cairo", "kenya", "ethiopia", "sudan",
                  "南非", "尼日利亚", "埃及", "非洲"]),
        ("南美洲", ["south america", "brazil", "argentina", "chile", "colombia",
                    "巴西", "阿根廷", "智利", "南美洲"]),
        ("大洋洲", ["australia", "sydney", "new zealand", "oceania",
                    "澳大利亚", "悉尼", "新西兰", "大洋洲"]),
    ]
    for region_name, keywords in regions:
        for kw in keywords:
            if _is_chinese(kw):
                if kw in text:
                    return region_name
            else:
                pattern = r"\b" + re.escape(kw) + (r"" if kw.endswith((" ", ".")) else r"\b")
                if re.search(pattern, text):
                    return region_name
    return "全球"


def clean_text(text: str) -> str:
    """清理 HTML 标签和多余空白"""
    text = re.sub(r"<[^>]+>", "", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def load_events() -> list:
    """加载已有事件"""
    if EVENTS_FILE.exists():
        with open(EVENTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_events(events: list):
    """保存事件到 JSON 文件"""
    EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(EVENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False, indent=2)
    print(f"✅ 已保存 {len(events)} 条事件到 {EVENTS_FILE}")


def fetch_rss(source: dict) -> list[dict]:
    """抓取单个 RSS 源，返回事件列表"""
    print(f"📡 正在抓取：{source['name']}")
    try:
        resp = requests.get(source["url"], headers=HEADERS, timeout=15)
        resp.encoding = resp.apparent_encoding
        feed = feedparser.parse(resp.text)
    except Exception as e:
        print(f"   ❌ 抓取失败：{e}")
        return []

    items = []
    for entry in feed.entries[:10]:  # 每次最多取 10 条
        title = clean_text(getattr(entry, "title", ""))
        summary = clean_text(getattr(entry, "summary", getattr(entry, "description", "")))
        link = getattr(entry, "link", "")
        published = getattr(entry, "published", "")
        # 解析日期
        date_str = datetime.date.today().isoformat()
        try:
            import email.utils
            if published:
                t = email.utils.parsedate_to_datetime(published)
                if t:
                    # 转为本地日期
                    import time
                    date_str = t.astimezone().strftime("%Y-%m-%d")
        except Exception:
            pass

        # 自动分类
        category = guess_category(title, summary)
        region = guess_region(title, summary)

        # 英文源 → 自动翻译为中文（中文源跳过）
        translated = False
        if source.get("lang") == "en" and TRANSLATE_ENABLED:
            orig_title = title
            title = translate_text(title)
            if summary:
                summary = translate_text(summary)
            translated = (title != orig_title)

        items.append({
            "id": make_id(title + link),
            "title": title[:120],  # 标题截断
            "date": date_str,
            "category": category,
            "country": source.get("country", "未知"),
            "region": region,
            "summary": summary[:200] if summary else title[:200],
            "content": summary if summary else title,
            "image": "",
            "videoUrl": "",
            "source": source["name"],
            "sourceUrl": link,
            "tags": [category, source["name"].split()[0]],
            "translated": translated,
        })

    print(f"   ✅ 解析到 {len(items)} 条"
          + (f"（已翻译 {sum(1 for i in items if i['translated'])} 条）"
             if source.get("lang") == "en" and TRANSLATE_ENABLED else ""))
    return items


def deduplicate(existing: list, new_items: list) -> list:
    """去重：根据 ID 和标题去重"""
    existing_ids = {e.get("id", "") for e in existing}
    existing_titles = {e.get("title", "").lower() for e in existing}
    added = []
    for item in new_items:
        if item["id"] in existing_ids:
            continue
        if item["title"].lower() in existing_titles:
            continue
        existing.append(item)
        existing_ids.add(item["id"])
        existing_titles.add(item["title"].lower())
        added.append(item)
    return added


def main():
    print("🌍 全球事件自动抓取开始…")
    print(f"📅 {datetime.date.today().isoformat()}\n")

    _load_translate_cache()

    # 加载已有数据
    events = load_events()
    print(f"📂 已有事件：{len(events)} 条\n")

    # 抓取所有 RSS 源
    all_new = []
    for source in RSS_SOURCES:
        items = fetch_rss(source)
        all_new.extend(items)

    _save_translate_cache()

    # 去重并追加
    added = deduplicate(events, all_new)
    print(f"\n🆕 新增事件：{len(added)} 条")
    for item in added:
        flag = "译" if item.get("translated") else "  "
        print(f"   + [{flag}] {item['date']} | {item['category']} | {item['title'][:50]}")

    # 保存
    if added:
        save_events(events)
        print(f"\n✅ 完成！当前共 {len(events)} 条事件")
    else:
        print("\n✅ 没有新事件需要添加")

    print(f"🌐 本次调用翻译接口 {_translate_calls} 次")
    print("\n💡 提示：运行 build.py 后即可同步到网站")


if __name__ == "__main__":
    main()
