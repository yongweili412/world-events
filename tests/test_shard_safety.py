# -*- coding: utf-8 -*-
"""分片入库安全闸门的回归测试（纯函数，不碰文件、不联网）。

2026-11-17 事故：本机自动任务拿着落后 332 条的 events.json 直接 split，
把分片里已入库的早年数据覆盖回旧状态。这个闸门必须在任何写库路径前拦住它。
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scraper"))

import shard_events as SE  # noqa: E402


class TestSplitGuard(unittest.TestCase):
    def test_blocks_stale_work_copy(self):
        """工作副本 530 条、分片索引 862 条 → 必须拒绝。"""
        self.assertTrue(SE.should_block_split(530, 862))

    def test_allows_equal_or_growing(self):
        self.assertFalse(SE.should_block_split(862, 862))
        self.assertFalse(SE.should_block_split(915, 862))

    def test_small_shrink_is_allowed(self):
        """小幅波动（如去重）不该误拦。"""
        self.assertFalse(SE.should_block_split(850, 862))

    def test_explicit_override(self):
        """合法删数据场景可用 WE_ALLOW_SHRINK=1 绕过。"""
        self.assertFalse(SE.should_block_split(530, 862, allow_shrink=True))

    def test_no_index_is_not_blocked(self):
        self.assertFalse(SE.should_block_split(530, None))
        self.assertFalse(SE.should_block_split(530, "unknown"))


if __name__ == "__main__":
    unittest.main()
