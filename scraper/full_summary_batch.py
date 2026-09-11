# -*- coding: utf-8 -*-
"""全文速览批量推进：取 N 条无 summaryFull 的事件，抓原文全文，LLM 基于全文生成速览"""
import json, re, time, sys, requests

KEY = "995611b2762144e88e023c856da104eb.d68AuncjSy9Qrd0O"
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 30
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"}

def fetch_fulltext(url):
    """抓正文：容器起点定位 + 向后截 9000 字再去标签（非贪婪 </div> 会提前截断，勿用）"""
    try:
        r = requests.get(url, headers=UA, timeout=15)
        if r.status_code != 200:
            return None
        if not r.encoding or r.encoding.lower() in ("iso-8859-1", "ascii"):
            r.encoding = r.apparent_encoding
        html = r.text
    except Exception:
        return None
    # 容器起点定位（常见正文容器 id/class），找不到就从 body 开始
    m = re.search(r'<div[^>]*(?:id|class)="(?:left_zw|content|article[_-]?content|article|TRS_Editor|zoom)"[^>]*>', html, re.I)
    start = m.start() if m else 0
    chunk = html[start:start + 9000]
    chunk = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", chunk, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", chunk)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:3000] if len(text) > 200 else None

def gen_full_summary(title, text, date):
    prompt = f"""你是新闻编辑，为"世界事件档案"网站的这条事件写一份"全文速览"（summaryFull）。
要求：基于提供的原文全文（不是仅凭标题），200-300字，覆盖事件全貌：发生时间地点、核心事实与数字、背景、各方反应或影响。平实新闻体，不用夸张词；字符串内禁止双引号。输出纯文本，不要 JSON、不要标题、不要"以下是"之类开场白。

事件：{title}（{date}）
原文全文：
{text}"""
    try:
        r = requests.post(
            "https://open.bigmodel.cn/api/paas/v4/chat/completions",
            headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"},
            json={
                "model": "glm-4.5-flash",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "thinking": {"type": "disabled"},
            },
            timeout=120,
        )
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
        [e for e in ev if not e.get("summaryFull") and (e.get("sources") or [{}])[0].get("url") and is_cn(e)],
        key=lambda e: e["date"], reverse=True,
    )[:LIMIT]
    print("本轮推进全文速览:", len(targets), "条（仅国内可达源）", flush=True)
    ok, skip = 0, 0
    for i, e in enumerate(targets):
        url = e["sources"][0]["url"]
        text = fetch_fulltext(url)
        if not text:
            skip += 1
            print(f"  [{i+1}/{len(targets)}] 抓全文失败，跳过: {e['title'][:40]}", flush=True)
            continue
        s = gen_full_summary(e["title"], text, e["date"])
        if s:
            e["summaryFull"] = s
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
