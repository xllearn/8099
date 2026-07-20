# 医疗器械公告智能分析与报告生成系统

> **Medical Notice Analyzer**  
> 面向医疗器械公告的证据约束 AI 分析与报告交付系统。

本项目从数据库选取公告主材料和辅助材料，解析网页正文及多格式附件，构建可追溯的 Evidence Pack，再由后端代理 Dify Workflow 生成分析报告。模型输出还会经过结构修复、质量检查、正文安全校验和受控发布，降低资料遗漏、事实混用与无依据生成的风险。

项目重点不只是“调用大模型生成文字”，而是围绕 **材料选择 → 文档理解 → 证据组织 → LLM 生成 → QA/Repair → 可靠交付** 建立完整流程。

## 项目解决什么问题

医疗器械采购、挂网、价格治理和集采接续类公告通常具有以下特点：

- 正文长、规则多，重要信息散落在多个章节；
- 同一项目可能同时包含公告正文、PDF、Word、Excel、压缩包等材料；
- 主材料与历史或辅助材料的事实边界容易混淆；
- 直接把原文交给 LLM，容易出现规则遗漏、引用错位和无依据补充；
- 长时间生成任务需要进度、诊断、历史版本和失败恢复能力；
- 最终报告需要经过质量门，不能把调试信息、模型推理痕迹或不完整内容直接交付。

### 输入

- 从公告数据库中选择的主材料；
- 用于背景、比较或补充说明的辅助材料；
- 与材料关联的 PDF、DOC/DOCX、XLS/XLSX/XLSM、CSV、TXT、HTML 和 ZIP 附件。

### 输出

- 经过 Schema 校验的完整 Evidence Pack；
- 面向 Dify 输入限制构建的紧凑证据包；
- Markdown 分析报告及结构化 ReportIR；
- 质量状态、问题列表、运行进度和分层诊断；
- 用户反馈修订后的报告版本；
- 满足发布开关和正文安全条件时生成的 DOCX 文件。

## 核心能力

| 能力 | 说明 |
|---|---|
| 公告材料检索与选择 | 检索数据库公告，区分主材料与辅助材料，并在生成前预览选择结果 |
| 多格式文档理解 | 解析网页正文及常见办公文档、表格、PDF 和压缩包；对扫描件提供可选 OCR 路径 |
| Evidence Pack | 将来源、材料角色、正文、附件摘要、结构化证据和告警统一组织为可校验的数据包 |
| 分层证据压缩 | 后端保留完整证据，对发送给 Dify 的内容按重要性压缩，避免对原始 JSON 直接截断 |
| LLM 工作流代理 | 前端只调用 FastAPI，Dify 凭据和工作流调用保留在后端 |
| ReportIR 与报告渲染 | 使用结构化中间表示组织标题、段落、表格、重点规则和企业提示 |
| QA 与受控修复 | 对结构、无依据事实、禁用表达和正文安全问题执行检查与有限修复 |
| 运行状态与诊断 | 为每次分析生成 run_id，保存阶段、进度、警告、失败归因和质量结果 |
| 历史、修订与记忆 | 保存分析历史和报告版本，支持用户反馈修订及可选的范围化报告记忆 |
| 受控 Word 发布 | Word 导出默认关闭；只有功能开关开启且正文检查通过时才允许下载 |

## 技术选型

| 层次 | 技术 | 用途 |
|---|---|---|
| 语言与运行时 | Python 3.11 | 服务端开发、文档处理和容器运行 |
| Web API | FastAPI、Uvicorn、Pydantic | HTTP 接口、参数校验、响应模型和服务运行 |
| 数据源 | MySQL、PyMySQL | 读取公告、附件元数据及业务记录 |
| LLM 工作流 | Dify Workflow、HTTPX | 后端代理工作流调用、超时与失败处理 |
| HTML 处理 | BeautifulSoup | 公告正文清洗和链接解析 |
| PDF 与 OCR | pdfplumber、pypdf、Poppler、Tesseract | PDF 文本/表格解析和可选中英文 OCR |
| Word 处理 | python-docx、LibreOffice、antiword | DOC/DOCX 解析、旧格式转换和 DOCX 导出 |
| 表格处理 | openpyxl、xlrd | XLSX/XLSM/XLS 内容解析 |
| 前端 | 原生 HTML、CSS、JavaScript | 材料选择、运行详情、历史和记忆页面，无前端构建链 |
| 部署 | Docker、Docker Compose | 封装系统依赖、启动服务及受控发布 |

