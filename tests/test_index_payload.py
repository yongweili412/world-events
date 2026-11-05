# -*- coding: utf-8 -*-
"""P2-15 回归：index.json 只在内容真变化时写盘，不被 generatedAt 拖着天天产生 blob。"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scraper"))

from shard_events import index_payload, index_content_changed  # noqa: E402


class TestIndexContentChanged(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False,
                                              encoding="utf-8")
        self.tmp.close()
        self.path = Path(self.tmp.name)
        self.shards = {"2026-10": [{"id": "a"}] * 3, "2026-11": [{"id": "b"}]}
        self.payload = index_payload(4, self.shards, {"2026": 4}, {"2026"})

    def tearDown(self):
        try:
            os.unlink(self.path)
        except OSError:
            pass

    def _write_old(self, **overrides):
        data = {"generatedAt": "2026-11-06T01:00:00+08:00", **self.payload}
        data.update(overrides)
        self.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_missing_file_needs_write(self):
        os.unlink(self.path)
        self.assertTrue(index_content_changed(self.payload, self.path))

    def test_only_timestamp_diff_skips_write(self):
        self._write_old(generatedAt="2020-01-01T00:00:00+08:00")
        self.assertFalse(index_content_changed(self.payload, self.path),
                         "只变时间戳应跳过写入")

    def test_total_events_change_needs_write(self):
        self._write_old()
        changed = index_payload(5, self.shards, {"2026": 5}, {"2026"})
        self.assertTrue(index_content_changed(changed, self.path))

    def test_shard_counts_change_needs_write(self):
        self._write_old()
        new_shards = dict(self.shards)
        new_shards["2026-10"] = [{"id": "a"}] * 4
        changed = index_payload(5, new_shards, {"2026": 5}, {"2026"})
        self.assertTrue(index_content_changed(changed, self.path))

    def test_extra_unknown_old_key_needs_write(self):
        """旧文件多出字段（格式升级）应视为变化，避免静默丢失元数据。"""
        self._write_old()
        d = json.loads(self.path.read_text(encoding="utf-8"))
        d["legacyFlag"] = True
        self.path.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        self.assertTrue(index_content_changed(self.payload, self.path))

    def test_corrupt_file_needs_write(self):
        self.path.write_text("{broken", encoding="utf-8")
        self.assertTrue(index_content_changed(self.payload, self.path))

    def test_payload_has_no_timestamp(self):
        self.assertNotIn("generatedAt", self.payload,
                         "payload 不能含时间戳，否则比较永远不相等")


if __name__ == "__main__":
    unittest.main(verbosity=2)
