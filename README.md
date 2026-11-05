# 全球大事记 · 世界事件永久存档

> 线上站点：<https://yongweili412.github.io/world-events/>
> 一个按「事件」而不是按「新闻」组织的全球大事档案：每条事件有独立 URL、原始来源、
> 时间线与地区标注，数据以 JSON 分片公开，可用 RSS 与月度接口复用。

---

## 现在是什么（不是规划，是现状）

| 项 | 现状 |
|---|---|
| 事件量 | 5 万+ 条，覆盖 1997–至今（2020 年后占 92%，详见《数据说明》） |
| 分类 | 10 类标准类目（自然灾害 / 政治 / 经济 / 体育 / 环境 / 社会 / 军事 / 科技 / 文化 / 其他） |
| 页面 | 首页（今日）、月归档、地区档案、世界地图、静态事件页 `e/*.html` |
| 数据出口 | `data/m/YYYY-MM.json` 月度分片、`data/index-lite.json` 全库索引、`feed.xml` |
| 抓取 | GitHub Actions 每日运行（RSS + 翻译 + 评分 + 构建 + 分片写回） |
| 分发 | GitHub Pages（构建产物在 Actions 里生成，不入库） |

## 架构（为什么长这样）

```
data/events.json            工作副本（不入库）：所有脚本读写它
data/events/*.json          入库真源：按年/月分片，可 diff、可合并、单文件 1–6MB
data/index-lite.json        全库轻量索引：搜索/地图/存档列表，按需加载
data/m/YYYY-MM.json         单月全量：详情页只取这一个月，不再下载全库
e/evt_*.html                静态详情页（KEEP 级事件）：给不跑 JS 的爬虫看
archive-YYYY-MM.html        月归档（>900 条自动分页）
index.html                  首页：静态渲染最新一天 + 内嵌精简事件 + 月份导航
```

三条历史教训直接决定了现在的结构：

1. **不要全量注入**。最初把 114MB 的 `events.js` 塞进每个页面，详情页 126 秒打不开。
   现在按 id 解析月份只取一个分片（`app.js` 的 `getEventById`）。
2. **不要整文件重写**。单行 79MB 的 JSON 每次改动都进 git 历史，仓库膨胀到 600MB+。
   现在 split/merge 分片，工作副本不入库。
3. **不要在产出侧各写各的类目**。分类 13→10 收敛过一次又漏回来。现在类目只有
   `scraper/taxonomy.py` 一份口径，`tests/test_data_contract.py` 用 AST 断言
   `scraper.py` 那份与它完全一致，不一致就测试失败。

## 本地开发

```bash
pip install -r scraper/requirements.txt

python scraper/shard_events.py merge     # 分片 → data/events.json
python scraper/scraper.py                # 抓取（可跳过：测试构建不需要）
python scraper/normalize_data.py         # 数据自愈（类目/标签/日期，幂等）
python -m pytest tests/ -q               # 回归测试
python scraper/validate.py               # 数据体检（结构错误会 exit 1）
python scraper/build.py                  # 生成静态站
python -m http.server 8080               # 本地预览
```

部署不需要手动 push 产物：`deploy.yml` 会重新 `build.py` 并发布 `dist/`。

### 数据契约（CI 会拦）

- `category` 只能是 `taxonomy.STANDARD_CATEGORIES` 里的 10 类；别名走 `normalize_category`。
- `date` 为 `YYYY-MM-DD`，不晚于明天；`id` 唯一且符合 `evt_YYYYMMDD_NNN` / `EVT-XXXXXXXX`。
- `sources` 非空，每项都要有 `url` 与 `name`。
- `decision`（KEEP/REVIEW/DROP）由**证据层 + 置信度**决定，不是 `archiveValue` 的阈值切分；
  页面上的分值只作参考。抓取入口的闸门目前是 `shadow`（观察，不拦截），见 `update.yml`。

## 目录

```
.github/workflows/   update（抓取+构建+写回）、deploy（发布 Pages）、backfill*（历史回填）、
                     enrich_sources（补证）、linkcheck（来源链接体检）
scraper/             抓取、评分、翻译、构建、分片、校验、自愈
  build.py           静态站生成（首页/归档/静态详情页/索引/分片/RSS/sitemap）
  taxonomy.py        类目与分值口径的唯一来源
  normalize_data.py  入库前自愈（类目/标签/来源 URL/地区/日期，幂等）
  validate.py        数据体检（errors 阻断 / warnings 报告）
  sources.py         来源 URL 规范化（修 Wikipedia 月度门户死链、去跟踪参数）
  regions.py         地区确定性归一（别名、城市→国家；不猜国家）
  check_links.py     来源链接体检（只报告；404 才叫 dead，403 记为反爬）
tests/               单元测试与数据契约测试（CI 会跑）
assets/              echarts、世界地图数据、分享图
docs/git-slim.md     仓库历史清理方案（手动执行，含回滚）
```

## 已知局限（如实写）

- **单来源占比高**：约 70% 的事件只有一个来源，越新的月份越高（2026-10 为 87%）。
- **历史层薄**：1997–2019 共约 4,200 条，是"骨架"不是"全量"；2008 年偏多是导入批次的痕迹。
- **溯源精度**：部分历史回填引用的是 Wikipedia 当月门户页（原 URL 格式整批 404，已改为可访问的 `Month_YYYY`），
  仍是月级而非单条条目；外部链接由每周 linkcheck 抽检。
- **速览覆盖**：仅约 5% 的事件有基于全文的速览，其余是 RSS 摘要级。
- **地区粒度**：地区字段混合国家/城市/大洲，"全球"约占四成、近一半无国家；没有坐标，地图只能国家级着色，
  且**不**用关键词推断国家（实测约 92% 准确，错归属比空着更糟），因此地图只统计有明确归属的事件。

## 许可

本项目采用**双许可**：数据与文字内容以 CC BY 4.0 发布、源代码以 MIT 发布，
完整条款见 [LICENSE.md](./LICENSE.md)。
事件摘要/正文摘录来自原始媒体，版权归原出版方；引用请保留来源链接。

## 纠错

发现错误请开 Issue，注明事件页链接与问题：<https://github.com/yongweili412/world-events/issues>
