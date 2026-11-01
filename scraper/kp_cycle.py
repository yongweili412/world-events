# -*- coding: utf-8 -*-
"""三要点续跑周期（供定时任务每 2 小时调用一次）。

一个周期做的事：
  1) 取防重叠锁（避免上一轮没跑完时并行写 events.json）
  2) sync1：fetch 远端；若云端有推送造成分叉，以远端为基重建 events.json，
            再把本地已有的三要点按 id 覆盖回去（保云端新数据 + 保我的成果）
  3) 跑一批 keypoints_batch.py N（直连 conatus、零付费 spillover）
  4) sync2：再同步一次（批运行期间云端可能又推了），同样按 id 并集保护三要点
  5) split → commit → push（带重试）

设计原则（与项目记忆一致）：
  - 分类/数据绝不整篇重判，只做「按 id 覆盖三要点」这一种最小改动；
  - 分片是真源，events.json 是工作副本：sync 后从分片重建，split 前写回分片；
  - 推送走 github.com 直连（内嵌 token），代理间歇 502/SSL EOF 时重试。

用法：
  python scraper/kp_cycle.py [N]      # N 为本批条数，默认 50
"""
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
PY = sys.executable
N = int(sys.argv[1]) if len(sys.argv) > 1 else 50
LOCK = "data/.kp_cycle.lock"
LOCK_MAX_AGE = 3 * 3600          # 锁超过 3 小时视为陈旧，强制清理
EV = "data/events.json"


def log(msg):
    print(msg, flush=True)


# ---------- 锁 ----------
def acquire_lock():
    if os.path.exists(LOCK):
        age = time.time() - os.path.getmtime(LOCK)
        if age < LOCK_MAX_AGE:
            return False
        log(f"[lock] 清理陈旧锁({age/3600:.1f}h)")
        try:
            os.remove(LOCK)
        except OSError:
            pass
    with open(LOCK, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    return True


def release_lock():
    try:
        os.remove(LOCK)
    except OSError:
        pass


# ---------- git ----------
def sh(cmd, check=False, timeout=600):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"cmd failed: {cmd}\n{r.stdout}\n{r.stderr}")
    return r


def get_token():
    p = os.path.expanduser("~/.git-credentials")
    try:
        for line in open(p, encoding="utf-8"):
            if "github.com" not in line:
                continue
            m = re.search(r"https://([^@]+)@github\.com", line.strip())
            if m:
                cred = m.group(1)
                return cred.split(":")[-1] if ":" in cred else cred
    except Exception:
        pass
    return ""


# ---------- 三要点读写 ----------
def load_kp():
    try:
        ev = json.load(open(EV, encoding="utf-8"))
    except Exception:
        return {}, 0
    kp = {e["id"]: e["summary"] for e in ev
          if (e.get("summary") or "").startswith("1. ") and e.get("id")}
    return kp, len(ev)


def overlay_kp(kp):
    """把 kp 里的三要点按 id 覆盖到当前 events.json（只覆盖，不动其他字段）。"""
    ev = json.load(open(EV, encoding="utf-8"))
    byid = {e.get("id"): e for e in ev}
    applied = 0
    for i, s in kp.items():
        t = byid.get(i)
        if t is not None:
            t["summary"] = s
            applied += 1
    tmp = EV + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(ev, f, ensure_ascii=False)
        f.flush()
    os.replace(tmp, EV)
    return applied, len(ev)


def sync(tag):
    """fetch + 处理分叉，保证 events.json = 远端最新数据 + 本地全部三要点。"""
    sh("git fetch origin", timeout=300)
    r = sh("git rev-list --left-right --count HEAD...origin/main", timeout=60)
    try:
        ahead, behind = [int(x) for x in r.stdout.split()]
    except Exception:
        ahead, behind = 0, 0
    if behind == 0:
        return f"{tag}: 无分叉(ahead={ahead})"
    # 远端有新提交：备份三要点 → reset → 从远端分片重建 → 覆盖回三要点
    kp, n0 = load_kp()
    log(f"{tag}: 检测到远端领先 {behind} 个提交，备份本地三要点 {len(kp)} 条后合并")
    sh("git reset --hard origin/main", check=True)
    sh(f'"{PY}" scraper/shard_events.py merge', check=True, timeout=900)
    applied, total = overlay_kp(kp)
    return f"{tag}: 已合并(远端+{behind}) 三要点{len(kp)}条覆盖{applied} 总{total}"


def push():
    tok = get_token()
    if not tok:
        return "推送跳过：未取到 github token"
    url = f"https://{tok}@github.com/yongweili412/world-events.git"
    last = ""
    for i in range(5):
        r = sh(f'git -c http.postBuffer=524288000 -c credential.helper= push "{url}" HEAD:main',
               timeout=900)
        if r.returncode == 0:
            return f"推送成功（第{i+1}次）"
        last = (r.stderr or r.stdout).strip().splitlines()[-1] if (r.stderr or r.stdout) else "?"
        time.sleep(8)
    return f"推送失败(5次重试): {last}"


def main():
    if not acquire_lock():
        log("[lock] 上一轮仍在运行，本次跳过")
        return
    try:
        log(f"===== kp_cycle 开始 N={N} {time.strftime('%Y-%m-%d %H:%M:%S')} =====")
        log(sync("sync1"))

        r = sh(f'"{PY}" scraper/keypoints_batch.py {N}', timeout=4 * 3600)
        tail = "\n".join((r.stdout or "").strip().splitlines()[-6:])
        log(f"[batch] rc={r.returncode}\n{tail}")

        log(sync("sync2"))
        sh(f'"{PY}" scraper/shard_events.py split', check=True, timeout=900)
        sh("git add data/events/ scraper/llm_cache.json", check=True, timeout=300)
        c = sh('git commit -m "data: 三要点自动续跑检查点"', timeout=300)
        if c.returncode != 0 and "nothing to commit" not in (c.stdout + c.stderr):
            log(f"[commit] 异常: {c.stdout} {c.stderr}")
        log(push())
        log("===== kp_cycle 结束 =====")
    except Exception as ex:
        log(f"[ERROR] {type(ex).__name__}: {ex}")
    finally:
        release_lock()


if __name__ == "__main__":
    main()
