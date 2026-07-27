# DeepEval Advisory 可观测性设计

日期：2026-07-27

状态：书面设计已批准，进入实施计划

适用项目：`medical-notice-analyzer / 8099`

## 1. 决策摘要

本设计将 DeepEval 接入为独立的质量可观测工具：

- 每份正式报告最终都登记一个异步 DeepEval Advisory 评测作业。
- DeepEval 只展示分数、覆盖率、趋势和 Judge 自身准确性。
- DeepEval 永远不阻止发布，不进入用户请求关键路径，也不改变任何业务状态。
- 运行时 Advisory、Nightly fixed10 和 Weekly 100 样本共用同一套指标、投影、Judge 适配和结果 Schema。
- 运行时 Advisory 使用完整输入哈希、成功结果缓存和 single-flight，避免相同报告重复付费。
- Nightly/Weekly 强制绕过运行时缓存，用于真实观测 Judge 漂移和准确性。
- 运行时 Judge 可通过独立功能开关暂停；暂停时确定性门禁和用户功能继续运行，待评报告保留为 backlog，恢复后补评。

DeepEval 的结果契约固定声明：

```text
purpose = advisory_observability
blocking = false
affects_deliverable = false
affects_report_status = false
affects_release = false
affects_user_response = false
```

这些字段是不可变的系统约束，不是可配置选项。

## 2. 当前基线

### 2.1 代码与运行基线

设计基于以下已核验代码事实：

- 事实工作树：
  `C:\Users\admin\.config\superpowers\worktrees\htmldataconclusion\fix-87-dify-near-limit-hardcode`
- 分支：
  `codex/new-server-87-medical-notice-analyzer-20260722`
- 基线提交：
  `a6dcf5673e25085448b1e42f3de5c6c8050d838a`
- 当前 8099 主服务使用文件形式保存 Evidence Pack 和 analysis run：
  - `EVIDENCE_PACK_DIR`
  - `ANALYSIS_RUN_DIR`
- 当前主服务通过进程内 daemon thread 执行 analysis run。该机制不具备独立 Advisory 任务所需的持久性，因此不能用来保证 100% 最终覆盖。
- 当前 `requirements.txt` 不包含 DeepEval。
- 当前 Compose 只有主服务，`./data` 挂载为 `/app/data`。

本设计不把 DeepEval 安装进 8099 主服务进程，而是使用独立 Worker 和独立依赖文件。

### 2.2 现有质量权责

现有确定性 evaluator 和报告状态机继续独占以下权责：

- 报告生成与修复；
- 确定性质量门禁；
- `finished`、`needs_manual_review`、`failed`、`interrupted`；
- `deliverable`；
- Word 生成、下载和正文；
- PR 或发布是否允许继续。

DeepEval 不得覆盖、降级、升级或重新解释这些结果。

### 2.3 已知评测资产

项目已有：

- fixed3 回归集；
- fixed10 回归定义；
- 16-case 探索脚本；
- `offline_quality_evaluator.py`；
- `evaluate_regression_baseline.py`；
- `run_fixed_regression.py`；
- Evidence Pack、analysis run 和 history 文件。

当前 fixed10 存在声明 10 个、实际 9 个有效案例的问题；16-case 存在动态选样和非版本化问题。Nightly/Weekly 数据真源只能使用重新冻结、可重放并有内容哈希的数据集。

## 3. 目标

### 3.1 功能目标

1. 对每份正式报告异步执行一次版本化 DeepEval Advisory。
2. 最终登记覆盖率达到 100%。
3. 显示每项指标分数、分布、趋势、可用性和版本。
4. 使用输入哈希、缓存和 single-flight 避免重复付费。
5. Nightly/Weekly 持续评估固定数据集和 Judge 准确性。
6. 支持暂停运行时 Judge，并在恢复后补齐 backlog。
7. 对 Judge 的成本、Token、延迟、错误和准确性建立审计记录。

### 3.2 安全目标

1. Judge 只接收当前 Claim 或有限章节及其对应的 Evidence A/B。
2. 不外发完整报告、完整 Evidence Pack、Evidence C、长期记忆、凭据、内部路径或内部 URL。
3. 主服务不持有 Judge 凭据。
4. Judge 故障、暂停或低分对用户不可见且不影响项目使用。
5. 日志不记录 Claim、Evidence、Prompt、凭据或 Provider 原始错误正文。

