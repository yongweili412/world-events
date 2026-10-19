# PASS_ON_FAIL 重判 + llm_budget 对照实验 + enforce 切换可行性评估

> 方案制定日期：2026-10-18
> 数据基线：`data/candidates/candidate-decisions-2026-10.jsonl`
> 涉及代码：`scraper/candidate_pipeline.py` / `scraper/value_judge.py` / `scraper/llm_guard.py`
> 状态：**待用户确认后开始执行**

---

## 0. 数据基线（实测，非摘要估算）

| 指标 | 值 | 来源 |
|------|----|------|
| PASS_ON_FAIL 总条数（10月账本） | **694** | `grep -c "PASS_ON_FAIL"` |
| 唯一 URL 数 | **585** | url 字段去重（有 109 条重复命中记录） |
| 按日分布 | 10-10:86 / 10-11:85 / 10-12:83 / 10-13:76 / 10-14:62 / 10-15:69 / 10-16:82 / 10-17:68 / 10-18:83 | `collections.Counter` on `at[:10]` |
| 入库命中（50 样本抽测） | 48/49 唯一 url 已入库，仅 1 条 `news.example.com` 是 test fixture | 实测 events.json 全量 sources[].url 匹配 |
| LLM 当前配置 | `LLM_MODEL=deepseek/deepseek-v4.1-flash`，`LLM_BASE_URL=api.qnaigc.com` | 读 env |
| `llm_budget` 现状 | **函数签名硬编码 30**，update.yml 未注入 env | `scraper/candidate_pipeline.py:141` |
| `INGEST_MODE` 现状 | `shadow`（update.yml:42） | update.yml |
| 闸门 8% 判定率口径 | 由 llm/llm_fail/rules 三类分布反推（见 §3.2） | 候选账本 method 字段 |

**关键判断**：PASS_ON_FAIL 候选**绝大多数已入库**，重判**不需要回写 events.json**——只需生成评估报告，告诉你"如果当时 LLM 在线，这 585 条里有多少会被 KEEP / REVIEW / DROP"。这份报告是 enforce 切换的**核心数据基础**。

---

## 1. PASS_ON_FAIL 重判方案

### 1.1 触发条件（必须全部满足才跑）

重判脚本**默认不自动触发**，需要手动 + 条件齐备：

| 条件 | 校验方式 | 不满足时的行为 |
|------|----------|----------------|
| ① LLM 可用性探测通过 | 调 `judge_one` 跑 3 条样本，3 条都必须返回非 None | 退出并打印「LLM 不可用，已取消本次重判」 |
| ② 限流余量估算通过 | 当日已用配额 < 80%（通过查 `chat_raw` 内嵌的限流日志或简单 sleep 节奏估算） | 跑半量（剩余候选按下批继续） |
| ③ 显式授权 | 用户运行 `python scraper/rejudge_pass_on_fail.py --apply` | 默认 `--dry-run` 只生成预览，不调 LLM，不写盘 |

**为什么不设计成自动**：模型可用性是外部事件，跑在 update.yml 里可能撞限流；云端 shadow 已在持续产生新 PASS_ON_FAIL，重判**不追新**、只清存量 694 条，单次跑完即存档。

### 1.2 批次处理策略

| 策略 | 值 | 备注 |
|------|----|------|
| 批次大小 | 5 条/批（与 `value_judge.BATCH` 一致） | 防输出截断 |
| 批间 sleep | 2.5 秒 | 略大于 `judge_items` 默认 2.0，留余量 |
| 单次运行预算 | 60 条（≈20 分钟） | 与 `judge_items` 默认 batch 节奏一致，不会撞限流 |
| 失败重试 | 同条失败 2 次后放弃，标记 `rejudge_failed` | 沿用 `chat_raw` 内置 retryable={429,500,502,503,504} 换档逻辑 |
| 全天跑完节奏 | 585 条 ÷ 60 ≈ 10 轮 ≈ 3.5 小时 | 可拆 3 天 × 200 条/天，看用户时间安排 |

### 1.3 新旧结果对比逻辑

每条候选只**追加**一条 `REJUDGE_*` 记录到当月账本，**绝不修改/删除**原 `PASS_ON_FAIL` 行（符合「审计痕迹不丢」红线）：

```jsonl
{"at":"2026-10-19T10:00:15","action":"REJUDGE_KEEP","rejudge_of_url":"https://...",
 "original_action":"PASS_ON_FAIL","original_archive_value":45,"original_method":"rules+llm_fail",
 "new_archive_value":72,"new_method":"llm","new_decision":"KEEP","new_event_type":"world",
 "reason":"[LLM] ...","matched_event_url":"https://...","matched_event_id":"evt_..."}
```

`action` 取值（对比三组）：

| 新 decision | 原 decision | 行为 | action 后缀 |
|------------|------------|------|-------------|
| KEEP | REVIEW | **新建升级**（影子层面，不写 events.json） | `REJUDGE_UPGRADE_KEEP` |
| KEEP | KEEP（不会，因原链都降级为 REVIEW） | — | — |
| REVIEW | REVIEW | 维持原判 | `REJUDGE_CONFIRM_REVIEW` |
| DROP | REVIEW | **下沉** | `REJUDGE_DOWNGRADE_DROP` |

