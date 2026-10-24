# -*- coding: utf-8 -*-
"""LLM 模型守卫（用户规定的机制性落实）。

模型结构（2026-10-19 起为三档，详见 scraper/llm_config.json）：
  free_promo   限时免费档      step-5-preview              首选，有密钥即排链首
  performance  性能优先兜底档  deepseek/deepseek-v4.1-flash 价值判断/重判等需推理质量的任务
  free_first   免费为主兜底档  tencent/hy3-free            翻译/速览/字段中文化等量大任务

硬约束：
  一、每档自带 base_url 与 key_env —— 换档时 model + 网关 + 密钥三者同步切换，
      绝不能只换模型名（否则会把 A 厂商模型发往 B 厂商网关，返回 model_not_found）。
  二、exhausted_models 中的模型（额度已耗尽）一律硬跳过，绝不作为切换目标；
      当前包含 glm-4.7 / glm-4.7-flash / glm-4.5-flash。
  三、GLM-5.3 与 KIMI K3 为禁用名单，仅在用户明确指定时才可用（exhausted 模型同样受此约束）。
  四、配置单一事实源为 scraper/llm_config.json；所需密钥未配置的档位自动跳过。

用法：
    from llm_guard import resolve_model, resolve_endpoint, resolve_model_chain
    model = resolve_model()                                  # 按默认场景取首个可用档
    model, base_url, source = resolve_endpoint()             # 需要 base_url / 选择依据时用这个
    chain = resolve_model_chain(scenario="free_first")       # 该场景下全部可用模型
    自检：python scraper/llm_guard.py [performance|free_first]
"""
import datetime
import json
import os
import time

import requests

# 默认模型（配置缺失时的最后回退）。
# 2026-10-19 变更：原 glm-4.5-flash 额度已用完（用户告知），
# 与 glm-4.7 / glm-4.7-flash 一并列入 llm_config.json 的 exhausted_models。
# 当前默认使用已实测可用的 deepseek/deepseek-v4.1-flash（qnaigc 网关）。
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"
DEFAULT_BASE_URL = "https://api.qnaigc.com/v1"
FORBIDDEN = ("glm-5.3", "glm5.3", "kimi-k3", "kimik3", "kimi k3")


def _norm(name: str) -> str:
    return (name or "").lower().replace("_", "").replace("-", "").replace(".", "").replace(" ", "")


