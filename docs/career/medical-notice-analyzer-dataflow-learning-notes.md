# 医疗公告分析 Agent：数据流、字段与面试学习笔记

> 本文是 `medical-notice-analyzer-resume-packaging.md` 的配套学习文档。
>
> 岗位方向：Agent 开发、AI 应用工程师、AI 后端工程师。
>
> 当前业务口径：日采集约 9000～10000 条公告；内部验证阶段正式分析通常每天 10 份以内；客户化包装完成态按日均 300～500 份、活动期峰值约 1000 份/日进行容量设计，后两项属于目标容量和模拟口径。
>
> 模型口径：报告生成使用 DeepSeek V4 Pro；质检与修订使用 DeepSeek V4 Flash；DeepEval Judge 使用公司部署的 GLM-5 与 DeepSeek V4 Flash。
>
> 说明：文档同时描述当前仓库实现与假设已完成的目标架构。未经真实测试的数字不能当成真实生产指标。

---

## 1. 字段阅读规则

后续所有字段按以下格式解释：

| 项目 | 含义 |
|---|---|
| 字段名 | 程序或 JSON 中实际使用的名称 |
| 中文含义 | 业务人员可以理解的含义 |
| 数据来源 | 该字段由数据库、解析器、规则或模型中的哪一方产生 |
| 存在原因 | 为什么必须保留该字段 |
| 示例 | 一个不包含公司敏感信息的样例 |

字段不是为了“显得复杂”而增加。每个字段至少应服务于以下目标之一：业务查询、证据追溯、状态控制、质量判断、失败恢复、版本审计或性能分析。

---

## 2. 当前系统与目标系统总览

### 2.1 当前仓库主链路

```text
材料选择
  -> 从 MySQL 读取公告正文和附件元数据
  -> 下载并解析附件
  -> 构建完整 Evidence Pack
  -> 生成面向 Dify 的紧凑输入
  -> Dify Workflow 生成 ReportIR / Markdown
  -> 本地结构修复与质量门
  -> 必要时受控修复
  -> 保存 Analysis Run
  -> 页面展示 / 用户修订 / 受控 Word 导出
  -> DeepEval 旁路评测
```

### 2.2 假设完成的目标链路

```text
客户请求进入 FastAPI
  -> 鉴权、账号额度、幂等检查和限流
  -> MySQL 事务写任务与 Outbox
  -> Celery 按优先级和任务类型投递队列
  -> LangGraph 根据 Agent State 执行节点
  -> MySQL 保存状态，MinIO 保存文件和大对象，Redis 保存队列/缓存/锁
  -> DeepEval 异步评测
  -> OpenTelemetry 记录提交、排队、执行、模型调用和存储 Trace
  -> 根据队列积压与等待 P95 扩容或收缩 Worker
```

---

## 3. 第 0 步：用户选择材料并创建客户任务

### 输入格式

```json
{
  "customer_id": "customer_demo_001",
  "requested_by_user_id": "user_demo_008",
  "idempotency_key": "req_20260804_001",
  "request_priority": 5,
  "capacity_profile": "customer_normal_v1",
  "primary_materials": [
    {"menu_code": "project_notice", "articleid": "article_001"}
  ],
  "auxiliary_materials": [
    {"menu_code": "policy_notice", "articleid": "article_002"}
  ],
  "enable_attachment_download": true,
  "force_refresh_attachments": false
}
```

### 字段解释

| 字段名 | 中文含义 | 数据来源 | 存在原因 | 读取或写入节点 | 示例 |
|---|---|---|---|---|---|
| `customer_id` | 发起任务的客户账号标识 | 登录鉴权上下文或客户账号表 | 用于额度、限流、计费和权限审计；不代表客户拥有私有知识库 | FastAPI任务创建接口写入MySQL和State；限流器、报表与审计读取 | `customer_demo_001` |
| `requested_by_user_id` | 客户账号下的具体操作用户 | 登录令牌解析结果 | 区分同一客户内由谁创建、取消或修订任务 | FastAPI写入；任务详情、审计日志和反馈修订读取 | `user_demo_008` |
| `idempotency_key` | 客户请求幂等键 | 客户端生成或服务端根据请求指纹生成 | 网络重试时避免创建两份相同正式任务 | FastAPI和MySQL唯一索引读取/写入；Celery任务启动前再次检查 | `req_20260804_001` |
| `request_priority` | 任务优先级 | 套餐规则、业务规则或管理员配置 | 峰值期间优先处理正式客户报告，避免低优先级评测占用资源 | FastAPI写入State；Celery路由节点读取并选择队列 | `5` |
| `capacity_profile` | 当前容量场景名称 | 部署配置、压测场景或任务策略 | 区分内部验证、客户常态和活动峰值使用的限流与告警阈值 | FastAPI写入；限流器、扩缩容与监控规则读取 | `customer_normal_v1` |
| `primary_materials` | 主分析材料列表 | 用户选择 | 主材料可以直接决定本次报告的事实范围 | FastAPI写入任务材料关系；`load_materials`读取 | 采购公告 |
| `auxiliary_materials` | 辅助材料列表 | 用户选择或RAG推荐后确认 | 提供背景和上下文，但不能覆盖主材料事实 | FastAPI或检索确认节点写入；`build_evidence`读取 | 政策说明 |
| `menu_code` | 公告栏目或来源分类代码 | MySQL | 与 `articleid` 一起唯一定位一条公告 | `load_materials`读取 | `project_notice` |
| `articleid` | 公告业务主键 | MySQL | 定位具体公告 | `load_materials`读取 | `article_001` |
| `enable_attachment_download` | 是否下载附件 | 用户或系统配置 | 某些测试场景只读取正文，生产分析通常需要附件 | `parse_attachments`读取 | `true` |
| `force_refresh_attachments` | 是否绕过附件缓存重新解析 | 用户或系统配置 | 处理附件更新、缓存损坏或解析器升级 | `parse_attachments`读取并决定是否复用MinIO资产 | `false` |

主材料与辅助材料必须分开。主材料中的事实可以写入正式结论；辅助材料只能用于背景、关联关系或受限补充，不能把历史项目价格、时间和范围直接当成本次事实。

### 输出、存储与失败处理