### 3.3 质量目标

1. 运行时分数反映当前正式报告质量。
2. 固定集分数独立反映 Judge 自身可靠性。
3. 分数的可比性由数据集、模型、Prompt、指标和代码版本共同限定。
4. 人工验证用于解释分数可信度，不产生发布阈值。

## 4. 非目标

本设计明确不包含：

- 使用 DeepEval 阻止 PR、合并、部署或发布；
- 使用 DeepEval 改变 `deliverable` 或报告状态；
- 将 DeepEval 接入同步报告生成链路；
- 让 DeepEval 修改、修复或删除报告正文；
- 把 DeepEval 分数写进 Word；
- 把 DeepEval 错误换算为质量低分；
- 使用历史缓存冒充本次评分；
- 修改 `.88`；
- 修改 Dify 生成工作流；
- 引入 Celery、Redis、MySQL 或 MinIO 作为本阶段前置条件；
- 将动态 16-case 作为 Nightly/Weekly 数据真源；
- 向 Confident AI 上传数据。

## 5. 术语与资格定义

### 5.1 正式报告

本设计中的“正式报告”必须同时满足：

```text
status in {"finished", "needs_manual_review"}
AND formal_body_present == true
```

`formal_body_present` 是 Worker 从现有 analysis run 的规范报告正文字段推导出的本地资格谓词：正文经规范化后必须为非空，并且不能只是错误诊断或技术说明。它不是要求主服务新增或回写的持久化字段，也不改变现有 run Schema。

以下 run 不进入覆盖率分母：

- `failed`；
- `interrupted`；
- 没有正式正文；
- 只有错误诊断而无正式正文。

`needs_manual_review` 报告进入 Advisory 覆盖率，但 DeepEval 仍不能改变其状态或 Word 语义。

### 5.2 一次 Advisory

“每份正式报告执行一次 Advisory”表示：

- 每份正式报告产生一个版本化 Advisory 作业；
- 作业使用一个稳定的 `advisory_input_sha256`；
- 作业运行一个固定版本的 metric set；
- DeepEval 指标内部可以产生多个 Judge 子调用；
- 所有子调用共享同一作业身份、数据边界、预算和审计上下文。

它不表示强制每份报告只产生一次模型 HTTP 请求。

### 5.3 Advisory 状态

允许的状态为：

```text
discovered
pending
paused
running
completed
cached
retrying
partial
unavailable
over_budget
```

这些状态只属于 Advisory Artifact，不属于 analysis run 状态机。

## 6. 方案比较与选择

### 6.1 方案一：主服务内 fire-and-forget

优点：

- 改动少；
- 触发及时。

缺点：

- 进程退出会丢任务；
- DeepEval 依赖和 Judge 凭据进入主服务；
- 资源竞争可能影响用户；
- 无法可靠保证最终 100% 覆盖。

结论：不采用。

### 6.2 方案二：独立 Worker 扫描文件存储

优点：

- 主服务不导入 DeepEval；
- Worker 可以独立暂停、重启和回滚；
- 对 analysis run 和 Evidence Pack 只读；
- 通过持续扫描和补偿实现最终覆盖；
- 与当前文件存储架构一致；
- 不要求提前引入队列基础设施。

缺点：

- 是最终一致而非毫秒级触发；
- 单机文件任务箱需要原子写入和租约恢复。

结论：本阶段采用。

### 6.3 方案三：Celery/Redis 或独立评测平台

优点：

- 调度和扩展能力更强；
- 更适合未来多节点执行。

缺点：

- 提前引入基础设施、运维和迁移复杂度；
- 超出当前只读可观测目标。

结论：保留为未来替换 Worker 调度层的演进方向，不作为本阶段前置。

## 7. 总体架构

```text
8099 主服务
  ├─ 写 Evidence Pack
  ├─ 写 analysis run
  └─ 正常服务用户

共享数据目录
  ├─ evidence_packs/          Worker 只读
  ├─ analysis_runs/           Worker 只读
  └─ deepeval_advisory/       Worker 读写

deepeval-advisory-worker
  ├─ Report Discovery / Reconciler
  ├─ Eligibility Filter
  ├─ A/B Projection + Boundary Guard
  ├─ Input Hash + Cache + Single-flight
  ├─ DeepEval Evaluator
  ├─ Approved Judge Adapter
  └─ Advisory Result / Coverage Index

Nightly / Weekly Scheduler
  └─ 调用同一个 DeepEval Evaluator，强制 cache bypass
```

