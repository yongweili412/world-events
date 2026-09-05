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
import difflib
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
    # ↓↓↓ 国内权威媒体 ↓↓↓
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
    {
        "name": "新华网国际",
        "url": "http://www.xinhuanet.com/world/news_world.xml",
        "category": "国际政治",
        "region": "全球",
        "country": "中国",
        "lang": "zh",
    },
    {
        "name": "新华网时政",
        "url": "http://www.news.cn/politics/news_politics.xml",
        "category": "国际政治",
        "region": "中国",
        "country": "中国",
        "lang": "zh",
    },
    {
        "name": "环球时报英文版",
        "url": "https://www.globaltimes.cn/rss/outbrain.xml",
        "category": "国际政治",
        "region": "全球",
        "country": "中国",
        "lang": "en",
    },
    # ↓↓↓ 国际权威媒体 ↓↓↓
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
        "name": "Sky News World",
        "url": "https://feeds.skynews.com/feeds/rss/world.xml",
        "category": "国际政治",
        "region": "全球",
        "country": "英国",
        "lang": "en",
    },
    {
        "name": "DW 德国之声",
        "url": "https://rss.dw.com/rdf/rss-en-all",
        "category": "国际政治",
        "region": "欧洲",
        "country": "德国",
        "lang": "en",
    },
    {
        "name": "France24",
        "url": "https://www.france24.com/en/rss",
        "category": "国际政治",
        "region": "欧洲",
        "country": "法国",
        "lang": "en",
    },
    {
        "name": "Euronews",
        "url": "https://www.euronews.com/rss?level=theme&name=news",
        "category": "国际政治",
        "region": "欧洲",
        "country": "法国",
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
        "name": "CNA 亚洲新闻台",
        "url": "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml",
        "category": "国际政治",
        "region": "东南亚",
        "country": "新加坡",
        "lang": "en",
    },
    {
        "name": "ABC 澳大利亚",
        "url": "https://www.abc.net.au/news/feed/51120/rss.xml",
        "category": "国际政治",
        "region": "大洋洲",
        "country": "澳大利亚",
        "lang": "en",
    },
    {
        "name": "Times of India",
        "url": "https://timesofindia.indiatimes.com/rssfeeds/296589292.cms",
        "category": "国际政治",
        "region": "印度",
        "country": "印度",
        "lang": "en",
    },
    {
        "name": "The Hindu 国际",
        "url": "https://www.thehindu.com/news/international/feeder/default.rss",
        "category": "国际政治",
        "region": "印度",
        "country": "印度",
        "lang": "en",
    },
    {
        "name": "NHK World 日本",
        "url": "https://www3.nhk.or.jp/rss/news/cat0.xml",
        "category": "国际政治",
        "region": "亚洲",
        "country": "日本",
        "lang": "en",
    },
    {
        "name": "韩联社 Yonhap",
        "url": "https://en.yna.co.kr/RSS/news.xml",
        "category": "国际政治",
        "region": "亚洲",
        "country": "韩国",
        "lang": "en",
    },
    {
        "name": "RT 今日俄罗斯",
        "url": "https://www.rt.com/rss/news/",
        "category": "国际政治",
        "region": "全球",
        "country": "俄罗斯",
        "lang": "en",
    },
    # ↓↓↓ 科技/机构源 ↓↓↓
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
]
# 注：以下源已实测不可用，勿加回：
#   新华网英文 worldrss.xml / 中国日报 world_rss.xml / CNN edition_world.rss ——
#   RSS 均为多年前的死存档（返回 2017-2023 旧文），不是新闻
#   新华社中文 rss、央视、联合早报、NHK World —— RSS 404 不存在
#   2026-09-06 复测：央视网 news.cctv.com/data/index.json 是 2019 年旧数据(巴哈马"多里安")，
#   中国日报/CNN RSS 仍为死存档；可用新增源已加入上方（新华网国际/时政、环球时报英文、NHK World、韩联社、RT）

