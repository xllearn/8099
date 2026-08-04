# 医疗公告分析 Agent：数据流、字段与面试学习笔记

> 本文记录包装完成态的九步业务数据流，以及每一步的输入、输出、存储、LangGraph State变化和失败处理。
>
> 最高优先级口径以 `medical-notice-analyzer/docs/career/PROJECT_PACKAGING_CONTEXT.md` 为准。
>
> 最近更新：2026-08-04

---

## 0. 数据来源和职责边界

公司上游数据平台负责公告采集和公告库维护，历史数据口径为每天采集约9000～10000条公告。医疗公告分析Agent不负责采集，只消费以下数据：

- 用户显式选择的1～3个主材料；
- 用户手动选择的0～10个辅助材料；
- 用户未选择辅助材料时，由公司GraphRAG自动召回的1～10个文档；
- 用户反馈阶段为补证或跨地区对比再次召回的文档。

客户化完成态按日均300～500份、活动期峰值约1000份分析任务进行容量设计；该数字是目标容量或模拟口径。

---

## 1. 字段阅读规则

所有字段按以下维度解释：

| 维度 | 说明 |
|---|---|
| 字段名 | 程序、数据库或JSON中的实际名称 |
| 中文含义 | 业务人员可理解的含义 |
| 数据来源 | 用户、MySQL、GraphRAG、解析器、规则、模型或运行时 |
| 为什么需要 | 业务、追溯、恢复、质量、安全或性能目的 |
| 项目作用 | 在当前九步流程中解决什么问题 |
| 读写节点 | 哪个节点写入、哪个节点读取 |
| 脱敏示例 | 不含公司敏感信息的样例 |

---

## 2. 整体架构与状态流转

```text
FastAPI接单
  -> MySQL事务创建run_id和Outbox
  -> Celery可靠入队
  -> 1. 主辅材料确定
  -> 2. 解析与两阶段证据包构建
  -> 3. Prompt组装与报告生成
  -> 4. 第一次质检
  -> 5. 定向修订
  -> 6. 二次质检
  -> 7. 后端校验、前端展示与Word交付
  -> 8. 用户反馈分类、补证和新版本修订
  -> 9. 偏好候选与用户确认
  -> DeepEval异步旁路
```

### LangGraph State基本原则

- State只保存任务状态、小型结构化结果和MinIO对象引用，不保存大型附件和完整Evidence Pack正文。
- 每个节点使用`run_id + node_name + input_hash`作为幂等键。
- 节点成功后保存Checkpoint，失败重试从最后安全节点继续。
- `heartbeat_at`由运行节点周期更新，Watchdog根据心跳和截止时间判断卡死任务。

核心State示例：

```python
class AnalysisState(TypedDict, total=False):
    run_id: str
    customer_id: str
    requested_by_user_id: str
    idempotency_key: str
    status: str
    current_node: str
    last_completed_node: str
    checkpoint_version: int
    retry_count: int
    auto_repair_count: int
    user_revision_count: int

    primary_material_ids: list[str]
    auxiliary_material_ids: list[str]
    retrieval_result_key: str
    parsed_document_keys: list[str]
    evidence_pack_object_key: str
    compact_payload_object_key: str
    report_object_key: str

    quality_gate_passed: bool
    claim_ab_support_rate: float
    unsupported_claim_count: int
    deliverable: bool
    needs_manual_review: bool

    preference_profile_id: str
    trace_id: str
    heartbeat_at: str
    error_code: str
```

---

## 3. 接单前置步骤：FastAPI、幂等和异步入队

虽然简历主叙事从主辅材料开始，但工程链路先完成可靠接单。

### 输入

```json
{
  "customer_id": "customer_demo_001",
  "requested_by_user_id": "user_demo_008",
  "idempotency_key": "req_20260804_001",
  "analysis_goal": "分析项目要求并识别企业准备风险",
  "primary_material_ids": ["notice_001"],
  "manual_auxiliary_material_ids": [],
  "auto_retrieve_auxiliary": true
}
```

