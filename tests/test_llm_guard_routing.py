# -*- coding: utf-8 -*-
"""模型路由守卫的防回归测试（纯规则，不联网、不调模型）。

把用户 2026-10-19 的硬性要求变成可自动验证的断言：

  1. glm-4.7 / glm-4.7-flash / glm-4.5-flash 额度已用完，
     **任何路径都不得选中它们**（候选链、解析结果、环境变量覆盖、换档路由）。
  2. 三档结构下，每档必须自带 base_url 与 key_env —— 换档时
     model + 网关 + 密钥三者同步切换，不能只换模型名。
  3. 缺密钥的档位必须跳过，而不是发出注定 401/400 的请求。
  4. 场景链顺序要符合约定：
     performance = free_promo → performance → free_first
     free_first  = free_promo → free_first  → performance

为什么要有这个测试：注释和文档只能提醒人，「不要再切到 glm-4.5-flash」
是行为约束，必须由断言守住，否则下次改配置时很容易悄悄回退。

运行：python tests/test_llm_guard_routing.py
"""
import os
import sys
import datetime as _dt
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "scraper"))

import llm_guard as LG

EXHAUSTED = ("glm-4.7", "glm-4.7-flash", "glm-4.5-flash")
SCENARIOS = ("performance", "free_first")


def _norm_set(models):
    return {LG._norm(m) for m in models}


class TestExhaustedModelsNeverSelected(unittest.TestCase):
    """额度耗尽的模型绝不能出现在任何候选链或解析结果里。"""

    def test_not_in_any_scenario_chain(self):
        for sc in SCENARIOS:
            chain = LG.resolve_model_chain(scenario=sc)
            picked = _norm_set(chain) & _norm_set(EXHAUSTED)
            self.assertEqual(picked, set(),
                             f"{sc} 场景的候选链混入了额度耗尽模型: {picked}")

    def test_not_in_any_scenario_endpoint(self):
        for sc in SCENARIOS:
            model, _base, _src = LG.resolve_endpoint(scenario=sc)
            self.assertNotIn(LG._norm(model), _norm_set(EXHAUSTED),
                             f"{sc} 场景的解析结果命中了额度耗尽模型: {model}")

    def test_env_override_to_exhausted_ignored(self):
        """即使环境变量把手动指向 glm-4.5-flash，也必须被忽略。"""
        with patch.dict(os.environ, {"LLM_MODEL": "glm-4.5-flash"}):
            model, _base, _src = LG.resolve_endpoint(scenario="performance")
            self.assertNotIn(LG._norm(model), _norm_set(EXHAUSTED),
                             f"环境变量指向耗尽模型后仍被采用: {model}")
            chain = LG.resolve_model_chain(scenario="performance")
            self.assertEqual(_norm_set(chain) & _norm_set(EXHAUSTED), set(),
                             f"环境变量指向耗尽模型后候选链混入了: {chain}")

    def test_route_returns_none_with_exhausted_reason(self):
        """换档路由层必须直接拒绝，而不是交给 HTTP 去撞 400。"""
        cfg = LG._load_config()
        for m in EXHAUSTED:
            route, reason = LG._route_for(m, cfg=cfg,
                                          fallback_base="https://example.invalid/v1",
                                          fallback_key="dummy")
            self.assertIsNone(route, f"{m} 竟然拿到了路由: {route}")
            self.assertEqual(reason, "exhausted",
                             f"{m} 的拒绝原因应为 exhausted，实际: {reason}")

    def test_default_model_is_not_exhausted(self):
        self.assertNotIn(LG._norm(LG.DEFAULT_MODEL), _norm_set(EXHAUSTED),
                         f"DEFAULT_MODEL 指向了额度耗尽的模型: {LG.DEFAULT_MODEL}")


