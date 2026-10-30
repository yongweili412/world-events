# -*- coding: utf-8 -*-
"""事件自动分类的防回归测试（纯规则，不联网）。

背景（2026-10-30 踩坑）：把旧类目名「灾难」「冲突」「国际政治」并入 10 类标准名时，
第一版做法把「军事」键提到了「自然灾害」之前，导致「地震造成伤亡」被判成军事、
「化工爆炸事故」也被判成军事——因为 CATEGORY_KEYWORDS 的**键顺序即匹配优先级**，
而两组的关键词有交叉（"伤亡""爆炸"同时出现在军事组）。

本测试锁死两件事：
  1. 类目体系只有 10 类，任何来源的旧名都被 normalize_category 收敛掉；
  2. 关键词合并后，分类结果与合并前保持一致（覆盖有交叉词的易错场景）。

运行：python tests/test_category_guess.py
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scraper"))

import scraper  # noqa: E402

STANDARD = ("自然灾害", "政治", "经济", "体育", "环境", "社会", "军事", "科技", "文化", "其他")

# (标题, 期望类目)。期望值取自合并前的实际行为，合并后必须一字不差。
CASES = [
    ("加州发生7.2级地震 已造成数百人伤亡", "自然灾害"),
    ("台风海燕登陆 沿海地区洪涝严重", "自然灾害"),
    ("某地化工厂发生爆炸事故 3人遇难", "自然灾害"),
    ("客机失事坠毁 机上人员全部身亡", "自然灾害"),
    ("以色列空袭加沙地带 造成多人死亡", "军事"),
    ("两国边境爆发武装冲突 双方互相炮击", "军事"),
    ("抗议者在首都游行要求提高最低工资", "社会"),
    ("市民持续示威 反对新建垃圾焚烧厂", "社会"),
    ("多地爆发骚乱 民众与警方对峙", "社会"),
    ("朝鲜再次试射弹道导弹 引发区域紧张", "军事"),
    ("众议院通过新法案 总统已签署生效", "政治"),
    ("日本首相访问美国 双方举行首脑会晤", "政治"),
    ("世界杯决赛阿根廷夺冠 梅西获金球奖", "体育"),
    ("全新人工智能大模型发布 参数破万亿", "科技"),
    ("央行宣布加息 通胀压力仍存", "经济"),
    ("戛纳电影节开幕 多部华语片入围", "文化"),
]


class TestCategorySystem(unittest.TestCase):
    """类目体系：只有 10 类，且词表键全部落在 10 类内。"""

    def test_keywords_keys_are_standard(self):
        bad = [k for k in scraper.CATEGORY_KEYWORDS if k not in STANDARD]
        self.assertEqual(bad, [], f"词表里仍存在非标准类目键: {bad}")

    def test_no_duplicate_keys(self):
        """合并时若两组撞名，后写的会静默覆盖前写的——键数必须与源码出现次数一致。"""
        src = (Path(__file__).parent.parent / "scraper" / "scraper.py").read_text(encoding="utf-8")
        import re
        block = re.search(r"CATEGORY_KEYWORDS\s*=\s*\{(.*?)\n\}", src, re.S).group(1)
        keys = re.findall(r'^\s{4}"([^"]+)"\s*:', block, re.M)
        self.assertEqual(len(keys), len(set(keys)), f"存在重复键，后写的会覆盖先写的: {keys}")
        self.assertEqual(len(keys), len(scraper.CATEGORY_KEYWORDS),
                         "源码键数与运行时键数不符，说明发生了覆盖")

    def test_normalize_category_converges_aliases(self):
        for alias, expect in [
            ("国际政治", "政治"), ("时政", "政治"), ("国际关系", "政治"),
            ("冲突", "军事"), ("巴以冲突", "军事"),
            ("灾难", "自然灾害"), ("灾害", "自然灾害"), ("事故", "自然灾害"),
            ("财经", "经济"), ("文体", "体育"), ("人工智能", "科技"),
            ("司法", "社会"), ("人权", "社会"), ("卫生", "社会"),
            ("政治", "政治"), ("自然灾害", "自然灾害"), ("其他", "其他"),
        ]:
            self.assertEqual(scraper.normalize_category(alias), expect,
                             f"{alias} 应归一为 {expect}")

    def test_normalize_unknown_falls_to_other(self):
        self.assertEqual(scraper.normalize_category("随便一个没登记的名字"), "其他")
        self.assertEqual(scraper.normalize_category(""), "其他")

    def test_standard_names_are_idempotent(self):
        for c in STANDARD:
            self.assertEqual(scraper.normalize_category(c), c)


class TestGuessCategoryBehavior(unittest.TestCase):
    """关键词合并后的行为必须与合并前一致（交叉词场景是重点）。"""

    def test_cases(self):
        for title, expect in CASES:
            got = scraper.guess_category(title, "")
            self.assertEqual(got, expect, f"「{title}」期望 {expect}，实际 {got}")

    def test_result_always_standard(self):
        """无论输入什么，返回值都必须落在 10 类内（不得出现库外类目名）。"""
        for title, _ in CASES:
            self.assertIn(scraper.guess_category(title, ""), STANDARD)
        self.assertIn(scraper.guess_category("完全无关的随机字符串 xyzzy", ""), STANDARD)

    def test_key_order_natural_disaster_before_military(self):
        """顺序断言：自然灾害必须先于军事匹配，否则「地震伤亡」会被判成军事。"""
        keys = list(scraper.CATEGORY_KEYWORDS.keys())
        self.assertLess(keys.index("自然灾害"), keys.index("军事"),
                        f"自然灾害必须排在军事之前，当前顺序: {keys}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