### 输出和存储

- MySQL事务创建任务、`run_id`、材料关系和Outbox事件；
- API返回`status=queued`，不等待检索或模型；
- Outbox消费者将`run_id`投递到Celery队列；
- Redis只作为Broker、锁和短期进度，不作为业务最终状态源。

### 失败处理

- 相同`customer_id + idempotency_key`返回原`run_id`；
- Redis不可用时Outbox保留，恢复后补投；
- 参数、材料范围或权限错误不重试；
- 单客户和全局限流防止队列被单一客户占满。

---

## 4. 第一步：主材料与辅助材料

### 输入

- 主材料：用户显式选择1～3个项目公告；
- 手动辅助材料：0～10个；
- 自动检索开关：用户未手选时可启用公司GraphRAG；
- `analysis_goal`：本次分析目标，用于检索查询理解和报告重点控制。

### GraphRAG处理

```text
主材料 + analysis_goal
  -> 实体、地区、品类和时间识别
  -> 关键词召回 || 向量召回 || 知识图谱扩展
  -> 权限/source_scope/地区/时间/有效期过滤
  -> Rerank与去重
  -> 证据充分性判断
  -> 选取1～10个辅助文档
```

GraphRAG属于公司提供的共享检索能力。本项目负责调用、过滤和消费结果，不将召回摘要直接当作事实。

### 输出

```json
{
  "document_id": "policy_023",
  "document_type": "regional_policy",
  "retrieval_reason": "同地区挂网规则",
  "retrieval_score": 0.91,
  "relation_type": "same_region_policy",
  "graph_path": ["当前公告", "所属地区", "挂网规则"],
  "source_scope": "company_curated",
  "effective_date": "2026-01-01",
  "selected": true,
  "retrieval_version": "graphrag-v3"
}
```

### 存储与State变化

- MySQL保存任务与材料关系；
- 完整召回明细进入MinIO；
- State写入`auxiliary_material_ids`和`retrieval_result_key`；
- `retrieve_auxiliary_materials`写入，`load_materials`和解析节点读取。

### 失败处理

- GraphRAG不可用：降级为关键词加结构化过滤；
- 仍不可用：只使用用户手选材料并记录告警；
- 空召回不是网络异常，不进行无意义重复调用；
- 关键词、向量和图关系可并行，但各自设置超时和并发池。

---

## 5. 第二步：正文与附件解析

### 正文输入和输出

MySQL保存HTML正文和公告元数据，系统先清洗为纯文本：

```json
{
  "article_id": "notice_001",
  "content_html": "<p>集采开始日期为5月20日</p>",
  "content_text": "集采开始日期为5月20日",
  "content_text_length": 12,
  "content_hash": "sha256_demo"
}
```

### 附件解析策略

```text
PDF -> pypdf文本提取 -> 扫描页或低文本密度时OCR -> Apache Tika兜底
DOCX -> python-docx -> Apache Tika兜底
XLSX/XLSM -> openpyxl只读解析 -> 结构化摘要/有限样例/统计 -> Apache Tika兜底
DOC/XLS/未知格式 -> 受控转换或Apache Tika
```

XLSM只读取数据，不执行宏。大型Excel不把全部单元格直接放进模型输入，而是保存结构、表头、统计、有限样例和可定向查询的行列索引。

### ParsedDocument输出

```json
{
  "document_id": "doc_001",
  "source_hash": "sha256_demo",
  "parser_name": "openpyxl",
  "parser_version": "xlsx-parser/v3",
  "parse_status": "partial_success",
  "fallback_used": false,
  "text_object_key": "parsed/doc_001/text.json",
  "table_summaries": [
    {
      "sheet_name": "采购清单",
      "rows": 12000,
      "columns_count": 12,
      "headers": ["企业名称", "产品名称", "采购量"],
      "sample_row_count": 20,
      "table_heavy": true
    }
  ],
  "warnings": ["merged_cells_detected"]
}
```

