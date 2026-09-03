#!/usr/bin/env python3
"""把 events.json 里尚是英文的历史事件补翻成中文（带缓存，可断点续跑）"""
import json
import re
from pathlib import Path

import scraper
from scraper import EVENTS_FILE, translate_text, _load_translate_cache, _save_translate_cache


def looks_english(text: str) -> bool:
    """判断文本是否主要是英文（中文字符占比很低）"""
    if not text:
        return False
    zh = len(re.findall(r"[\u4e00-\u9fff]", text))
    return zh / max(len(text), 1) < 0.15


def main():
    _load_translate_cache()
    with open(EVENTS_FILE, "r", encoding="utf-8") as f:
        events = json.load(f)

    todo = [e for e in events
            if not e.get("translated") and looks_english(e.get("title", ""))]
    print(f"待翻译：{len(todo)} 条（共 {len(events)} 条）")

    done = 0
    for i, ev in enumerate(todo, 1):
        orig_title = ev.get("title", "")
        t = translate_text(orig_title)
        s = translate_text(ev.get("summary", "") or ev.get("content", ""))
        changed = t != orig_title
        if changed:
            ev["title"] = t
        if s and s != (ev.get("summary") or ""):
            ev["summary"] = s[:200]
            if ev.get("content") and looks_english(ev["content"]):
                ev["content"] = s
        # 只有真的翻成功才标记，失败的下次会自动重试
        ev["translated"] = bool(changed or not looks_english(orig_title))
        done += 1
        if i % 10 == 0:
            # 每 10 条落盘一次，中断也不丢进度
            with open(EVENTS_FILE, "w", encoding="utf-8") as f:
                json.dump(events, f, ensure_ascii=False, indent=2)
            _save_translate_cache()
            print(f"  进度 {i}/{len(todo)}，已落盘")
        print(f"  [{i}] {ev['title'][:45]}")

    with open(EVENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False, indent=2)
    _save_translate_cache()
    print(f"✅ 完成，处理 {done} 条，当前共 {len(events)} 条事件")
    print(f"🌐 累计翻译接口调用：{scraper._translate_calls} 次")
    print("💡 若部分仍是英文，多为当日免费额度用尽，改天重跑本脚本即可（已缓存的不重复翻译）")


if __name__ == "__main__":
    main()
