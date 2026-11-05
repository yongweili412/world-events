# 悬而未决登记册（OPEN DECISIONS）

> 规则：只追加 + 就地关闭（OPEN → RESOLVED 并补 Resolution）。
> 每次开工前把未决项复现到上下文最前，逐条判断能否关闭。

| Date | Source | Open Item | Related Constraints | Current Leaning | Blocked By | Resolves When | Status |
|------|--------|-----------|---------------------|-----------------|------------|---------------|--------|
| 2026-11-06 | 交接包 P0-4 | git 历史瘦身（.git 616MB）是否执行、在哪个环境执行 | 不可逆：改写 694 个 commit SHA + 强推；云端 workflow 与本机定时任务每 2h 活跃写库 | 暂缓；若要做，只在 Linux 环境用 `git filter-repo` 剥离两个**已删路径**（`data/events.json` 148MB + `data/events.js` 67MB 打包大小），**不要**用 runbook 的 `--strip-blobs-bigger-than 8M` | ① Windows 上 `git-filter-repo` 报 OSError [Errno 22]（管道不兼容）、`git filter-branch` 报中文路径 invalid path；② 缺少无写库的干净窗口（云端+定时任务活跃） | 有 Linux runner/环境 + 暂停所有写库 workflow 与定时任务 + 用户再次确认强推 | OPEN |

## 已完成的准备（P0-4 一旦重启可直接复用）

- **mirror 备份**：`D:\WorkBuddy\projects\_we_git_backup_20261106\world-events-backup.git`
  （裸库 588MB / 694 提交 / fsck 0 错误 / HEAD `238662d`），另有同名 `.tar.gz` 574MB。
  回滚：`git push --mirror <备份路径>`。
- **只读分析**（`git filter-repo --analyze`，在备份副本上跑）：历史大头是
  `data/events.json`(累计打包 148MB) 与 `data/events.js`(67MB)，**两者均于 2026-10-06 从工作树删除**，
  只残留于历史 → 剥离它们不会触碰当前 HEAD 的任何活文件。
- **当前 HEAD 的活文件不能被过滤**：`data/events/2026-10.json` 有 17.4MB、
  `2026-09.json` 10.1MB、`admin.html` 38MB、`country.html` 36MB 等。
  runbook 建议的 `--strip-blobs-bigger-than 8M` 会误伤活分片，**已否决**。
- 试验：所有改写尝试都在备份副本 `slim-trial` 上进行，主仓库与远端 HEAD 全程未受影响
  （TREE 哈希核对一致、数据 54,716 条完好）。
