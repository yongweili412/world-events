# -*- coding: utf-8 -*-
"""数据手术：错位来源归位、劣译修正、HTML实体清理。用完即删。"""
import json, re
from pathlib import Path

ROOT = Path(__file__).parent
evts = json.load(open(ROOT / "data" / "events.json", encoding="utf-8"))
idx = {e["id"]: e for e in evts}

def add_src(e, s, tl_text=None):
    if not any((s.get("url") or "") and x.get("url") == s.get("url") for x in e.get("sources", [])):
        e.setdefault("sources", []).append(s)
    if tl_text:
        e.setdefault("timeline", []).append({"date": s.get("date", e["date"]), "text": tl_text})
        e["timeline"] = sorted(e["timeline"], key=lambda x: x.get("date", ""))

# ---- 1. evt_20260901_014 万斯/El-Sayed：去重 + 拆走两个错位来源 ----
e = idx["evt_20260901_014"]
srcs = e["sources"]
arg = srcs.pop(2)  # 阿根廷福克兰石油诉讼 (09-08)
nk = srcs.pop(2)   # 朝鲜铀浓缩 (09-09)
if len(srcs) > 1 and srcs[0].get("url") == srcs[1].get("url"):
    del srcs[1]  # 去重
e["timeline"] = [t for t in e["timeline"] if "米莱" not in t.get("text", "") and "原子能" not in t.get("text", "")]
e["title"] = "万斯在密歇根竞选集会上称参议院候选人埃尔-赛义德为“邪恶”"
e["summary"] = "万斯在密歇根州竞选集会上攻击民主党参议院候选人阿卜杜勒·埃尔-赛义德，称其为“邪恶”。埃尔-赛义德将与特朗普盟友迈克·罗杰斯角逐参议院席位，这场竞选因人身攻击不断而备受关注。"
e["description"] = e["summary"]
arg["title"] = "阿根廷将对在福克兰群岛运营的石油公司提起刑事诉讼"
add_src(idx["evt_20260901_035"], arg, "BBC｜阿根廷将对在福克兰群岛运营的石油公司提起刑事诉讼：此举发生在米莱加强主权主张数日后")
nk["title"] = "朝鲜已建造两层铀浓缩设施，监察机构称"
add_src(idx["evt_20260909_181"], nk, "BBC｜国际原子能机构：朝鲜新建两层铀浓缩设施，引发“严重关切”")

# ---- 2. evt_20260902_016 委内瑞拉石油：拆走 CNA 特朗普伊朗来源 ----
e = idx["evt_20260902_016"]
cna = e["sources"].pop(1)
e["timeline"] = [t for t in e["timeline"] if "伊朗战争" not in t.get("text", "")]
e["title"] = "特朗普盟友在“枪口外交”批评中捍卫委内瑞拉石油交易"
cna["title"] = "特朗普称美国可能像委内瑞拉协议一样留在伊朗并控制石油"
cna["snippet"] = "特朗普重申，他仍预计伊朗战争将于今年结束，可能在中期选举后不久。他将“留在伊朗控制石油”与美国同委内瑞拉达成的协议相提并论。"
add_src(idx["evt_20260914_024"], cna, "亚洲新闻台｜特朗普称美国可能留在伊朗并控制石油：以委内瑞拉协议作类比，预计战事年内结束")

# ---- 3. evt_20260903_016 约书亚司机：拆走乌克兰冬天来源 ----
e = idx["evt_20260903_016"]
ukr = e["sources"].pop(1)
e["timeline"] = [t for t in e["timeline"] if "联合国高级官员" not in t.get("text", "")]
e["title"] = "安东尼·约书亚司机致命车祸案开庭 庭上称其事发前试图超车"
e["summary"] = "拳王安东尼·约书亚的司机阿德尼伊·莫博拉吉·卡约德在尼日利亚被控危险驾驶致人死亡等罪名，法庭获悉其事发前曾试图超车。"
e["description"] = e["summary"]
new_ukr = {
    "id": "evt_20260912_213", "legacyIds": [],
    "title": "联合国警告：乌克兰将迎来全面入侵以来“最严酷的冬天”",
    "summary": "一名联合国高级官员告诉BBC，随着俄乌战事持续，乌克兰正争分夺秒确保冬季期间民众的电力和供水，将迎来自俄罗斯全面入侵以来最严酷的冬天。",
    "description": "据BBC报道，一名联合国高级官员表示，乌克兰将迎来自俄罗斯全面入侵以来“最严酷的冬天”，人道主义机构正争分夺秒确保冬季期间民众的电力和水的供应。",
    "date": "2026-09-12",
    "location": {"country": "乌克兰", "countryCode": "UA", "region": "东欧", "city": ""},
    "category": ["冲突"], "tags": ["冲突", "俄乌冲突", "BBC", "乌克兰"],
    "status": "ongoing",
    "sources": [ukr],
    "timeline": [{"date": "2026-09-12", "text": "BBC｜联合国警告乌克兰将迎来“最严酷的冬天”：正争分夺秒保障冬季电力与供水"}],
    "aiSummary": "联合国高级官员向BBC表示，乌克兰将迎来自俄罗斯全面入侵以来“最严酷的冬天”，人道主义机构正争分夺秒确保冬季期间民众的电力和供水。", "summaryFull": False
}
ukr["title"] = "乌克兰将迎来全面入侵以来“最严酷的冬天”，联合国称"
evts.append(new_ukr); idx[new_ukr["id"]] = new_ukr