**关键报告字段**（落地在 `data/candidates/rejudge_report_YYYY-MM-DD.json`）：

```json
{
  "run_at": "2026-10-19T10:05:00",
  "total_pass_on_fail": 694,
  "unique_urls": 585,
  "judged_success": 581,
  "judged_failed": 4,
  "decisions": {
    "KEEP": 187,
    "REVIEW": 302,
    "DROP": 92
  },
  "compare_to_original": {
    "upgrade_to_keep": 187,
    "confirm_review": 302,
    "downgrade_to_drop": 92,
    "failed": 4
  },
  "matched_in_events_json": 581,
  "not_in_events_json": 4,
  "note": "已入库 581 条不需 action；未入库 4 条进入 CAP/REJECT 队列复核"
}
```

### 1.4 异常处理机制（"绝不丢数据"是红线）

| 情况 | 处理 |
|------|------|
| LLM 中途变不可用 | 立即停跑，已完成的写入账本；剩余从断点继续（断点存在 `rejudge_checkpoint.json`） |
| 单条 candidate 在 events.json 中找不到 | 写入 `not_in_events_json` 计数 + 单独队列文件 `rejudge_orphan.json`，事后人工决定要不要入库（不影响其他候选） |
| 模型返回非合法 JSON | `_normalize` 返回 None → `judge_one` 返回 None → 标记 `judged_failed` 计数，**不重复** LLM 调用 |
| 中途用户 Ctrl-C | 已处理条目的账本行已写盘，下次 `rejudge --apply` 自动从断点恢复（基于 `rejudge_of_url` 唯一性） |
| LLM 限流（429）| `chat_raw` 内部已 retryable，整体失败时脚本会 sleep 60s 重试一次，仍失败 → 停跑等明天 |

### 1.5 跑完后的产物

| 文件 | 用途 |
|------|------|
| `data/candidates/candidate-decisions-2026-10.jsonl`（追加 `REJUDGE_*` 行） | 完整审计痕迹 |
| `data/candidates/rejudge_report_YYYY-MM-DD.json` | 本次重判的统计报告 |
| `data/candidates/rejudge_orphan.json` | 未入库孤儿（如有） |
| `data/candidates/rejudge_checkpoint.json` | 断点，跑完删 |

---

## 2. llm_budget 30→60 对照实验

### 2.1 实验设计

| 项 | 说明 |
|----|------|
| 实验类型 | **前后对照 + 单轮实验**（不轮替，因当日限流余量不能重置） |
| 对照组 | 最近 9 天（10-10 ~ 10-18）云端 shadow 跑的账本：`llm method 数 / 总候选数` |
| 实验组 | 明天（10-19）云端 shadow 之前**先跑一轮本地**：`INGEST_MODE=shadow python -c "...gate_items(...,llm_budget=60)..."` <br>**改动方式**：在 `candidate_pipeline.py` 加 `int(os.environ.get("LLM_BUDGET", "30"))`，本地 `LLM_BUDGET=60` |
| 样本量 N | 每轮 ≈350 候选（与云端每日抓取节奏一致） |
| 干预变量 | `llm_budget` 30 → 60（屏蔽其他变量：同 model、同 base_url、同时段） |

### 2.2 记录指标

| 指标 | 测量方法 | 数据来源 |
|------|----------|----------|
| **LLM 真实判定率** | `llm count / total candidates` | 账本 `method == "llm"` 计数 |
| **llm_fail 比例** | `rules+llm_fail count / total` | 账本 |
| **wall-clock** | 脚本 `time.time()` 起止差 | 运行日志 |
| **平均单条 LLM 耗时** | wall-clock / llm_used | 运行日志 |
| **限流触发** | 429 重试日志计数 | `chat_raw` 内部打印 |

### 2.3 显著性判断标准

| 判定维度 | 阈值 | 拒绝原假设的口径 |
|----------|------|------------------|
| ① 绝对提升 | LLM 判定率从 ~8% 提升到 **≥ 18%**（≥10 pp） | 直观业务判断 |
| ② 相对提升 | 提升倍数 ≥ 2.0× | 直观业务判断 |
| ③ 统计显著 | 卡方检验 p < 0.05 | `scipy.stats.chi2_contingency` on 2×2 表（实验组 llm vs 非-llm × 对照组 llm vs 非-llm） |
| ④ 样本量 | 实验组 ≥ 200 候选 | 否则不判定（等会再补一轮） |
| ⑤ 副作用 | wall-clock 增加 ≤ 2×，且 429 重试次数 ≤ 对照组均值 + 3 次 | 不能为了判定率把每日任务拖超时 |

**全部 5 条都满足 = 显著提升**，进入 §3 的 enforce 可行性评估。任意一条不满足 = 维持 30，**不切换**。

### 2.4 备选方案：如果提升不显著