### 7.1 主服务隔离

主服务：

- 不安装 DeepEval；
- 不持有 Judge API Key；
- 不读取 Advisory 结果；
- 不等待 Worker；
- 不因 Worker 不可用而失败；
- 不为 DeepEval 增加同步 HTTP 调用。

### 7.2 Worker 文件权限

Worker 挂载：

- `analysis_runs`：只读；
- `evidence_packs`：只读；
- `deepeval_advisory`：读写。

主服务无需挂载 `deepeval_advisory`。

## 8. 组件边界

建议新增：

```text
app/deepeval_advisory/
  __init__.py
  models.py
  projection.py
  hashing.py
  store.py
  cache.py
  judge.py
  evaluator.py
  worker.py
```

职责如下。

### 8.1 `models.py`

定义：

- Advisory job；
- Projection；
- Judge call context；
- metric result；
- cache envelope；
- coverage index；
- error envelope。

所有跨文件对象使用严格 Pydantic Schema，并拒绝未知字段。

### 8.2 `projection.py`

负责：

- 读取已验证的 analysis run 和 Evidence Pack；
- 确定正式报告资格；
- 将报告切分为 Claim 或有限章节；
- 只选择与当前对象相关的 Evidence A/B；
- 生成局部 evidence ID 和安全 locator；
- 执行长度、字段和敏感信息检查。

禁止直接序列化完整 `report`、`evidence_pack`、`attachments` 或 `memory`。

首版不依赖尚未落地的 ClaimIR：使用确定性、可测试、带版本号的章节切分器生成有限章节。未来接入 ClaimIR 时必须新增显式 Projection 版本并使缓存键自然失效，不得在同一版本下静默改变切分语义。

### 8.3 `hashing.py`

负责：

- 规范化 JSON；
- 计算报告、证据投影、指标和 Judge 配置哈希；
- 生成 `advisory_input_sha256`；
- 生成不暴露内部 ID 的 HMAC 引用。

### 8.4 `store.py`

负责：

- 原子创建任务；
- 唯一键去重；
- 任务租约；
- crash recovery；
- stale lease 回收；
- per-run Advisory 索引；
- coverage 分母和分子。

### 8.5 `cache.py`

负责：

- 成功结果缓存；
- 精确输入键查找；
- single-flight；
- 缓存完整性哈希；
- 版本变更自然失效。

### 8.6 `judge.py`

实现 `DeepEvalBaseLLM`：

- `load_model()`；
- `get_model_name()`；
- `generate(prompt, schema=None)`；
- `a_generate(prompt, schema=None)`。

当 DeepEval 提供 Pydantic Schema 时，Adapter 必须返回该 Schema 的实例；无 Schema 时返回字符串。异步实现必须使用真正的异步 HTTP 客户端。

### 8.7 `evaluator.py`

唯一评分核心，负责：

- 构建 `LLMTestCase`；
- 构建固定版本 metric set；
- 调用 DeepEval；
- 归一化 score、reason、error、Token、成本和延迟；
- 输出稳定的 Advisory Schema。

运行时、Nightly 和 Weekly 都调用这个模块。

### 8.8 `worker.py`

负责：

- 扫描新增和历史 analysis run；
- 计算资格；
- 建立任务；
- 暂停和恢复；
- 重试；
- backlog 补偿；
- coverage 统计。

## 9. 运行时 Advisory 数据流

1. Worker 扫描 `analysis_runs`。
2. 对每个 run 执行正式报告资格判断。
3. 使用 `pack_id` 从只读 `evidence_packs` 读取对应 Evidence Pack。
4. 校验 analysis run、报告正文、Evidence Pack 和版本。
5. 构造 Claim/章节与 A/B 证据投影。
6. 计算 `advisory_input_sha256`。
7. 原子登记 per-run Advisory 索引。
8. 查询成功结果缓存。
9. 缓存命中：
   - 不调用 Judge；
   - 写 `cached`；
   - 记录源 evaluation ID 和结果哈希。
10. 缓存未命中：
    - 获取 single-flight；
    - 调用 DeepEval；
    - 严格校验响应；
    - 只在完整成功时写缓存。
11. 写独立 Advisory 结果。
12. 更新 coverage index。

任何步骤失败均不得写回 analysis run。

