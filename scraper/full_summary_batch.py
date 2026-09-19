# -*- coding: utf-8 -*-
"""全文速览批量推进：取 N 条无 summaryFull 的事件，抓原文全文，LLM 基于全文生成速览"""
import json, re, time, sys, os, requests
from llm_guard import resolve_model, chat_raw  # 模型守卫：限时免费优先，其次 flash；禁用 GLM-5.3/KIMI K3

KEY = os.environ.get("LLM_API_KEY", "995611b2762144e88e023c856da104eb.d68AuncjSy9Qrd0O").strip()
ALL_SOURCES = os.environ.get("ALL_SOURCES") == "1"  # 云端网络畅通可抓外文源，本机默认仅国内源
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 30
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"}

def fetch_fulltext(url):
    """抓正文：容器起点定位 + 向后截 9000 字再去标签（非贪婪 </div> 会提前截断，勿用）
    返回 (text, image_url)，均可能为 None"""
    try:
        r = requests.get(url, headers=UA, timeout=15)
        if r.status_code != 200:
            return None, None
        if not r.encoding or r.encoding.lower() in ("iso-8859-1", "ascii"):
            r.encoding = r.apparent_encoding
        html = r.text
    except Exception:
        return None, None
    # 顺带提取主图（复用 scraper 的提取器）
    image = None
    try:
        import sys as _sys
        if "scraper" not in _sys.modules:
            _sys.path.insert(0, "scraper")
        import scraper as _sp
        image = _sp.extract_og_image(html, url)
    except Exception:
        image = None
    # 容器起点定位（常见正文容器 id/class），找不到就从 body 开始
    m = re.search(r'<div[^>]*(?:id|class)="(?:left_zw|content|article[_-]?content|article|TRS_Editor|zoom)"[^>]*>', html, re.I)
    start = m.start() if m else 0
    chunk = html[start:start + 9000]
    chunk = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", chunk, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", chunk)
    text = re.sub(r"\s+", " ", text).strip()
    return (text[:3000] if len(text) > 200 else None), (image or None)

def gen_full_summary(title, text, date):
    prompt = f"""你是新闻编辑，为"世界事件档案"网站的这条事件写一份"全文速览"（summaryFull）。
要求：基于提供的原文全文（不是仅凭标题），200-300字，覆盖事件全貌：发生时间地点、核心事实与数字、背景、各方反应或影响。平实新闻体，不用夸张词；字符串内禁止双引号。输出纯文本，不要 JSON、不要标题、不要"以下是"之类开场白。

事件：{title}（{date}）
原文全文：
{text}"""
    try:
        # 限时免费模型优先，遇 429/5xx 自动换档
        r = chat_raw(prompt, key=KEY, timeout=120)
        if r.status_code != 200:
            return None
        content = (r.json()["choices"][0]["message"].get("content") or "").strip()
        content = re.sub(r"^```[a-z]*\n?|```$", "", content.strip(), flags=re.M).strip()
        return content if 100 <= len(content) <= 600 else (content[:600] if len(content) > 600 else None)
    except Exception:
        return None

def main():
    ev = json.load(open("data/events.json", encoding="utf-8"))
    # 国内可达源域名（本机网络可直连）；外文被墙源留给云端处理
    CN_HOSTS = ("chinanews.com.cn", "people.com.cn", "cctv.com", "thepaper.cn", "news.cn",
                "xinhuanet.com", "huanqiu.com", "ifeng.com", "jiemian.com", "cri.cn",
                "zaobao.com", "chinanews.com", "gmw.cn", "yicai.com", "cnbeta.com")
    def is_cn(e):
        u = (e.get("sources") or [{}])[0].get("url") or ""
        return any(h in u for h in CN_HOSTS)
    targets = sorted(
        [e for e in ev if not e.get("summaryFull") and (e.get("sources") or [{}])[0].get("url") and (ALL_SOURCES or is_cn(e))],
        key=lambda e: e["date"], reverse=True,
    )[:LIMIT]
    print("本轮推进全文速览:", len(targets), "条" + ("" if ALL_SOURCES else "（仅国内可达源）"), flush=True)
    ok, skip = 0, 0
    for i, e in enumerate(targets):
        url = e["sources"][0]["url"]
        text, img = fetch_fulltext(url)
        if img and not e.get("image"):
            e["image"] = img  # 顺带补齐主图（即便正文抓取失败也已写入）
        if not text:
            skip += 1
            print(f"  [{i+1}/{len(targets)}] 抓全文失败，跳过: {e['title'][:40]}", flush=True)
            continue
        s = gen_full_summary(e["title"], text, e["date"])
        if s:
            # 修正：全文速览写入 aiSummary，summaryFull 只作"基于全文"的布尔标记
            e["aiSummary"] = s
            e["summaryFull"] = True
            # 留档：速览所依据的原文片段（供事后核验数字/事实，避免"无法追溯"）
            e["sourceExcerpt"] = re.sub(r"\s+", " ", text)[:400]
            e["summaryBasedOn"] = (e.get("sources") or [{}])[0].get("url", "")
            e["summaryAt"] = time.strftime("%Y-%m-%d")
            ok += 1
            print(f"  [{i+1}/{len(targets)}] ✓ {e['title'][:40]}", flush=True)
        else:
            skip += 1
            print(f"  [{i+1}/{len(targets)}] 生成失败: {e['title'][:40]}", flush=True)
        time.sleep(1.5)
    json.dump(ev, open("data/events.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    total = len([e for e in ev if e.get("summaryFull")])
    print(f"DONE: 成功 {ok}，跳过 {skip}，库内 summaryFull 总数 {total}/{len(ev)}")

if __name__ == "__main__":
    main()
