# -*- coding: utf-8 -*-
"""全文速览批量推进：取 N 条无 summaryFull 的事件，抓原文全文，LLM 基于全文生成速览。

省 token 优化（2026-09-19，第 5/8/9 条）：
  第 5 条 智能跳过：已有合格长速览（aiSummary ≥150 字）的事件不再重做。
  第 8 条 智能降级：按事件重要性分三级——
      A 重要（命中 IMPORTANT_KEYWORDS）→ 抓全文 3000 字 → 60-120 字 → summaryFull=true（"全文版"徽标）
      B 普通 → 抓前 1500 字 → 60-90 字 → summaryFull=false（不冒领"全文版"徽标，输入减半省 token）
      C 已有长速览 → 直接跳过
  第 9 条 结果复用：同一 URL+日期 的速览结果缓存，重复处理零 token；输出 max_tokens=600 封顶。

2026-11-28 修正（夜轮排障）：
  1. 失败诊断分四类——429 限流 / 5xx / 空或过短返回 / 请求异常，并打印实际应答模型名；
     原实现把"HTTP 200 但正文 <100 字"也标成"限流/失败"，日志里看不到任何 [llm] 换档记录，容易误判。
  2. 成功门槛 MIN_SUMMARY_LEN=50，与 60-120 字速览规则对齐（原 100 字门槛会误杀合格短速览）。
  3. 每 10 条把 events.json 原子落盘（临时文件 + os.replace），进程超时/中断不再丢已完成结果。
"""

import json, re, time, sys, os, requests
from llm_guard import resolve_model, chat_raw  # 模型守卫：限时免费优先，其次 flash；禁用 GLM-5.3/KIMI K3
from llm_cache import cache_get, cache_set, cache_save, stats as cache_stats
# 项目级公共实现（2026-11-28 提炼）：诊断/清洗/原子写回全项目共用一套，避免各脚本口径不一
from llm_utils import (MIN_SUMMARY_LEN, SUMMARY_TRIM, atomic_dump_events,
                       diagnose_response, resp_model, tidy_summary)

KEY = (os.environ.get("LLM_API_KEY") or "").strip()   # 安全：不内置兜底密钥（公开仓库会泄露）
ALL_SOURCES = os.environ.get("ALL_SOURCES") == "1"  # 云端网络畅通可抓外文源，本机默认仅国内源
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 100
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"}

TASK = "ft_summary"        # 缓存任务名
MAX_TOKENS = 600           # 速览输出上限
LONG_SUMMARY_MIN = 150     # 已有速览 ≥ 此长度视为"合格"，跳过（第 5 条）
TARGET_MAX_LEN = 60        # 存量轮只补真实缺口：速览缺失或不足 60 字才列入目标
IMPORTANT_INPUT = 3000     # A 级输入字数（全文）
NORMAL_INPUT = 1500        # B 级输入字数（减半省 token）
# MIN_SUMMARY_LEN / SUMMARY_TRIM 统一由 llm_utils 提供（60-120 字口径的单一事实源）
FLUSH_EVERY = 10           # 每 N 条落盘一次，进程中断不丢已完成结果

