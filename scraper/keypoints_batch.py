# -*- coding: utf-8 -*-
"""速览三要点批量生成：对「速览只是正文复述」的事件，用 LLM 从正文提炼三条要点，写回 summary。

为什么需要（对应评估报告 P2）：
  全库 53,198 条里 **50,116 条（94%）的 summary 只是 description 的复述**（与报告 93.4% 吻合），
  等于让用户把同一段话读两遍。静态页目前的做法是"识别到复述就隐藏速览框"——
  那只是止损；本脚本是根治：用 LLM 提炼真正的三条要点替换掉复述，速览框重新有内容可显示。

写哪个字段：
  summary   = 静态详情页（e/evt_*.html，7,113 个 SEO 页面）渲染的"速览" → 主受益面，直接覆盖。
  aiSummary = 动态详情页（event.html）渲染的"速览"；仅当它同样是复述时才一并修正，
              避免覆盖 full_summary_batch 已生成的"全文版"速览。

与 full_summary_batch.py 的区别（关键）：
  那个脚本依赖**抓取外网原文**（本机跑失败率高，实测 3 条里 2 条抓全文失败），
  本脚本直接基于事件**已有的正文**提炼要点，不依赖外网，成功率接近 100%。
  且输出是"三条要点"而非整段速览，更符合报告口径。

用法：
  python scraper/keypoints_batch.py [N]      # N 为本轮条数，默认 100
"""
import json
import os
import re
import sys
import time
from difflib import SequenceMatcher

from llm_guard import chat_raw
from llm_cache import cache_get, cache_set, cache_save, stats as cache_stats

KEY = (os.environ.get("LLM_API_KEY") or "").strip()
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 100
TASK = "keypoints3"
MIN_BODY = 120          # 正文太短不足以提炼要点
RESTATE_TH = 0.75       # 复述判定阈值，与 build.py _is_restatement 保持一致
MAX_TOKENS = 600        # 2026-10-31：400 对中文三要点偏紧，实测会截断末点，提到 600
RETRY_DELAYS = (4, 12, 30, 60)


def _norm(s):
    return re.sub(r"\s+", "", s or "")


def is_restatement(ai, body):
    """判定 aiSummary 是否只是正文的复述（与 build.py 同一套判定）。"""
    a, b = _norm(ai)[:60], _norm(body)[:60]
    if not a or not b:
        return False
    return SequenceMatcher(None, a, b).ratio() >= RESTATE_TH


def gen_keypoints(title, body, date):
    """让模型基于正文提炼三条要点；失败返回 None。"""
    if not KEY:
        print("⚠️ 未配置 LLM_API_KEY，跳过", flush=True)
        return None
    prompt = (
        "你是新闻编辑，为「世界事件档案」网站的这条事件提炼三条要点。\n"
        "要求：严格基于下面正文，不得编造正文没有的信息；每条不超过 60 字；"
        "三条分别覆盖 发生了什么（时间/地点/主体）、关键数字或核心事实、影响与后续（无后续则写背景）。\n"
        "输出严格三行，每行以 \"1. \" \"2. \" \"3. \" 开头。纯文本，不要 JSON、不要标题、不要开场白。\n\n"
        f"事件：{title}（{date}）\n正文：\n{body}"
    )
    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            r = chat_raw(prompt, key=KEY, timeout=120, max_tokens=MAX_TOKENS)
        except Exception:
            r = None
        if r is not None and r.status_code == 200:
            try:
                content = (r.json()["choices"][0]["message"].get("content") or "").strip()
            except Exception:
                content = ""
            content = re.sub(r"^```[a-z]*\n?|```$", "", content.strip(), flags=re.M).strip()
            # 只保留以 1./2./3. 开头的行，过滤模型夹带的废话
            lines = [ln.strip() for ln in content.splitlines()
                     if re.match(r"^[123][.、)]\s*\S", ln.strip())]
            if len(lines) >= 3:
                return "\n".join(lines[:3])
        if attempt < len(RETRY_DELAYS):
            print(f"  ↻ 限流/失败，{RETRY_DELAYS[attempt]}s 后重试", flush=True)
            time.sleep(RETRY_DELAYS[attempt])
    return None


def main():
    ev = json.load(open("data/events.json", encoding="utf-8"))

    def body_of(e):
        return (e.get("description") or "").strip()

    # 目标：正文够长（短正文硬凑要点只会注水）且 当前 summary 只是正文的复述。
    # 实测：全库 53,198 条里 summary 复述正文的有 50,116 条（94%，与报告 93.4% 吻合），
    #       但正文 ≥120 字的只有 1,950 条，其中 1,858 条是复述——这才是值得做的富矿。
    candidates = [e for e in ev
                  if len(body_of(e)) >= MIN_BODY
                  and is_restatement(e.get("summary"), body_of(e))]
    # 优先级：档案价值高的先做（KEEP 先受益），其次时间倒序
    candidates.sort(key=lambda e: (-(e.get("archiveValue") or 0), e.get("date") or ""), reverse=False)
    targets = candidates[:LIMIT]
    print(f"本轮生成三要点: {len(targets)} 条（候选池 {len(candidates)} 条：正文≥{MIN_BODY}字且速览为复述）",
          flush=True)

    ok, skip, n_cache = 0, 0, 0
    for i, e in enumerate(targets):
        body = body_of(e)
        ck = f"{e.get('id')}|{len(body)}"
        hit = cache_get(TASK, ck)
        if hit:
            e["summary"] = hit
            if is_restatement(e.get("aiSummary"), body):
                e["aiSummary"] = hit
            n_cache += 1
            ok += 1
            print(f"  [{i+1}/{len(targets)}] ↺ 缓存复用 {e.get('title','')[:36]}", flush=True)
            continue
        kp = gen_keypoints(e.get("title", ""), body[:2500], e.get("date", ""))
        if kp:
            # summary = 静态页速览（主受益面，7,113 个 SEO 页直接可见）
            e["summary"] = kp
            # aiSummary = 动态页速览；仅当它同样是复述时才一并修正，避免覆盖已有的全文版速览
            if is_restatement(e.get("aiSummary"), body):
                e["aiSummary"] = kp
            cache_set(TASK, ck, kp)
            ok += 1
            print(f"  [{i+1}/{len(targets)}] ✓ {e.get('title','')[:40]}", flush=True)
        else:
            skip += 1
            print(f"  [{i+1}/{len(targets)}] 生成失败: {e.get('title','')[:40]}", flush=True)
        time.sleep(1.2)
        if (i + 1) % 10 == 0:
            cache_save()   # 阶段性落盘，防中途被杀丢缓存

    cache_save()
    # 原子写回（与 full_summary_batch 同一套约定）
    tmp = "data/events.json.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(ev, f, ensure_ascii=False)
        f.flush()
    os.replace(tmp, "data/events.json")
    with open("data/events.json", encoding="utf-8") as f:
        assert len(json.load(f)) == len(ev), "复读断言失败"
    print(f"DONE: 成功 {ok}（缓存复用 {n_cache}），失败 {skip}；缓存: {cache_stats()}", flush=True)


if __name__ == "__main__":
    main()
