# -*- coding: utf-8 -*-
"""push 被拒后按「事件 id 并集」合并分片，替代 `git merge -X ours`。

背景（P1-5）：
  4 条写库 workflow（update / backfill / backfill_daily / enrich_sources）
  的 push 重试原先用 `git merge -X ours` + `checkout --ours`。对 JSON 分片而言，
  这等于「整个文件取本地版」——另一个 workflow 刚写入的事件会被静默丢弃。
  2026-10-30 已发生过一次，靠人工按 id 并集修复。

用法（在 CI 的 push 重试块内调用）：
    git fetch origin main
    git merge --no-commit --no-ff origin/main || true     # 允许冲突留在暂存区
    python scraper/merge_shards.py                        # 合并 U 状态的分片
    git add -A && git commit -m "merge: 按 id 并集合并分片"

原理：
  - 对 `git diff --name-only --diff-filter=U` 里 data/events/*.json 的冲突文件，
    用 `git show :2:<path>`（ours/本地）与 `git show :3:<path>`（theirs/远端）
    分别读出 JSON 数组，按事件 id 做并集：远端为基，本地独有的补入。
  - 同一 id 两边都改过时，字段取本地版（本轮成果优先），
    但 `sources` 按 URL 取并集——避免覆盖掉 enrich_sources 刚补的独立来源。
  - index.json（dict 结构）不是事件数组，跳过——它由 split 重新生成。
  - 非分片文件（代码/README 等）保持 git 默认冲突标记，交给调用方处理；
    本工具只保证「事件数据一条不丢」。

安全：
  - 只读 git 对象、只写工作树里的冲突分片；不 push、不改 git 历史。
  - 幂等：重复运行对已合并的文件无副作用。
  - 合并后打印统计，便于 CI 日志审计。
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
SHARD_PREFIX = "data/events/"
INDEX_NAME = "data/events/index.json"


def sh(args):
    r = subprocess.run(args, capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


def conflicted_files():
    code, out, _ = sh(["git", "diff", "--name-only", "--diff-filter=U"])
    if code != 0:
        return []
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def stage_blob(path, stage):
    """读 git 暂存区 stage（2=ours, 3=theirs）里的文件内容。"""
    code, out, _ = sh(["git", "show", f":{stage}:{path}"])
    if code != 0:
        return None
    try:
        return json.loads(out)
    except Exception:
        return None


def _merge_event(local, remote):
    """同 id 两边都改过：字段取本地（本轮成果优先），但 sources 按 URL 取并集。

    为什么 sources 要并集：enrich_sources 工作流专门给事件补独立来源，
    若另一条 workflow 持有它启动时的旧版，简单「取本地」会把刚补的来源覆盖掉。
    URL 去重后两边来源都保留，信息只增不减。
    """
    if local == remote:
        return local
    merged = dict(local)
    ls, rs = local.get("sources"), remote.get("sources")
    if isinstance(ls, list) or isinstance(rs, list):
        seen = set()
        out = []
        for s in (ls or []) + (rs or []):
            if not isinstance(s, dict):
                continue
            u = (s.get("url") or "").strip()
            k = u or json.dumps(s, ensure_ascii=False, sort_keys=True)
            if k in seen:
                continue
            seen.add(k)
            out.append(s)
        if out:
            merged["sources"] = out
    return merged


def merge_by_id(ours, theirs):
    """远端(theirs)为基，本地(ours)独有补入；同 id 合并字段 + sources 并集。"""
    if not isinstance(ours, list) or not isinstance(theirs, list):
        return None, None
    # 以远端顺序为基，保持分片原有排序（避免整片重排产生噪声 diff）。
    by_id = {}
    order = []
    for e in theirs:
        if isinstance(e, dict) and e.get("id") and e["id"] not in by_id:
            by_id[e["id"]] = e
            order.append(e["id"])
    base = len(order)
    added = 0
    overridden = 0
    for e in ours:
        if not (isinstance(e, dict) and e.get("id")):
            continue
        i = e["id"]
        if i not in by_id:
            by_id[i] = e
            order.append(i)
            added += 1
        elif by_id[i] != e:
            by_id[i] = _merge_event(e, by_id[i])
            overridden += 1
    merged = [by_id[i] for i in order]
    return merged, {"theirs": base, "added_from_ours": added,
                    "ours_overrode": overridden, "total": len(merged)}


def main():
    files = conflicted_files()
    shards = [f for f in files
              if f.startswith(SHARD_PREFIX) and f.endswith(".json") and f != INDEX_NAME]
    others = [f for f in files if f not in shards]

    if not shards:
        print("[merge_shards] 无分片冲突，无需合并")
        if others:
            print(f"[merge_shards] 非分片冲突 {len(others)} 个（不由本工具处理）: "
                  + ", ".join(others[:5]))
        return 0

    print(f"[merge_shards] 检测到 {len(shards)} 个分片冲突，按 id 并集合并…", flush=True)
    total_added = 0
    for path in shards:
        ours, theirs = stage_blob(path, 2), stage_blob(path, 3)
        if ours is None or theirs is None:
            # 单边新增/删除：无双向冲突，保留本地（本轮成果）
            print(f"  ⚠ {path} 单边缺失 ours/theirs，跳过（保留本地版）")
            sh(["git", "checkout", "--ours", path])
            sh(["git", "add", path])
            continue
        merged, stats = merge_by_id(ours, theirs)
        if merged is None:
            print(f"  ⚠ {path} 不是事件数组，跳过")
            sh(["git", "checkout", "--ours", path])
            sh(["git", "add", path])
            continue
        tmp = path + ".merge_tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(merged, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        sh(["git", "add", path])
        total_added += stats["added_from_ours"]
        print(f"  ✅ {path}: 远端 {stats['theirs']} + 本地独有 {stats['added_from_ours']} "
              f"= {stats['total']}（同id取本地 {stats['ours_overrode']}）", flush=True)

    # index.json 由 split 依据分片重建，避免残留旧索引
    if INDEX_NAME in files or os.path.exists(INDEX_NAME):
        pass  # split 会重算；此处不手动改
    print(f"[merge_shards] 完成：合并 {len(shards)} 个分片，补回本地独有事件 {total_added} 条", flush=True)
    print("[merge_shards] 下一步请执行 `python scraper/shard_events.py split` 与 `git add -A && git commit`",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
