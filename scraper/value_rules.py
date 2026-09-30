# -*- coding: utf-8 -*-
"""规则层：给全量 31,220 条事件做零成本初评，并分成四层。

分层结果（layer）：
  rules_keep    —— 规则明确高价值，暂定 KEEP
  rules_drop    —— 规则明确低价值（普通商业信息/日常内容），暂定 DROP
  boundary      —— 边界/文化/互联网候选，交给 glm-4.5-flash 精修
  weak_evidence —— 证据不足（只有维基月度存档页），进 REVIEW 等补证

设计要点：
  1. 规则可复现：同输入同配置 → 同结果，改配置即可重算，不用重新抓数据。
  2. 证据与价值分离：只有维基月度存档页 → weak；但世界级重大事件仍可暂定 KEEP（标记待补证）。
  3. 宁可 REVIEW 不误杀：弱证据永远不因"证据不足"判 DROP，只进 REVIEW。
"""
import json
from pathlib import Path

import event_schema as ES

CFG = ES.CFG
KW = CFG["keywordSets"]
DIM_KW = CFG["dimensionKeywords"]
NOISE = CFG["commercialNoise"]
RESCUE = CFG["rescueKeywords"]["words"]

WORLD_CATS = ("军事", "政治", "国际关系", "国际政治", "灾难", "自然灾害", "冲突")
CULTURE_CATS = ("文化", "体育")

# 安全网关键词：标题/摘要/来源里出现这些词，说明是真实世界/冲突/灾难/人道事件，
# 绝不自动 DROP（只进 REVIEW 等人工/补证）。防止商业噪声词误杀真正重要的事件。
SAFE_KEYWORDS = (
    "战争", "战事", "冲突", "武装", "空袭", "导弹", "袭击", "爆炸", "恐怖", "伤亡",
    "遇难", "难民", "危机", "制裁", "北约", "联合国", "安理会", "停火", "政变", "戒严",
    "封锁", "地震", "海啸", "台风", "洪灾", "洪水", "干旱", "饥荒", "疫情", "核",
    "撤军", "宣战", "斡旋", "维和", "人道主义", "流离失所", "交火", "战乱", "内战",
    "军演", "试射", "边境", "紧急会议", "霍乱", "麻疹", "空难", "坠机", "坍塌",
    "矿难", "沉船", "罢工", "骚乱", "示威", "抗议", "战区", "前线", "攻势", "沦陷",
    "总统", "总理", "外交", "使馆", "贸易战", "关税", "加息", "降息", "破产", "裁员",
    "分歧", "谴责", "抗议", "停战",
)


def build_text(ev):
    """参与关键词匹配的文本：标题 + 摘要 + 标签 + 来源标题。"""
    parts = [ev.get("title") or "", ev.get("summary") or "", ev.get("description") or ""]
    parts += [t for t in (ev.get("tags") or []) if t]
    parts += [(s.get("title") or "") for s in (ev.get("sources") or [])]
    return " ".join(p for p in parts if p)


def prior_for(ev):
    """按 category 取先验分（多分类时取各维度最大值）。"""
    priors = CFG["categoryPriors"]
    base = dict(priors["_default"])
    for c in (ev.get("category") or []):
        p = priors.get(c)
        if not p:
            continue
        for d in ES.DIMENSIONS:
            base[d] = max(base.get(d, 0), p.get(d, 0))
    return base


def _hits(text, words):
    return sum(1 for w in words if w and w in text)


def keyword_boosts(text):
    """按维度累加关键词加权分。命中 1 个词给满权重，命中 ≥2 个给 1.5 倍。"""
    boosts = {d: 0 for d in ES.DIMENSIONS}
    for dim, groups in DIM_KW.items():
        for gname, weight in groups.items():
            n = _hits(text, KW.get(gname, []))
            if n == 1:
                boosts[dim] += weight
            elif n >= 2:
                boosts[dim] += int(weight * 1.5)
    return boosts


def score_dimensions(ev):
    """先验 + 关键词加权 → 六维分（0-100）。"""
    text = build_text(ev)
    scores = prior_for(ev)
    boosts = keyword_boosts(text)
    for d in ES.DIMENSIONS:
        scores[d] = ES.clamp(scores.get(d, 0) + boosts.get(d, 0))

    # 商业噪声惩罚：普通发布/评测/促销类信息整体降权
    is_noise = _hits(text, NOISE["patterns"]) > 0 or _hits(text, NOISE["weakPatterns"]) > 0
    rescued = _hits(text, RESCUE) > 0
    # 商业噪声惩罚只针对「普通商业/日常信息」，绝不应施加于世界/外交/军事类事件——
    # 这类事件常含"合作/任命/发布"等词，若被降权会误杀真正的战争、外交、政治事件。
    is_world = bool(set(ev.get("category") or []) & set(WORLD_CATS))
    if is_noise and not rescued and not is_world:
        for d in ES.DIMENSIONS:
            scores[d] = ES.clamp(int(scores[d] * 0.60))
    return scores, is_noise, rescued


