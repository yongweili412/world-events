# -*- coding: utf-8 -*-
"""LLM 价值判断器：让模型回答"这件事是否值得作为这一年的时代记录"。

唯一判断标准（不是"这是不是新闻"）：
    如果十年后有人研究这一年，这件事是否值得作为这一年的时代记录？

护栏（必须遵守）：
  1. 一切调用走 llm_guard.chat_raw —— 自动遵守「限时免费优先 → 回退 flash」，
     并硬禁 glm-5.3 / kimi-k3；不得直连模型接口。
  2. 解析失败 / 调用失败 / 缺 Key → 一律返回 None（上层转 REVIEW），
     **绝不默认 KEEP，也绝不默认 DROP**。
  3. 待评估内容是不可信数据，prompt 已声明其中指令不执行。
"""
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from llm_guard import chat_raw  # 模型守卫：免费优先→flash；禁用 GLM-5.3/KIMI K3
import event_schema as ES

PROMPT_FILE = Path(__file__).parent / "prompts" / "archive_value_v1.txt"
BATCH = 5            # 每批条数（小批量避免输出截断与互相污染）
MAX_TOKENS = 2200
TASK = "value_judge"

EVENT_TYPES = ("world", "society_culture", "internet_trends")
DECISIONS = ("KEEP", "REVIEW", "DROP")


def load_prompt_template():
    return PROMPT_FILE.read_text(encoding="utf-8")


def build_prompt(item):
    """把单条候选填进 prompt 模板。用 __X__ 占位（避免与 JSON 示例花括号冲突）。"""
    tpl = load_prompt_template()
    date = item.get("date") or ""
    year = date[:4] or "该"
    srcs = item.get("sources") or ([{"url": item.get("sourceUrl", ""), "name": item.get("source", "")}]
                                   if item.get("sourceUrl") else [])
    n_ind = len({re.sub(r"^https?://(www\.)?", "", (s.get("url") or "").split("/")[0])
                 for s in srcs if s.get("url")})
    if not srcs:
        note = "无来源链接"
    elif n_ind >= 2:
        note = f"{len(srcs)} 个来源，含 {n_ind} 个独立域名（证据较强）"
    else:
        note = f"{len(srcs)} 个来源，独立域名 {n_ind} 个"

    return (tpl
            .replace("__YEAR__", year)
            .replace("__TITLE__", (item.get("title") or "")[:200])
            .replace("__DATE__", date)
            .replace("__SUMMARY__", (item.get("summary") or item.get("content") or "")[:400])
            .replace("__CATEGORY__", str(item.get("category") or ""))
            .replace("__SOURCES_NOTE__", note))


def _extract_json(text):
    """从模型输出里抠出第一个合法 JSON 对象（容忍前后废话/代码块）。"""
    if not text:
        return None
    text = re.sub(r"^```[a-z]*\n?|```$", "", text.strip(), flags=re.M).strip()
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except Exception:
                    return None
    return None


def _normalize(raw):
    """校验并收敛模型输出；任何异常都返回 None（→ REVIEW）。"""
    if not isinstance(raw, dict):
        return None
    et = raw.get("eventType")
    if et not in EVENT_TYPES:
        return None
    dec = raw.get("decision")
    if dec not in DECISIONS:
        return None
    scores = {}
    for d in ES.DIMENSIONS:
        v = raw.get(d)
        if not isinstance(v, (int, float)):
            return None
        scores[d] = ES.clamp(v)
        if scores[d] is None:
            return None
    archive_value = ES.compute_archive_value(scores)
    return {
        "eventType": et,
        "decision": dec,
        "scores": scores,
        "archiveValue": archive_value,
        "reason": str(raw.get("reason") or "")[:200],
        "method": "llm",
        "model": os.environ.get("LLM_MODEL", ""),
    }


def judge_one(item, timeout=120):
    """判断单条候选。失败返回 None。"""
    key = (os.environ.get("LLM_API_KEY") or "").strip()
    if not key:
        print("  ⚠️ 未配置 LLM_API_KEY，价值判断跳过（全部转 REVIEW）", flush=True)
        return None
    try:
        r = chat_raw(build_prompt(item), key=key, timeout=timeout,
                     temperature=0.2, max_tokens=MAX_TOKENS)
    except Exception as ex:
        print(f"  ⚠️ 价值判断调用异常 {type(ex).__name__}，转 REVIEW", flush=True)
        return None
    if r is None or getattr(r, "status_code", 0) != 200:
        print(f"  ⚠️ 价值判断 HTTP {getattr(r, 'status_code', '?')}，转 REVIEW", flush=True)
        return None
    try:
        content = (r.json()["choices"][0]["message"].get("content") or "").strip()
    except Exception:
        return None
    return _normalize(_extract_json(content))


def judge_items(items, batch=None, sleep=2.0):
    """串行判断一批候选（免费账户限流 2-3 次/分钟，必须串行 + 间隔）。

    返回 {index: result}，result 为 None 表示判断失败（上层转 REVIEW）。
    """
    batch = batch or BATCH
    out = {}
    idxs = list(range(len(items)))
    for i in range(0, len(idxs), batch):
        chunk = idxs[i:i + batch]
        for j in chunk:
            out[j] = judge_one(items[j])
        time.sleep(sleep)
    return out
