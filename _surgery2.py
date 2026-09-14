# -*- coding: utf-8 -*-
"""第二轮手术：006 垃圾桶清理 + 错位来源归位 + 新事件创建。用完即删。"""
import json, re
from pathlib import Path

ROOT = Path(__file__).parent
evts = json.load(open(ROOT / "data" / "events.json", encoding="utf-8"))
idx = {e["id"]: e for e in evts}

# 全库 URL 索引（事件 -> urls）
url_owner = {}
for e in evts:
    for s in e.get("sources", []):
        u = s.get("url") or ""
        if u:
            url_owner.setdefault(u, e["id"])

def add_src(tid, s, tl_text=None):
    t = idx[tid]
    if not any((s.get("url") or "") and x.get("url") == s.get("url") for x in t.get("sources", [])):
        t.setdefault("sources", []).append(s)
    if tl_text:
        ex = {(x.get("date"), x.get("text")) for x in t.get("timeline", [])}
        if (s.get("date", t["date"]), tl_text) not in ex:
            t["timeline"].append({"date": s.get("date", t["date"]), "text": tl_text})
            t["timeline"] = sorted(t["timeline"], key=lambda x: x.get("date", ""))

def next_id(d):
    n = [int(e["id"].rsplit("_", 1)[1]) for e in evts if e["id"].startswith("evt_" + d + "_")]
    return "evt_%s_%03d" % (d, max(n) + 1 if n else 1)

def new_event(eid, title, summary, date, country, region, category, tags, src, tl_text, ai, status="closed"):
    ev = {"id": eid, "legacyIds": [], "title": title, "summary": summary, "description": summary,
          "date": date, "location": {"country": country, "countryCode": "", "region": region, "city": ""},
          "category": category, "tags": tags, "status": status,
          "sources": [src], "timeline": [{"date": date, "text": tl_text}],
          "aiSummary": ai, "summaryFull": False}
    evts.append(ev); idx[eid] = ev
    print("NEW", eid, title)

# ============ 1. evt_20260830_006 尼泊尔事件清理 ============
e6 = idx["evt_20260830_006"]
keep, strays = [], []
for s in e6["sources"]:
    if "尼泊尔" in (s.get("title") or ""):
        keep.append(s)
    else:
        strays.append(s)
print("006: keep", len(keep), "strays", len(strays))

reloc_006 = {}
for s in strays:
    t = (s.get("title") or "")
    u = s.get("url") or ""
    if "Trump downplays warnings of AI" in t or "Trump dismisses calls for AI slowdown" in t:
        reloc_006[id(s)] = ("evt_20260914_077", "特朗普淡化AI风险警告：坚称对华竞争是关键", "特朗普反驳AI风险警告，称渲染威胁的是“负面力量”，并重申美国必须在AI领域领先中国。")
    elif "Indonesian ferry" in t or "Indonesian passenger shi" in t:
        reloc_006[id(s)] = ("evt_20260913_098", "印尼爪哇海渡轮倾覆：6死130人失踪", "渡轮在爪哇海倾覆后，多艘船只与直升机仍在搜寻失踪者。")
    elif "Vanuatu ferry" in t:
        reloc_006[id(s)] = ("__NEW_VANUATU__", None, None)
    elif "Kaaba" in t or "Mecca" in t:
        reloc_006[id(s)] = ("__NEW_MECCA__", None, None)
    elif "US boots on the ground in Iran" in t:
        reloc_006[id(s)] = ("evt_20260906_084", "解密画面显示美军已进入伊朗境内作战", "CBS披露的新细节揭示了此次行动的规模与物资代价。")
    elif "Pope commemorates" in t:
        reloc_006[id(s)] = ("__NEW_POPE__", None, None)
    elif u and u in url_owner and url_owner[u] != "evt_20260830_006":
        continue  # 已有别处事件持有同一 URL，直接丢弃
    else:
        # 无家可归的中文杂项：留在事件里（避免数据丢失）
        keep.append(s)

e6["sources"] = keep
e6["timeline"] = [t for t in e6["timeline"] if "尼泊尔" in t.get("text", "") or "隧道" in t.get("text", "") or "洪" in t.get("text", "")]
print("006 after: srcs", len(e6["sources"]), "tl", len(e6["timeline"]))

# 未归位来源先暂存，稍后处理新事件
pend = [s for s in strays if id(s) in reloc_006]

# ============ 2. 其他事件错位来源归位 ============
# evt_20260901_005 加沙四旋翼
e5 = idx["evt_20260901_005"]
gz = e5["sources"].pop(3)
e5["timeline"] = [t for t in e5["timeline"] if "quadcopter" not in t.get("text", "")]
pend_gz = gz

