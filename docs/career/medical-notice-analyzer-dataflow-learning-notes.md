# 医疗公告分析 Agent：数据流、字段与面试学习笔记

> 本文是 `medical-notice-analyzer-resume-packaging.md` 的配套学习文档。
>
> 岗位方向：Agent 开发、AI 应用工程师、AI 后端工程师。
>
> 当前业务口径：日采集约9000～10000条公告；内部验证阶段正式分析通常每天10份以内；客户化包装完成态按日均300～500份、活动期峰值约1000份/日进行容量设计，后两项属于目标容量和模拟口径。
>
> 模型口径：报告生成使用DeepSeek V4 Pro；质检、自动修复与反馈修订使用DeepSeek V4 Flash；DeepEval Judge使用公司部署的GLM-5与DeepSeek V4 Flash。
>
> 说明：文档同时描述当前仓库实现与假设已完成的目标架构。未经真实测试的数字不能当成真实生产指标。第19节是当前完整数据流的最高优先级学习口径。

---

## 1. 字段阅读规则

后续所有字段按以下格式解释：

| 项目 | 含义 |
|---|---|
| 字段名 | 程序或JSON中实际使用的名称 |
| 中文含义 | 业务人员可以理解的含义 |
| 数据来源 | 数据库、解析器、规则、模型或运行时 |
| 存在原因 | 业务查询、追溯、状态、恢复、质量、审计或性能 |
| 读写节点 | 哪个节点写入或读取 |
| 示例 | 不包含公司敏感信息的样例 |

---

## 2. 当前系统与目标系统总览

### 2.1 当前仓库主链路

```text
材料选择
  -> 从MySQL读取公告正文和附件元数据
  -> 下载并解析附件
  -> 构建完整Evidence Pack
  -> 生成面向Dify的紧凑输入
  -> Dify Workflow生成ReportIR / Markdown
  -> 本地结构修复与质量门
  -> 必要时受控修复
  -> 保存Analysis Run
  -> 页面展示 / 用户修订 / 受控Word导出
  -> DeepEval旁路评测
```

### 2.2 包装完成态主链路

```text
客户请求进入FastAPI
  -> 鉴权、额度、幂等、参数和限流
  -> MySQL事务创建run_id与Outbox
  -> Celery可靠入队
  -> GraphRAG检索辅助材料
  -> LangGraph执行加载、解析、证据、事实、分析、生成、质检和修复
  -> MySQL保存业务状态，MinIO保存大对象，Redis保存队列/缓存/锁
  -> 可交付报告受控导出
  -> 用户反馈补证修订
  -> 偏好记忆候选与用户确认
  -> DeepEval异步旁路
  -> OpenTelemetry全链路观测
```

---

## 3. 第0步：用户选择材料并创建客户任务

### 输入示例

```json
{
  "customer_id": "customer_demo_001",
  "requested_by_user_id": "user_demo_008",
  "idempotency_key": "req_20260804_001",
  "request_priority": 5,
  "capacity_profile": "customer_normal_v1",
  "analysis_goal": "分析项目规则、时间和企业准备风险",
  "auto_retrieve_auxiliary": true,
  "primary_materials": [
    {"menu_code": "project_notice", "articleid": "article_001"}
  ],
  "auxiliary_materials": [],
  "enable_attachment_download": true,
  "force_refresh_attachments": false
}
```

