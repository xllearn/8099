# 医疗公告智能分析 Agent：项目包装总上下文

> 本文件是本项目的**跨对话唯一入口文档**。后续任何关于简历包装、架构学习、面试问答、功能扩展和指标设计的对话，都应先读取本文件，再按文档索引读取详细内容。
>
> 维护分支：`codex/deepeval初步实现`
>
> 最近更新：2026-08-04

---

## 0. 给后续对话助手的使用说明

1. 先读取本文件，确认当前统一口径。
2. 必须区分：
   - **当前仓库可验证实现**；
   - **为了简历包装而假设已经完成的目标架构**；
   - **模拟指标或目标指标**。
3. 不得把模拟指标描述成真实生产压测结果，除非用户后续补充真实数据。
4. 用户希望以“独立负责项目从 0 到 1 设计与实现”的角度准备简历和面试，因此回答必须能够支撑架构、数据流、异常处理、性能和指标口径追问。
5. 出现字段名、配置名、指标名或组件名时，必须同时解释：中文含义、数据来源、存在原因、读写节点和脱敏示例。
6. 不得只回答最终设计，还要区分“原项目怎么做”和“目标优化后怎么做”。
7. 用户询问完整数据流时，需要说明每一步输入、输出、存储、State变化、失败处理、重试、超时、安全和并发控制。
8. 后续每次形成新的项目包装决策，应同时检查四份文档是否需要同步更新。

### 0.1 文档分工与同步规则

| 文档 | 作用 | 何时更新 |
|---|---|---|
| `PROJECT_PACKAGING_CONTEXT.md` | 当前最终包装口径，新对话第一入口 | 产品边界、架构、指标或简历叙事变化时 |
| `docs/career/medical-notice-analyzer-question-response-log.md` | 问题、推理、设计取舍和面试回答 | 每次出现新的架构或学习问题时，编号递增追加 |
| `docs/career/medical-notice-analyzer-dataflow-learning-notes.md` | 字段、数据格式、Agent State和Celery等详细学习内容 | 数据结构或流程变化时 |
| `docs/career/medical-notice-analyzer-resume-packaging.md` | 可直接使用的简历描述 | 项目定位、技术栈或指标口径确定后 |

同步顺序：问题日志记录“为什么” → 总上下文记录最终结论 → 学习笔记补充字段和数据流 → 简历文档同步外部表述。总上下文与详细文档冲突时，以总上下文为当前口径，但必须尽快修复详细文档。

---

## 1. 用户求职与项目叙事目标

### 1.1 目标岗位

- Agent 开发工程师
- AI 应用工程师
- AI 后端工程师
- 可兼容传统后端开发岗位

### 1.2 个人职责口径

> 独立负责医疗公告智能分析平台从 0 到 1 的架构设计与实现。

面试时必须能够讲清 Evidence Pack、A/B/C证据、事实与分析分层、GraphRAG辅助材料召回、LangGraph State/Node/Edge/Checkpoint、Celery与多级存储、幂等和失败恢复、DeepEval分层评测、用户反馈补证修订、偏好记忆和OpenTelemetry可观测性。

---

## 2. 已确认的业务与模型数据

- 数据覆盖约32个省级单位、333个市级单位、2800个县级单位，以及国家医疗保障局、卫生健康相关单位和大型医疗机构。
- 日采集公告约9000～10000条。
- 内部验证阶段正式进入AI分析链路的任务通常每天10份以内，使用者约为测试人员2～3人、业务人员3～5人。
- 客户化包装完成态按**日均300～500份分析任务、活动期峰值约1000份/日**进行容量设计。
- 上述客户化数字是**目标容量和简历模拟口径**，不是当前真实生产业务量。
- 单个公告通常包含0～10个附件，多数为0～4个附件。
- 主要公告类型为带量采购、集中采购及接续采购。
- 单份正式报告通常为2000～5000字，至少4～5个章节。
- 原人工单次流程通常需要2～3人协作约2小时；系统目标是缩短至分钟级生成和人工复核。

### 2.1 业务规模分层

| 阶段 | 分析任务量口径 | 性质 | 主要架构含义 |
|---|---:|---|---|
| 内部验证阶段 | 通常不超过10份/日 | 历史业务事实 | 单主Worker可运行，但仍需异步化和恢复 |
| 客户化常态 | 日均300～500份 | 目标容量/模拟口径 | 队列隔离、Worker水平扩容、模型并发控制 |
| 活动期峰值 | 约1000份/日 | 峰值容量/模拟口径 | 限流、配额、优先级、缓存复用和积压恢复 |