- FastAPI在MySQL事务中创建`run_id`、任务记录、材料关系和Outbox事件。
- API成功响应只表示任务已被可靠接收，不表示报告已生成。
- 如果相同`customer_id + idempotency_key`已经存在，返回原`run_id`，而不是重复创建任务。
- 如果客户超过即时提交额度，可返回限流状态或进入受控队列；不能让HTTP请求一直等待模型完成。
- 如果Redis暂时不可用，Outbox事件仍保存在MySQL，由补偿任务在Broker恢复后重新投递。

---

## 4. 第 1 步：MySQL 原始公告数据

### 4.1 公告正文表的主要字段

```json
{
  "menu_code": "project_notice",
  "articleid": "article_001",
  "title": "某医疗耗材采购公告",
  "audittime": "2026-06-01 10:00:00",
  "updatetime": "2026-06-02 10:00:00",
  "menu_name": "项目公告",
  "areaname": "山东省",
  "source": "省级采购平台",
  "sourceurl": "https://example.invalid/notice/001",
  "publicorg": "某公共资源交易中心",
  "projectphase": "申报",
  "projecttype": "医用耗材",
  "dl_project_type": "带量采购",
  "category": "高值耗材",
  "referencenumber": "REF-001",
  "policytype": "采购公告",
  "belongproject": "某采购项目",
  "projectabbreviation": "项目简称",
  "summary": "数据库已有摘要",
  "content": "<p>公告 HTML 正文</p>"
}
```

### 字段解释

| 字段名 | 中文含义 | 存在原因 |
|---|---|---|
| `title` | 公告标题 | 判断公告主题、项目类型和输出报告标题 |
| `audittime` | 公告发布时间 | 提取项目时间线并用于排序 |
| `updatetime` | 数据更新时间 | 判断数据是否发生更新以及缓存是否失效 |
| `menu_name` | 栏目名称 | 区分项目公告、政策通知等类型 |
| `areaname` | 地区名称 | 判断公告适用区域 |
| `source` | 数据来源平台 | 审计和来源展示 |
| `sourceurl` | 原公告地址 | 追溯原文，不直接交给外部 Judge |
| `publicorg` | 发布机构 | 判断责任主体和公告权威性 |
| `projectphase` | 项目阶段 | 区分申报、报价、中选、执行等阶段 |
| `projecttype` | 项目业务类型 | 控制分析模板和业务规则 |
| `dl_project_type` | 带量采购等细分类型 | 触发特定规则完整性检查 |
| `category` | 产品或公告分类 | 辅助识别业务领域 |
| `referencenumber` | 文号或项目编号 | 项目标识与交叉核验 |
| `policytype` | 政策类型 | 区分采购、价格治理、挂网等政策 |
| `belongproject` | 所属项目 | 将多条公告串联到同一项目 |
| `projectabbreviation` | 项目简称 | 页面展示和报告表达 |
| `summary` | 数据库已有摘要 | 只能作为辅助内容，不能替代原文证据 |
| `content` | HTML 原文 | 公告正文的原始形式，需要清洗后使用 |

### 4.2 附件元数据表

```json
{
  "articleattid": "att_001",
  "filename": "采购清单.xlsx",
  "filepath": "/files/采购清单.xlsx",
  "fileext": ".xlsx",
  "filesize": 123456,
  "uploadtime": "2026-06-01 11:00:00",
  "sortnum": 1,
  "fileerrortype": ""
}
```

| 字段名 | 中文含义 | 存在原因 |
|---|---|---|
| `articleattid` | 附件业务 ID | 唯一定位附件并建立证据引用 |
| `filename` | 原附件名 | 页面展示、格式判断和来源追溯 |
| `filepath` | 文件下载路径 | 获取二进制内容；不能直接发送给外部 Judge |
| `fileext` | 扩展名 | 选择 PDF、Word、Excel 等解析器 |
| `filesize` | 文件大小 | 进行大小限制、超时估算和安全检查 |
| `uploadtime` | 附件上传时间 | 判断附件是否更新 |
| `sortnum` | 附件顺序 | 保留原页面中的顺序 |
| `fileerrortype` | 数据源记录的附件错误 | 提前识别不可用附件 |

此时附件仍是 PDF、DOC/DOCX、XLS/XLSX、CSV、TXT、HTML 或 ZIP 等二进制文件，数据库通常只保存附件元数据和下载位置。

---

## 5. 第 2 步：正文清洗与附件解析

### 5.1 正文清洗结果

```json
{
  "content": "<p>申报时间为……</p>",
  "content_text": "申报时间为……",
  "content_text_length": 12800
}
```

| 字段名 | 中文含义 | 数据来源 | 存在原因 |
|---|---|---|---|
| `content` | 原 HTML 正文 | MySQL | 保留原始输入用于调试和追溯 |
| `content_text` | 清洗后的纯文本 | HTML 解析器 | 作为证据抽取和模型输入的正文 |
| `content_text_length` | 正文字符数 | 程序统计 | 决定完整输入还是压缩输入 |

### 5.2 附件解析结果

```json
{
  "articleattid": "att_001",
  "filename": "采购清单.xlsx",
  "download_status": "downloaded",
  "parse_status": "parsed_table_summary",
  "summary": "该文件包含企业、产品、规格和采购量字段。",
  "key_facts": [
    {"name": "时间", "value": "2026年8月10日"}
  ],
  "important_sections": [
    "企业和产品要求：……"
  ],
  "table_summaries": [
    {
      "sheet_name": "采购清单",
      "rows": 12000,
      "columns_count": 12,
      "headers": ["企业名称", "产品名称", "规格型号", "采购量"],
      "resolved_column_map": {
        "enterprise": 0,
        "product": 1,
        "specification": 2,
        "purchase_volume": 3
      },
      "mapping_status": "clear",
      "field_stats": {},
      "source_rows": [],
      "table_heavy": true,
      "evidence_value_score": 88
    }
  ],
  "warnings": []
}
```

### 附件字段解释

