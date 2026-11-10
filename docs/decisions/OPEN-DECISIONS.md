# 悬而未决登记册（OPEN DECISIONS）

> 规则：只追加 + 就地关闭（OPEN → RESOLVED 并补 Resolution）。
> 每次开工前把未决项复现到上下文最前，逐条判断能否关闭。

| Date | Source | Open Item | Related Constraints | Current Leaning | Blocked By | Resolves When | Status |
|------|--------|-----------|---------------------|-----------------|------------|---------------|--------|
| 2026-11-06 | 交接包 P0-4 | git 历史瘦身（.git 616MB）是否执行、在哪个环境执行 | 不可逆：改写 694 个 commit SHA + 强推；云端 workflow 与本机定时任务每 2h 活跃写库 | 暂缓；若要做，只在 Linux 环境用 `git filter-repo` 剥离两个**已删路径**（`data/events.json` 148MB + `data/events.js` 67MB 打包大小），**不要**用 runbook 的 `--strip-blobs-bigger-than 8M` | ① Windows 上 `git-filter-repo` 报 OSError [Errno 22]（管道不兼容）、`git filter-branch` 报中文路径 invalid path；② 缺少无写库的干净窗口（云端+定时任务活跃） | 有 Linux runner/环境 + 暂停所有写库 workflow 与定时任务 + 用户再次确认强推 | OPEN |
| 2026-11-10 | 网站体检 P0-3 | 仓库已从 616MB 涨到 **787MB**（GitHub 计），本地 `.git` 673MB；历史补全让分片再增 148MB | 数据还在每天增长；`data/events/*.json` 是入库真源不可删 | 优先级从「可选优化」升为「应尽快做」；同等条件下仍只在 Linux 环境执行，且需先暂停写库 | 同 2026-11-06 条目：Windows 工具链不可用 + 无干净窗口 | 同 2026-11-06 条目的解除条件 | OPEN |

## 2026-11-10 体检补充：已解决 / 新发现

- **已澄清（原判断有误）**：详情页「相关事件」并非依赖空的 `relatedEvents` 字段，
  而是 `app.js` 按「标签 0.5 + 分类 0.3 + 时间 0.15 + 地区 0.05」实时计算，功能真实存在。
  真问题在性能：它会在详情页首屏强制下载整份 index-lite（gzip 10.8MB）。
  **已修**：改为 IntersectionObserver 懒加载（滚到页底才取索引）。
- **已修（P0）**：`deploy-pages` 的 `timeout` 参数被 GitHub 强制压到最大 600 秒
  （设 1800 无效），而 dist 达 485MB / 12,738 文件，导致近 12 次部署 2 次失败。
  已做发布瘦身：月分片字段白名单、归档摘要截 60 字、静态页阈值 70→75（12,000 → 约 7,000 页）、
  index-lite 只写有值字段。**待验证**：新 dist 体积与部署成功率。
- **新增（P1）**：`sources[].snippet` 与 `description` 内容 100% 相同，
  说明「描述短」不是缺内容而是历史条目本身就简短（如「印尼教堂圣诞夜爆炸致18人死亡」信息完整）。
  故**放弃用 LLM 扩写描述**（会编造，违背可信档案定位），改为展示层如实标注。


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