# HTML 直抓源（无可用 RSS 的权威媒体，直接解析列表页→文章页；2026-09-06 实测可用）
# 每源配置：列表页 url、文章链接正则（捕获组 1 = 完整或相对链接）、日期提取（优先 meta/JSON-LD，date_url 为 URL 内日期模式兜底）
HTML_SOURCES = [
    {
        "name": "央视网国际",
        "url": "https://news.cctv.com/world/",
        "link_pattern": r'href="(https://news\.cctv\.com/20\d{2}/\d{2}/\d{2}/[A-Za-z0-9]+\.shtml)"',
        "date_url": r'/20(\d{2})/(\d{2})/(\d{2})/',
        "category": "国际政治",
        "region": "全球",
        "lang": "zh",
    },
    {
        "name": "联合早报国际",
        "url": "https://www.zaobao.com.sg/realtime/world",
        "link_pattern": r'href="(/news/(?:world|china)/story\d{8}-\d+)"',
        "url_prefix": "https://www.zaobao.com.sg",
        "date_url": r'story(20\d{6})',
        "category": "国际政治",
        "region": "东南亚",
        "lang": "zh",
    },
    {
        "name": "凤凰网国际",
        "url": "https://news.ifeng.com/listpage/11502/0/1/rtlist.shtml",
        "link_pattern": r'href="(https?://news\.ifeng\.com/c/[0-9a-zA-Z]+)"',
        "category": "国际政治",
        "region": "全球",
        "lang": "zh",
    },
    {
        "name": "澎湃新闻",
        "url": "https://www.thepaper.cn/",
        "link_pattern": r'href="(/newsDetail_forward_\d+)"',
        "url_prefix": "https://www.thepaper.cn",
        "category": "社会",
        "region": "中国",
        "lang": "zh",
    },
    {
        "name": "界面新闻国际",
        "url": "https://www.jiemian.com/lists/2.html",
        "link_pattern": r'href="(https?://www\.jiemian\.com/article/\d+\.html)"',
        "category": "国际政治",
        "region": "全球",
        "lang": "zh",
    },
    {
        "name": "国际在线",
        "url": "https://news.cri.cn/world",
        "link_pattern": r'href="(/20\d{6}/[0-9a-f\-]+\.html)"',
        "url_prefix": "https://news.cri.cn",
        "date_url": r'/(20\d{2})(\d{2})(\d{2})/',
        "category": "国际政治",
        "region": "全球",
        "lang": "zh",
    },
    {
        "name": "AP News",
        "url": "https://apnews.com/hub/ap-top-news",
        "link_pattern": r'href="(https?://apnews\.com/article/[0-9a-z\-]+)"',
        "category": "国际政治",
        "region": "全球",
        "lang": "en",
    },
    {
        "name": "共同社英文",
        "url": "https://english.kyodonews.net/news/world",
        "link_pattern": r'href="(/articles/-/\d+)"',
        "url_prefix": "https://english.kyodonews.net",
        "category": "国际政治",
        "region": "亚洲",
        "lang": "en",
    },
]
# 注：HTML 直抓不可用的站点（勿反复重试）：
#   Reuters —— www.reuters.com 返回 HTTP 401（反爬封锁，无 cookie 无法访问）
#   VOA —— 国内网络无法直连
#   参考消息 / 环球网 —— 前端渲染 SPA，列表页 HTML 无文章链接
#   大公文汇 —— 可抓但为繁体中文，与全站简体不一致，暂不接入

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


def guess_country(title: str, summary: str) -> str:
    """根据内容判断事发国家（避免把媒体所属国误当成事发地）；识别不出返回空字符串"""
    text = (title + " " + summary).lower()
    for country, keywords in COUNTRY_KEYWORDS.items():
        for kw in keywords:
            if _is_chinese(kw):
                if kw in text:
                    return country
            else:
                pattern = r"\b" + re.escape(kw) + (r"" if kw.endswith((" ", ".")) else r"\b")
                if re.search(pattern, text):
                    return country
    return ""