| 字段名 | 中文含义 | 存在原因 |
|---|---|---|
| `download_status` | 附件下载状态 | 区分未下载、成功和失败 |
| `parse_status` | 解析状态 | 区分纯文本解析、表格摘要、失败或不支持 |
| `summary` | 附件内容摘要 | 控制模型输入长度；不能作为唯一高风险事实来源 |
| `key_facts` | 规则初步提取出的关键片段 | 帮助后续构建结构化证据 |
| `important_sections` | 与业务相关的重要段落 | 避免长文压缩时遗漏申报、价格、执行等章节 |
| `table_summaries` | 表格结构化摘要列表 | 避免将数万行表格全部放入模型上下文 |
| `sheet_name` | Excel 工作表名或表格来源名 | 定位具体表格 |
| `rows` | 表格总行数 | 判断是否属于大表格 |
| `columns_count` | 表格列数 | 描述表格规模 |
| `headers` | 原始表头 | 识别企业、产品、价格等字段 |
| `resolved_column_map` | 业务字段到列序号的映射 | 将不同附件表头统一成标准语义 |
| `enterprise` | 企业字段 | 统一表示企业名称所在列 |
| `product` | 产品字段 | 统一表示产品名称所在列 |
| `specification` | 规格字段 | 统一表示规格型号所在列 |
| `purchase_volume` | 采购量字段 | 统一表示采购数量所在列 |
| `mapping_status` | 列映射是否明确 | 模糊时可以进入受限 LLM 辅助识别或人工复核 |
| `field_stats` | 字段统计信息 | 保存非空数、唯一值数、样例值和数值范围 |
| `source_rows` | 少量原始样例行 | 帮助理解表格结构，不代表全量数据 |
| `table_heavy` | 是否为大表格 | 触发结构化摘要而非全量输入 |
| `evidence_value_score` | 表格证据价值分 | 在压缩时优先保留高价值表格 |
| `warnings` | 解析告警 | 防止失败被误认为成功 |

解析并不保证对任意文件 100% 无损。扫描 PDF、异常编码、复杂合并单元格、公式、图片表格和损坏文件都可能产生信息损失。因此系统必须保存解析状态、告警、原始文件哈希和人工复核标记。

客户化后，多个客户选择同一公告时不应重复执行相同OCR和表格解析。系统使用`source_hash + parser_version`定位可复用的`ParsedDocument`；只有来源内容或解析器版本变化时才重新解析。

---

## 6. 第 3 步：完整 Evidence Pack

完整 Evidence Pack 是后端的事实底座，不等于最终发送给模型的压缩输入。

```json
{
  "evidence_schema_version": 2,
  "pack_id": "pack_001",
  "primary_materials": [],
  "auxiliary_materials": [],
  "evidence_items": [],
  "generation_guidance": {},
  "warnings": [],
  "timings": {}
}
```

### 顶层字段解释

| 字段名 | 中文含义 | 存在原因 |
|---|---|---|
| `evidence_schema_version` | 证据包结构版本 | 支持 Schema 演进和历史数据迁移 |
| `pack_id` | 本次证据包 ID | 将证据包、分析任务和报告关联起来 |
| `primary_materials` | 主材料的完整解析结果 | 确定本次报告的核心事实范围 |
| `auxiliary_materials` | 辅助材料的解析结果 | 提供背景但受到事实边界限制 |
| `evidence_items` | 标准化证据单元列表 | 为事实追溯、质量检查和 DeepEval 提供统一输入 |
| `generation_guidance` | 生成约束 | 规定不得虚构、不得要求查附件等规则 |
| `warnings` | 全局告警 | 汇总下载失败、解析失败和压缩风险 |
| `timings` | 构建阶段耗时 | 性能分析和 Agent Trace 展示 |

### 6.1 A 级直接证据

```json
{
  "evidence_id": "sha256...",
  "level": "A",
  "kind": "attachment_text",
  "value": "申报截止时间为……",
  "normalized_value": null,
  "source_ref": {
    "menu_code": "project_notice",
    "articleid": "article_001",
    "attachment_id": "att_001",
    "filename": "采购文件.pdf",
    "page_no": 3,
    "sheet_name": null,
    "table_index": null,
    "row": null,
    "column": null,
    "cell_range": null,
    "quote": "申报截止时间为……",
    "source_hash": "sha256..."
  },
  "derived_from": [],
  "extractor_version": "pdf-parser/v1",
  "mandatory": true
}
```

| 字段名 | 中文含义 | 存在原因 |
|---|---|---|
| `evidence_id` | 证据内容 ID | 根据规范化内容计算，识别重复与篡改 |
| `level` | 证据等级 | A 为直接证据，B 为派生事实，C 为辅助内容 |
| `kind` | 证据类型 | 区分正文、附件文本、表格单元格等 |
| `value` | 原始证据值 | 保留来源原义 |
| `normalized_value` | 标准化值 | A 级通常为空；B 级用于统一时间、金额等格式 |
| `source_ref` | 来源定位信息 | 将报告结论定位回具体公告或附件位置 |
| `attachment_id` | 附件 ID | 识别来源附件 |
| `page_no` | 页码 | 定位 PDF 内容 |
| `sheet_name` | 工作表名 | 定位 Excel 内容 |
| `table_index` | 表格序号 | 定位文档中的表格 |
| `row` / `column` | 行列号 | 定位具体表格单元格 |
| `cell_range` | 单元格范围 | 支持 A1 或 R1C1 范围表示 |
| `quote` | 最短原文摘录 | 在不发送整份文件的情况下提供证据 |
| `source_hash` | 来源内容哈希 | 验证来源是否发生变化 |
| `derived_from` | 依赖的证据 ID | A 级为空，B 级指向 A 级 |
| `extractor_version` | 解析器或提取器版本 | 版本升级后可重建并比较结果 |
| `mandatory` | 是否强制保留 | 二次压缩时不能删除关键证据 |

### 6.2 B 级派生事实

```json
{
  "evidence_id": "sha256...",
  "level": "B",
  "kind": "derived_fact",
  "value": {
    "name": "申报截止时间",
    "value": "2026年8月10日17:00"
  },
  "normalized_value": {
    "field": "submission_deadline",
    "value": "2026-08-10T17:00:00+08:00"
  },
  "source_ref": {},
  "derived_from": ["A_LEVEL_EVIDENCE_ID"],
  "extractor_version": "deadline-extractor/v2",
  "mandatory": true
}
```

| 字段名 | 中文含义 | 存在原因 |
|---|---|---|
| `name` | 事实中文名称 | 便于展示和调试 |
| `field` | 标准业务字段名 | 便于程序按字段比较、检索和评测 |
| `submission_deadline` | 申报截止时间字段 | 不同文档表达可以统一到同一字段 |
| `derived_from` | 该事实来自哪些 A 级证据 | 保证 B 级不是无来源的模型结论 |

