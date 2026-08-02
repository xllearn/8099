# 医疗公告分析 Agent：问题与应对记录

> 用途：持续记录项目学习、架构设计、简历包装和面试准备过程中提出的问题，以及对应的分析、解决方案和面试表述。
>
> 维护规则：历史问题编号保持不变；新增问题按照 9、10、11……继续追加；答案发生变化时，在原问题下增加“更新记录”，而不是删除旧结论。
>
> 下一条问题编号：**9**

## 已确认的项目事实

- 业务以带量采购、集中采购及其接续类公告为主。
- 日采集公告约 9000～10000 条，但真正进入正式 AI 分析链路的任务通常每天不超过 10 份。
- 正式报告通常为 2000～5000 字，至少包含 4～5 个章节；是否生成表格取决于公告中是否存在适合结构化展示的数据。
- 报告生成模型的官方 API `model` 字段为 `deepseek-v4-pro`。
- 质检和用户反馈修订模型的官方 API `model` 字段为 `deepseek-v4-flash`。
- DeepEval Judge 使用公司部署的 GLM-5 和 DeepSeek V4 Flash。
- 当前仓库已有用户文字反馈后的报告修订能力；长期记忆属于受控上下文补充，目前不是完整的向量 RAG。

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

---

# 1. Evidence Pack 是否需要像 RAG 一样提前离线处理

## 1.1 问题

对于证据包构建，是否应该像 RAG 系统一样先对文档进行离线处理，使用户选择公告后可以立即生成报告？日采集约一万条公告，如果全部进行附件下载、OCR、表格解析和证据构建，成本和资源消耗又不合理。用户选择材料以后再处理，还能否称为提前处理？

## 1.2 结论

不应该对每天采集的全部公告执行同等深度的完整处理。推荐采用：

> **全量轻处理 + 热点预处理 + 用户选择后的按需深处理 + 内容哈希缓存复用。**

同时要区分三个概念：

1. **数据采集处理**：对全部公告执行低成本的元数据清洗和去重。
2. **文档资产预处理**：将单份文档解析为可以复用的文本、表格和定位信息。
3. **任务级 Evidence Pack 组装**：根据本次用户选择的主材料、辅助材料和分析目标，组织最终证据包。

Evidence Pack 具有任务上下文：同一公告在一次任务中可能是主材料，在另一次任务中可能是辅助材料，因此完整的任务级 Evidence Pack 通常必须在用户选择以后组装。但附件解析结果、正文清洗结果和表格结构可以提前生成并复用。

## 1.3 推荐的三级处理架构

### 第一级：全量轻处理

对每天 9000～10000 条公告都执行：

- 保存公告元数据和附件元数据；
- HTML 正文清洗；
- 内容哈希计算；
- 文件类型识别；
- 重复公告和重复附件检测；
- 依据标题、栏目、发布机构和业务关键词进行粗分类；
- 标记是否属于带量采购、集采、中选结果、价格治理等高价值类型。

这一级不执行大规模 OCR，也不对所有大型 Excel 逐行解析。

关键字段：

| 字段名 | 中文含义 | 存在原因 |
|---|---|---|
| `source_hash` | 原始正文或附件内容哈希 | 判断内容是否变化，并复用已有解析结果 |
| `mime_type` | 文件真实媒体类型 | 不只依赖扩展名选择解析器 |
| `document_asset_id` | 文档资产唯一 ID | 将一份可复用文档与具体分析任务解耦 |
| `preprocess_status` | 轻处理状态 | 区分未处理、成功、失败和待重试 |
| `notice_category` | 公告粗分类 | 判断是否需要提前深处理 |

### 第二级：热点和高概率材料预处理

只对高概率会被业务人员使用的公告执行较重处理，例如：

- 带量采购和集采核心公告；
- 国家、省级重点单位发布的公告；
- 标题中包含申报、报价、中选、采购量、价格联动等关键词；
- 被业务人员频繁访问或进入候选列表的公告。

处理内容包括：

- 下载附件；
- PDF、Word、Excel 文本和表格解析；
- OCR；
- 页码、表格和单元格定位；
- 生成可复用的文档级证据资产。

### 第三级：用户选择后的任务级组装

用户选择 1～3 条主材料和若干辅助材料后：

1. 检查文档资产缓存；
2. 未解析的材料按需解析；
3. 给材料标记 `primary` 或 `auxiliary` 角色；
4. 根据分析目标提取 Mandatory Evidence；
5. 生成任务级 Evidence Pack；
6. 根据模型和平台限制生成 Compact Payload。

字段解释：

| 字段名 | 中文含义 | 存在原因 |
|---|---|---|
| `material_role` | 材料角色，主材料或辅助材料 | 防止将背景材料误写成当前公告事实 |
| `parser_version` | 解析器版本 | 解析逻辑升级后判断缓存是否需要重建 |
| `cache_hit` | 是否命中已有解析结果 | 统计按需处理的加速效果 |
| `evidence_pack_id` | 本次任务证据包 ID | 关联生成、质检、修复和评测结果 |