## 系统架构

~~~mermaid
flowchart LR
    U[用户与静态页面] --> API[FastAPI 编排层]
    API --> DB[(公告数据库)]
    API --> PARSE[正文与附件解析]
    PARSE --> EVIDENCE[完整 Evidence Pack]
    EVIDENCE --> COMPACT[分层压缩与紧凑证据]
    COMPACT --> DIFY[Dify Workflow]
    DIFY --> REPAIR[结构修复与事实约束]
    REPAIR --> GATE[Quality Gate 与正文安全检查]
    GATE --> REPORT[报告详情、修订与历史]
    GATE -->|发布开关与检查通过| DOCX[DOCX 受控导出]
    API --> STATE[(运行记录、检查点、记忆与缓存)]
~~~

公告源数据来自 MySQL；Evidence Pack、运行记录、检查点、历史、记忆和缓存目前主要保存到挂载的数据目录。检查点与自动恢复由功能开关控制，基础 Compose 配置默认关闭，强化运行时配置可显式开启。

## 端到端处理流程

1. **检索与选择材料**  
   用户在 <code>/records-ui</code> 检索公告，查看详情，并区分主材料和辅助材料。

2. **读取正文与附件信息**  
   后端按材料联合标识读取公告正文及附件元数据，避免只依赖单一文章编号。

3. **下载并解析附件**  
   系统在大小、超时和并发限制内处理附件，提取文本、表格和摘要；解析失败会形成告警，不会被静默当成成功结果。

4. **构建完整 Evidence Pack**  
   正文、附件摘要、材料角色、证据项、关键事实和告警被写入统一结构，并经过 Evidence Schema 校验。

5. **生成紧凑证据包**  
   完整包保留在后端，发送给 Dify 的版本优先保留主材料、关键事实和核心附件摘要，再压缩低优先级内容。

6. **创建分析运行**  
   <code>POST /analysis/run</code> 创建 run_id 和初始运行记录，然后由进程内后台线程执行生成任务。

7. **调用 Dify Workflow**  
   后端以 blocking 模式调用 Dify；前端不会接触 Dify API Key，也不直接连接模型工作流。

8. **修复与质量门**  
   模型结果进入结构修复、无依据事实检查、禁用表达检查、质量门和失败分层归因。无法安全交付的结果会保留问题状态并阻止发布，等待人工线下检查。

9. **展示、修订与记录历史**  
   报告详情页展示正文、进度、诊断和质量结果。用户可以提交反馈生成新版本，后端同时维护运行历史与版本记录。

10. **受控导出**  
    只有 <code>ENABLE_WORD_EXPORT=true</code> 且正式正文通过安全检查时，系统才生成并下载 DOCX。

启用强化运行时检查点后，主要阶段为：

<code>prepare → attachments → evidence → compact → provider → repair → quality_gate → word_publish</code>

系统会记录阶段输入/输出摘要和哈希，为符合条件的未完成运行生成恢复计划。

## AI 工程设计亮点

### 1. Evidence Grounding：先组织证据，再生成报告

系统不会把所有材料简单拼接成一个超长 Prompt。每条材料会保留来源、角色和结构化证据信息；主材料负责确定当前事实，辅助材料只用于背景、比较和补充，不能覆盖主材料。

这一边界让模型输入从“若干文档文本”变成可验证、可压缩、可诊断的 Evidence Pack，为后续的事实检查和失败定位提供基础。

### 2. 完整证据与模型输入分离

