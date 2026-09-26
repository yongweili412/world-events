# -*- coding: utf-8 -*-
"""字段级中文化：翻译事件内 sources[].title/snippet、timeline[].text、description 的英文残留。
全中文红线工具——每日流水线在云端/本机运行，多跑几轮自动收敛。

省 token 优化（2026-09-19）：
  1. 同源去重：description / sources[].snippet / timeline[].text 常是同一段新闻（实测同源率 80%+），
     规范化后只翻一次、回填多处，避免同条新闻被翻 2-3 次。
  2. 结果缓存：唯一文本先查 llm_cache（跨轮次/跨本机与云端共享），命中则零 token 消耗。
  3. max_tokens 封顶：翻译输出限 512 tokens，避免模型超长输出。
"""
import json, re, time, os, sys, requests
from llm_guard import resolve_model, chat_raw  # 模型守卫：限时免费优先，其次 flash；禁用 GLM-5.3/KIMI K3
from llm_cache import cache_get, cache_set, cache_save, stats as cache_stats

KEY = os.environ.get("LLM_API_KEY", "995611b2762144e88e023c856da104eb.d68AuncjSy9Qrd0O").strip()
BATCH = 12
TASK = "ft_translate"      # 缓存任务名（隔离不同任务的缓存）
MAX_TOKENS = 3000          # 翻译输出上限（12 条 × ~250 tokens，留足余量防截断）

def has_en(t):
    """判断文本是否为"需要翻译的英文块"。

    判据与 validate.py 的 is_en_text 对齐：英文字母数 > 20 且中文字数 < 英文字母数 * 0.3。
    注意：不要用"英文单词数 ≥3"这类宽松启发式——含 CNN / NASA / SpaceX / Politico 等
    品牌名的中文句子会被误判为英文块，导致每轮流水线对无需翻译的中文文本空转调用 LLM
    （2026-09-19 实测：宽松判据 559 块中 557 块为误判，真英文仅 2 块）。
    """
    t = t or ""
    en = len(re.findall(r"[A-Za-z]", t))
    zh = len(re.findall(r"[\u4e00-\u9fff]", t))
    return en > 20 and zh < en * 0.3

def norm_text(t):
    """规范化文本：压缩空白，使仅空格差异的文本视为同一份（提升去重与缓存命中率）。"""
    return re.sub(r"\s+", " ", (t or "")).strip()

def call(prompt, timeout=120):
    # 限时免费模型优先，遇 429/5xx 自动换档；输出限长
    return chat_raw(prompt, key=KEY, timeout=timeout, temperature=0.2, max_tokens=MAX_TOKENS)

PROMPT_HEAD = """你是新闻编辑，把下列英文新闻文本翻译成简体中文（用于"世界事件档案"资料库）。
要求：规范中文新闻译名；战争冲突灾难用中性词（遇难/身亡/死亡人数/武装冲突/爆炸袭击），平实陈述；专有名词/品牌名可保留英文；字符串内禁止双引号。输出严格 JSON 数组：[{"i":序号,"zh":"译文"}]，不要输出 JSON 以外文字。

文本列表：
"""