def _load_config(config_path: str = None) -> dict:
    cfg_path = config_path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "llm_config.json")
    try:
        with open(cfg_path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _active_free(cfg: dict) -> dict:
    """返回当前时段生效的第一个限时免费模型条目（无则返回空 dict）。"""
    chain = active_free_chain(cfg)
    return chain[0] if chain else {}


def active_free_chain(cfg: dict) -> list:
    """返回当前时段全部生效的限时免费模型条目（按登记顺序），已剔除禁用名单。"""
    entries = ((cfg.get("daily_automation") or {}).get("free_models")) or []
    today = datetime.date.today().isoformat()
    out = []
    if _free_disabled(cfg):
        return out
    for ent in entries:
        if not isinstance(ent, dict):
            continue
        model = (ent.get("model") or "").strip()
        if not model:
            continue
        start = (ent.get("from") or "").strip()
        end = (ent.get("until") or "").strip()
        if start and today < start:
            continue
        if end and today > end:
            continue
        if _is_forbidden(model, (cfg.get("daily_automation") or {}).get("forbidden_models")):
            continue
        out.append(ent)
    return out


def _free_disabled(cfg: dict) -> bool:
    """限时免费优先总开关：daily_automation.use_free_models 设为 false 时关闭（应急用）。"""
    da = cfg.get("daily_automation") or {}
    return da.get("use_free_models") is False


def _is_forbidden(model: str, cfg_forbidden: list) -> bool:
    forbidden = {_norm(f) for f in FORBIDDEN if f}
    forbidden |= {_norm(f) for f in (cfg_forbidden or []) if f}
    # 用户显式指定的免费变体（如 moonshotai/kimi-k3-free，2026-10-25 指定）
    # 与禁用的完整版是不同模型；必须在 _norm 之前判断——_norm 会去掉连字符。
    if "-free" in (model or "").lower():
        return False
    nm = _norm(model)
    return any(nf and (nf == nm or nf in nm) for nf in forbidden)


def _check_forbidden(model: str, cfg_forbidden: list, source: str) -> str:
    forbidden = {_norm(f) for f in FORBIDDEN if f}
    forbidden |= {_norm(f) for f in (cfg_forbidden or []) if f}
    # 用户显式指定的 -free 免费变体放行（判断同样须在 _norm 之前，理由同上）。
    if "-free" in (model or "").lower():
        return model
    nm = _norm(model)
    for nf in forbidden:
        if not nf:
            continue
        if nf == nm or nf in nm:
            raise RuntimeError(
                f"模型 '{model}'（来源：{source}）属于禁用名单（GLM-5.3 / KIMI K3），自动任务一概不得使用——"
                f"仅在用户明确指定时可用（-free 免费变体除外）。请改用限时免费模型或 {DEFAULT_MODEL}（见 scraper/llm_config.json 规定三/七）。"
            )
    return model


def _exhausted_set(cfg: dict) -> set:
    """额度已耗尽的模型集合（归一化）。这些模型不得再发起任何请求。"""
    da = cfg.get("daily_automation") or {}
    return {_norm(m) for m in (da.get("exhausted_models") or []) if m}


def _tier_table(cfg: dict) -> dict:
    """返回 model_tiers 档位表（键=档位名，值=档位定义）。旧配置无此结构时返回空 dict。"""
    da = cfg.get("daily_automation") or {}
    tiers = da.get("model_tiers") or {}
    return {k: v for k, v in tiers.items() if isinstance(v, dict) and (v.get("model") or "").strip()}


def _tier_for_model(cfg: dict, model: str) -> dict:
    """按模型标识反查档位定义（归一化后比较，容忍大小写/连字符差异）。"""
    n = _norm(model)
    for t in _tier_table(cfg).values():
        if _norm(t.get("model") or "") == n:
            return t
    return {}


def _tier_available(tier: dict, cfg: dict) -> bool:
    """档位是否可用：启用 + 未耗尽 + 未禁用 + 所需密钥已配置。

    密钥未配置的档位必须判定为不可用——否则会发出注定 401/400 的请求，
    既浪费时间又用错误码掩盖真实原因。
    """
    if tier.get("enabled") is False:
        return False
    model = (tier.get("model") or "").strip()
    if not model:
        return False
    if _norm(model) in _exhausted_set(cfg):
        return False
    if _is_forbidden(model, (cfg.get("daily_automation") or {}).get("forbidden_models")):
        return False
    key_env = (tier.get("key_env") or "").strip()
    if key_env and not (os.environ.get(key_env) or "").strip():
        return False
    return True


def _scenario_chain(cfg: dict, scenario: str = None) -> list:
    """按场景返回档位名列表（如 ['free_promo','performance','free_first']）。"""
    da = cfg.get("daily_automation") or {}
    sc = (scenario or da.get("default_scenario") or "performance").strip().lower()
    table = da.get("chain_by_scenario") or {}
    names = table.get(sc) or table.get("performance") or []
    tiers = _tier_table(cfg)
    return [n for n in names if isinstance(n, str) and n in tiers]


def tier_status(config_path: str = None, scenario: str = None) -> list:
    """诊断用：列出各档位的可用性与原因，便于排查「为什么走了这个模型」。"""
    cfg = _load_config(config_path)
    out = []
    for name in _scenario_chain(cfg, scenario):
        t = _tier_table(cfg)[name]
        model = (t.get("model") or "").strip()
        key_env = (t.get("key_env") or "").strip()
        if t.get("enabled") is False:
            reason = "档位已禁用"
        elif _norm(model) in _exhausted_set(cfg):
            reason = "额度已耗尽（exhausted_models）"
        elif _is_forbidden(model, (cfg.get("daily_automation") or {}).get("forbidden_models")):
            reason = "命中禁用名单"
        elif key_env and not (os.environ.get(key_env) or "").strip():
            reason = f"缺少密钥环境变量 {key_env}"
        else:
            reason = "可用"
        out.append({"tier": name, "label": t.get("label"), "model": model,
                    "base_url": t.get("base_url"), "key_env": key_env,
                    "available": _tier_available(t, cfg), "reason": reason})
    return out



def resolve_endpoint(config_path: str = None, scenario: str = None):
    """返回 (model, base_url, source)。

    选择顺序（2026-10-19 三档结构）：
      1) 环境变量 LLM_MODEL 显式指定 —— 强制覆盖，仅临时锁定单一模型时使用
      2) 按场景候选链（chain_by_scenario）取第一个「可用」档位；
         可用 = 未耗尽 + 未禁用 + 所需密钥已配置
      3) 旧配置兼容：free_models 限时免费档 → 回退 DEFAULT_MODEL
    """
    cfg = _load_config(config_path)
    da = cfg.get("daily_automation", {}) or {}
    cfg_forbidden = da.get("forbidden_models", []) or []
    env_model = os.environ.get("LLM_MODEL", "").strip()
    cfg_base = (da.get("base_url") or DEFAULT_BASE_URL).strip()

    # 1) 环境变量指定的模型（额度已耗尽的忽略，继续按档位选择）
    if env_model and _norm(env_model) in _exhausted_set(cfg):
        print(f"[llm] 忽略环境变量 LLM_MODEL={env_model}：该模型额度已耗尽", flush=True)
        env_model = ""
    # 环境变量若指向某个已登记档位内的模型，视为冗余：优先级由 chain_by_scenario 决定，
    # 否则残留的 LLM_MODEL 会一直把限时免费档压到链尾。
    if env_model and _tier_table(cfg) and _tier_for_model(cfg, env_model):
        env_model = ""
    if env_model:
        tier = _tier_for_model(cfg, env_model)
        base = ((tier.get("base_url") or cfg_base) if tier else cfg_base).strip()
        return _check_forbidden(env_model, cfg_forbidden, "环境变量 LLM_MODEL"), base, "env_override"

    # 2) 三档结构：按场景链取第一个可用档位
    tiers = _tier_table(cfg)
    if tiers:
        for name in _scenario_chain(cfg, scenario):
            t = tiers[name]
            if _tier_available(t, cfg):
                model = _check_forbidden((t.get("model") or "").strip(), cfg_forbidden,
                                         t.get("label") or name)
                return model, (t.get("base_url") or cfg_base).strip(), f"tier:{name}"
        # 所有档均不可用：返回空模型名，调用方据此跳过，绝不硬发请求
        return "", "", "no_available_tier"

    # 3) 旧结构兼容
    explicit = env_model or (da.get("model") or "").strip()
    if explicit and _norm(explicit) != _norm(DEFAULT_MODEL):
        return _check_forbidden(explicit, cfg_forbidden, "llm_config.json"), cfg_base, "explicit"
    free = _active_free(cfg)
    if free:
        model = free["model"].strip()
        return (_check_forbidden(model, cfg_forbidden, "free_models 限时免费"),
                (free.get("base_url") or cfg_base).strip(), "free_promo")
    model = explicit or DEFAULT_MODEL
    return _check_forbidden(model, cfg_forbidden, "默认模型"), cfg_base, "fallback_default"


# 模型名 → 厂商路由。不同厂商的模型必须走各自的网关与密钥：
# 只换模型名、不换网关，等于把 A 厂商的模型发给 B 厂商的网关，必然返回 model_not_found。
# 2026-10-19 修正：此前 chat_raw 在循环外算一次 base，换档时模型变了网关没变，
# 导致「deepseek 429 → 兜底 glm-4.5-flash 发往 qnaigc 网关 → 400 model_not_found」，
# 兜底链自加入起从未真正生效（这是 llm_fail 长期降不下去的真实根因之一）。
PROVIDER_ROUTES = (
    # (模型名前缀小写, 该厂商默认网关, 该厂商密钥环境变量名)
    ("deepseek", "https://api.qnaigc.com/v1", "LLM_API_KEY"),
    ("glm", "https://open.bigmodel.cn/api/paas/v4", "LLM_API_KEY_ZHIPU"),
    ("kimi", "https://api.qnaigc.com/v1", "LLM_API_KEY"),
)

MODEL_RETRY_ATTEMPTS = 2   # 同一档遇到 429 时先退避重试的次数（限流是暂时的，换档未必更优）
RETRY_BACKOFF = 15         # 退避基数（秒），第 n 次重试等待 RETRY_BACKOFF × n


def _route_for(model, cfg=None, fallback_base=None, fallback_key=None):
    """解析模型应使用的 ((base_url, api_key), reason)；不可用时 base 为 None。

    判定顺序：额度耗尽 → 档位表（该档自己的 base_url + key_env）→ 厂商前缀规则 → 调用方回退。
    只有模型名与网关、密钥三者同步切换，换档才算真正生效。
    """
    cfg = cfg if cfg is not None else _load_config()
    if _norm(model) in _exhausted_set(cfg):
        return None, "exhausted"

    # ① 档位表精确匹配（三档结构：每档自带 base_url 与 key_env）
    tier = _tier_for_model(cfg, model)
    if tier:
        key_env = (tier.get("key_env") or "").strip()
        k = (os.environ.get(key_env) or "").strip() if key_env else ""
        if key_env and not k:
            return None, f"missing_key:{key_env}"
        base = (tier.get("base_url") or fallback_base or DEFAULT_BASE_URL).rstrip("/")
        if not (k or fallback_key):
            return None, "no_key"
        return (base, k or fallback_key), ""

    # ② 厂商前缀规则（旧配置兼容 / 档位未登记时）
    n = _norm(model)
    for prefix, base_default, key_env in PROVIDER_ROUTES:
        if n.startswith(prefix):
            env_base = (os.environ.get(prefix.upper() + "_BASE_URL") or "").strip()
            k = (os.environ.get(key_env) or "").strip()
            if not k:
                return None, f"missing_key:{key_env}"
            return ((env_base or base_default).rstrip("/"), k), ""

    # ③ 调用方回退
    if not fallback_base or not fallback_key:
        return None, "no_key"
    return (fallback_base, fallback_key), ""


def chat_raw(prompt, key: str = None, base: str = None, timeout: int = 300, temperature: float = 0.3,
             max_tokens: int = None, thinking: str = "disabled", extra: dict = None, models: list = None,
             scenario: str = None):
    """按候选链依次调用 chat/completions，遇限流(429)/5xx/超时自动换下一档。

    每一档都按模型所属档位解析网关与密钥（model + base_url + key 三者同步切换）；
    额度耗尽或缺密钥的档直接跳过，绝不发出注定失败的请求。
    同一档遇 429 先退避重试，再考虑换档。

    scenario：任务场景，决定候选链顺序（如 performance / free_first）。
              默认取 llm_config.json 的 daily_automation.default_scenario。

    返回最后一次的 response 对象（成功即返回该响应），调用方可照常读 .status_code / .json()。
    """
    key = (key or os.environ.get("LLM_API_KEY", "")).strip()
    base = (base or os.environ.get("LLM_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    cfg = _load_config()
    chain = models or resolve_model_chain(scenario=scenario)
    if not chain:
        print("[llm] 候选链为空：所有档位均不可用（缺密钥或额度耗尽），本次不调用模型", flush=True)
        return None
    last = None
    for idx, m in enumerate(chain):
        route, reason = _route_for(m, cfg, base, key)
        if route is None:
            if reason == "exhausted":
                print(f"[llm] 跳过 {m}：额度已耗尽（llm_config.json exhausted_models）", flush=True)
            elif reason.startswith("missing_key:"):
                print(f"[llm] 跳过 {m}：未配置密钥 {reason.split(':', 1)[1]}"
                      f"（跳过而非硬发，避免 401/model_not_found）", flush=True)
            else:
                print(f"[llm] 跳过 {m}：{reason}", flush=True)
            continue
        m_base, m_key = route
        body = {"model": m, "messages": [{"role": "user", "content": prompt}], "temperature": temperature}
        if max_tokens:
            body["max_tokens"] = max_tokens
        if thinking:
            body["thinking"] = {"type": thinking}
        if extra:
            body.update(extra)

        r = None
        for attempt in range(MODEL_RETRY_ATTEMPTS):
            try:
                r = requests.post(m_base + "/chat/completions",
                                  headers={"Authorization": "Bearer " + m_key,
                                           "Content-Type": "application/json"},
                                  json=body, timeout=timeout)
            except Exception as ex:
                print(f"[llm] {m} 请求异常({type(ex).__name__})，不再重试同档", flush=True)
                r = None
                break
            if r.status_code == 200:
                if idx > 0:
                    print(f"[llm] 已自动换档到 {m} 调用成功", flush=True)
                return r
            last = r
            if r.status_code == 429 and attempt + 1 < MODEL_RETRY_ATTEMPTS:
                wait = RETRY_BACKOFF * (attempt + 1)
                print(f"[llm] {m} 限流 429，{wait}s 后重试同档"
                      f"（{attempt + 1}/{MODEL_RETRY_ATTEMPTS}）", flush=True)
                time.sleep(wait)
                continue
            break

        if r is None:
            continue
        if _is_retryable(r) and idx + 1 < len(chain):
            print(f"[llm] {m} 返回 {r.status_code}（限流/额度/服务不可用），"
                  f"切换到 {chain[idx + 1]}", flush=True)
            time.sleep(1.5)
            continue
        print(f"[llm] {m} 返回 {r.status_code}，不再换档", flush=True)
        return r
    return last


def _is_retryable(r) -> bool:
    """判断是否应换下一档：HTTP 限流/5xx，或智谱业务码 1113(无额度)/1302,1305(限流)。"""
    if r is None:
        return False
    if r.status_code in (429, 500, 502, 503, 504):
        return True
    try:
        txt = r.text or ""
    except Exception:
        return False
    for flag in ("余额不足", "无可用资源包", '"1113"', "1113", '"1302"', '"1305"', "访问量过大", "请稍后再试"):
        if flag in txt:
            return True
    return False


def resolve_model(config_path: str = None) -> str:
    """解析并校验当前应使用的模型。命中禁用名单直接抛错，阻止任务继续（宁可失败也不用违规模型）。"""
    return resolve_endpoint(config_path)[0]


def resolve_model_chain(config_path: str = None, scenario: str = None) -> list:
    """返回按优先级排列、且**当前确实可用**的模型列表，供脚本在限流/失败时自动换档。

    三档结构（2026-10-19）：
      - 候选链顺序由 chain_by_scenario 决定，performance 与 free_first 不同；
      - 只保留「未耗尽 + 未禁用 + 所需密钥已配置」的档位；
      - 环境变量 LLM_MODEL 存在时只返回该模型（强制锁定）。
    旧配置兼容：free_models 限时免费档 → 回退 DEFAULT_MODEL。
    """
    cfg = _load_config(config_path)
    da = cfg.get("daily_automation", {}) or {}
    cfg_forbidden = da.get("forbidden_models", []) or []
    env_model = os.environ.get("LLM_MODEL", "").strip()
    tiers = _tier_table(cfg)

    # 环境变量指向已耗尽的模型 → 忽略，按档位正常选择
    if env_model and _norm(env_model) in _exhausted_set(cfg):
        print(f"[llm] 忽略环境变量 LLM_MODEL={env_model}：该模型额度已耗尽", flush=True)
        env_model = ""

    # 环境变量指向档位内已登记的模型 → 冗余，优先级交给 chain_by_scenario
    if env_model and tiers and _tier_for_model(cfg, env_model):
        env_model = ""

    # 环境变量指定的模型不属于任何档位 → 完全锁定该模型（旧行为）
    if env_model and (not tiers or not _tier_for_model(cfg, env_model)):
        return [_check_forbidden(env_model, cfg_forbidden, "环境变量 LLM_MODEL")]

    # 三档结构
    if tiers:
        chain = []
        # LLM_MODEL 命中的档位排在链首，但不砍掉其他可用档（否则限流时无档可换）
        if env_model:
            chain.append(_check_forbidden(env_model, cfg_forbidden, "环境变量 LLM_MODEL"))
        for name in _scenario_chain(cfg, scenario):
            t = tiers[name]
            if not _tier_available(t, cfg):
                continue
            model = _check_forbidden((t.get("model") or "").strip(), cfg_forbidden,
                                     t.get("label") or name)
            if _norm(model) not in {_norm(m) for m in chain}:
                chain.append(model)
        return chain

    # 3) 旧结构兼容
    cfg_model = (da.get("model") or "").strip()
    if cfg_model and _norm(cfg_model) != _norm(DEFAULT_MODEL):
        chain = [_check_forbidden(cfg_model, cfg_forbidden, "llm_config.json")]
        if _norm(DEFAULT_MODEL) not in {_norm(m) for m in chain}:
            chain.append(_check_forbidden(DEFAULT_MODEL, cfg_forbidden, "备选回退"))
        return [m for m in chain if _norm(m) not in _exhausted_set(cfg)]
    chain = [ent["model"].strip() for ent in active_free_chain(cfg)]
    fallback = cfg_model or DEFAULT_MODEL
    if fallback not in chain:
        chain.append(fallback)
    return [m for m in chain if _norm(m) not in _exhausted_set(cfg)]


if __name__ == "__main__":
    import sys as _sys
    _sc = _sys.argv[1] if len(_sys.argv) > 1 else None
    _model, _base, _source = resolve_endpoint(scenario=_sc)
    print("场景:", _sc or "(默认)")
    print("解析模型:", _model or "(无可用档位)")
    print("接口地址:", _base or "-")
    print("选择依据:", _source)
    print()
    print("档位状态：")
    for _row in tier_status(scenario=_sc):
        _flag = "可用" if _row["available"] else "跳过"
        print(f"  [{_flag}] {_row['tier']}（{_row['label']}）"
              f"model={_row['model']} key_env={_row['key_env']} — {_row['reason']}")
    print()
    print("候选链:", resolve_model_chain(scenario=_sc) or "(空：无可用档位)")
    print()
    print("已耗尽额度:", sorted(_exhausted_set(_load_config())) or "(无)")