## 1.4 用户选择后再处理算不算提前处理

严格来说：

- 用户请求发起后才解析附件，属于**按需预处理**或**在线预处理**；
- 在报告生成模型调用之前完成解析，仍然是生成前处理；
- 只有在用户请求之前完成并可复用的解析，才属于典型的离线预处理。

面试时不要混用概念，可以说：

> 我把处理拆成文档资产预处理和任务级证据组装。全量公告只做低成本的清洗、哈希和分类；高价值公告提前完成附件解析；其他材料在用户选择后按需解析，并按 source_hash 与 parser_version 缓存。任务级 Evidence Pack 必须在选择后组装，因为主辅材料关系和分析目标是任务特定的。这样既避免全量 OCR 的资源浪费，又让重复使用的材料能够直接命中缓存。

## 1.5 推荐指标

- 文档资产缓存命中率；
- 按需解析 P50/P95 耗时；
- 热点预处理命中率；
- 无效预处理比例；
- 附件解析成功率；
- 从用户点击生成到任务进入模型节点的等待时间。

---

# 2. 专用解析器与 Apache Tika、MarkItDown 如何选择

## 2.1 问题

当前项目针对 PDF、Word、Excel 等类型分别编写解析逻辑。很多开源项目使用 Apache Tika 或 Microsoft MarkItDown 统一处理多种文档。哪种效果更好，项目应该如何选择？

## 2.2 结论

不存在一个统一解析器在所有格式和业务目标上都更好。推荐采用：

> **统一解析接口 + 关键格式专用适配器 + Tika/MarkItDown 作为通用兜底。**

医疗采购报告特别依赖价格、采购量、企业、产品、注册证、医保编码、表格行列和来源定位，因此不能仅依赖通用“文档转纯文本”结果。

## 2.3 三种方式对比

| 方案 | 优点 | 局限 | 适合用途 |
|---|---|---|---|
| 专用格式解析器 | 能保留页码、表格、Sheet、行列、公式结果和业务字段；可针对复杂文件修复 | 代码量大，格式升级和异常样本维护成本高 | 关键 PDF、Excel、Word 的高保真解析 |
| Apache Tika | 格式覆盖广，自动检测类型，文本和元数据提取成熟，适合统一服务 | Java 服务依赖较重；业务表格语义、行列定位和领域字段能力有限 | 广格式兜底、文件类型检测、基础文本提取 |
| MarkItDown | Python 集成方便；输出 Markdown，适合 LLM；能保留标题、列表、链接和部分表格结构 | 官方定位是面向文本分析而非高保真文档转换；复杂表格和精确定位仍有限 | 快速 LLM 输入、原型、低风险格式和兜底转换 |

Apache Tika 官方列出了 Office、PDF、HTML、压缩包等广泛格式，并强调其主要能力是格式检测、文本和元数据提取。MarkItDown 官方说明其目标是将文件转换为适合 LLM 和文本分析的 Markdown，而不是面向人工使用的高保真转换。

## 2.4 推荐实现

定义统一接口：

```python
class DocumentParser(Protocol):
    def supports(self, mime_type: str, extension: str) -> bool: ...
    def parse(self, content: bytes, metadata: dict) -> ParsedDocument: ...
```

字段解释：

| 字段名 | 中文含义 | 存在原因 |
|---|---|---|
| `supports` | 当前解析器是否支持该文件 | 让调度器自动选择适配器 |
| `mime_type` | 文件真实媒体类型 | 处理扩展名错误或缺失的文件 |
| `ParsedDocument` | 统一解析结果 | 下游证据构建不依赖具体解析库 |
| `parser_name` | 实际使用的解析器 | 便于诊断不同解析器效果 |
| `fallback_used` | 是否使用兜底解析器 | 评估专用解析失败率 |

解析顺序建议：

```text
PDF    -> 专用 PDF 文本/表格解析 -> OCR -> MarkItDown/Tika 兜底
Excel  -> openpyxl/xlrd 专用解析 -> MarkItDown/Tika 兜底
Word   -> python-docx/LibreOffice -> MarkItDown/Tika 兜底
未知格式 -> Tika 或 MarkItDown
```

## 2.5 当前项目为什么保留专用解析器

当前附件解析器会识别企业、产品、注册证、医保编码、价格和采购量等列，还会统计表格总行数、字段非空数量、唯一值、样例值和数值范围。这些领域能力是通用文本转换工具不会自动提供的。

因此最合理的说法不是“自研一定比开源好”，而是：

