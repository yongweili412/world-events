# AGENTS.md · 任何 agent 进入本仓库前必读

> 本仓库由多个 agent（WorkBuddy / CodeBuddy / 其他）先后维护。人类所有者不逐行 review。
> 下面这些规则是为了让"下一个 agent"不必猜测上一个 agent 干了什么。
> **改动代码前先读完本文；与本文冲突的临时做法，请在 PR 里说明理由。**

## 1. 谁在写什么

| 角色 | 负责 | 不要碰 |
|---|---|---|
| 数据流水线 agent | `scraper/`（抓取/翻译/评分/回填）、`data/events/*.json` | 前端、构建产物 |
| 前端/构建 agent | `build.py`、`app.js`、模板、`style.css` | 抓取逻辑、评分权重 |
| 人类所有者 | 方向、许可、发布 | — |

**同一时间只允许一个 agent 写仓库。** 并发写入 + `git merge -X ours` 会静默丢事件
（2026-10-30 已发生过一次，靠按 id 并集人工修复）。

## 2. 唯一数据流

```
data/events.json         工作副本（gitignore，不在仓库）
   ↑ merge / ↓ split
data/events/*.json       入库真源
```

- 所有脚本读写 `data/events.json`。
- **只有 `scraper/shard_events.py split/merge` 能碰分片。**
- `split()` 已改为增量写（内容没变的分片不写盘）。不要再改回全量重写。
- 不要提交 `data/events.json`（会让仓库多出 ~80 MB blob）。

## 3. 生成物，不要手改

`index.html`（markers 区块）、`data-about.html`（DATASTATS）、`e/*.html`、
`archive-*.html`、`data/m/*.json`、`data/index-lite.json`、`feed.xml`、`robots.txt`、
`sitemap.xml` 全部由 `python scraper/build.py` 生成。

改了模板后**必须跑一次 build**，否则页面里的时间线与统计还是旧值。

## 4. 提交前必须跑（CI 同序）

```bash
python scraper/shard_events.py ensure     # 没有工作副本时
python scraper/normalize_data.py          # 幂等；改完数据先自愈
python -m pytest tests/test_data_contract.py -q
python scraper/validate.py
python scraper/build.py
python scraper/shard_events.py split
```

任一失败：不要提交。`validate.py` 的 errors 是阻断级的；warnings 只报告。

## 5. 类目只有一份口径

- 标准 10 类与别名表在 `scraper/taxonomy.py`。
- `scraper.py` 里那份是抓取入口用的副本，`tests/test_data_contract.py` 用 AST 断言两者一致。
- **加/改类目必须同时改两处**，否则测试立刻失败——这是故意的，不是让你去改测试。

## 6. 数据口径（别按直觉改）

- `decision`（KEEP/REVIEW/DROP）由**证据层 + 置信度**决定，不是 `archiveValue` 的阈值函数
  （见 `value_rules.py:218-254`）。不要把 decision 重算成分值分档。
- 价值闸门当前是 `shadow`（只统计不拦截）。要改 `enforce`，先做 ≥200 条人工抽检。
- **不要用关键词推断国家**：实测 85.9%（限单国提及 91.7%），错归属比空着更糟。
  地区只做确定性映射（`scraper/regions.py`）。
- 死链判断：**404/410/451 才算 dead**；401/403/429 是反爬，记 `blocked`（`check_links.py`）。

## 7. 合并与历史

- **不要用 `git merge -X ours` 合 JSON 分片**：等于整文件取本地，会丢另一个 agent 的事件。
  冲突时按事件 id 做并集。
- 不要在 Actions 运行期间改 git 历史或强推。
- 历史重写必须先 mirror 备份，见 `docs/git-slim.md`。
- 大文件（>5 MB）不入库；`data/events/*.json` 单文件应控制在 1–3 MB。

## 8. 文档放哪

- 给人和 agent 的操作规则：本文件。
- 一次性改动记录：`docs/`，不要塞进 README。
- README 只描述"现在是什么、怎么跑"，不是 changelog。

## 9. 如果你是从旧版本接手

先跑 `python scraper/validate.py --json` 看数据现状，再读 `docs/` 最近一份完成记录。
不要假设 README 与代码一致——先验证，再改。

## 10. 公共工具：写库 / 诊断 / 速览口径（2026-11-28）

`scraper/llm_utils.py` 是这三个约定的单一实现，新增脚本请直接引用，不要各写一套：

| 能力 | 用法 | 约定 |
|---|---|---|
| 原子写回 | `atomic_dump_events(ev)` / `atomic_dump(obj, path)` | 临时文件 + `os.replace` + 复读断言；`events.json` 用紧凑格式（与 `shard_events.py merge` 一致） |
| 应答诊断 | `diagnose_response(r, exc=...)` | 分 `ok / rate_limit / server_error / short / http / none / exception`，`reason` 必带网关回包的 `model` |
| 速览清洗 | `tidy_summary(s)` | 去代码块、压空白、按句截断到 120 字；`MIN_SUMMARY_LEN=50` 为合格下限 |

两条硬规则：

- **禁止** `json.dump(ev, open("data/events.json","w"))` 这种非原子写法——中途被杀会留下半截
  JSON，且 `indent=1/2` 会让 70MB 工作副本反复膨胀。写库统一走上面的工具。
- LLM 失败日志**禁止**再写"限流/失败"这种笼统标签：200 但空/过短是上游软性降载，
  不是 429；两者处置完全不同（前者退避重试，后者才需要换档/冷却）。

已接入：`scraper.py`、`full_summary_batch.py`、`keypoints_batch.py`、`field_translate.py`、
`translate_cloud.py`、`fill_images.py`、`backfill_gaps.py`、`backfill_early_years.py`、
`value_judge.py`、`llm_cache.py`。`normalize_data.py` / `kp_cycle.py` 本就自带等价的
`tmp + os.replace`，无需改动。