# 关键词 → 国家映射（用于 location.country；与 guess_region 同样的匹配规则）
COUNTRY_KEYWORDS = {
    "中国": ["中国", "中方", "北京", "上海", "台湾", "香港", "澳门",
            "china", "chinese", "beijing", "shanghai", "taiwan", "hong kong"],
    "美国": ["美国", "美方", "美军", "华盛顿", "白宫", "纽约", "五角大楼",
            "usa", "u.s.", "america", "american", "washington", "white house", "new york", "pentagon"],
    "俄罗斯": ["俄罗斯", "俄军", "俄方", "莫斯科", "普京", "克里姆林",
             "russia", "russian", "moscow", "putin", "kremlin"],
    "乌克兰": ["乌克兰", "乌军", "乌方", "基辅", "泽连斯基",
             "ukraine", "ukrainian", "kyiv", "zelensky"],
    "日本": ["日本", "东京", "日方", "japan", "japanese", "tokyo"],
    "韩国": ["韩国", "首尔", "韩方", "south korea", "seoul"],
    "朝鲜": ["朝鲜", "平壤", "north korea", "pyongyang"],
    "印度": ["印度", "新德里", "莫迪", "india", "indian", "delhi", "modi"],
    "巴基斯坦": ["巴基斯坦", "伊斯兰堡", "pakistan", "islamabad"],
    "阿富汗": ["阿富汗", "喀布尔", "afghanistan", "kabul", "taliban", "塔利班"],
    "英国": ["英国", "伦敦", "英方", "uk ", "britain", "british", "london", "england", "scotland"],
    "法国": ["法国", "巴黎", "法方", "france", "french", "paris"],
    "德国": ["德国", "柏林", "德方", "germany", "german", "berlin"],
    "意大利": ["意大利", "罗马", "italy", "italian", "rome"],
    "西班牙": ["西班牙", "马德里", "spain", "spanish", "madrid"],
    "波兰": ["波兰", "华沙", "poland", "polish", "warsaw"],
    "荷兰": ["荷兰", "阿姆斯特丹", "netherlands", "dutch", "amsterdam"],
    "瑞典": ["瑞典", "斯德哥尔摩", "sweden", "swedish", "stockholm"],
    "瑞士": ["瑞士", "苏黎世", "switzerland", "swiss", "zurich"],
    "土耳其": ["土耳其", "安卡拉", "伊斯坦布尔", "turkey", "turkish", "ankara", "istanbul"],
    "以色列": ["以色列", "特拉维夫", "耶路撒冷", "israel", "israeli", "tel aviv", "jerusalem"],
    "伊朗": ["伊朗", "德黑兰", "iran", "iranian", "tehran"],
    "沙特阿拉伯": ["沙特", "利雅得", "saudi", "riyadh"],
    "阿联酋": ["阿联酋", "迪拜", "uae", "dubai", "emirati"],
    "伊拉克": ["伊拉克", "巴格达", "iraq", "iraqi", "baghdad"],
    "叙利亚": ["叙利亚", "大马士革", "syria", "syrian", "damascus"],
    "黎巴嫩": ["黎巴嫩", "贝鲁特", "lebanon", "beirut"],
    "也门": ["也门", "yemen", "houthi", "胡塞"],
    "卡塔尔": ["卡塔尔", "多哈", "qatar", "doha"],
    "埃及": ["埃及", "开罗", "egypt", "egyptian", "cairo"],
    "苏丹": ["苏丹", "喀土穆", "sudan", "khartoum"],
    "埃塞俄比亚": ["埃塞俄比亚", "亚的斯亚贝巴", "ethiopia", "addis"],
    "尼日利亚": ["尼日利亚", "拉各斯", "nigeria", "lagos"],
    "肯尼亚": ["肯尼亚", "内罗毕", "kenya", "nairobi"],
    "南非": ["南非", "约翰内斯堡", "south africa", "johannesburg"],
    "刚果（金）": ["刚果", "congo"],
    "澳大利亚": ["澳大利亚", "悉尼", "澳方", "australia", "australian", "sydney", "canberra"],
    "新西兰": ["新西兰", "惠灵顿", "new zealand", "wellington"],
    "加拿大": ["加拿大", "渥太华", "多伦多", "canada", "canadian", "ottawa", "toronto"],
    "墨西哥": ["墨西哥", "mexico", "mexican"],
    "巴西": ["巴西", "巴西利亚", "brazil", "brazilian", "brasilia"],
    "阿根廷": ["阿根廷", "布宜诺斯艾利斯", "argentina", "buenos aires"],
    "智利": ["智利", "圣地亚哥", "chile", "chilean", "santiago"],
    "委内瑞拉": ["委内瑞拉", "加拉加斯", "venezuela", "caracas"],
    "哥伦比亚": ["哥伦比亚", "colombia", "colombian"],
    "秘鲁": ["秘鲁", "利马", "peru", "lima"],
    "越南": ["越南", "河内", "vietnam", "viet nam", "hanoi"],
    "泰国": ["泰国", "曼谷", "thailand", "bangkok"],
    "菲律宾": ["菲律宾", "马尼拉", "philippines", "manila"],
    "印度尼西亚": ["印尼", "印度尼西亚", "雅加达", "indonesia", "jakarta"],
    "马来西亚": ["马来西亚", "吉隆坡", "malaysia", "kuala lumpur"],
    "新加坡": ["新加坡", "singapore", "singaporean"],
    "缅甸": ["缅甸", "myanmar", "burma"],
    "柬埔寨": ["柬埔寨", "金边", "cambodia", "phnom penh"],
    "尼泊尔": ["尼泊尔", "加德满都", "nepal", "kathmandu"],
    "斯里兰卡": ["斯里兰卡", "科伦坡", "sri lanka", "colombo"],
    "孟加拉国": ["孟加拉", "达卡", "bangladesh", "dhaka"],
    "哈萨克斯坦": ["哈萨克斯坦", "阿斯塔纳", "kazakhstan", "astana"],
}