不能用日采集公告量替代日分析任务量。

### 2.2 模型分工

- 报告生成：官方API `model=deepseek-v4-pro`。
- 质检、自动修复和用户反馈修订：官方API `model=deepseek-v4-flash`。
- DeepEval Judge：公司部署的GLM-5与DeepSeek V4 Flash。

---

## 3. 当前仓库可验证实现

当前项目是医疗公告证据约束分析和报告生成系统，主要链路为：

```text
数据库选择公告
  -> 读取公告正文和附件元数据
  -> 下载并按格式解析附件
  -> 构建完整 Evidence Pack
  -> 生成 Compact Payload
  -> 后端代理 Dify Workflow 生成 Markdown 与 ReportIR
  -> 本地质量门与受控修复
  -> 保存运行状态、Checkpoint和版本
  -> 用户反馈后生成修订版本
  -> 受控导出 Word
  -> DeepEval 非阻塞旁路评测
```

### 3.1 当前技术特征

- FastAPI作为服务端API和编排入口。
- MySQL读取公告正文和附件元数据。
- 针对PDF、Word、Excel、CSV、HTML、ZIP等格式使用专用解析逻辑。
- 完整Evidence Pack主要由确定性解析器和规则构建；长输入支持LLM语义压缩和规则降级。
- A类为直接证据，B类为基于A的结构化事实，C类为摘要、指导、诊断和记忆等辅助信息。
- 完整证据包与发送给模型的Compact Payload分离。
- 生成结果包含Markdown和结构化ReportIR。
- 当前存在质量门、Claim-Evidence Index、受控修复、运行状态、Checkpoint、历史对比、报告记忆和用户反馈修订能力。
- 当前DeepEval为独立、非阻塞的Advisory Sidecar。

### 3.2 必须诚实说明的限制

- 不能承诺任意PDF、Word和Excel实现100%无损解析。
- 扫描件、跨页表格、合并单元格、公式、宏和大型Excel存在解析边界。
- LLM压缩有损，只能通过关键证据保护、损失记录和人工复核降低风险。
- 当前DeepEval主要评价报告，不能单独证明Evidence Pack完整。
- 当前长期记忆不是完整向量RAG。
- 公司GraphRAG、统一LangGraph编排、Celery弹性队列和以下完整安全机制属于本项目的**包装完成态**，不能描述为当前仓库全部已经可验证。

---

## 4. 简历假设完成的目标架构

### 4.1 DeepEval质量评测体系

建立离线回归与线上异步旁路评测。基础指标包括Faithfulness、Critical Fact Coverage、Attachment State Consistency、Answer Relevancy；扩展Contextual Precision/Recall、数值日期Exact Match、结构完整性、连贯性、流畅性和反馈遵循度。固定测试集、Prompt、工作流、指标和Judge版本进行对比。

### 4.2 事实与分析分层

- 事实层只能输出有A/B级证据支持的内容。
- **C级不是推理结论层。**C级保存摘要、生成指导、告警、诊断和受控偏好等辅助信息，不能单独证明事实。
- 推理结论进入独立`Analysis Layer`，每条`analysis_item`必须引用`supporting_fact_ids`，并记录`confidence`、`risk_level`和`uncertainty`。
- “公司应在5月20日前准备材料”属于分析或建议，不属于C级证据。
- 价格、数量、时间、企业、产品、资质和中选条件属于高风险字段。

### 4.3 LangGraph Agent Orchestration

采用固定工作流型Agent：

```text
create_run
  -> retrieve_auxiliary_materials
  -> load_materials
  -> parse_attachments
  -> build_evidence
  -> compress_evidence
  -> extract_facts
  -> generate_analysis
  -> generate_report
  -> quality_review
      -> pass: persist_result
      -> fixable and auto_repair_count < 2: repair_report -> quality_review
      -> blocking/high_risk: human_review
  -> enqueue_deepeval
```

State只保存必要的小型状态和对象引用，不直接保存大型附件或完整证据包。每个节点通过`run_id + node_name + input_hash`实现幂等，并在成功节点后保存Checkpoint。

### 4.4 模型抽象调用层

统一封装DeepSeek V4 Pro、DeepSeek V4 Flash和GLM-5，支持结构化输出、Schema校验、连接/读取/总超时、可恢复异常重试、指数退避和抖动、并发信号量、Token统计、版本审计、模型降级和错误分类。

### 4.5 Celery + Redis异步任务体系

