# 医疗公告智能分析 Agent：项目包装总上下文

> 本文件是本项目的**跨对话唯一入口文档**。后续任何关于简历包装、架构学习、面试问答、功能扩展和指标设计的对话，都应先读取本文件，再按文档索引读取详细内容。
>
> 维护分支：`codex/deepeval初步实现`
>
> 最近更新：2026-08-03

---

## 0. 给后续对话助手的使用说明

1. 先读取本文件，确认当前统一口径。
2. 必须区分：
   - **当前仓库可验证实现**；
   - **为了简历包装而假设已经完成的目标架构**；
   - **模拟指标或目标指标**。
3. 不得把模拟指标描述成真实生产压测结果，除非用户后续补充真实数据。
4. 用户希望以“独立负责项目从 0 到 1 设计与实现”的角度准备简历和面试，因此回答必须能够支撑架构、数据流、异常处理、性能和指标口径追问。
5. 用户目前对项目原理和新增技术均不熟悉。出现字段名、配置名、指标名或组件名时，必须同时解释：
   - 它是什么以及中文含义；
   - 数据从哪里来；
   - 为什么存在；
   - 哪些节点读取或写入；
   - 一个脱敏示例。
6. 不得只回答最终设计，还要区分“原项目怎么做”和“目标优化后怎么做”。
7. 用户询问完整数据流时，不能只画高层箭头；需要说明每一步输入格式、输出格式、存储位置、状态字段和失败处理。
8. 后续每次形成新的项目包装决策，应同时检查四份文档是否需要同步更新。

### 0.1 文档分工与同步规则

| 文档 | 作用 | 何时更新 |
|---|---|---|
| `PROJECT_PACKAGING_CONTEXT.md` | 当前最终包装口径，新对话第一入口 | 产品边界、架构、指标或简历叙事变化时 |
| `docs/career/medical-notice-analyzer-question-response-log.md` | 问题、推理、设计取舍和面试回答 | 每次出现新的架构或学习问题时，编号递增追加 |
| `docs/career/medical-notice-analyzer-dataflow-learning-notes.md` | 字段、数据格式、Agent State和Celery等详细学习内容 | 数据结构或流程变化时 |
| `docs/career/medical-notice-analyzer-resume-packaging.md` | 可直接使用的简历描述 | 项目定位、技术栈或指标口径确定后 |

同步顺序：

1. 问题日志记录“为什么”；
2. 总上下文记录最终结论；
3. 学习笔记补充字段和数据流；
4. 简历文档同步外部表述。

总上下文与详细文档冲突时，以总上下文为当前口径，但必须尽快修复详细文档。

---

## 1. 用户求职与项目叙事目标

### 1.1 目标岗位

- Agent 开发工程师
- AI 应用工程师
- AI 后端工程师
- 可兼容传统后端开发岗位

### 1.2 个人职责口径

简历主叙事采用：

> 独立负责医疗公告智能分析平台从 0 到 1 的架构设计与实现。

面试时必须能够讲清：

- Evidence Pack为什么存在，以及如何构建和压缩；
- A/B/C证据的职责和一致性；
- 事实层与分析层如何分离；
- LangGraph State、Node、Edge和Checkpoint；
- Celery、Redis、MySQL、MinIO如何分工；
- 数据一致性、幂等和失败恢复；
- DeepEval以及解析、检索、生成的分层指标；
- RAG如何自动选择辅助材料；
- 公司统一知识库如何审核、版本化和商业化。

---

## 2. 已确认的业务与模型数据

- 数据覆盖约32个省级单位、333个市级单位、2800个县级单位，以及国家医疗保障局、卫生健康相关单位和大型医疗机构。
- 日采集公告约9000～10000条。
- 正式进入AI分析链路的任务通常每天10份以内。
- 单个公告通常包含0～10个附件，多数为0～4个附件。
- 主要公告类型为带量采购、集中采购及接续采购。
- 单份正式报告通常为2000～5000字，至少4～5个章节；表格数量取决于公告是否含适合结构化展示的数据。
- 原人工流程通常需要2～3人协作约2小时；系统目标是缩短至分钟级生成和人工复核。
- 当前内部使用人员约为测试人员2～3人、业务人员3～5人。

### 2.1 模型分工

- 报告生成：官方API `model`字段为`deepseek-v4-pro`。
- 质检与用户反馈修订：官方API `model`字段为`deepseek-v4-flash`。
- DeepEval Judge：公司部署的GLM-5与DeepSeek V4 Flash。

`model`表示调用模型API时指定的实际模型标识，不只是简历中的营销名称。

---

## 3. 当前仓库可验证实现

当前项目是医疗公告证据约束分析和报告生成系统，主要链路为：

