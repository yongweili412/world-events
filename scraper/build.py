#!/usr/bin/env python3
"""
构建脚本：把 events.json 的数据"印"进 HTML 页面（静态预渲染）

为什么需要它：
线上用户的浏览器可能加载不出 app.js、也可能 fetch 不到 JSON（国内网络访问
境外沙箱域名时常被卡住）。本脚本做几件事：
  1. index.html：静态渲染最近 50 条 + 内嵌最近 300 条（JS 搜索增强），
     更多历史通过"按月归档"页面浏览
  2. archive-YYYY-MM.html：每个月一个静态归档页（全部该月事件）
  3. event.html：内嵌全量 JSON（详情页按 id 查找任意事件）
  4. admin.html：静态渲染全量列表 + 内嵌数据

用法（每次抓取新闻后运行一次）：
    python build.py
"""
import difflib
import html as html_mod
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
EVENTS_FILE = ROOT / "data" / "events.json"
TEMPLATE_FILE = ROOT / "archive_template.html"
DATA_DIR = ROOT / "data"
INDEX_LITE_FILE = DATA_DIR / "index-lite.json"   # 轻量索引：全库搜索/地图/存档列表用
MONTH_SHARD_DIR = DATA_DIR / "m"                 # 单月全量分片：详情页按需加载
INDEX_STATIC_COUNT = 50   # 首页静态渲染条数
INDEX_EMBED_COUNT = 300   # 首页内嵌 JSON 条数（供搜索/筛选）
ARCHIVE_PAGE_SIZE = 900    # 归档页单页事件上限：大于此值按月内分块（P1-A）
STATIC_EVENT_DIR = ROOT / "e"            # 静态详情页目录（P1-B，SEO 最后一公里）
STATIC_EVENT_MONTHS = 0                  # 静态详情页覆盖最近几个月；0 = 不限月份（覆盖全量历史高价值条目）
STATIC_EVENT_MIN_VALUE = 70              # 静态详情页的最低档案价值分（KEEP 线）
STATIC_EVENT_MAX = 12000                 # 静态详情页总量上限，防止构建失控

# index-lite 每个事件保留的字段。详情页正文/时间线/来源不在这里，
# 而是按需从 data/m/YYYY-MM.json 取——这是把详情页首屏从 114MB 降到 <1MB 的关键。
# summary 截 40 字：搜索匹配足够，同时把索引 gzip 体积从 ~5MB 压到 ~3MB（首页后台下载更轻）。
LITE_FIELDS_SUMMARY_LIMIT = 40

START = "<!-- EMBED_EVENTS_START -->"
END = "<!-- EMBED_EVENTS_END -->"
S_START = "<!-- STATS_START -->"
S_END = "<!-- STATS_END -->"
T_START = "<!-- TIMELINE_START -->"
T_END = "<!-- TIMELINE_END -->"
A_START = "<!-- ADMINLIST_START -->"
A_END = "<!-- ADMINLIST_END -->"
M_START = "<!-- MONTHNAV_START -->"
M_END = "<!-- MONTHNAV_END -->"

# 分类体系 2026-10-25 收敛为 10 类（评估报告 P1-1，迁移脚本 scraper/merge_categories.py）。
# 新增/补齐了迁移前的缺口：政治、军事、文化（原表只有 8 类且缺政治/军事/文化）。
CAT_COLOR = {
    "自然灾害": "#185FA5", "政治": "#534AB7", "科技": "#0C447C",
    "环境": "#3B6D11", "体育": "#BA7517", "经济": "#854F0B",
    "社会": "#A32D2D", "军事": "#993C1D", "文化": "#5F5E5A", "其他": "#5F5E5A",
}
CAT_BG = {
    "自然灾害": "#E6F1FB", "政治": "#EEEDFE", "科技": "#E6F1FB",
    "环境": "#EAF3DE", "体育": "#FAEEDA", "经济": "#FAEEDA",
    "社会": "#FCEBEB", "军事": "#FBE9E7", "文化": "#F1EFE8", "其他": "#F1EFE8",
}


def esc(s):
    """HTML 转义，防止 RSS 内容里的尖括号破坏页面结构"""
    return html_mod.escape(str(s or ""), quote=True)


# 统一 SVG 图标（描边风格，随字号缩放）—— 替代原先的 emoji 功能图标（P0 规则）
# 语义：多来源 / 新闻出处
ICON_SOURCES = (
    '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" '
    'style="width:12px;height:12px;vertical-align:-2px">'
    '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>'
    '<polyline points="14 2 14 8 20 8"/>'
    '<line x1="16" y1="13" x2="8" y2="13"/>'
    '<line x1="16" y1="17" x2="8" y2="17"/>'
    '<polyline points="10 9 9 9 8 9"/></svg>'
)