## 10. 固定数据集与人工标注

### 10.1 fixed3

用途：

- 快速 smoke；
- identical-input 生成波动；
- Judge 重复判分检查。

fixed3 不用于表示整体业务覆盖。

### 10.2 fixed10

用途：

- Nightly 项目质量观测；
- Judge 日常漂移检测。

前置要求：

- 实际存在并可重放的 10 个案例；
- 不允许声明 10 个但跳过 1 个；
- 每个案例有材料身份和内容哈希；
- 数据集版本不可变。

### 10.3 100 样本校准集

首版固定 100 个 Claim/章节单元。

切分规则：

- 按来源公告分组；
- 独立验证集不少于 40；
- 校准集不超过 60；
- 同一来源不得同时进入校准集和验证集。

预标流程：

1. 两个子智能体接收相同的版本化输入和 rubric。
2. 两者独立运行，互不可见对方标签。
3. 两者不可见 DeepEval/Judge 结果。
4. 保存各自模型版本、Prompt 版本、输出和哈希。

人工审核范围：

- 两个子智能体的全部分歧；
- 全部 P0/高风险样本，即使两者意见一致；
- 独立验证集的全部样本。

人工结论是最终 Golden。未经人工确认的低风险一致样本只能用于校准，不得作为独立验证结论。

高风险范围至少包括：

- 日期；
- 金额；
- 主体；
- 采购范围；
- 附件存在和解析状态；
- Evidence C 越界；
- 长期记忆越界；
- 无 A/B 支撑的关键结论。

## 11. DeepEval 版本与使用方式

### 11.1 版本

精确锁定：

```text
deepeval==4.1.3
```

使用 Worker 专用依赖文件和锁定文件，不使用宽松版本范围。

### 11.2 数据模型

项目自己的 Dataset Manifest 是数据真源。DeepEval `Golden` 和 `LLMTestCase` 是运行时适配层，不反向定义项目存储 Schema。

运行时映射：

```text
input             = 当前 Claim/章节的明确业务任务
actual_output     = 当前 Claim 或有限长度章节
retrieval_context = 对应的 Evidence A/B 片段
expected_output   = 人工确认的必备事实清单，可为空
metadata          = Advisory 的本地身份和版本
```

### 11.3 配置

合规默认值：

```text
DEEPEVAL_TELEMETRY_OPT_OUT=1
DEEPEVAL_DISABLE_DOTENV=1
DEEPEVAL_NO_INSPECT_PROMPT=1
```

同时：

- 不设置 `CONFIDENT_API_KEY`；
- 不使用 `official=True`；
- 运行时结果缓存由项目管理；
- DeepEval 自带磁盘缓存关闭。

## 12. 指标集

指标按单维度展示，不计算可以相互抵消问题的综合通过分。

### 12.1 `claim_faithfulness_v1`

使用 Faithfulness：

- 判断 Claim/章节是否由 A/B 证据支撑；
- 真实但未出现在所提供证据中的事实仍视为不忠实；
- 是主要事实依据分数。

### 12.2 `critical_coverage_v1`

使用固定 `evaluation_steps + rubric` 的单维 GEval：

- 判断人工确认的必备事实是否遗漏；
- 不同时评价文风、相关性或结构。

### 12.3 `attachment_state_consistency_v1`

使用独立 GEval：

- 判断报告对附件存在、缺失、不可解析和证据来源的描述是否一致。

### 12.4 `answer_relevancy_v1`

用途：

- 观测跑题和冗余；
- 不替代 Faithfulness；
- 不参与任何业务或发布决策。

### 12.5 仍由确定性规则负责的事项

DeepEval 不替代：

- 章节存在；
- JSON/Schema；
- 禁止短语；
- Word 安全；
- 状态矛盾；
- 材料身份；
- 文件和哈希完整性。

## 13. Judge 数据边界

### 13.1 允许外发

每次 Judge 子调用仅允许：

- 一个 Claim，或一个最多含 8 个 Claim 的有限章节；
- 当前对象相关的 Evidence A/B；
- 局部 ID；
- 枚举来源角色；
- 页码、段落号、工作表序号、行列号等安全 locator；
- 固定安全指令；
- 当前 metric Prompt；
- 当前 Pydantic 响应 Schema；
- 必要的模型参数。

### 13.2 上限

