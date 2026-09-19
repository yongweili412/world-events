# -*- coding: utf-8 -*-
"""统一 LLM 结果缓存（节省 token 的核心机制）。

目的：同一条文本/同一个事件不重复调用 LLM——跨脚本、跨轮次、跨本机与云端共享。

设计：
  key   = md5(f"{task}:{规范化文本}")，task 隔离不同任务（避免翻译结果被速览复用）
  存储  = scraper/llm_cache.json（提交 git，云端与本机共享）
  淘汰  = LRU，上限 MAX_ENTRIES 条，超出时丢弃最久未使用的
  体积  = value 截断 MAX_VALUE_LEN 字符

用法：
    from llm_cache import cache_get, cache_set, cache_save, chat_cached

    # 方式一：手动查/写
    hit = cache_get("ft_translate", text)
    if hit is None:
        ...调 LLM...
        cache_set("ft_translate", text, zh)

    # 方式二：包装调用（推荐）
    content, from_cache = chat_cached("ft_summary", prompt, key=KEY, max_tokens=600)

批量任务结束时调用 cache_save() 落盘（模块每 AUTOSAVE_EVERY 次写入也会自动落盘一次）。
"""
import hashlib
import json
import os
import re
import threading

CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "llm_cache.json")
MAX_ENTRIES = 5000          # LRU 上限
MAX_VALUE_LEN = 1000        # 单条缓存值截断长度
AUTOSAVE_EVERY = 50         # 每 N 次写入自动落盘一次（防进程被杀丢缓存）

_lock = threading.Lock()
_cache = None               # {"key": {"v": 值, "seq": 序号}}
_seq = 0
_dirty = 0


def _norm(text: str) -> str:
    """规范化文本：压缩空白 + 去首尾，避免仅空格差异导致缓存未命中。"""
    return re.sub(r"\s+", " ", (text or "")).strip()


def _key(task: str, text: str) -> str:
    return hashlib.md5(f"{task}:{_norm(text)}".encode("utf-8")).hexdigest()


def _load():
    global _cache, _seq
    if _cache is not None:
        return _cache
    try:
        raw = json.load(open(CACHE_PATH, encoding="utf-8"))
        entries = raw.get("entries") if isinstance(raw, dict) else None
        if isinstance(entries, dict):
            _cache = entries
            _seq = int(raw.get("seq") or 0)
        else:
            _cache, _seq = {}, 0
    except Exception:
        # 文件不存在/损坏 → 静默降级为"无缓存"，不影响主流程
        _cache, _seq = {}, 0
    return _cache


def cache_get(task: str, text: str):
    """命中返回缓存值，未命中返回 None。"""
    with _lock:
        c = _load()
        ent = c.get(_key(task, text))
        if not isinstance(ent, dict):
            return None
        global _seq
        _seq += 1
        ent["seq"] = _seq          # 刷新使用时间（LRU）
        return ent.get("v")


def cache_set(task: str, text: str, value: str):
    """写入缓存并维护 LRU。"""
    if not value:
        return
    global _seq, _dirty
    with _lock:
        c = _load()
        _seq += 1
        c[_key(task, text)] = {"v": str(value)[:MAX_VALUE_LEN], "seq": _seq}
        if len(c) > MAX_ENTRIES:
            # 丢弃最久未使用的条目
            for k, _ in sorted(c.items(), key=lambda kv: kv[1].get("seq", 0))[: len(c) - MAX_ENTRIES]:
                c.pop(k, None)
        _dirty += 1
        if _dirty >= AUTOSAVE_EVERY:
            _write()


def cache_save():
    """显式落盘（批量任务结束时调用）。"""
    with _lock:
        _write()


def _write():
    """内部落盘（调用方需持锁）。原子写，避免进程中断损坏缓存文件。"""
    global _dirty
    if _cache is None or not _dirty:
        return
    try:
        tmp = CACHE_PATH + ".tmp"
        json.dump({"version": 1, "seq": _seq, "entries": _cache},
                  open(tmp, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, CACHE_PATH)
        _dirty = 0
    except Exception:
        pass  # 落盘失败不影响主流程


def stats() -> dict:
    with _lock:
        c = _load()
        return {"entries": len(c), "max": MAX_ENTRIES}


def chat_cached(task: str, prompt: str, *, key: str = None, base: str = None, timeout: int = 120,
                temperature: float = 0.3, max_tokens: int = None, use_cache: bool = True):
    """带缓存的 LLM 调用。返回 (content | None, from_cache: bool)。

    命中缓存直接返回，不产生任何 token 消耗；未命中才调 llm_guard.chat_raw。
    """
    if use_cache:
        hit = cache_get(task, prompt)
        if hit:
            return hit, True
    from llm_guard import chat_raw  # 延迟导入，避免循环依赖
    r = chat_raw(prompt, key=key, base=base, timeout=timeout,
                 temperature=temperature, max_tokens=max_tokens)
    if r is None or r.status_code != 200:
        return None, False
    content = (r.json()["choices"][0]["message"].get("content") or "").strip()
    content = re.sub(r"^```[a-z]*\n?|```$", "", content, flags=re.M).strip()
    if content and use_cache:
        cache_set(task, prompt, content)
    return content or None, False


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "stats":
        print("缓存条目:", stats())
    else:
        print("用法: python scraper/llm_cache.py stats")
