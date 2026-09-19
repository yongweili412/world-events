# -*- coding: utf-8 -*-
"""全文速览批量推进：取 N 条无 summaryFull 的事件，抓原文全文，LLM 基于全文生成速览。

省 token 优化（2026-09-19，第 5/8/9 条）：
  第 5 条 智能跳过：已有合格长速览（aiSummary ≥150 字）的事件不再重做。
  第 8 条 智能降级：按事件重要性分三级——
      A 重要（命中 IMPORTANT_KEYWORDS）→ 抓全文 3000 字 → 200-300 字 → summaryFull=true（"全文版"徽标）
      B 普通 → 抓前 1500 字 → ≤200 字 → summaryFull=false（不冒领"全文版"徽标，输入减半省 token）
      C 已有长速览 → 直接跳过
  第 9 条 结果复用：同一 URL+日期 的速览结果缓存，重复处理零 token；输出 max_tokens=600 封顶。
"""
import json, re, time, sys, os, requests
from llm_guard import resolve_model, chat_raw  # 模型守卫：限时免费优先，其次 flash；禁用 GLM-5.3/KIMI K3
from llm_cache import cache_get, cache_set, cache_save, stats as cache_stats

KEY = os.environ.get("LLM_API_KEY", "995611b2762144e88e023c856da104eb.d68AuncjSy9Qrd0O").strip()
ALL_SOURCES = os.environ.get("ALL_SOURCES") == "1"  # 云端网络畅通可抓外文源，本机默认仅国内源
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 30
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"}

TASK = "ft_summary"        # 缓存任务名
MAX_TOKENS = 600           # 速览输出上限
LONG_SUMMARY_MIN = 150     # 已有速览 ≥ 此长度视为"合格"，跳过（第 5 条）
IMPORTANT_INPUT = 3000     # A 级输入字数（全文）
NORMAL_INPUT = 1500        # B 级输入字数（减半省 token）

# 第 8 条：重要性关键词——命中即按 A 级（全文）处理。
# 注意：只用"显著事件"词，避免"发布/上市/会议"这类日常高频词把普通事件误升为 A 级；
#      避免单字词（如"核"会误命中"核心/审核"）。
IMPORTANT_KEYWORDS = ("战争", "战事", "地震", "疫情", "大选", "选举", "爆炸", "袭击", "坠机", "政变",
                      "峰会", "洪灾", "空难", "火灾", "枪击", "武装冲突", "交火", "空袭", "导弹",
                      "制裁", "罢工", "台风", "洪水", "海啸", "恐怖", "伤亡", "遇难", "坍塌",
                      "停火", "核试验", "示威", "骚乱", "人道危机", "入侵", "军事行动")


def is_important(e) -> bool:
    t = (e.get("title") or "") + (e.get("summary") or "")
    return any(k in t for k in IMPORTANT_KEYWORDS)


def fetch_fulltext(url, limit=None):
    """抓正文：容器起点定位 + 向后截 9000 字再去标签（非贪婪 </div> 会提前截断，勿用）
    返回 (text, image_url)，均可能为 None。limit 控制返回文本上限（A 级 3000 / B 级 1500）。"""
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
    # 容器起点定位（常见正文容器 id/class）。
    # 策略：取出所有候选容器，逐个向后截 9000 字去标签，**选文本最长的那个**作为正文起点。
    # 原因：同一个页面常有多个匹配（如 CCTV 的 text_area 是空壳仅 100 字、content_area 才是正文
    # 702 字），只取"第一个匹配"会命中空壳容器导致抓取失败（2026-09-19 修复，此前 CCTV 源 100% 失败）。
    cands = [mm.start() for mm in re.finditer(
        r'<div[^>]*(?:id|class)="(?:left_zw|content_area|text_area|article[_-]?content|TRS_Editor|zoom|content|article)"[^>]*>',
        html, re.I)]
    if not cands:
        cands = [0]

    def _clean(seg):
        seg = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", seg, flags=re.I)
        seg = re.sub(r"<[^>]+>", " ", seg)
        return re.sub(r"\s+", " ", seg).strip()

    best = ""
    for st in cands[:6]:          # 最多评估前 6 个候选，避免大页面重复处理
        t = _clean(html[st:st + 9000])
        if len(t) > len(best):
            best = t
    text = best
    cap = limit or IMPORTANT_INPUT
    return (text[:cap] if len(text) > 200 else None), (image or None)


