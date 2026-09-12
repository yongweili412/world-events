# -*- coding: utf-8 -*-
"""字段级中文化：翻译事件内 sources[].title/snippet、timeline[].text、description 的英文残留。
全中文红线工具——每日流水线在云端/本机运行，多跑几轮自动收敛。"""
import json, re, time, os, requests

KEY = os.environ.get("LLM_API_KEY", "995611b2762144e88e023c856da104eb.d68AuncjSy9Qrd0O").strip()
BATCH = 12

def has_en(t):
    return len(re.findall(r"[A-Za-z]{4,}", t or "")) >= 3

def call(prompt, timeout=120):
    return requests.post(
        "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"},
        json={
            "model": os.environ.get("LLM_MODEL", "glm-4.5-flash"),
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "thinking": {"type": "disabled"},
        },
        timeout=timeout,
    )

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

    n_ok = 0
    n_batches = (len(blocks) + BATCH - 1) // BATCH
    for i in range(0, len(blocks), BATCH):
        batch = blocks[i:i + BATCH]
        payload = [{"i": j, "en": (b[3][:280])} for j, b in enumerate(batch)]
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
                            mid = len(batch) // 2
                            def sr(b, depth=0):
                                if not b:
                                    return {}
                                rr = call(PROMPT_HEAD + json.dumps([{"i": j, "en": (x[3][:280])} for j, x in enumerate(b)], ensure_ascii=False))
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
                                a2raw = sr([(0, x[1], x[2], x[3]) for x in b[m2:]], depth + 1)
                                a2 = {k + m2: v for k, v in a2raw.items()}
                                g = {**a1, **a2}
                                return g
                            got = sr(batch)
                        break
                else:
                    break  # 无数组 → 不再重试该批
            if r is not None and "1301" in r.text:
                # 内容过滤 → 拆单条
                if len(batch) > 1:
                    for j, b in enumerate(batch):
                        try:
                            r2 = call(PROMPT_HEAD + json.dumps([{"i": 0, "en": b[3][:280]}], ensure_ascii=False))
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
        # 应用
        for j, (ei, kind, idx, _old) in enumerate(batch):
            zh = got.get(j)
            if not zh:
                continue
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
                n_ok += 1
            except Exception:
                pass
        if i % (BATCH * 5) == 0:
            print(f"  进度 {i//BATCH+1}/{n_batches}: 累计 {n_ok}", flush=True)
        time.sleep(2)

    json.dump(ev, open("data/events.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    left = 0
    for e in ev:
        if has_en(e.get("description") or ""):
            left += 1
        for s in e.get("sources") or []:
            if has_en(s.get("title") or ""):
                left += 1
    print(f"DONE: 翻译 {n_ok} 块，剩余英文块(desc+src_title 粗计) {left}")

if __name__ == "__main__":
    main()
