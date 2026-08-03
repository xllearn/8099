# 医疗公告分析 Agent：问题与应对记录

> 用途：持续记录项目学习、架构设计、简历包装和面试准备过程中提出的问题，以及对应结论、应对方案和面试表述。
>
> 维护规则：
>
> 1. 历史问题编号保持不变；新增问题继续递增。
> 2. 答案发生变化时，在原问题下增加“更新记录”，不静默删除旧结论。
> 3. 每次新增问题后，同时检查是否需要更新项目包装总上下文。
> 4. 本文件记录“为什么这样设计”和“面对追问如何回答”；最终统一口径以 `medical-notice-analyzer/docs/career/PROJECT_PACKAGING_CONTEXT.md` 为准。
> 5. 字段名、配置名、指标名或组件名首次出现时，必须同时解释：中文含义、数据来源、存在原因以及项目中的使用方式。
>
> 下一条问题编号：**19**

## 相关文档

- `medical-notice-analyzer/docs/career/PROJECT_PACKAGING_CONTEXT.md`：跨对话唯一入口，保存当前最终包装口径。
- `docs/career/medical-notice-analyzer-dataflow-learning-notes.md`：逐步数据格式、字段和基础技术学习笔记。
- `docs/career/medical-notice-analyzer-resume-packaging.md`：简历项目描述和面试叙事。

## 已确认的项目事实

- 业务以带量采购、集中采购及其接续类公告为主。
- 日采集公告约 9000～10000 条，正式进入 AI 分析链路的任务通常每天不超过 10 份。
- 单份正式报告通常为 2000～5000 字，至少包含 4～5 个章节；是否生成表格取决于公告数据是否适合结构化展示。
- 报告生成模型的官方 API `model` 字段为 `deepseek-v4-pro`。
- 质检和用户反馈修订模型的官方 API `model` 字段为 `deepseek-v4-flash`。
- DeepEval Judge 使用公司部署的 GLM-5 和 DeepSeek V4 Flash。
- 当前仓库已有用户文字反馈后的报告修订能力。
- 当前前端为原生 HTML/CSS/JavaScript 管理页面，但不是求职包装重点。
- 简历主叙事采用“独立负责项目从 0 到 1 的架构设计与实现”。

---

## 目录

