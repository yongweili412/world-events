# -*- coding: utf-8 -*-
"""数据契约回归测试：锁住"类目只有 10 类、脏标签会被清掉、结构错误会被拦下"。

背景：2026-10-30 分类收敛 13→10，10-31 又漏回 13 类；根因是收敛只做了存量迁移，
没有拦住新写入，也没有测试盯着"库里的类目必须合法"。这组测试 + validate.py
放在 build 之前，才能让同类回归在 CI 就失败，而不是靠人过几天发现。

可以用 pytest 跑，也可以直接用 python 跑（本文件自带极简 runner）：
    python -m pytest tests/test_data_contract.py -q
    python tests/test_data_contract.py
"""
import ast
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scraper"))

import normalize_data  # noqa: E402
import regions  # noqa: E402
import sources  # noqa: E402
import taxonomy  # noqa: E402
import validate  # noqa: E402


def _event(**over):
    ev = {
        "id": "evt_20261030_001",
        "title": "测试事件",
        "summary": "摘要",
        "date": "2026-10-30",
        "category": ["政治"],
        "tags": ["美国"],
        "sources": [{"name": "Reuters", "url": "https://example.com/a"}],
        "aiSummary": "速览",
        "archiveValue": 72,
        "decision": "KEEP",
    }
    ev.update(over)
    return ev


# ---------- 类目口径只有一份 ----------

