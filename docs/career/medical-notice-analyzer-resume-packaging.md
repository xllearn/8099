# 智能医疗公告分析项目：简历包装与面试手册

> 面向岗位：Agent开发、AI应用工程师、AI后端工程师。
>
> 项目口径：以“独立负责从0到1设计与实现”为主线。
>
> 重要边界：本文区分“当前仓库可验证实现”和“假设已完成的目标架构”。目标架构、模拟指标和简历文案用于学习与面试演练；没有真实测试报告时，不应把模拟数字当成真实生产数据。

---

## 1. 项目名称与定位

### 推荐名称

**基于LangGraph与Evidence Grounding的医疗采购公告分析Agent平台**

### 技术栈

`Python` `FastAPI` `LangGraph` `GraphRAG` `DeepEval` `Celery` `Redis` `MySQL` `MinIO` `OpenTelemetry` `Docker`

### 项目描述

面向医疗保障、卫生健康及大型医疗机构公告分析场景，建设基于Evidence Grounding的AI Agent平台。数据覆盖32个省级单位、333个市级单位、约2800个县级单位及国家级部门和大型医疗机构，日采集公告约9000～10000条。原内部验证阶段正式分析通常每天不超过10份；产品客户化完成态按日均300～500份、活动期峰值约1000份/日的目标容量设计异步处理链路。系统完成主辅材料选择、GraphRAG辅助召回、多格式附件解析、证据构建、事实与分析分层、报告生成、质量门禁、定向修复、用户反馈补证和受控文档导出。

> 日均300～500份、峰值约1000份/日以及后文质量和性能数字均属于目标容量或模拟评测口径。没有真实日志时，应使用“按该容量设计”“在模拟场景下验证”，不能使用“线上稳定处理”。

---

## 2. 原项目与包装完成态

### 当前仓库可验证主链路

```text
数据库选取公告
  -> 读取公告正文和元数据
  -> 下载、解析附件
  -> 构建Evidence Pack
  -> 生成Dify紧凑输入
  -> Dify生成ReportIR/Markdown
  -> 本地质量门与受控修复
  -> 保存运行、Checkpoint和版本
  -> 用户文字反馈修订
  -> Word导出
  -> DeepEval旁路评测
```

当前仓库具备数据库选材、正文清洗、多格式附件解析、Full Pack与Compact Payload分离、ReportIR、质量门、修复、Checkpoint、用户反馈和DeepEval Advisory原型。当前没有完整统一LangGraph State；GraphRAG、Celery弹性队列、完整多级存储和下述安全机制属于包装完成态。

### 包装完成态主链路

```text
FastAPI鉴权、额度、限流和可靠接单
  -> 公司GraphRAG确定辅助材料
  -> LangGraph解析、证据、事实、分析、生成、质检和修复
  -> MySQL/MinIO/Redis分层存储与Checkpoint恢复
  -> 可交付判断和受控导出
  -> 用户反馈补证修订
  -> 偏好记忆候选和用户确认
  -> DeepEval异步旁路
```

---

## 3. 主辅材料与公司GraphRAG

- 主材料由用户显式选择1～3条项目公告，决定本次报告的事实范围。
- 辅助材料为0～10条，可由用户手动选择，也可由系统自动召回。
- 辅助资料类型包括补充/更正公告、历史轮次、政策规则、地区准入规则、公司审核历史分析、价格采购量数据和风险案例，不只限于“项目公告类型”。
- 所有客户共用公开公告库和公司审核知识库，不允许上传个人文件或建立客户私有知识库。

公司GraphRAG完成态组合：

1. 关键词检索；
2. 向量语义检索；
3. 知识图谱关系扩展；
4. 地区、品类、阶段、时间、`source_scope`和有效期过滤；
5. Rerank、去重和证据充分性判断。

GraphRAG返回的是候选资料，不是可直接写入报告的事实。候选资料仍需解析、来源定位并构建A/B证据。

面试口径：