# evt_20260901_010 兹维列夫美网
e10 = idx["evt_20260901_010"]
zv = e10["sources"].pop(9)
e10["timeline"] = [t for t in e10["timeline"] if "Zverev" not in t.get("text", "") and "兹维列夫" not in t.get("text", "")]
pend_zv = zv

# evt_20260903_013 Tiananmen 敏感来源并入 101（保持原文）
e13 = idx["evt_20260903_013"]
tn = e13["sources"].pop(2)
e13["timeline"] = [t for t in e13["timeline"] if "Tiananmen" not in t.get("text", "") and "香港" not in t.get("text", "")]
add_src("evt_20260911_101", tn, None)

# evt_20260905_014 威特科夫：6 个错位来源
e14 = idx["evt_20260905_014"]
p14 = []
keep14 = []
for s in e14["sources"]:
    t = (s.get("title") or "")
    if "金砖" in t and "普京" in t:
        add_src("evt_20260912_130", s, "德国之声｜金砖峰会：普京与莫迪重申伙伴关系，莫迪将会晤习近平")
    elif "金砖" in t and "克制" in t:
        add_src("evt_20260912_207", s, "世界报｜金砖国家呼吁中东战争各方保持“最大克制”")
    elif "drone" in t.lower() and ("Beijing" in t or "北京" in t):
        p14.append(s)
    elif "diesel" in t.lower() or "柴油" in t:
        add_src("evt_20260913_092", s, "印度教徒报｜特朗普呼吁乌克兰停止打击俄罗斯柴油设施：乌方数月来持续袭击俄油气产业")
    elif "independence summit" in t.lower() or "独立峰会" in t:
        p14.append(s)
    else:
        keep14.append(s)
e14["sources"] = keep14
e14["timeline"] = [t for t in e14["timeline"] if not any(k in t.get("text", "") for k in ["Beijing", "北京", "drone", "diesel", "diesel", "独立峰会", "separatist", "金砖"])]

# evt_20260905_033 下萨克森
e33 = idx["evt_20260905_033"]
ns = e33["sources"].pop(11)
e33["timeline"] = [t for t in e33["timeline"] if "CDU" not in t.get("text", "") and "下萨克森" not in t.get("text", "")]

# evt_20260906_084 美伊主事件：4 个也门来源归位
e84 = idx["evt_20260906_084"]
keep84 = []
for s in e84["sources"]:
    t = (s.get("title") or "")
    if "Houthis advance" in t:
        add_src("evt_20260911_262", s, "半岛电视台｜胡塞武装向马里卜、塔伊兹政府军据点推进：也门战事加剧")
    elif "Hormuz talks" in t or "Oman says" in t:
        add_src("evt_20260912_206", s, "半岛电视台｜阿曼宣布与伊朗及海湾国家的霍尔木兹会谈推迟：需“适当条件”")
    elif "uncomfortable choice" in t:
        add_src("evt_20260911_262", s, "亚洲新闻台｜胡塞挺进令海湾国家面临两难：曼德海峡战略岛屿易手改变格局")
    elif "new attacks on Saudi" in t:
        add_src("evt_20260913_090", s, "美国广播公司｜伊朗支持的胡塞武装声称对沙特发动新一轮袭击")
    else:
        keep84.append(s)
e84["sources"] = keep84
e84["timeline"] = [t for t in e84["timeline"] if not any(k in t.get("text", "") for k in ["Houthis", "Hormuz talks", "uncomfortable", "attacks on Saudi", "Oman says"])]

# evt_20260909_139 / evt_20260910_289 来源就地翻译
for s in idx["evt_20260909_139"]["sources"]:
    if "witness box" in (s.get("title") or ""):
        s["title"] = "直播：知名运动员在艾伦·琼斯案庭审中出庭作证"
        s["snippet"] = "一名被称为“投诉人M”的知名运动员正在悉尼法庭作证，琼斯被控多项猥亵罪名。"
for t in idx["evt_20260909_139"]["timeline"]:
    if "witness box" in t.get("text", "") or "Complainant M" in t.get("text", ""):
        t["text"] = "ABC澳大利亚｜直播：知名运动员出庭作证，琼斯案庭审进入关键阶段"
for s in idx["evt_20260910_289"]["sources"]:
    if "How 9/11 changed the far right" in (s.get("title") or ""):
        s["title"] = "“9·11”如何改变了极右翼"
