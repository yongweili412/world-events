# -*- coding: utf-8 -*-
"""类目与档案价值口径的唯一来源（single source of truth）。

为什么单独一个文件：
  分类收敛（13 → 10）曾在 2026-10-30 修好、10-31 又漏回 13 类，根因是
  "产出侧各处各写一份类目名"。任何需要判断"这是不是一个合法类目"的地方
  （validate / normalize / build / 测试）都必须问这里，而不是各自抄一份列表。

与 scraper.py 的关系：
  scraper.py 里仍有一份 STANDARD_CATEGORIES / CATEGORY_ALIAS（抓取入口在用）。
  本文件是"契约侧"，tests/test_data_contract.py 会断言两边完全一致，
  任何一边改动而另一边没跟上，测试立刻失败——用测试锁住重复定义。
"""
from __future__ import annotations

STANDARD_CATEGORIES = (
    "自然灾害", "政治", "经济", "体育", "环境",
    "社会", "军事", "科技", "文化", "其他",
)

# 库外类目名 → 标准名。与 scraper.py:CATEGORY_ALIAS 必须逐字一致（见测试）。
CATEGORY_ALIAS = {
    "灾难": "自然灾害", "灾害": "自然灾害", "气候环境": "自然灾害",
    "人道主义": "自然灾害", "人道": "自然灾害", "事故": "自然灾害",
    "国际政治": "政治", "时政": "政治", "国际关系": "政治",
    "财经": "经济", "能源": "经济", "贸易": "经济", "产业": "经济",
    "文体": "体育",
    "司法案件": "社会", "司法": "社会", "法治": "社会", "人权": "社会",
    "卫生": "社会", "卫生疫情": "社会", "媒体": "社会", "交通": "社会", "航空": "社会",
    "冲突": "军事", "巴以冲突": "军事",
    "人工智能": "科技",
}

FALLBACK_CATEGORY = "其他"

# 档案价值分档（data-about.html 对外承诺的口径，改这里要同步改页面文案）
KEEP_LINE = 70
REVIEW_LINE = 40


def normalize_category(cat: str) -> str:
    """把任意类目名归一到 10 类标准名；无法识别的归入「其他」。"""
    c = (cat or "").strip()
    if c in STANDARD_CATEGORIES:
        return c
    return CATEGORY_ALIAS.get(c, FALLBACK_CATEGORY)


def normalize_categories(cats) -> list[str]:
    """归一一个事件的类目数组：去空、去重、保序；全空则回落「其他」。"""
    out: list[str] = []
    for c in cats or []:
        n = normalize_category(c if isinstance(c, str) else str(c))
        if n and n not in out:
            out.append(n)
    return out or [FALLBACK_CATEGORY]


def tier_of(value) -> str:
    """档案价值分 → 名义档位（仅用于展示/预警，decision 还受证据层与置信度影响）。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "UNSCORED"
    if v >= KEEP_LINE:
        return "KEEP"
    if v >= REVIEW_LINE:
        return "REVIEW"
    return "DROP"