后端保存完整 Evidence Pack，Dify 默认读取紧凑版本。压缩过程按材料角色和证据优先级工作，而不是对 JSON 字符串直接截断，从而尽量保留：

- 主材料正文；
- 关键规则与数字；
- 核心附件摘要；
- 必须保留的证据项；
- 解析告警和来源标识。

完整包仍可用于后端诊断，模型输入大小与证据可追溯性因此可以同时管理。

### 3. ReportIR：稳定模型输出与交付格式

ReportIR 是报告的结构化中间表示，负责描述：

- 报告标题和文档信息；
- 引导段落与业务章节；
- 分类表格、备注和重点规则；
- 企业提示和内部元数据。

Markdown 展示与 DOCX 导出基于统一结构处理，减少直接解析自由文本带来的不确定性。系统也保留受约束的 Markdown 回退路径，但不会把原始模型回复直接写入正式文档。

### 4. QA、受控修复与 Fail-Closed 发布

质量链路关注的不只是语言通顺，还包括：

- 标题和结构是否完整；
- 表格列数是否一致；
- 是否残留变量、代码围栏或技术调试信息；
- 是否出现缺乏依据的断言；
- 历史材料是否被误写为当前事实；
- 正式正文是否包含推理、调试或不应交付的内容。

修复流水线只处理受控问题，并记录修复结果。发布开关关闭或正文安全检查失败时，Word 导出会拒绝执行；启用严格质量要求时，QA 未通过也会阻断导出。

### 5. 运行状态、分层诊断与恢复基础

每次生成都保留 run_id、pack_id、阶段状态、时间、警告、质量结果和 Provider 失败归因。前端可以查询进度和诊断，而不是只等待一个最终字符串。

仓库还实现了可选的阶段检查点、Schema 迁移和恢复入口。它们为进程重启后的恢复提供基础，但当前仍依赖文件持久化、单写者拓扑和功能开关，不能等同于分布式任务队列。

### 6. 历史、修订与范围化记忆

分析历史用于定位同一材料的不同运行结果，修订接口会保存报告版本和用户反馈。报告记忆与 memory items 需要显式选择或限定作用范围，避免把历史规则无条件注入当前公告。

## 功能状态与边界

| 能力 | 实现状态 | 说明 |
|---|---|---|
| 数据库材料检索与选择 | 基础能力 | 支持检索、筛选、详情、主辅材料选择和预览 |
| 多格式附件解析 | 基础能力 | 支持常见文档、表格、PDF、文本、HTML 和 ZIP；旧 DOC 依赖 LibreOffice |
| Evidence Pack 与紧凑证据 | 基础能力 | 完整包保存在后端，Dify 使用按规则构建的紧凑包 |
| Dify 报告生成 | 基础能力 | 当前唯一实际生成 Provider；后端使用 blocking 调用 |
| 报告详情与用户修订 | 基础能力 | 支持进度、诊断、正文、反馈修订和版本记录 |
| 分析历史后端 | 默认启用 | 提供列表、详情和同材料运行对比；基础配置中的历史 UI 默认关闭 |
| 报告记忆 | 已实现，可选使用 | 支持正式记忆、候选记忆和范围化 memory items |
| Word 导出 | 已实现，默认关闭 | <code>ENABLE_WORD_EXPORT=false</code> 时导出和下载 Fail-Closed |
| URL 分析旧流程 | 兼容能力，默认关闭 | <code>/analyze</code> 和 <code>/analyze_v2</code> 仅用于兼容或回滚验证 |
| 检查点与自动恢复 | 已实现，开关控制 | 基础 Compose 默认关闭，<code>docker-compose.s4-runtime.yml</code> 可开启 |
| 严格质量门与证据索引 | 已实现，开关控制 | 不同运行时可逐项开启报告规则、修复和严格交付门 |
| OCR 与并发解析 | 已实现，开关控制 | PDF OCR、图片表格 OCR 和并发解析均需配置开启 |
| LangGraph Agent 编排 | Roadmap | 当前阶段由 Python 代码手动编排，并非 LangGraph |
| Redis/Celery/Arq 队列 | Roadmap | 当前运行任务依赖应用进程内后台线程 |
| MinIO 与分布式文件存储 | Roadmap | 当前证据包、运行和报告主要使用本地挂载目录 |
| 多模型自动切换 | Roadmap | ReportGenerator 已抽象，但当前只实现 Dify |
| 历史公告 RAG | Roadmap | 当前由用户主动选择材料，尚未建立向量检索链路 |
| OpenTelemetry/Prometheus/Grafana | Roadmap | 当前有阶段计时和诊断，尚无标准化全链路观测栈 |