```text
数据库选择公告
  -> 读取公告正文和附件元数据
  -> 下载并按格式解析附件
  -> 构建完整 Evidence Pack
  -> 根据输入限制生成紧凑证据视图
  -> 后端代理 Dify Workflow 生成报告
  -> 输出 Markdown 与 ReportIR
  -> 执行本地质量门和结构修复
  -> 必要时进行受控修复
  -> 保存运行状态、检查点和历史版本
  -> 用户反馈后生成修订版本
  -> 受控导出 Word
  -> DeepEval 非阻塞旁路评测
```

### 3.1 当前技术特征

- FastAPI作为服务端API和编排入口。
- MySQL读取公告正文和附件元数据。
- 针对PDF、Word、Excel、CSV、HTML、ZIP等格式使用专用解析逻辑。
- 完整Evidence Pack主要由确定性解析器和规则构建；长输入可以使用LLM语义压缩，并提供规则压缩降级。
- Evidence Pack采用A/B/C分层：
  - A：原文、表格单元格等直接证据；
  - B：基于A生成的结构化、标准化事实；
  - C：摘要、指导、诊断和记忆等辅助内容。
- 完整证据包与发送给模型的Compact Payload分离。
- 生成结果包含Markdown和结构化ReportIR。
- 当前存在质量门、Claim-Evidence Index、受控修复、运行状态、Checkpoint、历史对比、报告记忆和用户反馈修订能力。
- 当前DeepEval为独立、非阻塞的Advisory Sidecar，不影响正式报告交付。
- 当前前端为原生HTML/CSS/JavaScript管理页面，但不是求职包装重点。

### 3.2 当前实现必须诚实说明的限制

- 不能承诺任意PDF、Word和Excel实现100%无损解析。
- 复杂扫描件、跨页表格、合并单元格和大型Excel存在解析边界。
- LLM压缩是有损的，只能通过强制证据保护、损失记录和人工复核降低风险。
- 当前DeepEval主要评价报告，不能单独证明Evidence Pack完整。
- 当前两个自定义评测输入尚未完全生成时，Coverage和附件一致性可能返回不适用。
- 当前长期记忆不是完整的向量RAG。

---

## 4. 简历假设完成的目标架构

### 4.1 DeepEval质量评测体系

- 建立离线回归与线上异步旁路评测。
- 基础指标：Faithfulness、Critical Fact Coverage、Attachment State Consistency、Answer Relevancy。
- 扩展指标：Contextual Precision、Contextual Recall、数值日期Exact Match、结构完整性、连贯性、流畅性和反馈遵循度。
- 使用固定测试集、指标版本、Prompt版本、Judge模型和数据分层进行前后对比。

### 4.2 事实与分析分层

- 事实层只能输出有A/B级证据支持的内容。
- 分析层允许基于事实推理，但必须引用结构化事实并记录未知条件。
- 通过置信度、风险等级和人工复核门禁控制可靠性。
- 价格、数量、时间、企业、产品、资质和中选条件属于高风险字段。

### 4.3 LangGraph Agent Orchestration

采用固定工作流型Agent为主：

```text
load_materials
  -> parse_attachments
  -> build_evidence
  -> compress_evidence
  -> extract_facts
  -> generate_report
  -> quality_review
  -> repair / human_review / persist_result
```

- State：当前任务共享数据快照；
- Node：执行具体步骤的函数；
- Edge：根据State决定下一步；
- Checkpoint：可恢复的State历史快照。

Agent State只保存必要的小型状态、结果和对象引用，不直接保存大型PDF或完整证据包。

### 4.4 模型抽象调用层

统一封装DeepSeek V4 Pro、DeepSeek V4 Flash和GLM-5，支持结构化输出、Schema校验、超时、重试、并发限制、Token统计、Prompt/模型/工作流版本、模型降级和错误分类。

### 4.5 Celery + Redis异步任务体系

- FastAPI创建任务并返回`run_id`。
- Celery Worker后台执行一次LangGraph运行。
- Redis作为Broker、短期缓存和分布式锁。
- DeepEval进入低优先级评测队列。
- 附件较多时可并行解析。
- MySQL保存业务最终状态，不能只依赖Celery Result Backend。

### 4.6 MySQL + MinIO + Redis多级存储

- MySQL：公告和任务元数据、状态、版本、索引、关系及指标汇总。
- MinIO：原始附件、解析产物、完整Evidence Pack、Compact Payload、ReportIR、Markdown、Word/PDF。
- Redis：消息队列、短期缓存、进度、锁和热点结果。
- 使用Transactional Outbox、内容哈希、幂等键和补偿任务处理跨存储一致性。

### 4.7 OpenTelemetry全链路可观测性

贯通FastAPI、Celery、LangGraph节点、模型调用、MySQL、Redis、MinIO和DeepEval，记录Trace、Metric和结构化日志。前端Trace页面不是简历重点，重点描述后端可观测数据采集、关联和查询能力。

