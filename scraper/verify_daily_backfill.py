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
import json
import sys

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
    ev = json.load(open("data/events.json", encoding="utf-8"))
    # 按月份统计该月全部事件，不再靠来源名过滤。
    # 原因（2026-11-07 事故）：新回填事件若有独立引用，来源名是域名（如 bbc.co.uk），
    # 只有少数无引用的才叫「当日新闻存档 YYYY-MM」。按旧口径只数到 51 条，
    # 把实际入库的 831 条误判为「产出不达标」，导致后续 build/split/commit 全被跳过。
    # 是否真失败由 source_counts 的相对校验判定（入库数 vs 源条目数）。
    cnt = collections.Counter(e["date"][:7] for e in ev if e.get("date"))

    try:
        cfg = json.load(open("data/backfill_daily_targets.json", encoding="utf-8"))
        years = [str(y) for y in cfg.get("years", [])]
    except Exception:
        years = []
    try:
        prog = json.load(open("data/backfill_daily_progress.json", encoding="utf-8"))
        done = set(prog.get("done_months", []))
        empty = set(prog.get("empty_months", []))   # 无源数据的月份，不计入不达标
        src = prog.get("source_counts", {}) or {}   # 各月源条目数，用于相对校验
    except Exception:
        done, empty, src = set(), set(), {}

    print(f"验收范围: {years} | 已完成月份: {len(done)} | 无源数据: {len(empty)} | 库中事件总数: {len(ev)}")
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
        print(f"\n⚠️ {len(problems)} 个已标记完成的月份产出异常:")
        for ym, n, reason in problems:
            print(f"  {ym}: 实际 {n} 条，{reason}")
        print("\n可能原因：API 限流导致翻译静默失败 / 抓取失败。请查日志并按需重跑（从 progress 移除该月）。")
        sys.exit(1)
    print(f"\n✅ 所有已完成月份产出达标（按年份分层阈值）")


if __name__ == "__main__":
    main()