### 存储与State变化

- 原始附件、解析文本和表格明细存MinIO；
- MySQL保存解析状态、解析器版本、哈希和对象Key；
- Redis分布式锁防止同一附件重复OCR；
- State只写`parsed_document_keys`和告警摘要。

### 失败处理

- `source_hash + parser_version`一致时复用解析资产；
- 网络下载、对象存储短时故障可重试；
- 文件损坏、密码保护或不支持格式不盲目重试；
- 下载、OCR、单文件解析和整组附件分别设置超时；
- 文件魔数、大小、页数、行数和压缩比校验；ZIP防路径穿越。

---

## 6. 第二步续：A/B/C Evidence Pack构建

### 构建方式

Evidence Pack不是简单关键词截取，由以下组件共同构建：

```text
文档结构解析
  -> 领域词典/正则/表头映射识别候选
  -> 字段语义映射
  -> 值标准化
  -> 一致性和来源校验
  -> A/B/C证据写入
```

复杂表格、跨句引用或复杂PDF可调用受约束LLM做候选映射。LLM只能从提供的真实片段和索引中选择，输出还要通过Schema、字段白名单、索引存在性和值解析校验。

### A级证据

```json
{
  "evidence_id": "ev_a_001",
  "level": "A",
  "kind": "attachment_text",
  "value": "集采开始日期为5月20日",
  "source_ref": {
    "article_id": "notice_001",
    "filename": "采购文件.pdf",
    "page_no": 3,
    "quote": "集采开始日期为5月20日"
  },
  "mandatory": true
}
```

A级保存原文是什么以及在哪里，是最终事实追溯依据。

### B级证据

```json
{
  "evidence_id": "ev_b_001",
  "level": "B",
  "kind": "derived_fact",
  "value": {"集采开始日期": "5月20日"},
  "normalized_value": null,
  "derived_from": ["ev_a_001"],
  "extractor_version": "date-extractor/v3",
  "uncertainty": "原文未给出年份"
}
```

B级保存程序对A级证据的结构化理解。原文没有年份时不能擅自补充年份。

### C级证据

C级保存摘要、生成指导、解析告警、诊断和受控偏好。C级不能单独支撑价格、数量、时间、企业、产品、资质或中选条件等事实。

### 输出、存储和失败处理

- Full Evidence Pack存MinIO；MySQL保存`pack_id`、版本、哈希和状态；
- State写`evidence_pack_object_key`；
- 冲突事实保存为`conflict/unknown`并转人工，不能自动任选一个；
- Mandatory Evidence无法定位来源时设置`needs_manual_review=true`。

---

## 7. 第二步续：Compact Payload二次压缩

完整Evidence Pack永久保留，模型只消费派生的Compact Payload。

### 压缩流程

```text
Full Evidence Pack
  -> 去重和模板噪声清理
  -> Mandatory Evidence保护
  -> 规则压缩
  -> 超预算时LLM分块压缩
  -> 字符与Token预算复核
  -> 仍超限则分阶段生成或人工复核
```

`application_input_cap_chars=240000`是应用硬上限。触发条件是输入超过安全预算，不是要求压缩结果尽量接近240000字符。实际预算需要扣除系统Prompt、用户任务、偏好、输出Token和安全余量。

### 输出示例

```json
{
  "pack_variant": "generation_payload",
  "application_input_cap_chars": 240000,
  "estimated_input_tokens": 56000,
  "reserved_output_tokens": 10000,
  "safety_margin_tokens": 6000,
  "generation_payload_chars": 182000,
  "mandatory_evidence_retention_rate": 1.0,
  "input_strategy": "safe_compact"
}
```

### 失败处理

