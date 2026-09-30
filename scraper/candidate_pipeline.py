# -*- coding: utf-8 -*-
"""候选闸门 —— 项目核心技术：不要让新闻直接变成事件。

    RSS/HTML/热榜/社交媒体
        ↓
      候选信息（candidate）
        ↓
    规则初评（零成本、可复现）
        ↓
    边界候选 → LLM 精修（"十年后是否值得记录"）
        ↓
    KEEP / REVIEW / DROP + 理由
        ↓
    查重 → 新建 / 合并 → events.json

模式（环境变量 INGEST_MODE）：
    off      默认。完全不判断，保持原有行为（安全网，确保线上不受影响）
    shadow   只统计并打印"如果开闸门会怎样"，仍全部放行（用于观察误杀率）
    enforce  真闸门：只有值得留下的才新建事件

关键设计：
  - **能匹配到已有事件的条目一律放行**：它只是给老事件补一个来源/时间线，
    不产生新事件，没有"新闻变事件"的风险，反而能丰富已有档案。
  - 只有"要新建事件"的候选才过价值判断 —— 这才是闸门真正要卡的地方。
  - 未通过的不静默丢弃：写入候选账本 data/candidates/，可复核、可重放。
  - 任何异常一律放行 —— 闸门故障绝不能变成丢数据。
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import event_schema as ES
import value_rules as VR

ROOT = Path(__file__).parent.parent
CANDIDATE_DIR = ROOT / "data" / "candidates"

DAILY_CAP = 30          # 每日最多新建事件数（按 archiveValue 排序，超额留到下次）


def mode():
    return (os.environ.get("INGEST_MODE", "off") or "off").strip().lower()


def item_to_pseudo_event(item):
    """把抓取条目包装成"事件形状"，好复用 value_rules 的规则层。"""
    return {
        "id": item.get("id", ""),
        "title": item.get("title", "") or "",
        "summary": item.get("summary") or item.get("content") or "",
        "description": item.get("content") or item.get("summary") or "",
        "date": item.get("date", "") or "",
        "category": [item.get("category")] if item.get("category") else [],
        "tags": list(item.get("tags") or []),
        "sources": [{"name": item.get("source", ""), "url": item.get("sourceUrl", "")}],
    }


def classify(item, use_llm=True):
    """单条候选 → 规则初评（+ 必要时 LLM 精修）→ 结论。"""
    r = VR.score_event(item_to_pseudo_event(item))
    result = {
        "layer": r["layer"],
        "eventType": r["eventType"],
        "scores": r["scores"],
        "archiveValue": r["archiveValue"],
        "decision": r["decision"],
        "reason": r["reason"],
        "method": r["method"],
    }
    # 边界/文化/互联网候选 → 交给模型结合语境判断
    if use_llm and r["layer"] == "boundary":
        try:
            import value_judge as VJ
            llm = VJ.judge_one(item)
        except Exception as ex:
            print(f"  ⚠️ 价值判断异常 {type(ex).__name__}，沿用规则结论", flush=True)
            llm = None
        if llm:
            result.update({
                "eventType": llm["eventType"],
                "scores": llm["scores"],
                "archiveValue": llm["archiveValue"],
                "decision": llm["decision"],
                "reason": llm["reason"],
                "method": "llm",
            })
        else:
            # 模型不可用 → 保守转 REVIEW，绝不默认 KEEP/DROP
            result["decision"] = "REVIEW"
            result["method"] = "rules+llm_fail"
            result["reason"] = (r["reason"] + "；模型判断不可用，转 REVIEW").strip("；")
    return result


def action_of(decision):
    return {"KEEP": "CREATE", "DROP": "REJECT", "REVIEW": "REVIEW"}.get(decision, "REVIEW")


def _match_existing(events, item):
    """是否会并入已有事件（是 → 只补来源，风险极低，一律放行）。"""
    if not events:
        return None
    try:
        import scraper as sp
        return sp._find_event(events, item)
    except Exception:
        return None


def _append_candidate(item, result, action):
    """未通过的候选写账本：不静默丢弃，可复核/重放。"""
    try:
        CANDIDATE_DIR.mkdir(parents=True, exist_ok=True)
        f = CANDIDATE_DIR / f"candidate-decisions-{time.strftime('%Y-%m')}.jsonl"
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "action": action,
                "title": (item.get("title") or "")[:120],
                "url": item.get("sourceUrl", ""),
                "source": item.get("source", ""),
                "date": item.get("date", ""),
                "archiveValue": result.get("archiveValue"),
                "eventType": result.get("eventType"),
                "decision": result.get("decision"),
                "method": result.get("method"),
                "reason": (result.get("reason") or "")[:200],
            }, ensure_ascii=False) + "\n")
    except Exception:
        pass


def gate_items(items, events=None, mode_override=None, daily_cap=DAILY_CAP,
               use_llm=True, llm_budget=30):
    """价值闸门。返回 (放行的条目, 报告)。

    llm_budget：单轮最多调用模型的次数上限。免费账户限流 2-3 次/分钟，
    不设上限可能把每日任务拖到超时，因此默认 30 次（约 10-15 分钟）。
    """
    m = (mode_override or mode()).strip().lower()
    report = {"mode": m, "total": len(items), "update_only": 0, "create": 0,
              "review": 0, "reject": 0, "llm_used": 0, "capped": 0,
              "llm_budget": llm_budget}

    if m not in ("shadow", "enforce"):
        # off：完全不动，保持原有行为
        return list(items), report

    allowed, judged = [], []
    for it in items:
        # ① 能并入已有事件 → 直接放行（只是补来源，不新建事件）
        if _match_existing(events, it) is not None:
            report["update_only"] += 1
            allowed.append(it)
            continue

        # ② 需要新建 → 过价值判断（模型调用受预算约束，避免拖垮每日任务）
        res = classify(it, use_llm=(use_llm and report["llm_used"] < llm_budget))
        if res.get("method") == "llm":
            report["llm_used"] += 1
        act = action_of(res["decision"])
        judged.append((it, res, act))

        if act == "CREATE":
            report["create"] += 1
        elif act == "REJECT":
            report["reject"] += 1
            _append_candidate(it, res, act)
        else:
            report["review"] += 1
            _append_candidate(it, res, act)

    # ③ 新建名额按 archiveValue 排序，超额留到下次（不静默丢弃）
    creates = [(it, r) for it, r, a in judged if a == "CREATE"]
    creates.sort(key=lambda x: -(x[1].get("archiveValue") or 0))
    kept = creates[:daily_cap]
    report["capped"] = max(0, len(creates) - daily_cap)
    for it, r in creates[daily_cap:]:
        _append_candidate(it, r, "CAP")

    for it, r in kept:
        # 把评分结果带回条目，供后续统一写入事件
        it["_value"] = r
        allowed.append(it)

    if m == "shadow":
        # shadow：只观察，仍全部放行
        report["shadow_note"] = "shadow 模式：仅统计，未实际拦截"
        return list(items), report

    return allowed, report


def print_report(report):
    print("\n=== 价值闸门 ===")
    print(f"  模式 {report['mode']} | 候选 {report['total']} 条")
    print(f"  并入已有事件（放行） {report['update_only']}")
    print(f"  判定可新建 {report['create']} | 待复核 {report['review']} | 丢弃 {report['reject']}"
          f" | 超额留到下轮 {report['capped']}")
    print(f"  LLM 精修 {report['llm_used']} 条")
    if report.get("shadow_note"):
        print(f"  ⚠️ {report['shadow_note']}")
    print("================")