> 我使用通用框架降低格式覆盖成本，但对 PDF 表格、Excel 清单和 Word 结构等关键路径保留领域适配器。通用解析器负责广度，专用解析器负责业务准确性和来源定位，两者通过统一 ParsedDocument 接口接入证据构建链路。

## 2.6 如何客观选择

建立真实附件测试集，按格式和复杂度分层比较：

- 文本覆盖率；
- 表格结构恢复准确率；
- 关键字段抽取 Precision、Recall、F1；
- 页码和行列定位准确率；
- 解析耗时和内存；
- 扫描件 OCR 成功率；
- 异常文件隔离能力。

不要只比较“能不能读出文字”。

---

# 3. 重要信息未进入证据包时如何发现和兜底

## 3.1 问题

如果一个重要信息在 Evidence Pack 构建阶段没有被提取，生成模型还能否覆盖？当前报告指标都以 Evidence Pack 为参照，如果证据包本身有问题，如何发现和兜底？

## 3.2 结论

如果重要信息既没有进入完整 Evidence Pack，也没有进入模型输入，生成模型不应该可靠地覆盖它。模型偶然根据常识或训练数据写出类似内容，也不能视为正确，因为它没有当前公告的证据支撑。

必须增加一组上游评测：

> **Raw Source → Parsed Asset → Evidence Pack → Compact Payload → Report**

每个箭头都要有独立的覆盖率和质量门，不能只评估最后的报告。

## 3.3 四层质量门

### 第一层：原始文件解析质量

指标：

| 指标 | 含义 |
|---|---|
| `attachment_parse_success_rate` | 成功解析附件数 ÷ 总附件数 |
| `page_coverage_rate` | 已解析页数 ÷ 总页数 |
| `table_scan_coverage_rate` | 已扫描表格行数 ÷ 总行数 |
| `ocr_low_confidence_page_count` | OCR 可信度不足的页数 |
| `parse_warning_count` | 解析告警数量 |

### 第二层：Source-to-Evidence 质量

建立人工标注的 Golden Fact 测试集：对代表性公告标注必须提取的时间、价格、采购量、产品、企业、注册证、执行要求和中选规则。

指标：

```text
关键事实抽取 Precision
= 正确抽取的关键事实数 / 系统抽取的关键事实总数

关键事实抽取 Recall
= 正确抽取的关键事实数 / 原始资料中应抽取的关键事实总数

F1
= Precision 与 Recall 的调和平均
```

字段解释：

| 字段名 | 中文含义 | 存在原因 |
|---|---|---|
| `golden_fact` | 人工确认的标准事实 | 判断证据构建是否漏掉原文信息 |
| `source_to_evidence_recall` | 原始资料到证据包的事实召回率 | 发现上游漏提取问题 |
| `locator_accuracy` | 证据来源位置正确率 | 防止内容正确但引用页码错误 |
| `fact_conflict_count` | 多来源冲突事实数量 | 阻止系统自动选择一个矛盾值 |

### 第三层：压缩保留质量

- Mandatory Evidence 保留率；
- A/B 依赖闭包保留率；
- 压缩前后关键字段数量差异；
- 被省略内容类型和风险等级；
- Compact Payload 对 Golden Facts 的覆盖率。

### 第四层：报告质量

只有前三层通过后，Faithfulness、Coverage、Relevancy 等报告指标才具有可信意义。

## 3.4 证据缺口恢复流程

```text
构建 Evidence Pack
       ↓
按公告类型执行必需主题检查
       ↓
发现价格/时间/采购量等主题缺失
       ↓
回到完整解析产物进行定向检索
       ↓
仍未找到：调用受约束的 LLM Fact Extractor 检查原文
       ↓
找到：生成新的 A/B 证据并记录来源
未找到：标记 needs_manual_review
```

`needs_manual_review` 表示需要人工复核，不能自动交付。

## 3.5 生成阶段可否直接回查原文

可以在目标 LangGraph 中增加 `retrieve_missing_evidence` 工具节点，但必须满足：

1. 只从当前任务允许访问的原始文档或内部知识库检索；
2. 工具返回原文摘录和来源定位；
3. 新事实先写回 Evidence Pack，再进入报告；
4. 不允许模型直接将工具返回的无来源文本写入正式报告；
5. 更新 Evidence Pack 版本和哈希，重新执行质检。

## 3.6 面试表述

> 只用 Faithfulness 对报告和 Evidence Pack 做比较，会掩盖证据包自身的漏提取问题。因此我把质量评估拆成 Source-to-Evidence 和 Evidence-to-Report 两层。前者使用人工标注的 Golden Facts 计算关键事实抽取 Precision、Recall、F1 和来源定位准确率；后者使用 DeepEval 检查 Faithfulness、Coverage 和 Relevancy。当公告类型规则发现价格、时间或采购量等必需主题缺失时，Agent 会回到完整解析资产做定向检索并补建证据；仍无法找到时转人工复核，而不是让生成模型猜测。

