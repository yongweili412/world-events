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

⚠️ 限流规避策略（2026-10-31 探针实测后定）：
  conatus-free 有两道限流——① 每密钥 RPM（初始突发≈5，之后按分钟窗口锁）、
  ② 全局活动配额 free_campaign_global_limit（所有用户共享容量，随负载波动，无 Retry-After 头）。
  本脚本**直连 conatus、绕开 llm_guard 换档链**，从根本上杜绝"漏到付费 deepseek 档"。
  遇 429 只在 conatus 上指数退避重试；重试耗尽则该条跳过（defer），下一轮批次会自动重新选中补做。
  自适应节奏：连续成功逐步收紧到 MIN_GAP；连续 429 指数放大间隔给全局配额降温。

用法：
  python scraper/keypoints_batch.py [N]      # N 为本轮条数，默认 100
"""
import json
import os
import re
import sys
import time
from difflib import SequenceMatcher

import requests

from llm_cache import cache_get, cache_set, cache_save, stats as cache_stats

KEY = (os.environ.get("LLM_API_KEY") or "").strip()
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 100
TASK = "keypoints3"
MIN_BODY = 120          # 正文太短不足以提炼要点
RESTATE_TH = 0.75       # 复述判定阈值，与 build.py _is_restatement 保持一致
MAX_TOKENS = 600        # 中文三要点约 180 字，400 会截断末点，提到 600
CONATUS_MODEL = "qiniu/conatus-free"
CONATUS_BASE = "https://api.qnaigc.com/v1"

# 自适应节奏参数
MIN_GAP = 12            # 基础间隔（秒）：≈5 次/分，卡在每密钥 RPM 安全线内
MAX_GAP = 60            # 全局配额饱和时的最大退避间隔
RETRY_BACKOFFS = (10, 20, 40, 80, 120)   # 单次调用内 429 退避序列（秒），全程只在 conatus
MAX_CALL_RETRIES = len(RETRY_BACKOFFS)

# 统计（证明零 spillover 到付费档）
STATS = {"conatus_calls": 0, "deepseek_calls": 0, "skipped": 0}


def _norm(s):
    return re.sub(r"\s+", "", s or "")


def is_restatement(ai, body):
    """判定 aiSummary 是否只是正文的复述（与 build.py 同一套判定）。"""
    a, b = _norm(ai)[:60], _norm(body)[:60]
    if not a or not b:
        return False
    return SequenceMatcher(None, a, b).ratio() >= RESTATE_TH


def call_conatus_direct(prompt, timeout=120):
    """直连 conatus-free，绕过换档链。返回 (content_or_None, hit_429_bool)。

    遇 429/5xx 只在 conatus 上指数退避重试；重试耗尽返回 None。绝不调用 deepseek。
    """
    body = {"model": CONATUS_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3, "max_tokens": MAX_TOKENS}
    STATS["conatus_calls"] += 1
    last_429 = False
    for attempt in range(MAX_CALL_RETRIES + 1):
        try:
            r = requests.post(CONATUS_BASE + "/chat/completions",
                             headers={"Authorization": "Bearer " + KEY,
                                      "Content-Type": "application/json"},
                             json=body, timeout=timeout)
        except Exception as ex:
            print(f"    ⚠ 请求异常({type(ex).__name__})，{RETRY_BACKOFFS[min(attempt,4)]}s后重试", flush=True)
            if attempt < MAX_CALL_RETRIES:
                time.sleep(RETRY_BACKOFFS[min(attempt, 4)])
                continue
            return None, False
        if r.status_code == 200:
            try:
                content = (r.json()["choices"][0]["message"].get("content") or "").strip()
            except Exception:
                content = ""
            content = re.sub(r"^```[a-z]*\n?|```$", "", content.strip(), flags=re.M).strip()
            lines = [ln.strip() for ln in content.splitlines()
                     if re.match(r"^[123][.、)]\s*\S", ln.strip())]
            if len(lines) >= 3:
                return "\n".join(lines[:3]), False
            # 返回 200 但格式不对：当作失败重试一次（不计入 429，但退避）
            if attempt < MAX_CALL_RETRIES:
                time.sleep(RETRY_BACKOFFS[min(attempt, 4)])
                continue
            return None, False
        last_429 = (r.status_code == 429)
        if attempt < MAX_CALL_RETRIES:
            why = "限流429" if last_429 else f"{r.status_code}"
            print(f"    ↻ {why}，{RETRY_BACKOFFS[attempt]}s 后重试(conatus)", flush=True)
            time.sleep(RETRY_BACKOFFS[attempt])
            continue
        return None, last_429
    return None, last_429


def gen_keypoints(title, body, date, pacer):
    """让模型基于正文提炼三条要点；失败返回 None。pacer 用于自适应节奏反馈。"""
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
    content, was_429 = call_conatus_direct(prompt)
    if content:
        pacer.on_success()
        return content
    # 失败：反馈节奏控制器（连续 429 会放大间隔）
    pacer.on_fail(was_429)
    return None


class Pacer:
    """自适应节奏控制：连续成功收紧，连续 429 放大（AIMD 风格）。"""
    def __init__(self):
        self.gap = MIN_GAP
        self.consec_ok = 0
        self.consec_429 = 0

    def on_success(self):
        self.consec_ok += 1
        self.consec_429 = 0
        if self.consec_ok >= 5 and self.gap > MIN_GAP:
            self.gap = max(MIN_GAP, self.gap - 2)
            self.consec_ok = 0

    def on_fail(self, was_429):
        self.consec_ok = 0
        if was_429:
            self.consec_429 += 1
            if self.consec_429 >= 2:
                self.gap = min(MAX_GAP, self.gap * 2)
                self.consec_429 = 0
                print(f"    ⏸ 全局配额疑似饱和，间隔放大至 {self.gap}s", flush=True)


def main():
    ev = json.load(open("data/events.json", encoding="utf-8"))

    def body_of(e):
        return (e.get("description") or "").strip()

    # 目标：正文够长（短正文硬凑要点只会注水）且 当前 summary 只是正文的复述。
    candidates = [e for e in ev
                  if len(body_of(e)) >= MIN_BODY
                  and is_restatement(e.get("summary"), body_of(e))]
    # 优先级：档案价值高的先做（KEEP 先受益），其次时间倒序
    candidates.sort(key=lambda e: (-(e.get("archiveValue") or 0), e.get("date") or ""), reverse=False)
    targets = candidates[:LIMIT]
    print(f"本轮生成三要点: {len(targets)} 条（候选池 {len(candidates)} 条：正文≥{MIN_BODY}字且速览为复述）",
          flush=True)
    if not targets:
        print("候选池已空，全部完成或正文不足，无需继续。", flush=True)
        return

    pacer = Pacer()
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
        kp = gen_keypoints(e.get("title", ""), body[:2500], e.get("date", ""), pacer)
        if kp:
            e["summary"] = kp
            if is_restatement(e.get("aiSummary"), body):
                e["aiSummary"] = kp
            cache_set(TASK, ck, kp)
            ok += 1
            print(f"  [{i+1}/{len(targets)}] ✓ {e.get('title','')[:40]}", flush=True)
        else:
            skip += 1
            STATS["skipped"] += 1
            print(f"  [{i+1}/{len(targets)}] ⏭ 生成失败/限流耗尽，跳过(下轮补做) {e.get('title','')[:36]}", flush=True)
        # 自适应间隔：连续成功≈12s，饱和时自动放大到 60s
        time.sleep(pacer.gap)
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
    print(f"DONE: 成功 {ok}（缓存复用 {n_cache}），跳过 {skip}", flush=True)
    print(f"  [零spill校验] conatus调用={STATS['conatus_calls']} | deepseek调用={STATS['deepseek_calls']}(必须为0) | 跳过={STATS['skipped']}",
          flush=True)


if __name__ == "__main__":
    main()