class TestTierStructure(unittest.TestCase):
    """三档结构完整性：每档必须自带模型标识、网关、密钥变量名。"""

    def test_three_tiers_defined_with_full_fields(self):
        tiers = LG._tier_table(LG._load_config())
        for key in ("free_promo", "performance", "free_first"):
            self.assertIn(key, tiers, f"缺少档位 {key}")
            t = tiers[key]
            self.assertTrue((t.get("model") or "").strip(), f"{key} 缺 model")
            self.assertTrue((t.get("base_url") or "").strip(),
                            f"{key} 缺 base_url —— 换档会退化成只换模型名")
            self.assertTrue((t.get("key_env") or "").strip(), f"{key} 缺 key_env")

    def test_model_ids_match_actual_api_identifiers(self):
        """配置里的模型标识必须与实际可用 API 标识逐字一致（防止写成别名）。"""
        tiers = LG._tier_table(LG._load_config())
        self.assertEqual(tiers["free_promo"]["model"], "qiniu/conatus-free")
        self.assertEqual(tiers["performance"]["model"], "deepseek/deepseek-v4.1-flash")
        self.assertEqual(tiers["free_first"]["model"], "moonshotai/kimi-k3-free")
        self.assertEqual(tiers["free_first"]["model"], "moonshotai/kimi-k3-free")

    def test_chain_order_performance(self):
        with patch.dict(os.environ, {"LLM_API_KEY_STEP": "s",
                                     "LLM_API_KEY_ORCAROUTER": "o"}):
            chain = LG.resolve_model_chain(scenario="performance")
            self.assertEqual(chain[:3],
                             ["qiniu/conatus-free", "deepseek/deepseek-v4.1-flash",
                              "moonshotai/kimi-k3-free"])

    def test_chain_order_free_first(self):
        with patch.dict(os.environ, {"LLM_API_KEY_STEP": "s",
                                     "LLM_API_KEY_ORCAROUTER": "o"}):
            chain = LG.resolve_model_chain(scenario="free_first")
            self.assertEqual(chain[:3],
                             ["qiniu/conatus-free", "moonshotai/kimi-k3-free",
                              "deepseek/deepseek-v4.1-flash"])

    def test_missing_key_tier_skipped(self):
        """缺密钥的档位不得进入候选链（否则会发出注定失败的请求）。"""
        env = {k: v for k, v in os.environ.items() if k != "LLM_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            chain = LG.resolve_model_chain(scenario="performance")
            self.assertNotIn("qiniu/conatus-free", chain, "缺密钥的 conatus 档不该入选")
            self.assertNotIn("moonshotai/kimi-k3-free", chain, "缺密钥的 kimi 档不该入选")
            self.assertNotIn("deepseek/deepseek-v4.1-flash", chain, "缺密钥的 deepseek 档不该入选")

    def test_route_uses_tier_own_base_url(self):
        """conatus-free 必须路由到 qnaigc 网关（与 kimi/deepseek 共用同一网关与密钥）。"""
        with patch.dict(os.environ, {"LLM_API_KEY": "sk-qnaigc"}):
            route, reason = LG._route_for("qiniu/conatus-free", cfg=LG._load_config())
            self.assertIsNotNone(route, f"路由失败: {reason}")
            base, key = route
            self.assertEqual(base, "https://api.qnaigc.com/v1")
            self.assertEqual(key, "sk-qnaigc")

    def test_kimi_free_route_uses_qnaigc(self):
        """kimi-k3-free 与 deepseek 共用 qnaigc 网关与密钥（2026-10-25 用户指定）。"""
        with patch.dict(os.environ, {"LLM_API_KEY": "sk-qnaigc"}):
            route, reason = LG._route_for("moonshotai/kimi-k3-free", cfg=LG._load_config())
            self.assertIsNotNone(route, f"路由失败: {reason}")
            self.assertEqual(route[0], "https://api.qnaigc.com/v1")
            self.assertEqual(route[1], "sk-qnaigc")


class TestTierExpiry(unittest.TestCase):
    """限时免费档的自动过期机制（用户 2026-10-25 指定 conatus-free 限至 2026-11-30）。"""

    def test_expired_tier_skipped(self):
        """expires 已过的档位必须自动下线，不进入候选链。"""
        cfg = LG._load_config()
        t = dict(LG._tier_table(cfg)["free_promo"])
        t["expires"] = "2026-10-24"   # 昨天
        self.assertFalse(LG._tier_available(t, cfg), "已过期的档位不该可用")
        ok, why = LG._expire_reason(t)
        self.assertTrue(ok)
        self.assertIn("2026-10-24", why)

    def test_future_expiry_still_available(self):
        """未到期的限免档位正常可用（到期当日仍可用，次日下线）。"""
        cfg = LG._load_config()
        t = dict(LG._tier_table(cfg)["free_promo"])
        t["expires"] = "2026-11-30"   # 未来
        with patch.dict(os.environ, {"LLM_API_KEY": "sk-qnaigc"}):
            self.assertTrue(LG._tier_available(t, cfg))
        ok, why = LG._expire_reason(t)
        self.assertFalse(ok)
        self.assertEqual(why, "")

    def test_expiry_today_still_available(self):
        """到期当天仍可用（语义：免费持续到当天 23:59）。"""
        import datetime as _dt
        cfg = LG._load_config()
        t = dict(LG._tier_table(cfg)["free_promo"])
        t["expires"] = _dt.date.today().isoformat()
        with patch.dict(os.environ, {"LLM_API_KEY": "sk-qnaigc"}):
            self.assertTrue(LG._tier_available(t, cfg))

    def test_real_config_expiry_is_valid_iso(self):
        """配置里的 expires 必须是合法 ISO 日期，否则过期机制静默失效。"""
        cfg = LG._load_config()
        for name, t in LG._tier_table(cfg).items():
            exp = (t.get("expires") or "").strip()
            if exp:
                try:
                    _dt.date.fromisoformat(exp)
                except ValueError:
                    self.fail(f"{name} 的 expires={exp} 不是合法 ISO 日期")

    def test_real_config_promo_is_conatus(self):
        """限免档当前必须是 conatus-free 且带 2026-11-30 到期日。"""
        cfg = LG._load_config()
        t = LG._tier_table(cfg)["free_promo"]
        self.assertEqual(t["model"], "qiniu/conatus-free")
        self.assertEqual(t.get("expires"), "2026-11-30")
        self.assertEqual(t["key_env"], "LLM_API_KEY")


class TestForbiddenStillEnforced(unittest.TestCase):
    """禁用名单不能被三档结构绕过。"""

    def test_forbidden_models_raise(self):
        for m in ("glm-5.3", "kimi-k3", "moonshotai/kimi-k3"):
            with self.assertRaises(RuntimeError, msg=f"{m} 应触发禁用名单"):
                LG._check_forbidden(m, [], "test")

    def test_free_variant_of_forbidden_model_allowed(self):
        """用户显式指定的 -free 免费变体不受完整版禁令约束（2026-10-25 指定）。"""
        for m in ("moonshotai/kimi-k3-free", "kimi-k3-free"):
            try:
                out = LG._check_forbidden(m, [], "test")
                self.assertEqual(out, m)
            except RuntimeError as ex:
                self.fail(f"{m} 是用户显式指定的免费变体，不应被拦截: {ex}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
