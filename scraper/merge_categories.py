#!/usr/bin/env python3
"""
分类体系收敛（P1）：把 36 个类目合并为 10 个标准类目。

背景（docs/网站评估报告-2026-10-22.md P1-1）：用户在下拉里看到「灾害」点进去
只有 7 条——同义分裂比没有分类更伤体验。本脚本按报告映射表 + 数据实际演进
（新增「国际关系」「冲突」等报告未提及的大项）制定合并规则。

合并规则（旧 → 新）：
  灾难/自然灾害/灾害/气候环境/人道主义/人道/事故        → 自然灾害
  政治/国际政治/时政/国际关系                           → 政治
  经济/财经/能源/贸易/产业                              → 经济
  体育/文体                                            → 体育
  环境                                                 → 环境
  社会/司法案件/司法/法治/人权/卫生/卫生疫情/媒体/交通/航空 → 社会
  军事/冲突/巴以冲突                                   → 军事
  科技/人工智能                                        → 科技
  文化/历史                                            → 文化
  其他                                                 → 其他（不变）

设计要点：
  1. 幂等：只改映射表里有登记的旧名，已是标准类目的原样保留，可重复执行。
  2. dry-run 默认：不带 --apply 只打印前后分布对比，不落盘。
  3. 自动备份：--apply 前把 data/events.json 复制为 .bak-pre-catmerge（翻车可回滚）。
  4. 只动 category 字段，不动评分；评分重算由 rescore.py 负责（须随后执行）。

用法：
    python scraper/merge_categories.py            # 预览
    python scraper/merge_categories.py --apply    # 执行迁移
"""
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).parent.parent
EVENTS_FILE = ROOT / "data" / "events.json"

MERGE_MAP = {
    # 灾难族
    "灾难": "自然灾害", "自然灾害": "自然灾害", "灾害": "自然灾害",
    "气候环境": "自然灾害", "人道主义": "自然灾害", "人道": "自然灾害", "事故": "自然灾害",
    # 政治族
    "政治": "政治", "国际政治": "政治", "时政": "政治", "国际关系": "政治",
    # 经济族
    "经济": "经济", "财经": "经济", "能源": "经济", "贸易": "经济", "产业": "经济",
    # 体育族
    "体育": "体育", "文体": "体育",
    # 环境
    "环境": "环境",
    # 社会族
    "社会": "社会", "司法案件": "社会", "司法": "社会", "法治": "社会",
    "人权": "社会", "卫生": "社会", "卫生疫情": "社会", "媒体": "社会",
    "交通": "社会", "航空": "社会",
    # 军事族
    "军事": "军事", "冲突": "军事", "巴以冲突": "军事",
    # 科技族
    "科技": "科技", "人工智能": "科技",
    # 文化族
    "文化": "文化", "历史": "文化",
    # 兜底
    "其他": "其他",
}
STANDARD_CATS = ["自然灾害", "政治", "经济", "体育", "环境", "社会", "军事", "科技", "文化", "其他"]


def normalize_cats(ev):
    """返回该事件映射后的类目列表（保持原顺序、去重）。"""
    raw = ev.get("category")
    if isinstance(raw, list):
        src = [c for c in raw if c]
    elif raw:
        src = [raw]
    else:
        src = []
    out, seen = [], set()
    for c in src:
        new = MERGE_MAP.get(c, "其他")   # 未登记类目一律入「其他」，不留野类目
        if new not in seen:
            seen.add(new)
            out.append(new)
    return out or ["其他"]


def count_dist(events):
    c = Counter()
    for ev in events:
        for cat in normalize_cats(ev):
            c[cat] += 1
    return c


def main():
    apply_mode = "--apply" in sys.argv
    if not EVENTS_FILE.exists():
        print(f"找不到 {EVENTS_FILE}")
        return 1

    events = json.loads(EVENTS_FILE.read_text(encoding="utf-8"))
    before = Counter()
    for ev in events:
        raw = ev.get("category")
        if isinstance(raw, list):
            for c in raw:
                if c:
                    before[c] += 1
        elif raw:
            before[raw] += 1
    after = count_dist(events)

    print(f"=== 分类收敛（{'APPLY' if apply_mode else 'DRY-RUN 预览'}）===")
    print(f"事件总数: {len(events)}")
    print(f"\n迁移前类目数: {len(before)} → 迁移后: {len(after)}")
    print("\n--- 迁移后分布（目标 10 类）---")
    for cat in STANDARD_CATS:
        print(f"  {cat}: {after.get(cat, 0):,}")
    unknown = {k: v for k, v in before.items() if k not in MERGE_MAP}
    if unknown:
        print(f"\n⚠️ 未登记类目 {len(unknown)} 个（将归入「其他」）: "
              f"{dict(sorted(unknown.items(), key=lambda kv: -kv[1]))}")

    if not apply_mode:
        print("\n（预览模式，未写入。加 --apply 执行）")
        return 0

    backup = EVENTS_FILE.with_suffix(".json.bak-pre-catmerge")
    shutil.copy2(EVENTS_FILE, backup)
    print(f"\n已备份原文件 → {backup.name}")

    changed = 0
    for ev in events:
        new = normalize_cats(ev)
        if ev.get("category") != new:
            ev["category"] = new
            changed += 1
    tmp = EVENTS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(events, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(EVENTS_FILE)
    print(f"✅ 已写回 {EVENTS_FILE.name}（{changed} 条事件的 category 发生变化）")
    print("下一步（顺序固定）：rescore --all → split → build → 验证分布")
    return 0


if __name__ == "__main__":
    sys.exit(main())
