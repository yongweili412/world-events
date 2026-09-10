# -*- coding: utf-8 -*-
"""backfill_history._parse_events_html 的离线测试（直接调用实现，永不脱节）"""
import sys
sys.path.insert(0, r'D:\WorkBuddy\projects\world-events\scraper')
import importlib.util
spec = importlib.util.spec_from_file_location('bf', r'D:\WorkBuddy\projects\world-events\scraper\backfill_history.py')
bf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bf)

CASES = [
 ('old-style-span-id',
  '<h2><span class="mw-headline" id="Events">Events</span></h2><ul><li>January 1 - Pascal Lloreda becomes president of some country.<sup>[1]</sup></li><li>February 1: Space Shuttle Columbia disintegrates during reentry, killing all seven astronauts.<sup>[2]</sup><ul><li>Sub-item should be ignored here</li></ul></li><li>March 20 - The United States and allies invade Iraq.</li></ul><h3><span class="mw-headline" id="Births">Births</span></h3><ul><li>January 1 - someone born later with a long text tail</li></ul>',
  3),
 ('new-style-h-id',
  '<h2 id="Events">Events</h2><ul><li>March 20 - War begins in the Gulf region after ultimatum expires worldwide.</li></ul><h3 id="April">April</h3><ul><li>April 9 - Baghdad falls to invading forces after days of heavy fighting.</li></ul><h2 id="Births">Births</h2><ul><li>May 5 - a baby was born somewhere in the world today</li></ul>',
  2),
 ('en-dash',
  '<h2 id="Events">Events</h2><ul><li>January 1 \u2013 The European Union expands to include ten new member states.</li></ul>',
  1),
 ('no-events',
  '<h2 id="Notable_people">Notable people</h2><ul><li>January 1 - someone notable was born a long time ago</li></ul>',
  0),
]

ok = True
for name, html, expect_n in CASES:
    got = bf._parse_events_html(html, 2003)
    leak = [it for it in got if 'Sub-item' in it['text_en'] or 'born later' in it['text_en'] or 'baby was born' in it['text_en']]
    status = 'PASS' if len(got) == expect_n and not leak else 'FAIL'
    if status == 'FAIL':
        ok = False
    print('== %s: %d 条 (期望 %d) | 泄漏 %d | %s' % (name, len(got), expect_n, len(leak), status))
    for it in got:
        print('   ', it['date'], '|', it['text_en'][:60])

print('RESULT:', 'PASS' if ok else 'FAIL')
sys.exit(0 if ok else 1)