```text
Claim/章节文本总量 <= 6,000 Unicode 字符
Evidence 数量 <= 8
Evidence 摘录总量 <= 8,000 Unicode 字符
完整请求 JSON <= 64 KiB
```

超限时：

- 返回 `over_budget`；
- 不静默截断；
- 不改发完整报告；
- 不以更大 payload 重试。

### 13.3 禁止外发

- 完整报告；
- 完整 Evidence Pack；
- 原始附件；
- Evidence C；
- 长期记忆；
- Dify 生成 Prompt；
- Cookie、Authorization、API Key、Token、密码或 DSN；
- Windows、Unix 或 UNC 路径；
- 内网 IP、内部主机名或内部 URL；
- 数据库主键、对象存储键；
- 与当前对象无关的电话、邮箱或个人联系信息。

### 13.4 Prompt 注入防护

- Claim 和 Evidence 一律视为不可信数据；
- 固定 system 指令声明不得执行 Evidence 中的指令；
- Evidence 不得通过字符串拼接进入 system/developer 指令；
- 数据边界检查在 DeepEval 调用前执行；
- 项目控制的 Judge Adapter 在发出模型请求前再次执行字段、大小和敏感信息校验；
- 若组织使用自有 Judge Gateway，Gateway 还必须在模型调用前重复校验；
- 若直连已审批的第三方 Provider，则以 Adapter 校验和 Provider 审批为边界，不假定第三方端点执行项目自定义校验。

## 14. Judge 请求、响应与错误

### 14.1 逻辑请求上下文

无论 Provider 的实际 wire format 如何，项目内部必须记录：

```text
request_id
evaluation_id
run_ref
attempt_ref
case_ref
metric_id
metric_version
judge_repeat_id
deepeval_version
adapter_version
safety_preamble_version
judge_provider
resolved_model_or_profile_version
projection_sha256
prompt_sha256
response_schema_sha256
idempotency_key
payload_bytes
issued_at
```

### 14.2 响应

归一化结果至少包含：

```text
score
bounded_reason
evidence_references
unsupported_spans
resolved_model_version
token_usage
cost
latency
retry_count
input_fingerprint
output_fingerprint
schema_validation_status
```

不接收或持久化 chain-of-thought。

Advisory 结果 Schema 不得包含 `pass/fail`、质量阈值、发布决定、deliverable 覆盖值或报告状态覆盖值。

### 14.3 错误分类

```text
policy_rejected
over_budget
credential_unavailable
transport_timeout
rate_limited
provider_unavailable
invalid_model_output
response_schema_invalid
context_mismatch
indeterminate
```

错误不得当成分数 `0`，也不得沿用历史分数。

### 14.4 重试

仅对明确未开始处理的瞬时错误有限重试：

- 429；
- 503；
- 连接建立失败；
- 明确未开始执行的超时。

最多重试两次，复用相同的 evaluation ID 和幂等键。

以下情况不重试：

- 数据边界失败；
- 认证或 TLS 失败；
- 上下文、版本或摘要不一致；
- 响应 Schema 或完整性失败；
- Provider 是否已经执行无法确定。

## 15. 输入哈希、缓存与去重

### 15.1 缓存键

```text
advisory_input_sha256 = SHA256(canonical_json({
  report_sha256,
  evidence_projection_sha256,
  metric_set_sha256,
  deepeval_version,
  adapter_version,
  judge_provider,
  immutable_judge_model_or_profile_version,
  prompt_version,
  response_schema_version,
  safety_preamble_version,
  locale
}))
```

不允许只用报告文字哈希作为缓存键。

### 15.2 缓存规则

- 只缓存 `completed` 且 Schema/完整性校验成功的结果；
- 不缓存 `partial`、`unavailable`、`over_budget` 或错误；
- 每个 run 都生成自己的 Advisory 索引；
- 缓存命中记录源 evaluation ID 和结果哈希；
- Prompt、模型、指标、证据、报告或 Adapter 任一变化都会产生新键；
- 使用原子唯一键和 single-flight 防止并发重复付费。

### 15.3 模型版本要求

Judge 必须使用不可变模型版本或受控 profile 版本。若 Provider 只提供可漂移 alias，必须由 `judge_profile_version` 在每次模型变化时显式升级，否则不允许复用缓存。

### 15.4 固定集缓存

Nightly/Weekly 用于观测 Judge 自身，因此强制：

```text
cache_mode = bypass
```