def main():
    if not KEY:
        print("未配置 LLM_API_KEY，跳过")
        return
    ev = json.load(open("data/events.json", encoding="utf-8"))
    blocks = []  # (event_idx, kind, idx, text)
    for ei, e in enumerate(ev):
        if has_en(e.get("description") or ""):
            blocks.append((ei, "desc", 0, e["description"]))
        for si, s in enumerate(e.get("sources") or []):
            if has_en(s.get("title") or ""):
                blocks.append((ei, "src_title", si, s["title"]))
            if has_en(s.get("snippet") or ""):
                blocks.append((ei, "src_snip", si, s["snippet"]))
        for ti, t in enumerate(e.get("timeline") or []):
            if has_en(t.get("text") or ""):
                blocks.append((ei, "tl", ti, t["text"]))
    print("待翻译文本块:", len(blocks), flush=True)
    if not blocks:
        print("NOTHING")
        return

    # 新事件优先（后续限量与排序都基于此顺序）
    blocks.sort(key=lambda b: ev[b[0]].get("date", ""), reverse=True)

    # ── 第 4 条：同源去重。同一段文本（规范化后）只翻一次，结果回填所有引用位置 ──
    uniq = {}   # norm_text -> {"orig": 原文, "refs": [(ei, kind, idx)], "zh": 译文}
    for (ei, kind, idx, text) in blocks:
        k = norm_text(text)
        if not k:
            continue
        ent = uniq.get(k)
        if ent is None:
            uniq[k] = {"orig": text, "refs": [(ei, kind, idx)], "zh": None}
        else:
            ent["refs"].append((ei, kind, idx))
    print(f"去重后唯一文本 {len(uniq)} 条（同源重复 {len(blocks) - len(uniq)} 块免翻）", flush=True)

    # ── 第 3 条：先查缓存。命中即零 token 消耗 ──
    n_cache = 0
    pending = []   # [(k, ent)] 待调用 LLM
    for k, ent in uniq.items():
        hit = cache_get(TASK, k)
        if hit:
            ent["zh"] = hit
            n_cache += 1
        else:
            pending.append((k, ent))
    print(f"缓存命中 {n_cache} 条，待调用 LLM {len(pending)} 条（缓存共 {cache_stats()['entries']} 条）", flush=True)

    # 防超时限量：每轮最多 N 条待翻（可传参覆盖，默认 240），新事件优先，多轮自然收敛
    cap = int(sys.argv[1]) if len(sys.argv) > 1 else 240
    if len(pending) > cap:
        pending = pending[:cap]
        print(f"每轮限量 {cap} 条，新事件优先（本轮处理），其余留待后续轮次", flush=True)

    n_ok = n_cache
    n_batches = (len(pending) + BATCH - 1) // BATCH
    for i in range(0, len(pending), BATCH):
        batch = pending[i:i + BATCH]
        payload = [{"i": j, "en": (ent["orig"][:280])} for j, (k, ent) in enumerate(batch)]
        r = None
        got = {}
        for attempt in (1, 2):
            try:
                r = call(PROMPT_HEAD + json.dumps(payload, ensure_ascii=False))
            except Exception as ex:
                print(f"  网络异常: {type(ex).__name__}", flush=True)
                r = None
            if r is not None and r.status_code == 200:
                content = r.json()["choices"][0]["message"].get("content") or ""
                m = re.search(r"\[.*\]", content, re.S)
                if m:
                    try:
                        arr = json.loads(m.group(0))
                        got = {a.get("i"): str(a.get("zh", "")).strip() for a in arr if isinstance(a, dict) and a.get("zh")}
                    except Exception:
                        # JSON 无效（截断/裸引号）→ 对半拆分重试
                        if len(batch) > 1:
                            def sr(b, depth=0):
                                if not b:
                                    return {}
                                rr = call(PROMPT_HEAD + json.dumps([{"i": j, "en": x[1]["orig"][:280]} for j, x in enumerate(b)], ensure_ascii=False))
                                g = {}
                                if rr.status_code == 200:
                                    mm = re.search(r"\[.*\]", rr.json()["choices"][0]["message"].get("content") or "", re.S)
                                    if mm:
                                        try:
                                            g = {a.get("i"): str(a.get("zh", "")).strip() for a in json.loads(mm.group(0)) if isinstance(a, dict) and a.get("zh")}
                                        except Exception:
                                            g = {}
                                if g or len(b) == 1:
                                    return g
                                m2 = len(b) // 2
                                a1 = sr(b[:m2], depth + 1)
                                # 右半的序号要平移
                                a2raw = sr(b[m2:], depth + 1)
                                a2 = {k2 + m2: v for k2, v in a2raw.items()}
                                g = {**a1, **a2}
                                return g
                            got = sr(batch)
                        break
                else:
                    break  # 无数组 → 不再重试该批
            if r is not None and "1301" in r.text:
                # 内容过滤 → 拆单条
                if len(batch) > 1:
                    for j, (k, ent) in enumerate(batch):
                        try:
                            r2 = call(PROMPT_HEAD + json.dumps([{"i": 0, "en": ent["orig"][:280]}], ensure_ascii=False))
                        except Exception:
                            continue
                        if r2.status_code == 200:
                            mm = re.search(r"\[.*\]", r2.json()["choices"][0]["message"].get("content") or "", re.S)
                            if mm:
                                try:
                                    arr = json.loads(mm.group(0))
                                    for a in arr:
                                        if isinstance(a, dict) and a.get("zh"):
                                            got[j] = str(a["zh"]).strip()
                                except Exception:
                                    pass
                    break
                else:
                    print(f"  批次 {i//BATCH+1} 单条被过滤，跳过", flush=True)
                    break
            time.sleep(4)
        # 写缓存
        for j, (k, ent) in enumerate(batch):
            zh = got.get(j)
            if zh:
                ent["zh"] = zh
                cache_set(TASK, k, zh)
        if i % (BATCH * 5) == 0:
            print(f"  进度 {i//BATCH+1}/{n_batches}: 累计 {n_ok}", flush=True)
        time.sleep(2)

    # ── 回填：一份译文写入所有引用位置（desc / src_snip / tl 通用） ──
    for k, ent in uniq.items():
        zh = ent.get("zh")
        if not zh:
            continue
        for (ei, kind, idx) in ent["refs"]:
            e = ev[ei]
            try:
                if kind == "desc":
                    e["description"] = zh
                elif kind == "src_title":
                    e["sources"][idx]["title"] = zh
                elif kind == "src_snip":
                    e["sources"][idx]["snippet"] = zh
                elif kind == "tl":
                    e["timeline"][idx]["text"] = zh
                if not ent.get("_counted"):
                    n_ok += 1
                    ent["_counted"] = True
            except Exception:
                pass

    cache_save()
    json.dump(ev, open("data/events.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    left = 0
    for e in ev:
        if has_en(e.get("description") or ""):
            left += 1
        for s in e.get("sources") or []:
            if has_en(s.get("title") or ""):
                left += 1
    print(f"DONE: 翻译 {n_ok} 条唯一文本（缓存命中 {n_cache}），剩余英文块(desc+src_title 粗计) {left}")
    print(f"缓存: {cache_stats()}")

if __name__ == "__main__":
    main()
