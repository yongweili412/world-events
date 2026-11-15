# -*- coding: utf-8 -*-
"""非事件标题硬拦的回归测试（2026-11-15 体检收口）。

背景：audit 检出 97 组重复标题 / 217 条，其中「韩联社新闻摘要」
「韩国主要报纸头条新闻」「历史上的今天」是一天新闻的目录页，
「阿富汗战争持续进行」这类是无具体事实的持续占位。
价值闸门在 off/shadow 与模型限流 PASS_ON_FAIL 时不拦，因此改在
merge_into_events（所有入库必经路径）做窄规则拦截。

规则只防新增；存量 217 条保持不动（删除需用户确认）。
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scraper"))

from scraper import is_non_event_title, merge_into_events  # noqa: E402
from verify_daily_backfill import account_conflicts  # noqa: E402


def mk(title, date="2026-11-15", url="https://example.com/a"):
    return {"title": title, "date": date, "sourceUrl": url,
            "summary": "正文", "source": "测试源"}


class TestNonEventTitle(unittest.TestCase):
    def test_blocks_digest_titles(self):
        """目录页标题整串命中 → 拦。"""
        for t in ("韩联社新闻摘要", "韩国主要报纸头条新闻",
                  "韩国历史上的今天", "今日要闻", "每日简报",
                  "新闻速览", "要闻汇总"):
            with self.subTest(title=t):
                self.assertTrue(is_non_event_title(t))

    def test_blocks_vague_ongoing_titles(self):
        """「XX持续进行」这类无具体事实的占位 → 拦。"""
        for t in ("西班牙宪法危机持续发酵", "阿富汗战争持续进行",
                  "也门内战持续", "穆勒特别检察官调查持续推进",
                  "索马里内战持续进行"):
            with self.subTest(title=t):
                self.assertTrue(is_non_event_title(t))

    def test_keeps_concrete_events(self):
        """真实事件不得误杀。"""
        for t in ("联合国通过《京都议定书》", "坦帕湾海盗队夺得超级碗冠军",
                  "微软发布Windows 98测试版", "日本儿童因《精灵宝可梦》癫痫发作",
                  "2005年1月以军开枪打死两名巴勒斯坦少年"):
            with self.subTest(title=t):
                self.assertFalse(is_non_event_title(t))

    def test_keeps_keyword_inside_concrete_title(self):
        """含『摘要/持续』等词但描述具体事件 / 不以占位词结尾 → 放行。"""
        for t in ("联合国发布气候变化摘要报告",
                  "俄乌冲突持续升级，欧盟追加制裁",
                  "政府发布新闻摘要披露新的减排目标",
                  "朝韩首脑会晤新闻发布会举行"):
            with self.subTest(title=t):
                self.assertFalse(is_non_event_title(t))

    def test_empty_title_is_not_blocked(self):
        for t in ("", None, "   "):
            with self.subTest(title=t):
                self.assertFalse(is_non_event_title(t))

    def test_merge_blocks_only_the_bad_item(self):
        """硬拦发生在 URL 去重之前：相邻同 URL 的好条目不能被连坐跳过。"""
        events = []
        items = [
            mk("韩联社新闻摘要", url="https://example.com/shared"),
            mk("联合国大会通过第78届会议决议", url="https://example.com/shared"),
        ]
        merge_into_events(events, items)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["title"], "联合国大会通过第78届会议决议")

    def test_merge_keeps_all_concrete_items(self):
        events = []
        titles = ("联合国通过《京都议定书》", "微软发布Windows 98测试版",
                  "日本儿童因《精灵宝可梦》癫痫发作")
        items = [mk(t, url=f"https://example.com/{i}") for i, t in enumerate(titles)]
        merge_into_events(events, items)
        self.assertEqual(len(events), 3)


class TestAccountConflicts(unittest.TestCase):
    def test_detects_done_and_empty_overlap(self):
        self.assertEqual(account_conflicts(["2001-01", "2002-01"], ["2001-01"]),
                         ["2001-01"])

    def test_clean_account_has_no_conflict(self):
        self.assertEqual(account_conflicts(["2002-01"], ["2001-01"]), [])


if __name__ == "__main__":
    unittest.main()