运行时缓存不能用于 Judge 准确性或重复稳定性结论。

## 16. 覆盖率

### 16.1 指标

```text
eligible_reports
enrolled_reports
scored_reports
cached_reports
pending_reports
paused_reports
retrying_reports
unavailable_reports
over_budget_reports
```

```text
enrollment_coverage =
  (enrolled_reports / eligible_reports) * 100%

valid_score_coverage =
  ((scored_reports + cached_reports) / eligible_reports) * 100%
```

### 16.2 目标与语义

- `enrollment_coverage` 必须最终达到 100%；
- `valid_score_coverage` 以 100% 为目标；
- Provider 故障时，缺失项作为 backlog 显示；
- 不允许静默删除、排除或用历史分数填充；
- 暂停期间的正式报告仍应在恢复后被扫描和补评。

## 17. 运行方式

统一入口：

```text
scripts/run_deepeval_advisory.py --mode runtime-worker
scripts/run_deepeval_advisory.py --mode fixed10
scripts/run_deepeval_advisory.py --mode calibration100
scripts/run_deepeval_advisory.py --mode backfill
```

四种模式复用同一个投影、指标、Judge Adapter 和结果 Schema。只有 `fixed10` 与 `calibration100` 强制绕过缓存；`runtime-worker` 与 `backfill` 使用完整输入缓存和 single-flight。

### 17.1 每份正式报告

- 由 Worker 异步发现；
- 允许成功结果缓存；
- 低并发执行；
- 不影响用户响应。

### 17.2 Nightly

- 固定、版本化的 fixed10；
- 每个样本至少评一次；
- 强制 cache bypass；
- 展示项目质量趋势和 Judge 日常漂移。

### 17.3 Weekly

- 完整 100 样本；
- 重复 Judge 评分；
- 强制 cache bypass；
- 比较 Judge 与人工 Golden；
- 展示一致率、分数波动、各风险类型混淆矩阵。

### 17.4 Release snapshot

可以在发布并行或发布后生成只读快照，但：

- 不是 required check；
- Judge 不可用不阻止发布；
- 分数高低不阻止发布；
- 不改变已发布报告。

## 18. 分数展示

项目负责人看到：

- 各指标分数；
- 样本数；
- 成功评分数；
- 未评分数；
- 中位数；
- 最低分；
- 最高分；
- 分数波动；
- 与上一可比版本的趋势；
- Judge 重复评分一致性；
- coverage；
- cache hit 和节省的调用数；
- Token、成本和延迟；
- 数据集、模型、Prompt、指标和代码版本；
- `partial`、`unavailable`、`over_budget` 数量和原因分类。

不显示：

- 发布“通过/失败”；
- 可交付“通过/失败”；
- 能覆盖 P0 的综合平均分；
- chain-of-thought；
- 完整 Claim、Evidence 或 Prompt。

人工校准仅说明分数可信度，不生成发布阈值。

## 19. 功能开关

```text
DEEPEVAL_RUNTIME_ADVISORY_ENABLED=true|false
DEEPEVAL_SCHEDULED_EVALUATION_ENABLED=true|false
```

### 19.1 运行时开关关闭

- 不发出运行时 Judge 请求；
- 不影响主服务；
- backlog 在恢复后由扫描器补齐；
- Nightly/Weekly 可继续运行。

### 19.2 定时评测开关关闭

- 停止 fixed10 和 100 样本的定时任务；
- 运行时 Advisory 可继续；
- 不改变任何报告。

### 19.3 凭据缺失

凭据缺失时：

- Worker 不调用 Judge；
- 记录 `credential_unavailable`；
- 不影响主服务；
- 不把结果记作已评分。

## 20. 安全与留存

### 20.1 凭据

- Judge 凭据与 Dify 生成凭据分离；
- 只注入 Worker；
- 不进入仓库、镜像层、命令行、URL、日志或结果；
- 使用审批主机和 HTTPS；
- 禁止重定向和动态 base URL；
- 校验证书链和主机名。

### 20.2 Provider 条件

Judge Provider 必须满足：

- 已审批；
- 不使用输入训练模型；
- 留存和数据地域符合组织策略；
- 模型或 profile 版本可追溯；
- 可提供 Token、成本和请求身份，或由 Adapter 可靠补充。

### 20.3 日志

日志只允许：

