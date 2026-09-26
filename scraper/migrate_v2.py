# -*- coding: utf-8 -*-
"""
migrate_v2.py —— World Events 数据模型 v2 一次性迁移脚本
按《World_Events 产品设计与开发需求文档 V1.0》执行：
  1. 扁平新闻条目 → "事件"模型（一事件多来源 + 时间线）
  2. 相似标题报道聚合为同一事件（AI 语义聚合在定时任务层做，此处用确定性算法）
  3. 新永久 ID：evt_YYYYMMDD_NNN（旧 ID 存入 legacyIds 兼容历史链接）
  4. 新增 location{country,countryCode,region,city} / status / sources / timeline / relatedEvents
用法：python migrate_v2.py  （原 events.json 自动备份为 data/events_v1_backup.json）
"""
import json, re, difflib, os
from collections import defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(BASE, 'data', 'events.json')
BACKUP = os.path.join(BASE, 'data', 'events_v1_backup.json')

COUNTRY_CODE = {
    '中国':'CN','美国':'US','俄罗斯':'RU','乌克兰':'UA','欧盟':'EU','印度':'IN','日本':'JP','韩国':'KR',
    '朝鲜':'KP','英国':'GB','法国':'FR','德国':'DE','印尼':'ID','印度尼西亚':'ID','泰国':'TH','越南':'VN',
    '菲律宾':'PH','缅甸':'MM','巴基斯坦':'PK','伊朗':'IR','以色列':'IL','巴勒斯坦':'PS','沙特':'SA',
    '沙特阿拉伯':'SA','阿联酋':'AE','土耳其':'TR','埃及':'EG','南非':'ZA','尼日利亚':'NG','苏丹':'SD',
    '埃塞俄比亚':'ET','肯尼亚':'KE','刚果（金）':'CD','刚果民主共和国':'CD','墨西哥':'MX','巴西':'BR',
    '阿根廷':'AR','加拿大':'CA','澳大利亚':'AU','新西兰':'NZ','孟加拉国':'BD','斯里兰卡':'LK','尼泊尔':'NP',
    '阿富汗':'AF','伊拉克':'IQ','叙利亚':'SY','约旦':'JO','黎巴嫩':'LB','卡塔尔':'QA','也门':'YE',
    '阿曼':'OM','科威特':'KW','匈牙利':'HU','波兰':'PL','捷克':'CZ','瑞典':'SE','瑞士':'CH','荷兰':'NL',
    '西班牙':'ES','意大利':'IT','希腊':'GR','塞尔维亚':'RS','克罗地亚':'HR','拉脱维亚':'LV','爱沙尼亚':'EE',
    '立陶宛':'LT','芬兰':'FI','丹麦':'DK','挪威':'NO','新加坡':'SG','马来西亚':'MY','蒙古':'MN',
    '哈萨克斯坦':'KZ','乌兹别克斯坦':'UZ','吉尔吉斯斯坦':'KG','塔吉克斯坦':'TJ','海地':'HT','委内瑞拉':'VE',
    '智利':'CL','哥伦比亚':'CO','秘鲁':'PE','玻利维亚':'BO','古巴':'CU','冰岛':'IS','奥地利':'AT',
    '比利时':'BE','爱尔兰':'IE','葡萄牙':'PT','突尼斯':'TN','阿尔及利亚':'DZ','摩洛哥':'MA','利比亚':'LY',
    '加纳':'GH','索马里':'SO','莫桑比克':'MZ','马达加斯加':'MG','西班牙':'ES','巴拿马':'PA','厄瓜多尔':'EC',
    '老挝':'LA','柬埔寨':'KH','文莱':'BN','东帝汶':'TL','巴布亚新几内亚':'PG','斐济':'FJ','也门':'YE',
}

def norm_title(t):
    """归一化标题用于聚类比较"""
    t = re.sub(r'[\s\W_]+', '', (t or '').lower())
    return t

def snippet(text, n=120):
    t = re.sub(r'\s+', ' ', (text or '')).strip()
    return t[:n]