A 与 B 看似冗余，但职责不同：A 保存“原文是什么”，B 保存“程序如何理解原文”。删除 A 会失去审计和追溯；删除 B 会让每个后续节点重复解析原文，难以统一格式和做规则判断。两者属于有意冗余，不是两份独立真相。

为了避免 B 级过期：A 级应尽量不可变；B 级必须保存 `derived_from`、`source_hash` 和 `extractor_version`；父证据或提取器版本变化后，应使旧 B 级失效并重新生成。

### 6.3 C 级辅助证据

C 级包括摘要、生成指导、告警、诊断和历史记忆。它可以帮助模型组织语言，但不能单独支撑价格、时间、数量、企业和产品等事实性结论。

---

## 7. 第 4 步：二次压缩后的生成输入

系统不会修改完整 Evidence Pack，而是生成一个派生的模型输入视图。

```json
{
  "pack_variant": "generation_payload",
  "input_strategy": "llm_compressed",
  "effective_content_chars": 280000,
  "generation_payload_chars": 78000,
  "primary_materials": [],
  "auxiliary_materials": [],
  "generation_guidance": {},
  "generation_warnings": [],
  "mandatory_evidence_retention": {
    "total": 36,
    "retained": 36,
    "rate": 1.0,
    "status": "measured"
  },
  "evidence_items_omitted_count": 120,
  "evidence_items_omitted_sha256": "sha256..."
}
```

### 字段解释

| 字段名 | 中文含义 | 存在原因 |
|---|---|---|
| `pack_variant` | 数据包用途 | 说明这是生成输入，而不是完整事实底座 |
| `input_strategy` | 输入策略 | 标记未压缩、规则压缩、LLM 压缩或分阶段生成 |
| `effective_content_chars` | 去重后的有效内容字符数 | 判断真实内容规模 |
| `generation_payload_chars` | 最终生成输入字符数 | 检查平台变量和模型上下文限制 |
| `generation_warnings` | 压缩阶段告警 | 说明 LLM 压缩失败、使用规则降级等情况 |
| `mandatory_evidence_retention` | 强制证据保留结果 | 证明关键证据没有被压缩删除 |
| `total` | 压缩前强制证据数量 | 保留率的分母 |
| `retained` | 压缩后保留数量 | 保留率的分子 |
| `rate` | 保留比例 | `retained / total` |
| `evidence_items_omitted_count` | 被省略的可选证据数量 | 将信息损失显式量化 |
| `evidence_items_omitted_sha256` | 被省略证据 ID 集合的哈希 | 便于审计不同压缩版本 |

### 输入策略

| `input_strategy` | 含义 |
|---|---|
| `direct_clean` / `full_input` | 内容未超限，尽量完整输入 |
| `light_compact` | 删除模板、重复和低价值内容 |
| `safe_compact` | 接近输入上限，优先保留主材料和强制证据 |
| `llm_compressed` | 将长材料分块后通过 LLM 生成受约束摘要 |
| `rule_compressed_fallback` | LLM 压缩不可用时使用确定性规则降级 |
| `attachment_led` | 正文较短、核心事实集中在附件 |
| `table_heavy` | 大表格使用字段结构、统计和样例，不输入全量行 |
| `staged_generation` | 单次输入仍然过大，先分材料生成中间结果，再综合 |

### 会不会损失信息

会。任何压缩都有损失，附件解析本身也可能损失。系统不能诚实地承诺“提取全部信息且完全无损”。正确目标是：

1. 完整 Evidence Pack 始终作为后端事实底座保留。
2. 模型只看到派生的紧凑视图，不能反向覆盖完整包。
3. 关键字段和 Mandatory Evidence 保留率必须达到 100%。
4. 记录被省略的证据数量与哈希，使损失可审计。
5. 大表格不宣称全量进入模型，而是保留原文件并抽取结构、统计和关键行。
6. 解析失败、Mandatory 证据超限或关键事实冲突时进入分阶段生成或人工复核。
7. 生成后用关键事实覆盖率和 Claim-Evidence 支持率检查是否发生重要遗漏。

面试表达应使用“有界损失、关键事实保真、全量证据可追溯”，不要使用“完全无损压缩”。

---

## 8. 第 5 步：生成节点输出

生成模型根据紧凑证据输出 `ReportGenerationResult`。

```json
{
  "success": true,
  "provider": "dify",
  "provider_run_id": "workflow_run_001",
  "report_title": "某医疗耗材采购公告分析",
  "report_markdown": "# 报告标题\n……",
  "report_ir": {},
  "quality_check": {},
  "remaining_issues": [],
  "generation_warnings": [],
  "raw_response_hash": "sha256...",
  "raw_response_excerpt": "",
  "generation_failure_codes": [],
  "metadata": {}
}
```

| 字段名 | 中文含义 | 存在原因 |
|---|---|---|
| `success` | 模型工作流是否完成 | 与报告是否可交付不同；生成成功仍可能质检失败 |
| `provider` | 生成服务提供者 | 当前实际为 Dify 代理，目标架构可进一步记录模型供应商 |
| `provider_run_id` | 上游工作流运行 ID | 将本地任务关联到 Dify 运行记录 |
| `report_title` | 报告标题 | 页面和 Word 文件使用 |
| `report_markdown` | 可展示的 Markdown 报告 | 页面展示和文本检查 |
| `report_ir` | 结构化报告中间表示 | 稳定渲染 Markdown 和 DOCX，避免直接处理自由文本 |
| `quality_check` | 生成工作流返回的初步质检结果 | 后续本地质量门会继续补充 |
| `remaining_issues` | 尚未解决的问题 | 控制是否进入修复或人工复核 |
| `generation_warnings` | 生成阶段告警 | 记录降级、截断和异常 |
| `raw_response_hash` | 原始响应哈希 | 不保存完整敏感响应时仍可审计 |
| `raw_response_excerpt` | 有界的响应摘要 | 调试使用，不能包含推理过程和敏感信息 |
| `generation_failure_codes` | 生成失败码 | 区分超时、格式错误、供应商失败等 |
| `metadata` | 扩展元数据 | 保存版本、耗时、修复状态等 |