## 快速开始

### 1. 准备环境变量

在项目根目录新建 <code>.env</code>，将下列占位符替换为实际环境值。这里仅列出主链路所需的最小配置；不要把真实密码、API Key、Cookie 或 Token 提交到 Git。

~~~dotenv
PUBLIC_BASE_URL=http://127.0.0.1:8099
DB_HOST=your-db-host
DB_PORT=3306
DB_NAME=your-database
DB_USER=your-db-user
DB_PASSWORD=your-db-password
DIFY_BASE_URL=https://your-dify-host/v1
DIFY_WORKFLOW_API_KEY=your-dify-workflow-api-key
~~~

其他功能开关可先使用 Docker Compose 默认值，再按“功能状态与边界”逐项启用。

### 2. 使用 Docker Compose 启动

~~~powershell
docker compose up -d --build
~~~

Docker 镜像包含 LibreOffice、antiword、Poppler、Tesseract 和中文字体，适合验证完整文档处理链路。

### 3. 健康检查

~~~powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8099/health
~~~

<code>/health</code> 返回 HTTP 200 只说明 Web 服务已经启动；还应访问 <code>/records</code> 验证数据库连接，并使用一组测试材料验证完整的 Dify 生成链路。

### 4. 打开材料选择页

~~~text
http://127.0.0.1:8099/records-ui
~~~

非 Docker 本地开发也可以运行：

~~~powershell
.\start.ps1
~~~

但直接使用 Windows Python 环境时，DOC 转换、PDF 工具和 OCR 等系统依赖不一定完整。

## 核心 API

### 材料与选择

| 方法 | 路由 | 用途 |
|---|---|---|
| GET | <code>/records</code> | 检索与分页读取公告 |
| GET | <code>/records/{menu_code}/{articleid}</code> | 获取单条材料详情 |
| POST | <code>/analysis/selection/preview</code> | 预览主辅材料选择结果 |
| POST | <code>/analysis/prepare</code> | 构建并保存 Evidence Pack |

### Evidence Pack

| 方法 | 路由 | 用途 |
|---|---|---|
| GET | <code>/analysis/packs/{pack_id}</code> | 默认返回紧凑证据包；诊断时可请求完整包 |
| GET | <code>/analysis/packs/{pack_id}/summary</code> | 获取证据包摘要 |
| GET | <code>/analysis/packs/{pack_id}/diagnostics</code> | 查看解析与压缩诊断 |
| POST | <code>/analysis/cache/cleanup</code> | 清理受控缓存 |

### 分析运行

| 方法 | 路由 | 用途 |
|---|---|---|
| POST | <code>/analysis/run</code> | 创建报告生成运行 |
| GET | <code>/analysis/runs/{run_id}</code> | 查询状态和进度 |
| GET | <code>/analysis/runs/{run_id}/diagnostics</code> | 查询分层诊断 |
| GET | <code>/analysis/runs/{run_id}/report</code> | 获取安全处理后的报告 |
| POST | <code>/analysis/runs/{run_id}/revise</code> | 根据用户反馈生成新版本 |
| POST | <code>/analysis/runs/{run_id}/recover</code> | 在满足检查点条件时请求恢复 |
| GET | <code>/analysis/runs/{run_id}/download</code> | 在发布条件满足时下载 DOCX |

### 历史与记忆

