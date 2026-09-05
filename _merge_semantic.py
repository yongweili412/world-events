# -*- coding: utf-8 -*-
"""语义聚合：删除死存档旧闻 + 跨源重复事件合并进主事件"""
import json, io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

path = r'D:\WorkBuddy\projects\world-events\data\events.json'
evs = json.load(open(path, encoding='utf-8'))
by = {e['id']: e for e in evs}

# ---- 1. 删除死存档旧闻（新华英文/中国日报/CNN 的 RSS 返回多年旧文）----
DELETE = [
    # 新华网英文世界（2017-2018 旧闻）
    'evt_20260906_009', 'evt_20260906_010', 'evt_20260906_011', 'evt_20260906_012',
    'evt_20260906_013', 'evt_20260906_014', 'evt_20260906_015', 'evt_20260906_016',
    'evt_20260906_017', 'evt_20260906_018',
    # 中国日报世界（2017 旧文）
    'evt_20260906_019', 'evt_20260906_020', 'evt_20260906_021', 'evt_20260906_022',
    'evt_20260906_023',
    # CNN World（2022-2023 旧文）
    'evt_20230415_001', 'evt_20230415_002', 'evt_20230414_001', 'evt_20230414_002',
    'evt_20230414_003', 'evt_20220322_001', 'evt_20220323_001', 'evt_20220320_001',
    'evt_20230418_001', 'evt_20230331_001',
    # Euronews 每日简报（无实质事件内容）
    'evt_20260905_050',
]
n_del = 0
for eid in DELETE:
    if eid in by:
        evs.remove(by.pop(eid)); n_del += 1
print(f'删除死存档旧闻: {n_del} 条')

# ---- 2. 语义合并：src -> dst ----
MERGES = {
    # 美军打击伊朗油轮（哈格岛/海湾）→ 主事件已有 Euronews+AlJazeera 2 源
    'evt_20260906_024': 'evt_20260905_046',
    'evt_20260905_054': 'evt_20260905_046',
    'evt_20260905_069': 'evt_20260905_046',
    'evt_20260905_080': 'evt_20260905_046',
    'evt_20260906_030': 'evt_20260905_046',
    'evt_20260905_087': 'evt_20260905_046',
    # 美特使访俄乌 + 双方暂停打击首都 → 主事件为昨日已有事件
    'evt_20260906_025': 'evt_20260905_014',
    'evt_20260906_029': 'evt_20260905_014',
    'evt_20260906_040': 'evt_20260905_014',
    'evt_20260906_041': 'evt_20260905_014',
    'evt_20260905_049': 'evt_20260905_014',
    'evt_20260905_071': 'evt_20260905_014',
    'evt_20260905_081': 'evt_20260905_014',
    'evt_20260905_082': 'evt_20260905_014',
    # 尼泊尔洪灾各侧面（死亡人数/救援幸存者/火化/赔偿诉求/印度人失踪）→ 主事件为 08-31 已有事件
    'evt_20260904_048': 'evt_20260831_007',
    'evt_20260905_042': 'evt_20260831_007',
    'evt_20260905_044': 'evt_20260831_007',
    'evt_20260905_052': 'evt_20260831_007',
    'evt_20260905_055': 'evt_20260831_007',
    'evt_20260905_083': 'evt_20260831_007',
    'evt_20260905_079': 'evt_20260831_007',
    'evt_20260906_033': 'evt_20260831_007',
    'evt_20260906_039': 'evt_20260831_007',
    # 多佛港反移民抗议（DW/AlJazeera/Hindu）
    'evt_20260906_027': 'evt_20260905_086',
    'evt_20260905_058': 'evt_20260905_086',
    # 联合国新世界地图（BBC/France24 → Sky 主事件）
    'evt_20260906_026': 'evt_20260905_035',
    'evt_20260906_037': 'evt_20260905_035',
    # 德国萨克森-安哈尔特州选举/AfD（DW×2 → BBC 主事件）
    'evt_20260906_034': 'evt_20260905_033',
    'evt_20260906_035': 'evt_20260905_033',
    # 玻利维亚军营爆炸（Euronews → BBC+Sky 主事件）
    'evt_20260905_048': 'evt_20260905_031',
    # 福克兰群岛（Sky → 既有 09-01 主事件）
    'evt_20260904_051': 'evt_20260901_035',
}

def merge(dst, src):
    urls = {s.get('url') for s in dst.get('sources', [])}
    for s in src.get('sources', []):
        if s.get('url') not in urls:
            dst.setdefault('sources', []).append(s)
            urls.add(s.get('url'))
    dst.setdefault('timeline', []).extend(src.get('timeline', []))
    if src['date'] < dst['date']:
        dst['date'] = src['date']
    dst.setdefault('legacyIds', []).extend(src.get('legacyIds', []))
    dates = {s.get('date') for s in dst.get('sources', [])}
    dst['status'] = 'ongoing' if len(dates) > 1 else dst.get('status', 'closed')

n_mg = 0
for src_id, dst_id in MERGES.items():
    if src_id in by and dst_id in by:
        merge(by[dst_id], by.pop(src_id))
        evs.remove(next(e for e in evs if e['id'] == src_id))
        n_mg += 1
print(f'语义合并: {n_mg} 个事件并入主事件')

# timeline 按 date 排序
for e in evs:
    if e.get('timeline'):
        e['timeline'].sort(key=lambda t: t.get('date', ''))

json.dump(evs, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print(f'当前事件总数: {len(evs)}')
for eid in ['evt_20260905_046', 'evt_20260905_014', 'evt_20260831_007', 'evt_20260905_035']:
    e = by.get(eid)
    if e:
        print(f"  {eid}: {len(e['sources'])} 来源, date={e['date']}, status={e['status']}")
