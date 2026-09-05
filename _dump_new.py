# -*- coding: utf-8 -*-
"""导出本次抓取新建的事件（对比 git HEAD 版本）"""
import json, io, sys, subprocess
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

old = json.loads(subprocess.run(
    ['git', 'show', 'HEAD:data/events.json'],
    capture_output=True, cwd=r'D:\WorkBuddy\projects\world-events').stdout.decode('utf-8'))
old_ids = {e['id'] for e in old}
old_urls = {s.get('url') for e in old for s in e.get('sources', []) if s.get('url')}

evs = json.load(open(r'D:\WorkBuddy\projects\world-events\data\events.json', encoding='utf-8'))
new_events = [e for e in evs if e['id'] not in old_ids]
# 被更新（追加来源）的旧事件
updated = [e for e in evs if e['id'] in old_ids
           and any(s.get('url') not in old_urls for s in e.get('sources', []))]

print(f'新建事件: {len(new_events)}, 更新事件: {len(updated)}')

def ar(s): return sum(1 for c in s if ord(c) < 128) / max(len(s), 1)
en_new = [e for e in new_events if ar(e.get('title') or '') > 0.5]
zh_new = [e for e in new_events if ar(e.get('title') or '') <= 0.5]
print(f'新建中英文待译: {len(en_new)}, 已中文: {len(zh_new)}')

out = []
for e in new_events:
    out.append({
        'id': e['id'], 'date': e['date'],
        'title': e.get('title', ''),
        'summary': (e.get('summary') or '')[:260],
        'src': [{'name': s['name'], 'date': s.get('date',''), 'title': (s.get('title') or '')[:100], 'url': s.get('url','')} for s in e.get('sources', [])],
        'country': (e.get('location') or {}).get('country',''),
        'region': (e.get('location') or {}).get('region',''),
        'category': e.get('category', []),
    })
json.dump(out, open(r'D:\WorkBuddy\projects\world-events\_new_events.json', 'w', encoding='utf-8'),
          ensure_ascii=False, indent=1)
# 更新的旧事件：只看新增的来源
upd_out = []
for e in updated:
    added = [s for s in e.get('sources', []) if s.get('url') not in old_urls]
    upd_out.append({'id': e['id'], 'title': e.get('title',''), 'added_sources': [
        {'name': s['name'], 'title': (s.get('title') or '')[:100]} for s in added]})
json.dump(upd_out, open(r'D:\WorkBuddy\projects\world-events\_updated_events.json', 'w', encoding='utf-8'),
          ensure_ascii=False, indent=1)
print('已导出 _new_events.json / _updated_events.json')
