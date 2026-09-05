# -*- coding: utf-8 -*-
"""测试候选 RSS 源可达性（国内网络环境）"""
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import requests, feedparser

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; WorldEventsBot/1.0)"}

CANDIDATES = [
    # --- 国内权威媒体 ---
    ("新华社中文-国际", "http://www.xinhuanet.com/world/rss.xml", "zh"),
    ("新华网英文-世界", "http://www.news.cn/english/rss/worldrss.xml", "en"),
    ("中国日报-世界", "http://www.chinadaily.com.cn/rss/world_rss.xml", "en"),
    ("环球时报-英文", "http://www.globaltimes.cn/rss/outbrain.xml", "en"),
    ("人民网-时政", "http://www.people.com.cn/rss/politics.xml", "zh"),
    ("央视新闻CCTV", "https://news.cctv.com/2019/07/gaiban/cmsdatainterface/page/news_1.json", "zh"),
    # --- 国际权威媒体 ---
    ("CNN World", "http://rss.cnn.com/rss/edition_world.rss", "en"),
    ("DW 德国之声英文", "https://rss.dw.com/rdf/rss-en-all", "en"),
    ("France24 英文", "https://www.france24.com/en/rss", "en"),
    ("NHK World 英文", "https://www3.nhk.or.jp/nhkworld/en/news/feeds/all/?lang=en", "en"),
    ("CNA 亚洲新闻台", "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml", "en"),
    ("Euronews 英文", "https://www.euronews.com/rss?level=theme&name=news", "en"),
    ("Sky News World", "https://feeds.skynews.com/feeds/rss/world.xml", "en"),
    ("ABC 澳大利亚-世界", "https://www.abc.net.au/news/feed/51120/rss.xml", "en"),
    ("联合早报-世界", "https://www.zaobao.com.sg/rss/realtime/world", "zh"),
    ("Times of India-世界", "https://timesofindia.indiatimes.com/rssfeeds/296589292.cms", "en"),
    ("The Hindu-国际", "https://www.thehindu.com/news/international/feeder/default.rss", "en"),
]

ok = []
for name, url, lang in CANDIDATES:
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        ct = r.headers.get('content-type', '')
        feed = feedparser.parse(r.text)
        n = len(feed.entries)
        t = feed.entries[0].get('title', '')[:60] if n else ''
        if n >= 5:
            print(f"✅ {name}: {n} 条 | {t}")
            ok.append((name, url, lang))
        else:
            print(f"⚠️ {name}: HTTP {r.status_code}, {ct[:30]}, 仅 {n} 条（疑似非RSS）")
    except Exception as e:
        print(f"❌ {name}: {str(e)[:70]}")

print('\n=== 可用源 ===')
for n, u, l in ok:
    print(f'{n} | {u} | {l}')
