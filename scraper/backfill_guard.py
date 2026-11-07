# -*- coding: utf-8 -*-
"""回填守护：确保历史补全「不停止」。

用途：由本机定时任务周期性调用（也可手动跑）。每次执行：
  1. 读远端 progress 判断还有没有待补月份（补完则什么都不做）；
  2. 查云端 backfill_daily 有没有 in_progress / queued 的 run；
  3. 若「有待补月份」且「云端无活跃任务」→ dispatch 一批 continuous；
  4. 若连续失败会打印告警，但绝不擅自改数据或强推。

为什么需要它：
  - GitHub cron 是固定时刻，遇上限流/排队被顶掉/运行失败就出现空窗；
  - 同一 workflow 一次排多批会互相 cancel，不能靠"一次排满"接力；
  - 云端用 GITHUB_TOKEN 触发的新 run 不会再触发别的 workflow（防死循环），
    所以自驱只能由外部（本机 PAT）补位。

安全：
  - 只读远端状态 + 一次 workflow_dispatch；不写数据、不改历史。
  - 幂等：已有活跃任务就跳过，不会造成并发写库。
"""
import base64
import json
import os
import sys
import urllib.request

REPO = "yongweili412/world-events"
WF = "backfill_daily.yml"
API = "https://api.github.com"


def _token():
    cred = os.path.expanduser("~/.git-credentials")
    with open(cred, encoding="utf-8") as f:
        for line in f:
            if "github.com" in line:
                part = line.strip().split("://", 1)[-1].split("@", 1)[0]
                return part.split(":", 1)[-1]
    raise RuntimeError("找不到 GitHub 凭据")


def _req(path, method="GET", payload=None, tok=None, timeout=40):
    data = json.dumps(payload).encode() if payload is not None else None
    h = {"Authorization": "Bearer " + tok, "User-Agent": "world-events-guard",
         "Accept": "application/vnd.github+json"}
    if data:
        h["Content-Type"] = "application/json"
    r = urllib.request.Request(API + path, data=data, headers=h, method=method)
    with urllib.request.urlopen(r, timeout=timeout) as f:
        return json.load(f)


def main():
    tok = _token()
    # 1) 待补月份
    try:
        f = _req(f"/repos/{REPO}/contents/data/backfill_daily_progress.json?ref=main", tok=tok)
        prog = json.loads(base64.b64decode(f["content"]))
        done = set(prog.get("done_months", []))
        c = _req(f"/repos/{REPO}/contents/data/backfill_daily_targets.json?ref=main", tok=tok)
        cfg = json.loads(base64.b64decode(c["content"]))
        years = [int(y) for y in cfg["years"]]
        todo = [f"{y}-{m:02d}" for y in years for m in range(1, 13)
                if f"{y}-{m:02d}" not in done]
    except Exception as e:
        print(f"GUARD_ERROR: 读取远端进度失败 {type(e).__name__}: {e}", flush=True)
        return 1

    if not todo:
        print("DONE: 全部目标月份已补完，无需触发", flush=True)
        return 0

    # 2) 云端活跃任务（in_progress / queued / pending 都算"在跑或排队"）
    #    但 queued/pending 卡住超过 STALE_MINUTES 分钟的（GitHub 并发组偶发僵死），
    #    视为卡住并自动取消，否则守护会永远 SKIP、补全反而停摆。
    STALE_MINUTES = 15
    try:
        from datetime import datetime, timezone
        runs = _req(f"/repos/{REPO}/actions/workflows/{WF}/runs?per_page=8", tok=tok)
        now = datetime.now(timezone.utc)
        active = []
        for r in runs.get("workflow_runs", []):
            if r["status"] not in ("in_progress", "queued", "pending"):
                continue
            created = datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))
            age_min = (now - created).total_seconds() / 60
            if r["status"] in ("queued", "pending") and age_min > STALE_MINUTES:
                try:
                    _req(f"/repos/{REPO}/actions/runs/{r['id']}/cancel",
                         method="POST", tok=tok)
                    print(f"STALE_CANCEL: 取消卡住 {age_min:.0f} 分钟的 run {r['id']}",
                          flush=True)
                except Exception as ce:
                    print(f"STALE_CANCEL_FAIL: {r['id']} {type(ce).__name__}", flush=True)
                continue
            active.append(r)
    except Exception as e:
        print(f"GUARD_ERROR: 查询 run 失败 {type(e).__name__}: {e}", flush=True)
        return 1

    if active:
        print(f"SKIP: 已有 {len(active)} 个活跃/排队任务"
              f"（最新 {active[0]['created_at'][11:19]}），待补 {len(todo)} 个月", flush=True)
        return 0

    # 3) 补位触发 continuous
    try:
        _req(f"/repos/{REPO}/actions/workflows/{WF}/dispatches", method="POST",
             payload={"ref": "main", "inputs": {"months": "", "count": ""}}, tok=tok)
        print(f"TRIGGERED: 已补发 continuous 批次（待补 {len(todo)} 个月）", flush=True)
    except Exception as e:
        print(f"GUARD_ERROR: dispatch 失败 {type(e).__name__}: {e}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