- FastAPI鉴权、额度、限流、幂等检查后，在MySQL事务创建`run_id`和Outbox，立即返回任务已接收状态。
- Celery Worker后台执行LangGraph；附件、生成、质检、用户修订、导出和DeepEval使用独立队列。
- Redis作为Broker、短期缓存和分布式锁；MySQL是业务最终状态源。
- Worker根据队列积压、最老任务年龄、队列等待P95和模型并发配额水平扩容或收缩。
- 通过`acks_late`、幂等键、Checkpoint和MySQL Outbox处理Worker崩溃、重复投递及Redis短时不可用。
- 主链路积压时暂停或降低低优先级DeepEval消费。

### 4.6 MySQL + MinIO + Redis多级存储

- MySQL：公告与客户任务元数据、状态、版本、材料关系、用户反馈、偏好候选、索引及指标汇总。
- MinIO：原始附件、解析产物、完整Evidence Pack、Compact Payload、ReportIR、Markdown、Word/PDF和大明细。
- Redis：队列、短期缓存、进度、锁和热点结果。
- 使用Transactional Outbox、临时对象、内容哈希、幂等消费者和补偿任务处理最终一致性。

### 4.7 OpenTelemetry全链路可观测性

贯通FastAPI、Celery、LangGraph节点、GraphRAG、模型调用、MySQL、Redis、MinIO和DeepEval，记录Trace、Metric和结构化日志。敏感原文、完整Prompt、令牌、文件路径和个人信息不得直接写入日志。

### 4.8 统一的端到端防护原则

兜底、重试、超时、安全和并发不是最后一步才执行，而是贯穿全部节点：

- **兜底**：专用解析器失败转OCR或通用解析器；LLM压缩失败转规则压缩或分阶段生成；GraphRAG不可用时使用关键词/结构化检索或仅用用户选材；DeepEval失败不阻塞报告。
- **重试**：只重试网络、限流、短时存储不可用等可恢复异常；格式错误、权限错误、证据冲突等不可恢复异常不盲目重试。
- **超时**：HTTP、下载、OCR、解析、GraphRAG、模型、导出和整任务分别设置超时；Watchdog检测长期无心跳任务并恢复或转人工。
- **安全**：鉴权与租户审计、共享知识库范围过滤、文件类型和大小校验、宏不执行、ZIP防路径穿越、提示词注入隔离、敏感日志脱敏、对象下载使用短时授权。
- **并发**：附件解析可按文件并行；同一附件用内容哈希和分布式锁避免重复解析；生成、质检和GraphRAG分别受并发池与供应商配额控制；同一报告版本用乐观锁避免并发覆盖。

---

## 5. GraphRAG与辅助材料自动选择

### 5.1 产品边界

所有外部用户使用同一份共享资料体系：公开项目公告资料库和公司审核行业知识库。不支持用户上传个人文件、建立客户私有知识库或维护客户独立知识空间。用户偏好记忆属于账户级样式配置，不是客户私有事实知识库。

### 5.2 材料数量与角色

- 主材料：用户显式选择1～3条项目公告，决定当前报告的事实范围。
- 辅助材料：0～10条，可由用户手动选择，也可由系统自动召回；类型可以是补充/更正公告、历史轮次、政策、地区规则、公司审核分析或风险规则，不应只描述为“项目公告类型”。
- 同一资料不能同时作为主材料和辅助材料。
- 辅助材料不得覆盖或改写主材料事实。

### 5.3 公司GraphRAG完成态

公司提供的GraphRAG作为共享检索引擎，组合：

1. 关键词检索；
2. 向量语义检索；
3. 知识图谱关系扩展；
4. 地区、品类、公告阶段、时间和`source_scope`结构化过滤；
5. Rerank、去重、有效期过滤和证据充分性判断。

```text
主公告 + 分析目标
  -> 查询理解与实体识别
  -> 关键词/向量/图关系候选召回
  -> 元数据和权限过滤
  -> Rerank与去重
  -> 选取1～10条辅助材料
  -> 记录召回原因和图关系路径
  -> 解析并构建任务级Evidence Pack
```

GraphRAG返回的是候选资料，不是可直接写入报告的事实；资料必须经过解析、来源定位和A/B证据构建。

关键字段：`document_id`、`document_type`、`retrieval_reason`、`retrieval_score`、`relation_type`、`graph_path`、`source_scope`、`effective_date`、`selected`、`retrieval_version`。

---

## 6. 公司指定的付费知识库设计

候选类型包括集采项目关系与时间线、政策规则标准化、地区准入与挂网规则、历史中选价格采购量、企业产品主数据、公司审核历史分析、风险规则与公开案例、行业术语本体、专家问答和数据质量修复。首期优先：项目时间线、政策规则、历史中选价格采购量、公司审核分析、风险规则案例。