```text
timestamp
request_id
evaluation_id
pseudonymous run_ref
metric/version
model/profile version
input/output fingerprint
status
score
duration
token counts
cache status
error category/code
retry count
```

禁止：

- Claim；
- Evidence；
- Prompt；
- Authorization Header；
- Provider 原始响应；
- Provider 原始错误正文；
- 内部路径和凭据。

## 21. 测试设计

### 21.1 单元测试

覆盖：

- 正式报告资格；
- `finished` 与 `needs_manual_review`；
- 无正文、失败和中断排除；
- Claim/章节切分；
- Evidence A/B 白名单；
- Evidence C 拒绝；
- 大小限制；
- 敏感信息和内部路径拒绝；
- 规范化输入哈希；
- 版本变化导致缓存失效；
- 成功结果缓存；
- 错误结果不缓存；
- single-flight；
- 原子任务登记；
- 租约恢复；
- pause/resume；
- coverage 计算。

### 21.2 DeepEval 合约测试

覆盖：

- DeepEval 4.1.3 导入；
- metric 构造器实际签名；
- `DeepEvalBaseLLM` 同步调用；
- 真异步调用；
- Pydantic Schema 注入和实例返回；
- invalid JSON；
- response schema mismatch；
- Token、成本和延迟归一化；
- DeepEval 自带缓存关闭。

### 21.3 集成测试

使用 fake Judge：

1. 正式报告出现后被 Worker 发现；
2. 任务持久化；
3. A/B 投影生成；
4. DeepEval 返回分数；
5. 独立 Advisory Artifact 写入；
6. analysis run 内容和哈希保持不变。

还必须覆盖：

- 两个 run 的相同输入只调用一次 Judge；
- Worker 进程在 running 阶段终止并恢复；
- Provider 429/503；
- Provider outcome unknown；
- Worker 暂停后恢复并补齐 backlog；
- Nightly/Weekly cache bypass；
- 历史 backfill。

### 21.4 隔离测试

必须证明：

- 主服务 requirements 不包含 DeepEval；
- 主服务环境不包含 Judge 凭据；
- 主服务不开启 Judge 网络连接；
- Worker 停止时 8099 仍正常；
- 开关前后业务 API 响应一致；
- 开关前后报告正文、状态、deliverable 和 Word 一致；
- Advisory 失败不写 analysis run；
- Advisory 结果固定 `blocking=false`。

### 21.5 真实 Judge smoke

只有在以下条件满足后，才允许最小真实 Judge smoke：

- 独立 Judge API 已审批；
- 凭据安全注入；
- 数据边界测试通过；
- 仅使用经过批准的最小样本；
- 成本和请求数有硬上限；
- 不在 `.88` 运行；
- 不修改 Dify。

## 22. 验收条件

### 22.1 覆盖率

- 100 份符合资格的正式报告全部登记；
- 不合格 run 不进入分母；
- 暂停期间产生的报告在恢复后全部补齐；
- backlog 和未评分项可见。

### 22.2 缓存与成本

- 相同 `advisory_input_sha256` 只产生一次付费评测；
- 每个 run 都有独立 Advisory 索引；
- 任一版本字段变化都会导致 cache miss；
- Nightly/Weekly 从不命中运行时缓存；
- 缓存节省的请求和成本可统计。

### 22.3 用户隔离

- Worker 停机不影响报告生成；
- Judge 故障不影响报告查看和下载；
- DeepEval 分数不改变 report status；
- DeepEval 分数不改变 deliverable；
- DeepEval 分数不改变 Word；
- DeepEval 分数不改变发布；
- 开关前后用户可见结果一致。

### 22.4 安全

- Evidence C 被拒绝；
- 完整报告和完整 Evidence Pack 被拒绝；
- 凭据、路径和内部 URL 被拒绝；
- 日志和结果中无秘密；
- Worker 使用独立凭据；
- 主服务不持有 Judge 凭据。

### 22.5 Judge 可信度

- 100 样本预标流程完整；
- 全部分歧和高风险样本有人工审核；
- 至少 40 个独立验证样本全部人工确认；
- Weekly 报告 Judge 与人工标签的一致率和混淆矩阵；
- 分数波动和不可用情况如实展示；
- 不把人工验证转化为发布阈值。

## 23. 分阶段实施

### 阶段 A：合约与 fake Judge

