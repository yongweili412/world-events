# -*- coding: utf-8 -*-
"""P1-5 回归测试：push 冲突时按 id 并集合并分片，禁止静默丢事件。

背景：4 条 workflow 曾用 `git merge -X ours` + `checkout --ours`，
对 JSON 分片等于整文件取本地，另一个 workflow 刚写入的事件会丢失。
本测试锁死「并集」语义，防止回退。
"""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scraper"))

from merge_shards import merge_by_id  # noqa: E402


class TestMergeById(unittest.TestCase):
    def test_union_keeps_both_sides(self):
        theirs = [{"id": "A"}, {"id": "B"}, {"id": "C"}]
        ours = [{"id": "B"}, {"id": "D"}]
        merged, st = merge_by_id(ours, theirs)
        self.assertEqual({e["id"] for e in merged}, {"A", "B", "C", "D"})
        self.assertEqual(st["added_from_ours"], 1)
        self.assertEqual(st["total"], 4)

    def test_same_id_local_wins(self):
        theirs = [{"id": "A", "v": "远端"}]
        ours = [{"id": "A", "v": "本地改过"}]
        merged, st = merge_by_id(ours, theirs)
        self.assertEqual(merged[0]["v"], "本地改过")
        self.assertEqual(st["ours_overrode"], 1)
        self.assertEqual(st["total"], 1)  # 不重复

    def test_remote_order_preserved_local_appended(self):
        theirs = [{"id": "C"}, {"id": "A"}, {"id": "B"}]
        ours = [{"id": "B", "v": 1}, {"id": "D"}]
        merged, _ = merge_by_id(ours, theirs)
        self.assertEqual([e["id"] for e in merged], ["C", "A", "B", "D"])

    def test_empty_local_keeps_all_remote(self):
        theirs = [{"id": "A"}, {"id": "B"}]
        merged, st = merge_by_id([], theirs)
        self.assertEqual(st["total"], 2)
        self.assertEqual(st["added_from_ours"], 0)

    def test_empty_remote_keeps_all_local(self):
        ours = [{"id": "A"}, {"id": "B"}, {"id": "C"}]
        merged, st = merge_by_id(ours, [])
        self.assertEqual(st["total"], 3)

    def test_sources_union_when_same_id(self):
        """enrich_sources 补的来源不能被另一条 workflow 的旧版覆盖。"""
        remote = [{"id": "A", "title": "远端标题",
                   "sources": [{"url": "https://a.com/1"}, {"url": "https://b.com/2"}]}]
        local = [{"id": "A", "title": "本地标题",
                  "sources": [{"url": "https://c.com/3"}]}]
        merged, _ = merge_by_id(local, remote)
        urls = {s["url"] for s in merged[0]["sources"]}
        self.assertEqual(urls, {"https://a.com/1", "https://b.com/2", "https://c.com/3"})
        self.assertEqual(merged[0]["title"], "本地标题")  # 其余字段本地优先

    def test_sources_dedup_by_url(self):
        remote = [{"id": "A", "sources": [{"url": "https://a.com/1"}]}]
        local = [{"id": "A", "sources": [{"url": "https://a.com/1"}]}]
        merged, _ = merge_by_id(local, remote)
        self.assertEqual(len(merged[0]["sources"]), 1, "相同 URL 应去重")

    def test_non_list_input_rejected(self):
        self.assertIsNone(merge_by_id("bad", [{"id": "A"}])[0])
        self.assertIsNone(merge_by_id([], {"not": "a list"})[0])

    def test_entries_without_id_ignored(self):
        theirs = [{"id": "A"}, {"no_id": 1}]
        ours = [{"no_id": 2}, {"id": "B"}]
        merged, st = merge_by_id(ours, theirs)
        self.assertEqual({e["id"] for e in merged}, {"A", "B"})
        self.assertEqual(st["total"], 2)

    def test_no_data_loss_on_large_set(self):
        theirs = [{"id": f"evt_{i:04d}"} for i in range(1000)]
        ours = [{"id": f"evt_{i:04d}"} for i in range(500, 1500)]
        merged, st = merge_by_id(ours, theirs)
        self.assertEqual(len(merged), 1500, "并集后应一条不少")
        self.assertEqual(st["added_from_ours"], 500)


if __name__ == "__main__":
    unittest.main(verbosity=2)