| 字段名 | 中文含义 | 数据来源 | 为什么需要 | 读写节点 | 示例 |
|---|---|---|---|---|---|
| `customer_id` | 客户账号标识 | 鉴权上下文 | 额度、限流、计费和审计 | FastAPI写入；报表、审计读取 | `customer_demo_001` |
| `requested_by_user_id` | 操作用户标识 | 登录令牌 | 确认由谁创建、修订和导出 | FastAPI写入；审计读取 | `user_demo_008` |
| `idempotency_key` | 请求幂等键 | 客户端或请求指纹 | 网络重试不重复创建任务 | FastAPI/MySQL唯一索引读写 | `req_20260804_001` |
| `request_priority` | 任务优先级 | 套餐或业务规则 | 峰值队列路由 | FastAPI写入；Celery路由读取 | `5` |
| `capacity_profile` | 容量场景 | 部署或压测配置 | 区分常态与活动峰值阈值 | FastAPI写入；监控读取 | `customer_normal_v1` |
| `analysis_goal` | 当前分析目标 | 用户输入 | 控制GraphRAG查询和报告重点 | FastAPI写入；检索/生成读取 | `比较挂网规则` |
| `auto_retrieve_auxiliary` | 是否自动召回辅助材料 | 用户选择或默认配置 | 决定是否调用GraphRAG | FastAPI写入；检索节点读取 | `true` |
| `primary_materials` | 主材料1～3条 | 用户显式选择 | 决定当前事实范围 | FastAPI写入；`load_materials`读取 | 项目公告 |
| `auxiliary_materials` | 手动辅助材料0～10条 | 用户选择 | 提供历史、政策和背景 | FastAPI写入；检索合并节点读取 | 政策说明 |

### 输出、存储和失败处理

- MySQL事务创建`run_id`、任务、材料关系和Outbox事件。
- API只返回`queued`，不等待模型。
- 相同`customer_id + idempotency_key`返回原`run_id`。
- Redis不可用时Outbox保留并补投。
- 单客户和全局双层限流；超过额度返回可解释限流状态。

---

## 4. 第1步：MySQL原始公告数据

MySQL保存公告元数据、HTML正文和附件元数据；正文不是天然已经清洗好的纯文本。

```json
{
  "menu_code": "project_notice",
  "articleid": "article_001",
  "title": "某医疗耗材采购公告",
  "audittime": "2026-06-01 10:00:00",
  "updatetime": "2026-06-02 10:00:00",
  "areaname": "山东省",
  "source": "省级采购平台",
  "sourceurl": "https://example.invalid/notice/001",
  "publicorg": "某公共资源交易中心",
  "projectphase": "申报",
  "projecttype": "医用耗材",
  "dl_project_type": "带量采购",
  "category": "高值耗材",
  "referencenumber": "REF-001",
  "content": "<p>公告HTML正文</p>"
}
```

附件元数据包括`articleattid`、`filename`、`filepath`、`fileext`、`filesize`、`uploadtime`、`fileerrortype`。原始附件在目标架构中进入MinIO，MySQL保存对象Key和状态。

---

## 5. 第2步：正文清洗与附件解析

正文清洗输出：

```json
{
  "content": "<p>申报时间为……</p>",
  "content_text": "申报时间为……",
  "content_text_length": 12800,
  "content_hash": "sha256..."
}
```

附件完成态解析策略：

```text
PDF -> pypdf等文本提取 -> 低文本密度/扫描页OCR -> 通用解析器兜底
DOCX -> python-docx -> 通用解析器兜底
DOC -> LibreOffice受控转换 -> DOCX解析 -> 通用解析器兜底
XLSX/XLSM -> openpyxl(read_only/data_only，不执行宏) -> 通用解析器兜底
XLS -> xlrd或受控转换 -> 通用解析器兜底
CSV/TXT/HTML -> 编码检测与专用解析
ZIP -> 安全解压后逐文件处理
```

`ParsedDocument`示例：

```json
{
  "document_id": "doc_001",
  "source_hash": "sha256...",
  "parser_name": "openpyxl",
  "parser_version": "xlsx-parser/v2",
  "download_status": "downloaded",
  "parse_status": "partial_success",
  "text_object_key": "parsed/doc_001/text.json",
  "table_summaries": [
    {
      "sheet_name": "采购清单",
      "rows": 12000,
      "columns_count": 12,
      "headers": ["企业名称", "产品名称", "规格型号", "采购量"],
      "field_stats": {},
      "source_rows": [],
      "table_heavy": true
    }
  ],
  "warnings": ["merged_cells_detected"]
}
```

工程机制：

- `source_hash + parser_version`命中则复用解析资产。
- 同一附件用Redis分布式锁避免重复OCR。
- 下载、OCR、单文件解析和整组附件分别设置超时。
- 网络和对象存储短时故障重试；文件损坏、密码保护、格式不支持不盲目重试。
- 文件魔数、扩展名、大小、页数、行数和压缩比校验；宏不执行；ZIP防路径穿越；解析进程限制CPU/内存。

