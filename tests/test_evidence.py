# -*- coding: utf-8 -*-
"""证据修复相关测试：引用抢救 + 存档页判定 + 中英条目匹配。

运行：python tests/test_evidence.py
（无需联网，全部用构造样本）
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scraper"))

import event_schema as ES
import enrich_sources as EN
import backfill_daily as bd


class TestRefExtraction(unittest.TestCase):
    """backfill_daily 必须能在 clean_wiki 删掉 <ref> 之前把引用 URL 抢救出来。"""

    SAMPLE = (
        "'''Armed conflicts and attacks'''\n"
        "* Harmony Airways Flight 185 crashed near Ankara, Turkey, killing all 8 passengers "
        "including Libya's army chief.<ref>{{cite news|title=Libya army chief killed|"
        "url=https://www.reuters.com/world/africa/libya-2025-12-23/|work=Reuters}}</ref>"
        "<ref>https://www.bbc.com/news/world-africa-999</ref>\n"
        "* Israel formally recognises Somaliland as an independent state, becoming the first "
        "country to do so.<ref>{{cite web|"
        "url=https://www.aljazeera.com/news/israel-somaliland|"
        "archive-url=https://web.archive.org/xxx}}</ref>\n"
    )

    def test_refs_extracted(self):
        items = bd.parse_day(self.SAMPLE)
        self.assertGreaterEqual(len(items), 2)
        refs = items[0]["refs"]
        self.assertIn("https://www.reuters.com/world/africa/libya-2025-12-23/", refs)
        self.assertIn("https://www.bbc.com/news/world-africa-999", refs)

    def test_excludes_wikipedia_and_archive(self):
        refs = bd.parse_day(self.SAMPLE)[0]["refs"]
        self.assertFalse(any("wikipedia.org" in u for u in refs))
        self.assertFalse(any("archive.org" in u or "archive.today" in u for u in refs))

    def test_text_still_cleaned(self):
        text = bd.parse_day(self.SAMPLE)[0]["text"]
        self.assertNotIn("<ref", text)
        self.assertNotIn("cite", text.lower())
        self.assertGreater(len(text), 30)

    def test_archive_url_kept_out_of_text(self):
        """archive-url 不得进入正文，也不得被当成引用。"""
        refs = bd.parse_day(self.SAMPLE)[1]["refs"]
        self.assertEqual(refs, ["https://www.aljazeera.com/news/israel-somaliland"])

    def test_domain_of(self):
        self.assertEqual(bd.domain_of("https://www.reuters.com/x"), "reuters.com")
        self.assertEqual(bd.domain_of(""), "")


class TestArchiveUrlDetection(unittest.TestCase):
    """存档页判定：只认聚合页，不得把真实条目误判成存档页。"""

    def test_portal_is_archive(self):
        self.assertTrue(ES._is_archive_url("https://en.wikipedia.org/wiki/Portal:Current_events/2020_March"))

    def test_year_page_is_archive(self):
        self.assertTrue(ES._is_archive_url("https://en.wikipedia.org/wiki/2020"))

    def test_real_article_not_archive(self):
        # 关键回归：/wiki/20 子串匹配会把真实条目误判，必须防住
        self.assertFalse(ES._is_archive_url("https://en.wikipedia.org/wiki/2020_Beirut_explosion"))
        self.assertFalse(ES._is_archive_url("https://en.wikipedia.org/wiki/Beirut"))

    def test_independent_media_not_archive(self):
        self.assertFalse(ES._is_archive_url("https://www.reuters.com/world/x"))

    def test_evidence_upgrades_after_enrichment(self):
        before = ES.assess_evidence([{"url": "https://en.wikipedia.org/wiki/Portal:Current_events/2020_March"}])
        self.assertEqual(before[0], "weak")
        after = ES.assess_evidence([
            {"url": "https://en.wikipedia.org/wiki/Portal:Current_events/2020_March"},
            {"url": "https://www.reuters.com/world/africa/x"},
        ])
        self.assertEqual(after[0], "medium")


class TestEventBulletMatching(unittest.TestCase):
    """中文事件 ↔ 英文 bullet 的启发式匹配：宁可不补，也不补错。"""

    def test_matches_by_shared_numbers(self):
        events = [{
            "id": "evt_a",
            "title": "利比亚军长等8人在土耳其空难中遇难",
            "summary": "和谐航空185号航班在土耳其安卡拉附近坠毁，8名乘客全部遇难。",
        }]
        bullets = [{
            "text": "Harmony Airways Flight 185 crashed near Ankara, killing all 8 passengers aboard.",
            "refs": ["https://www.reuters.com/b"],
        }]
        self.assertEqual(EN.assign(events, bullets).get("evt_a"), ["https://www.reuters.com/b"])

    def test_no_blind_guess_without_signal(self):
        """没有数字/拉丁词重合时不得硬凑。"""
        events = [{"id": "evt_b", "title": "以色列承认索马里兰", "summary": "以色列宣布承认索马里兰独立。"}]
        bullets = [{"text": "Israeli PM announces recognition of Somaliland.", "refs": ["https://x.com/a"]}]
        self.assertNotIn("evt_b", EN.assign(events, bullets))

    def test_one_to_one_no_cross_match(self):
        events = [
            {"id": "e1", "title": "地震造成120人遇难", "summary": "该国发生地震，已致120人遇难。"},
            {"id": "e2", "title": "洪水导致45人失踪", "summary": "暴雨引发洪水，45人失踪。"},
        ]
        bullets = [
            {"text": "Floods kill 45 people and displace thousands.", "refs": ["https://a.com/flood"]},
            {"text": "Earthquake leaves 120 dead as rescues continue.", "refs": ["https://b.com/quake"]},
        ]
        hits = EN.assign(events, bullets)
        self.assertEqual(hits.get("e1"), ["https://b.com/quake"])
        self.assertEqual(hits.get("e2"), ["https://a.com/flood"])
        self.assertEqual(len(set(map(str, hits.values()))), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
