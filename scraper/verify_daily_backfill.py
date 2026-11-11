# -*- coding: utf-8 -*-
"""逐日存档验收：检查目标年份各月产出条数是否达标。

背景（2026-09-15 事故）：免费 flash 账户限流导致翻译大面积静默失败，
run 显示 success 但每月只剩 9-20 条（正常 100-500 条）。
本脚本用于每次回填后自动验收，不达标即报警（退出码 1），
可接入 workflow 步骤实现"产出不达标即失败告警"。

用法：python scraper/verify_daily_backfill.py [最低条数阈值，默认 50]

阈值按年份分层（2026-11-06）：
  维基 Portal:Current_events 的逐日页在 2008 年前本身就很稀疏（一个月常只有十几条），
  统一用 50 会把"数据本来就少"误判成"翻译静默失败"，进而中断 job、数据不落库。
  故 2008 年前用 EARLY_MIN（默认 10），2008 年起仍用 50——真失败（0-5 条）两种情况都会被抓。
"""
import collections
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROGRESS_FILE = os.path.join(ROOT, "data", "backfill_daily_progress.json")


def month_counts_from_shards():
    """逐分片统计每月事件数（真源 data/events/*.json），不整库载入内存。

    2026-11-11：全库 12 万条后 data/events.json 达 241MB，json.load 会 MemoryError
    （本机可用内存约 4GB）。分片是入库真源，用它统计更准确、内存只占单片。
    """
    cnt = collections.Counter()
    total = 0
    for p in sorted(glob.glob(os.path.join(ROOT, "data", "events", "*.json"))):
        if os.path.basename(p) == "index.json":
            continue
        try:
            with open(p, encoding="utf-8") as f:
                rows = json.load(f)
        except Exception:
            continue
        if not isinstance(rows, list):
            continue
        for e in rows:
            total += 1
            d = (e.get("date") or "")[:7]
            if d:
                cnt[d] += 1
    return cnt, total


DEFAULT_MIN_PER_MONTH = 50
MIN_PER_MONTH = DEFAULT_MIN_PER_MONTH   # 运行期可被命令行参数覆盖（见 main）
EARLY_MIN = 10          # 2008 年前逐日页稀疏，降低阈值避免误报
EARLY_UNTIL = 2008      # 不含该年
MIN_OUTPUT_RATIO = 0.5  # 有源条目数时，入库数至少达到源的 50%（翻译整体失败通常只剩 0-20%）


def _parse_min_per_month(argv):
    """安全解析首个参数；非数字时回退默认值。

    不能写成模块级 `int(sys.argv[1])`：被 pytest 等工具 import 时 argv 是工具自己的参数，
    会直接 ValueError 导致整组测试无法收集（2026-11-06 修复）。
    """
    if len(argv) > 1:
        try:
            return int(argv[1])
        except ValueError:
            pass
    return DEFAULT_MIN_PER_MONTH


def threshold_for(ym: str) -> int:
    """按月所属年份返回达标阈值。

    只认 'YYYY-MM' 这种 4 位年份；'99'、'abc'、None 等畸形输入一律回退常规阈值，
    避免把 2 位数字误当成年份而放宽标准。
    """
    if not isinstance(ym, str) or len(ym) < 4 or not ym[:4].isdigit():
        return MIN_PER_MONTH
    return EARLY_MIN if int(ym[:4]) < EARLY_UNTIL else MIN_PER_MONTH