---

## 5. RAG与辅助材料自动选择方案

### 5.1 最新产品边界

所有外部用户使用同一份共享资料体系：

1. 公开项目公告资料库；
2. 公司审核并指定的行业知识库。

明确不支持：

- 用户个人上传PDF、Word、Excel；
- 用户建立个人或企业私有知识库；
- 不同客户之间维护独立知识空间。

可以支持：

- 用户选择系统已有主公告；
- 用户手动输入分析目标、问题或修订意见；
- 用户手动选择系统已有辅助材料；
- 系统自动推荐辅助材料；
- 用户对候选材料增删确认。

用户输入文字属于当前任务指令或反馈，不自动沉淀为共享知识事实。

### 5.2 RAG作用

- 发现当前公告的补充、更正、结果和执行公告；
- 召回上一轮或同类集采项目；
- 召回相关政策、地区规则和业务解释；
- 召回公司审核过的历史分析和知识条目；
- 为用户反馈修订补充新证据；
- 将召回资料重新构建为可追溯Evidence。

推荐流程：

```text
主公告 + 用户分析目标
  -> 查询理解和检索条件生成
  -> 混合检索（关键词 + 向量 + 结构化过滤）
  -> Rerank
  -> 展示候选材料及召回原因
  -> 用户确认或系统自动选择
  -> 构建任务级 Evidence Pack
  -> 生成、质检和评测
```

### 5.3 自动召回字段

| 字段 | 含义 |
|---|---|
| `document_id` | 系统资料唯一标识 |
| `document_type` | 公告、政策、历史分析或规则卡片类型 |
| `retrieval_reason` | 为什么召回该资料 |
| `retrieval_score` | 综合相关性评分 |
| `relation_type` | 补充公告、历史轮次、同地区或同品类关系 |
| `source_scope` | `public_notice`或`company_curated` |
| `effective_date` | 生效日期，用于过滤过期规则 |
| `selected` | 是否进入最终Evidence Pack |

---

## 6. 公司指定的付费知识库设计

推荐候选类型：

1. 集采项目关系与时间线库；
2. 政策规则标准化库；
3. 地区准入与挂网规则库；
4. 历史中选、价格与采购量库；
5. 企业、产品、注册证与医保编码主数据；
6. 公司审核历史分析库；
7. 风险规则与公开案例库；
8. 行业术语、字段别名与规则本体库；
9. 专家问答与标准处置库；
10. 数据质量、OCR纠错和异常修复库。

首期推荐五类：

- 集采项目关系与时间线；
- 政策规则标准化；
- 历史中选、价格与采购量；
- 公司审核历史分析；
- 风险规则与案例。

付费不依赖客户私有数据，而依赖知识深度、分析能力、生成额度、批处理、数据导出、API、项目跟踪以及服务保障。

---

## 7. 用户反馈修订链路

当前仓库已经支持用户文字反馈后修订报告。

### 7.1 不需要新增检索

例如内容简化、增加表格、调整章节、修改表达。输入为：

```text
当前报告 + 用户反馈 + 原 Evidence Pack
```

### 7.2 需要当前公告补证

例如“报告内容太少，请继续检查附件”。系统回到当前公告的完整解析资产进行定向检索，补建带来源定位的新证据。

### 7.3 需要共享RAG

例如增加上一轮集采对比、跨地区规则或历史价格。流程为：

```text
用户反馈
  -> 意图分类
  -> RAG召回统一资料库
  -> 构建新增Evidence
  -> 合并或版本化Evidence Pack
  -> 修订相关章节
  -> 重新QA、事实回归检查和DeepEval
```

每次修订保存反馈文本、召回材料、前后版本差异、修订模型、质检结果和最终是否采纳。

---

## 8. 指标口径

### 8.1 报告质量指标

- Claim Support Rate：报告断言中有A/B证据支持的比例。
- Critical Fact Coverage：应覆盖关键事实中实际被覆盖的比例。
- Unsupported Claim Rate：无证据断言比例。
- Numeric/Date Exact Match：价格、数量和日期准确率。
- Section/Topic Completeness：必需章节和主题完整率。
- First-pass Quality Gate Pass Rate：首轮质检通过率。
- Revision Acceptance Rate：用户反馈后一次修订通过率。

### 8.2 解析、Evidence和检索指标

- Attachment Parse Success Rate：附件解析成功率。
- Source-to-Evidence Precision/Recall/F1：原始资料到证据包的事实准确性和覆盖率。
- Locator Accuracy：来源页码、表格和行列定位准确率。
- Mandatory Evidence Retention：压缩后强制证据保留率。
- Contextual Precision、Contextual Recall、Contextual Relevancy：RAG召回质量。

### 8.3 系统工程指标