| 方法 | 路由 | 用途 |
|---|---|---|
| GET | <code>/analysis/history</code> | 分页检索分析历史 |
| GET | <code>/analysis/history/{run_id}</code> | 获取历史运行详情 |
| GET | <code>/analysis/history/compare</code> | 对比同一材料的两个运行 |
| GET/PUT | <code>/memory/report</code> | 读取或更新正式报告记忆 |
| GET/PUT | <code>/memory/candidates</code> | 管理候选记忆 |
| GET | <code>/memory/items</code> | 查询范围化记忆项 |
| PUT | <code>/memory/items/{item_id}</code> | 新增或更新记忆项 |

### 报告工具

| 方法 | 路由 | 用途 |
|---|---|---|
| POST | <code>/report/render</code> | 解析并渲染 ReportIR/Markdown |
| POST | <code>/report/render_v2</code> | 新版渲染兼容入口 |
| POST | <code>/report/qa</code> | 解析 QA 输出并执行本地检查 |
| POST | <code>/report/export_checked</code> | 在质量和发布条件满足时导出 DOCX |

## 项目结构

~~~text
.
├─ app/
│  ├─ main.py                 # FastAPI 入口、API 与主流程编排
│  ├─ static/                 # 材料、运行、历史与记忆页面
│  ├─ generation/             # ReportGenerator 抽象与 Dify 实现
│  ├─ core/memory/            # 范围化记忆模型与存储
│  ├─ report_rules/           # 领域规则配置
│  ├─ attachment_parser.py    # 多格式附件解析
│  ├─ compact_pack.py         # Evidence Pack 分层压缩
│  ├─ evidence_schema.py      # 证据结构与校验
│  ├─ quality_gate.py         # 质量门
│  ├─ repair_pipeline.py      # 受控修复流水线
│  ├─ run_checkpoints.py      # 阶段检查点与恢复计划
│  └─ analysis_history.py     # 分析历史存储与查询
├─ prompts/                   # Dify 报告、修订与 QA Prompt
├─ scripts/                   # 评测、发布、回滚和维护脚本
├─ tests/                     # unittest 回归与模块测试
├─ docs/                      # 设计、计划、质量基线与 Runbook
├─ dify_workflow_pack_id_human_style.yml
├─ Dockerfile
├─ docker-compose.yml
├─ docker-compose.s4-runtime.yml
└─ requirements.txt
~~~

## 测试与验证

仓库使用 Python <code>unittest</code>，测试范围覆盖材料接口、附件解析、Evidence Schema、压缩策略、质量门、修复、历史、记忆、检查点、迁移、发布脚本和回归评估等模块。

运行测试：

~~~powershell
python -m unittest discover -s tests -v
~~~

或在容器中运行：

~~~powershell
docker exec medical-notice-analyzer python -m unittest discover -s tests -v
~~~

本文档只提供测试命令，不声明某个未在当前环境复现的通过数量或成功率。

## 可靠性与安全边界

- **凭据隔离**：数据库密码、Dify API Key、Cookie 和 Token 只从环境变量读取；前端不需要获得 Dify Key。
- **输入控制**：附件处理设置大小、超时、并发和缓存边界；临时文件在解析流程结束后清理。
- **证据边界**：辅助材料不能覆盖主材料事实，历史内容不能无条件成为当前公告依据。
- **诊断隔离**：解析失败、OCR、Evidence Pack 和 Dify 等技术信息进入诊断或告警，不进入正式报告正文。
- **受控发布**：URL 旧流程和 Word 发布默认关闭；关闭时接口明确拒绝执行，而不是产生不完整交付物。
- **正文安全**：报告发布前扫描变量残留、调试标记、推理字段、表格结构和不适合正式交付的表达。
- **恢复约束**：检查点会校验阶段状态和哈希；Provider 结果不确定或检查点损坏时拒绝盲目恢复。
- **受控发布脚本**：强化发布工具包含制品校验、备份、健康检查和回滚支持，部署目标由调用者显式提供。

## 已知限制