def cat_of(ev):
    """主分类：v2 模型 category 是数组，取第一个；兼容 v1 字符串"""
    c = ev.get("category")
    if isinstance(c, list):
        return c[0] if c else "其他"
    return c or "其他"


def region_of(ev):
    loc = ev.get("location") or {}
    return loc.get("region") or ev.get("region") or "全球"


def color(cat):
    return CAT_COLOR.get(cat, "#5F5E5A")


def bg(cat):
    return CAT_BG.get(cat, "#F1EFE8")


def sort_events(events):
    return sorted(events, key=lambda e: e.get("date", ""), reverse=True)


def render_stats(events):
    cats = {cat_of(e) for e in events}
    regions = {region_of(e) for e in events}
    n_multi = sum(1 for e in events if len(e.get("sources") or []) > 1)
    n_full = sum(1 for e in events if e.get("summaryFull") is True)
    return (
        f'<div class="stat-card"><div class="stat-num">{len(events)}</div>'
        f'<div class="stat-label">事件总数</div></div>'
        f'<div class="stat-card"><div class="stat-num">{len(cats)}</div>'
        f'<div class="stat-label">事件分类</div></div>'
        f'<div class="stat-card"><div class="stat-num">{len(regions)}</div>'
        f'<div class="stat-label">涉及地区</div></div>'
        f'<div class="stat-card"><div class="stat-num">{n_multi}</div>'
        f'<div class="stat-label">多来源事件</div></div>'
        f'<div class="stat-card"><div class="stat-num">{n_full}</div>'
        f'<div class="stat-label">全文速览</div></div>'
    )


def render_timeline(events):
    out = []
    for ev in sort_events(events):
        cat = cat_of(ev)
        region = region_of(ev)
        n_src = len(ev.get("sources") or [])
        extra = ""
        if ev.get("status") == "ongoing":
            extra += ' <span style="color:#B45309;font-size:12px">● 持续发展</span>'
        if ev.get("summaryFull") is True:
            extra += ' <span class="full-pill">全文速览</span>'
        if n_src > 1:
            extra += f' <span style="color:var(--text-secondary);font-size:12px">{ICON_SOURCES} {n_src} 个来源</span>'
        tags = "".join(
            f'<span class="timeline-tag">#{esc(t)}</span>' for t in (ev.get("tags") or [])
        )
        out.append(f'''    <a class="timeline-item" href="./event.html?id={esc(ev.get("id", ""))}">
      <div class="timeline-dot" style="background:{color(cat)};box-shadow:0 0 0 2px {color(cat)}"></div>
      <div class="timeline-date">
        <span class="timeline-category" style="background:{bg(cat)};color:{color(cat)}">{esc(cat)}</span>
        {esc(ev.get("date", ""))} · {esc(region)}{extra}
      </div>
      <div class="timeline-title">{esc(ev.get("title", ""))}</div>
      <div class="timeline-summary">{esc(ev.get("summary", ""))}</div>
      <div class="timeline-meta">{tags}</div>
    </a>''')
    return "\n".join(out)


def render_admin_list(events):
    out = []
    for ev in sort_events(events):
        cat = cat_of(ev)
        out.append(f'''    <div style="display:flex;align-items:center;gap:12px;padding:12px 0;border-bottom:1px solid var(--border);flex-wrap:wrap">
      <span style="color:var(--text-secondary);font-size:13px;min-width:90px">{esc(ev.get("date", ""))}</span>
      <span style="flex:1;font-size:14px">{esc(ev.get("title", ""))}</span>
      <span class="timeline-category" style="background:{bg(cat)};color:{color(cat)}">{esc(cat)}</span>
      <button onclick="deleteEvent('{esc(ev.get("id", ""))}')" class="btn btn-danger" style="font-size:12px;padding:4px 10px">删除</button>
    </div>''')
    return "\n".join(out)


def replace_between(html_text, start, end, replacement, label):
    pattern = re.escape(start) + r".*?" + re.escape(end)
    new_html, n = re.subn(pattern, lambda _m: start + "\n" + replacement + "\n" + end,
                          html_text, flags=re.S)
    if n == 0:
        print(f"  ⚠️ {label} 标记缺失，跳过")
    return new_html, n


def month_label(ym):
    y, m = ym.split("-")
    return f"{y}年{int(m)}月"


def render_month_nav(months, current=None):
    links = []
    for ym in months:
        cls = ' class="current"' if ym == current else ""
        links.append(f'<a href="./archive-{ym}.html"{cls}>{month_label(ym)}</a>')
    links.append('<a href="./index.html">最新事件</a>')
    return "\n".join(links)


