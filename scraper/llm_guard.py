# -*- coding: utf-8 -*-
"""LLM 模型守卫（用户规定 2026-09-15 的机制性落实）。

规定：
  一、一切自动任务优先使用 flash 档模型（glm-4.5-flash）；
  二、一切自动任务禁止使用 GLM-5.3 与 KIMI K3，仅在用户明确指定时才可用；
  三、配置单一事实源为 scraper/llm_config.json（换模型只改此文件 + GitHub Secrets 的 LLM_MODEL）。

用法：
    from llm_guard import resolve_model
    model = resolve_model()   # 读 LLM_MODEL 环境变量 > llm_config.json > 默认 flash，并校验禁用名单
"""
import json
import os

DEFAULT_MODEL = "glm-4.5-flash"
FORBIDDEN = ("glm-5.3", "glm5.3", "kimi-k3", "kimik3", "kimi k3")


def _norm(name: str) -> str:
    return (name or "").lower().replace("_", "").replace("-", "").replace(".", "").replace(" ", "")


def resolve_model(config_path: str = None) -> str:
    """解析并校验当前应使用的模型。命中禁用名单直接抛错，阻止任务继续（宁可失败也不用违规模型）。"""
    env_model = os.environ.get("LLM_MODEL", "").strip()
    cfg_model, cfg_forbidden = "", []
    if not env_model:
        cfg_path = config_path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "llm_config.json")
        try:
            cfg = json.load(open(cfg_path, encoding="utf-8"))
            da = cfg.get("daily_automation", {}) or {}
            cfg_model = da.get("model", "") or ""
            cfg_forbidden = da.get("forbidden_models", []) or []
        except Exception:
            pass
    model = env_model or cfg_model or DEFAULT_MODEL

    forbidden = {_norm(f) for f in FORBIDDEN if f}
    forbidden |= {_norm(f) for f in cfg_forbidden if f}
    nm = _norm(model)
    for nf in forbidden:
        if nf and (nf == nm or nf in nm):
            raise RuntimeError(
                f"模型 '{model}' 属于禁用名单（GLM-5.3 / KIMI K3），自动任务一概不得使用——"
                f"仅在用户明确指定时可用。请改用 {DEFAULT_MODEL}（见 scraper/llm_config.json 规定二/三）。"
            )
    return model


if __name__ == "__main__":
    print("解析模型:", resolve_model())