for t in idx["evt_20260910_289"]["timeline"]:
    if "How 9/11" in t.get("text", ""):
        t["text"] = "世界报｜“9·11”如何改变了极右翼：袭击成为激进右翼运动的转折点"

# evt_20260910_284 嫦娥六号 aiSummary
idx["evt_20260910_284"]["aiSummary"] = "嫦娥六号采回的月球背面样本研究显示，月球正面与背面迥异的地貌可能源于早期演化中的不均匀受热过程，为破解月球“二分性”之谜提供了关键证据。"

# ============ 3. 创建新事件 ============
for s, spec in reloc_006.items():
    pass  # 占位（已在上方处理逻辑中）

# 从 pend 中分流到新事件
for s in pend:
    tid, t, sn = reloc_006[id(s)]
    d = (s.get("date") or "2026-09-14")[:10]
    if tid == "__NEW_VANUATU__":
        s["title"] = "瓦努阿图渡轮倾覆致1死30人失踪"
        s["snippet"] = "南太平洋岛际渡轮倾覆后，救援因条件受限进展缓慢。"
        new_event(next_id(d), "瓦努阿图渡轮倾覆致1死30人失踪", "一艘在南太平洋岛际航行的渡轮倾覆，造成1人死亡、30人失踪，救援进展缓慢。", d, "瓦努阿图", "大洋洲", ["事故"], ["事故", "渡轮", "德国之声", "瓦努阿图"], s, "德国之声｜瓦努阿图渡轮倾覆：1死30失踪，救援进展缓慢", "一艘南太平洋岛际渡轮倾覆，造成1人死亡、30人失踪，救援因条件受限进展缓慢。")
    elif tid == "__NEW_MECCA__":
        s["title"] = "麦加天降大雨雷电 朝觐者雨中礼拜场面震撼"
        s["snippet"] = "暴雨和雷暴袭击麦加，朝觐者在雨中的礼拜场面蔚为壮观。"
        new_event(next_id(d), "麦加突降暴雨雷电 朝觐者雨中礼拜", "暴雨与雷暴袭击伊斯兰圣城麦加，天房周边出现罕见大雨，朝觐者雨中礼拜的场景在社交媒体广泛传播。", d, "沙特阿拉伯", "中东", ["社会"], ["社会", "气象", "半岛电视台", "沙特阿拉伯"], s, "半岛电视台｜麦加天降大雨：暴雨雷暴中朝觐者坚持礼拜", "暴雨与雷暴袭击麦加，朝觐者在雨中礼拜的震撼场面广为流传。")
    elif tid == "__NEW_POPE__":
        s["title"] = "教皇纪念“9·11”袭击 为和平与遇难者祈祷"
        s["snippet"] = "教皇在圣彼得广场发表讲话，纪念“9·11”袭击事件，为和平与遇难者祈祷。"
        new_event(next_id(d), "教皇纪念“9·11”袭击25周年 为和平与遇难者祈祷", "罗马天主教教皇在圣彼得广场发表讲话，纪念“9·11”恐怖袭击事件，为世界和平与遇难者祈祷。", d, "梵蒂冈", "欧洲", ["国际"], ["国际", "宗教", "美国广播公司", "梵蒂冈"], s, "美国广播公司｜教皇在圣彼得广场纪念“9·11”：为和平与遇难者祈祷", "教皇在圣彼得广场发表讲话，纪念“9·11”袭击事件，为和平与遇难者祈祷。")
    else:
        s["title"] = t; s["snippet"] = sn
        add_src(tid, s, (s.get("name") or "") + "｜" + t)

# 加沙四旋翼
d = (pend_gz.get("date") or "2026-09-01")[:10]
pend_gz["title"] = "以军四旋翼无人机低飞穿行加沙城居民区"
pend_gz["snippet"] = "视频显示，一架低空飞行的以色列四旋翼无人机在加沙城居民楼宇间穿行，向居民喊话恐吓。"
new_event(next_id(d), "以军四旋翼无人机低飞穿行加沙城居民区恐吓民众", "半岛电视台公布视频显示，一架低空飞行的以色列四旋翼无人机在加沙城居民楼宇间机动穿行，被指用于恐吓当地居民。", d, "巴勒斯坦", "中东", ["冲突"], ["冲突", "巴以冲突", "半岛电视台", "巴勒斯坦"], pend_gz, "半岛电视台｜以军四旋翼无人机低飞穿行加沙居民楼间：视频记录恐吓场景", "半岛电视台公布视频显示，一架低空飞行的以色列四旋翼无人机在加沙城居民楼宇间机动穿行，被指用于恐吓当地居民。")

