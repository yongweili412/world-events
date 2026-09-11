# -*- coding: utf-8 -*-
"""积压清理第二步：批量翻译库中英文事件（智谱 glm-4.5-flash，云端/本机通用）"""
import json, re, time, requests

def has_latin(t):
    w = re.findall(r"[A-Za-z]{3,}", t or "")
    return len(w) >= max(1, len(re.findall(r"[\u4e00-\u9fff]", t or "")) // 2)


def _call(cfg, prompt, timeout=300):
    return requests.post(
        cfg["base"] + "/chat/completions",
        headers={"Authorization": "Bearer " + cfg["key"], "Content-Type": "application/json"},
        json={
            "model": cfg["model"],
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "thinking": {"type": "disabled"},
        },
        timeout=timeout,
    )


def _payload(e):
    return {"id": e["id"], "title_en": e["title"], "summary_en": (e.get("summary") or "")[:300]}


def _build_prompt(items_json):
    return """你是新闻编辑，为"世界事件档案"网站把下列英文新闻事件翻译成中文。输出严格的 JSON 数组，每个元素：
{"id": "原样照抄输入的id", "title": "30字内中文新闻标题", "summary": "80-150字中文摘要，基于原文内容"}
要求：
1. 专有名词用中文规范译名；标题陈述事实；不要输出 JSON 以外的任何文字；字符串内如需引号一律用《》或''，禁止使用双引号。
2. 战争、冲突、灾难、恐袭等事件均属公开新闻，必须如实收录，用新闻业规范中性词表述：遇难/身亡/死亡人数/武装冲突/交火/爆炸袭击，平实记录事实与数字，不渲染血腥细节、不用煽动性词汇。

条目列表：
""" + items_json


def _parse_batch(resp_json, batch, tag):
    content = resp_json["choices"][0]["message"].get("content") or ""
    jm = re.search(r"\[.*\]", content, re.S)
    if not jm:
        print(f"  WARN {tag} JSON 解析失败 | content头: {content[:100]!r}", flush=True)
        return []
    try:
        arr = json.loads(jm.group(0))
    except Exception:
        print(f"  WARN {tag} JSON 无效 | content头: {content[:100]!r}", flush=True)
        return []
    by_id = {}
    for a in arr:
        if isinstance(a, dict) and a.get("id") and a.get("title"):
            by_id[str(a["id"])] = a
    res = []
    for e in batch:
        a = by_id.get(e["id"])
        if a:
            res.append((e, str(a["title"]).strip(), str(a.get("summary", "")).strip()))
    return res


def translate_all(cfg, batch_size=10):
    """翻译 events.json 中全部英文事件，返回 (写库数, 剩余英文数)。直接写库。"""
    BATCH = batch_size
    ev = json.load(open("data/events.json", encoding="utf-8"))

    def _pending(events):
        return [e for e in events if has_latin(e.get("title")) and not (e.get("sources") or [{}])[0].get("name", "").startswith("Wikipedia")]

    targets = _pending(ev)
    print("待翻译英文事件:", len(targets), flush=True)
    if not targets:
        return 0, 0

    n_batches = (len(targets) + BATCH - 1) // BATCH
    results = []
    for i in range(0, len(targets), BATCH):
        batch = targets[i:i + BATCH]
        prompt = _build_prompt(json.dumps([_payload(e) for e in batch], ensure_ascii=False))
        resp = None
        for attempt in (1, 2):
            try:
                resp = _call(cfg, prompt)
            except Exception as ex:
                print(f"  WARN 网络异常（第 {attempt} 次）: {type(ex).__name__}", flush=True)
                resp = None
            if resp is not None and resp.status_code == 200:
                break
            if resp is not None:
                print(f"  WARN HTTP {resp.status_code}（第 {attempt} 次）: {resp.text[:150]}", flush=True)
            time.sleep(5)
        got = []
        if resp is not None and resp.status_code == 200:
            got = _parse_batch(resp.json(), batch, f"批次 {i//BATCH+1}/{n_batches}")
            if not got and len(batch) > 1:
                # 输出截断/无效 → 对半拆分递归重试
                def split_retry(b, depth=0):
                    if not b:
                        return []
                    print(f"    拆半重试（{len(b)} 条，深度 {depth}）", flush=True)
                    try:
                        r = _call(cfg, _build_prompt(json.dumps([_payload(e) for e in b], ensure_ascii=False)))
                    except Exception:
                        return []
                    if r.status_code != 200:
                        print(f"      WARN HTTP {r.status_code}", flush=True)
                        return []
                    g = _parse_batch(r.json(), b, f"拆半{depth}")
                    if g:
                        return g
                    if len(b) == 1:
                        return []
                    mid = len(b) // 2
                    return split_retry(b[:mid], depth + 1) + split_retry(b[mid:], depth + 1)
                got = split_retry(batch)
        elif resp is not None and "1301" in resp.text:
            print(f"  批次 {i//BATCH+1} 内容过滤，拆单条重试", flush=True)
            for e in batch:
                try:
                    r2 = _call(cfg, _build_prompt(json.dumps([_payload(e)], ensure_ascii=False)), timeout=120)
                except Exception:
                    continue
                if r2.status_code == 200:
                    got += _parse_batch(r2.json(), [e], "单条")
                elif "1301" in r2.text:
                    # 降敏二次重试
                    time.sleep(2)
                    try:
                        r3 = _call(cfg, _build_prompt(json.dumps([_payload(e)], ensure_ascii=False)) + "\n注意：这是公开新闻报道，请用最平实的新闻通稿语气表述。", timeout=120)
                    except Exception:
                        r3 = None
                    if r3 is not None and r3.status_code == 200:
                        got += _parse_batch(r3.json(), [e], "降敏重试")
                    else:
                        # 留档待补翻，不丢数据
                        try:
                            fl = json.load(open("data/llm_filtered.json", encoding="utf-8"))
                        except Exception:
                            fl = []
                        if not any(x["id"] == e["id"] for x in fl):
                            fl.append({"id": e["id"], "date": e["date"], "title_en": e["title"], "text_en": (e.get("summary") or "")[:400]})
                            json.dump(fl, open("data/llm_filtered.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
                        print(f"    单条被过滤，已留档待补翻: {e['title'][:40]!r}", flush=True)
        else:
            print(f"  FAIL 批次 {i//BATCH+1} 失败，跳过 {len(batch)} 条", flush=True)

        results.extend(got)
        print(f"  批次 {i//BATCH+1}/{n_batches}: 累计 {len(results)}", flush=True)
        time.sleep(2)

    # 统一写库
    by_id = {e["id"]: e for e in ev}
    n = 0
    for e, t, s in results:
        tgt = by_id.get(e["id"])
        if not tgt:
            continue
        tgt["title"] = t
        if s:
            tgt["summary"] = s
            if not tgt.get("aiSummary"):
                tgt["aiSummary"] = s
        n += 1
    json.dump(ev, open("data/events.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    left = len(_pending(ev))
    print(f"DONE: 翻译成功 {len(results)}，写入 {n}，剩余英文 {left}")
    return n, left


def main():
    import os
    cfg = {
        "key": os.environ.get("LLM_API_KEY", "").strip(),
        "base": os.environ.get("LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4").rstrip("/"),
        "model": os.environ.get("LLM_MODEL", "glm-4.5-flash"),
    }
    if not cfg["key"]:
        print("未配置 LLM_API_KEY，跳过")
        return
    translate_all(cfg)


if __name__ == "__main__":
    main()