def gen_full_summary(title, text, date, important=True):
    """生成速览。important=True 走 A 级（200-300 字），False 走 B 级（≤200 字）。"""
    if important:
        spec = "200-300字，覆盖事件全貌：发生时间地点、核心事实与数字、背景、各方反应或影响"
    else:
        spec = "120-200字，抓住事件核心事实（时间地点、发生了什么、结果），不必展开背景"
    prompt = f"""你是新闻编辑，为"世界事件档案"网站的这条事件写一份速览。
要求：基于提供的原文（不是仅凭标题），{spec}。平实新闻体，不用夸张词；字符串内禁止双引号。输出纯文本，不要 JSON、不要标题、不要"以下是"之类开场白。

事件：{title}（{date}）
原文：
{text}"""
    try:
        r = chat_raw(prompt, key=KEY, timeout=120, max_tokens=MAX_TOKENS)
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

    # 第 5 条：智能跳过——已有合格长速览的事件不再重做
    def skip_long(e):
        return (not e.get("summaryFull")) and len(e.get("aiSummary") or "") >= LONG_SUMMARY_MIN

    candidates = [e for e in ev
                  if not e.get("summaryFull")
                  and (e.get("sources") or [{}])[0].get("url")
                  and (ALL_SOURCES or is_cn(e))]
    n_skip_long = sum(1 for e in candidates if skip_long(e))
    targets = sorted([e for e in candidates if not skip_long(e)],
                     key=lambda e: e["date"], reverse=True)[:LIMIT]
    print(f"本轮推进全文速览: {len(targets)} 条" + ("" if ALL_SOURCES else "（仅国内可达源）")
          + f"；跳过已有长速览 {n_skip_long} 条（第5条）", flush=True)

    ok, skip, n_cache, n_imp = 0, 0, 0, 0
    for i, e in enumerate(targets):
        url = e["sources"][0]["url"]
        important = is_important(e)   # 第 8 条：重要性分级
        # 第 9 条：结果缓存——同 URL+日期 直接复用
        ck = f"{url}|{e.get('date','')}|{'A' if important else 'B'}"
        hit = cache_get(TASK, ck)
        if hit:
            e["aiSummary"] = hit
            e["summaryFull"] = True if important else False
            e["summaryBasedOn"] = url
            e["summaryAt"] = time.strftime("%Y-%m-%d")
            n_cache += 1
            ok += 1
            print(f"  [{i+1}/{len(targets)}] ↺ 缓存复用({'A' if important else 'B'}) {e['title'][:36]}", flush=True)
            continue

        text, img = fetch_fulltext(url, IMPORTANT_INPUT if important else NORMAL_INPUT)
        if img and not e.get("image"):
            e["image"] = img  # 顺带补齐主图（即便正文抓取失败也已写入）
        if not text:
            skip += 1
            print(f"  [{i+1}/{len(targets)}] 抓全文失败，跳过: {e['title'][:40]}", flush=True)
            continue
        s = gen_full_summary(e["title"], text, e["date"], important=important)
        if s:
            # 速览文本写入 aiSummary；summaryFull 只作"基于全文"的布尔标记
            e["aiSummary"] = s
            e["summaryFull"] = True if important else False   # B 级不冒领"全文版"徽标
            # 留档：A 级（全文版）留 sourceExcerpt 供事后核验数字/事实
            if important:
                e["sourceExcerpt"] = re.sub(r"\s+", " ", text)[:400]
                n_imp += 1
            e["summaryBasedOn"] = url
            e["summaryAt"] = time.strftime("%Y-%m-%d")
            cache_set(TASK, ck, s)
            ok += 1
            print(f"  [{i+1}/{len(targets)}] ✓ {'A' if important else 'B'} {e['title'][:40]}", flush=True)
        else:
            skip += 1
            print(f"  [{i+1}/{len(targets)}] 生成失败: {e['title'][:40]}", flush=True)
        time.sleep(1.5)

    cache_save()
    json.dump(ev, open("data/events.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    total = len([e for e in ev if e.get("summaryFull")])
    print(f"DONE: 成功 {ok}（其中缓存复用 {n_cache}、A级全文版 {n_imp}），跳过 {skip}，"
          f"库内 summaryFull 总数 {total}/{len(ev)}")
    print(f"缓存: {cache_stats()}")


if __name__ == "__main__":
    main()
