# -*- coding: utf-8 -*-
"""LLM 模型守卫（用户规定的机制性落实）。

规定：
  一、一切自动任务优先使用【时段内限时免费】的模型（登记在 llm_config.json 的 free_models）；
  二、没有可用的限时免费模型时，使用 flash 档模型（glm-4.5-flash）；
  三、一切自动任务禁止使用 GLM-5.3 与 KIMI K3，仅在用户明确指定时才可用（限时免费模型同样受此约束）；
  四、配置单一事实源为 scraper/llm_config.json（换模型只改此文件 + GitHub Secrets 的 LLM_MODEL）。

用法：
    from llm_guard import resolve_model, resolve_endpoint
    model = resolve_model()                 # 限时免费 > flash，并校验禁用名单
    model, base_url, source = resolve_endpoint()   # 需要 base_url / 选择依据时用这个
"""
import datetime
import json
import os

DEFAULT_MODEL = "glm-4.5-flash"
DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
FORBIDDEN = ("glm-5.3", "glm5.3", "kimi-k3", "kimik3", "kimi k3")


def _norm(name: str) -> str:
    return (name or "").lower().replace("_", "").replace("-", "").replace(".", "").replace(" ", "")


def _load_config(config_path: str = None) -> dict:
    cfg_path = config_path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "llm_config.json")
    try:
        return json.load(open(cfg_path, encoding="utf-8"))
    except Exception:
        return {}


def _active_free(cfg: dict) -> dict:
    """返回当前时段生效的第一个限时免费模型条目（无则返回空 dict）。"""
    entries = ((cfg.get("daily_automation") or {}).get("free_models")) or []
    today = datetime.date.today().isoformat()
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
        return ent
    return {}


def _check_forbidden(model: str, cfg_forbidden: list, source: str) -> str:
    forbidden = {_norm(f) for f in FORBIDDEN if f}
    forbidden |= {_norm(f) for f in (cfg_forbidden or []) if f}
    nm = _norm(model)
    for nf in forbidden:
        if nf and (nf == nm or nf in nm):
            raise RuntimeError(
                f"模型 '{model}'（来源：{source}）属于禁用名单（GLM-5.3 / KIMI K3），自动任务一概不得使用——"
                f"仅在用户明确指定时可用。请改用限时免费模型或 {DEFAULT_MODEL}（见 scraper/llm_config.json 规定三/七）。"
            )
    return model


def resolve_endpoint(config_path: str = None):
    """返回 (model, base_url, source)。

    优先级：人工显式指定的非 flash 模型（LLM_MODEL 环境变量 / llm_config.json）
            > 时段内限时免费模型 > 回退 flash（glm-4.5-flash）。
    """
    cfg = _load_config(config_path)
    da = cfg.get("daily_automation", {}) or {}
    cfg_forbidden = da.get("forbidden_models", []) or []
    env_model = os.environ.get("LLM_MODEL", "").strip()
    cfg_model = (da.get("model") or "").strip()
    cfg_base = (da.get("base_url") or DEFAULT_BASE_URL).strip()

    # 1) 显式指定了非 flash 的模型：尊重人工指定
    explicit = env_model or cfg_model
    if explicit and _norm(explicit) != _norm(DEFAULT_MODEL):
        src = "环境变量 LLM_MODEL" if env_model else "llm_config.json"
        return _check_forbidden(explicit, cfg_forbidden, src), cfg_base, "explicit"

    # 2) 时段内限时免费模型优先
    free = _active_free(cfg)
    if free:
        model = free["model"].strip()
        return (_check_forbidden(model, cfg_forbidden, "free_models 限时免费"),
                (free.get("base_url") or cfg_base).strip(), "free_promo")

    # 3) 回退 flash
    model = explicit or DEFAULT_MODEL
    return _check_forbidden(model, cfg_forbidden, "默认 flash"), cfg_base, "fallback_flash"


def resolve_model(config_path: str = None) -> str:
    """解析并校验当前应使用的模型。命中禁用名单直接抛错，阻止任务继续（宁可失败也不用违规模型）。"""
    return resolve_endpoint(config_path)[0]


if __name__ == "__main__":
    model, base, source = resolve_endpoint()
    print("解析模型:", model)
    print("接口地址:", base)
    print("选择依据:", {"free_promo": "时段内限时免费模型", "explicit": "人工显式指定", "fallback_flash": "回退 flash 档"}.get(source, source))