1. [Evidence Pack 是否需要像 RAG 一样提前离线处理](#1-evidence-pack-是否需要像-rag-一样提前离线处理)
2. [专用解析器与 Apache Tika、MarkItDown 如何选择](#2-专用解析器与-apache-tikamarkitdown-如何选择)
3. [重要信息未进入证据包时如何发现和兜底](#3-重要信息未进入证据包时如何发现和兜底)
4. [现有证据包构建方法是否合理](#4-现有证据包构建方法是否合理)
5. [LangChain、LangGraph 和 HTTP 调用的上下文限制](#5-langchainlanggraph-和-http-调用的上下文限制)
6. [完整数据流第 0～11 步分别存在哪里](#6-完整数据流第-011-步分别存在哪里)
7. [DeepEval 指标是否足够以及报告应如何全面评估](#7-deepeval-指标是否足够以及报告应如何全面评估)
8. [用户反馈修订和公司内部资料 RAG 如何设计](#8-用户反馈修订和公司内部资料-rag-如何设计)
9. [出现字段名时应如何解释](#9-出现字段名时应如何解释)
10. [基础证据包与 LLM 压缩是否会造成信息损失](#10-基础证据包与-llm-压缩是否会造成信息损失)
11. [A 类证据与 B 类证据是否冗余](#11-a-类证据与-b-类证据是否冗余)
12. [完整链路每一步的数据格式是什么](#12-完整链路每一步的数据格式是什么)
13. [哪些指标评估报告、模型、检索和系统](#13-哪些指标评估报告模型检索和系统)
14. [Agent State 是什么以及为什么需要它](#14-agent-state-是什么以及为什么需要它)
15. [Celery 的基础知识和项目中的定位](#15-celery-的基础知识和项目中的定位)
16. [通过共享 RAG 将内部工具升级为客户产品是否合理](#16-通过共享-rag-将内部工具升级为客户产品是否合理)
17. [公司统一付费知识库应该包含哪些类型](#17-公司统一付费知识库应该包含哪些类型)
18. [跨对话文档如何分工并保持同步](#18-跨对话文档如何分工并保持同步)

---

# 1. Evidence Pack 是否需要像 RAG 一样提前离线处理

## 问题

日采集约一万条公告，是否应该全部提前完成附件下载、OCR、表格解析和 Evidence Pack 构建，让用户点击后直接生成报告？用户选择材料后再处理，还算不算预处理？

## 结论

采用：

> **全量轻处理 + 热点预处理 + 用户选择后按需深处理 + 内容哈希缓存复用。**

需要区分：

- **全量轻处理**：所有公告都做元数据清洗、正文去噪、内容哈希、文件类型识别、去重和业务分类。
- **文档资产预处理**：对高价值或高频材料提前下载附件，解析 PDF、Word、Excel、OCR 和表格结构，形成可复用 `ParsedDocument`。
- **任务级 Evidence Pack 组装**：用户选择主材料、辅助材料和分析目标后，再把已经解析的文档资产组装为本次任务的证据包。

`ParsedDocument` 表示文档级解析结果，可以跨任务复用；Evidence Pack 包含任务角色和分析目标，通常必须在用户选择后生成。

## 面试表述

> 我没有对每天近万条公告执行同等成本的深度解析，而是将离线文档资产处理与在线任务证据组装拆开。全量数据只做低成本清洗和分类，高价值公告提前解析，其他材料在用户选择后按需处理，并通过 `source_hash` 和 `parser_version` 复用缓存。这样兼顾点击响应速度和资源成本。

---

# 2. 专用解析器与 Apache Tika、MarkItDown 如何选择

## 结论

不存在一个通用解析器在全部格式和业务目标上都最好。推荐：

> **统一解析接口 + 关键格式专用适配器 + Tika/MarkItDown 通用兜底。**

医疗采购报告需要企业、产品、价格、采购量、注册证、医保编码，以及 PDF 页码和 Excel 行列位置。通用工具适合覆盖格式和提取基础文本，但复杂表格与来源定位仍需专用解析器。

## 推荐顺序

```text
PDF   -> 专用文本/表格解析 -> OCR -> 通用解析器兜底
Excel -> openpyxl/xlrd 专用解析 -> 通用解析器兜底
Word  -> python-docx/LibreOffice -> 通用解析器兜底
未知格式 -> Tika 或 MarkItDown
```

## 关键字段

| 字段 | 含义 | 为什么需要 |
|---|---|---|
| `parser_name` | 实际使用的解析器 | 比较各解析器质量并定位故障 |
| `parser_version` | 解析规则版本 | 版本升级后判断缓存是否失效 |
| `fallback_used` | 是否使用兜底解析器 | 统计主解析器失败率 |
| `parse_status` | 解析成功、部分成功或失败 | 决定是否允许生成和是否转人工 |

---

# 3. 重要信息未进入证据包时如何发现和兜底

## 结论

如果信息没有进入完整 Evidence Pack，也没有进入模型输入，生成模型不应该可靠地覆盖它。模型偶然写对也不能算正确，因为缺少当前公告证据。

质量评估必须覆盖完整链路：

```text
Raw Source
  -> Parsed Asset
  -> Full Evidence Pack
  -> Compact Payload
  -> Report
```

## 上游指标

- 附件解析成功率；
- PDF 页覆盖率；
- 表格扫描覆盖率；
- Source-to-Evidence Precision、Recall 和 F1；
- 来源定位准确率；
- Mandatory Evidence 保留率；
- 压缩前后关键事实覆盖差异。

## 兜底流程

```text
发现必需主题缺失
  -> 回到完整解析产物定向检索
  -> 必要时使用受约束 Fact Extractor 补建候选事实
  -> 生成带 source_ref 的新 A/B 证据
  -> 更新 Evidence Pack 版本并重新质检
  -> 仍无法找到则 needs_manual_review
```

`source_ref` 表示来源定位，包括文件、页码、表格、行列和原文摘录；`needs_manual_review` 表示必须人工复核，不允许系统猜测后直接交付。

---

# 4. 现有证据包构建方法是否合理

## 结论

当前方案作为受控领域的工程原型是合理的，明显优于把所有正文和附件简单拼接后直接交给模型，但尚未达到完整生产级证据系统。

## 合理之处

- 区分主材料和辅助材料；
- 针对 PDF、Word、Excel 等格式专门解析；
- 完整证据包与模型紧凑输入分离；
- 使用 A/B/C 证据分层；
- 对关键证据实施压缩保护；
- 记录解析失败和压缩损失；
- 报告后执行 Claim-Evidence 检查和质量门。

## 主要不足

- 规则抽取存在 Recall 上限；
- 复杂 PDF、OCR 和大型表格存在解析边界；
- 缺少系统的 Source-to-Evidence 标注评测集；
- 当前 DeepEval 主要评估生成结果，无法单独证明证据包完整；
- 原始附件和全部解析资产的长期不可变存储仍属于目标架构。

---

# 5. LangChain、LangGraph 和 HTTP 调用的上下文限制

## 结论

LangGraph 负责 State、Node、Edge 和执行路由；LangChain提供统一模型调用接口；HTTP负责传输。它们都不会自动替应用安全控制上下文窗口。

`max_tokens` 通常表示最大输出 Token，不是最大输入长度。项目需要自行维护：

| 字段 | 含义 |
|---|---|
| `model_context_tokens` | 模型支持的总上下文窗口 |
| `estimated_input_tokens` | 本次输入 Token 估算值 |
| `reserved_output_tokens` | 为报告输出预留的 Token |
| `safety_margin_tokens` | 给工具消息和估算误差保留的余量 |
| `application_input_cap` | 应用主动设置的最大输入预算 |

应用可用输入预算应同时受模型窗口、模型输出预留、Dify变量限制和网关限制约束。即使模型支持超长上下文，也不能把所有资料无差别塞入，因为会增加延迟、成本和无关噪声。

---

# 6. 完整数据流第 0～11 步分别存在哪里

## 目标存储职责

- **MySQL**：公告元数据、任务状态、版本、索引、关系和评测汇总。
- **MinIO**：原始附件、解析产物、完整证据包、压缩快照、ReportIR、Markdown、Word/PDF。
- **Redis**：Celery Broker、短期缓存、进度和分布式锁，不作为永久业务事实源。
- **LangGraph State**：当前任务的小型状态和对象引用。
- **Celery消息**：只传 `run_id`、`pack_id` 等标识。

## 第 0～11 步

| 步骤 | 数据 | 推荐存储 |
|---:|---|---|
| 0 | 用户选择、文字目标和参数 | MySQL任务表及材料关系表 |
| 1 | 公告正文和附件元数据 | MySQL；原始附件进入MinIO |
| 2 | 清洗正文和文档资产 | MinIO解析JSON；MySQL记录版本与状态 |
| 3 | PDF/Word/Excel解析结果 | MinIO文本、表格、OCR；Redis热点缓存 |
| 4 | 完整Evidence Pack | MinIO不可变JSON；MySQL存ID、哈希和路径 |
| 5 | Compact Payload | Redis短期缓存；MinIO审计快照；MySQL存策略 |
| 6 | 任务和Agent State | MySQL状态、LangGraph Checkpoint、Celery消息 |
| 7 | 初次ReportIR/Markdown | MinIO版本化草稿；MySQL报告版本元数据 |
| 8 | QA和质量门 | MySQL可统计字段；MinIO保存大明细 |
| 9 | 修复或用户修订结果 | MinIO新版本；MySQL保存修复动作和版本关系 |
| 10 | 最终报告和运行结果 | MySQL最终状态；MinIO正式报告文件 |
| 11 | DeepEval评测 | 评测队列在Redis；明细在MinIO；汇总在MySQL |

---

# 7. DeepEval 指标是否足够以及报告应如何全面评估

## 结论

当前四项指标是最小 Advisory 版本，不足以完整评价 2000～5000 字的多章节医疗采购报告。Precision和Recall必须说明评估对象。

## 六层指标体系

1. **解析层**：附件成功率、页覆盖率、表格扫描覆盖率、OCR低置信度率。
2. **Evidence层**：关键事实提取Precision、Recall、F1、来源定位准确率、强制证据保留率。
3. **RAG层**：Contextual Precision、Contextual Recall、Contextual Relevancy、Top-K Hit Rate、MRR。
4. **报告层**：Faithfulness、Claim Support Rate、Critical Fact Coverage、数值日期Exact Match、结构完整性、连贯性和流畅性。
5. **修订层**：反馈遵循度、事实回归率、修改局部性、一次修订通过率。
6. **工程和业务层**：P50/P95、成功率、重试率、Token成本、人工复核率和人工耗时节省。

## 当前四项DeepEval指标

| 指标 | 含义 |
|---|---|
| `claim_faithfulness_v1` | 报告断言是否被证据支持 |
| `critical_coverage_v1` | 应覆盖关键事实是否被报告覆盖 |
| `attachment_state_consistency_v1` | 报告描述的附件状态是否真实 |
| `answer_relevancy_v1` | 报告是否围绕本次任务目标 |

---

# 8. 用户反馈修订和公司内部资料 RAG 如何设计

## 当前实现

项目已经支持用户提交文字反馈，使用当前报告、原Evidence Pack和可选长期记忆生成新版本，并重新质检。

## 反馈分类

- **样式或结构修改**：只使用当前报告、反馈和原Evidence Pack。
- **当前公告补证**：回到当前公告完整解析资产，定向检索并补建证据。
- **共享知识扩展**：检索公开公告资料库和公司审核知识库，用于历史对比、规则解释和风险分析。

## 推荐流程

```text
用户反馈
  -> 意图分类
  -> 必要时查询改写
  -> 混合检索和结构化过滤
  -> Rerank
  -> 构建 revision_evidence_pack
  -> 修订相关章节
  -> QA、事实回归检查与DeepEval
```

`revision_evidence_pack` 表示本次修订新增的证据集合；它必须区分当前公告证据、历史项目证据和公司分析知识，不能把历史价格或规则覆盖成当前项目事实。

---

# 9. 出现字段名时应如何解释

## 用户要求

后续不能只给出 `run_id`、`source_ref`、`acks_late` 等英文名称，必须同步说明每个字段的意义。

## 固定解释模板

| 项目 | 说明 |
|---|---|
| 字段或组件名 | 程序中实际名称 |
| 中文含义 | 用业务语言解释它是什么 |
| 数据来源 | 数据库、解析器、规则、模型还是运行时生成 |
| 存在原因 | 它解决查询、追溯、状态、恢复还是质量问题 |
| 使用位置 | 哪个节点读取和写入它 |
| 示例 | 一个脱敏样例 |

## 示例

`run_id`：一次分析运行的唯一标识。由任务创建接口生成，用于关联MySQL状态、LangGraph State、Celery任务、日志、Trace、报告版本和DeepEval结果。如果没有它，各组件无法判断数据属于哪一次运行。

`pack_id`：一次完整Evidence Pack的唯一标识。生成、质检和修订通过它定位同一份事实底座。

`source_ref`：证据来源定位。通常包含公告ID、附件名、页码、表格和原文摘录，用来证明报告事实来自哪里。

---

# 10. 基础证据包与 LLM 压缩是否会造成信息损失

## 问题

基础Evidence Pack主要由确定性解析器构建，过长时再使用LLM语义压缩。这样能否完整提取全部信息，压缩是否会丢失信息，面试时如何讲得可信？

## 结论

不能承诺对任意PDF、Word、Excel实现100%无损提取，也不能把LLM压缩描述成无损压缩。合理目标是：

> **原始资料可追溯、完整解析资产尽量保留、关键事实强制保真、压缩损失可量化、异常可转人工。**

## 三类损失

1. **解析损失**：扫描PDF、跨页表格、合并单元格、图片和损坏文件可能解析不完整。
2. **事实抽取损失**：规则或模型未识别某种业务表达，导致信息未进入Evidence Pack。
3. **压缩损失**：为了满足上下文预算，重复文本、辅助材料细节和大型表格明细可能不进入Compact Payload。

## 保护机制

- 原始文件和完整解析产物长期保存；
- Full Evidence Pack与Compact Payload分离；
- 价格、数量、日期、企业、产品和资格规则标为Mandatory Evidence；
- 记录压缩前后字符或Token、被删除证据数量、类型和哈希；
- 对关键附件失败、强制证据超限和冲突事实转人工复核；
- 用Source-to-Evidence Recall和Mandatory Evidence Retention验证上游质量。

## 面试表述

> 我不会宣称所有复杂文档都能无损解析。系统保留原始文件和完整解析资产，发送给模型的是可重建的紧凑视图。压缩时按照材料角色、证据等级和业务字段排序，价格、时间、数量等强制证据及其来源必须保留，并记录压缩损失。如果关键附件失败或强制证据无法在预算内保留，任务进入分阶段处理或人工复核。目标不是零损失，而是关键事实保真、损失可量化、结果可追溯。

---

# 11. A 类证据与 B 类证据是否冗余

## 结论

两者有意保留少量冗余，但职责不同，不能简单删除其中一个。

- **A类证据**回答“原文到底写了什么、在哪里写的”。例如PDF第3页的一段原文或Excel某个单元格。
- **B类证据**回答“程序如何标准化理解这段原文”。例如把“8月10日下午5点前”转为标准时间 `2026-08-10T17:00:00+08:00`。

只有A类时，每个后续节点都需要重复解析日期、金额和业务字段，且不同节点可能产生不同理解。只有B类时，无法回到原文核验，也无法判断提取器是否犯错。

## 一致性规则

| 字段 | 含义 |
|---|---|
| `derived_from` | 当前B类事实依赖的A类证据ID |
| `extractor_version` | 产生B类事实的提取器版本 |
| `normalized_value` | 标准化后的时间、金额、数量或枚举值 |
| `source_hash` | 原始来源内容哈希，来源变化时使旧B类失效 |

面试中可以称B类为A类之上的“结构化物化视图”：它提高查询和规则计算效率，但不替代原始证据。

---

# 12. 完整链路每一步的数据格式是什么

## 主链路格式变化

```text
MySQL公告记录 + 附件元数据
  -> Material对象
  -> ParsedAttachment / ParsedDocument
  -> Full Evidence Pack
  -> Compact Payload
  -> Agent State中的对象引用和FactItems
  -> ReportIR + Markdown
  -> QA Result / Claim-Evidence Index
  -> Repaired Report Version
  -> Final Analysis Run + DOCX
  -> DeepEval Projection + Metric Result
```

## 主要结构

| 阶段 | 代表数据 | 核心字段及含义 |
|---|---|---|
| 数据库 | 公告正文、附件元数据 | `articleid`公告ID；`content` HTML正文；`filename`附件名；`filepath`下载位置 |
| 材料对象 | 清洗后的单篇公告 | `material_role`主/辅角色；`content_text`纯文本；`attachments`附件列表 |
| 附件解析 | 文本、表格和告警 | `parse_status`解析状态；`table_summaries`表格结构；`warnings`风险提示 |
| 完整证据包 | A/B/C证据集合 | `pack_id`证据包ID；`evidence_items`证据列表；`generation_guidance`生成约束 |
| 压缩输入 | 面向模型的派生视图 | `input_strategy`压缩策略；`compact_pack_chars`压缩后字符数；`omitted_content`省略内容 |
| 事实抽取 | 结构化事实 | `fact_items`事实列表；每项包含标准值和证据引用 |
| 生成结果 | ReportIR与Markdown | `sections`章节；`tables`报告表格；`enterprise_tips`企业建议 |
| 质检结果 | 问题与门禁 | `unsupported_claims`无证据断言；`missing_rules`遗漏规则；`passed`是否通过 |
| 修复结果 | 新报告版本 | `repair_count`修复次数；`repair_actions`修复动作；`remaining_issues`遗留问题 |
| 最终运行 | 任务状态和交付物 | `run_id`任务ID；`deliverable`是否可交付；`needs_manual_review`是否人工复核 |
| DeepEval | 脱敏评测单元和分数 | `metric_id`指标ID；`score`得分；`status`已评分、不适用或失败 |

每一步的完整示例继续维护在 `medical-notice-analyzer-dataflow-learning-notes.md`。

---

# 13. 哪些指标评估报告、模型、检索和系统

## 指标必须按对象分类

### 单份报告质量

- Claim Support Rate；
- Critical Fact Coverage；
- Unsupported Claim Rate；
- Numerical/Date Exact Match；
- Structure Completeness；
- Coherence、Fluency；
- 是否可交付以及是否需要人工复核。

### LLM应用版本质量

在固定测试集、Evidence Pack、Prompt、工作流和Judge条件下，统计多个任务的平均Faithfulness、Coverage、修订通过率等。它评估的是“模型 + Prompt + 检索 + 工作流”，不应直接等同于基础模型能力。

### 模型对比

只有固定测试集、Prompt、Evidence Pack、工作流和Judge，仅替换生成模型时，差异才可用于比较DeepSeek V4 Pro与其他模型。

### 检索质量

Contextual Precision、Contextual Recall、Contextual Relevancy、Top-K Hit Rate、MRR和过期资料召回率。

### 系统工程质量

API P95、排队时间、端到端P50/P95/P99、成功率、重试率、Token成本和缓存命中率。

---

# 14. Agent State 是什么以及为什么需要它

## 结论

Agent State不是状态机本身，而是当前任务在多个节点之间共享的数据快照。

LangGraph工作流由：

- **State**：当前任务数据；
- **Node**：执行具体业务的函数；
- **Edge**：决定下一节点的路由；
- **Checkpoint**：State的可恢复历史快照；

共同组成有状态工作流。

## 为什么需要

没有统一State时，解析、生成、质检和修复节点可能使用不同字段名，重复读取数据库，无法知道当前修复次数，也难以在中断后恢复和展示任务进度。

## 推荐分组

| 分组 | 代表字段 | 作用 |
|---|---|---|
| 身份 | `run_id`、`pack_id`、`idempotency_key` | 关联任务并防止重复创建 |
| 流程 | `status`、`current_node`、`retry_count` | 控制当前阶段与重试 |
| 数据引用 | `evidence_pack_key`、`report_object_key` | 指向MinIO对象，避免把大文件塞入State |
| 业务结果 | `fact_items`、`report_ir`、`qa_issues` | 节点间传递中间结果 |
| 质量 | `deepeval_scores`、`risk_level` | 决定修复、交付或人工复核 |
| 可观测性 | `trace_id`、`timings`、`token_usage` | 诊断性能和模型成本 |
| 恢复 | `last_completed_node`、`checkpoint_version` | 从安全节点继续执行 |

原项目已经通过Evidence Pack JSON、Analysis Run JSON、Checkpoint JSON和Dify变量形成隐式状态机；目标优化是使用LangGraph将其收敛为显式的类型化State。

---

# 15. Celery 的基础知识和项目中的定位

## Celery是什么

Celery是Python分布式任务队列框架，不是消息队列中间件本身。

```text
FastAPI Producer
  -> Redis/RabbitMQ Broker
  -> Celery Worker
  -> MySQL/MinIO持久化结果
```

- **Producer**：发送任务的一方，本项目是FastAPI。
- **Broker**：传递任务消息，本项目推荐Redis。
- **Queue**：任务分类通道，例如附件、生成、评测队列。
- **Worker**：后台执行任务的进程。
- **Result Backend**：保存Celery任务状态的可选组件，不能代替MySQL业务状态。
- **Celery Beat**：定时任务调度器。

## 为什么项目需要

附件解析、LLM生成、质检和导出都属于长耗时任务，不应阻塞HTTP请求。FastAPI创建任务后立即返回`run_id`，Worker在后台运行LangGraph，用户通过`run_id`查询进度。

## 常见配置

| 配置 | 含义 |
|---|---|
| `acks_late` | 任务执行结束后再确认消息，Worker崩溃时任务可重新投递 |
| `autoretry_for` | 指定哪些异常自动重试 |
| `retry_backoff` | 重试等待时间逐步增加 |
| `task_time_limit` | 任务最大执行时间 |
| `worker_prefetch_multiplier` | Worker提前领取任务数量 |

开启`acks_late`后可能重复执行，因此任务必须幂等：同一个`run_id`重复执行不能生成多份正式报告或破坏状态。

## 与LangGraph分工

> Celery负责任务何时执行、在哪个Worker执行和失败如何重试；LangGraph负责单次分析任务内部按哪些节点和条件执行。

日均不足10份正式报告时，最容易学习和解释的方案是一个Celery主任务运行一次LangGraph；附件较多时再使用并行子任务；DeepEval放入低优先级队列。

---

# 16. 通过共享 RAG 将内部工具升级为客户产品是否合理

## 最新产品边界

- 所有用户使用同一份公开公告资料库和公司审核知识库；
- 用户不能上传个人PDF、Word和Excel；
- 不建立客户私有知识库；
- 用户可以输入分析目标、手动选择系统材料或使用系统自动推荐；
- 用户文字默认只用于当前任务，不直接沉淀为共享事实。

## 判断

方向合理。它把产品从“内部公告报告生成工具”升级为“医疗采购行业知识分析平台”，增加历史项目对比、政策关联、风险分析和用户反馈补证等场景，同时避免客户私有文件带来的隔离与隐私复杂度。

## 推荐模式

系统自动召回候选资料，展示`retrieval_reason`和`retrieval_score`，允许用户增删确认。相比完全自动或全部手选，混合模式更可信、更容易解释。

## 风险

- 公司知识必须经过审核、版本化和失效管理；
- 不能把历史项目事实误写为当前项目事实；
- 过期资料需要时间过滤；
- 自动召回效果必须有Contextual Precision与Recall评测；
- 对外使用量增长后，需要Celery、缓存、限流、成本和可观测性体系支撑。

---

# 17. 公司统一付费知识库应该包含哪些类型

## 推荐十类

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

## 首期推荐五类

- 集采项目关系与时间线；
- 政策规则标准化；
- 历史中选、价格与采购量；
- 公司审核历史分析；
- 风险规则与案例。

付费逻辑不依赖客户私有数据，而依赖知识深度、分析能力、生成额度、批处理、导出、API、项目跟踪和服务保障。

---

# 18. 跨对话文档如何分工并保持同步

## 问题

如何让新对话快速了解项目包装，同时保证问题日志、学习笔记和简历文档不会相互矛盾？

## 分工

- `PROJECT_PACKAGING_CONTEXT.md`：保存当前最终结论，是新对话第一读取入口。
- 本问题日志：保存问题、设计取舍、面试回答和历史变化。
- `medical-notice-analyzer-dataflow-learning-notes.md`：保存详细字段和逐步数据格式。
- `medical-notice-analyzer-resume-packaging.md`：保存可直接使用的简历描述。

## 同步规则

每次产生新的包装决定时：

1. 在问题日志追加问题及推理；
2. 将最终结论同步进总上下文；
3. 涉及字段和数据流时更新学习笔记；
4. 影响项目定位、技术栈或指标时更新简历文档；
5. 总上下文与详细文档冲突时，总上下文优先，但必须尽快修复详细文档。

## 新对话启动语

> 请先通过 GitHub 读取 `medical-notice-analyzer/docs/career/PROJECT_PACKAGING_CONTEXT.md`，再按其中的相关文档索引读取问题日志或学习笔记，并以这些文件作为本项目后续回答的统一口径。

---

## 更新记录

### 2026-08-03

- 将原第1～8题整理为统一格式。
- 历史补录字段解释、信息损失、A/B证据关系、完整数据格式、指标分类、Agent State和Celery等早期问题。
- 追加共享RAG客户化、公司付费知识库和跨对话文档同步问题。
- 将下一条问题编号更新为19。
