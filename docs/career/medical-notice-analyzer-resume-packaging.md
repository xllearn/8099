# 智能医疗公告分析项目：简历包装与面试手册

> 面向岗位：Agent 开发、AI 应用工程师、AI 后端工程师。
>
> 项目口径：以“独立负责从 0 到 1 设计与实现”为主线。
>
> 重要边界：本文区分“当前仓库可验证实现”和“假设已完成的目标架构”。目标架构、模拟指标和简历文案用于学习与面试演练；没有真实测试报告时，不应把模拟数字当成真实生产数据。

---

## 1. 项目名称与定位

### 推荐名称

**基于 LangGraph 的可信医疗公告分析 Agent 平台**

### 技术栈

`Python` `FastAPI` `LangGraph` `DeepEval` `Celery` `Redis` `MySQL` `MinIO` `OpenTelemetry` `Docker`

### 项目描述

面向医疗保障、卫生健康及大型医疗机构公告的智能分析场景，建设基于 Evidence Grounding 的 AI Agent 分析平台。数据采集范围覆盖 32 个省级单位、333 个市级单位、约 2800 个县级单位及国家级部门和大型医疗机构，日采集公告约 9000～10000 条，其中业务人员每日选取约 10～50 条进入 AI 分析链路。系统自动完成公告读取、附件解析、证据包构建、报告生成、质量评测、结果修复和文档导出，将原本 2～3 人每天约 2 小时的分析工作缩短为系统分钟级处理和人工复核。

---

## 2. 原项目是怎样运行的

当前仓库本质上是一套由 FastAPI 承载、Dify 负责模型工作流的医疗公告分析流水线，而不是一个自由规划型 Agent。

```text
数据库选取公告
  -> 读取公告正文和元数据
  -> 下载、解析 0～10 个附件
  -> 构建全量 Evidence Pack
  -> 根据 Dify 输入限制生成压缩视图
  -> 调用 Dify 生成 ReportIR / Markdown
  -> 第一次质量检查
  -> 不通过则执行修复
  -> 第二次质量检查
  -> 导出 Word 报告
```

当前项目已经具备以下工程基础：

- 数据库选材、公告正文清洗和多格式附件解析。
- 全量证据包与面向 Dify 的压缩输入分离。
- 结构化 ReportIR、质量门禁、修复链路和 Word 导出。
- 运行记录、失败归因、步骤 Checkpoint 和恢复机制。
- 独立的 DeepEval Advisory 旁路评测原型。

当前没有真正使用 LangGraph。流程状态分散在 Evidence Pack JSON、Analysis Run JSON、Checkpoint JSON 和 Dify 工作流变量中，可以理解为“隐式状态机”。目标优化是将这些状态收拢为显式、类型化的 LangGraph State。

---

## 3. Evidence Pack 如何构建

## 3.1 两阶段结构

你的记忆是正确的：系统先构建一个较完整、可追溯的基础证据包，再根据模型输入限制生成二次压缩视图。

```text
原始公告与附件
      |
      v
基础 Evidence Pack：完整、可追溯、用于持久化和诊断
      |
      v
Generation Payload / Compact Pack：面向模型输入的受控压缩视图
```

全量 Evidence Pack 是事实底座；压缩后的 Payload 只是一次模型调用的输入视图，不能反过来替代事实底座。

## 3.2 基础证据包构建步骤

### 第一步：材料选择

- 主材料选择 1～3 条，作为本次报告的核心事实来源。
- 辅助材料最多选择 10 条，用于背景和关联分析。
- 同一公告不能同时作为主材料和辅助材料。

### 第二步：读取数据库

从 MySQL 读取公告标题、正文、发布时间、区域、发布机构、项目类型、公告分类和附件列表。HTML 正文会被清洗为纯文本，脚本、样式和重复空白会被移除。

### 第三步：附件下载与解析

支持 PDF、DOC、DOCX、XLS、XLSX、CSV、TXT、HTML 和 ZIP 等格式。每个附件会生成：

- 下载状态和解析状态。
- 文本摘要、关键事实和重要段落。
- 表格标题、表头、关键列、行数和代表性行。
- 页码、表格序号、行列位置等定位信息。
- 原始文件或解析文本的 SHA-256 哈希。
- 解析失败、文件过大和格式不支持等告警。