def guess_region(title: str, summary: str) -> str:
    """判断地区：具体国家/地区优先于大洲（英文用词边界匹配，中文子串匹配）"""
    text = (title + " " + summary).lower()
    regions = [
        # —— 具体国家/地区（优先匹配）——
        ("中国", ["中国", "中方", "我国", "北京", "上海", "台湾", "台北", "香港", "澳门",
                  "china", "chinese", "beijing", "shanghai", "taiwan", "taipei", "sino-"]),
        ("美国", ["美国", "美方", "美军", "华盛顿", "白宫", "纽约", "五角大楼", "美国总统",
                  "usa", "u.s.", "america", "american", "washington", "white house",
                  "new york", "pentagon", "trump", "biden"]),
        ("乌克兰", ["乌克兰", "乌军", "乌方", "基辅", "泽连斯基",
                    "ukraine", "ukrainian", "kyiv", "zelensky"]),
        ("俄罗斯", ["俄罗斯", "俄军", "俄方", "莫斯科", "普京", "克里姆林",
                    "russia", "russian", "moscow", "putin", "kremlin"]),
        ("东南亚", ["东南亚", "东盟", "越南", "泰国", "菲律宾", "印尼", "印度尼西亚", "缅甸",
                    "马来西亚", "新加坡", "柬埔寨", "老挝", "文莱", "河内", "曼谷", "马尼拉", "雅加达",
                    "vietnam", "viet nam", "thailand", "philippines", "indonesia", "myanmar",
                    "malaysia", "singapore", "cambodia", "laos", "brunei", "asean",
                    "hanoi", "bangkok", "manila", "jakarta"]),
        ("印度", ["印度", "新德里", "莫迪", "印方", "india", "indian", "delhi", "modi"]),
        ("中东", ["中东", "以色列", "加沙", "巴勒斯坦", "约旦河西岸", "伊朗", "伊拉克",
                  "叙利亚", "黎巴嫩", "约旦", "沙特", "阿联酋", "也门", "胡塞", "卡塔尔",
                  "科威特", "巴林", "阿曼", "霍尔木兹", "真主党", "哈马斯", "德黑兰",
                  "特拉维夫", "耶路撒冷", "利雅得", "巴格达",
                  "israel", "gaza", "palestin", "iran", "iraq", "syria", "lebanon",
                  "jordan", "saudi", "uae", "yemen", "houthi", "qatar", "kuwait",
                  "bahrain", "oman", "hormuz", "middle east", "hamas", "hezbollah",
                  "tehran", "tel aviv", "jerusalem", "riyadh", "baghdad"]),
        ("欧盟", ["欧盟", "欧洲联盟", "欧委会", "布鲁塞尔",
                  "european union", "european commission", "brussels"]),
        ("苏丹", ["苏丹", "喀土穆", "sudan", "khartoum"]),
        # —— 大洲兜底 ——
        ("亚洲", ["亚洲", "日本", "东京", "韩国", "朝鲜", "首尔", "平壤", "韩方", "日方",
                  "巴基斯坦", "阿富汗", "塔利班", "尼泊尔", "斯里兰卡", "孟加拉", "哈萨克斯坦",
                  "japan", "tokyo", "korea", "korean", "seoul", "pyongyang", "pakistan",
                  "afghanistan", "taliban", "nepal", "sri lanka", "bangladesh", "kazakhstan", "asia"]),
        ("欧洲", ["欧洲", "德国", "法国", "英国", "伦敦", "巴黎", "柏林", "意大利", "西班牙",
                  "波兰", "瑞士", "荷兰", "瑞典", "挪威", "芬兰", "北约", "匈牙利", "捷克",
                  "germany", "france", "uk ", "britain", "london", "paris", "berlin", "italy",
                  "spain", "poland", "nato", "europe", "european", "netherlands", "sweden",
                  "norway", "finland", "hungary", "czech", "estonia"]),
        ("非洲", ["非洲", "刚果", "埃塞俄比亚", "尼日利亚", "埃及", "肯尼亚", "南非", "埃博拉",
                  "africa", "congo", "ethiopia", "nigeria", "egypt", "kenya", "ebola"]),
        ("北美洲", ["加拿大", "墨西哥", "渥太华", "多伦多",
                    "canada", "canadian", "ottawa", "toronto", "mexico"]),
        ("南美洲", ["南美洲", "巴西", "阿根廷", "智利", "委内瑞拉", "哥伦比亚", "秘鲁",
                    "brazil", "argentina", "chile", "venezuela", "colombia", "peru"]),
        ("大洋洲", ["大洋洲", "澳大利亚", "新西兰", "悉尼", "澳方",
                    "australia", "sydney", "new zealand", "oceania"]),
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

        # 自动分类 + 地区/国家判定（国家从内容判断，避免误用媒体所属国）
        category = guess_category(title, summary)
        region = guess_region(title, summary)
        country = guess_country(title, summary) or ("中国" if source.get("lang") == "zh" and region == "中国" else "")

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
            "country": country,
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


def _norm(t: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", (t or "").lower())


def _find_event(events: list, item: dict):
    """在事件库中查找该报道所属的事件（标题相似 + 日期相近）"""
    nt = _norm(item["title"])
    from datetime import date as _d

    def _ord(s):
        y, m, dd = map(int, s.split("-"))
        return _d(y, m, dd).toordinal()

    for ev in events:
        if _norm(ev["title"])[:22] == nt[:22]:
            return ev
    for ev in events:
        try:
            gap = abs(_ord(ev["date"]) - _ord(item["date"]))
        except Exception:
            continue
        if gap <= 3 and difflib.SequenceMatcher(None, _norm(ev["title"]), nt).ratio() > 0.68:
            return ev
    return None


def merge_into_events(events: list, new_items: list) -> list:
    """v2 事件模型：新报道合并进已有事件（追加 source/timeline），否则新建事件。

    返回受影响的事件列表。URL 相同的报道直接跳过（同一篇报道不重复收录）。
    """
    seen_urls = {s.get("url") for e in events for s in (e.get("sources") or []) if s.get("url")}
    touched = []
    for item in new_items:
        url = item.get("sourceUrl") or ""
        if url and url in seen_urls:
            continue
        if url:
            seen_urls.add(url)
        ev = _find_event(events, item)
        snippet = re.sub(r"\s+", " ", (item.get("summary") or item.get("content") or "")).strip()[:120]
        src_entry = {
            "name": item.get("source") or "",
            "url": url,
            "date": item["date"],
            "title": item["title"],
            "snippet": snippet,
        }
        if ev is None:
            ymd = item["date"].replace("-", "")
            n = sum(1 for e in events if e["id"].startswith(f"evt_{ymd}")) + 1
            eid = f"evt_{ymd}_{n:03d}"
            while any(e["id"] == eid for e in events):
                n += 1
                eid = f"evt_{ymd}_{n:03d}"
            cat = item.get("category") or "其他"
            country = item.get("country") or ""
            region = item.get("region") or "全球"
            ev = {
                "id": eid,
                "legacyIds": [],
                "title": item["title"],
                "summary": snippet[:200] or item["title"],
                "description": snippet,
                "date": item["date"],
                "time": "",
                "location": {"country": country, "countryCode": "", "region": region, "city": ""},
                "category": [cat],
                "tags": item.get("tags") or [],
                "status": "closed",
                "sources": [src_entry],
                "timeline": [{"date": item["date"], "text": snippet[:90] or item["title"][:90]}],
                "relatedEvents": [],
            }
            events.append(ev)
        else:
            if src_entry not in (ev.get("sources") or []):
                ev.setdefault("sources", []).append(src_entry)
            ev.setdefault("timeline", []).append({"date": item["date"], "text": snippet[:90] or item["title"][:90]})
            if item["date"] < ev["date"]:
                ev["date"] = item["date"]
            dates = {s["date"] for s in ev["sources"]}
            ev["status"] = "ongoing" if len(dates) > 1 else ev.get("status", "closed")
        if ev not in touched:
            touched.append(ev)
    return touched


def _html_date(url: str, html: str, date_url_pat: str) -> str:
    """文章日期：JSON-LD / og meta / publishTime 优先，URL 模式兜底，最后今天"""
    # 1. JSON-LD datePublished / og:published_time / publishTime / pubdate
    for pat in (
        r'"datePublished"\s*:\s*"([^"]+)"',
        r'property="og:published_time"\s+content="([^"]+)"',
        r'content="([^"]+)"\s+property="og:published_time"',
        r'"publishTime"\s*:\s*"?(\d{4}[-/]\d{2}[-/]\d{2})',
        r'"pubDate"\s*:\s*"([^"]+)"',
        r'(20\d{2}-\d{2}-\d{2}[ T]\d{2}:\d{2})',
    ):
        m = re.search(pat, html)
        if m:
            d = m.group(1)[:10].replace("/", "-")
            if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", d):
                return d
    # 2. URL 内嵌日期（如 story20260905-xxx / /2026/09/05/ / /20260905/）
    if date_url_pat:
        m = re.search(date_url_pat, url)
        if m:
            try:
                parts = m.groups()
                if len(parts) == 1:  # story20260905 → YYYYMMDD
                    s = parts[0]
                    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
                if len(parts) == 3:  # /2026/09/05/ 或 /20260905 分组
                    y, mo, d = parts
                    if len(y) == 2:
                        y = "20" + y
                    return f"{y}-{mo}-{d}"
            except Exception:
                pass
    return datetime.date.today().isoformat()


def fetch_html(source: dict) -> list[dict]:
    """HTML 直抓：列表页 → 文章链接 → 逐篇抓取标题/正文/日期"""
    print(f"📰 正在直抓：{source['name']}")
    try:
        resp = requests.get(source["url"], headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            print(f"   ❌ 列表页 HTTP {resp.status_code}")
            return []
        resp.encoding = resp.apparent_encoding
        page = resp.text
    except Exception as e:
        print(f"   ❌ 抓取失败：{e}")
        return []

    raw_links = re.findall(source["link_pattern"], page)
    prefix = source.get("url_prefix", "")
    links, seen = [], set()
    for l in raw_links:
        full = l if l.startswith("http") else prefix + l
        if full in seen:
            continue
        seen.add(full)
        links.append(full)
    links = links[:8]  # 每源每次最多 8 篇
    if not links:
        print("   ⚠️ 列表页未命中文章链接（页面结构可能已变化）")
        return []

    items = []
    for url in links:
        try:
            ar = requests.get(url, headers=HEADERS, timeout=15)
            if ar.status_code != 200:
                continue
            ar.encoding = ar.apparent_encoding
            art = ar.text
        except Exception:
            continue
        m = re.search(r"<title[^>]*>([^<]+)</title>", art)
        if not m:
            continue
        title = clean_text(m.group(1))
        # 迭代剥掉标题尾部站点/频道名（"xx_运动家_澎湃新闻" "xx - 联合早报" "xx_新闻频道" 等）
        _SUFFIXES = {"联合早报", "凤凰网", "凤凰网资讯", "国际在线", "澎湃新闻", "The Paper",
                     "界面新闻", "新闻频道", "央视网(cctv.com)", "央视网", "澎湃新闻-The Paper"}
        while True:
            m2 = re.search(r"[-_|｜_]([^_|｜]+)$", title)
            if m2 and m2.group(1).strip() in _SUFFIXES:
                title = title[: m2.start()].strip()
                title = re.sub(r"[-_|｜_\s]+$", "", title)
            else:
                break
        if len(title) < 8:
            continue
        # 正文：取前 8 个较长的 <p> 段落
        paras = re.findall(r"<p[^>]*>([\s\S]{40,600}?)</p>", art)
        body = clean_text(" ".join(paras[:8]))[:1200]
        if len(body) < 40:
            continue
        date_str = _html_date(url, art, source.get("date_url"))
        # 时效过滤：HTML 列表页可能混有旧文，7 天前的直接丢弃
        try:
            if datetime.date.fromisoformat(date_str) < datetime.date.today() - datetime.timedelta(days=7):
                continue
        except Exception:
            pass

        category = guess_category(title, body)
        region = guess_region(title, body)
        country = guess_country(title, body) or ("中国" if source.get("lang") == "zh" and region == "中国" else "")

        translated = False
        if source.get("lang") == "en" and TRANSLATE_ENABLED:
            orig_title = title
            title = translate_text(title)
            if body:
                body = translate_text(body)
            translated = (title != orig_title)

        items.append({
            "id": make_id(title + url),
            "title": title[:120],
            "date": date_str,
            "category": category,
            "country": country,
            "region": region,
            "summary": body[:200] if body else title[:200],
            "content": body if body else title,
            "image": "",
            "videoUrl": "",
            "source": source["name"],
            "sourceUrl": url,
            "tags": [category, source["name"].split()[0]],
            "translated": translated,
        })
    print(f"   ✅ 抓到 {len(items)} 篇文章")
    return items


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

    # HTML 直抓源（无 RSS 的权威媒体）
    for source in HTML_SOURCES:
        items = fetch_html(source)
        all_new.extend(items)

    _save_translate_cache()

    # 合并进事件模型（一事件多来源）
    touched = merge_into_events(events, all_new)
    n_new = sum(1 for e in touched if len(e.get("sources") or []) == 1)
    print(f"\n🆕 新建事件 {n_new} 个，更新已有事件 {len(touched) - n_new} 个")
    for ev in touched:
        print(f"   · {ev['date']} | {ev['title'][:50]} ({len(ev.get('sources') or [])} 来源)")

    # 保存
    if touched:
        save_events(events)
        print(f"\n✅ 完成！当前共 {len(events)} 个事件")
    else:
        print("\n✅ 没有新事件需要添加")

    print(f"🌐 本次调用翻译接口 {_translate_calls} 次")
    print("\n💡 提示：运行 build.py 后即可同步到网站")


if __name__ == "__main__":
    main()