- API提交P95；
- 队列等待P95；
- 端到端任务P50/P95/P99；
- 最终成功率、超时率和重试率；
- Token和单任务成本；
- 缓存命中率；
- 自动辅助材料召回采用率。

### 8.4 当前模拟完成态指标

以下仅用于简历演练，不是真实生产数据：

- Faithfulness：0.80 → 0.94；
- Critical Fact Coverage：0.73 → 0.91；
- Attachment Consistency：0.82 → 0.96；
- Answer Relevancy：0.85 → 0.93；
- 首轮质检通过率：66.5% → 88.5%；
- 无证据断言率：9.0% → 2.2%；
- 常规任务P95：180秒以内；
- 最终任务成功率：98.8%。

使用这些数字时必须同时说明测试集、数据分层、Judge模型和基线，否则不要描述为真实成果。

---

## 9. 推荐简历项目定位

### 项目名称

**基于LangGraph与Evidence Grounding的医疗采购公告分析Agent平台**

### 一句话定位

面向医疗器械带量采购和集中采购场景，独立设计并实现基于证据约束、自动资料召回、Agent编排和大模型质量评测的公告分析平台，将公告正文、附件、历史项目和公司行业知识转化为可追溯的结构化报告，并通过异步任务、分层存储和全链路可观测性保障生产运行。

### 商业化升级叙事

> 将原本仅供内部业务人员使用的公告报告生成工具，升级为面向企业客户的共享行业知识分析平台；基于公开公告库和公司审核知识库，通过RAG自动召回历史项目、政策规则、价格采购量数据和风险案例，为报告生成、跨项目对比和用户反馈修订提供证据支持。

---

## 10. 历史核心问题覆盖索引

以下内容来自用户最早期的项目学习要求，后续对话不得忽略。

| 历史问题 | 当前统一结论 | 详细文档 |
|---|---|---|
| 字段名必须解释 | 已写入本文件第0节，所有字段同时解释含义、来源、原因和使用节点 | 问题日志第9题 |
| 基础证据包不使用LLM、压缩是否丢失 | 不能承诺零损失；原始资产和完整包保留，Compact Payload有损但关键证据受保护 | 问题日志第10题、数据流学习笔记 |
| A/B证据是否冗余 | A负责原文追溯，B负责结构化计算；B必须记录`derived_from`和提取器版本 | 问题日志第11题 |
| 每一步数据格式 | 必须从MySQL记录、附件、解析结果、Evidence Pack、Compact Payload、ReportIR、QA、修复到DeepEval逐步说明 | 问题日志第12题、数据流学习笔记 |
| 项目有哪些指标 | 按解析、Evidence、RAG、报告、模型对比、修订和工程层分类 | 问题日志第7与第13题 |
| Agent State是什么 | 它是共享数据快照，不等于状态机；State+Node+Edge+Checkpoint形成有状态工作流 | 问题日志第14题 |
| Celery基础 | Celery是任务执行框架，Redis/RabbitMQ是Broker；Celery和LangGraph职责分离 | 问题日志第15题 |
| 日均任务和模型分工 | 每天正式分析10份以内；生成V4 Pro，质检/修订V4 Flash，Judge为GLM-5和V4 Flash | 本文件第2节 |
| 前端是否存在 | 仓库有原生管理页面，但不是简历重点；重点描述后端Trace和查询能力 | 本文件第3与4.7节 |

---

## 11. 相关详细文档

- `docs/career/medical-notice-analyzer-resume-packaging.md`：简历包装和面试手册。
- `docs/career/medical-notice-analyzer-dataflow-learning-notes.md`：数据流、字段、Agent State和Celery学习笔记。
- `docs/career/medical-notice-analyzer-question-response-log.md`：用户问题和应对记录，当前下一题编号为19。

新对话启动语：

> 请先通过GitHub读取 `medical-notice-analyzer/docs/career/PROJECT_PACKAGING_CONTEXT.md`，再根据其中的索引读取问题日志或学习笔记，并以这些文件作为本项目后续回答的统一口径。

---

## 12. 变更记录

### 2026-08-03

- 创建跨对话项目包装总上下文。
- 确认所有外部用户使用统一的公开公告库和公司指定知识库。
- 明确不允许用户上传个人文件或创建私有知识库。
- 将RAG定位为自动辅助材料选择、历史项目关联和用户反馈补证能力。
- 新增公司付费知识库十类候选方案，并推荐首期五类核心知识库。
- 明确前端Agent Trace不是简历重点，重点为后端OpenTelemetry可观测能力。
- 增加文档分工和同步规则。
- 增加早期问题覆盖索引，明确字段解释、信息损失、A/B证据、数据格式、指标、Agent State和Celery等长期回答要求。
- 同步问题日志第1～18题，下一题编号更新为19。