- LLM压缩失败时降级为规则压缩；
- 仍超限时按章节或材料分阶段生成；
- Mandatory Evidence不能保留时不得强行生成；
- Compact Payload保存对象Key、哈希和省略明细，支持审计和复现。

---

## 8. 第三步：Prompt组装与生成

### 四部分Prompt

1. 系统提示词：模型角色、证据边界、禁止编造、安全要求、输出Schema；
2. 当前用户任务：分析目标和报告大致结构；
3. Compact Payload和Evidence引用；
4. 用户已确认的偏好Profile。

公告、附件、RAG资料和反馈均为不可信数据，使用明确分隔符包裹，不能覆盖系统规则。

### 生成输出

```json
{
  "report_ir": {
    "title": "某项目公告分析",
    "sections": [],
    "claims": [
      {
        "claim_id": "claim_001",
        "text": "集采开始日期为5月20日",
        "evidence_ids": ["ev_a_001", "ev_b_001"]
      }
    ]
  },
  "markdown_object_key": "reports/run_001/v1/report.md"
}
```

报告生成使用DeepSeek V4 Pro。模型调用层记录模型、Prompt版本、连接/读取/总超时、Token、成本、重试次数和响应哈希。

### State与失败处理

- State写`report_object_key`和报告版本；
- 网络、限流和短时上游故障指数退避；
- Schema错误允许一次受控格式修复；
- 权限、安全或输入超限错误不盲目重试；
- 生成并发受模型信号量和账号额度控制。

---

## 9. 第四步：第一次质检

规则门和DeepSeek V4 Flash语义质检共同检查：

- 是否为空、ReportIR Schema是否合法；
- 发布机构、时间、标的物和必需章节是否缺失；
- 数值与日期是否和A/B证据一致；
- Claim是否存在有效Evidence引用；
- C级内容是否被误作事实；
- 辅助材料或历史项目事实是否泄漏为当前事实；
- 附件失败状态是否与报告表述一致。

输出：

```json
{
  "status": "needs_fix",
  "issues": [
    {
      "issue_code": "MISSING_PUBLISH_ORG",
      "target_path": "sections[0]",
      "severity": "medium"
    }
  ],
  "claim_ab_support_rate": 0.96,
  "unsupported_claim_count": 2,
  "missing_topic_ids": ["publish_org"]
}
```

DeepEval不作为同步阻断门，保持异步旁路。

---

## 10. 第五步：定向修订

### 输入

- 当前问题段落和路径；
- 结构化`issue_code`；
- 允许使用的A/B证据；
- 禁止新增事实约束；
- 当前报告版本和乐观锁版本号。

### 处理

1. 先修复结构、空字段、禁用表达和无证据表格行；
2. 再由DeepSeek V4 Flash局部生成新段落；
3. 保存新ReportIR版本、修改动作和前后diff。

### 失败处理

- `auto_repair_count`最多2次；
- 两个修订任务不能覆盖同一基线版本；
- 高风险无证据事实或冲突证据不自动修复，转人工。

---

## 11. 第六步：二次质检

二次质检执行与第一次相同的规则门和语义门，输出剩余问题、A/B支持率、无证据事实数量和阻断状态。

State变化：

- 通过：`quality_gate_passed=true`；
- 可修复且次数未满：回到修订节点；
- 高风险或次数耗尽：`needs_manual_review=true`。

模拟评测口径：首轮质量门通过率66.5%提升至88.5%，无证据断言率9.0%下降至2.2%。该数据不是生产实测。

---

## 12. 第七步：后端校验、前端展示与Word交付

### 最终后端校验

- 报告非空；
- 二次质检不存在阻断问题；
- ReportIR与Markdown一致；
- MinIO对象哈希、MySQL版本关系和状态迁移一致；
- 关键附件解析状态满足交付要求；
- 报告版本持久化成功。

### 输出

```json
{
  "run_id": "run_001",
  "status": "finished",
  "version": 2,
  "claim_ab_support_rate": 0.96,
  "unsupported_claim_count": 2,
  "quality_issues": [],
  "deliverable": true,
  "needs_manual_review": false
}
```

