# -*- coding: utf-8 -*-
"""修复 scraper.py 中 COUNTRY_KEYWORDS 块内被误转的中文引号"""
import io, sys, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

path = r'D:\WorkBuddy\projects\world-events\scraper\scraper.py'
src = open(path, encoding='utf-8').read()

start = src.index('COUNTRY_KEYWORDS = {')
end = src.index('def guess_region', start)
block = src[start:end]

n_before = block.count('\u201c') + block.count('\u201d')
block_fixed = block.replace('\u201c', '"').replace('\u201d', '"')
src = src[:start] + block_fixed + src[end:]
open(path, 'w', encoding='utf-8').write(src)
print(f'已替换 {n_before} 个中文引号为 ASCII 引号（仅限 COUNTRY_KEYWORDS 块）')