### 第四步：构建证据项

当前证据模型可以用 A/B/C 三层解释：

- **A 级直接证据**：公告字段、公告正文、附件文本、表格单元格。必须包含合法 source_ref，可以回到具体公告、附件、页码或表格位置。
- **B 级派生事实**：对 A 级证据做标准化后得到的结构化事实，例如将“截止至 2026 年 8 月 10 日 17:00”归一化为统一时间格式。B 级事实必须记录 derived_from、extractor_version，并且只能依赖 A 级证据。
- **C 级辅助信息**：摘要、生成指导、告警、诊断、历史记忆等。C 级内容可帮助生成，但不能单独证明事实。

每个 evidence_item 根据规范化内容计算 evidence_id；来源内容还有 source_hash，用于发现内容被替换或引用失配。

### 第五步：校验并持久化

保存前校验：

- evidence_id 是否与规范化内容一致。
- A 级证据是否包含直接来源。
- B 级证据是否只依赖有效 A 级证据。
- source_hash 是否与源文本或源文件一致。
- 是否存在重复证据和非法定位信息。

通过后，将完整 Evidence Pack 持久化，供后续生成、评测、诊断和重新执行使用。

## 3.3 构建基础证据包是否使用 LLM

结论：**主流程以规则和解析器为主，LLM 只是可选增强，不是基础证据包成立的前提。**

基础阶段中的大部分工作不需要 LLM：

- 数据库字段读取。
- HTML 清洗。
- 文件格式识别和文本提取。
- 表格单元格抽取。
- 来源定位、哈希计算和证据 ID 生成。
- 明确表头的字段映射和常见业务关键词识别。

只有当表格表头含义模糊、规则无法稳定识别关键列时，系统才可以启用可选 LLM，为表格生成语义摘要和列映射。该 LLM 被限制为只使用输入行，不得补全、纠错、合计或创造企业、产品、数量和价格。

面试口径：

> 我没有让 LLM 直接构造事实底座。可定位的原始证据主要由确定性解析器生成，LLM 只处理规则难以覆盖的模糊表格语义，并且输出仍需要经过字段白名单、索引范围和来源一致性校验。

## 3.4 二次压缩是否使用 LLM

二次压缩有三条路径：

### 路径一：直接输入

当清洗后的有效内容未超过配置阈值时，不做语义压缩，直接保留主材料正文、附件摘要和结构化表格信息。

### 路径二：可选 LLM 长文本压缩

输入过长时，将材料按块切分，调用低温度 LLM 生成自包含证据摘要。Prompt 强制保留企业、产品、规格、注册证、医保编码、采购量、价格、时间和执行要求，禁止推测、纠错和要求用户查阅原附件。

### 路径三：纯规则降级压缩

如果 LLM 压缩未启用、调用失败或结果不完整，系统使用规则压缩：

- 删除重复模板和低价值噪声。
- 限制正文和附件摘要长度。
- 优先保留主材料、核心附件和业务关键字段。
- 保留 mandatory 证据、B 级事实及其 A 级依赖。
- 按证据优先级删除可选项，并记录省略数量和哈希。

因此，正确表述是：

> 基础证据包主要由确定性解析与规则构建；二次压缩支持 LLM 语义压缩，但必须提供规则压缩降级路径，并对强制证据执行保留率校验。

---

## 4. 事实与分析分层如何实现

事实与分析分层不是把报告简单分成两个标题，而是从数据模型、生成约束、校验规则和发布策略四个层面实现。

## 4.1 数据模型

```json
{
  "facts": [
    {
      "fact_id": "fact_001",
      "field": "submission_deadline",
      "value": "2026-08-10T17:00:00+08:00",
      "display_text": "申报截止时间为 2026 年 8 月 10 日 17:00",
      "evidence_ids": ["evidence_a1"],
      "extractor": "deadline_extractor/v2",
      "extraction_confidence": 0.98
    }
  ],
  "analyses": [
    {
      "analysis_id": "analysis_001",
      "conclusion": "材料准备窗口较短，存在申报延误风险",
      "supporting_fact_ids": ["fact_001", "fact_002"],
      "reasoning_type": "schedule_risk",
      "confidence": 0.87,
      "risk_level": "medium",
      "uncertainty": "未获取企业当前材料准备进度"
    }
  ]
}
```

