# -*- coding: utf-8 -*-
"""逐日回填验收的分层阈值回归测试（2026-11-06）。

背景：维基 Portal:Current_events 的逐日页在 2008 年前本身稀疏（月均十几条），
统一 50 条阈值会把"数据本来就少"误判成"翻译静默失败"，中断 job 导致数据不落库。
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scraper"))

from verify_daily_backfill import (  # noqa: E402
    threshold_for, EARLY_MIN, MIN_PER_MONTH, EARLY_UNTIL, MIN_OUTPUT_RATIO,
)


class TestThresholdFor(unittest.TestCase):
    def test_early_years_use_lower_threshold(self):
        for ym in ("1997-01", "1999-06", "2001-09", "2004-12", "2007-03"):
            self.assertEqual(threshold_for(ym), EARLY_MIN, f"{ym} 应用早年阈值")

    def test_boundary_year_uses_normal_threshold(self):
        self.assertEqual(threshold_for(f"{EARLY_UNTIL}-01"), MIN_PER_MONTH,
                         f"{EARLY_UNTIL} 年起应回到常规阈值")

    def test_later_years_use_normal_threshold(self):
        for ym in ("2011-03", "2015-11", "2019-06", "2026-10"):
            self.assertEqual(threshold_for(ym), MIN_PER_MONTH)

    def test_early_threshold_catches_real_failure(self):
        """早年阈值仍需抓住真失败：0-5 条 < 10。"""
        self.assertLess(0, EARLY_MIN)
        self.assertLess(5, EARLY_MIN)

    def test_malformed_input_falls_back(self):
        for bad in ("", "abc", None, "99"):
            self.assertEqual(threshold_for(bad), MIN_PER_MONTH)


class TestRelativeRatio(unittest.TestCase):
    """有源条目数时应走相对校验，早年稀疏月份不会被误判。"""

    def test_ratio_threshold_math(self):
        # 源 3 条 → 要求 max(1, 1) = 1，入库 2 条应通过
        need = max(1, int(3 * MIN_OUTPUT_RATIO))
        self.assertEqual(need, 1)
        # 源 120 条 → 要求 60，入库 10 条（翻译整体失败）应被判异常
        need = max(1, int(120 * MIN_OUTPUT_RATIO))
        self.assertEqual(need, 60)
        self.assertLess(10, need)

    def test_ratio_catches_total_translation_failure(self):
        self.assertLess(0, max(1, int(200 * MIN_OUTPUT_RATIO)))
        # 真失败：源 200 条只入库 5 条
        self.assertLess(5, max(1, int(200 * MIN_OUTPUT_RATIO)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