前端展示二次质检问题、原文遵循评分、A/B支持率、无证据事实数量和可交付状态。

只有`deliverable=true`的版本可以导出Word。DOCX由ReportIR受控渲染；导出失败进入独立队列重试，不重新生成报告。

### 贯穿式失败处理

重试、超时和降级并非只在第七步执行：

- GraphRAG降级到关键词或手选；
- 专用解析器降级到OCR或Apache Tika；
- LLM压缩降级到规则压缩；
- 网络和存储短时故障重试；
- 不可恢复问题进入人工；
- Watchdog根据心跳恢复卡死任务。

---

## 13. 第八步：用户反馈与补证修订

### 统一反馈输入

```json
{
  "feedback_id": "feedback_001",
  "run_id": "run_001",
  "base_report_version": 2,
  "feedback_source": "free_text",
  "feedback_text": "增加新疆与山东挂网规则对比",
  "intent_type": "shared_knowledge_extension",
  "user_revision_count": 1
}
```

### 预制按钮

“更正式”“分析更详细”等预制按钮使用：反馈意见 + 当前最终报告 + 原Evidence Pack。修订结果仍需生成新版本并进入质检，不能直接覆盖正式报告。

### 自由文本三类路由

1. 样式或结构：用户反馈 + 当前报告 + 原Evidence Pack；
2. 当前公告补证：回到ParsedDocument，定向检索并补建新的A/B证据；
3. 跨地区、历史或政策扩展：调用GraphRAG召回材料，构建`revision_evidence_pack`后修订。

“新疆与山东挂网对比”通常先召回1～3个高相关文档；最终数量由证据充分性决定，但不超过辅助材料总上限10个。

### 次数和失败处理

- 用户最多自助修订3轮；
- 成功产生新报告版本后才增加`user_revision_count`；
- 网络重试、Worker重投和同一轮模型重试不计入轮次；
- 超过3轮或仍有阻断问题提交人工处理。

---

## 14. 第九步：偏好记忆候选与确认

反馈日志、审计记录和长期偏好记忆分开保存。

```json
{
  "memory_candidate_id": "mem_candidate_001",
  "user_id": "user_demo_008",
  "source_feedback_ids": ["feedback_001"],
  "preference_type": "report_style",
  "preference_value": "优先展示数据并保持结论简洁",
  "status": "pending",
  "scope": "user"
}
```

### 规则

- 只抽取稳定的结构和表达偏好；
- 公告事实、一次性需求和敏感信息不能进入记忆；
- 用户明确同意后状态变为`approved/active`；
- 用户可以查看、修改、撤销和删除；
- 记忆优先级低于系统规则、证据和当前任务；
- 不进入共享GraphRAG事实库，不跨客户复用。

---

## 15. 核心字段完整解释