### ReportIR 格式

```json
{
  "schema_version": 1,
  "title": "报告标题",
  "suggested_filename": "报告文件名",
  "notice_type": "集采/接续采购类",
  "publish_date": "2026-06-01",
  "source_agency": "发布机构",
  "document_name": "公告名称",
  "lead_paragraphs": [],
  "sections": [
    {
      "heading": "一、项目概况",
      "paragraphs": [],
      "tables": [
        {
          "title": "关键时间",
          "headers": ["事项", "时间"],
          "rows": [["申报截止", "2026-08-10 17:00"]],
          "notes": []
        }
      ],
      "highlights": []
    }
  ],
  "enterprise_tips": [],
  "disclaimer": ""
}
```

| 字段名 | 中文含义 | 存在原因 |
|---|---|---|
| `schema_version` | ReportIR 结构版本 | 兼容历史报告 |
| `suggested_filename` | 建议导出文件名 | 生成安全且稳定的 DOCX 名称 |
| `notice_type` | 公告业务类型 | 决定报告结构和质检规则 |
| `publish_date` | 发布日期 | 报告元数据 |
| `source_agency` | 发布机构 | 报告来源说明 |
| `document_name` | 原公告名称 | 保留业务对应关系 |
| `lead_paragraphs` | 报告导语 | 结构化保存开篇段落 |
| `sections` | 报告章节 | 支持自然章节而非固定模板 |
| `heading` | 章节标题 | 结构完整性检查 |
| `paragraphs` | 章节正文 | 存储分析内容 |
| `tables` | 章节表格 | 存储时间、价格、产品等结构化结果 |
| `headers` | 表头 | 定义每列语义 |
| `rows` | 表格数据行 | 存储结构化事实 |
| `notes` | 表格备注 | 说明口径与限制 |
| `highlights` | 章节重点 | 页面突出展示 |
| `enterprise_tips` | 企业注意事项 | 分析层结论，必须有事实支持 |
| `disclaimer` | 免责声明 | 输出发布要求 |

---

## 9. 第 6 步：质量检查

质量检查不是单一分数，而是多层门禁。

### 9.1 Claim-Evidence Index

系统从 Markdown 和 ReportIR 中拆出 Claim，并尝试关联 A/B 级证据。

```json
{
  "claim_id": "sha256...",
  "text": "申报截止时间为2026年8月10日17:00",
  "normalized_text": "申报截止时间为2026年8月10日17:00",
  "locations": ["report_ir.sections[0].paragraphs[0].sentence[0]"],
  "supported": true,
  "evidence_ids": ["evidence_001"],
  "source_refs": [],
  "support_levels": ["A", "B"],
  "match_score": 100
}
```

| 字段名 | 中文含义 | 存在原因 |
|---|---|---|
| `claim_id` | 报告断言 ID | 去重、修复和评测时定位同一句结论 |
| `text` | 原始断言 | 展示和修复使用 |
| `normalized_text` | 去空格和符号后的断言 | 提高匹配稳定性 |
| `locations` | 断言在 Markdown/ReportIR 中的位置 | 定向删除或改写 |
| `supported` | 是否找到 A/B 证据 | 判断是否属于无依据事实 |
| `evidence_ids` | 支持该断言的证据 ID | 追溯来源 |
| `source_refs` | 支持证据的位置引用 | 展示证据和校验引用合法性 |
| `support_levels` | 使用了 A 还是 B 级证据 | 禁止只用 C 级摘要支持事实 |
| `match_score` | 规则匹配分 | 描述匹配强度，不等于 DeepEval 分数 |

聚合指标：

| 指标 | 含义 |
|---|---|
| `claim_count` | 报告中识别出的断言总数 |
| `supported_claim_count` | 找到 A/B 证据的断言数 |
| `unsupported_claim_count` | 没有证据支持的断言数 |
| `ab_support_rate` | `supported_claim_count / claim_count` |
| `c_independent_support_count` | 仅依靠 C 级内容的事实数量，目标应为 0 |

### 9.2 规则质量门

规则质量门检查：

- 无证据断言；
- C 级摘要被错误地当作事实；
- 来源定位无效；
- 必需章节缺失；
- 有证据的关键主题未被报告覆盖；
- 事实冲突；
- 禁用表达或正式正文为空。

主要输出字段：

| 字段名 | 中文含义 |
|---|---|
| `passed` | 是否通过当前质量门 |
| `status` | `passed`、`blocked` 或 `not_applicable` |
| `primary_failure_code` | 优先级最高的失败原因 |
| `blocking_issue_codes` | 所有阻断交付的问题码 |
| `missing_section_ids` | 缺少的必需章节 |
| `missing_topic_ids` | 有证据但报告遗漏的主题 |
| `claim_ab_support_rate` | A/B 证据支持率 |

### 9.3 LLM 质检输出

```json
{
  "status": "needs_fix",
  "issues": [],
  "unsupported_claims": [],
  "history_leakage": [],
  "missing_rules": [],
  "language_issues": [],
  "fix_instructions": [],
  "summary": ""
}
```

| 字段名 | 中文含义 |
|---|---|
| `status` | `pass`、`needs_fix` 或 `block` |
| `issues` | 通用问题列表 |
| `unsupported_claims` | 无证据或与证据矛盾的结论 |
| `history_leakage` | 将历史项目事实错误写入当前报告的问题 |
| `missing_rules` | 报告遗漏的重要规则 |
| `language_issues` | 夸张、绝对化或不专业表达 |
| `fix_instructions` | 修订节点执行的定向要求 |
| `summary` | 质检摘要 |

---

## 10. 第 7 步：修复节点

修复后仍使用 `ReportGenerationResult` 格式，但会更新：

- `report_markdown`：修复后的 Markdown；
- `report_ir`：修复后的结构化报告；
- `quality_check`：再次质检结果；
- `remaining_issues`：仍未解决的问题；
- `metadata.repair_count`：修复次数；
- `metadata.repair_actions`：执行过的删除、缩窄或结构补全动作；
- `metadata.repair_success`：修复后是否通过。

修复分为两类：

1. 确定性修复：删除禁用表达、删除无证据表格行、将混合句缩窄到有证据的部分、修复结构格式。
2. LLM 修订：把 `fix_instructions`、原报告和受限证据交给 DeepSeek V4 Flash，要求只修复指定问题，不能新增事实。