---

## 6. 第3步：GraphRAG辅助材料召回

主材料由用户选择1～3条；辅助材料最终0～10条，可以手动选择或自动召回。辅助类型包括补充/更正公告、历史轮次、政策、地区规则、公司审核分析、价格采购量和风险规则，不只限于项目公告。

```text
主材料 + analysis_goal
  -> 查询理解、实体和时间识别
  -> 关键词召回 || 向量召回 || 知识图谱关系扩展
  -> 地区/品类/阶段/时间/source_scope过滤
  -> Rerank、去重和有效期过滤
  -> 证据充分性判断
  -> 选取1～10条辅助材料
```

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

GraphRAG只返回候选资料，候选仍需经过第5步解析和后续Evidence构建。

兜底：GraphRAG不可用时降级为关键词+结构化过滤；仍不可用时使用用户手动材料并记录告警。关键词、向量和图关系可并行，但分别受超时和并发池控制。权限和`source_scope`过滤必须在内容进入任务前完成。

---

## 7. 第4步：完整Evidence Pack、Fact Layer与Analysis Layer

```json
{
  "evidence_schema_version": 3,
  "pack_id": "pack_001",
  "primary_materials": [],
  "auxiliary_materials": [],
  "evidence_items": [],
  "fact_items": [],
  "analysis_items": [],
  "generation_guidance": {},
  "warnings": [],
  "timings": {}
}
```

### 7.1 A级直接证据

```json
{
  "evidence_id": "ev_a_001",
  "level": "A",
  "kind": "attachment_text",
  "value": "集采开始日期为5月20日",
  "source_ref": {
    "articleid": "article_001",
    "attachment_id": "att_001",
    "filename": "采购文件.pdf",
    "page_no": 3,
    "quote": "集采开始日期为5月20日",
    "source_hash": "sha256..."
  },
  "derived_from": [],
  "extractor_version": "pdf-parser/v2",
  "mandatory": true
}
```

### 7.2 B级结构化事实

```json
{
  "evidence_id": "ev_b_001",
  "level": "B",
  "kind": "derived_fact",
  "value": {
    "name": "集采开始日期",
    "raw_value": "5月20日"
  },
  "normalized_value": null,
  "derived_from": ["ev_a_001"],
  "extractor_version": "date-extractor/v3",
  "source_hash": "sha256...",
  "mandatory": true,
  "uncertainty": "原文未给出年份"
}
```

B级不能在原文缺少年份时擅自生成具体年份。

### 7.3 C级辅助信息

C级包括摘要、生成指导、告警、诊断和受控偏好。它不能单独支撑价格、时间、数量、企业、产品或资质事实。

### 7.4 Analysis Layer

```json
{
  "analysis_id": "analysis_001",
  "conclusion": "企业应在项目开始前完成材料准备",
  "supporting_fact_ids": ["fact_start_date_001"],
  "reasoning_type": "schedule_preparation",
  "confidence": 0.78,
  "risk_level": "medium",
  "uncertainty": "未获取企业当前材料准备进度"
}
```

“企业应在5月20日前准备好”是分析或建议，不是C级证据。

Evidence构建不应描述成“主要只靠正则”，而是确定性解析、领域规则、词典、正则和结构化提取器共同完成。复杂语义可以调用受约束LLM生成候选，但候选必须通过Schema、字段白名单、索引范围和来源一致性校验。

冲突事实记录`conflict`并转人工，不能自动任选一个答案。

---

## 8. 第5步：Compact Payload二次压缩

完整Evidence Pack永久保留，Compact Payload只是模型输入视图。

```json
{
  "pack_variant": "generation_payload",
  "application_input_cap_chars": 240000,
  "model_context_tokens": 128000,
  "estimated_input_tokens": 56000,
  "reserved_output_tokens": 10000,
  "safety_margin_tokens": 6000,
  "input_strategy": "safe_compact",
  "generation_payload_chars": 182000,
  "mandatory_evidence_retention": {
    "total": 36,
    "retained": 36,
    "rate": 1.0
  },
  "evidence_items_omitted_count": 120,
  "evidence_items_omitted_sha256": "sha256..."
}
```

