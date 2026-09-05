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
import html as html_mod
import json
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
EVENTS_FILE = ROOT / "data" / "events.json"
TEMPLATE_FILE = ROOT / "archive_template.html"
INDEX_STATIC_COUNT = 50   # 首页静态渲染条数
INDEX_EMBED_COUNT = 300   # 首页内嵌 JSON 条数（供搜索/筛选）

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

CAT_COLOR = {
    "自然灾害": "#185FA5", "国际政治": "#534AB7", "科技": "#0C447C",
    "环境": "#3B6D11", "体育": "#BA7517", "经济": "#854F0B",
    "社会": "#A32D2D", "其他": "#5F5E5A",
}
CAT_BG = {
    "自然灾害": "#E6F1FB", "国际政治": "#EEEDFE", "科技": "#E6F1FB",
    "环境": "#EAF3DE", "体育": "#FAEEDA", "经济": "#FAEEDA",
    "社会": "#FCEBEB", "其他": "#F1EFE8",
}


def esc(s):
    """HTML 转义，防止 RSS 内容里的尖括号破坏页面结构"""
    return html_mod.escape(str(s or ""), quote=True)


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
    return (
        f'<div class="stat-card"><div class="stat-num">{len(events)}</div>'
        f'<div class="stat-label">事件总数</div></div>'
        f'<div class="stat-card"><div class="stat-num">{len(cats)}</div>'
        f'<div class="stat-label">事件分类</div></div>'
        f'<div class="stat-card"><div class="stat-num">{len(regions)}</div>'
        f'<div class="stat-label">涉及地区</div></div>'
        f'<div class="stat-card"><div class="stat-num">{n_multi}</div>'
        f'<div class="stat-label">多来源事件</div></div>'
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
        if n_src > 1:
            extra += f' <span style="color:var(--text-secondary);font-size:12px">📎 {n_src} 个来源</span>'
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
    h, _ = replace_between(h, M_START, M_END, render_month_nav(months), "index 月份导航")
    embed_latest = events_sorted[:INDEX_EMBED_COUNT]
    payload = json.dumps(embed_latest, ensure_ascii=False).replace("</", "<\\/")
    embed_snippet = (f"<script>window.__TOTAL_EVENTS__ = {len(events)}; "
                     f"window.__LATEST_DATE__ = '{latest_date}'; "
                     f"window.__EMBEDDED_EVENTS__ = {payload};</script>")
    h, _ = replace_between(h, START, END, embed_snippet, "index 内嵌数据")
    idx.write_text(h, encoding="utf-8")
    print(f"  ✅ index.html：今日({latest_date}) {len(today_events)} 个事件，"
          f"内嵌 {len(embed_latest)} 条（总 {len(events)} 事件），月份导航 {len(months)} 个月")

    # 2) archive-YYYY-MM.html：每个月一个静态归档页
    template = TEMPLATE_FILE.read_text(encoding="utf-8")
    for ym in months:
        month_events = [e for e in events_sorted if e.get("date", "")[:7] == ym]
        page = template.replace("{{MONTH_TITLE}}", f"{month_label(ym)} · 世界大事记")
        page = page.replace("{{MONTH_COUNT}}", str(len(month_events)))
        page = page.replace("{{MONTH_NAV}}", render_month_nav(months, current=ym))
        page, n1 = replace_between(page, T_START, T_END,
                                   render_timeline(month_events), f"{ym} 时间线")
        out = ROOT / f"archive-{ym}.html"
        out.write_text(page, encoding="utf-8")
        print(f"  ✅ archive-{ym}.html：{len(month_events)} 条")
    # 清理不再有数据的旧归档页
    for old in ROOT.glob("archive-*.html"):
        ym = old.stem.replace("archive-", "")
        if ym not in months:
            old.unlink()
            print(f"  🗑️ 删除过期归档页 {old.name}")

    # 3) event.html + country.html：内嵌全量数据
    payload = json.dumps(events, ensure_ascii=False).replace("</", "<\\/")
    for name, label in (("event.html", "event 内嵌数据"), ("country.html", "country 内嵌数据")):
        pg = ROOT / name
        h = pg.read_text(encoding="utf-8")
        h, _ = replace_between(h, START, END,
                               f"<script>window.__EMBEDDED_EVENTS__ = {payload};</script>", label)
        pg.write_text(h, encoding="utf-8")
        print(f"  ✅ {name} 已内嵌全量数据（{len(events)} 个事件）")

    # 4) admin.html：静态全量列表 + 内嵌数据
    adm = ROOT / "admin.html"
    h = adm.read_text(encoding="utf-8")
    h, _ = replace_between(h, A_START, A_END, render_admin_list(events), "admin 列表")
    h, _ = replace_between(h, START, END,
                           f"<script>window.__EMBEDDED_EVENTS__ = {payload};</script>",
                           "admin 内嵌数据")
    adm.write_text(h, encoding="utf-8")
    print(f"  ✅ admin.html 已静态渲染 {len(events)} 条")

    print("✨ 构建完成")


if __name__ == "__main__":
    main()