---

## 7. 用户反馈、修订和偏好记忆

### 7.1 三类反馈路由

1. **样式或结构修改**：当前报告 + 用户反馈 + 原Evidence Pack，不能只把报告和反馈交给LLM。
2. **当前公告补证**：回到当前公告完整解析资产定向检索，补建带`source_ref`的新A/B证据。
3. **共享知识扩展**：调用GraphRAG召回公开公告库和公司审核知识库，构建`revision_evidence_pack`后局部修订。

每次修订保存反馈文本、意图分类、召回资料、Evidence Pack版本、前后差异、修订模型、QA结果和采纳状态。

### 7.2 次数和失败计算

- 系统自动修复最多2次，由`auto_repair_count`记录。
- 用户自助反馈修订最多3轮，由`user_revision_count`记录；超过3轮或仍存在阻断问题时转人工。
- 网络重试、Celery重复投递和同一轮内部模型重试不计入用户修订轮次。
- 使用报告版本号和乐观锁避免两个反馈请求覆盖彼此。

### 7.3 偏好记忆

- 用户反馈日志与长期记忆分开保存。
- 只提取稳定的样式偏好，例如“优先表格”“结论简洁”“增加数值对比”；项目事实、一次性要求、敏感内容和跨客户数据不得进入偏好记忆。
- 生成结束后形成`memory_candidate`，状态为`pending`；只有用户明确同意后才转为`approved/active`。
- 用户可以查看、修改、撤销和删除偏好；记忆有版本、来源反馈、适用范围和过期策略。
- 偏好以受控结构化字段加载到Prompt上下文，优先级低于系统规则、证据和当前用户指令，不能改变事实。

---

## 8. 上下文压缩与Prompt口径

### 8.1 24万字符的正确含义

`application_input_cap_chars=240000`可以作为应用层硬上限配置，但不能把证据包压缩到“尽量填满24万字符”。最终预算还必须扣除系统提示词、任务提示词、偏好上下文、工具消息、输出Token和安全余量，并以Token估算做最终校验。

建议流程：

```text
Full Evidence Pack
  -> 去重与低价值清理
  -> Mandatory Evidence保护
  -> 规则压缩
  -> 必要时受约束LLM分块压缩
  -> Token预算复核
  -> 超限则分阶段生成或人工复核
```

LLM压缩失败时使用规则降级；Mandatory Evidence不能保留时不得强行生成。

### 8.2 Prompt组成

1. 系统规则：模型角色、证据边界、安全规则、输出Schema。
2. 当前任务指令：分析目标、报告结构和用户本次要求。
3. Evidence Context：Compact Payload及来源引用。
4. 可选偏好Profile：用户已确认的样式偏好，使用结构化白名单字段。

公告正文、附件、RAG资料和用户反馈均视为不可信数据，使用明确分隔符包裹，不允许其中的“忽略系统规则”等内容改变系统指令。

---

## 9. 质量、修复、交付和DeepEval

### 9.1 同步质量门

同步质量门检查ReportIR Schema、必需章节、发布机构、日期、标的物、附件状态、数值日期精确匹配、Claim-Evidence支持、历史事实泄漏、来源定位和禁用表达。输出`QAResult`，区分`passed`、`needs_fix`和`blocked`。

### 9.2 修复与二次质检

先执行确定性修复，再使用DeepSeek V4 Flash对指定段落局部修订。修订输入包含问题码、目标段落、允许使用的A/B证据和禁止新增事实约束。修复后必须重新执行相同质量门。高风险无证据事实、关键附件失败或两次自动修复仍失败时进入人工复核。

### 9.3 最终后端校验

最终校验负责确认非空输出、ReportIR与Markdown一致、对象哈希、版本关系、MySQL/MinIO持久化、状态迁移和可交付标志。Watchdog、节点超时和重试贯穿全链路，而不是只在最终校验阶段才开始。

前端立即展示确定性质量指标，例如`claim_ab_support_rate`、`unsupported_claim_count`、`missing_topic_ids`和`deliverable`；DeepEval保持异步旁路，前端显示`pending/completed/unavailable`，不能因Judge不可用阻塞报告交付。

### 9.4 Word导出

仅`deliverable=true`的报告允许正式导出。DOCX由ReportIR受控渲染，文件名清洗，模板和图片来源白名单，临时下载链接短时有效；导出失败可单独重试，不重新调用生成模型。

---

## 10. 指标口径

### 10.1 报告质量

Claim Support Rate、Critical Fact Coverage、Unsupported Claim Rate、Numeric/Date Exact Match、Section/Topic Completeness、First-pass Quality Gate Pass Rate、Revision Acceptance Rate。