24万字符是应用硬上限，不是目标填满长度。预算还要扣除系统Prompt、当前任务、偏好、工具消息、输出Token和安全余量，最终以Token估算校验。

压缩顺序：去重/噪声清理 → Mandatory保护 → 规则压缩 → 必要时LLM分块压缩 → Token复核 → 仍超限则`staged_generation`。

LLM压缩失败时使用规则降级；Mandatory Evidence无法保留时不得强行生成。

---

## 9. 第6步：Prompt组装与生成

Prompt优先级：

1. 系统规则、身份、安全要求和输出Schema；
2. 当前任务指令和报告结构；
3. Compact Payload及来源引用；
4. 用户已确认的结构化偏好Profile。

公告正文、附件、GraphRAG资料和用户反馈均为不可信数据，使用明确分隔符包裹，不能覆盖系统规则。

生成节点使用DeepSeek V4 Pro，输出`ReportIR + Markdown`。模型调用层记录：

- `model_name`、`prompt_version`、`workflow_version`；
- 连接、读取和总超时；
- Token输入输出和成本；
- 并发信号量；
- 错误分类和重试次数；
- 原始响应哈希和有界摘要。

网络、限流和短时上游故障指数退避+抖动；Schema失败允许一次受控格式修复；权限、输入超限和安全错误不盲目重试。

---

## 10. 第7步：第一次质量检查

质量检查包括规则门和DeepSeek V4 Flash语义质检。

`Claim-Evidence Index`示例：

```json
{
  "claim_id": "claim_001",
  "text": "申报截止时间为2026年8月10日17:00",
  "locations": ["report_ir.sections[0].paragraphs[0].sentence[0]"],
  "supported": true,
  "evidence_ids": ["ev_a_001", "ev_b_001"],
  "support_levels": ["A", "B"],
  "match_score": 100
}
```

同步质量门检查：非空输出、ReportIR Schema、必需章节、发布机构、时间、标的物、附件状态、数值日期Exact Match、Claim-Evidence支持、来源定位、C级误用、历史事实泄漏、禁用表达和高风险无证据结论。

```json
{
  "status": "needs_fix",
  "passed": false,
  "issues": [],
  "unsupported_claims": [],
  "missing_section_ids": [],
  "missing_topic_ids": [],
  "history_leakage": [],
  "fix_instructions": [],
  "claim_ab_support_rate": 0.96
}
```

DeepEval不作为同步阻断门。

---

## 11. 第8步：自动修复与二次质检

1. 确定性修复：结构、空字段、禁用表达、无证据表格行、Markdown/ReportIR同步。
2. LLM局部修复：目标段落+问题码+允许使用的A/B证据+禁止新增事实约束。
3. 生成新报告版本并记录diff和`repair_actions`。
4. 重新执行相同质量门。

`auto_repair_count`最多2次。报告版本使用乐观锁；两个修复任务不能覆盖同一基线版本。

两次后仍存在阻断问题、高风险无证据事实、关键附件失败或冲突事实时，`needs_manual_review=true`。

---

## 12. 第9步：最终Analysis Run、持久化和交付

```json
{
  "run_id": "run_001",
  "pack_id": "pack_001",
  "customer_id": "customer_demo_001",
  "status": "finished",
  "version": 2,
  "deliverable": true,
  "needs_manual_review": false,
  "auto_repair_count": 1,
  "user_revision_count": 0,
  "quality_gate": {},
  "report_object_key": "reports/run_001/v2/report.json",
  "report_sha256": "sha256...",
  "trace_id": "trace_demo_001"
}
```

最终校验确认ReportIR/Markdown一致、对象哈希、版本关系、MySQL/MinIO持久化、状态迁移和`deliverable`。Watchdog、超时和重试贯穿所有步骤，最终节点只负责状态收口和一致性。

前端同步展示`claim_ab_support_rate`、`unsupported_claim_count`、`missing_topic_ids`、告警和可交付状态。

---

## 13. 第10步：Word导出与DeepEval旁路

只有`deliverable=true`的报告允许正式导出。DOCX由ReportIR受控渲染，文件名清洗、模板/图片白名单、短时签名下载链接。导出失败进入`export_queue`单独重试，不重新调用生成模型。

