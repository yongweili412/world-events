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
import re
from pathlib import Path

import event_schema as ES

CFG = ES.CFG
KW = CFG["keywordSets"]
DIM_KW = CFG["dimensionKeywords"]
NOISE = CFG["commercialNoise"]
RESCUE = CFG["rescueKeywords"]["words"]

WORLD_CATS = ("军事", "政治", "国际关系", "国际政治", "灾难", "自然灾害", "冲突")
CULTURE_CATS = ("文化", "体育")

# 安全网关键词：标题/摘要/来源里出现这些词，说明是真实世界/冲突/灾难/人道/重大奖项事件，
# 绝不自动 DROP（只进 REVIEW 等人工/补证）。防止商业噪声词误杀真正重要的事件。
# 单一事实源：词表在 value_config.json 的 safeKeywords.words。
SAFE_KEYWORDS = tuple(CFG.get("safeKeywords", {}).get("words", []))

# 重大奖项（prestige）：确认「已获奖」才触发，提名/预测一律不算。
PRESTIGE = CFG.get("prestige", {})
SCI_AWARDS = tuple(PRESTIGE.get("sciAwards", []))
HUMAN_AWARDS = tuple(PRESTIGE.get("humanAwards", []))
AWARD_WEAK = tuple(PRESTIGE.get("weakSignals", []))
PRESTIGE_FLOORS = PRESTIGE.get("floors", {})


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


def detect_prestige(ev, text):
    """判断是否为「已确认获奖」的重大奖项事件。

    返回 (是否触发, 领域 key)。三重条件缺一不可：
      1. 文本里出现奖项名；
      2. 出现获奖动词且位置紧邻奖项名（正则限定 14 字内，避免"获奖"与奖项名相隔太远）；
      3. 不含提名/预测/入围等弱信号。

    判定范围优先用标题（短、精准）；标题里没有奖项名时才退回全文。
    """
    if not SCI_AWARDS and not HUMAN_AWARDS:
        return False, None
    title = ev.get("title") or ""
    scope = title if any(a in title for a in SCI_AWARDS + HUMAN_AWARDS) else text
    if not any(a in scope for a in SCI_AWARDS + HUMAN_AWARDS):
        return False, None
    if _hits(scope, AWARD_WEAK) > 0:
        return False, None
    for pat in PRESTIGE.get("winRegex", []):
        if re.search(pat, scope):
            key = "science" if any(a in scope for a in SCI_AWARDS) else "humanities"
            # 诺贝尔经济学奖归属人文社科，科学名单已单独列出物理/化学/医学/生理
            if "经济学奖" in scope or "诺贝尔文学奖" in scope or "和平奖" in scope:
                key = "humanities"
            return True, key
    return False, None


def score_dimensions(ev):
    """先验 + 关键词加权 → 六维分（0-100）。"""
    text = build_text(ev)
    scores = prior_for(ev)
    boosts = keyword_boosts(text)
    for d in ES.DIMENSIONS:
        scores[d] = ES.clamp(scores.get(d, 0) + boosts.get(d, 0))

    # 重大奖项直通：诺贝尔奖等不该被"活动/发布"这类歧义噪声词压到 DROP 区间
    is_prestige, prestige_key = detect_prestige(ev, text)
    rescued = _hits(text, RESCUE) > 0
    if is_prestige:
        floor = PRESTIGE_FLOORS.get(prestige_key) or PRESTIGE_FLOORS.get("humanities") or {}
        for d in ES.DIMENSIONS:
            f = floor.get(d)
            if f is not None:
                scores[d] = ES.clamp(max(scores.get(d, 0), f))
        return scores, False, rescued, True

    # 商业噪声惩罚分两档：强噪声（明确商业）×0.60，弱噪声（语义歧义）×0.85。
    # 弱噪声只轻微降权，不再单独把事件推进 DROP 区间。
    strong = _hits(text, NOISE["patterns"]) > 0
    ambiguous = _hits(text, NOISE.get("ambiguousPatterns", [])) > 0
    # 商业噪声惩罚只针对「普通商业/日常信息」，绝不应施加于世界/外交/军事类事件——
    # 这类事件常含"合作/任命/发布"等词，若被降权会误杀真正的战争、外交、政治事件。
    is_world = bool(set(ev.get("category") or []) & set(WORLD_CATS))
    if rescued or is_world:
        pass
    elif strong:
        for d in ES.DIMENSIONS:
            scores[d] = ES.clamp(int(scores[d] * NOISE.get("penaltyStrong", 0.60)))
    elif ambiguous:
        for d in ES.DIMENSIONS:
            scores[d] = ES.clamp(int(scores[d] * NOISE.get("penaltyAmbiguous", 0.85)))
    return scores, (strong or ambiguous), rescued, False


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


def layer_of(ev, scores, event_type, archive_value, evidence_status, is_noise, rescued,
             is_prestige=False):
    """决定这条事件走哪一层。"""
    lay = CFG["layering"]
    t = CFG["thresholds"]
    text = build_text(ev)
    viral = _hits(text, KW["viral"]) > 0
    peak = max((scores.get(d) or 0) for d in ES.DIMENSIONS)

    # 重大奖项（已确认获奖）直通 KEEP，放在最前——否则会先被"文化分高→边界候选"
    # 拦下，最终仍可能因低分+无突出维度被判 DROP。
    if is_prestige:
        return "rules_keep"

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
    scores, is_noise, rescued, is_prestige = score_dimensions(ev)
    event_type = infer_event_type(ev, scores)
    archive_value = ES.compute_archive_value(scores)
    evidence_status, n_domains = ES.assess_evidence(ev.get("sources"))
    layer = layer_of(ev, scores, event_type, archive_value, evidence_status, is_noise, rescued,
                     is_prestige)

    # 每层给出暂定结论（boundary 层后续由 LLM 覆盖）
    if layer == "rules_keep":
        decision = "KEEP"
        confidence, method = 70, "rules"
    elif layer == "rules_drop":
        # 只有「极低分 + 明确商业噪声 + 证据非弱」才配得上 DROP 的高置信度（默认 80）。
        # 其余疑似噪声一律降到 softDropConfidence（默认 65）→ decide() 自然落到 REVIEW，
        # 保留"疑似噪声"标记但不删数据。宁可 REVIEW 不误杀。
        t = CFG["thresholds"]
        strong_noise = _hits(build_text(ev), NOISE["patterns"]) > 0
        hard_drop = (
            (archive_value or 0) < t.get("hardDropMaxScore", 25)
            and strong_noise
            and evidence_status != "weak"
        )
        decision = "DROP" if hard_drop else "REVIEW"
        confidence = t.get("hardDropConfidence", 80) if hard_drop else t.get("softDropConfidence", 65)
        method = "rules"
    elif layer == "weak_evidence":
        decision = "REVIEW"
        confidence, method = 50, "rules"
    else:
        decision = "REVIEW"
        confidence, method = 45, "rules"

    reason = _reason(layer, scores, archive_value, evidence_status, is_noise, rescued, n_domains,
                     is_prestige)
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
        "isPrestige": is_prestige,
        "reason": reason,
        "inputHash": ES.input_hash(ev),
    }


def _reason(layer, scores, av, evidence, is_noise, rescued, n_domains, is_prestige=False):
    top = max(ES.DIMENSIONS, key=lambda d: scores.get(d) or 0)
    bits = []
    if is_prestige:
        bits.append(f"重大奖项（已确认获奖）直通保留（综合{av}，最强维度{top}={scores.get(top)}）")
    elif layer == "rules_keep":
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