---

# 4. 现有证据包构建方法是否合理

## 4.1 结论

当前方法作为受控领域的工程原型是合理的，而且已经优于“把全部附件文本拼接后直接交给 LLM”的简单做法。但它还不是完整的生产级证据系统。

## 4.2 合理之处

1. 区分主材料和辅助材料，减少历史或背景资料污染当前事实。
2. 针对 PDF、Word、Excel 等格式分别处理，能够保留更多业务结构。
3. 将完整 Evidence Pack 与发送给 Dify 的 Compact Payload 分开。
4. 使用 A/B/C 证据等级和来源引用。
5. 对价格、采购量、企业、产品和表格等信息设置更高优先级。
6. 压缩时保留 Mandatory Evidence，并记录省略内容和风险。
7. 附件解析失败会形成告警，不会静默当作成功。
8. 报告生成后还有 Claim-Evidence 检查、质量门和有限修复。

## 4.3 主要不足

1. 用户选择后的附件深解析可能增加等待时间。
2. 基于关键词和规则的关键事实抽取存在 Recall 上限。
3. PDF 页数、字符数和大表格扫描行数存在上限。
4. 当前主链路长期主要保存解析 JSON，未将全部原始附件作为不可变资产长期存入 MinIO。
5. 缺少 Source-to-Evidence 的系统评测集和指标。
6. 当前 DeepEval 主要评估生成结果，并不能证明证据包完整。
7. 当前 DeepEval Projection 中 `expected_facts` 和 `attachment_expectation` 尚未完全生成，使两项指标经常不适用。
8. Dify 变量限制和模型上下文限制混在一起，需要分别治理。

## 4.4 推荐目标结构

```text
Raw Document Asset
    ↓ 文档级解析，可缓存复用
ParsedDocument
    ↓ 规则 + 领域事实提取
Full Evidence Pack
    ↓ 任务目标和输入预算
Compact Payload
    ↓ 生成
ReportIR / Markdown
```

其中：

- 原始附件和完整解析产物存 MinIO；
- MySQL 存元数据、版本、状态和索引；
- Redis 存缓存、锁和队列；
- Evidence Pack 是不可变版本化快照；
- Compact Payload 是可以重新生成的派生视图；
- 报告所有事实必须回溯到 A/B 证据。

## 4.5 面试评价

> 现有方案方向正确，尤其是完整证据与模型输入分离、主辅材料隔离、表格领域解析和压缩保真机制。它的主要短板不在于“有没有证据包”，而在于上游解析召回率缺少量化、原始资产持久化不足，以及任务选择后深处理带来的时延。我进一步将其升级为文档资产层、任务证据层和生成视图层三层架构，并增加 Source-to-Evidence 评测和证据缺口恢复。

---

# 5. LangChain、LangGraph 和 HTTP 调用的上下文限制

## 5.1 结论

LangChain、LangGraph 和普通 HTTP 客户端本身不会替模型决定上下文窗口，也通常不会自动安全截断输入。

- **LangGraph** 负责节点、状态和路由，不定义模型上下文上限。
- **LangChain** 提供统一模型接口，具体能力和限制来自模型供应商。
- **HTTP** 只是传输 JSON；如果输入超过模型或服务限制，通常由服务端拒绝、截断或返回 `finish_reason=length`。

## 5.2 DeepSeek V4 当前官方限制

当前官方文档显示：

- `deepseek-v4-pro` 和 `deepseek-v4-flash` 的上下文长度均为 1M tokens；
- 最大输出长度为 384K tokens；
- `max_tokens` 限制本次生成的最大输出 token 数；
- 输入 token 与输出 token 总和受上下文窗口限制。

`max_tokens` 不是输入限制，也不是总上下文限制。

## 5.3 需要显式传什么

API 通常需要或允许传：

| 字段名 | 中文含义 | 注意事项 |
|---|---|---|
| `model` | 模型标识 | 本项目为 `deepseek-v4-pro` 或 `deepseek-v4-flash` |
| `messages` | 系统、用户、助手和工具消息 | 全部都会占用上下文 |
| `max_tokens` | 最大输出 token 数 | 不限制输入长度 |
| `thinking.type` | 是否启用思考模式 | 思考 token 也会影响延迟和用量 |
| `response_format` | 输出格式 | 可要求 JSON，但仍需 Schema 校验 |

应用层还应自行维护：

| 字段名 | 中文含义 |
|---|---|
| `model_context_tokens` | 模型官方上下文窗口 |
| `estimated_input_tokens` | 请求输入 token 估算值 |
| `reserved_output_tokens` | 为最终报告预留的输出 token |
| `safety_margin_tokens` | 为工具消息、格式差异和估算误差保留的余量 |
| `application_input_cap` | 项目主动设置的最大输入预算 |

计算方式：

