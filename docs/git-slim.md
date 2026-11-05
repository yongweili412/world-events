# 仓库瘦身方案（git 历史清理）

> 现状：仓库 629 MB（GitHub 计 `size` ~644,000 KB），其中**已跟踪文件只有 146 MB**，
> 差额几乎全在 git 历史里——每天 `split` 都重写 117 个分片，每个都变成新 blob。
> 2026-10-25 → 11-05 11 天涨了约 150 MB。

## 先止血，再手术

**止血已经做了**：`scraper/shard_events.py` 的 `split()` 现在只写"内容真的变了"的分片
（未变化的分片跳过写入，也不再误删 `index.json`）。每天只有当前 1–2 个月份会变，
预计历史增速从 ~15 MB/天 降到 ~1–2 MB/天。

**手术（清理存量历史）需要手动执行**，因为它会改写所有 commit SHA、需要强推，
并且要通知任何 clone 过仓库的人。建议在流量低谷做，做完立刻重新部署。

## 执行步骤

```bash
# 0) 必备：镜像备份（整个仓库对象，含所有分支）
git clone --mirror https://github.com/yongweili412/world-events.git world-events-backup.git
tar -czf world-events-backup-$(date +%F).tar.gz world-events-backup.git
# 把 tar.gz 存到仓库之外（网盘/对象存储），这一步不能省

# 1) 工具
pip install git-filter-repo

# 2) 全新克隆（filter-repo 要求 fresh clone）
git clone https://github.com/yongweili412/world-events.git world-events-slim
cd world-events-slim

# 3) 先看看到底哪些路径占了历史（只读分析）
git filter-repo --analyze
# 打开 .git/filter-repo/analysis/*.txt，确认大头是 data/events/*.json

# 4) 方案 A（推荐）：只保留最近 12 个月的 data/events 历史，
#    更早的分片历史折叠成一次提交（当前文件内容完全保留）
git filter-repo --path data/events --path-glob 'data/events/*.json' \
  --refs main --replace-text /dev/null --dry-run   # 先 dry-run 看看

# 更稳妥的做法：用 --strip-blobs-bigger-than 处理单文件大 blob
git filter-repo --strip-blobs-bigger-than 8M --refs main

# 5) 校验：当前工作树必须与远端一致（这一步失败就不要推）
git diff --stat origin/main   # 应该只显示 filter 造成的差异，不应有内容差异
python scraper/shard_events.py merge
python scraper/validate.py --quiet

# 6) 强推（先确认分支保护是否允许）
git remote add origin https://github.com/yongweili412/world-events.git
git push origin main --force

# 7) 让 GitHub 真正回收
#    仓库 Settings → 联系 GitHub Support 触发 gc，或等待自动 gc；
#    本地验证：du -sh .git
```

## 风险与回滚

- **所有 commit SHA 会变**：Issue/PR 里的 commit 链接、外部引用会失效。
- **必须强推**：分支保护若开启需临时放开。
- **回滚**：用第 0 步的 mirror 备份重新 push：
  `git push --mirror world-events-backup.git`（把备份作为 remote 推回）。
- **不要在 Actions 运行中执行**：先禁用 update/deploy 的 schedule，做完再开。

## 之后怎么防止再长胖

1. `split` 已改为增量写入（本次改动）。
2. 年度归档：每年把 12 个旧分片打包成一个 `data/archive/YYYY.json.gz` 并删除原分片
   （`merge` 需支持读取归档；工作量约半天，可留到仓库再次逼近 1 GB 时做）。
3. 大文件（>5 MB）一律不入库，放 Release 附件或对象存储。
4. 每月看一次 `git count-objects -vH`，超过 800 MB 就安排一次清理。