def main():
    events = json.load(open(SRC, encoding='utf-8'))
    json.dump(events, open(BACKUP, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f'原数据 {len(events)} 条，已备份到 data/events_v1_backup.json')

    # ---- 第一步：聚类（同一事件的多篇报道） ----
    # 1a. 归一化标题前 22 字精确分组（捕获"XX事件遇难人数升至NNN"这类系列报道）
    groups = []  # list[list[event]]
    by_prefix = defaultdict(list)
    leftovers = []
    for e in events:
        k = norm_title(e['title'])[:22]
        if k and len(by_prefix[k]) > 0:
            by_prefix[k].append(e)
        elif k:
            # 检查是否与已有组前缀高度相似（如"山洪遇难18人"vs"泥石流遇难289人"）
            placed = False
            for g in groups:
                if norm_title(g[0]['title'])[:12] == k[:12] and difflib.SequenceMatcher(
                        None, norm_title(g[0]['title']), norm_title(e['title'])).ratio() > 0.62:
                    g.append(e); placed = True; break
            if not placed:
                by_prefix[k].append(e)
                groups.append(by_prefix[k])
        else:
            leftovers.append([e])
    # 1b. 同 region 且日期相差 <=3 天、标题相似度高的组再合并一轮
    merged = []
    for g in sorted(groups, key=lambda x: min(e['date'] for e in x)):
        placed = False
        for g2 in merged:
            if g2[0]['region'] == g[0]['region']:
                d1 = min(e['date'] for e in g); d2 = min(e['date'] for e in g2)
                if abs((d1 > d2) - 0) >= 0:  # placeholder, real check below
                    pass
                from datetime import date
                y1,m1,dd1 = map(int, d1.split('-')); y2,m2,dd2 = map(int, d2.split('-'))
                gap = abs(date(y1,m1,dd1).toordinal() - date(y2,m2,dd2).toordinal())
                if gap <= 3 and difflib.SequenceMatcher(
                        None, norm_title(g2[0]['title']), norm_title(g[0]['title'])).ratio() > 0.68:
                    g2.extend(g); placed = True; break
        if not placed:
            merged.append(list(g))
    for g in merged:
        g.sort(key=lambda e: (e['date'], e.get('source','')))
    groups = merged + leftovers
    n_multi = sum(1 for g in groups if len(g) > 1)
    print(f'聚类完成：{len(groups)} 个事件（其中 {n_multi} 个事件含多篇报道）')

    # ---- 第二步：生成新事件结构 ----
    # 按最早日期排序，生成 evt_YYYYMMDD_NNN
    groups.sort(key=lambda g: (min(e['date'] for e in g), min(e.get('source','') for e in g)))
    day_counter = defaultdict(int)
    out = []
    for g in groups:
        g.sort(key=lambda e: (e['date'], e.get('source','')))
        first = g[0]
        dates = sorted({e['date'] for e in g})
        ymd = dates[0].replace('-', '')
        day_counter[ymd] += 1
        eid = f'evt_{ymd}_{day_counter[ymd]:03d}'
        cat = first.get('category') or '其他'
        country = first.get('country') or ''
        region = first.get('region') or ''
        tags = []
        for e in g:
            for t in (e.get('tags') or []):
                if t and t not in tags:
                    tags.append(t)
        # 摘要与描述
        summary = snippet(first.get('summary') or first.get('content'), 200)
        desc_parts = []
        for e in g:
            s = snippet(e.get('summary') or e.get('content'), 200)
            if s and s not in desc_parts:
                desc_parts.append(s)
        description = ' '.join(desc_parts)
        # 来源（保留原文链接）
        sources = []
        for e in g:
            if e.get('sourceUrl') or e.get('source'):
                sources.append({
                    'name': e.get('source') or '',
                    'url': e.get('sourceUrl') or '',
                    'date': e['date'],
                    'title': e['title'],
                    'snippet': snippet(e.get('summary') or e.get('content'), 120),
                })
        # 时间线：跨多天的报道天然构成事件发展线
        timeline = []
        if len(dates) > 1:
            for d in dates:
                day_reports = [e for e in g if e['date'] == d]
                best = max(day_reports, key=lambda e: len(e.get('summary') or ''))
                txt = snippet(best.get('summary') or best.get('title'), 90)
                timeline.append({'date': d, 'text': txt})
        else:
            timeline = [{'date': dates[0], 'text': snippet(first.get('summary') or first.get('title'), 90)}]
        # AI 速览：优先取"全文版"，否则取最早非空
        ai = ''
        ai_full = False
        for e in g:
            if e.get('summaryFull') and (e.get('aiSummary') or '').strip():
                ai = e['aiSummary']; ai_full = True; break
        if not ai:
            for e in g:
                if (e.get('aiSummary') or '').strip():
                    ai = e['aiSummary']; break
        # 相关旧 ID（兼容历史链接）
        legacy = [e['id'] for e in g]
        status = 'ongoing' if len(dates) > 1 else 'closed'
        out.append({
            'id': eid,
            'legacyIds': legacy,
            'title': first['title'],
            'summary': summary,
            'description': description,
            'date': dates[0],
            'time': first.get('time') or '',
            'location': {
                'country': country,
                'countryCode': COUNTRY_CODE.get(country, ''),
                'region': region,
                'city': '',
            },
            'category': [cat],
            'tags': tags,
            'status': status,
            'sources': sources,
            'timeline': timeline,
            'relatedEvents': [],
            'aiSummary': ai,
            'summaryFull': ai_full,
        })
    json.dump(out, open(SRC, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    n_src = sum(len(e['sources']) for e in out)
    print(f'迁移完成：{len(events)} 条报道 → {len(out)} 个事件（{n_src} 个来源链接），已写回 data/events.json')
    print(f'ID 样例: {out[0]["id"]} ... {out[-1]["id"]}')
    print(f'ongoing(多日持续)事件: {sum(1 for e in out if e["status"]=="ongoing")}')

if __name__ == '__main__':
    main()