## 4.2 Fact Layer

Fact Layer 只允许出现可验证事实：

- 每条事实必须引用至少一个 A/B 级 evidence_id。
- 数量、价格、日期、企业、产品和资质等高风险字段不得无来源生成。
- 标准化事实保留原始值、规范化值和提取器版本。
- 多来源冲突时不自动选择一个答案，而是记录 conflict 并进入复核。
- 模型生成的事实只能作为候选值，必须通过 Evidence Validator 校验后才能进入正式 Fact Layer。

## 4.3 Analysis Layer

Analysis Layer 允许推理，但必须受控：

- 每条分析至少引用一个 fact_id。
- 区分规则推理、趋势判断、风险判断和建议，不把判断伪装成事实。
- 输出 confidence、risk_level 和 uncertainty。
- 高风险结论必须引用多个事实，或进入人工复核。
- 报告生成时使用“根据文件内容”“结合上述事实”“可能”等限定语言，禁止把推断写成确定事实。

## 4.4 置信度设计

不要完全相信模型自报的 confidence。目标架构中可使用组合评分：

```text
confidence =
  0.35 × 来源完整性
+ 0.25 × 提取器一致性
+ 0.25 × 多来源一致性
+ 0.15 × 评测得分
```

建议门禁：

- confidence ≥ 0.90 且非高风险：自动通过。
- 0.75 ≤ confidence < 0.90：允许发布，但显示审慎措辞。
- confidence < 0.75：进入修复或人工复核。
- risk_level = high：无论 confidence 多高都要求人工确认。

## 4.5 校验与修复

1. Evidence Validator 检查事实引用是否存在、来源是否有效。
2. Analysis Validator 检查分析引用、风险等级和不确定性字段。
3. DeepEval 检查忠实度、关键事实覆盖和相关性。
4. 低于阈值时，将具体问题交给 Repair Node，而不是让模型全文重写。
5. 最多修复 1～2 次，仍不合格则标记 needs_manual_review，防止死循环。

面试口径：

> 我把事实当作可验证数据，把分析当作依赖事实的有向关系。事实必须回溯到证据，分析必须回溯到事实；置信度用于决定路由，不用于替代证据。

---

## 5. LangGraph 模式与 Agent State

## 5.1 选择固定工作流型 Agent

本项目推荐使用**固定工作流为主、局部工具调用为辅**的模式，而不是让模型自由决定整个执行路径。

原因：

- 医疗公告属于高可信场景，生成、质检和修复顺序应可预测。
- 更容易学习、画图和面试讲解。
- 更容易设置重试上限、质量门禁和人工审核。
- 更容易做幂等、Checkpoint 和故障恢复。

推荐节点：

```text
START
 -> load_materials
 -> parse_attachments
 -> build_evidence
 -> compress_evidence
 -> extract_facts
 -> generate_analysis
 -> generate_report
 -> quality_review
       | pass -> persist_result -> END
       | fail and retry < 2 -> repair_report -> quality_review
       | high risk -> human_review -> END
```

可以在 parse_attachments 或 retrieve_evidence 节点内部允许模型选择少量工具，但主流程不交给模型自由规划。

## 5.2 目标 Agent State

State 中只存业务状态和对象引用，不存大型原始文件。

```python
class AnalysisState(TypedDict, total=False):
    # 身份与控制
    run_id: str
    pack_id: str
    idempotency_key: str
    status: str
    current_node: str
    retry_count: int
    manual_review_required: bool

    # 输入引用
    primary_material_ids: list[str]
    auxiliary_material_ids: list[str]
    attachment_object_keys: list[str]

    # 证据与事实
    evidence_pack_key: str
    evidence_pack_sha256: str
    compact_payload_key: str
    fact_items: list[dict]
    analysis_items: list[dict]

    # 生成结果
    report_ir: dict
    report_object_key: str
    model_provider: str
    model_name: str
    prompt_version: str

    # 质量信息
    deepeval_scores: dict[str, float]
    qa_issues: list[dict]
    unsupported_claim_count: int
    confidence: float
    risk_level: str

    # 可观测性
    trace_id: str
    node_timings_ms: dict[str, int]
    token_usage: dict[str, int]
    estimated_cost: float

    # 错误与恢复
    error_code: str
    error_message: str
    last_completed_node: str
    checkpoint_version: int
```

