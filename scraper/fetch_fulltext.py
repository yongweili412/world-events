# -*- coding: utf-8 -*-
"""临时抓取全部新闻全文（用完即弃，不存档），供 AI 生成一分钟速览
输出: _fulltext.jsonl  每行 {"id": ..., "text": 前3000字}
"""
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

ROOT = Path(__file__).parent.parent
DATA = ROOT / "data" / "events.json"
OUT = ROOT / "_fulltext.jsonl"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
      "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}


def extract_text(html: str) -> str:
    # 去掉 script/style
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    # 中新网正文优先
    m = re.search(r'<div class="left_zw">(.*?)</div>\s*<', html, re.S)
    paras = []
    if m:
        src = m.group(1)
    else:
        src = html
        # 常见文章容器
        for pat in [r'<article[^>]*>(.*?)</article>', r'<div[^>]*class="[^"]*(?:article|content|body)[^"]*"[^>]*>(.*)']:
            mm = re.search(pat, src, re.S | re.I)
            if mm:
                src = mm.group(1)
                break
    for p in re.findall(r"<p[^>]*>(.*?)</p>", src, re.S):
        t = re.sub(r"<[^>]+>", "", p)
        t = t.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'")
        t = re.sub(r"\s+", " ", t).strip()
        if len(t) > 12:
            paras.append(t)
    text = "\n".join(paras)[:3000]
    return text


def fetch_one(ev):
    url = ev.get("sourceUrl") or ""
    if not url.startswith("http"):
        return ev["id"], ""
    for _ in range(2):
        try:
            r = requests.get(url, headers=UA, timeout=15)
            if r.status_code == 200 and len(r.content) > 500:
                if r.encoding in (None, "ISO-8859-1"):
                    r.encoding = r.apparent_encoding or "utf-8"
                return ev["id"], extract_text(r.text)
        except Exception:
            time.sleep(1)
        time.sleep(0.3)
    return ev["id"], ""


def main():
    events = json.loads(DATA.read_text(encoding="utf-8"))
    todo = [e for e in events if not e.get("aiSummary") and (e.get("sourceUrl") or "").startswith("http")]
    print(f"待抓取全文: {len(todo)} 条")
    done = 0
    with open(OUT, "w", encoding="utf-8") as f:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(fetch_one, e): e["id"] for e in todo}
            for fut in as_completed(futures):
                eid, text = fut.result()
                f.write(json.dumps({"id": eid, "text": text}, ensure_ascii=False) + "\n")
                done += 1
                if done % 100 == 0:
                    f.flush()
                    print(f"进度 {done}/{len(todo)}")
    print(f"✅ 全文抓取完成：{done} 条 -> {OUT.name}")


if __name__ == "__main__":
    main()
