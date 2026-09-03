#!/usr/bin/env python3
"""
构建脚本：把 events.json 的数据"印"进 HTML 页面（静态预渲染）

为什么需要它：
线上用户的浏览器可能加载不出 app.js、也可能 fetch 不到 JSON（国内网络访问
境外沙箱域名时常被卡住）。本脚本做两件事：
  1. 把事件列表直接渲染成静态 HTML 塞进页面 → 即使 JS 完全加载不了，内容照样显示
  2. 同时内嵌一份 JSON 数据 → JS 加载成功时用它实现搜索/筛选等增强功能

用法（每次抓取新闻后运行一次）：
    python build.py
"""
import html as html_mod
import json
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
EVENTS_FILE = ROOT / "data" / "events.json"

START = "<!-- EMBED_EVENTS_START -->"
END = "<!-- EMBED_EVENTS_END -->"
S_START = "<!-- STATS_START -->"
S_END = "<!-- STATS_END -->"
T_START = "<!-- TIMELINE_START -->"
T_END = "<!-- TIMELINE_END -->"
A_START = "<!-- ADMINLIST_START -->"
A_END = "<!-- ADMINLIST_END -->"

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


def color(cat):
    return CAT_COLOR.get(cat, "#5F5E5A")


def bg(cat):
    return CAT_BG.get(cat, "#F1EFE8")


def sort_events(events):
    return sorted(events, key=lambda e: e.get("date", ""), reverse=True)


def render_stats(events):
    cats = {e.get("category", "其他") for e in events}
    regions = {e.get("region", "全球") for e in events}
    return (
        f'<div class="stat-card"><div class="stat-num">{len(events)}</div>'
        f'<div class="stat-label">事件总数</div></div>'
        f'<div class="stat-card"><div class="stat-num">{len(cats)}</div>'
        f'<div class="stat-label">事件分类</div></div>'
        f'<div class="stat-card"><div class="stat-num">{len(regions)}</div>'
        f'<div class="stat-label">涉及地区</div></div>'
    )


def render_timeline(events):
    out = []
    for ev in sort_events(events):
        cat = ev.get("category", "其他")
        tags = "".join(
            f'<span class="timeline-tag">#{esc(t)}</span>' for t in (ev.get("tags") or [])
        )
        out.append(f'''    <a class="timeline-item" href="./event.html?id={esc(ev.get("id", ""))}">
      <div class="timeline-dot" style="background:{color(cat)};box-shadow:0 0 0 2px {color(cat)}"></div>
      <div class="timeline-date">
        <span class="timeline-category" style="background:{bg(cat)};color:{color(cat)}">{esc(cat)}</span>
        {esc(ev.get("date", ""))} · {esc(ev.get("country", ""))}
      </div>
      <div class="timeline-title">{esc(ev.get("title", ""))}</div>
      <div class="timeline-summary">{esc(ev.get("summary", ""))}</div>
      <div class="timeline-meta">{tags}</div>
    </a>''')
    return "\n".join(out)


def render_admin_list(events):
    out = []
    for ev in sort_events(events):
        cat = ev.get("category", "其他")
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


def main():
    with open(EVENTS_FILE, "r", encoding="utf-8") as f:
        events = json.load(f)

    # 防止新闻内容里的 </script> 把 script 标签提前截断
    payload = json.dumps(events, ensure_ascii=False).replace("</", "<\\/")
    embed_snippet = f"<script>window.__EMBEDDED_EVENTS__ = {payload};</script>"

    # 1) index.html：静态时间线 + 统计 + 内嵌数据
    idx = ROOT / "index.html"
    h = idx.read_text(encoding="utf-8")
    h, _ = replace_between(h, S_START, S_END, render_stats(events), "index 统计")
    h, _ = replace_between(h, T_START, T_END, render_timeline(events), "index 时间线")
    h, _ = replace_between(h, START, END, embed_snippet, "index 内嵌数据")
    idx.write_text(h, encoding="utf-8")
    print(f"  ✅ index.html 已静态渲染 {len(events)} 条事件")

    # 2) event.html：内嵌数据（供详情页 JS 读取）
    ev_page = ROOT / "event.html"
    h = ev_page.read_text(encoding="utf-8")
    h, _ = replace_between(h, START, END, embed_snippet, "event 内嵌数据")
    ev_page.write_text(h, encoding="utf-8")
    print("  ✅ event.html 已内嵌数据")

    # 3) admin.html：静态列表 + 内嵌数据
    adm = ROOT / "admin.html"
    h = adm.read_text(encoding="utf-8")
    h, _ = replace_between(h, A_START, A_END, render_admin_list(events), "admin 列表")
    h, _ = replace_between(h, START, END, embed_snippet, "admin 内嵌数据")
    adm.write_text(h, encoding="utf-8")
    print(f"  ✅ admin.html 已静态渲染 {len(events)} 条")

    print("✨ 构建完成")


if __name__ == "__main__":
    main()