> 我把GraphRAG定位为共享知识材料选择器，而不是事实生成器。它并行召回关键词、语义和图关系候选，经过权限、时间和业务过滤后选出0～10条辅助材料；后续仍走统一解析和Evidence构建，避免把检索摘要直接当成当前项目事实。

---

## 4. Evidence Pack、事实层与分析层

### 4.1 两阶段结构

```text
原始公告/附件/GraphRAG资料
  -> Full Evidence Pack：完整、可追溯、持久化
  -> Compact Payload：面向单次模型调用的受控视图
```

### 4.2 A/B/C定义

- **A级直接证据**：公告字段、正文原文、附件文本和表格单元格，包含`source_ref`。
- **B级结构化事实**：对A级做标准化，记录`normalized_value`、`derived_from`、`extractor_version`和`source_hash`。
- **C级辅助信息**：摘要、生成指导、告警、诊断和受控偏好，不能单独支撑事实。

**C级不是推理结论层。**例如“企业应在5月20日前完成准备”属于Analysis Layer，需要引用`supporting_fact_ids`，并记录`confidence`、`risk_level`和`uncertainty`。

面试口径：

> A保存原文和位置，B保存程序对原文的结构化理解，C只提供组织和诊断辅助。推理结论单独进入Analysis Layer。事实必须回溯到证据，分析必须回溯到事实，置信度不能替代证据。

### 4.3 解析器口径

包装完成态采用统一解析接口和专用适配器：PDF文本提取后对扫描页OCR；DOCX使用`python-docx`；XLSX/XLSM使用`openpyxl`只读解析、结构化摘要和有限样例，不执行宏；DOC/XLS使用受控转换或通用解析器兜底。文件执行魔数、大小、页数、行数、压缩比和恶意压缩包检查。

---

## 5. 上下文压缩与Prompt

`application_input_cap_chars=240000`可以作为应用硬上限，但不能把模型输入压到“尽量接近24万字符”。还要为系统Prompt、当前任务、偏好、工具消息、输出Token和安全余量留空间，并用Token估算做最终校验。

压缩顺序：去重与噪声清理 → Mandatory Evidence保护 → 规则压缩 → 必要时LLM分块压缩 → Token复核 → 分阶段生成或人工复核。

Prompt组成：

1. 系统规则、证据边界、安全规则和输出Schema；
2. 当前用户任务和报告结构；
3. Compact Payload及来源引用；
4. 用户已确认的结构化样式偏好。

公告、附件、RAG内容和反馈均视为不可信数据，用分隔符隔离，不能覆盖系统指令。

---

## 6. LangGraph、质量门和修复

### 6.1 固定工作流型Agent

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
      | pass -> persist_result
      | fixable and auto_repair_count < 2 -> repair_report -> quality_review
      | blocking/high_risk -> human_review
 -> enqueue_deepeval