def render_month_nav_grouped(months, current=None, recent=12):
    """首页归档导航：最近 N 个月平铺，更早的按年份折叠（<details>，无 JS 依赖）。

    为什么：1997-2026 共 350+ 个月份链接一次性平铺在首页，视觉噪音太大，
    会把核心内容（时间线）推到很深的位置。折叠后首屏只看到 12 个最近月份。
    """
    recent_months = months[-recent:]
    older = months[:-recent]
    parts = ['<div class="archive-head">',
             '<h2>浏览全部档案</h2>',
             f'<span class="archive-range">{month_label(months[0])} – '
             f'{month_label(months[-1])} · 共 {len(months)} 个月</span>',
             '</div>',
             '<div class="month-nav month-nav-recent">']
    for ym in recent_months:
        cls = ' class="current"' if ym == current else ""
        parts.append(f'<a href="./archive-{ym}.html"{cls}>{month_label(ym)}</a>')
    parts.append('</div>')
    if older:
        by_year = {}
        for ym in older:
            by_year.setdefault(ym[:4], []).append(ym)
        parts.append('<div class="year-fold-list">')
        for y in sorted(by_year, reverse=True):
            yms = by_year[y]
            inner = "".join(
                f'<a href="./archive-{ym}.html">{month_label(ym)}</a>' for ym in yms
            )
            parts.append(
                f'<details class="year-fold"><summary><span>{y} 年</span>'
                f'<span class="year-count">{len(yms)} 个月</span></summary>'
                f'<div class="month-nav">{inner}</div></details>'
            )
        parts.append('</div>')
    return "\n".join(parts)