# 兹维列夫美网夺冠
d = (pend_zv.get("date") or "2026-09-13")[:10]
pend_zv["title"] = "兹维列夫四盘击败谢尔顿 首夺美网男单冠军"
pend_zv["snippet"] = "德国名将兹维列夫以6比3、7比6等比分四盘击败美国选手谢尔顿，赢得个人首个美网男单冠军。"
new_event(next_id(d), "兹维列夫四盘击败谢尔顿 首夺美网男单冠军", "德国网球名将兹维列夫在美网男单决赛中以四盘击败美国选手谢尔顿，夺得个人首个美网冠军。", d, "美国", "北美", ["体育"], ["体育", "网球", "美网", "半岛电视台"], pend_zv, "半岛电视台｜美网男单决赛：兹维列夫四盘力克谢尔顿首夺冠军", "兹维列夫在美网男单决赛中四盘击败谢尔顿，收获个人首个美网男单冠军。")

# 北京无人机禁令（2来源）
drone_srcs = p14[:2]
if drone_srcs:
    d = (drone_srcs[0].get("date") or "2026-09-13")[:10]
    for s in drone_srcs:
        if "China set to ban" in (s.get("title") or ""):
            s["title"] = "北京将自11月起禁止在市内持有无人机"; s["snippet"] = "北京拟自11月起将无人机禁令扩大至市内持有环节，监管进一步收紧。"
        elif "Beijing bans drone" in (s.get("title") or ""):
            s["title"] = "北京禁止在市内持有无人机"; s["snippet"] = "在早前收紧监管的基础上，北京进一步禁止在市内持有无人机。"
    ev = {"id": next_id(d), "legacyIds": [], "title": "北京将自11月起禁止在市内持有无人机",
          "summary": "据报道，北京将自11月起把无人机禁令扩大至市内持有环节，此前当地已逐步收紧无人机监管。",
          "description": "北京拟将无人机禁令扩大到在市内持有无人机，新规预计自11月起施行，这是当地近期一系列收紧无人机监管举措的最新一步。",
          "date": d, "location": {"country": "中国", "countryCode": "CN", "region": "亚洲", "city": "北京"},
          "category": ["社会"], "tags": ["社会", "无人机", "监管", "中国"], "status": "closed",
          "sources": drone_srcs,
          "timeline": [{"date": d, "text": "印度时报｜北京将自11月起禁止在市内持有无人机，监管进一步收紧"}],
          "aiSummary": "据报道，北京将自11月起把无人机禁令扩大至市内持有环节。此前当地已陆续收紧无人机飞行监管，新规将持有也纳入禁止范围。", "summaryFull": False}
    evts.append(ev); idx[ev["id"]] = ev; print("NEW", ev["id"], ev["title"])
    if len(p14) > 2:
        uk = p14[2]
        d2 = (uk.get("date") or d)[:10]
        uk["title"] = "苏格兰、威尔士与北爱尔兰分离政党将举行独立峰会"
        uk["snippet"] = "三地分离主义政党领导人将于周一举行罕见会谈，商讨独立议题。"
        new_event(next_id(d2), "苏格兰、威尔士与北爱尔兰分离政党将举行独立峰会", "英国三地分离主义政党的领导人将举行罕见会谈，共同商讨推动独立议题，被视为对伦敦中央政府的联合施压。", d2, "英国", "欧洲", ["政治"], ["政治", "独立议题", "世界报", "英国"], uk, "世界报｜苏格兰、威尔士、北爱尔兰分离政党将举行独立峰会：三地领导人罕见会谈", "英国三地分离主义政党领导人将举行罕见会谈，商讨共同推动独立议题。")

# 下萨克森选举
d = (ns.get("date") or "2026-09-13")[:10]
ns["title"] = "下萨克森州地方选举：基民盟领先"
ns["snippet"] = "初步结果显示，基民盟有望在德国西北部州的下萨克森地方选举中获胜。"
new_event(next_id(d), "下萨克森州地方选举初步结果：基民盟领先", "德国下萨克森州举行地方选举，初步计票显示基民盟领先，有望在西北部这个州的地方选举中获胜。", d, "德国", "欧洲", ["政治"], ["政治", "选举", "德国之声", "德国"], ns, "德国之声｜下萨克森州地方选举：基民盟领先，有望获胜", "德国下萨克森州地方选举初步计票显示基民盟领先，有望在西北部州的地方选举中获胜。")

json.dump(evts, open(ROOT / "data" / "events.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("done. total:", len(evts))