```

医疗公告属于高可信场景，因此主流程不能由模型自由跳转或跳过质量门。

### 6.2 同步质量门

检查ReportIR Schema、必需章节、发布机构、时间、标的物、附件状态、数值日期Exact Match、Claim-Evidence支持、来源定位、C级误用、历史事实泄漏和禁用表达。

### 6.3 定向修复

先做确定性修复，再用DeepSeek V4 Flash对指定段落局部修改。输入包含问题码、目标段落、允许使用的A/B证据和禁止新增事实约束。自动修复最多2次，仍失败或高风险则人工复核。

DeepEval保持异步旁路。前端立即展示A/B支持率、无证据断言数、缺失主题和可交付状态；Judge完成后再更新Faithfulness等分数。

---

## 7. 用户反馈修订和偏好记忆

### 7.1 反馈路由

- 样式/结构：当前报告 + 用户反馈 + 原Evidence Pack。
- 当前公告补证：回到完整ParsedDocument定向检索并补建证据。
- 跨地区/历史/政策：GraphRAG召回，构建`revision_evidence_pack`后局部修订。

预制按钮也不能只把“意见+报告”交给模型。用户自助修订最多3轮；网络重试、Celery重投和同一轮模型重试不占轮次。超过3轮或仍有阻断问题则转人工。

### 7.2 偏好记忆

用户反馈日志和长期记忆分开。只提取稳定样式偏好，例如表格优先、结论简洁、增加数值对比；公告事实、一次性任务和敏感信息不得沉淀。生成`memory_candidate`后由用户明确确认，才能转为`approved/active`。用户可查看、修改、撤销和删除。偏好不进入共享GraphRAG事实库，也不能跨客户复用。

---

## 8. Celery、存储和工程保护

### 8.1 Celery与队列

FastAPI在MySQL事务创建`run_id`与Outbox后立即返回。Celery将GraphRAG、附件、生成、质检、修订、导出和DeepEval拆分到独立队列。Redis负责Broker、缓存和锁；MySQL是业务最终状态源；MinIO保存原始附件、解析产物、Evidence、ReportIR和导出文件。

关键机制：`acks_late`、`worker_prefetch_multiplier=1`、软硬超时、可恢复异常自动重试、指数退避和抖动、任务路由、Worker并发配置、Checkpoint和幂等键。

### 8.2 贯穿所有节点的保护

- **幂等**：任务创建使用`customer_id + idempotency_key`；节点使用`run_id + node_name + input_hash`。
- **重试**：只重试网络、限流和短时存储故障；权限、格式、证据冲突不盲目重试。
- **超时**：API、下载、OCR、解析、GraphRAG、模型、导出、节点和整任务分层超时。
- **Watchdog**：根据心跳、当前节点和截止时间恢复卡死任务或转人工。
- **并发**：队列隔离、模型信号量、附件并行、相同附件分布式锁、报告版本乐观锁。
- **安全**：鉴权、资料范围过滤、宏不执行、ZIP防路径穿越、Prompt注入隔离、日志脱敏、短时下载授权。
- **一致性**：Transactional Outbox、MinIO临时对象、内容哈希、幂等消费者和补偿任务。
- **降级**：GraphRAG→关键词/手选；专用解析→OCR/通用解析；LLM压缩→规则压缩；DeepEval失败不阻塞。

---

## 9. DeepEval与模拟指标

### 9.1 评测指标

- Faithfulness
- Critical Fact Coverage
- Attachment State Consistency
- Answer Relevancy
- Contextual Precision/Recall/Relevancy
- 数值日期Exact Match
- 结构完整性、反馈遵循度和事实回归率

### 9.2 模拟完成态

以下不是当前仓库实测结果：

| 指标 | 基线 | 完成态 |
|---|---:|---:|
| Faithfulness | 0.80 | 0.94 |
| Critical Fact Coverage | 0.73 | 0.91 |
| Attachment Consistency | 0.82 | 0.96 |
| Answer Relevancy | 0.85 | 0.93 |
| 首轮质量门通过率 | 66.5% | 88.5% |
| 无证据断言率 | 9.0% | 2.2% |

容量模拟：日均300～500份、峰值约1000份/日、50份任务/小时、稳定并发20、队列等待P95小于60秒、常规任务P95小于180秒、最终成功率98.8%。

---

## 10. 简历正式版本

> 以下为假设完成态的简历演练版本。使用具体质量和容量数字前，应准备脱敏测试集、压测脚本和原始日志。

### 基于LangGraph与Evidence Grounding的医疗采购公告分析Agent平台

`Python` `FastAPI` `LangGraph` `GraphRAG` `DeepEval` `Celery` `Redis` `MySQL` `MinIO` `OpenTelemetry`

- **项目描述：**独立负责医疗公告智能分析平台从0到1的架构设计与实现，覆盖32个省级、333个市级及约2800个县级单位，日采集约9000～10000条公告；将内部低频报告工具升级为面向企业客户的共享行业知识分析平台，按日均300～500份、活动期峰值约1000份/日的目标容量设计分析链路。

- 集成公司GraphRAG，对关键词、向量与知识图谱候选执行地区、品类、阶段、时间和知识范围过滤及Rerank，自动选择0～10条历史项目、政策规则和公司审核知识；将召回材料重新解析为可追溯Evidence，避免检索摘要直接污染当前项目事实。

- 建立Evidence Grounding与事实/分析分层，将原文和表格单元格抽象为A级证据、标准化事实抽象为B级证据、摘要与诊断抽象为C级辅助信息；分析结论引用结构化事实并记录置信度、风险与未知条件，在模拟回归中将无证据断言率由9.0%降至2.2%。

- 基于LangGraph构建有状态受控工作流，拆分选材、解析、证据、压缩、事实抽取、分析、生成、质检和局部修复节点，通过类型化State、条件边、Checkpoint和幂等键实现失败续跑；自动修复最多2次，高风险任务进入人工审核。

- 引入DeepEval建立离线回归与线上异步旁路评测，在模拟测试集上评估Faithfulness、关键事实覆盖、附件状态一致性和回答相关性；同步质量门立即控制可交付状态，Judge不可用不阻塞正式报告。

- 基于Celery + Redis拆分GraphRAG、附件、生成、质检、用户修订、导出和评测队列，结合客户额度、令牌桶限流、模型并发信号量、延迟确认、指数退避、Watchdog和Worker弹性扩缩容，在模拟容量场景下实现提交接口P95小于200ms、队列等待P95小于60秒和20个正式任务稳定并发。

- 设计MySQL、MinIO、Redis多级存储，使用Transactional Outbox、临时对象、内容哈希、分布式锁和补偿任务处理跨存储一致性并复用相同附件解析资产；通过OpenTelemetry贯通API、队列、GraphRAG、LangGraph、模型和存储，采集排队、节点耗时、Token、重试和证据引用Trace。

- 建立用户反馈补证修订和偏好记忆机制，将反馈分为样式修改、当前公告补证和共享知识扩展，最多支持3轮用户自助修订；仅将经用户确认的稳定样式偏好加入账户Profile，避免一次性要求、项目事实和跨客户数据进入长期记忆。

---

## 11. 面试时必须能讲清的重点

### C级证据为什么不是推理结论

C级内容不能作为高风险事实依据。推理结论要进入Analysis Layer，显式引用Fact ID，记录风险和未知条件，否则会把“证据”和“判断”混成一层，难以质检和修订。

### 为什么24万字符不能全部填满

24万是应用硬上限，还要预留系统规则、任务、偏好、输出Token和误差空间。最终以Token预算为准，超限时分阶段生成，关键证据无法保留时转人工。

### 为什么GraphRAG召回后还要构建Evidence

检索命中只表示相关，不表示内容已经适合支撑当前结论。只有经过解析、来源定位、A/B结构化和主辅事实边界校验后，材料才能进入报告生成和Claim-Evidence检查。

### 为什么后端保护不是最后一步

超时、重试和Watchdog如果只放在二次质检后，解析、检索和模型节点卡死时根本走不到最终校验。正确方式是在每个节点设置超时、幂等、Checkpoint和错误分类，最终节点只做持久化与状态收口。

### 自动修复2次和用户反馈3轮有什么区别

自动修复由质量门触发，处理模型生成缺陷；用户反馈由用户发起，处理新增需求。两者使用不同计数器。网络重试和Worker重投不占用户修订轮次。

### 用户偏好记忆是否违反“不建立私有知识库”

不违反。偏好Profile只保存格式和风格配置，不保存行业事实或客户文件；它不进入共享GraphRAG，不跨客户，并且需要用户确认且可撤销删除。

---

## 12. 后续需要补充的真实证据

- 公司GraphRAG接口、索引结构、图关系Schema和真实检索评测结果。
- 各解析器、OCR、文件安全和沙箱配置。
- 每个LangGraph节点的错误码、超时、最大重试和并发配置。
- 真实客户数、活跃用户、日均与峰值任务量。
- 客户常态和活动峰值压测脚本与原始结果。
- 真实脱敏测试集、DeepEval导出和人工复核记录。
- MySQL任务、反馈、偏好、Outbox表结构和MinIO对象命名。
- OpenTelemetry Trace、告警和Watchdog恢复记录。