def build_index_lite(events):
    """生成 data/index-lite.json —— 全库轻量索引。

    只保留列表/搜索/地图/存档渲染需要的字段，正文、时间线、完整来源不进索引。
    体积约为全量数据的 1/10（gzip 后约 2-3MB），可一次加载常驻内存做全库搜索。
    """
    lite = []
    for ev in events:
        summary = (ev.get("summary") or "")[:LITE_FIELDS_SUMMARY_LIMIT]
        lite.append({
            "id": ev.get("id", ""),
            "legacyIds": ev.get("legacyIds") or [],
            "date": ev.get("date", ""),
            "title": ev.get("title", ""),
            "summary": summary,
            "category": cat_of(ev),
            "region": region_of(ev),
            "country": (ev.get("location") or {}).get("country") or ev.get("country") or "",
            "tags": (ev.get("tags") or [])[:6],
            "image": (ev.get("image") or "") if str(ev.get("image") or "").startswith("http") else "",
            "status": ev.get("status", ""),
            "summaryFull": bool(ev.get("summaryFull")),
            "srcCount": len(ev.get("sources") or []),
        })
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_LITE_FILE.write_text(
        json.dumps(lite, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    size_mb = os.path.getsize(INDEX_LITE_FILE) / 1024 / 1024
    print(f"  ✅ data/index-lite.json 已生成（{len(lite)} 条，{size_mb:.1f}MB 原始）")
    return lite


def build_month_shards(events_sorted):
    """生成 data/m/YYYY-MM.json —— 单月全量分片，详情页按需只取一个月的文件。"""
    MONTH_SHARD_DIR.mkdir(parents=True, exist_ok=True)
    by_month = {}
    for ev in events_sorted:
        ym = (ev.get("date") or "")[:7]
        if ym:
            by_month.setdefault(ym, []).append(ev)
    written, total_bytes = 0, 0
    for ym, rows in by_month.items():
        out = MONTH_SHARD_DIR / f"{ym}.json"
        out.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        total_bytes += out.stat().st_size
        written += 1
    # 清理不再有数据的月份分片（例如事件被合并到别的月份）
    for old in MONTH_SHARD_DIR.glob("*.json"):
        if old.stem not in by_month:
            old.unlink()
    avg_kb = (total_bytes / written / 1024) if written else 0
    print(f"  ✅ data/m/ 已生成 {written} 个月度分片（平均 {avg_kb:.0f}KB/月）")
    return by_month


def render_data_stats(events, months):
    """填充 data-about.html：年份分布（纯 CSS 条形）、总览指标、主要来源 TOP 20。

    坦率展示 1997-2019 的稀疏——档案站的立身之本是可信度，包括承认局限。
    """
    total = len(events)
    by_year = {}
    src_counter = {}
    n_single = n_full = n_img = 0
    for ev in events:
        y = (ev.get("date") or "")[:4]
        if y:
            by_year[y] = by_year.get(y, 0) + 1
        n_src = len(ev.get("sources") or [])
        if n_src == 0:
            n_single += 1
        elif n_src == 1:
            n_single += 1
        if n_src > 1:
            for src in (ev.get("sources") or [])[:3]:
                name = (src.get("name") or src.get("url") or "").strip()
                if name:
                    src_counter[name] = src_counter.get(name, 0) + 1
        if ev.get("summaryFull") is True:
            n_full += 1
        if str(ev.get("image") or "").startswith("http"):
            n_img += 1
    max_year = max(by_year.values()) if by_year else 1

    bars = []
    for y in sorted(by_year):
        n = by_year[y]
        w = max(2, round(n / max_year * 100))
        bars.append(
            f'<div style="display:flex;align-items:center;gap:10px;margin:3px 0">'
            f'<span style="min-width:52px;font-size:13px;color:var(--text-secondary)">{y}</span>'
            f'<div style="flex:1;background:var(--border);border-radius:4px;overflow:hidden">'
            f'<div style="width:{w}%;background:#185FA5;height:16px"></div></div>'
            f'<span style="min-width:60px;text-align:right;font-size:13px">{n:,}</span></div>'
        )
    year_html = "<br>".join(bars)

    top_src = sorted(src_counter.items(), key=lambda kv: -kv[1])[:20]
    src_html = "<br>".join(
        f'<span style="display:inline-block;margin:2px 6px;padding:2px 10px;'
        f'background:var(--bg);border-radius:12px;font-size:13px">{esc(n)}（{c} 次）</span>'
        for n, c in top_src
    )

    pct = lambda a: f"{a / total * 100:.1f}%" if total else "-"
    stats = (
        '<div class="stat-row" style="display:flex;flex-wrap:wrap;gap:12px;margin:16px 0">'
        + "".join(
            f'<div class="stat-card"><div class="stat-num">{v}</div><div class="stat-label">{k}</div></div>'
            for k, v in [
                ("事件总数", f"{total:,}"), ("覆盖年份", f"{len(by_year)} 年"),
                ("单来源占比", pct(n_single)), ("全文速览", pct(n_full)),
                ("含封面图", pct(n_img)),
            ]
        ) + "</div>"
        + '<h2>年份分布（1997 → 今）</h2>'
        + '<p style="color:var(--text-secondary);font-size:14px">'
        + '2020 年起为自动化管道持续收集；更早年份来自历史资料回填，条数稀疏是如实呈现。</p>'
        + f'<div style="max-width:720px">{year_html}</div>'
        + '<h2 style="margin-top:28px">主要来源（按引用次数 TOP 20）</h2>'
        + f'<div>{src_html}</div>'
    )
    return stats


def build_rss(events_sorted, latest_date):
    """生成 feed.xml —— 最近 50 条事件的 RSS 订阅源（纯静态，无需后端）。

    档案站的价值在于"能被复用"：RSS 让研究者和聚合器能订阅更新，
    也是最便宜的外部分发出口。
    """
    site = "https://yongweili412.github.io/world-events/"
    items = []
    for ev in events_sorted[:50]:
        url = f"{site}event.html?id={esc(ev.get('id', ''))}"
        cat = cat_of(ev)
        desc_parts = [esc((ev.get("summary") or ev.get("title") or "")[:200])]
        for src in (ev.get("sources") or [])[:3]:
            u = esc(src.get("url") or "")
            if u:
                desc_parts.append(f'<a href="{u}">来源：{esc(src.get("title") or u)}</a>')
        items.append(
            f"    <item>"
            f"<title>{esc(ev.get('title', ''))}</title>"
            f"<link>{url}</link>"
            f"<guid isPermaLink=\"false\">{esc(ev.get('id', ''))}</guid>"
            f"<pubDate>{esc(ev.get('date', ''))}</pubDate>"
            f"<category>{esc(cat)}</category>"
            f"<description><![CDATA[ {'<br>'.join(desc_parts)} ]]></description>"
            f"</item>"
        )
    rss = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">\n'
        '  <channel>\n'
        '    <title>全球大事记</title>\n'
        f'    <link>{site}</link>\n'
        '    <description>世界事件永久存档：自动收集全球大事，每条含来源、时间线与地区标注（1997-2026）</description>\n'
        '    <language>zh-CN</language>\n'
        f'    <lastBuildDate>{esc(latest_date)}</lastBuildDate>\n'
        f'    <atom:link href="{site}feed.xml" rel="self" type="application/rss+xml"/>\n'
        + "\n".join(items) + "\n  </channel>\n</rss>\n"
    )
    (ROOT / "feed.xml").write_text(rss, encoding="utf-8")
    print(f"  ✅ feed.xml 已生成（最近 {min(50, len(events_sorted))} 条 RSS）")


def archive_page_name(ym: str, page_idx: int) -> str:
    """归档分块页文件名：第 1 块沿用原命名（外链不失效），后续为 -p2 / -p3。"""
    return f"archive-{ym}.html" if page_idx <= 1 else f"archive-{ym}-p{page_idx}.html"


def build_archive_pages(events_sorted, months, template):
    """生成月归档页；单月超过 ARCHIVE_PAGE_SIZE 条时按月内分块（P1-A）。

    分块原因：2026-09 单月 3,260 条挤在一页 = 3.2MB，比它包含的详情页还重，
    移动端首屏无法接受。每块独立 URL + canonical，既可被搜索引擎收录，
    也让"回首页/上下月/块间跳转"三套导航仍然可用。
    """
    pages = []
    for i, ym in enumerate(months):
        month_events = [e for e in events_sorted if e.get("date", "")[:7] == ym]
        y, m = ym.split("-")
        prev_ym = months[i - 1] if i > 0 else ""
        next_ym = months[i + 1] if i + 1 < len(months) else ""
        total = len(month_events)
        n_pages = max(1, (total + ARCHIVE_PAGE_SIZE - 1) // ARCHIVE_PAGE_SIZE)
        nav = render_month_nav(months, current=ym)

        for pi in range(1, n_pages + 1):
            lo = (pi - 1) * ARCHIVE_PAGE_SIZE
            chunk = month_events[lo:lo + ARCHIVE_PAGE_SIZE]
            pn = []
            if prev_ym:
                pn.append(f'<a href="./{archive_page_name(prev_ym, 1)}" '
                          f'style="color:var(--primary);text-decoration:none">← {month_label(prev_ym)}</a>')
            if pi > 1:
                pn.append(f'<a href="./{archive_page_name(ym, pi - 1)}" '
                          f'style="color:var(--primary);text-decoration:none">‹ 上一页</a>')
            if n_pages > 1:
                pn.append(f'<span style="color:var(--text-secondary);margin:0 10px">'
                          f'第 {pi}/{n_pages} 页 · 本页 {lo + 1}–{lo + len(chunk)} 条（共 {total} 条）</span>')
            else:
                pn.append(f'<span style="color:var(--text-secondary);margin:0 10px">{total} 条事件</span>')
            if pi < n_pages:
                pn.append(f'<a href="./{archive_page_name(ym, pi + 1)}" '
                          f'style="color:var(--primary);text-decoration:none">下一页 ›</a>')
            if next_ym:
                pn.append(f'<a href="./{archive_page_name(next_ym, 1)}" '
                          f'style="color:var(--primary);text-decoration:none">{month_label(next_ym)} →</a>')

            title = f"{month_label(ym)} · 世界大事记"
            tail = ""
            if n_pages > 1:
                title += f"（第 {pi}/{n_pages} 页）"
                tail = f" › 第 {pi} 页"
            desc = (f"{month_label(ym)}全球大事记，共 {total} 条事件"
                    + (f"，本页第 {lo + 1}–{lo + len(chunk)} 条" if n_pages > 1 else "")
                    + "，每条含来源链接、时间线与地区标注，永久存档。")

            page = template.replace("{{MONTH_TITLE}}", title)
            page = page.replace("{{MONTH_LABEL_CN}}", month_label(ym))
            page = page.replace("{{MONTH_YEAR}}", y)
            page = page.replace("{{MONTH_YM}}", ym)
            page = page.replace("{{MONTH_DESC}}", desc)
            page = page.replace("{{MONTH_COUNT}}", str(total))
            page = page.replace("{{MONTH_SUBTITLE}}",
                                f"月度归档 · 共 {total} 条 · 自动收集 · 永久存档")
            page = page.replace("{{PAGE_TAIL}}", tail)
            page = page.replace("{{CANONICAL_PATH}}", archive_page_name(ym, pi))
            page = page.replace("{{MONTH_PREV_NEXT}}", " ".join(pn))
            page = page.replace("{{MONTH_NAV}}", nav)
            page, _n = replace_between(page, T_START, T_END,
                                       render_timeline(chunk), f"{ym} p{pi} 时间线")
            name = archive_page_name(ym, pi)
            (ROOT / name).write_text(page, encoding="utf-8")
            pages.append(name)
        if n_pages > 1:
            print(f"  ✅ archive-{ym}.html 分 {n_pages} 页：{total} 条（单页上限 {ARCHIVE_PAGE_SIZE}）")
        else:
            print(f"  ✅ archive-{ym}.html：{total} 条")

    # 清理不再有数据的旧归档页（含分块页）
    keep = set(pages)
    for old in ROOT.glob("archive-*.html"):
        if old.name not in keep:
            old.unlink()
            print(f"  删除过期归档页 {old.name}")
    return pages


def build_static_event_pages(events_sorted, months):
    """为近期高价值事件生成静态详情页 e/evt_xxx.html（P1-B）。

    必要性：动态详情页 event.html?id= 靠 JS 运行时注入 title/OG/正文，
    不跑 JS 的爬虫（百度、微信）只能看到空壳；且 sitemap 里没有详情页入口，
    5 万条内容对搜索引擎基本不可见。

    范围控制：archiveValue 达 KEEP 线的事件均可获得静态页；
    STATIC_EVENT_MONTHS > 0 时才额外限定「最近 N 个月」（0 = 不限月份，覆盖全量历史）。
    总量封顶 STATIC_EVENT_MAX——历史低价值条目不值得给静态页。
    """
    if not months:
        return []
    recent = set(months[-STATIC_EVENT_MONTHS:]) if STATIC_EVENT_MONTHS > 0 else None
    picked = []
    for ev in events_sorted:
        if recent is not None and (ev.get("date") or "")[:7] not in recent:
            continue
        if not ev.get("id"):
            continue
        if (ev.get("archiveValue") or 0) < STATIC_EVENT_MIN_VALUE:
            continue
        picked.append(ev)
        if len(picked) >= STATIC_EVENT_MAX:
            break

    STATIC_EVENT_DIR.mkdir(parents=True, exist_ok=True)
    wrote = []
    for ev in picked:
        eid = ev.get("id")
        (STATIC_EVENT_DIR / f"{eid}.html").write_text(
            _render_static_event(ev), encoding="utf-8")
        wrote.append(eid)
    # 清理已不在范围内的旧静态页
    keep_ids = set(wrote)
    for old in STATIC_EVENT_DIR.glob("evt_*.html"):
        if old.stem not in keep_ids:
            old.unlink()
    scope = f"最近 {STATIC_EVENT_MONTHS} 个月" if STATIC_EVENT_MONTHS > 0 else "全量历史"
    print(f"  ✅ e/ 静态详情页 {len(wrote)} 条（{scope}、"
          f"档案价值 ≥ {STATIC_EVENT_MIN_VALUE}）")
    return [f"e/{i}.html" for i in wrote]


# 地区排序表，与 app.js 的 REGION_ORDER 一致（首页下拉框选项由本文件预置，顺序须吻合）
REGION_ORDER = ['中国', '美国', '俄罗斯', '乌克兰', '欧盟', '印度', '东南亚', '中东', '苏丹',
                '亚洲', '欧洲', '非洲', '北美洲', '南美洲', '大洋洲', '全球']


def _sorted_regions(regions):
    """按 REGION_ORDER 排序，未登记的地区排在后面（保持与前端一致的展示顺序）。"""
    return sorted(regions, key=lambda r: REGION_ORDER.index(r) if r in REGION_ORDER else 999)


def _is_restatement(summary: str, desc: str) -> bool:
    """速览是否只是正文的复述（全库约 93% 属此类，重复渲染等于让用户读两遍）。

    用前 60 字的字符级相似度判定，而非 startswith——改写过的复述（同义替换、
    语序调整）也能识别出来。
    """
    if not summary or not desc:
        return False
    a, b = summary[:60], desc[:60]
    if len(a) < 8:
        return False
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.75


def _render_static_event(ev) -> str:
    """单条事件的静态详情页 HTML：标题/摘要/正文/时间线/来源全预渲染。"""
    eid = ev.get("id", "")
    title = (ev.get("title") or "未命名事件").strip()
    date = ev.get("date") or ""
    cat = cat_of(ev)
    region = region_of(ev)
    summary = (ev.get("summary") or "").strip()
    desc = (ev.get("description") or "").strip()
    # 速览常是对正文的复述（全库约 93% 如此），两段几乎一样时只保留更完整的正文
    if summary and _is_restatement(summary, desc):
        summary = ""
    loc = ev.get("location") or {}
    place = loc.get("country") or loc.get("region") or ""
    meta_desc = (summary or desc or title)[:150].replace("\n", " ").strip()

    tags = ev.get("tags") or []
    tag_html = "".join(
        f'<span class="tag">{esc(t)}</span>' for t in tags[:8]
    )
    timeline = ev.get("timeline") or []
    tl_html = ""
    rows = []
    for t in timeline[:20]:
        raw = (t.get("text") or t.get("title") or t.get("description") or "").strip()
        # timeline.text 形如「来源｜来源｜标题：描述」，去掉重复来源前缀只留正文
        if "｜" in raw:
            raw = raw.split("｜")[-1].strip()
        if raw:
            rows.append((t.get("date") or "", raw))
    if rows:
        items = "".join(f"<li><strong>{esc(d)}</strong> {esc(x)}</li>" for d, x in rows)
        tl_html = f'<h2 class="section-title">事件脉络</h2><ul class="tl">{items}</ul>'

    srcs = ev.get("sources") or []
    src_html = ""
    if srcs:
        lis = "".join(
            f'<li><a href="{esc(s.get("url") or "#")}" target="_blank" rel="noopener">'
            f'{esc(s.get("name") or s.get("url") or "来源")}</a></li>'
            for s in srcs[:10])
        src_html = f'<h2 class="section-title">信息来源（{len(srcs)}）</h2><ul class="src">{lis}</ul>'

    body = desc.replace("\n", "<br>") if desc else ""
    val = ev.get("archiveValue")
    val_html = (f'<span class="badge">档案价值 {val}</span>' if isinstance(val, (int, float)) else "")

    return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{esc(title)} · 世界事件存档</title>
<meta name="description" content="{esc(meta_desc)}">
<link rel="canonical" href="https://yongweili412.github.io/world-events/e/{esc(eid)}.html">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(meta_desc)}">
<meta property="og:type" content="article">
<meta property="article:published_time" content="{esc(date)}">
<link rel="stylesheet" href="../style.css">
</head>
<body>
<div class="container">
  <div class="page-header">
    <p style="margin:0 0 8px;font-size:14px;color:var(--text-secondary)">
      <a href="../index.html" style="color:var(--primary);text-decoration:none">首页</a>
      › <a href="../{archive_page_name((date or "")[:7], 1)}" style="color:var(--primary);text-decoration:none">{esc((date or "")[:7])}</a>
      › {esc(cat)}
    </p>
    <h1 class="page-title">{esc(title)}</h1>
    <p class="page-subtitle">{esc(date)}{(" · " + esc(place)) if place else ""} · {esc(cat)} · {esc(region)} {val_html}</p>
    <p style="margin:8px 0 0">{tag_html}</p>
  </div>
  {("<p class=" + chr(34) + "summary" + chr(34) + ">" + esc(summary) + "</p>") if summary else ""}
  {("<p class=" + chr(34) + "body" + chr(34) + ">" + body + "</p>") if body else ""}
  {tl_html}
  {src_html}
  <p style="text-align:center;margin:24px 0">
    <a href="../event.html?id={esc(eid)}" style="color:var(--primary);text-decoration:none">查看完整档案（含相关事件）</a>
    · <a href="../index.html" style="color:var(--primary);text-decoration:none">返回首页</a>
  </p>
</div>
</body>
</html>'''


def build_robots_and_sitemap(months, latest_date, extra_urls=None):
    """生成 robots.txt 与 sitemap.xml —— 让搜索引擎能真正抓到这个站。"""
    site = "https://yongweili412.github.io/world-events/"
    robots = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /admin.html\n"
        f"Sitemap: {site}sitemap.xml\n"
    )
    (ROOT / "robots.txt").write_text(robots, encoding="utf-8")

    urls = [f"{site}", f"{site}map.html", f"{site}country.html", f"{site}data-about.html"]
    urls += [f"{site}archive-{ym}.html" for ym in months]
    urls += [f"{site}{u}" for u in (extra_urls or [])]
    # 最近 12 个月按事件生成详情页链接，帮助爬虫发现详情内容
    body = ['<?xml version="1.0" encoding="UTF-8"?>',
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in urls:
        lastmod = latest_date or ""
        body.append(f"  <url><loc>{u}</loc>" + (f"<lastmod>{lastmod}</lastmod>" if lastmod else "") + "</url>")
    body.append("</urlset>")
    (ROOT / "sitemap.xml").write_text("\n".join(body), encoding="utf-8")
    print(f"  ✅ robots.txt + sitemap.xml 已生成（{len(urls)} 个页面入口）")


def main():
    with open(EVENTS_FILE, "r", encoding="utf-8") as f:
        events = json.load(f)
    events_sorted = sort_events(events)

    months = sorted({e.get("date", "")[:7] for e in events if e.get("date")})

    # 1) index.html：最新日期的事件静态渲染（今日世界） + 最近 300 条内嵌 + 月份导航
    idx = ROOT / "index.html"
    h = idx.read_text(encoding="utf-8")
    latest_date = events_sorted[0].get("date", "") if events_sorted else ""
    today_events = [e for e in events if e.get("date") == latest_date]
    h, _ = replace_between(h, S_START, S_END, render_stats(events), "index 统计")
    h, _ = replace_between(h, T_START, T_END,
                           render_timeline(today_events),
                           "index 今日事件")
    h, _ = replace_between(h, M_START, M_END, render_month_nav_grouped(months), "index 归档导航")
    embed_latest = events_sorted[:INDEX_EMBED_COUNT]
    payload = json.dumps(embed_latest, ensure_ascii=False).replace("</", "<\\/")
    # 分类/地区下拉选项预置进页面（P1-C）：首页不必为了填两个下拉框而下载 20MB 索引，
    # 索引改为用户真正搜索时才加载。地区顺序与 app.js 的 REGION_ORDER 保持一致。
    cats_all = sorted({cat_of(e) for e in events})
    regions_all = _sorted_regions({region_of(e) for e in events})
    embed_snippet = (f"<script>window.__TOTAL_EVENTS__ = {len(events)}; "
                     f"window.__LATEST_DATE__ = '{latest_date}'; "
                     f"window.__CATS__ = {json.dumps(cats_all, ensure_ascii=False)}; "
                     f"window.__REGIONS__ = {json.dumps(regions_all, ensure_ascii=False)}; "
                     f"window.__EMBEDDED_EVENTS__ = {payload};</script>")
    h, _ = replace_between(h, START, END, embed_snippet, "index 内嵌数据")
    idx.write_text(h, encoding="utf-8")
    print(f"  ✅ index.html：今日({latest_date}) {len(today_events)} 个事件，"
          f"内嵌 {len(embed_latest)} 条（总 {len(events)} 事件），归档导航 {len(months)} 个月（折叠式）")

    # 2) archive-YYYY-MM.html：月归档页（超过 900 条自动分块，见 build_archive_pages）
    #    每页带：meta description（搜索摘要）+ 面包屑 + 上/下月 + 块间跳转 + canonical
    #    —— 此前 354 个归档页只有"回首页"一个出口，搜索引擎也拿不到页面摘要；
    #    分块则解决大月单页 3.2MB 的问题（2026-09 有 3,260 条）。
    template = TEMPLATE_FILE.read_text(encoding="utf-8")
    archive_pages = build_archive_pages(events_sorted, months, template)

    # 3) 轻量索引 + 月度分片 —— 替代原先 114MB 的 data/events.js
    #    详情页只加载一个月（约 200KB-3MB），全库搜索用 index-lite（约 2-3MB gzip）
    lite = build_index_lite(events)
    build_month_shards(events_sorted)

    # 4) 各页面清空数据引用块：数据改为运行时按需 fetch（不再整包脚本注入）
    for name in ("event.html", "country.html", "map.html"):
        pg = ROOT / name
        h = pg.read_text(encoding="utf-8")
        h, _ = replace_between(h, START, END, "", f"{name} 数据引用")
        pg.write_text(h, encoding="utf-8")
        print(f"  ✅ {name} 已移除全量数据引用（改为按需加载）")

    # 5) admin.html：不再静态渲染全量列表（原 28.5MB），改为运行时从 index-lite 加载
    adm = ROOT / "admin.html"
    h = adm.read_text(encoding="utf-8")
    h, _ = replace_between(h, A_START, A_END, "", "admin 列表")
    h, _ = replace_between(h, START, END, "", "admin 数据引用")
    adm.write_text(h, encoding="utf-8")
    print(f"  ✅ admin.html 已瘦身（列表改为运行时加载）")

    # 5.5) 静态详情页 e/evt_xxx.html：让不跑 JS 的爬虫也能读到正文（P1-B）
    static_event_urls = build_static_event_pages(events_sorted, months)

    # 6) robots.txt + sitemap.xml：让搜索引擎能抓到这个站（含归档分块页与静态详情页）
    build_robots_and_sitemap(months, latest_date, extra_urls=static_event_urls)

    # 7) feed.xml：RSS 订阅源（最近 50 条），研究者和聚合器的复用出口
    build_rss(events_sorted, latest_date)

    # 8) data-about.html：数据说明页（年份分布/判定标准/主要来源/已知局限）
    about = ROOT / "data-about.html"
    if about.exists():
        h = about.read_text(encoding="utf-8")
        h, _ = replace_between(h, "<!-- DATASTATS_START -->", "<!-- DATASTATS_END -->",
                               render_data_stats(events, months), "数据说明统计")
        about.write_text(h, encoding="utf-8")
        print("  ✅ data-about.html 统计已填充")

    print("✨ 构建完成")


if __name__ == "__main__":
    main()