# ---- 4. evt_20260909_181 IAEA朝鲜：拆走叙利亚核调查来源 ----
e = idx["evt_20260909_181"]
syr = e["sources"].pop(1)
e["timeline"] = [t for t in e["timeline"] if "叙利亚" not in t.get("text", "")]
new_syr = {
    "id": "evt_20260909_413", "legacyIds": [],
    "title": "国际原子能机构结束对叙利亚长期核调查",
    "summary": "随着叙利亚新政权变得更加配合，国际原子能机构宣布结束自2011年认定叙利亚违反核不扩散义务以来对该国长达数年的核调查。",
    "description": "国际原子能机构星期三宣布，随着叙利亚新政权在核查合作上更加配合，决定结束自2011年以来对叙利亚长达数年的核调查。",
    "date": "2026-09-09",
    "location": {"country": "叙利亚", "countryCode": "SY", "region": "中东", "city": ""},
    "category": ["国际"], "tags": ["国际", "核问题", "联合早报", "叙利亚"],
    "status": "closed",
    "sources": [syr],
    "timeline": [{"date": "2026-09-09", "text": "联合早报｜国际原子能机构结束对叙利亚长期核调查：新政权加强合作促调查告终"}],
    "aiSummary": "随着叙利亚新政权在核查合作上更加配合，国际原子能机构宣布结束自2011年认定叙利亚违反核不扩散义务以来对该国长达数年的核调查。", "summaryFull": False
}
evts.append(new_syr); idx[new_syr["id"]] = new_syr

# ---- 5. 劣译修正 ----
fix = {
    "evt_20260909_257": {"summary": "美国劳工部在涉嫌欺诈调查中采取行动，暂停了Cognizant公司的新PERM（永久劳工证）申请。专家表示，科技行业可能面临更严格的审查。", "description": "美国劳工部在涉嫌欺诈调查中暂停了Cognizant公司的新PERM申请，专家预计科技行业外籍劳工申请将面临更严格审查。"},
    "evt_20260904_027": {"title": "委内瑞拉反对派领袖马查多：“非法”政权无权签署美国石油协议"},
    "evt_20260905_083": {"summary": "美联社一周看点聚焦NCAA大学橄榄球揭幕战：密歇根大学主场迎战西密歇根大学的比赛出现争议判罚，成为当日大学体育的焦点话题。", "description": "据美联社报道，NCAA大学橄榄球赛季揭幕战中，密歇根大学主场对阵西密歇根大学的比赛出现争议判罚，引发对裁判执法的讨论。"},
    "evt_20260903_042": {"title": "西班牙首相桑切斯：无证据表明摩洛哥支持休达边境违规行为", "summary": "西班牙首相桑切斯告诉议会，西班牙没有看到“任何证据”表明摩洛哥计划或促成其北非领土休达的大规模越境事件。此前警方报告称边境部队帮助约7万人越境。", "description": "西班牙首相桑切斯在议会表示，没有“任何证据”表明摩洛哥策划或协助了休达边境的大规模越境事件，并继续为拉巴特方面辩护。"},
    "evt_20260912_135": {"summary": "美国白宫称，一名俄罗斯官员将出席9月14日至16日在得克萨斯州休斯敦举行的G20能源会议。在伊朗战争扰乱全球燃料市场之际，会议聚焦能源安全与供给。"},
    "evt_20250531_001": {"summary": "何塞-路易斯·塞拉诺·彭蒂纳特正式就任安道尔主教大公兼乌赫尔主教，接替因年龄限制辞职的霍安·恩里克·比维斯。", "description": "2025年5月31日，何塞-路易斯·塞拉诺·彭蒂纳特正式就任安道尔主教大公兼乌赫尔主教，成为这个欧洲公国的共同国家元首之一。"},
    "evt_20260912_163": {"summary": "泰国建筑师库拉帕·扬特拉萨斯特（Why Architecture创始人）在洛杉矶威尼斯海滩设计了一座三层住宅，内部充满艺术品，采用裸露混凝土结构，融合艺术、自然与加州光线。"},
    "evt_20260905_006": {"title": "萨巴伦卡因大麻气味中断美网对阵拉希莫娃的比赛"},
    "evt_20260912_189": {"title": "以色列“黄线”政策挤压巴勒斯坦人生存空间", "summary": "报道显示，以色列的“黄线”政策正在不断挤压巴勒斯坦人的生存空间。居民奥拉·伊斯蒂维的家被拆除，她与孩子们无家可归。该政策导致巴勒斯坦人失去土地和住所，加剧了地区紧张局势。"},
    "evt_20260912_181": {"summary": "艾普莉·库尔森因脊髓出血导致瘫痪，其国家残障保险计划（NDIS）资金被削减。该机构以“物有所值”为由减少其理疗时长，引发关注。"},
}
for eid, fields in fix.items():
    if eid in idx:
        idx[eid].update(fields)

# 约书亚事件时间线重写
idx["evt_20260903_016"]["timeline"] = [{"date": "2026-09-03", "text": "BBC｜约书亚司机车祸案开庭：卡约德被控危险驾驶致人死亡，庭上称其事发前试图超车"}]

# ---- 6. 全局 HTML 实体清理 ----
ENT = {"&ldquo;": "“", "&rdquo;": "”", "&middot;": "·", "&quot;": "”", "&nbsp;": " ", "&hellip;": "……", "&rsquo;": "’", "&lsquo;": "‘", "&mdash;": "——"}
def clean(o):
    if isinstance(o, str):
        for k, v in ENT.items():
            o = o.replace(k, v)
        return o
    if isinstance(o, list):
        return [clean(x) for x in o]
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    return o
evts = [clean(e) for e in evts]

json.dump(evts, open(ROOT / "data" / "events.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("surgery done. total events:", len(evts))