def test_taxonomy_matches_scraper_constants():
    """scraper.py 与 taxonomy.py 的类目表必须逐字一致（用 AST 读取，不 import 抓取脚本）。"""
    src = (HERE.parent / "scraper" / "scraper.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            name = getattr(node.targets[0], "id", None)
            if name in ("STANDARD_CATEGORIES", "CATEGORY_ALIAS"):
                found[name] = ast.literal_eval(node.value)
    assert found.get("STANDARD_CATEGORIES") == tuple(taxonomy.STANDARD_CATEGORIES)
    assert found.get("CATEGORY_ALIAS") == taxonomy.CATEGORY_ALIAS


def test_normalize_category_alias_and_fallback():
    assert taxonomy.normalize_category("国际政治") == "政治"
    assert taxonomy.normalize_category("冲突") == "军事"
    assert taxonomy.normalize_category("灾难") == "自然灾害"
    assert taxonomy.normalize_category("政治") == "政治"
    assert taxonomy.normalize_category("完全不存在的类目") == "其他"
    assert taxonomy.normalize_category("") == "其他"


def test_normalize_categories_dedupe_and_fallback():
    assert taxonomy.normalize_categories(["冲突", "军事", "国际政治"]) == ["军事", "政治"]
    assert taxonomy.normalize_categories([]) == ["其他"]
    assert taxonomy.normalize_categories(None) == ["其他"]


# ---------- 自愈脚本 ----------

def test_normalize_fixes_category_and_tags():
    ev = _event(category=["冲突"], tags=["ESPN", "政治", "人工智能", "美国"])
    ev["sources"] = [{"name": "ESPN", "url": "https://example.com/a"}]
    out, stats = normalize_data.normalize_events([ev])
    assert out[0]["category"] == ["军事"]
    # 来源名 / 类目名不该占标签位；主题词保留
    assert out[0]["tags"] == ["人工智能", "美国"]
    assert stats["category_fixed"] == 1
    assert stats["tags_fixed"] == 1


def test_normalize_cleans_date_and_whitespace():
    ev = _event(date="2026/1/5", title="  带空格  ")
    out, _ = normalize_data.normalize_events([ev])
    assert out[0]["date"] == "2026-01-05"
    assert out[0]["title"] == "带空格"


def test_normalize_is_idempotent():
    ev = _event(category=["国际政治"], tags=["中新网", "经济", "中东"])
    ev["sources"] = [{"name": "中新网", "url": "https://example.com/a"}]
    once, _ = normalize_data.normalize_events([ev])
    snapshot = [dict(once[0], tags=list(once[0]["tags"]))]
    twice, stats = normalize_data.normalize_events(once)
    assert twice[0]["category"] == snapshot[0]["category"]
    assert twice[0]["tags"] == snapshot[0]["tags"]
    assert stats["category_fixed"] == 0 and stats["tags_fixed"] == 0


# ---------- 校验器 ----------

def test_validate_clean_data_passes():
    errors, warnings, stats = validate.collect([_event()])
    assert errors == []
    assert stats["total"] == 1


def test_validate_flags_nonstandard_category():
    errors, _, _ = validate.collect([_event(category=["冲突"])])
    assert any("非标准类目" in e for e in errors)


def test_validate_flags_duplicate_and_bad_id():
    errors, _, _ = validate.collect([_event(), _event()])
    assert any("重复 id" in e for e in errors)
    errors, _, _ = validate.collect([_event(id="evt-bad")])
    assert any("非法/缺失 id" in e for e in errors)


def test_validate_flags_future_and_bad_date():
    errors, _, _ = validate.collect([_event(date="2099-01-01")], today=date(2026, 10, 31))
    assert any("晚于明天" in e for e in errors)
    errors, _, _ = validate.collect([_event(date="2026/10/30")], today=date(2026, 10, 31))
    assert any("date 格式非法" in e for e in errors)


def test_validate_flags_missing_sources():
    errors, _, _ = validate.collect([_event(sources=[])])
    assert any("缺 sources" in e for e in errors)
    errors, _, _ = validate.collect([_event(sources=[{"name": "x"}])])
    assert any("sources 结构缺失" in e for e in errors)


def test_validate_warns_tag_pollution_and_keep_low():
    ev = _event(tags=["Reuters", "政治"], archiveValue=30)
    _, warnings, _ = validate.collect([ev])
    joined = " ".join(warnings)
    assert "tags 混入来源名/类目名" in joined
    assert "decision=KEEP 但分值<50" in joined


def test_validate_strict_promotes_warnings():
    ev = _event(archiveValue=30)
    errors, warnings, _ = validate.collect([ev])
    assert errors == [] and warnings


# ---------- 来源 URL ----------

def test_source_url_repairs_wikipedia_month_portal():
    # 实测 2020_March 这种写法在 en.wikipedia.org 上一律 404，March_2020 才是 200
    assert (sources.canonicalize_source_url(
        "https://en.wikipedia.org/wiki/Portal:Current_events/2020_March")
        == "https://en.wikipedia.org/wiki/Portal:Current_events/March_2020")


def test_source_url_keeps_daily_portal_page():
    u = "https://en.wikipedia.org/wiki/Portal:Current_events/2020_March_12"
    assert sources.canonicalize_source_url(u) == u


def test_source_url_strips_tracking_params():
    got = sources.canonicalize_source_url(
        "https://example.com/a?utm_source=rss&utm_medium=feed&id=7&fbclid=x")
    assert got == "https://example.com/a?id=7"


def test_source_url_dedupes_within_event():
    ev = _event(sources=[
        {"name": "A", "url": "https://example.com/a?utm_source=x"},
        {"name": "B", "url": "https://example.com/a"},
        {"name": "C", "url": "https://example.com/c"},
    ])
    changed = sources.canonicalize_event_sources(ev)
    assert changed == 2                     # 1 处去参数 + 1 处重复
    assert [s["url"] for s in ev["sources"]] == [
        "https://example.com/a", "https://example.com/c"]


# ---------- 地区 ----------

def test_region_alias_and_city_country():
    loc = {"country": "", "region": "北美", "city": ""}
    assert regions.normalize_location(loc) >= 1
    assert loc["region"] == "北美洲"

    loc = {"country": "", "region": "东京", "city": ""}
    regions.normalize_location(loc)
    assert loc["country"] == "日本"

    loc = {"country": "", "region": "美国", "city": ""}
    regions.normalize_location(loc)
    assert loc["country"] == "美国"


def test_region_does_not_invent_country_for_global():
    loc = {"country": "", "region": "全球", "city": ""}
    regions.normalize_location(loc)
    assert loc["country"] == ""            # 跨国议题不强行归属


def test_region_falls_back_to_country_when_missing():
    loc = {"country": "法国", "region": "", "city": ""}
    regions.normalize_location(loc)
    assert loc["region"] == "法国"


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ✓ {name}")
            except AssertionError as exc:
                failed += 1
                print(f"  ✗ {name}: {exc}")
    print(f"\n{len([n for n in globals() if n.startswith('test_')]) - failed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