```text
可用输入预算
= min(
    应用主动上限,
    模型上下文 - 输出预留 - 安全余量,
    Dify/网关/变量平台限制
  )
```

## 5.4 为什么模型支持 1M 也不能全塞

1. 上下文越大，延迟和费用越高；
2. 名义支持 1M 不代表长上下文每个位置都能稳定利用；
3. 低价值内容会稀释关键证据；
4. Dify 变量、反向代理和服务网关可能有更小限制；
5. 过长输出不适合 2000～5000 字的业务报告；
6. 质检和修订还需要额外上下文预算。

当前项目中的 160K、220K、240K 等数值主要是字符级 Dify 输入预算，不等同于 DeepSeek 的 1M token 上下文。

## 5.5 面试表述

> LangGraph 不会自动管理模型上下文，它只管理工作流状态；LangChain 也只是统一模型调用接口。真正的上下文窗口由模型和服务商决定。项目中我不会直接使用模型宣称的 1M 上限，而是根据模型窗口、输出预留、Dify 变量上限和安全余量计算应用级预算。`max_tokens` 只控制输出，因此输入侧还要在调用前估算 token，并采用证据排序、分段处理和分阶段生成。

---

# 6. 完整数据流第 0～11 步分别存在哪里

## 6.1 存储职责

目标架构中：

- **MySQL**：业务事实来源，保存任务、状态、元数据、版本、索引和评测汇总。
- **MinIO**：保存原始附件、解析产物、完整证据包、压缩快照和报告文件等大对象。
- **Redis**：保存 Celery 队列、短期缓存、分布式锁和临时进度，不作为永久业务事实来源。
- **LangGraph State**：保存当前运行所需的小型状态和对象引用，不直接塞入大型 PDF 或完整 Evidence Pack。
- **Celery 消息**：只传 `run_id`、`pack_id` 等标识，不传大型正文和附件。

## 6.2 第 0～11 步存储表

| 步骤 | 数据 | 当前实现 | 目标存储 | 原因 |
|---:|---|---|---|---|
| 0 | 用户选择主材料、辅助材料和参数 | HTTP 请求，部分内容进入 Evidence Pack | MySQL `analysis_task`、`task_material` | 保存任务输入快照，支持审计和重试 |
| 1 | 公告正文和附件元数据 | MySQL 公告表、附件表 | MySQL 保留；原始附件同步到 MinIO | MySQL 负责查询，MinIO 负责文件 |
| 2 | 清洗后的正文和文档资产 | 进程内对象，随后写入 Evidence Pack JSON | MinIO `parsed/{source_hash}/document.json`；MySQL `document_asset` | 解析结果可跨任务复用 |
| 3 | PDF/Word/Excel 解析结果 | Evidence Pack 中的附件摘要；解析缓存目录 | MinIO 文本、表格 JSON、OCR 结果；MySQL 解析状态；Redis 热缓存 | 大结果持久化，状态可查询 |
| 4 | 完整 Evidence Pack | 本地挂载目录 JSON | MinIO 不可变 JSON；MySQL 保存 `pack_id`、版本、哈希、对象路径 | 完整事实底座必须可追溯 |
| 5 | Compact Payload | 运行时生成并可缓存 | Redis 短期缓存；MinIO 保存审计快照；MySQL 保存策略、字符/token 数和哈希 | 可重建，但要支持复盘 |
| 6 | 生成任务和 Agent State | Analysis Run JSON、线程后台任务、Dify 运行状态 | MySQL 任务状态；LangGraph Checkpoint；Celery 消息仅传 ID | 支持异步、恢复和幂等 |
| 7 | 初次生成的 ReportIR/Markdown | Analysis Run JSON | MinIO 版本化草稿；MySQL `report_version` 元数据 | 报告较大且需要版本管理 |
| 8 | QA/质量门结果 | Analysis Run JSON 内嵌 | MySQL `quality_result`；较大明细存 MinIO | 支持统计、过滤和回归分析 |
| 9 | 修复后的报告和修复动作 | Analysis Run 与 Revision 记录 | MinIO 新报告版本；MySQL `repair_attempt`、`revision` | 不覆盖旧版本，便于对比 |
| 10 | 最终 Analysis Run 和 DOCX | 运行 JSON、报告目录 DOCX | MySQL 为最终状态源；MinIO 保存正式 ReportIR、Markdown、DOCX | 数据库状态与文件解耦 |
| 11 | DeepEval Projection、指标和原因 | 独立 Advisory 文件存储 | Redis/Celery 评测队列；MinIO 保存脱敏输入和详细结果；MySQL 保存指标汇总 | 不阻塞主链路，并支持版本对比 |

## 6.3 关键字段解释