完成修复后必须重新运行质量门，而不是把“模型说已修好”当作成功。

---

## 11. 第 8 步：最终 Analysis Run

```json
{
  "run_id": "run_001",
  "pack_id": "pack_001",
  "customer_id": "customer_demo_001",
  "request_priority": 5,
  "capacity_profile": "customer_normal_v1",
  "status": "finished",
  "run_status": "finished",
  "workflow_run_id": "workflow_run_001",
  "provider_run_id": "workflow_run_001",
  "report_title": "报告标题",
  "report_markdown": "……",
  "report_ir": {},
  "version": 1,
  "deliverable": true,
  "needs_manual_review": false,
  "repair_attempted": true,
  "repair_success": true,
  "repair_count": 1,
  "quality_check": {},
  "quality_gate": {},
  "warnings": [],
  "compact_pack_chars": 78000,
  "input_strategy": "llm_compressed",
  "provider": "dify",
  "generator_version": "",
  "prompt_version": "",
  "workflow_version": "",
  "timings": {}
}
```

| 字段名 | 中文含义 | 存在原因 |
|---|---|---|
| `run_id` | 本地分析任务 ID | 查询进度、报告和历史版本 |
| `customer_id` | 任务所属客户账号 | 支持额度、计费与审计，但不改变共享知识库边界 |
| `request_priority` | 当前任务优先级 | 决定峰值时的队列路由和资源顺序 |
| `capacity_profile` | 容量场景 | 关联该任务使用的限流、扩容和告警阈值 |
| `status` / `run_status` | 当前任务状态 | 两个字段必须一致，防止状态迁移歧义 |
| `workflow_run_id` | Dify 工作流 ID | 上游排障 |
| `version` | 报告版本 | 用户修订时递增 |
| `deliverable` | 是否允许交付 | 生成成功不等于可交付 |
| `needs_manual_review` | 是否需要人工复核 | 高风险或质量门失败时为真 |
| `repair_attempted` | 是否尝试过修复 | 解释最终结果经过哪些步骤 |
| `repair_success` | 修复是否成功 | 判断是否仍需人工处理 |
| `repair_count` | 修复次数 | 防止无限修复循环 |
| `compact_pack_chars` | 实际模型输入大小 | 性能与信息保留分析 |
| `provider` | 当前生成后端 | 现有系统记录为 Dify，目标系统可记录具体模型路由 |
| `generator_version` | 生成器代码版本 | 回归和审计 |
| `prompt_version` | Prompt 版本 | 比较不同 Prompt 的质量 |
| `workflow_version` | 工作流版本 | 比较 Dify/LangGraph 流程版本 |

---

## 12. 第 9 步：DeepEval 旁路评测

报告会被投影为有限、脱敏的 `ProjectionUnit`：

```json
{
  "unit_id": "unit_001",
  "kind": "claim",
  "text": "申报截止时间为……",
  "claim_count": 1,
  "evidence": [],
  "expected_facts": ["申报截止时间为……"],
  "attachment_expectation": "2个附件均成功解析"
}
```

| 字段名 | 中文含义 | 存在原因 |
|---|---|---|
| `unit_id` | 评测单元 ID | 单独定位某个 Claim 的评分 |
| `kind` | 评测单元类型 | 当前主要是 Claim |
| `text` | 待评测报告内容 | 对应 DeepEval 的 `actual_output` |
| `claim_count` | 单元包含的断言数量 | 控制评测粒度 |
| `evidence` | 脱敏后的 A/B 证据摘录 | 对应 Faithfulness 的 `retrieval_context` |
| `expected_facts` | 应当覆盖的关键事实 | 对应 Critical Coverage 的 `expected_output` |
| `attachment_expectation` | 正确的附件状态描述 | 用于附件一致性指标 |

DeepEval 输出：

```json
{
  "evaluation_id": "eval_001",
  "status": "completed",
  "metrics": [
    {
      "metric_id": "claim_faithfulness_v1",
      "metric_version": "1",
      "status": "scored",
      "score": 0.94,
      "bounded_reason": ""
    }
  ],
  "judge_provider": "internal",
  "resolved_model_or_profile_version": "glm5",
  "token_input": 12000,
  "token_output": 1200,
  "cost": 0,
  "latency_ms": 18000
}
```

| 字段名 | 中文含义 |
|---|---|
| `evaluation_id` | 一次评测任务 ID |
| `metric_id` | 指标 ID |
| `metric_version` | 指标定义版本 |
| `status` | `scored`、`not_applicable`、`unavailable` 或 `error` |
| `score` | 归一化到 0～1 的分数 |
| `bounded_reason` | 经过长度和安全过滤的评分原因 |
| `judge_provider` | Judge 服务来源 |
| `resolved_model_or_profile_version` | 实际 Judge 模型或配置版本 |
| `token_input` / `token_output` | Judge 输入/输出 Token 数 |
| `cost` | Judge 调用成本；公司内部部署时可为 0 或内部核算值 |
| `latency_ms` | 评测耗时 |

---

## 13. 指标分类

### 13.1 报告质量指标

这些指标直接回答“这份报告是否可信、完整、可交付”：

- `claim_ab_support_rate`：报告断言的 A/B 证据支持率；
- `unsupported_claim_count`：无证据断言数；
- `missing_topic_ids`：有证据但未覆盖的关键主题；
- `missing_section_ids`：缺少的必需章节；
- `history_leakage`：历史材料事实泄漏问题数；
- `quality_gate.passed`：规则质量门是否通过；
- 首轮质检通过率；
- 修复成功率；
- 人工复核率；
- 最终可交付率。

### 13.2 DeepEval / 模型输出评估指标

这些指标由 Judge 对生成结果评分：

- Faithfulness：报告是否与提供的证据一致；
- Answer Relevancy：报告是否围绕任务要求；
- Critical Fact Coverage：是否准确覆盖预期关键事实；
- Attachment State Consistency：附件存在、解析状态和报告描述是否一致。

它们更准确地说是“当前模型 + Prompt + 工作流 + 输入策略”的联合效果，不应只归因于基础模型。

### 13.3 系统工程指标

这些指标回答“系统是否快、稳、可恢复”：