DeepEval进入低优先级`evaluation_queue`，前端先显示`pending`，完成后更新Faithfulness等指标；Judge不可用时为`unavailable`，不阻塞报告。

---

## 14. 第11步：用户反馈修订

统一反馈结构：

```json
{
  "feedback_id": "feedback_001",
  "run_id": "run_001",
  "base_report_version": 2,
  "feedback_source": "free_text",
  "feedback_text": "增加新疆与山东挂网规则对比",
  "intent_type": "shared_knowledge_extension",
  "intent_confidence": 0.94,
  "user_revision_count": 1
}
```

三类路由：

- 样式/结构：当前报告+反馈+原Evidence Pack。
- 当前公告补证：回到ParsedDocument定向检索并补建证据。
- 跨地区/历史/政策：GraphRAG召回，构建`revision_evidence_pack`后局部修订。

预制按钮也不能只使用“意见+报告”；至少要携带原Evidence Pack。针对性反馈可先召回1～3条高相关材料，但最终数量由证据充分性决定，最多10条。

用户自助修订最多3轮。`user_revision_count`只在成功生成新版本后递增；网络重试、Worker重投和同一轮模型重试不占轮次。超过3轮或仍阻断则人工处理。

---

## 15. 第12步：偏好记忆候选和沉淀

反馈日志与长期偏好记忆分开。

```json
{
  "memory_candidate_id": "mem_candidate_001",
  "customer_id": "customer_demo_001",
  "user_id": "user_demo_008",
  "source_feedback_ids": ["feedback_001"],
  "preference_type": "report_style",
  "preference_value": "优先使用表格并保持结论简洁",
  "status": "pending",
  "scope": "user",
  "created_at": "2026-08-04T10:30:00+08:00",
  "expires_at": null
}
```

只允许稳定样式偏好进入候选；公告事实、一次性要求、敏感数据和跨客户数据不得进入。用户明确同意后状态变为`approved/active`。用户可查看、修改、撤销和删除。偏好优先级低于系统规则、证据和当前指令，不进入共享GraphRAG事实库。

---

## 16. Agent State基础理解

```python
class AnalysisState(TypedDict, total=False):
    customer_id: str
    requested_by_user_id: str
    run_id: str
    pack_id: str
    idempotency_key: str
    request_priority: int
    capacity_profile: str
    queue_name: str
    submitted_at: str
    started_at: str
    heartbeat_at: str
    watchdog_deadline_at: str
    queue_wait_ms: int

    status: str
    current_node: str
    retry_count: int
    auto_repair_count: int
    user_revision_count: int
    manual_review_required: bool

    primary_material_ids: list[str]
    auxiliary_material_ids: list[str]
    retrieval_result_key: str
    evidence_pack_object_key: str
    evidence_pack_sha256: str
    compact_payload_object_key: str

    fact_items: list[dict]
    analysis_items: list[dict]
    report_ir: dict
    report_object_key: str

    quality_gate_passed: bool
    unsupported_claim_count: int
    deepeval_status: str
    deepeval_scores: dict[str, float]
    risk_level: str

    model_provider: str
    model_name: str
    prompt_version: str
    preference_profile_id: str

    trace_id: str
    node_timings_ms: dict[str, int]
    token_usage: dict[str, int]

    error_code: str
    error_message: str
    last_completed_node: str
    checkpoint_version: int
```

`heartbeat_at`由运行节点周期写入；Watchdog读取它和`watchdog_deadline_at`判断任务是否卡死。脱敏示例：节点`parse_attachments`最后心跳为`2026-08-04T10:02:15+08:00`，截止时间为`10:07:15`。

---

## 17. Celery与并发控制

推荐队列：

| 队列 | 任务 | 峰值原则 |
|---|---|---|
| `retrieval_queue` | GraphRAG查询和Rerank | 独立并发池和总超时 |
| `attachment_queue` | 下载、OCR和解析 | 水平扩容、缓存复用、同附件锁 |
| `generation_high` | 高优先级客户生成 | 模型信号量和账号配额 |
| `generation_normal` | 普通生成 | 按积压扩容 |
| `quality_queue` | 质检和自动修复 | 与生成并发池隔离 |
| `revision_queue` | 用户反馈修订 | 报告版本乐观锁 |
| `export_queue` | Word导出 | CPU/IO隔离 |
| `evaluation_queue` | DeepEval | 低优先级，可暂停 |