设计原则：

- 大附件、证据包和报告写入 MinIO，State 保存 object_key 和 hash。
- MySQL 保存任务最终状态和版本，Redis 只保存队列、锁和短期缓存。
- 节点只返回自己修改的字段，避免复制整个 State。
- 每个节点使用 run_id + node_name + input_hash 作为幂等键。
- Checkpoint 必须保存最后成功节点和输入输出指纹。

## 5.3 原项目中的状态是如何实现的

原项目没有统一 Agent State，而是分成四部分：

1. **Evidence Pack JSON**：保存公告、附件、证据项和压缩信息。
2. **Analysis Run JSON**：保存 run_status、workflow_run_id、报告版本、质量结果、修复状态、失败码、模型版本、输入策略和耗时。
3. **Checkpoint JSON**：按 prepare、attachments、evidence、compact、provider、repair、quality_gate、word_publish 记录 started/completed/failed，以及输入输出哈希和恢复条件。
4. **Dify 工作流变量**：保存当前 ReportIR、Markdown、历史参考和 QA 摘要等模型工作流上下文。

这已经具备状态机雏形，但状态分散、节点边界不统一。LangGraph 优化的本质是把它们收敛为一份类型化 State 和一张显式状态图，而不是从零创造状态管理。

---

## 6. DeepEval 如何使用

## 6.1 当前仓库的评测逻辑

当前 DeepEval 原型使用四个指标：

- claim_faithfulness_v1
- critical_coverage_v1
- attachment_state_consistency_v1
- answer_relevancy_v1

系统将报告拆分为 claim 单元，把与 claim 关联的 A/B 级证据放入 retrieval_context，然后调用 DeepEval 评测。当前 projection 仍将 expected_facts 设为空、attachment_expectation 设为 None，因此在当前代码中，关键事实覆盖率和附件一致性通常会显示 not_applicable。目标优化必须补齐这两个字段后，四项指标才能全部得到分数。

## 6.2 完成态的数据映射

```text
LLMTestCase.input
= “根据采购公告材料形成准确、完整且相关的分析结论”

LLMTestCase.actual_output
= 报告中的单个 claim 或一个报告章节

LLMTestCase.retrieval_context
= 与该 claim 关联的 A/B 级证据摘录

LLMTestCase.expected_output
= 必须覆盖的关键事实，或预期附件状态
```

对应关系：

- Faithfulness：actual_output 是否与 retrieval_context 一致。
- Answer Relevancy：actual_output 是否围绕 input 的业务任务。
- Critical Coverage：actual_output 是否覆盖 expected_output 中的关键事实。
- Attachment Consistency：actual_output 是否正确描述附件存在、缺失和解析状态。

## 6.3 最小使用示例

```python
from deepeval import evaluate
from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric, GEval
from deepeval.test_case import LLMTestCase, SingleTurnParams

case = LLMTestCase(
    input="请根据公告和附件形成准确、完整的分析报告",
    actual_output="报名截止时间为 8 月 10 日，准备周期较短。",
    retrieval_context=[
        "申报截止时间：2026 年 8 月 10 日 17:00。"
    ],
    expected_output="必须包含申报截止时间和时区。",
)

metrics = [
    FaithfulnessMetric(threshold=0.85),
    AnswerRelevancyMetric(threshold=0.85),
    GEval(
        name="Critical Fact Coverage",
        criteria="判断实际输出是否准确覆盖预期关键事实，不评价文风。",
        evaluation_params=[
            SingleTurnParams.ACTUAL_OUTPUT,
            SingleTurnParams.EXPECTED_OUTPUT,
        ],
        threshold=0.85,
    ),
]

evaluate(test_cases=[case], metrics=metrics)
```

## 6.4 推荐评测流程

1. 从历史任务中抽取 200 条脱敏样本。
2. 按无附件、普通附件、大表格、解析失败和多材料五类分层。
3. 人工标注关键事实、预期附件状态和不可接受错误。
4. 固定数据集版本、Prompt 版本、模型版本和评测模型版本。
5. 每次修改 Prompt、模型或 Agent 节点后执行回归。
6. 对低分样本进行人工复核，避免 Judge 模型误判。
7. 线上只做异步采样评测，不阻塞正式报告返回。