def infer_event_type(ev, scores):
    """推断档案三类。存量无互联网类目，只能靠传播类关键词 + 维度分组合判断。"""
    text = build_text(ev)
    viral = _hits(text, KW["viral"]) > 0
    # 传播关键词（如"转发"）本身不足以构成互联网事件，必须同时有文化/记忆信号，
    # 否则普通内容会被误升级成边界候选、该 DROP 的没 DROP。
    culture_signal = scores.get("culturalImpact", 0) >= 40 or scores.get("memoryValue", 0) >= 40
    cats = ev.get("category") or []

    if viral and culture_signal and scores.get("globalImpact", 0) < 45:
        return "internet_trends"
    if any(c in WORLD_CATS for c in cats) or scores.get("globalImpact", 0) >= 45:
        return "world"
    if any(c in CULTURE_CATS for c in cats) or scores.get("culturalImpact", 0) >= 45:
        return "society_culture"
    if scores.get("socialImpact", 0) >= 40:
        return "society_culture"
    return "internet_trends" if (viral and culture_signal) else "society_culture"


def layer_of(ev, scores, event_type, archive_value, evidence_status, is_noise, rescued):
    """决定这条事件走哪一层。"""
    lay = CFG["layering"]
    t = CFG["thresholds"]
    text = build_text(ev)
    viral = _hits(text, KW["viral"]) > 0
    peak = max((scores.get(d) or 0) for d in ES.DIMENSIONS)

    culture_signal = scores.get("culturalImpact", 0) >= 40 or scores.get("memoryValue", 0) >= 40

    # 文化/互联网候选交给 LLM——这类高度依赖语境，关键词容易误杀也容易误捧。
    # 但仅有"转发"之类的弱传播词、没有文化/记忆信号时不得升级，否则会漏掉本该 DROP 的普通内容。
    if (viral and culture_signal) or event_type == "internet_trends" \
            or scores.get("culturalImpact", 0) >= 55 or scores.get("memoryValue", 0) >= 55:
        return "boundary"

    if rescued:
        return "boundary"

    if evidence_status == "weak":
        # 世界级重大事件证据弱也可暂定 KEEP（标记待补证）；其余进 REVIEW
        if event_type == "world" and archive_value >= t["keep"]:
            return "rules_keep"
        return "weak_evidence"

    if archive_value >= lay["rulesKeepScore"]:
        return "rules_keep"

    if archive_value < lay["rulesDropScore"] and is_noise and peak < t["keepMinDim"]:
        # 安全网：含世界/冲突/灾难/人道关键词的事件绝不自动 DROP，改进 REVIEW 等人工/补证
        if _hits(text, SAFE_KEYWORDS) > 0:
            return "weak_evidence"
        return "rules_drop"

    return "boundary"


def score_event(ev):
    """对单条事件做规则初评。返回结构化结果（不修改 ev）。"""
    scores, is_noise, rescued = score_dimensions(ev)
    event_type = infer_event_type(ev, scores)
    archive_value = ES.compute_archive_value(scores)
    evidence_status, n_domains = ES.assess_evidence(ev.get("sources"))
    layer = layer_of(ev, scores, event_type, archive_value, evidence_status, is_noise, rescued)

    # 每层给出暂定结论（boundary 层后续由 LLM 覆盖）
    if layer == "rules_keep":
        decision = "KEEP"
        confidence, method = 70, "rules"
    elif layer == "rules_drop":
        decision = "DROP"
        confidence, method = 80, "rules"
    elif layer == "weak_evidence":
        decision = "REVIEW"
        confidence, method = 50, "rules"
    else:
        decision = "REVIEW"
        confidence, method = 45, "rules"

    reason = _reason(layer, scores, archive_value, evidence_status, is_noise, rescued, n_domains)
    return {
        "id": ev.get("id"),
        "scores": scores,
        "archiveValue": archive_value,
        "eventType": event_type,
        "layer": layer,
        "decision": decision,
        "confidence": confidence,
        "method": method,
        "evidenceStatus": evidence_status,
        "independentDomains": n_domains,
        "needsEvidence": evidence_status == "weak",
        "isNoise": is_noise,
        "rescued": rescued,
        "reason": reason,
        "inputHash": ES.input_hash(ev),
    }


def _reason(layer, scores, av, evidence, is_noise, rescued, n_domains):
    top = max(ES.DIMENSIONS, key=lambda d: scores.get(d) or 0)
    bits = []
    if layer == "rules_keep":
        bits.append(f"规则判定高价值（综合{av}，最强维度{top}={scores.get(top)}）")
    elif layer == "rules_drop":
        bits.append(f"疑似普通商业/日常信息（综合{av}，无突出维度）")
    elif layer == "weak_evidence":
        bits.append("来源仅维基月度存档页，缺独立佐证，待补证")
    else:
        bits.append("边界候选，需模型结合语境判断")
    if rescued:
        bits.append("命中产业转型/现象级关键词，已豁免噪声惩罚并升级")
    if is_noise and not rescued:
        bits.append("命中商业噪声词，已整体降权")
    if evidence == "weak":
        bits.append("证据等级 weak")
    elif n_domains:
        bits.append(f"{n_domains} 个独立来源域名")
    return "；".join(bits)