| 字段名 | 中文含义 | 数据来源 | 为什么需要 | 项目作用 | 读写节点 | 脱敏示例 |
|---|---|---|---|---|---|---|
| `run_id` | 一次分析运行ID | FastAPI任务创建 | 关联状态、队列、报告、日志和评测 | 全链路主键 | 创建任务写；所有节点读 | `run_001` |
| `primary_material_ids` | 主材料列表 | 用户选择 | 限定当前事实范围 | 防止辅助材料覆盖主公告 | 接单写；加载、证据、生成读 | `["notice_001"]` |
| `auxiliary_material_ids` | 辅助材料列表 | 用户或GraphRAG | 提供政策、历史和对比背景 | 扩展分析但不改变主事实 | 检索写；解析和证据读 | `["policy_023"]` |
| `source_ref` | 原文来源定位 | 解析器 | 支持事实追溯和人工复核 | 将Claim回溯到页码或单元格 | 解析/证据写；质检/前端读 | `采购文件.pdf，第3页` |
| `derived_from` | B级依赖的A级证据 | 结构化提取器 | 证明结构化事实如何得到 | 连接原文和标准字段 | 证据构建写；质检读 | `["ev_a_001"]` |
| `application_input_cap_chars` | 输入字符硬上限 | 系统配置 | 控制应用输入预算 | 触发压缩但不要求填满 | 压缩节点读 | `240000` |
| `claim_ab_support_rate` | Claim获得A/B支持比例 | 质量门计算 | 评估事实可追溯性 | 决定修订和交付 | 质检写；后端/前端读 | `0.96` |
| `unsupported_claim_count` | 无证据事实数量 | 质量门 | 发现模型幻觉或遗漏 | 触发修订或阻断 | 质检写；修订/前端读 | `2` |
| `auto_repair_count` | 自动修复次数 | 修订节点 | 防止无限修复循环 | 最多2次后转人工 | 修订写；路由读 | `1` |
| `user_revision_count` | 用户修订轮数 | 成功修订后累加 | 控制最多3轮 | 区分用户需求与网络重试 | 修订写；路由读 | `2` |
| `deliverable` | 是否允许正式交付 | 后端最终校验 | 阻止不合格版本下载 | 控制Word导出 | 最终校验写；导出读 | `true` |
| `memory_candidate_id` | 偏好候选ID | 记忆提取节点 | 候选与正式偏好分离 | 等待用户确认 | 记忆节点写；确认接口读写 | `mem_candidate_001` |

---

## 16. Celery、存储和可观测性

### 队列

| 队列 | 内容 |
|---|---|
| `retrieval_queue` | GraphRAG查询与Rerank |
| `attachment_queue` | 下载、OCR与解析 |
| `generation_queue` | 报告生成 |
| `quality_queue` | 质检与自动修订 |
| `revision_queue` | 用户反馈修订 |
| `export_queue` | Word导出 |
| `evaluation_queue` | DeepEval低优先级旁路 |

### 存储

- MySQL：任务、状态、材料关系、版本、反馈、候选记忆、Outbox和指标汇总；
- MinIO：原始附件、ParsedDocument、Full Evidence Pack、Compact Payload、ReportIR、Markdown和DOCX；
- Redis：Celery Broker、短期缓存、进度和分布式锁。

### 工程机制

- `acks_late`和`worker_prefetch_multiplier=1`降低Worker异常造成的任务丢失和长任务抢占；
- Transactional Outbox解决MySQL任务已创建但队列消息未发出的不一致；
- OpenTelemetry以`run_id/trace_id`串联API、队列、GraphRAG、节点、模型和存储；
- 敏感原文、完整Prompt、令牌和个人信息不直接写日志；
- DeepEval失败只更新`unavailable`，不阻塞可交付报告。

---

## 17. 面试复述版本

> 用户先选择1～3个主公告，辅助材料可以手选0～10个，也可以由公司GraphRAG通过关键词、向量和知识图谱自动召回1～10个。系统从MySQL读取HTML正文并清洗，附件按PDF、DOCX和Excel使用专用解析器，失败时用Apache Tika兜底。解析结果先构建完整A/B/C Evidence Pack，再根据24万字符硬上限和Token预算生成Compact Payload。Prompt由系统规则、当前任务、证据上下文和用户确认偏好四部分组成，生成节点要求关键Claim返回Evidence引用。报告经过第一次质检、局部修订和二次质检，再由后端做非空、阻断、版本和持久化校验，前端展示A/B支持率和无证据事实数量。用户可以通过按钮或文字反馈，文字反馈分为样式修改、当前公告补证和GraphRAG扩展三类，最多自助修订3轮。流程结束后只抽取稳定样式偏好形成候选记忆，必须由用户确认后才生效。九步业务链路由LangGraph编排，Celery、MySQL、MinIO、Redis、Checkpoint、幂等和OpenTelemetry负责异步执行、失败恢复和追踪。