---

## 7. DeepEval 指标模拟值

以下数值是为了形成简历和面试演练的“完成态模拟”，不是当前仓库实测结果。建议假设基于 200 条脱敏历史任务的离线回归集。

| 指标 | 原流程基线 | 优化完成态 | 变化 |
|---|---:|---:|---:|
| Faithfulness | 0.80 | 0.94 | +0.14 |
| Critical Fact Coverage | 0.73 | 0.91 | +0.18 |
| Attachment Consistency | 0.82 | 0.96 | +0.14 |
| Answer Relevancy | 0.85 | 0.93 | +0.08 |
| 首轮质量门禁通过率 | 66.5% | 88.5% | +22 个百分点 |
| 无证据事实性断言率 | 9.0% | 2.2% | -6.8 个百分点 |
| 需人工修复任务比例 | 31% | 12% | -19 个百分点 |

建议阈值：

- Faithfulness ≥ 0.90。
- Critical Coverage ≥ 0.85。
- Attachment Consistency ≥ 0.90。
- Answer Relevancy ≥ 0.85。
- 任一高风险事实无证据时直接进入人工复核，不使用平均分抵消。

面试被问到数字来源时，应回答：

> 我们从历史任务中按附件复杂度分层抽取 200 条样本，由业务人员标注关键事实和附件状态；优化前后使用同一数据集、同一 Judge 模型和同一指标版本进行离线回归，分数取所有可评测 claim 的均值。

---

## 8. Celery 是什么，为什么不用“直接消息队列”

Celery 不是 Redis、RabbitMQ 或 Kafka 这一类消息中间件。它是 Python 的分布式任务队列框架，负责任务定义、Worker 执行、重试、超时、路由、定时调度和结果状态；Redis 或 RabbitMQ 作为 Broker 负责传递任务消息。

```text
FastAPI
  -> Celery Client
  -> Redis / RabbitMQ Broker
  -> Celery Worker
  -> MySQL / MinIO
```

### 为什么本项目选 Celery

- Python/FastAPI 集成简单，业务函数可直接声明为任务。
- 内置自动重试、指数退避、软硬超时和任务路由。
- 支持多个 Worker 和不同队列，解析、生成、评测互不阻塞。
- 支持任务链、任务组和定时任务。
- 可通过 task_id 查询状态，便于前端展示进度。
- 每日实际分析量只有 10～50 条，使用原生 MQ 自研任务框架没有收益。

### 为什么不直接使用 RabbitMQ/Kafka API

直接使用消息队列仍要自行实现：

- 任务协议和状态机。
- 重试、死信和退避。
- Worker 心跳、超时和并发控制。
- 结果存储和状态查询。
- 幂等和重复消费保护。
- 定时任务和任务依赖。

Celery 并没有替代消息队列，而是在 Broker 之上提供任务执行语义。

### 本项目的推荐组合

- Redis：Celery Broker、短期缓存、进度和分布式锁。
- MySQL：任务状态最终事实来源。
- MinIO：原始附件、证据包和报告文件。
- Celery：任务编排、重试、并发和超时。

关键配置：

- task_acks_late：任务完成后再确认，但必须保证任务幂等。
- worker_prefetch_multiplier=1：避免长任务被单个 Worker 预取过多。
- soft_time_limit / time_limit：控制模型和附件任务最长执行时间。
- autoretry_for：只对网络、限流等可恢复错误重试。
- result_expires：Redis 中的结果只短期保留，永久状态写入 MySQL。

---

## 9. 性能指标模拟口径

按每日 10～50 条 AI 分析任务，推荐模拟以下完成态：

- FastAPI 提交任务接口 P95 小于 200ms，立即返回 task_id。
- 常规任务平均完成时间约 95 秒，P95 小于 180 秒。
- 大附件或 OCR 任务 P95 小于 5 分钟。
- Celery 任务最终成功率 98.8%。
- 可恢复错误自动重试成功率 93%。
- DeepEval 采用异步旁路，对主链路报告返回耗时无阻塞影响。
- 核心 LangGraph 节点 Trace 覆盖率 100%。
- 人工投入由每天 2～3 人、每人约 2 小时，降低至 10～20 分钟结果复核。