| 字段名 | 中文含义 | 推荐存储 |
|---|---|---|
| `run_id` | 一次分析运行 ID | MySQL、State、日志和对象路径 |
| `pack_id` | 完整 Evidence Pack ID | MySQL 和 MinIO |
| `object_key` | MinIO 中的对象路径 | MySQL 元数据和 State |
| `content_sha256` | 内容哈希 | MySQL，校验 MinIO 对象 |
| `report_version` | 报告版本号 | MySQL |
| `checkpoint_version` | Agent 状态快照版本 | LangGraph Checkpoint/MySQL |
| `idempotency_key` | 防止重复创建任务的业务键 | MySQL 唯一索引、Redis 锁 |
| `ttl` | Redis 数据过期时间 | Redis |

## 6.4 一致性原则

1. MySQL 是任务状态的权威来源；
2. MinIO 对象必须带哈希、大小和版本；
3. Redis 丢失不能导致永久业务数据丢失；
4. State 只存引用和必要中间结果；
5. 使用 Transactional Outbox 解决“数据库提交成功但 Celery 消息未发送”的问题；
6. 使用补偿任务扫描孤儿对象、缺失对象和长期停留的中间状态。

---

# 7. DeepEval 指标是否足够以及报告应如何全面评估

## 7.1 问题

当前只有 Faithfulness、Critical Coverage、Attachment Consistency、Answer Relevancy 四项 DeepEval 指标，是否太少？是否应该增加 Precision、Recall 等指标？其他报告生成系统通常如何评估？

## 7.2 结论

四项指标适合作为最小 Advisory 版本，但不足以完整描述一个 2000～5000 字、多章节、可能包含表格的医疗采购分析报告。

同时，Precision 和 Recall 要明确评估对象：

- **报告事实 Precision**：报告写出的事实中有多少得到证据支持；
- **报告事实 Recall**：原始资料中的关键事实有多少被报告覆盖；
- **RAG Contextual Precision**：召回结果中相关内容是否排在前面；
- **RAG Contextual Recall**：理想回答需要的信息是否都被召回。

这些不是同一个指标。

## 7.3 当前四项指标的定位

| 指标 | 评估对象 | 主要问题 |
|---|---|---|
| `claim_faithfulness_v1` | 报告 Claim 对证据 | 是否出现证据不支持的断言 |
| `critical_coverage_v1` | 报告对 Expected Facts | 是否遗漏关键事实 |
| `attachment_state_consistency_v1` | 报告对附件状态 | 是否误称附件存在、成功解析或包含某内容 |
| `answer_relevancy_v1` | 报告对任务目标 | 是否跑题或加入大量无关内容 |

## 7.4 推荐的完整评测矩阵

### A. 原始资料与解析层

- 附件解析成功率；
- 页覆盖率；
- 表格行扫描覆盖率；
- OCR 低置信度率；
- 文档类型识别准确率。

### B. Evidence Pack 构建层

- 关键事实抽取 Precision；
- 关键事实抽取 Recall；
- 关键事实 F1；
- 来源定位准确率；
- Mandatory Evidence 保留率；
- 冲突事实检测 Recall；
- C 级内容被错误当作事实的比例。

### C. RAG/检索层

DeepEval 已提供：

- Contextual Precision：相关 Chunk 是否靠前；
- Contextual Recall：理想输出需要的事实是否被召回；
- Contextual Relevancy：召回上下文中有多少内容与问题相关。

还可以统计：

- Top-K Hit Rate；
- MRR；
- 去重率；
- 过期资料召回率；
- 权限过滤错误率。

### D. 报告生成层

| 指标 | 推荐含义 |
|---|---|
| Factual Precision / Claim Support Rate | 有 A/B 证据支持的事实断言数 ÷ 全部事实断言数 |
| Critical Fact Recall / Coverage | 已覆盖关键事实数 ÷ 应覆盖关键事实数 |
| Numerical Exact Match | 价格、数量、比例、日期等精确值正确率 |
| Faithfulness | 结论是否能由输入证据推出 |
| Relevancy | 是否围绕本次公告和用户目标 |
| Structure Completeness | 必需章节和主题是否齐全 |
| Coherence | 章节与段落逻辑是否连贯 |
| Fluency | 语言是否通顺、专业、无明显格式问题 |
| Non-redundancy | 是否存在重复段落或反复表述 |
| Analysis Usefulness | 是否提供基于事实的风险、影响和企业注意事项 |
| Risk Calibration | 高风险/低置信度标记是否与真实错误概率匹配 |

SummEval 的人工评价维度包括 Coherence、Consistency、Fluency 和 Relevance；RAGAS 将检索质量、上下文利用和生成质量拆开；QAFactEval 和 QAGS 说明可以通过从报告和原文生成问题、比较答案来检测事实一致性，并且 QA 与蕴含类指标可以提供互补信号。

### E. 用户反馈修订层

