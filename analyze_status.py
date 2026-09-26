#!/usr/bin/env python3
"""分析当前数据库状态"""
import json
from collections import Counter

# 读取数据
with open('data/events.json', 'r', encoding='utf-8') as f:
    d = json.load(f)

print('=== 当前数据库状态 ===')
print(f'总事件数: {len(d)}')
print(f'已翻译: {len([e for e in d if e.get("translated")])}')
print(f'未翻译: {len([e for e in d if not e.get("translated") and e.get("title")])}')

# 分析未翻译事件
untranslated = [e for e in d if not e.get('translated') and e.get('title')]
print(f'\n=== 未翻译事件分析 ===')
print(f'未翻译事件数: {len(untranslated)}')

# 按类别统计
categories = Counter()
for e in untranslated:
    cats = e.get('category', [])
    for cat in cats:
        categories[cat] += 1

print('\n未翻译事件类别分布:')
for cat, count in categories.most_common():
    print(f'  {cat}: {count} 条')

# 显示一些未翻译事件示例
print('\n未翻译事件示例:')
for i, e in enumerate(untranslated[:5]):
    print(f'  {i+1}. {e.get("title", "无标题")[:60]}...')
    print(f'     类别: {e.get("category", [])}')

# 检查时间分布
dates = [e.get('date', '')[:7] for e in untranslated]
date_dist = Counter(dates)
print('\n未翻译事件时间分布:')
for month in sorted(date_dist.keys()):
    print(f'  {month}: {date_dist[month]} 条')

print('\n=== 下一步计划 ===')
print('1. 处理剩余未翻译的英文事件')
print('2. 继续抓取历史数据（2020-2022年）')
print('3. 优化数据质量和分类')
print('4. 完善网站功能')