1. **预算再提一档**（60 → 120）：若 30→60 提升 < 5%，说明真正的瓶颈是限流而非 budget，且也暴露下游 base_url 容量限制——不值得再烧时间
2. **降 budget**：如果 30→60 判定率**下降**了，可能 model 在 60 量级上稳定被限流，那回退到 30 并切到 `glm-4.5-flash 兜底` 模式
3. **并行跑**：目前 `judge_items` 是串行，如果显著提升且允许，可考虑 2 个 worker（**红线：不改 model 链，只用 flash**）

---

## 3. enforce 模式可行性评估报告（**仅在 §2 显著提升时触发**）

### 3.1 评估报告结构（届时填实数）

#### 3.1.1 风险

| 风险 | 等级 | 缓解措施 | 备注 |
|------|------|---------|------|
| **误拦真实事件** | **P0 红线** | 切换后 PASS_ON_FAIL 仍保留（红线：闸门故障绝不丢数据）；最近 7 天误杀率需 < 2%（账本 `REJECT` 数 / 总候选数） | 这是项目红线 |
| **限流加剧** | 中 | llm_budget 上限锁死 60；禁改 model 链；观察云端 LLM_BASE_URL 稳定性 | qnaigc.com 是社区代理，可能波动 |
| **任务时长膨胀** | 低 | wall-clock 增加 ≤ 15min 才允许；update.yml 总时长 ≤ 25min | 当前 ~10min |
| **回滚成本** | 低 | env 切换 1 行 commit；账本可重放恢复全部候选 | 应急成本低 |
| **判定漂移** | 中 | 同一 model 温度 0.2 不变；保留 `method != "llm"` 的 rules-only 兜底日志 | 模型本身可能更新 |

#### 3.1.2 收益

| 收益 | 定量估计 | 备注 |
|------|---------|------|
| **LLM 判定真覆盖** | 8% → ~60%（基于先例界实验推算） | 大多数 borderline 候选不再走"模糊 REVIEW" |
| **新入库垃圾减少** | KEEP 7,042 → 增速下降但质量提升 | 对照治理前 53% 低分入库 |
| **REVIEW 池减轻** | ~302 待复核 → ~150 待复核 | 人工复核规模减半 |
| **示范价值** | 闸门从「观察模式」升级为「真闸门」 | 项目核心价值主张兑现 |

#### 3.1.3 实施步骤（分阶段）

| 阶段 | 动作 | 门禁 |
|------|------|------|
| **Phase A：本地 enforce dry-run** | `INGEST_MODE=enforce python scraper/update.py` 一次本地跑，核对账本 `REJECT` 数 ≤ 5% | 误杀率不超阈值 |
| **Phase B：云端 shadow + budget=60** | update.yml 临时加 `LLM_BUDGET: '60'`（不动 INGEST_MODE），观察 1 天 | shadow 报告判定率 ≥ 60% |
| **Phase C：云端 enforce 灰度** | update.yml 改 `INGEST_MODE: 'enforce'`，**每日人工看账本** 3 天 | 3 天内 REJECT 数 ≤ 5%，PASS_ON_FAIL ≈ 0 |
| **Phase D：全自动** | dashboard/通知接到账本关键指标（KEEP/REJECT/PASS_ON_FAIL 比例） | 连续 7 天指标稳定 |

**回滚预案**：任何阶段触发任一红线 → 改回 `shadow` commit，从账本把期间 REJECT 条目按 url 反查 events.json 恢复入库（**红线：闸门故障绝不丢数据，账本必须可重放**）。

---

## 4. 推进顺序建议

| 顺序 | 任务 | 依赖 | 时长估计 |
|------|------|------|----------|
| 1 | **写 `scraper/rejudge_pass_on_fail.py`**（dry-run 默认） | 无 | 30 分钟 |
| 2 | **跑 §1 重判**（dry-run 预览 → 用户确认 → apply 跑存量 585 条 ≈ 3 天） | LLM 可用 | 3 天 |
| 3 | **改 `candidate_pipeline.py` 加 `LLM_BUDGET` env 支持**（1 行 diff） | 无 | 5 分钟 |
| 4 | **跑 §2 对照实验**（本地 1 轮 ≈ 20-40 分钟） | LLM 可用 + budget env 支持已合入 | 半天 |
| 5 | **统计分析 + 判定显著性** | 实验完成 | 30 分钟 |
| 6 | （如显著）**填 §3 enforce 可行性报告** | 显著性成立 | 1 小时 |
| 7 | （用户拍板后）**Phase A → D 灰度** | 可行性报告通过 | 1-2 周观察 |

**这是设计文档，不动代码**。等您确认以下内容后，我可以立即开始 step 1（写 `rejudge_pass_on_fail.py` 的 dry-run 版本）：

1. 重判是否追新产生的 PASS_ON_FAIL？（建议：**不追**，只清 694 存量）
2. 单次运行预算 60 条是否合适？（再大可能拖慢主线）
3. 对照实验是否同意用「本地跑单轮」跳开云端 yml？（最快、零风险）