- Feedback Instruction Adherence：是否满足用户修改要求；
- Factual Regression Rate：修订是否破坏原本正确的事实；
- Evidence Preservation Rate：未要求修改的证据引用是否仍然保留；
- Edit Locality：是否只修改相关章节；
- Revision Pass Rate：一次修订后质量门通过率；
- 用户再次修改率。

### F. 系统与业务层

- P50/P95 总耗时；
- 任务成功率、超时率和重试率；
- 单任务 Token 和成本；
- 自动交付率；
- 人工复核率；
- 业务人员平均修改时间；
- 报告一次验收率；
- 人工分析耗时节省比例。

## 7.5 推荐最小指标集

为了避免指标过多但无法维护，第一阶段建议至少保留 12 项：

1. 附件解析成功率；
2. Source-to-Evidence 关键事实 Recall；
3. 关键事实抽取 Precision；
4. Mandatory Evidence 保留率；
5. Contextual Precision；
6. Contextual Recall；
7. Claim Support Rate/Factual Precision；
8. Critical Fact Coverage/Factual Recall；
9. Numerical Exact Match；
10. Structure Completeness；
11. Coherence/Fluency 综合 GEval；
12. 用户反馈修订一次通过率。

## 7.6 Judge 可靠性

本项目计划使用 GLM-5 和 DeepSeek V4 Flash 作为 Judge，还应评估：

- 双 Judge 评分一致率；
- 与人工标注的一致率；
- 同一输入重复评测的方差；
- Judge 对不同模型输出是否存在偏好；
- 高风险错误的漏检率。

不能把 LLM Judge 的分数当作绝对真值。

## 7.7 面试表述

> 当前四项指标是最小可用版本，只覆盖了证据忠实度、关键事实覆盖、附件一致性和任务相关性。我进一步将评测拆成解析、证据构建、检索、生成、修订和业务六层。Precision 和 Recall 也分别定义：Claim Support Rate 衡量报告事实精度，Critical Fact Coverage 衡量事实召回；如果引入内部 RAG，再增加 Contextual Precision、Contextual Recall 和 Contextual Relevancy。长报告还需要数值精确匹配、章节完整性、连贯性、流畅性和用户反馈遵循度。

---

# 8. 用户反馈修订和公司内部资料 RAG 如何设计

## 8.1 当前仓库已有能力

当前项目支持：

```text
POST /analysis/runs/{run_id}/revise
```

用户可以提交文字反馈，系统读取：

- 当前报告；
- 原 Evidence Pack；
- 用户反馈；
- 是否突出分析；
- 可选的长期报告记忆；

然后调用修订工作流生成新版本并再次执行质量检查。

主要字段：

| 字段名 | 中文含义 | 作用 |
|---|---|---|
| `feedback` | 用户输入的修改要求 | 指导本次修订 |
| `mode` | 修订模式 | 区分普通反馈、专项修改等场景 |
| `analysis_highlight` | 是否突出分析结论 | 控制报告表达侧重 |
| `current_report` | 当前版本报告 | 作为修改基础，不从零生成 |
| `evidence_pack` | 原任务证据包 | 防止修订引入无依据事实 |
| `report_memory` | 受控长期记忆内容 | 提供写作规则或已批准经验，不等于向量检索 |
| `revision_id` | 修订版本 ID | 关联修订前后报告 |

此前完整数据流没有单列这一分支，是文档遗漏，应修正为两条链路：

```text
初次生成链路
Evidence Pack -> Generate -> QA -> Repair -> Final

反馈修订链路
Current Report + Feedback + Evidence Pack
    -> 判断是否需要扩展证据
    -> Revision Generate
    -> QA/Regression Check
    -> New Report Version
```

## 8.2 为什么现有长期记忆不是完整 RAG

现有 `report_memory` 主要是：

- 受控写作规范；
- 被选择的结构化记忆条目；
- 有字符上限的上下文拼接。

它没有完整体现：

- 对公司内部文档切片和向量化；
- 根据用户反馈动态检索；
- Top-K 召回和重排；
- 每个 Chunk 的权限、时间和来源过滤；
- Contextual Precision/Recall 评测。

因此简历中不能把现有 Memory 直接称为“公司知识库 RAG”。

## 8.3 反馈分类

LangGraph 中增加 `classify_revision_intent` 节点：

### 类型一：不需要新知识

例如：

- 缩短报告；
- 调整语气；
- 增加表格；
- 对某一章节改写；
- 突出企业注意事项。

只使用当前报告和原 Evidence Pack 修订。

### 类型二：需要扩展当前公告证据

例如：

- “报告内容太少，请检查附件是否还有未覆盖规则”；
- “补充采购量和价格联动细节”。

回到当前公告的完整解析资产执行定向检索和证据补建。

### 类型三：需要公司内部资料 RAG

例如：

- “增加与上一轮集采项目的串联分析”；
- “结合公司内部历史报告分析企业影响”；
- “对比本项目和某省历史政策差异”。

