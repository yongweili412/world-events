# -*- coding: utf-8 -*-
"""首页日期跳转的防回归测试（源码契约，不联网、不跑浏览器）。

2026-11-20 修复：首页只内嵌最近 300 条（约 3 天），原实现把日期控件的 min/max
与前后按钮绑在这批内嵌数据上，导致「最多只能往前跳 3 天」。
现在改为全库 data/dates.json 索引驱动 + 选中日期时按需加载当月分片。
"""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class TestDateNavigationSource(unittest.TestCase):
    def setUp(self):
        self.app = (ROOT / "app.js").read_text(encoding="utf-8")
        self.build = (ROOT / "scraper" / "build.py").read_text(encoding="utf-8")
        self.index = (ROOT / "index.html").read_text(encoding="utf-8")

    def test_app_loads_full_date_index(self):
        self.assertIn("data/dates.json", self.app)
        self.assertIn("getDateIndex", self.app)

    def test_app_no_longer_binds_range_to_embedded_dates(self):
        """回归点：min/max 一旦绑回内嵌日期，就又会「只能往前跳 3 天」。"""
        self.assertNotIn('min="${allDates[0]}"', self.app)
        self.assertNotIn('max="${allDates[allDates.length-1]}"', self.app)

    def test_app_loads_month_shard_on_demand(self):
        self.assertIn("loadMonthEvents", self.app)
        self.assertIn("MONTH_SHARD_URL", self.app)

    def test_app_keeps_url_date_even_if_not_embedded(self):
        """URL 直跳历史日期时不能被强制改到最近日期。"""
        self.assertIn("isValidYmd", self.app)
        self.assertNotIn("current = allDates.find(d => d >= current)", self.app)

    def test_build_injects_earliest_and_writes_dates(self):
        self.assertIn("__EARLIEST_DATE__", self.build)
        self.assertIn("build_date_index", self.build)
        self.assertIn("dates.json", self.build)

    def test_index_app_version_bumped(self):
        """改 app.js 必须 bump 版本号，否则老访客用的还是缓存里的旧逻辑。"""
        m = re.search(r"app\.js\?v=(\d+)", self.index)
        self.assertIsNotNone(m)
        self.assertGreaterEqual(int(m.group(1)), 15)


class TestDatesFile(unittest.TestCase):
    def test_dates_json_sorted_and_valid(self):
        p = ROOT / "data" / "dates.json"
        if not p.exists():
            self.skipTest("dates.json 尚未生成（build 时产出）")
        dates = json.loads(p.read_text(encoding="utf-8"))
        self.assertTrue(dates)
        self.assertEqual(dates, sorted(set(dates)), "日期索引必须升序且去重")
        for d in dates[:50] + dates[-50:]:
            self.assertRegex(d, r"^\d{4}-\d{2}-\d{2}$")


if __name__ == "__main__":
    unittest.main()
