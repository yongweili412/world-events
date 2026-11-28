"""项目级公共工具：events.json 原子写回 + LLM 应答诊断 + 速览长度规范化。

2026-11-28 从 full_summary_batch.py 的三点修复提炼，供全项目复用，避免各脚本
各写一套（此前只有速览脚本会区分"429 限流"和"200 但内容过短"，其余脚本一律
打成"限流/失败"，日志里看不到真实原因与模型名）。

三个能力：
1. atomic_dump_events / atomic_dump —— 临时文件 + os.replace + 复读断言，
   写库中途被中断不会留下半截 JSON；格式与 shard_events.py merge 一致（紧凑）。
2. diagnose_response —— 把一次 LLM 应答分成 ok / rate_limit / server_error /
   short / http / exception，并带上网关回包里的实际模型名。
3. tidy_summary —— 去代码块标记、压缩空白、按句截断到上限，统一 60-120 字口径。

用法：
    from llm_utils import diagnose_response, atomic_dump_events
    d = diagnose_response(r, min_len=50)
    if not d["ok"]:
        print(d["reason"])          # 例：空/过短返回（glm-4.6v，36字）
"""

import json
import os
import re

EVENTS_PATH = "data/events.json"
MIN_SUMMARY_LEN = 50      # 低于此长度视为无效返回（模型软性降载常见 200+空/短）
SUMMARY_TRIM = 120        # 超过此长度按句截断（对齐 60-120 字速览口径）


def resp_model(r):
    """取实际应答的模型名（网关回包 model 字段），取不到返回空串。"""
    try:
        return str((r.json() or {}).get("model") or "")
    except Exception:
        return ""


def tidy_summary(s, min_len=MIN_SUMMARY_LEN, max_len=SUMMARY_TRIM):
    """去代码块标记/压缩空白，并把超长文本按句截断到 max_len 字左右。

    截断优先落在句号/分号；找不到合适断点才硬截并补句号。
    """
    s = re.sub(r"^```[a-z]*\n?|```$", "", (s or "").strip(), flags=re.M).strip()
    s = re.sub(r"\s+", " ", s)
    if len(s) > max_len:
        cut = max(s.rfind("。", 0, max_len + 1), s.rfind("；", 0, max_len + 1))
        s = s[:cut + 1] if cut >= min_len else s[:max_len - 2].rstrip("，、；：") + "。"
    return s


def diagnose_response(r, min_len=MIN_SUMMARY_LEN, max_len=SUMMARY_TRIM, exc=None):
    """诊断一次 LLM 调用结果，返回统一字典。

    字段：ok / kind / reason / model / content / status / retryable
      kind: ok | rate_limit | server_error | short | http | none | exception
    retryable: 同档退避重试是否有意义（非限流类 4xx 为 False，直接放弃本条）
    """
    model = resp_model(r)
    if exc is not None:
        return {"ok": False, "kind": "exception", "model": model, "status": 0, "content": "",
                "reason": f"请求异常 {type(exc).__name__}", "retryable": True}
    if r is None:
        return {"ok": False, "kind": "none", "model": model, "status": 0, "content": "",
                "reason": "未获得响应", "retryable": True}

    code = getattr(r, "status_code", 0)
    if code == 200:
        try:
            raw = (r.json()["choices"][0]["message"].get("content") or "").strip()
        except Exception:
            raw = ""
        content = tidy_summary(raw, min_len=min_len, max_len=max_len)
        if content and len(content) >= min_len:
            return {"ok": True, "kind": "ok", "model": model, "status": 200,
                    "content": content, "reason": "", "retryable": False}
        # 200 但空/过短：多为上游软性降载或思考链占满，绝不是 429
        return {"ok": False, "kind": "short", "model": model, "status": 200, "content": content,
                "reason": f"空/过短返回（{model or '未知模型'}，{len(content)}字）", "retryable": True}

    if code == 429:
        return {"ok": False, "kind": "rate_limit", "model": model, "status": 429, "content": "",
                "reason": f"限流 429（{model or '未知模型'}）", "retryable": True}
    if code and code >= 500:
        return {"ok": False, "kind": "server_error", "model": model, "status": code, "content": "",
                "reason": f"服务不可用 {code}（{model or '未知模型'}）", "retryable": True}
    return {"ok": False, "kind": "http", "model": model, "status": code, "content": "",
            "reason": f"HTTP {code}（{model or '未知模型'}）", "retryable": False}


def atomic_dump(obj, path, compact=True, expect_len=None):
    """原子写 JSON：临时文件 + os.replace；expect_len 给出时复读断言条数。

    compact=True 与 shard_events.py merge 的格式一致（无缩进），避免工作副本
    因格式差异产生无意义的字节变更。
    """
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        if compact:
            json.dump(obj, f, ensure_ascii=False)
        else:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        f.flush()
        try:
            os.fsync(f.fileno())   # 与 full_summary_batch 原实现一致：进程被杀也不留半截
        except Exception:
            pass
    os.replace(tmp, path)
    if expect_len is not None:
        with open(path, encoding="utf-8") as f:
            got = len(json.load(f))
        assert got == expect_len, f"复读断言失败 {got} != {expect_len}"


def atomic_dump_events(ev, path=EVENTS_PATH, expect_len=True):
    """原子写回 events.json（默认断言条数不变，防半截/丢条）。"""
    atomic_dump(ev, path, compact=True, expect_len=len(ev) if expect_len else None)