### 10.2 解析、Evidence与检索

Attachment Parse Success Rate、Source-to-Evidence Precision/Recall/F1、Locator Accuracy、Mandatory Evidence Retention、Contextual Precision/Recall/Relevancy、Top-K Hit Rate、MRR、图关系命中率和过期资料召回率。

### 10.3 系统工程

API提交P95、队列等待P95、端到端P50/P95/P99、日均/峰值完成任务数、峰值提交速率、最大稳定并发、积压量与清空时间、成功率、超时率、重试率、缓存命中率、Token和单任务成本。

### 10.4 模拟完成态指标

以下仅用于简历演练，不是真实生产数据：日均300～500份、峰值约1000份/日、50份/小时、稳定并发20、队列等待P95 60秒以内、常规任务P95 180秒以内、最终成功率98.8%；Faithfulness 0.80→0.94、Critical Fact Coverage 0.73→0.91、Attachment Consistency 0.82→0.96、Answer Relevancy 0.85→0.93、首轮质检通过率66.5%→88.5%、无证据断言率9.0%→2.2%。

---

## 11. 推荐简历项目定位

### 项目名称

**基于LangGraph与Evidence Grounding的医疗采购公告分析Agent平台**

### 一句话定位

面向医疗器械带量采购和集中采购场景，独立设计并实现基于证据约束、公司GraphRAG自动资料召回、LangGraph编排和大模型质量评测的公告分析平台，将公告正文、附件、历史项目和公司行业知识转化为可追溯结构化报告，并通过异步任务、分层存储、失败恢复和全链路可观测性保障客户化运行。

---

## 12. 历史核心问题覆盖索引

| 历史问题 | 当前统一结论 | 详细文档 |
|---|---|---|
| 字段名必须解释 | 同时解释含义、来源、原因、读写节点和示例 | 问题日志第9题 |
| 压缩是否丢失 | 不能承诺零损失；完整包保留，关键证据保护 | 问题日志第10题 |
| A/B是否冗余 | A负责原文追溯，B负责结构化计算 | 问题日志第11题 |
| 完整数据格式 | 逐步说明输入输出、存储、State和失败处理 | 问题日志第12题、数据流笔记 |
| Agent State | State+Node+Edge+Checkpoint形成有状态工作流 | 问题日志第14题 |
| Celery | 负责跨任务异步执行、队列、重试和扩缩容 | 问题日志第15题 |
| 客户化容量 | 内部≤10份/日是历史事实；完成态日均300～500、峰值约1000为目标容量 | 问题日志第19题 |
| 完整端到端数据流 | C级与分析层分离，GraphRAG、压缩、修复、反馈和记忆采用本文件第4～9节口径 | 问题日志第20题 |

---

## 13. 相关详细文档

- `docs/career/medical-notice-analyzer-resume-packaging.md`：简历包装和面试手册。
- `docs/career/medical-notice-analyzer-dataflow-learning-notes.md`：数据流、字段、State、GraphRAG、Celery和安全机制。
- `docs/career/medical-notice-analyzer-question-response-log.md`：用户问题和应对记录，当前下一题编号为21。

---

## 14. 变更记录

### 2026-08-04：完整数据流口径

- 新增问题日志第20题，下一题编号更新为21。
- 确认公司GraphRAG作为包装完成态共享检索引擎，支持关键词、向量、知识图谱、结构化过滤和Rerank。
- 明确主材料1～3条、辅助材料0～10条；辅助材料不局限于项目公告。
- 纠正C级证据定义：推理结论属于Analysis Layer，不属于C级。
- 将24万字符定义为应用硬上限而非目标填充长度，增加Token安全预算和分阶段生成。
- 确认自动修复最多2次、用户自助修订最多3轮，分别计数。
- 明确超时、重试、Watchdog、安全和并发控制贯穿所有节点。
- 增加偏好记忆候选、用户确认、撤销删除和跨客户隔离规则。
- 明确同步质量门立即展示，DeepEval异步旁路不阻塞交付。

### 2026-08-04：客户化容量

- 保留内部验证阶段每天10份以内作为历史事实。
- 客户化完成态采用日均300～500份、峰值约1000份/日目标容量。
- 增加队列隔离、弹性Worker、账号限流和模型并发控制。

### 2026-08-03

- 创建跨对话项目包装总上下文。
- 确认共享公开公告库和公司审核知识库的产品边界。
- 明确不允许用户上传个人文件或创建私有知识库。
- 建立四份文档分工与同步规则。
