# -*- coding: utf-8 -*-
"""入库闸门测试：默认不影响线上 / 并入老事件的放行 / 该拦的拦住 / 模型挂了转 REVIEW。

运行：python tests/test_ingest_gate.py
（不联网、不调模型，use_llm=False 走纯规则路径，结果确定可复现）
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scraper"))

import candidate_pipeline as CP


def mk(title, summary, date="2026-09-20", cat="社会", url="https://news.example.com/a/1", source="示例媒体"):
    return {
        "id": "x1", "title": title, "date": date, "category": cat,
        "country": "", "region": "全球", "summary": summary, "content": summary,
        "source": source, "sourceUrl": url, "tags": [cat],
    }


WAR = mk("多国爆发全面武装冲突 联合国召开紧急会议",
         "俄军多路进入邻国境内，联合国召开紧急会议，欧美宣布大规模制裁，引发欧洲最大难民危机。",
         cat="军事")
CAR = mk("某汽车品牌发布新款中型SUV 售价18.98万元起",
         "新车搭载2.0T发动机，配备全景天窗与L2级辅助驾驶，即日起开启预售。", cat="经济")
# 明确垃圾（评分收紧后仍会真判 DROP 的样本：极低分 + 强商业噪声词）
JUNK = mk("某品牌耳机开启预售 售价199元 限时优惠",
          "新款蓝牙耳机上市，优惠促销，即日起开售，售价199元。", cat="其他")
VIDEO = mk("网友发布一段猫咪搞笑视频", "一名用户上传了自家猫咪的趣味短片，获得亲友转发。", cat="其他")


class TestGateModes(unittest.TestCase):
    def test_off_mode_passthrough(self):
        """默认 off：一个都不少，线上行为完全不变。"""
        items = [WAR, CAR, VIDEO]
        allowed, rep = CP.gate_items(items, events=[], mode_override="off")
        self.assertEqual(len(allowed), 3)
        self.assertEqual(rep["mode"], "off")

    def test_shadow_mode_passthrough_but_reports(self):
        """shadow：仍全部放行，但报告里要能看出会拦下什么。"""
        items = [WAR, CAR, VIDEO]
        allowed, rep = CP.gate_items(items, events=[], mode_override="shadow", use_llm=False)
        self.assertEqual(len(allowed), 3, "shadow 不得真的拦截")
        self.assertIn("shadow_note", rep)
        self.assertGreater(rep["reject"] + rep["review"], 0, "shadow 应统计出会被拦的条目")

    def test_enforce_keeps_high_value_and_blocks_noise(self):
        """enforce：明显高价值放行，明确商业广告拦下。"""
        items = [WAR, JUNK]
        allowed, rep = CP.gate_items(items, events=[], mode_override="enforce", use_llm=False)
        self.assertEqual(rep["reject"], 1, "明确商业广告（极低分+强噪声）应被丢弃")
        self.assertIn(WAR, allowed)
        self.assertNotIn(JUNK, allowed)

    def test_enforce_blocks_low_confidence_noise_as_review(self):
        """评分收紧后：中等分的商业信息落 REVIEW，仍然不新建事件（只记账等复核）。"""
        allowed, rep = CP.gate_items([CAR], events=[], mode_override="enforce", use_llm=False)
        self.assertNotIn(CAR, allowed, "未过线的商业信息不应新建事件")
        self.assertEqual(rep["review"], 1)
        self.assertEqual(rep["reject"], 0, "分数未低到硬门槛，应归 REVIEW 而非硬丢弃")

    def test_llm_failure_passes_through_in_enforce(self):
        """模型限流/故障时绝不拦数据：llm_failed 的条目在 enforce 下必须放行（可后续重放）。"""
        orig = CP.classify
        CP.classify = lambda item, use_llm=True: {
            "layer": "boundary", "eventType": "society_culture", "scores": {},
            "archiveValue": 45, "decision": "REVIEW", "reason": "模型判断不可用，转 REVIEW",
            "method": "rules+llm_fail", "llm_failed": True,
        }
        try:
            allowed, rep = CP.gate_items([WAR], events=[], mode_override="enforce", use_llm=True)
        finally:
            CP.classify = orig
        self.assertEqual(len(allowed), 1, "闸门故障绝不能变成丢数据")
        self.assertEqual(rep["llm_failed"], 1)
        self.assertIn("_value", allowed[0])


class TestUpdateOnlyPassthrough(unittest.TestCase):
    def test_existing_event_item_always_passes(self):
        """能并入已有事件的条目一律放行——它只是给老事件补来源，不新建事件。"""
        existing = [{
            "id": "evt_20260920_001", "title": WAR["title"], "date": WAR["date"],
            "category": ["军事"], "sources": [], "tags": [],
        }]
        allowed, rep = CP.gate_items([WAR], events=existing, mode_override="enforce", use_llm=False)
        self.assertEqual(len(allowed), 1)
        self.assertEqual(rep["update_only"], 1)


class TestSafeties(unittest.TestCase):
    def test_no_silent_drop_writes_ledger(self):
        """被拦下的候选要写进账本，不能静默丢失。"""
        import tempfile, os
        with tempfile.TemporaryDirectory() as td:
            orig = CP.CANDIDATE_DIR
            CP.CANDIDATE_DIR = Path(td)
            try:
                CP.gate_items([JUNK], events=[], mode_override="enforce", use_llm=False)
                files = list(Path(td).glob("*.jsonl"))
                self.assertEqual(len(files), 1, "应写出候选账本")
                self.assertIn("REJECT", files[0].read_text(encoding="utf-8"))
            finally:
                CP.CANDIDATE_DIR = orig

    def test_daily_cap(self):
        """新建数量受每日上限约束，超出的留到下轮而不是丢掉。"""
        many = [mk(f"重大国际事件第{i}号 联合国紧急会议", "多国爆发武装冲突并宣布制裁，引发大规模难民危机。",
                   url=f"https://news.example.com/a/{i}", cat="军事") for i in range(5)]
        allowed, rep = CP.gate_items(many, events=[], mode_override="enforce",
                                     daily_cap=2, use_llm=False)
        self.assertLessEqual(len(allowed), 2)
        self.assertEqual(rep["capped"], 3, "超额 3 条应标记留到下轮")

    def test_scores_attached_to_passed_item(self):
        """放行的新建候选要带上评分，供入库时写入事件。"""
        allowed, _ = CP.gate_items([WAR], events=[], mode_override="enforce", use_llm=False)
        self.assertIn("_value", allowed[0])
        self.assertIn("archiveValue", allowed[0]["_value"])
        self.assertIn("eventType", allowed[0]["_value"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