def main():
    global MIN_PER_MONTH
    MIN_PER_MONTH = _parse_min_per_month(sys.argv)
    cnt, total_events = month_counts_from_shards()

    try:
        cfg = json.load(open(os.path.join(ROOT, "data", "backfill_daily_targets.json"), encoding="utf-8"))
        years = [str(y) for y in cfg.get("years", [])]
    except Exception:
        years = []
    try:
        prog = json.load(open(PROGRESS_FILE, encoding="utf-8"))
        done = set(prog.get("done_months", []))
        empty = set(prog.get("empty_months", []))   # 无源数据的月份，不计入不达标
        src = prog.get("source_counts", {}) or {}   # 各月源条目数，用于相对校验
    except Exception:
        prog = {"done_months": [], "empty_months": [], "source_counts": {}}
        done, empty, src = set(), set(), {}

    print(f"验收范围: {years} | 已完成月份: {len(done)} | 无源数据: {len(empty)} | 库中事件总数: {total_events}")
    print(f"阈值: {EARLY_UNTIL} 年前 ≥{EARLY_MIN} 条/月，{EARLY_UNTIL} 年起 ≥{MIN_PER_MONTH} 条/月")
    problems = []
    for y in years:
        for m in range(1, 13):
            ym = f"{y}-{m:02d}"
            if ym not in done:
                continue  # 未标记完成的不算问题（可能还没跑）
            if ym in empty:
                continue  # 该月维基无源数据，属正常，不算产出异常
            # 账货一致性：标记 done 的月份，库里必须真的有该月数据。
            # 2026-11-07 事故：进度记了 done，但 events.json 是 gitignore 工作副本，
            # 真源分片没 split，runner 一销毁 831 条全丢——这里直接把"账有货无"拦下来。
            n_month = cnt.get(ym, 0)
            if n_month == 0:
                problems.append((ym, 0, "已标记完成但库中该月 0 条（账货不一致，数据可能未落库）"))
                continue
            n = cnt.get(ym, 0)
            s = src.get(ym)
            if isinstance(s, int) and s > 0:
                # 相对校验优先：入库数应达到源条目的一定比例，
                # 这样早年稀疏月份（源本来只有几条）不会被误判。
                need = max(1, int(s * MIN_OUTPUT_RATIO))
                reason = f"应 ≥{need}（源 {s} 条，入库率 {n/s:.0%}，要求 ≥{MIN_OUTPUT_RATIO:.0%}）"
            else:
                need = threshold_for(ym)
                reason = f"应 ≥{need}（按年份阈值，无源条目记录）"
            if n < need:
                problems.append((ym, n, reason))

    if problems:
        print(f"\n{len(problems)} 个已标记完成的月份产出异常:")
        for ym, n, reason in problems:
            print(f"  {ym}: 实际 {n} 条，{reason}")

        # 自愈（2026-11-11）：把产出异常的月份从 done_months 撤销，
        # 让下一轮 auto/continuous 自动重跑，而不是每次 cron 都判 failure
        # 却什么都不做（此前连续 5 次 failure 就是这个死循环）。
        # 重试上限 3 次：反复失败的月份不再无限撤销，避免刷爆免费模型额度。
        MAX_RETRY = 3
        retries = prog.get("retry_counts", {}) or {}
        revoked, exhausted = [], []
        for ym, _n, _reason in problems:
            times = int(retries.get(ym, 0)) + 1
            retries[ym] = times
            if times > MAX_RETRY:
                exhausted.append(ym)
                continue
            if ym in done:
                done.discard(ym)
                revoked.append(ym)
        prog["retry_counts"] = retries
        prog["done_months"] = sorted(done)
        prog.setdefault("auto_revoked_months", [])
        for ym in revoked:
            if ym not in prog["auto_revoked_months"]:
                prog["auto_revoked_months"].append(ym)
        try:
            with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
                json.dump(prog, f, ensure_ascii=False, indent=1)
        except Exception as ex:
            print(f"进度写回失败: {type(ex).__name__}: {ex}", flush=True)
            sys.exit(1)
        print(f"\n已自动撤销 {len(revoked)} 个假完成月份，等待重跑"
              f"（重试次数已记录，上限 {MAX_RETRY}）")
        if exhausted:
            print(f"已达重试上限、不再自动撤销: {', '.join(sorted(exhausted))}")
            print("这些月份需人工检查数据源或模型额度。")
            sys.exit(1)
        if revoked:
            # 本轮已安排重跑，不算失败；让后续 rescore/build/split 正常执行
            print("本轮无其它问题，交由守护补位重跑。")
            return 0
        sys.exit(1)
    print(f"\n所有已完成月份产出达标（按年份分层阈值）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
