# -*- coding: utf-8 -*-
"""监控 backfill_daily run（含 push 阶段状态）"""
import json, time, requests, sys

PAT = None
with open(r"C:\Users\Lenovo\.git-credentials", "r", encoding="utf-8") as f:
    for line in f:
        if "github.com" in line and "@" in line:
            seg = line.strip().split("://", 1)[1]
            token = seg.split("@", 1)[0].split(":", 1)[1]
            if token:
                PAT = token
                break

run_id = sys.argv[1]
API = "https://api.github.com/repos/yongweili412/world-events/actions"
H = {"Authorization": "token " + PAT}
JOB = None

for i in range(140):
    try:
        r = requests.get(API + "/runs/" + run_id, headers=H, timeout=30)
        d = r.json()
        status, concl = d.get("status"), d.get("conclusion")
        if status == "completed":
            print(time.strftime("%H:%M:%S"), "FINAL:", concl, flush=True)
            sys.exit(0 if concl == "success" else 2)
        if i % 4 == 3:
            print(time.strftime("%H:%M:%S"), status, flush=True)
            if JOB is None:
                jr = requests.get(API + "/runs/" + run_id + "/jobs", headers=H, timeout=30)
                jobs = jr.json().get("jobs", [])
                JOB = jobs[0]["id"] if jobs else None
            if JOB:
                lr = requests.get(API + "/jobs/" + str(JOB) + "/logs", headers=H, timeout=60)
                if lr.status_code == 200:
                    keys = ("📅", "✅", "🌐", "翻译进度", "PUSH", "push 被拒", "Traceback")
                    lines = [l for l in lr.text.splitlines() if any(k in l for k in keys)]
                    for l in lines[-5:]:
                        print("   LOG:", l.split("Z ", 1)[-1][:112], flush=True)
    except Exception as ex:
        print("  poll error:", type(ex).__name__, flush=True)
    time.sleep(90)
print("TIMEOUT")
sys.exit(3)