调用内部资料检索节点。

## 8.4 内部 RAG 推荐链路

```text
用户反馈
  ↓
意图分类与查询改写
  ↓
权限、项目、地区、时间过滤
  ↓
混合检索（关键词 + 向量）
  ↓
Rerank
  ↓
构建 revision_evidence_pack
  ↓
事实层与分析层分离
  ↓
修订报告
  ↓
DeepEval + 回归检查
```

可以增加独立检索层，例如 Elasticsearch/OpenSearch：

- MySQL 保存文档元数据、ACL、项目和版本；
- MinIO 保存内部原文件和解析资产；
- Elasticsearch/OpenSearch 保存关键词索引和向量索引；
- Redis 保存查询缓存；
- LangGraph 负责是否调用检索工具和如何回到修订节点。

## 8.5 `revision_evidence_pack` 数据结构

```json
{
  "current_notice_evidence": [],
  "internal_retrieved_evidence": [],
  "user_feedback": "增加与上一轮集采的串联分析",
  "retrieval_query": "当前项目与上一轮集采在产品范围、采购量和价格规则上的差异",
  "retrieval_filters": {
    "region": "某省",
    "document_type": "approved_internal_report",
    "time_before": "当前公告发布时间",
    "permission_scope": "current_user"
  },
  "retrieval_metrics": {
    "top_k": 8,
    "reranked_k": 4
  }
}
```

字段解释：

| 字段名 | 中文含义 | 作用 |
|---|---|---|
| `current_notice_evidence` | 当前公告事实证据 | 正式公告事实的唯一主要来源 |
| `internal_retrieved_evidence` | 公司内部召回资料 | 只能用于历史对比、经验和串联分析 |
| `retrieval_query` | 经过改写的检索问题 | 比直接使用用户原话更适合召回 |
| `retrieval_filters` | 权限和业务过滤条件 | 防止跨项目、跨用户或过期资料泄漏 |
| `top_k` | 初次召回数量 | 控制召回广度 |
| `reranked_k` | 重排后进入模型的数量 | 控制上下文噪声 |

## 8.6 事实边界

内部资料不能自动成为当前公告事实。报告中要显式区分：

- “根据当前公告”：当前 Evidence Pack 支撑的事实；
- “结合历史项目”：内部 RAG 提供的历史事实；
- “据此判断”：基于两者形成的分析结论。

内部文档的价格、日期、产品范围和采购量不能覆盖当前公告字段。

## 8.7 修订评测

增加：

- 用户反馈遵循度；
- 内部检索 Contextual Precision/Recall/Relevancy；
- 引用正确率；
- 当前公告事实回归率；
- 历史事实误写为当前事实的比例；
- 权限过滤错误率；
- 过期资料使用率；
- 修订后结构完整性；
- 修订一次通过率。

## 8.8 面试表述

> 项目原本已经支持用户通过文字反馈修订报告，修订时会同时传入当前报告和原 Evidence Pack，保证修改不能脱离证据。我进一步把反馈分为样式修改、当前公告证据扩展和内部知识扩展三类。只有第三类才触发公司知识库 RAG，通过权限和时间过滤、混合检索与重排构建独立的 revision_evidence_pack。当前公告证据与历史内部资料在数据结构和报告措辞中严格分层，并在修订后检查用户指令遵循度、事实回归、引用正确性和检索 Precision/Recall。

---

# 参考资料

1. Apache Tika Supported Document Formats：`https://tika.apache.org/3.0.0/formats.html`
2. Microsoft MarkItDown：`https://github.com/microsoft/markitdown`
3. LangChain Providers and Models：`https://docs.langchain.com/oss/python/concepts/providers-and-models`
4. LangGraph Context Overview：`https://docs.langchain.com/oss/python/concepts/context`
5. LangGraph Persistence：`https://docs.langchain.com/oss/python/langgraph/persistence`
6. DeepSeek Models and Pricing：`https://api-docs.deepseek.com/zh-cn/quick_start/pricing`
7. DeepSeek Chat Completion API：`https://api-docs.deepseek.com/zh-cn/api/create-chat-completion/`
8. DeepEval Contextual Precision：`https://deepeval.com/docs/metrics-contextual-precision`
9. DeepEval Contextual Recall：`https://deepeval.com/docs/metrics-contextual-recall`
10. DeepEval Contextual Relevancy：`https://deepeval.com/docs/metrics-contextual-relevancy`
11. RAGAS: Automated Evaluation of Retrieval Augmented Generation：`https://arxiv.org/abs/2309.15217`
12. SummEval：`https://aclanthology.org/2021.tacl-1.24/`
13. QAFactEval：`https://aclanthology.org/2022.naacl-main.187/`
14. QAGS：`https://aclanthology.org/2020.acl-main.450/`
