# -*- coding: utf-8 -*-
"""早年回填解析层的离线回归测试（不联网、不调 LLM）。

覆盖两类源的解析边界：BBC 日期页的年份前缀与相对链接、
维基领域年表的 "Month Day –" 行（含 [[日期]] 形式）、wikitext 清理、
以及「只收 1997-2001 / science+sports」的范围约束。
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scraper"))

import backfill_early_years as BE  # noqa: E402


BBC_HTML = """
<html><body>
<div id="main">
<A HREF="/onthisday/hi/dates/stories/november/1/newsid_123/123.stm">1999: Super-cyclone wreaks havoc in India</A>
<A HREF="/onthisday/hi/dates/stories/november/1/newsid_456/456.stm">1990: Howe resigns over Europe policy</A>
<A HREF="/onthisday/hi/dates/stories/november/1/newsid_789/789.stm">1997: Princess Diana sparks landmines row</A>
<p>1966: Viet Cong bombs Saigon</p>
</div></body></html>
"""
BBC_PAGE = "http://news.bbc.co.uk/onthisday/hi/dates/stories/november/1/default.stm"

WIKI_TEXT = """==Events==
* [[January 17]] &ndash; Explosion of a [[Delta II]] rocket carrying a military [[GPS]] payload
* February 3 – Scientists announce the cloning of a sheep named [[Dolly (sheep)|Dolly]]
* March 1 – x
* Not an event line
* April 12 – First commercial [[DVD]] player released, see https://example.com/a/report
"""


class TestBBCParse(unittest.TestCase):
    def test_only_early_years_and_date_construction(self):
        out = BE.parse_bbc_html(BBC_HTML, BBC_PAGE, 11, 1)
        dates = sorted(c["date"] for c in out)
        self.assertEqual(dates, ["1997-11-01", "1999-11-01"])
        for c in out:
            self.assertTrue(1997 <= int(c["date"][:4]) <= 2001)
            self.assertEqual(c["kind"], "bbc")

    def test_relative_href_resolved(self):
        out = BE.parse_bbc_html(BBC_HTML, BBC_PAGE, 11, 1)
        by_year = {c["date"][:4]: c for c in out}
        self.assertIn("newsid_123", by_year["1999"]["sourceUrl"])
        self.assertTrue(by_year["1999"]["sourceUrl"].startswith("http://news.bbc.co.uk/"))

    def test_key_is_stable_and_distinct(self):
        out = BE.parse_bbc_html(BBC_HTML, BBC_PAGE, 11, 1)
        self.assertEqual(len(out), 2)
        self.assertNotEqual(out[0]["key"], out[1]["key"])
        again = BE.parse_bbc_html(BBC_HTML, BBC_PAGE, 11, 1)
        self.assertEqual([c["key"] for c in out], [c["key"] for c in again])


class TestWikiParse(unittest.TestCase):
    def test_dated_lines_and_wikitext_cleanup(self):
        out = BE.parse_wiki_wikitext(WIKI_TEXT, 1997, "1997_in_science")
        self.assertGreaterEqual(len(out), 2)
        # 第一行是 [[January 17]] 形式，日期应正确补零
        self.assertIn("1997-01-17", [c["date"] for c in out])
        titles = " ".join(c["text_en"] for c in out)
        self.assertIn("Delta II", titles)
        self.assertNotIn("[[", titles)
        self.assertNotIn("{{", titles)

    def test_too_short_or_undated_lines_skipped(self):
        out = BE.parse_wiki_wikitext(WIKI_TEXT, 1997, "1997_in_science")
        self.assertNotIn("1997-03-01", [c["date"] for c in out])
        self.assertTrue(all(len(c["text_en"]) >= 12 for c in out))

    def test_external_ref_used_as_source_when_present(self):
        out = BE.parse_wiki_wikitext(WIKI_TEXT, 1997, "1997_in_science")
        april = next((c for c in out if c["date"] == "1997-04-12"), None)
        self.assertIsNotNone(april)
        self.assertEqual(april["sourceUrl"], "https://example.com/a/report")

    def test_no_ref_falls_back_to_wiki_page(self):
        out = BE.parse_wiki_wikitext("* May 5 – A major scientific conference opens in Geneva",
                                     2000, "2000_in_science")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["sourceUrl"], "https://en.wikipedia.org/wiki/2000_in_science")


class TestScope(unittest.TestCase):
    def test_topics_exclude_music_and_television(self):
        self.assertEqual(BE.WIKI_TOPICS, ("science", "sports"))

    def test_wiki_page_names_include_in(self):
        """回归：曾把 1997_in_science 拼成 1997_science，导致 10 页全空。"""
        pages = [p for _, p in BE.wiki_pages()]
        self.assertEqual(len(pages), 10)
        self.assertIn("1997_in_science", pages)
        self.assertIn("2001_in_sports", pages)
        self.assertTrue(all("_in_" in p for p in pages))

    def test_categories_are_the_ten_standard_ones(self):
        self.assertEqual(set(BE.CATEGORIES), {
            "自然灾害", "政治", "经济", "体育", "环境", "社会",
            "军事", "科技", "文化", "其他"})

    def test_clean_wikitext_strips_links_and_templates(self):
        self.assertEqual(
            BE._clean_wikitext("A [[Delta II|rocket]] and {{cite web|x}} '''bold'''"),
            "A rocket and bold")

    def test_only_translated_keys_are_recorded(self):
        """回归：首轮把整个 pick 记账，导致翻译失败的 60 条被永久跳过。"""
        translated = [{"key": "a"}, {"key": "b"}]
        self.assertEqual(BE.keys_to_record(translated), ["a", "b"])
        self.assertEqual(BE.keys_to_record([]), [])


class TestSimpleFallback(unittest.TestCase):
    """单条兜底：极简 prompt 只取一行标题，不再要求长 JSON。"""

    class _Resp:
        def __init__(self, content, status=200):
            self.status_code = status
            self._content = content

        def json(self):
            return {"choices": [{"message": {"content": self._content}}]}

    def test_prompt_is_short_without_json_structure(self):
        p = BE.build_simple_prompt("Some English news " * 40)
        self.assertIn("只输出标题本身", p)
        self.assertNotIn("idx", p)
        self.assertNotIn("summary", p)
        self.assertNotIn('"title"', p)

    def test_parse_plain_title(self):
        self.assertEqual(BE._parse_simple(self._Resp("戴安娜王妃引发地雷争议")),
                         "戴安娜王妃引发地雷争议")

    def test_parse_strips_quotes_and_numbering(self):
        self.assertEqual(BE._parse_simple(self._Resp('"戴安娜王妃引发地雷争议"')),
                         "戴安娜王妃引发地雷争议")
        self.assertEqual(BE._parse_simple(self._Resp("1. 刚果总统卡比拉遇刺")),
                         "刚果总统卡比拉遇刺")

    def test_parse_takes_first_line_only(self):
        self.assertEqual(BE._parse_simple(self._Resp("英国首次最低工资\n解释：这是 1999 年的事件")),
                         "英国首次最低工资")

    def test_parse_rejects_bad_responses(self):
        self.assertIsNone(BE._parse_simple(None))
        self.assertIsNone(BE._parse_simple(self._Resp("标题", status=429)))
        self.assertIsNone(BE._parse_simple(self._Resp("   \n  ")))

    def test_parse_caps_length(self):
        self.assertLessEqual(len(BE._parse_simple(self._Resp("标" * 200))), 60)


if __name__ == "__main__":
    unittest.main()