# 最近一次生成失败的诊断信息（不改变 gen_full_summary 的返回类型）
LAST_FAIL = {"reason": "", "model": ""}
LAST_MODEL = ""

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
        # 2026-11-29：强制按 UTF-8→GB18030 解码。实测 zaobao 等站点被 apparent_encoding
        # 判成 latin-1，取到的正文是乱码；而 r.text 也会踩这个坑。
        raw = r.content
        html = None
        for enc in ("utf-8", "gb18030"):
            try:
                cand = raw.decode(enc)
                if cand and cand.count("\ufffd") < 5:
                    html = cand
                    break
            except UnicodeDecodeError:
                continue
        if html is None:
            html = raw.decode(r.apparent_encoding or "utf-8", errors="ignore")
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
    # 2026-10-14 增补：cri.cn（news.cri.cn）正文容器为 div id="abody"（class="list-abody abody"），
    # 不在旧列表内导致该源 100% 抓取失败；加入 abody 后恢复正常（实测提取 700+ 字）。
    # 2026-11-29：先整体剥离 script/style 再定位容器。否则容器起点落在 <script> 内部时，
    # 截出来的片段没有 <script> 开标签，去标签正则不生效，整段 JS 会被当成正文
    # （zaobao 实测：抓到 1500 字 function getUrlParam…，模型只能凭标题臆写）。
    html_wo = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>|<noscript[\s\S]*?</noscript>",
                     " ", html, flags=re.I)

    cands = [mm.start() for mm in re.finditer(
        r'<div[^>]*(?:id|class)="(?:left_zw|content_area|text_area|article[_-]?content|TRS_Editor|zoom|content|article|abody)"[^>]*>',
        html_wo, re.I)]
    if not cands:
        cands = [0]

    def _clean(seg):
        seg = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", seg, flags=re.I)
        seg = re.sub(r"<[^>]+>", " ", seg)
        return re.sub(r"\s+", " ", seg).strip()

    def _zh_ratio(t):
        return len(re.findall(r"[\u4e00-\u9fff]", t)) / max(1, len(t))

    def _looks_code(t):
        """代码特征：JS 关键字密集或括号/分号占比过高。"""
        kw = len(re.findall(r"\b(?:function|var|let|const|return|window|document|undefined|null|true|false)\b", t))
        punct = t.count("{") + t.count("}") + t.count(";")
        return kw >= 3 or punct > max(20, len(t) * 0.03)

    def _usable(t):
        if not t or len(t) < 200 or _looks_code(t):
            return False
        latin = len(re.findall(r"[A-Za-z]", t)) / max(1, len(t))
        return _zh_ratio(t) > 0.25 or latin > 0.45

    best = ""
    # 2026-11-29：zaobao 正文在 JSON-LD 的 articleBody 里，优先直接取，成功率 100%。
    m_ab = re.search(r'"articleBody"\s*:\s*"((?:[^"\\]|\\.){80,})"', html)
    if m_ab:
        try:
            import json as _json
            body = _json.loads('"' + m_ab.group(1) + '"')
        except Exception:
            body = m_ab.group(1)
        body = _clean(body)
        if _usable(body):
            best = body

    for st in cands[:6]:          # 最多评估前 6 个候选，避免大页面重复处理
        t = _clean(html_wo[st:st + 9000])
        if _usable(t) and len(t) > len(best):
            best = t
    # 2026-10-21 增补：Next.js 站点（澎湃 thepaper.cn 等）正文由 JS 渲染，
    # HTML 里只剩 __NEXT_DATA__ 的 JSON，容器正则取不到正文（实测 64 条全部失败）。
    # 兜底：解析 __NEXT_DATA__，取最长的含 <p> 的字符串作为正文。
    if len(best) < 400 and "__NEXT_DATA__" in html:
        try:
            import json as _json
            m = re.search(r'<script id="__NEXT_DATA__"[^>]*>([\s\S]*?)</script>', html, re.I)
            if m:
                data = _json.loads(m.group(1))
                stack, longest = [data], ""
                while stack:
                    cur = stack.pop()
                    if isinstance(cur, dict):
                        stack.extend(cur.values())
                    elif isinstance(cur, list):
                        stack.extend(cur)
                    elif isinstance(cur, str) and len(cur) > len(longest) and "<p" in cur:
                        longest = cur
                if longest:
                    cand = _clean(longest)
                    if _usable(cand) and len(cand) > len(best):
                        best = cand
        except Exception:
            pass
    if not _usable(best):
        return None, (image or None)
    cap = limit or IMPORTANT_INPUT
    return best[:cap], (image or None)


def _resp_model(r):
    """兼容旧调用名：取实际应答的模型名（实现在 llm_utils）。"""
    return resp_model(r)


def _tidy_summary(s):
    """兼容旧调用名：去代码块/压缩空白/按 120 字截断（实现在 llm_utils）。"""
    return tidy_summary(s)