- API 提交延迟 P50/P95/P99；
- 端到端任务耗时 P50/P95/P99；
- 附件下载和解析耗时；
- 模型生成、修复和质检耗时；
- 成功率、超时率和重试率；
- 队列等待时间；
- 日均与峰值日完成任务数；
- 峰值提交速率和稳定并发运行数；
- 队列积压量、最老任务年龄和积压清空时间；
- Worker 利用率；
- Token 使用量和单任务成本；
- Evidence 压缩率；
- Mandatory Evidence 保留率；
- Checkpoint 恢复成功率。

不要把 DeepEval 分数与系统延迟混成同一类指标，也不能把目标容量配置当成实际测量值。

---

## 14. Agent State 基础理解

Agent State 不是“状态机图本身”，而是状态机执行过程中共享的当前数据快照。

可以把它类比为一个快递单：

- 节点是不同处理站；
- 边是下一站的路由规则；
- State 是随包裹一起流转、不断更新的快递单；
- Checkpoint 是在某一站拍下并保存的快递单快照。

没有 State 时，每个节点需要自行读取数据库、猜测上一步输出格式并通过大量参数互相传递，容易造成字段不一致、无法恢复和难以调试。

### 目标 Agent State

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
    queue_wait_ms: int

    status: str
    current_node: str
    retry_count: int
    manual_review_required: bool

    evidence_pack_object_key: str
    evidence_pack_sha256: str
    compact_payload_object_key: str

    fact_items: list[dict]
    analysis_items: list[dict]
    report_ir: dict
    report_object_key: str

    quality_gate_passed: bool
    unsupported_claim_count: int
    deepeval_scores: dict[str, float]
    risk_level: str

    model_provider: str
    model_name: str
    prompt_version: str

    trace_id: str
    node_timings_ms: dict[str, int]
    token_usage: dict[str, int]

    error_code: str
    error_message: str
    checkpoint_version: int
```

### 字段分组及原因

| 分组 | 主要字段 | 设计原因 |
|---|---|---|
| 客户身份 | `customer_id`、`requested_by_user_id` | 额度、计费和审计；不等于客户私有知识空间 |
| 幂等与容量 | `idempotency_key`、`request_priority`、`capacity_profile`、`queue_name` | 防止重复任务并支持峰值队列路由 |
| 排队时间 | `submitted_at`、`started_at`、`queue_wait_ms` | 区分排队慢和节点执行慢，驱动扩容告警 |
| 身份 | `run_id`、`pack_id` | 关联任务和证据包 |
| 流程控制 | `status`、`current_node`、`retry_count` | 决定下一节点及是否停止重试 |
| 人工介入 | `manual_review_required`、`risk_level` | 高风险任务进入人工复核 |
| 大对象引用 | `evidence_pack_object_key`、`report_object_key` | State 不直接存放大型原文和附件 |
| 完整性 | `evidence_pack_sha256` | 验证对象内容未被替换 |
| 业务中间结果 | `fact_items`、`analysis_items`、`report_ir` | 节点之间使用结构化数据通信 |
| 质量 | `quality_gate_passed`、`unsupported_claim_count`、`deepeval_scores` | 条件边据此选择通过、修复或人工复核 |
| 模型审计 | `model_provider`、`model_name`、`prompt_version` | 解释某个结果由哪个版本生成 |
| 可观测性 | `trace_id`、`node_timings_ms`、`token_usage` | Trace 页面和性能分析 |
| 异常恢复 | `error_code`、`checkpoint_version` | 失败后判断能否从检查点恢复 |

`queue_name`由FastAPI任务路由或Celery路由器写入，表示任务进入哪个队列，例如`generation_high`；Worker读取它完成消费和审计。`submitted_at`由FastAPI创建任务时写入，`started_at`由Worker真正开始执行时写入，`queue_wait_ms`由两者差值计算并写入MySQL与OpenTelemetry。脱敏示例：提交时间`2026-08-04T10:00:00+08:00`、开始时间`2026-08-04T10:00:21+08:00`、排队等待`21000`毫秒。

当前仓库没有统一 LangGraph State，但 Evidence Pack JSON、Analysis Run JSON、Checkpoint JSON 和 Dify 会话变量共同承担了相似职责。优化的意义是将这些隐式状态收敛为一个类型化、显式、可检查的共享状态。

---

## 15. Celery 基础知识

### 15.1 Celery 是什么

Celery 是 Python 分布式任务队列框架。它不等于 Redis 或 RabbitMQ：

- Celery 定义任务、Worker、重试、超时、路由和工作流；
- Redis/RabbitMQ 作为 Broker 负责传递任务消息；
- Redis、数据库等可以作为 Result Backend 保存任务结果或状态。

### 15.2 核心角色

| 名称 | 中文解释 | 在本项目中的例子 |
|---|---|---|
| Celery App | 任务系统配置中心 | 配置 Broker、队列、序列化和超时 |
| Task | 可异步执行的 Python 函数 | `parse_attachments(run_id)` |
| Producer / Client | 发送任务的一方 | FastAPI 接口 |
| Broker | 消息中转站 | Redis |
| Queue | 对任务进行分类的队列 | `generation_queue` |
| Worker | 从队列取任务并执行的进程 | 生成 Worker、解析 Worker |
| Result Backend | 保存 Celery 任务状态或结果 | Redis；但业务最终状态仍以 MySQL 为准 |
| Celery Beat | 定时任务调度器 | 定时清理孤儿文件、执行评测采样 |

### 15.3 一次任务如何执行

```text
客户请求 FastAPI
  -> 鉴权、额度、限流和幂等检查
  -> MySQL 创建 run_id 与 Outbox
  -> Outbox Publisher 调用 task.apply_async(run_id, priority)
  -> Celery 将任务消息写入 Redis
  -> Worker 获取消息并记录 started_at
  -> Worker 根据 run_id 从 MySQL/MinIO 读取数据
  -> 执行 LangGraph
  -> 将最终业务状态写回 MySQL
  -> Celery 记录 SUCCESS/FAILURE
  -> OpenTelemetry计算queue_wait_ms和节点耗时
```

消息中只传 `run_id`、`pack_id` 等小型标识，不传完整 PDF 或 Evidence Pack，避免消息过大。

### 15.4 基本代码

```python
from celery import Celery

celery_app = Celery(
    "medical_notice",
    broker="redis://redis:6379/0",
    backend="redis://redis:6379/1",
)