关键配置：`acks_late`、`worker_prefetch_multiplier=1`、`soft_time_limit/time_limit`、`autoretry_for`、`retry_backoff`、`task_routes`、`worker_concurrency`和短期`result_expires`。

---

## 18. 跨步骤工程保护矩阵

| 机制 | 统一规则 |
|---|---|
| 幂等 | 创建任务使用`customer_id + idempotency_key`；节点使用`run_id + node_name + input_hash` |
| 重试 | 仅可恢复异常重试，指数退避+抖动；不可恢复错误直接失败或人工 |
| 超时 | API、下载、OCR、解析、GraphRAG、模型、导出、节点和整任务分层超时 |
| Watchdog | 检查心跳、当前节点和截止时间，恢复卡死任务或转人工 |
| Checkpoint | 每个成功节点保存输入输出指纹和最后完成节点 |
| 并发 | 队列隔离、模型信号量、附件并行、同附件锁、报告乐观锁 |
| 安全 | 鉴权、范围过滤、文件沙箱、Prompt注入隔离、日志脱敏、短时下载授权 |
| 一致性 | MySQL Outbox、MinIO临时对象、哈希、幂等消费者和补偿任务 |
| 可观测性 | OpenTelemetry关联`run_id/trace_id`，记录排队、检索、节点、模型、存储、重试和成本 |
| 降级 | GraphRAG→关键词/手选；专用解析→OCR/通用解析；LLM压缩→规则压缩；DeepEval失败不阻塞 |

---

## 19. 当前规范化0～12步复述口径

```text
0. FastAPI鉴权、额度、限流、幂等，MySQL事务创建run_id与Outbox
1. 用户选择1～3条主材料，手选或GraphRAG确定0～10条辅助材料
2. 从MySQL读取HTML正文和附件元数据，从MinIO读取原始附件
3. 专用解析器/OCR生成可缓存ParsedDocument
4. 构建A/B/C Evidence、Fact Layer和独立Analysis Layer
5. 依据24万字符硬上限与Token预算生成Compact Payload
6. 组装系统规则、当前任务、Evidence Context和已确认偏好，DeepSeek V4 Pro生成ReportIR/Markdown
7. 规则质量门+DeepSeek V4 Flash语义质检
8. 确定性修复+局部LLM修复，最多2次，再次质检
9. 最终后端一致性校验、持久化和deliverable判断
10. 受控Word导出；DeepEval异步旁路
11. 用户反馈意图分类、当前资料补证或GraphRAG补证，最多3轮自助修订
12. 从反馈中提取稳定样式偏好候选，用户确认后加入账户偏好Profile
```

所有步骤均具备幂等、Checkpoint、可恢复重试、分层超时、Watchdog、权限和安全校验、并发控制及OpenTelemetry Trace。

---

## 20. 面试回答：如何证明压缩可信

> 我不会把方案描述成完全无损。完整Evidence Pack和原始解析资产永久保存，模型消费的是派生Compact Payload。24万字符只是应用硬上限，实际输入还要预留Prompt、输出Token和安全余量。压缩按主辅材料、证据等级和业务字段排序，价格、时间、数量、企业、产品等Mandatory Evidence及B级事实的A级父证据必须保留，并记录省略数量和哈希。LLM压缩失败时降级到规则压缩，仍超限则分阶段生成；关键证据无法保留时转人工。最后通过关键事实覆盖、Claim-Evidence支持和异步DeepEval检查关键遗漏。

---

## 21. 下一步学习问题

1. 明确五类高频公告的Mandatory Evidence字段。
2. 设计20～50条脱敏DeepEval基准样本，再扩展到200条模拟回归集。
3. 绘制MySQL、MinIO、Redis、Celery、LangGraph、GraphRAG和DeepEval架构图，并逐箭头解释格式。
4. 为每个节点确定具体超时、最大重试次数、并发池和错误码。
5. 设计用户偏好确认、查看、撤销和删除页面的数据接口。
