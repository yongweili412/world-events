# -*- coding: utf-8 -*-
"""World Events 事件模型 v3：三类档案 + 七维价值评分。

定位（唯一真源）：
    World Events 不是新闻聚合网站，而是一个记录世界事件、社会变化、
    文化现象与时代记忆的长期数字档案。
    新闻是来源，事件才是数据。
    世界影响力决定事件的重要性；时代记忆决定事件是否值得被留下。

设计原则：
  1. 只增不改——不删除/重命名任何旧字段，存量事件缺新字段仍可正常渲染。
  2. 单一事实源——权重/阈值/关键词全在 value_config.json，改配置即可重算全库，
     无需重新抓数据（这正是保留 KEEP/REVIEW/DROP + reason 的意义）。
  3. 证据与价值分离——evidenceStatus 表示"我们有多确定"，
     价值分表示"若事实成立是否值得保存"，两者不得混为一谈。
"""
import json
import hashlib
import re
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "value_config.json"

DIMENSIONS = (
    "globalImpact", "socialImpact", "culturalImpact",
    "economicImpact", "technologyImpact", "memoryValue",
)
VALUE_FIELDS = DIMENSIONS + ("archiveValue",)
EVENT_TYPES = ("world", "society_culture", "internet_trends")

EVENT_TYPE_LABELS = {
    "world": "世界大事",
    "society_culture": "社会文化",
    "internet_trends": "互联网时代",
}


def load_config(path=None):
    return json.loads(Path(path or CONFIG_PATH).read_text(encoding="utf-8"))


CFG = load_config()
VERSION = CFG["version"]


def clamp(v, lo=0, hi=100):
    """把分数夹到 0-100 的整数区间。"""
    try:
        v = int(round(float(v)))
    except (TypeError, ValueError):
        return None
    return max(lo, min(hi, v))


def compute_archive_value(scores):
    """archiveValue = round(weighted*0.60 + max(六维)*0.40)。

    "最高维加权"确保：
      - 战争靠 世界/社会/记忆 入库；
      - 网络现象即使世界影响低，也能靠 文化/记忆 入库；
      - 普通产品发布不会轻易达标。
    """
    w = CFG["weights"]
    f = CFG["archiveFormula"]
    vals = [scores.get(d) or 0 for d in DIMENSIONS]
    weighted = sum(v * w[d] for v, d in zip(vals, DIMENSIONS))
    return clamp(weighted * f["weightedPart"] + max(vals) * f["maxPart"])


def decide(archive_value, scores=None, event_type=None, evidence_status="medium",
           confidence=None):
    """根据 archiveValue / 类型 / 证据 给出 KEEP / REVIEW / DROP。

    关键护栏：
      - 弱证据的文化/互联网事件禁止自动 KEEP（模型容易凭常识推高 memoryValue）；
      - 弱证据不得成为 DROP 的理由——信息不足时进 REVIEW；
      - 世界级重大事件即使证据弱也可暂定 KEEP，但会标记"待补独立来源"。
    """
    t = CFG["thresholds"]
    scores = scores or {}
    av = archive_value or 0

    if av < t["review"]:
        # 低分：只有"置信度够高且无任何维度突出"才可判 DROP
        has_peak = any((scores.get(d) or 0) >= t["keepMinDim"] for d in DIMENSIONS)
        conf = t["dropMinConfidence"] if confidence is None else confidence
        if not has_peak and conf >= t["dropMinConfidence"]:
            return "DROP"
        return "REVIEW"

    if av >= t["keep"]:
        # 高分：文化/互联网类若证据弱，只能进 REVIEW 等补证
        if evidence_status == "weak" and event_type in ("society_culture", "internet_trends"):
            return "REVIEW"
        return "KEEP"

    return "REVIEW"


def assess_evidence(sources):
    """证据质量评级：strong / medium / weak。

    注意：84.7% 的存量事件只指向维基月度存档页（Portal:Current_events/YYYY_Month），
    这类聚合页不能计作"独立来源"，只能算 weak。
    """
    ev = CFG["evidence"]
    patterns = ev["archiveUrlPatterns"]
    srcs = sources or []
    if not srcs:
        return "weak", 0

    domains = set()
    archive_only = True
    for s in srcs:
        url = (s.get("url") or "").strip()
        if not url:
            continue
        if any(p in url for p in patterns):
            continue
        archive_only = False
        m = re.match(r"https?://([^/]+)", url)
        if m:
            domains.add(m.group(1).lower())

    n = len(domains)
    if archive_only or n == 0:
        return "weak", 0
    if n >= ev["strong"]["minIndependentDomains"]:
        return "strong", n
    return "medium", n


def input_hash(ev):
    """输入指纹：标题+摘要+日期+来源URL集合。内容不变则跳过重算。"""
    srcs = "|".join(sorted((s.get("url") or "") for s in (ev.get("sources") or [])))
    raw = f"{ev.get('title','')}|{ev.get('summary','')}|{ev.get('date','')}|{srcs}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


def build_assessment(method, confidence, evidence_status, needs_evidence=False,
                     model=None, input_hash_value=None):
    return {
        "version": VERSION,
        "method": method,
        "confidence": clamp(confidence),
        "evidenceStatus": evidence_status,
        "needsEvidence": bool(needs_evidence),
        "assessedAt": _now(),
        "model": model,
        "inputHash": input_hash_value,
    }


def _now():
    import datetime
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def apply_scores(ev, scores, event_type=None, method="rules", confidence=None,
                 reason="", model=None):
    """把评分结果写回事件字典（只增字段，不动旧字段）。返回新的 assessment。"""
    for d in DIMENSIONS:
        ev[d] = clamp(scores.get(d))
    ev["archiveValue"] = compute_archive_value(scores)

    status, _n = assess_evidence(ev.get("sources"))
    ev["eventType"] = event_type
    ev["decision"] = decide(ev["archiveValue"], scores, event_type, status)
    ev["decisionReason"] = reason

    if confidence is None:
        confidence = {"rules": 60, "llm": 85, "hybrid": 80, "manual": 100}.get(method, 60)
    if status == "weak":
        confidence = min(confidence, CFG["evidence"]["weak"]["maxConfidence"])

    ev["assessment"] = build_assessment(
        method=method,
        confidence=confidence,
        evidence_status=status,
        needs_evidence=(status == "weak"),
        model=model,
        input_hash_value=input_hash(ev),
    )
    return ev["assessment"]


# ---------- 兼容读取（前端/构建用，存量缺字段时绝不崩） ----------

def get_event_type(ev):
    """存量事件没有 eventType 时返回 None（前端显示"待分类"），不猜。"""
    t = ev.get("eventType")
    return t if t in EVENT_TYPES else None


def get_score(ev, dim):
    """取某个维度分。缺失返回 None —— 绝不当作 0（0 分与未评分含义不同）。"""
    v = ev.get(dim)
    return v if isinstance(v, int) else None


def has_scores(ev):
    return ev.get("archiveValue") is not None


def to_public(ev):
    """导出给前端的最小字段集（避免把 assessment 内部细节塞进 45MB 的 events.js）。"""
    out = {"id": ev.get("id"), "eventType": get_event_type(ev)}
    for d in VALUE_FIELDS:
        out[d] = get_score(ev, d)
    out["decision"] = ev.get("decision")
    a = ev.get("assessment") or {}
    out["evidence"] = a.get("evidenceStatus")
    return out
