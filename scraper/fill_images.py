# -*- coding: utf-8 -*-
"""图片回填：对无 image 的事件抓取来源页 og:image 并写入。
本机默认只抓国内可达源；云端 ALL_SOURCES=1 可抓全部源。"""
import json, sys, time, os, requests

ALL_SOURCES = os.environ.get("ALL_SOURCES") == "1"
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 60
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"}
CN_HOSTS = ("chinanews.com.cn", "people.com.cn", "cctv.com", "thepaper.cn", "news.cn",
            "xinhuanet.com", "huanqiu.com", "ifeng.com", "jiemian.com", "cri.cn",
            "zaobao.com", "chinanews.com", "gmw.cn", "yicai.com", "cnbeta.com")

def main():
    sys.path.insert(0, "scraper")
    import scraper as sp

    ev = json.load(open("data/events.json", encoding="utf-8"))
    def is_cn(e):
        u = (e.get("sources") or [{}])[0].get("url") or ""
        return any(h in u for h in CN_HOSTS)
    targets = sorted(
        [e for e in ev if not e.get("image") and (e.get("sources") or [{}])[0].get("url") and (ALL_SOURCES or is_cn(e))],
        key=lambda e: e["date"], reverse=True,
    )[:LIMIT]
    print("待补图事件:", len(targets), "条" + ("" if ALL_SOURCES else "（仅国内源）"), flush=True)
    if not targets:
        print("NOTHING")
        return

    ok = 0
    for i, e in enumerate(targets):
        url = e["sources"][0]["url"]
        try:
            r = requests.get(url, headers=UA, timeout=12)
            if r.status_code != 200:
                continue
            if not r.encoding or r.encoding.lower() in ("iso-8859-1", "ascii"):
                r.encoding = r.apparent_encoding
            img = sp.extract_og_image(r.text, url)
            if img:
                e["image"] = img
                ok += 1
                print(f"  [{i+1}/{len(targets)}] ✓ {e['title'][:36]}", flush=True)
        except Exception:
            pass
        time.sleep(0.8)

    json.dump(ev, open("data/events.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    total = len([e for e in ev if e.get("image")])
    print(f"DONE: 补图 {ok}，全库有图事件 {total}/{len(ev)}")


def clean_duplicate_images():
    """清理站点默认图：同一图片 URL 被 ≥3 条不同事件共用 → 判定为占位/默认图，清空。"""
    import collections
    ev = json.load(open("data/events.json", encoding="utf-8"))
    cnt = collections.Counter(e["image"] for e in ev if e.get("image"))
    dups = {u for u, c in cnt.items() if c >= 3}
    if not dups:
        print("默认图清理: 无需处理")
        return
    n = 0
    for e in ev:
        if e.get("image") in dups:
            e["image"] = ""
            n += 1
    json.dump(ev, open("data/events.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"默认图清理: 移除 {n} 条（{len(dups)} 个重复 URL）")


if __name__ == "__main__":
    main()
    clean_duplicate_images()