| 限制 | 当前影响 |
|---|---|
| 进程内任务 | 客户端可快速获得 run_id，但任务由应用进程内后台线程执行；服务重启、并发扩展和任务重试能力有限 |
| 单机文件持久化 | 运行、历史、检查点、记忆和报告主要写入挂载目录；历史存储要求单写者拓扑 |
| 文件格式与系统依赖 | 旧 DOC 依赖 LibreOffice，扫描 PDF 依赖 OCR；损坏、加密或超限文件可能产生解析告警 |
| 效果指标待实测 | 仓库具备离线评估和固定回归基础，但尚未在统一评测集上给出事实一致性、完整性、幻觉率、延迟和成本指标 |

## 后续优化方向

后续路线优先提升可评测的 Agent 编排、证据链、可靠任务执行和模型工程能力；表中的技术均为规划，不代表当前已经接入。

### P0：可评测的显式编排与可靠任务执行

| 方向 | 当前基础或限制 | 下一步交付与验证 |
|---|---|---|
| LangGraph 状态图 | 现有阶段由 Python 手动编排，并具备可选检查点 | 将 Document、Evidence、Report、QA、Repair、Export 建模为 State、Node 和 Edge；验证中断恢复与节点轨迹 |
| 系统化 LLM Evaluation | 已有离线评估与固定回归，但指标口径尚未统一 | 建设版本化评测集，持续计算 Faithfulness、Citation Accuracy、Completeness、Hallucination Rate 和 Repair Rate；结果由可复现运行产生 |
| Redis + Celery/Arq | 当前任务依赖应用进程内线程 | 拆分队列与 Worker，增加重试、超时、优先级、幂等和分布式锁；验证服务重启与并发任务 |
| Claim 级证据链 | Evidence Pack 已保留材料角色、来源和证据项 | 增加 Claim–Evidence–Citation–Confidence 关联、来源位置及确认状态；用固定样例验证逐条回溯 |

### P1：平台化存储、模型网关与知识检索

| 方向 | 当前基础或限制 | 下一步交付与验证 |
|---|---|---|
| 关系数据库 + MinIO | 公告源数据来自 MySQL，运行制品主要保存在本地目录 | 将运行元数据和版本迁入关系数据库，附件、Evidence Pack 与报告交给对象存储；验证生命周期与一致性 |
| 多 Provider Gateway | 已定义 ReportGenerator 抽象，当前只实现 Dify | 接入 OpenAI 兼容接口、DeepSeek 或本地模型，统一超时、重试、回退、Token、成本和延迟统计；在同一评测集比较 |
| 历史公告 RAG | 当前依赖用户主动选择辅助材料 | 建设候选检索与重排链路，由 Evidence 校验决定能否写入报告；验证召回内容不会覆盖主材料事实 |

### P2：可观测性、人工门控与治理

| 方向 | 当前基础或限制 | 下一步交付与验证 |
|---|---|---|
| OpenTelemetry + Prometheus + Grafana | 已有阶段计时和分层诊断，尚无标准化观测栈 | 采集队列、解析、模型、修复、Token、成本、质量门和失败类型指标，并关联 run_id Trace |
| Human-in-the-loop 与 Agent Trace | 当前阻断型问题只保留状态，等待线下检查 | 增加 WAIT_HUMAN、APPROVED、CONTINUE 状态和审核界面，记录节点摘要、耗时、结果与人工操作 |
| 企业安全治理 | 已有凭据隔离、输入边界和正文安全检查 | 增加权限控制、文件扫描、Prompt Injection 防护、敏感信息识别脱敏和细粒度审计 |
| Prompt Registry 与 A/B 测试 | Prompt 以仓库文件管理，缺少实验生命周期 | 增加版本、实验、发布和回滚机制，在同一评测集比较 Prompt、模型和编排策略 |

---

本项目当前定位是一个具备 **文档理解、证据组织、LLM 工作流、质量门、运行诊断和可靠交付基础** 的 AI 应用。Roadmap 的目标是在不推倒现有业务能力的前提下，将其进一步演进为可评测、可恢复、可观测、支持多 Provider 的 Agent 应用平台。