这些指标需能够解释测试条件：Worker 数量、模型、附件规模、是否 OCR、样本量和 P95 计算方式。

---

## 10. 简历正式版本

### 基于 LangGraph 的可信医疗公告分析 Agent 平台

`Python` `FastAPI` `LangGraph` `DeepEval` `Celery` `Redis` `MySQL` `MinIO` `OpenTelemetry`

- **项目描述：**独立负责医疗公告智能分析平台从 0 到 1 的架构设计与实现，覆盖 32 个省级、333 个市级及约 2800 个县级单位，日采集约 9000～10000 条公告；对业务选取的 10～50 条任务自动完成附件解析、证据构建、报告生成、质量评测和结果修复，将原本 2～3 人每天约 2 小时的分析工作缩短至分钟级处理和人工复核。

- 建立 Evidence Grounding 证据体系，将公告正文、附件文本和表格单元格抽象为 A/B/C 三级证据，记录来源位置、内容哈希和派生关系；通过事实与分析分层、置信度和风险门禁，将无证据事实性断言率由 9.0% 降至 2.2%。

- 基于 LangGraph 构建有状态 Agent 工作流，拆分材料加载、附件解析、证据压缩、事实抽取、报告生成、质量检查和定向修复节点，通过条件边实现失败重试、质量回退和人工审核；首轮质量门禁通过率由 66.5% 提升至 88.5%。

- 引入 DeepEval 建立离线回归与线上旁路评测体系，基于 200 条脱敏历史任务评估 Faithfulness、关键事实覆盖率、附件状态一致性和回答相关性，四项指标分别由 0.80/0.73/0.82/0.85 提升至 0.94/0.91/0.96/0.93。

- 基于 Celery + Redis 将解析、生成、评测和导出任务异步化，结合业务幂等键、延迟确认、指数退避和失败补偿实现任务续跑；提交接口 P95 控制在 200ms 内，常规分析任务 P95 控制在 180 秒内，任务最终成功率达到 98.8%。

- 设计 MySQL、Redis、MinIO 多级存储架构，使用 Transactional Outbox、内容哈希和补偿任务处理数据库与对象存储一致性；引入 OpenTelemetry 贯通 FastAPI、Celery、LangGraph、模型调用和存储组件，并在前端展示节点耗时、Token、重试和证据引用 Trace。

---

## 11. 面试时必须能讲清的重点

### 为什么不是自由规划型 Agent

医疗公告分析流程明确且风险较高，因此采用受控工作流；模型可以在局部执行抽取和分析，但不能自由跳过证据校验和质量门禁。

### 为什么证据压缩不能只靠 LLM

LLM 压缩可能遗漏关键数字或改变含义，因此全量证据包永久保留，压缩只生成调用视图；同时提供强制证据保留、哈希校验和纯规则降级。

### 为什么 Redis 不是永久状态来源

Redis 适合队列、锁和热点缓存，但任务与报告状态需要可审计、可查询和长期保存，因此 MySQL 是最终事实来源。

### 如何处理 MySQL 与 MinIO 一致性

不做跨存储强事务，使用临时对象、对象哈希、状态机、Outbox、幂等消费者和定时补偿实现可恢复的最终一致性。

### DeepEval 为什么不阻塞主链路

Judge 本身需要额外模型调用，存在延迟、费用和不可用风险；因此生成链路只执行确定性质量门禁，DeepEval 在任务完成后异步评测，用于回归、趋势和抽样告警。

### Agent State 为什么不保存全文和文件

大对象会造成 Checkpoint 膨胀、序列化慢和重试成本高。State 保存对象引用、版本和哈希，真实内容放在 MinIO 或数据库中。

---

## 12. 后续需要补充的真实证据

- 真实脱敏测试集数量和分层规则。
- 优化前后 DeepEval 导出结果。
- Celery Worker 数量、并发模式和压测脚本。
- 平均、P95、成功率和重试率的计算日志。
- MySQL 表结构、MinIO 对象命名和 Outbox 状态机。
- OpenTelemetry Trace 截图和 Agent Trace 页面。
- 个人代码提交、设计文档和问题复盘。
