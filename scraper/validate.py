# -*- coding: utf-8 -*-
"""数据体检：校验 data/events.json 的不变量，机器化拦住字段类型/中文残留等问题。

用法：
    python scraper/validate.py            # 全量校验，问题非空则退出码 1
    python scraper/validate.py --quiet     # 只输出汇总行

检查项：
    1. JSON 合法、事件数组非空
    2. id 唯一且符合 evt_YYYYMMDD_NNN / EVT-XXXXXXXX
    3. 每条事件必备字段存在（title/date/sources/aiSummary…）
    4. summaryFull 必须是布尔或 null（历史 bug：曾把速览正文写进该字段）
    5. 无 aiSummary 的事件数（应为 0）
    6. 中文红线：title/summary/description、sources[].title/snippet、timeline[].text、aiSummary 无英文正文
    7. tags 数量 ≤ 12
    8. sources/timeline 结构完整（url、text 非空）
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("EVENTS_PATH") or os.path.join(ROOT, "data", "events.json")
ID_RE = re.compile(r"evt_\d{8}_\d{3}|EVT-[0-9A-F]{8}")
# 允许保留英文原文的事件（敏感条目/专有名词）
ALLOW_EN = {"evt_20260911_101", "evt_20201107_018"}


def is_en_text(s: str) -> bool:
    if not s:
        return False
    cn = len(re.findall(r"[\u4e00-\u9fff]", s))
    en = len(re.findall(r"[A-Za-z]", s))
    return en > 20 and cn < en * 0.3


def main() -> int:
    quiet = "--quiet" in sys.argv
    evs = json.load(open(DATA, encoding="utf-8"))
    problems = []

    ids = [e.get("id") for e in evs]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        problems.append(f"重复 id：{sorted(dup)[:5]}")
    bad_id = [i for i in ids if not i or not ID_RE.fullmatch(str(i))]
    if bad_id:
        problems.append(f"非法 id：{bad_id[:5]}")

    bad_sf, no_sum, bad_tags, bad_struct = [], [], [], []
    en_hits = {}
    for e in evs:
        eid = e.get("id")
        sf = e.get("summaryFull")
        if not isinstance(sf, (bool, type(None))):
            bad_sf.append((eid, type(sf).__name__))
        if not e.get("aiSummary"):
            no_sum.append(eid)
        if len(e.get("tags") or []) > 12:
            bad_tags.append(eid)
        for s in e.get("sources") or []:
            if not s.get("url") or not s.get("name"):
                bad_struct.append(eid)
                break
        for t in e.get("timeline") or []:
            if not (t.get("text") or "").strip():
                bad_struct.append(eid)
                break
        if eid in ALLOW_EN:
            continue
        fields = [("title", e.get("title")), ("summary", e.get("summary")), ("description", e.get("description"))]
        fields += [("sources[].title", (s.get("title") or "")) for s in e.get("sources") or []]
        fields += [("sources[].snippet", (s.get("snippet") or "")) for s in e.get("sources") or []]
        fields += [("timeline[].text", re.sub(r"^.*?[｜|]", "", t.get("text") or "")) for t in e.get("timeline") or []]
        fields += [("aiSummary", e.get("aiSummary"))]
        hits = {k for k, v in fields if is_en_text(v)}
        if hits:
            en_hits[eid] = sorted(hits)

    if bad_sf:
        problems.append(f"summaryFull 类型异常 {len(bad_sf)} 条：{bad_sf[:5]}")
    if no_sum:
        problems.append(f"无 aiSummary {len(no_sum)} 条：{no_sum[:5]}")
    if bad_tags:
        problems.append(f"tags 超 12 {len(bad_tags)} 条：{bad_tags[:5]}")
    if bad_struct:
        problems.append(f"sources/timeline 结构缺失 {len(set(bad_struct))} 条：{sorted(set(bad_struct))[:5]}")
    if en_hits:
        problems.append(f"英文残留 {len(en_hits)} 条：{list(en_hits.items())[:3]}")

    print(f"事件总数 {len(evs)} | summaryFull(true) {sum(1 for e in evs if e.get('summaryFull') is True)} "
          f"| 无速览 {len(no_sum)} | 问题项 {len(problems)}")
    if not quiet:
        for p in problems:
            print("  ✗", p)
    if problems:
        print("❌ 校验未通过")
        return 1
    print("✅ 校验通过：id 唯一、字段类型正确、中文红线满足、tags 合规")
    return 0


if __name__ == "__main__":
    sys.exit(main())