@celery_app.task(
    bind=True,
    autoretry_for=(TimeoutError,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
    acks_late=True,
)
def run_analysis_task(self, run_id: str) -> dict:
    # 根据 run_id 读取业务状态
    # 检查幂等键和已完成状态
    # 执行 LangGraph
    # 保存结果
    return {"run_id": run_id, "status": "finished"}
```

字段解释：

| 配置 | 含义 |
|---|---|
| `broker` | Redis 消息队列地址 |
| `backend` | Celery 结果状态保存位置 |
| `bind=True` | 任务函数可以通过 `self` 访问任务上下文和重试能力 |
| `autoretry_for` | 指定哪些异常自动重试 |
| `retry_backoff` | 重试时间指数增长，避免立即反复请求模型 |
| `max_retries` | 最大重试次数 |
| `acks_late` | 执行完成后再确认消息；Worker 中途崩溃时任务可能重新投递 |

`acks_late` 会带来重复执行可能，因此任务必须幂等。例如相同 `run_id + node_name + input_hash` 已完成时直接复用结果，而不是再次生成报告。

### 15.5 常见任务状态

| 状态 | 含义 |
|---|---|
| `PENDING` | 任务尚未开始，或 Result Backend 中没有记录 |
| `STARTED` | Worker 已开始执行，需要启用 started tracking |
| `RETRY` | 当前失败但等待再次执行 |
| `SUCCESS` | Celery 函数执行成功 |
| `FAILURE` | Celery 函数最终失败 |
| `REVOKED` | 任务被取消 |

Celery `SUCCESS` 只表示 Python 任务没有抛出异常，不等于报告质量通过。业务上还需要 MySQL 中的 `deliverable` 和 `quality_gate_passed`。

### 15.6 本项目推荐的队列

| 队列 | 任务 | 峰值处理原则 |
|---|---|---|
| `attachment_queue` | 文件下载、OCR 和解析 | 可水平扩容；优先复用相同附件解析缓存 |
| `generation_high` | 正式客户报告生成 | 高优先级，受模型并发配额控制 |
| `generation_normal` | 普通报告生成 | 常态队列，可根据积压扩容 |
| `quality_queue` | DeepSeek V4 Flash 质检与修订 | 不与生成使用同一并发池 |
| `evaluation_queue` | GLM-5 / DeepSeek V4 Flash DeepEval 评测 | 低优先级；主链路积压时可暂停消费 |
| `export_queue` | ReportIR 渲染和 Word 导出 | CPU/IO任务，与模型调用隔离 |

内部验证阶段每天正式任务不足10份时，一个Celery主任务运行一次LangGraph即可满足需求。客户化完成态按日均300～500份、峰值约1000份/日设计后，仍采用Celery + Redis，但必须增加队列隔离、优先级、账号限流、Worker弹性扩缩容和模型并发控制。这里的日均与峰值是目标容量，不是真实生产数据。

### 15.7 客户化容量数据流与模拟指标

```text
FastAPI提交成功
  -> submitted_at写入MySQL
  -> Celery进入指定queue_name
  -> Worker启动时写started_at
  -> 计算queue_wait_ms
  -> LangGraph执行并记录node_timings_ms
  -> OpenTelemetry聚合队列等待P95、并发数和失败率
  -> 扩缩容控制器读取积压与等待指标
  -> 增减对应队列Worker
```

目标模拟口径：

- 日均分析任务：300～500份；
- 活动期峰值：约1000份/日；
- 峰值提交速率：50份/小时；
- 最大稳定并发运行数：20；
- 队列等待P95：60秒以内；
- 常规任务端到端P95：180秒以内；
- 最终成功率：98.8%。

失败处理：

- 单客户超过速率时由限流器拒绝或延迟，不允许占满全局生成队列。
- 模型返回限流错误时，任务保留在队列并执行指数退避；Schema错误等不可恢复异常不自动无限重试。
- `queue_wait_ms`持续超过目标时优先扩容对应Worker，若受模型并发上限限制，则暂停低优先级评测并告警。
- Worker崩溃后依赖`acks_late`重新投递，通过节点幂等键和Checkpoint跳过已完成步骤。
- Redis故障时依赖MySQL Outbox重新投递，Redis不能成为唯一业务事实源。

---

## 16. 前端代码情况

仓库包含前端功能，但不是独立前端工程：

- 使用原生 HTML、CSS 和 JavaScript；
- 提供 `/records-ui` 材料检索与选择页面；
- 包含运行详情、报告展示、历史和记忆等页面能力；
- 没有 React/Vue 构建链；
- 当前没有完整的 LangGraph Agent Trace 可视化页面。

因为目标岗位不是前端，可在简历中只写“提供任务进度与节点 Trace 展示”，面试重点放在后端如何产生 Trace 数据，而不是前端组件实现。

---

## 17. 面试回答：如何证明压缩可信

推荐回答：

> 我不会把这套方案描述成完全无损。基础 Evidence Pack 由确定性解析器构建并作为后端事实底座保存，模型只消费它的派生紧凑视图。压缩时按照主辅材料、证据等级和业务字段设置保留优先级，价格、时间、数量、企业、产品等 Mandatory Evidence 以及 B 级事实的 A 级父证据必须 100% 保留。系统同时记录压缩前后字符数、强制证据保留率、被省略证据数量及其哈希。对大表格采用结构化字段、统计值和关键行，而不是声称把数万行全部放入上下文；若解析失败、关键证据超限或事实冲突，就转为分阶段生成或人工复核。最后再通过关键事实覆盖率、Claim-Evidence 支持率和 DeepEval Faithfulness 检查是否发生关键内容遗漏。因此我的目标不是零损失，而是关键事实保真、损失可量化、结果可追溯。

---

## 18. 下一步学习问题

1. 明确正式报告最常分析的五类公告，并为每类列出必须保留的关键事实字段。
2. 设计 20～50 条脱敏的 DeepEval 基准样本，而不是一开始虚构 200 条真实测试数据。
3. 绘制一张包含 MySQL、MinIO、Redis、Celery、LangGraph 和 DeepEval 的架构图，并能够逐箭头解释数据格式。
4. 设计客户常态与活动峰值两套压测场景，明确Worker数量、模型并发配额、附件分布和积压清空时间。
