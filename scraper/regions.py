# -*- coding: utf-8 -*-
"""地区字段的确定性规范化：只做"查表能得到唯一答案"的修正。

背景（2026-10 实测）：
  · 71 个地区标签里混着国家、城市、州、大洲和法语区名（`Île-de-France`）
  · 同义分裂：北美/北美洲、南美/南美洲、台湾地区/中国台湾
  · region 与 country 经常互相矛盾（如 region=印度 而 country=日本）
  · 47% 的事件没有 country，其中 2,338 条是 region=全球（跨国议题，无单一国家）

  「用标题关键词猜国家」的实测准确率是 85.9%；限制到"只提到一个国家"也只有 91.7%。
  对档案来说 8% 的错误归属比"没有归属"更糟——地图上一个错的针是硬伤。
  所以这里**不做关键词推断**，只做确定性映射：
    · 别名归一（北美 → 北美洲）
    · 城市/州/区 → 所属国家（东京 → 日本）
    · region 本身就是国名时补 country
    · region 为空而 country 有值时，region 回落为国家名（与库内既有用法一致）

无 country 的事件不会被强行编造归属，地图页应把它们如实标成"未定位"。
"""
from __future__ import annotations

# 同一地区的不同写法 → 库内统一写法
REGION_ALIASES = {
    "北美": "北美洲",
    "北美洲": "北美洲",
    "南美": "南美洲",
    "南美洲": "南美洲",
    "台湾地区": "中国台湾",
    "中国台湾": "中国台湾",
    "Île-de-France": "法兰西岛",
    "法兰西岛": "法兰西岛",
}

# 城市 / 州 / 区 / 岛屿 → 所属国家（只收数据里实际出现过的标签）
COUNTRY_BY_REGION = {
    "东京": "日本",
    "莫斯科": "俄罗斯",
    "北京": "中国",
    "辽宁丹东": "中国",
    "布鲁塞尔": "比利时",
    "柏林": "德国",
    "巴伐利亚": "德国",
    "基辅": "乌克兰",
    "德黑兰": "伊朗",
    "平壤": "朝鲜",
    "万象": "老挝",
    "喀土穆": "苏丹",
    "西科尔多凡州": "苏丹",
    "波哥大": "哥伦比亚",
    "雷克雅未克": "冰岛",
    "第比利斯": "格鲁吉亚",
    "巴厘岛": "印度尼西亚",
    "提格雷": "埃塞俄比亚",
    "基达尔": "马里",
    "鞑靼斯坦": "俄罗斯",
    "俄亥俄州": "美国",
    "新墨西哥州": "美国",
    "华盛顿特区": "美国",
    "法兰西岛": "法国",
}

# 会直接作为 region 出现的国名（补 country 用）
COUNTRIES = {
    "中国", "美国", "俄罗斯", "乌克兰", "印度", "日本", "韩国", "朝鲜",
    "法国", "德国", "英国", "意大利", "西班牙", "葡萄牙", "希腊", "芬兰",
    "丹麦", "比利时", "奥地利", "爱尔兰", "瑞士", "塞尔维亚", "土耳其",
    "蒙古", "苏丹", "以色列", "巴勒斯坦", "澳大利亚", "加拿大", "墨西哥",
    "巴西", "越南", "泰国", "老挝", "伊朗", "哥伦比亚", "冰岛", "格鲁吉亚",
    "印度尼西亚", "埃塞俄比亚", "马里", "中国台湾",
}

# 多国/跨国议题：不应被强行归到某个国家
MULTI_COUNTRY = {"全球", "亚洲", "欧洲", "非洲", "北美洲", "南美洲", "大洋洲",
                 "中东", "东南亚", "南亚", "中亚", "西亚", "东亚", "拉美",
                 "美洲", "欧亚", "亚太", "高加索", "东欧", "欧盟",
                 "阿尔卑斯山区", "北方四岛（南千岛群岛）"}


def normalize_location(loc: dict) -> int:
    """就地规范化 location，返回改动处数。"""
    if not isinstance(loc, dict):
        return 0
    changed = 0

    region = (loc.get("region") or "").strip()
    canon = REGION_ALIASES.get(region, region)
    if canon != region:
        loc["region"] = canon
        region = canon
        changed += 1

    country = (loc.get("country") or "").strip()
    if not country:
        fill = COUNTRY_BY_REGION.get(region)
        if not fill and region in COUNTRIES:
            fill = region
        if fill:
            loc["country"] = fill
            country = fill
            changed += 1

    # region 缺失但有国家：回落成国家名（库里 region 常直接是国家名）
    if not loc.get("region") and country:
        loc["region"] = country
        changed += 1

    return changed
