# -*- coding: utf-8 -*-
"""事件匹配合并的回归测试（2026-11-11 数据完整性修复）。

背景：`_find_event` 第一层原本是「标题前 22 字相同即合并」，没有日期限制，
导致跨年份的不同事件被合并（实测 8.2% 多来源事件跨年份混源），
例如「坦帕湾海盗队夺得超级碗冠军」把 2003 与 2021 两次夺冠合成一条。
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scraper"))

from scraper import _find_event  # noqa: E402


def mk(date, title):
    return {"id": "x", "date": date, "title": title}


class TestFindEventDateWindow(unittest.TestCase):
    def test_same_title_different_year_not_merged(self):
        """2003 与 2021 的超级碗夺冠是不同事件，不得合并。"""
        old = mk("2003-01-26", "坦帕湾海盗队夺得超级碗冠军")
        new = mk("2021-02-07", "坦帕湾海盗队夺得超级碗冠军")
        self.assertIsNone(_find_event([old], new))

    def test_same_title_same_day_merged(self):
        old = mk("2026-03-05", "某国发生地震")
        new = mk("2026-03-05", "某国发生地震")
        self.assertIsNotNone(_find_event([old], new))

    def test_within_7_days_merged(self):
        old = mk("2026-03-05", "某地区冲突持续升级")
        new = mk("2026-03-11", "某地区冲突持续升级")
        self.assertIsNotNone(_find_event([old], new))

    def test_over_7_days_not_merged(self):
        old = mk("2026-03-05", "某地区冲突持续升级")
        new = mk("2026-03-20", "某地区冲突持续升级")
        self.assertIsNone(_find_event([old], new))

    def test_invalid_item_date_not_merged_by_prefix(self):
        old = mk("2026-03-05", "某地区冲突持续升级")
        new = mk("", "某地区冲突持续升级")
        self.assertIsNone(_find_event([old], new))

    def test_cross_year_israel_syria_case(self):
        """真实案例：2003/2014/2021 的「以色列空袭叙利亚境内目标」是不同事件。"""
        old = mk("2003-10-05", "以色列空袭叙利亚境内目标")
        new = mk("2014-03-01", "以色列空袭叙利亚境内目标")
        self.assertIsNone(_find_event([old], new))


if __name__ == "__main__":
    unittest.main(verbosity=2)