- 新增 Schema、投影、哈希、Store、Cache 和 fake Adapter；
- 不添加真实凭据；
- 不扫描生产数据；
- 验证所有隔离契约。

### 阶段 B：固定集

- 冻结有效 fixed10；
- 建立 100 样本；
- 接入已审批 Judge；
- 运行 Nightly/Weekly；
- 不扫描运行时报告。

### 阶段 C：Runtime disabled

- 部署独立 Worker；
- 运行时开关关闭；
- 只核对正式报告发现、资格和覆盖分母；
- 不调用运行时 Judge。

### 阶段 D：Runtime Advisory

- 开启运行时 Judge；
- 低并发处理新报告；
- 使用缓存和 single-flight；
- 逐步 backfill 历史报告；
- 观测成本、延迟和 backlog。

### 阶段 E：稳态

- 每份正式报告最终有 Advisory；
- Nightly fixed10；
- Weekly 100 样本；
- 运行时和 Judge 准确性分开展示；
- 永久保持 non-blocking。

## 24. 回滚

### 24.1 最小回滚

```text
DEEPEVAL_RUNTIME_ADVISORY_ENABLED=false
```

效果：

- 停止运行时 Judge；
- 保留现有任务和结果；
- 主服务无变化；
- 确定性门禁继续。

### 24.2 完全停止 Worker

- 停止 `deepeval-advisory-worker`；
- 不删除任务、缓存和结果；
- 8099 主服务继续运行；
- 恢复 Worker 后重新扫描并补齐。

### 24.3 禁止的回滚方式

- 不删除 analysis run；
- 不删除 Word；
- 不修改报告状态；
- 不重写历史 Advisory 分数；
- 不把旧缓存迁移成当前版本结果；
- 不操作 `.88`。

## 25. 实施文件范围

预计允许修改或新增：

```text
medical-notice-analyzer/app/deepeval_advisory/**
medical-notice-analyzer/scripts/run_deepeval_advisory.py
medical-notice-analyzer/requirements-deepeval.txt
medical-notice-analyzer/Dockerfile.deepeval
medical-notice-analyzer/docker-compose.deepeval-advisory.yml
medical-notice-analyzer/tests/test_deepeval_advisory_*.py
medical-notice-analyzer/tests/fixtures/deepeval_advisory/**
medical-notice-analyzer/docs/superpowers/**
```

默认不修改：

```text
medical-notice-analyzer/app/main.py
medical-notice-analyzer/requirements.txt
Dify 工作流
.88 部署
现有报告状态和 Word 代码
```

若实施过程中发现无法在不修改 `app/main.py` 的情况下实现正式报告发现，必须停止并重新评审设计；不得静默增加同步触发或状态写回。

## 26. Stage 0 前置清单

开始实现前必须完成：

1. 固定 DeepEval 4.1.3 和依赖锁。
2. 完成实际 API 签名 smoke。
3. 审批独立 Judge API。
4. 冻结 Judge 模型/profile、Prompt 和响应 Schema。
5. 修正 fixed10 为真实 10 个案例。
6. 建立 100 样本标注流程。
7. 完成人工确认的至少 40 个独立验证样本。
8. 固定 A/B Projection 和数据边界。
9. 固定缓存键和结果 Schema。
10. 确认 Worker 只读挂载 analysis run 和 Evidence Pack。
11. 确认主服务不安装 DeepEval、不持有 Judge 凭据。
12. 所有运行开关默认关闭。

## 27. 最终交付物

- 本设计 spec；
- 后续实施计划；
- DeepEval 4.1.3 兼容性证据；
- fixed10 不可变数据集；
- 100 样本 Dataset Manifest；
- 两个子智能体的独立预标记录；
- 人工审核与裁决记录；
- 独立 Advisory Worker；
- 输入哈希、缓存和 single-flight；
- Nightly/Weekly 评分结果；
- 运行时 Advisory 结果；
- coverage、成本、延迟和 Judge 准确性报告；
- 回滚和隔离验证证据。

## 28. 设计完成判定

本设计只有在以下条件同时成立时才视为被正确实施：

```text
每份正式报告最终登记 Advisory
AND 相同完整输入不重复付费
AND Nightly/Weekly 绕过缓存
AND Judge 数据边界可验证
AND Worker 可独立暂停和恢复
AND 主服务不持有 DeepEval 或 Judge 凭据
AND DeepEval 永远不影响 deliverable、报告状态、发布或用户
```