def fetch_lead(url):
    """抓「正文开头/导语」作兜底（全文抓不到时用），返回文本或 None。

    适用：zaobao（JSON-LD 只有 articleBody 导语）、France24（chapo）、
    以及多数站点的 meta description。只接受 ≥80 字，避免把导航当导语。
    调用方须保持 summaryFull=False，并在失败清单里注明"仅导语"。
    """
    try:
        r = requests.get(url, headers=UA, timeout=15)
        if r.status_code != 200:
            return None
        raw = r.content
        html = None
        for enc in ("utf-8", "gb18030"):
            try:
                cand = raw.decode(enc)
                if cand and cand.count("\ufffd") < 5:
                    html = cand
                    break
            except UnicodeDecodeError:
                continue
        if html is None:
            html = raw.decode(r.apparent_encoding or "utf-8", errors="ignore")
    except Exception:
        return None

    def _clean(seg):
        seg = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", seg, flags=re.I)
        seg = re.sub(r"<[^>]+>", " ", seg)
        for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&quot;", '"'),
                     ("&#39;", "'"), ("&ldquo;", "“"), ("&rdquo;", "”")):
            seg = seg.replace(a, b)
        return re.sub(r"\s+", " ", seg).strip()

    cands = []
    m = re.search(r'"articleBody"\s*:\s*"((?:[^"\\]|\\.){40,})"', html)
    if m:
        try:
            import json as _json
            cands.append(_clean(_json.loads('"' + m.group(1) + '"')))
        except Exception:
            cands.append(_clean(m.group(1)))
    m2 = re.search(r'<p class="t-content__chapo"[^>]*>([\s\S]*?)</p>', html)
    if m2:
        cands.append(_clean(m2.group(1)))
    m3 = re.search(r'<meta[^>]+name="description"[^>]+content="([^"]{40,})"', html, re.I)
    if m3:
        cands.append(_clean(m3.group(1)))
    for t in cands:
        if len(t) >= 50 and not re.search(r"\b(?:function|var|let|const|window)\b", t):
            return t[:800]
    return None


def gen_full_summary(title, text, date, important=True):
    """生成 60-120 字速览。

    失败原因区分四类并记录实际模型名：429 限流 / 5xx 服务不可用 / 空或过短返回 / 请求异常。
    2026-11-28 起不再把"200 但内容短"笼统标成"限流"，诊断信息写入 LAST_FAIL 供 main 汇总。
    """
    global LAST_MODEL, LAST_FAIL
    if important:
        spec = ("2-3 句话，总长 60-120 字；第一句写清时间地点与核心事实，"
                "后文补充关键数字、各方反应或影响")
    else:
        spec = "2-3 句话，总长 60-100 字；写清时间地点、发生了什么、结果"
    prompt = f"""你是新闻编辑，为“世界事件档案”写一条中文速览。必须同时满足：
①{spec}；
②只使用原文中出现的事实，不得编造原文没有的细节；
③平实新闻体，不用夸张词，不使用英文（专名除外），不使用双引号；
④只输出速览正文，不要标题、不要开场白。

事件：{title}（{date}）
原文：
{text}"""
    if not KEY:
        # 与代码库既有约定一致（translate_cloud / backfill_history）：缺 Key 时跳过而非中断夜跑
        print("⚠️ 未配置 LLM_API_KEY，跳过（已移除内置兜底密钥，请在 GitHub Secrets / 本机环境变量配置）")
        LAST_FAIL = {"reason": "缺少 LLM_API_KEY", "model": ""}
        return None
    # 2026-10-11：加入同模型指数退避重试——网关 429 时先退避重试同档。
    # 2026-10-19 更新：备选档已改为三档结构（见 llm_guard），原 glm-4.5-flash
    # 因额度耗尽列入 exhausted_models，不再作为切换目标。
    RETRY_DELAYS = (4, 12, 30, 60)
    reason = "未获得响应"
    expanded = False
    try:
        for attempt in range(len(RETRY_DELAYS) + 1):
            try:
                r = chat_raw(prompt, key=KEY, timeout=120, max_tokens=MAX_TOKENS)
                d = diagnose_response(r, min_len=MIN_SUMMARY_LEN, max_len=SUMMARY_TRIM)
            except Exception as ex:
                d = diagnose_response(None, exc=ex)
            if d["model"]:
                LAST_MODEL = d["model"]
            if d["ok"]:
                return d["content"]
            reason = d["reason"]
            # 2026-11-29：短返回是模型的稳定行为（同一 prompt 重试会得到逐字相同的结果，
            # 实测 39 字两次完全一致），按 429 退避纯属浪费。改为一次明确的“扩写”调用。
            if d["kind"] == "short":
                short = d.get("content") or ""
                if not expanded and len(short) >= 15:
                    expanded = True
                    ep = f"""下面这条速览只有 {len(short)} 字，太短。请依据原文扩写到 60-110 字，分 2-3 句，
补充原文中已有的数字、地点或各方反应，不得编造原文没有的事实；只输出扩写后的速览。

原文：
{text}
现有速览：{short}"""
                    try:
                        r2 = chat_raw(ep, key=KEY, timeout=120, max_tokens=MAX_TOKENS)
                        d2 = diagnose_response(r2, min_len=MIN_SUMMARY_LEN, max_len=SUMMARY_TRIM)
                    except Exception as ex:
                        d2 = diagnose_response(None, exc=ex)
                    if d2["ok"]:
                        return d2["content"]
                    reason = f"{d['reason']}；扩写后仍不足（{len(d2.get('content') or '')}字）"
                break
            # 401/403/404 等非限流类 4xx：重试无意义，直接放弃本事件
            if not d["retryable"]:
                break
            if attempt < len(RETRY_DELAYS):
                print(f"  ↻ {reason}，{RETRY_DELAYS[attempt]}s 后重试", flush=True)
                time.sleep(RETRY_DELAYS[attempt])
        LAST_FAIL = {"reason": reason, "model": LAST_MODEL}
        return None
    except Exception as ex:
        LAST_FAIL = {"reason": f"未预期异常 {type(ex).__name__}", "model": LAST_MODEL}
        return None


def _write_events(ev):
    """原子写回 events.json（临时文件 + os.replace），供中途落盘与收尾共用。

    实现在 llm_utils.atomic_dump（与 shard_events.py merge 一样用紧凑格式）。
    """
    atomic_dump_events(ev)


def main():
    ev = json.load(open("data/events.json", encoding="utf-8"))
    # 国内可达源域名（本机网络可直连）；外文被墙源留给云端处理
    CN_HOSTS = ("chinanews.com.cn", "people.com.cn", "cctv.com", "thepaper.cn", "news.cn",
                "xinhuanet.com", "huanqiu.com", "ifeng.com", "jiemian.com", "cri.cn",
                "zaobao.com", "chinanews.com", "gmw.cn", "yicai.com", "cnbeta.com")
    def is_cn(e):
        u = (e.get("sources") or [{}])[0].get("url") or ""
        return any(h in u for h in CN_HOSTS)

    # 第 5 条：智能跳过——已有合格速览的事件不再重做
    # 2026-11-29：存量轮改为只挑「速览缺失或不足 60 字」。此前按日期倒序取前 100，
    # 命中的全是"有缓存但非全文版"的近期条目，每轮重复占用配额却不产生新内容。
    def needs_work(e):
        return len(e.get("aiSummary") or "") < TARGET_MAX_LEN

    candidates = [e for e in ev
                  if not e.get("summaryFull")
                  and needs_work(e)
                  and (e.get("sources") or [{}])[0].get("url")
                  and (ALL_SOURCES or is_cn(e))]
    targets = sorted(candidates, key=lambda e: e["date"], reverse=True)[:LIMIT]
    print(f"本轮推进全文速览: {len(targets)} 条" + ("" if ALL_SOURCES else "（仅国内可达源）")
          + f"；候选缺口共 {len(candidates)} 条（速览<{TARGET_MAX_LEN}字）", flush=True)

    ok, skip, n_cache, n_imp = 0, 0, 0, 0
    n_fail_fetch, n_fail_gen = 0, 0
    fails = []

    def flush(done_tag):
        """缓存 + 事件数据一起落盘（原子写），供每 N 条及收尾调用。"""
        cache_save()
        _write_events(ev)
        print(f"  💾 已落盘 {done_tag}/{len(targets)}（中断不丢已完成结果）", flush=True)

    for i, e in enumerate(targets):
        url = e["sources"][0]["url"]
        important = is_important(e)   # 第 8 条：重要性分级
        # 第 9 条：结果缓存——同 URL+日期 直接复用
        ck = f"{url}|{e.get('date','')}|{'A' if important else 'B'}"
        # 缓存命中也做一次清洗/截断（历史缓存可能超长）；短于目标口径的旧缓存视为无效，重新生成
        hit = _tidy_summary(cache_get(TASK, ck) or "")
        if hit and len(hit) >= TARGET_MAX_LEN:
            e["aiSummary"] = hit
            e["summaryFull"] = True if important else False
            e["summaryBasedOn"] = url
            e["summaryAt"] = time.strftime("%Y-%m-%d")
            n_cache += 1
            ok += 1
            print(f"  [{i+1}/{len(targets)}] ↺ 缓存复用({'A' if important else 'B'}) {e['title'][:36]}", flush=True)
            if (i + 1) % FLUSH_EVERY == 0:
                flush(i + 1)
            continue

        text, img = fetch_fulltext(url, IMPORTANT_INPUT if important else NORMAL_INPUT)
        partial = False
        if img and not e.get("image"):
            e["image"] = img  # 顺带补齐主图（即便正文抓取失败也已写入）
        if not text:
            # 全文抓不到时退一步取导语：只写"基于正文开头"的速览，绝不冒领全文版
            lead = fetch_lead(url)
            if lead:
                text, partial = lead, True
        if not text:
            skip += 1
            n_fail_fetch += 1
            fails.append({"id": e.get("id"), "url": url, "stage": "fetch",
                          "reason": "正文与导语均抓取失败", "model": ""})
            print(f"  [{i+1}/{len(targets)}] 抓全文失败，跳过: {e['title'][:40]}", flush=True)
            if (i + 1) % FLUSH_EVERY == 0:
                flush(i + 1)
            continue
        s = gen_full_summary(e["title"], text, e["date"], important=important)
        if s:
            # 速览文本写入 aiSummary；summaryFull 只作"基于全文"的布尔标记
            e["aiSummary"] = s
            # 仅导语 → 一律 False；B 级（1500 字正文开头）同样不冒领"全文版"徽标
            e["summaryFull"] = bool(important and not partial)
            # 留档：全文版留 sourceExcerpt 供事后核验数字/事实
            if important and not partial:
                e["sourceExcerpt"] = re.sub(r"\s+", " ", text)[:400]
                n_imp += 1
            e["summaryBasedOn"] = url
            e["summaryAt"] = time.strftime("%Y-%m-%d")
            cache_set(TASK, ck, s)
            ok += 1
            print(f"  [{i+1}/{len(targets)}] ✓ {'A' if important else 'B'}{'·导语' if partial else ''} {e['title'][:40]}", flush=True)
        else:
            skip += 1
            n_fail_gen += 1
            fails.append({"id": e.get("id"), "url": url, "stage": "summary",
                          "reason": LAST_FAIL.get("reason", ""), "model": LAST_FAIL.get("model", "")})
            print(f"  [{i+1}/{len(targets)}] 生成失败（{LAST_FAIL.get('reason', '')}）: {e['title'][:40]}", flush=True)
        time.sleep(1.5)
        if (i + 1) % FLUSH_EVERY == 0:
            flush(i + 1)

    flush(len(targets))
    with open("data/events.json", encoding="utf-8") as f:
        _chk = json.load(f)
    assert len(_chk) == len(ev), f"复读断言失败 {len(_chk)} != {len(ev)}"
    total = len([e for e in ev if e.get("summaryFull")])
    print(f"DONE: 成功 {ok}（其中缓存复用 {n_cache}、A级全文版 {n_imp}），跳过 {skip}"
          f"（抓全文失败 {n_fail_fetch}、生成失败 {n_fail_gen}），"
          f"库内 summaryFull 总数 {total}/{len(ev)}")
    if fails:
        by_reason = {}
        for f in fails:
            k = f["reason"] or "未知"
            by_reason[k] = by_reason.get(k, 0) + 1
        print("失败原因分布: " + json.dumps(by_reason, ensure_ascii=False))
        for f in fails:
            print("FAIL_ITEM " + json.dumps(f, ensure_ascii=False), flush=True)
    print(f"缓存: {cache_stats()}")


if __name__ == "__main__":
    main()
