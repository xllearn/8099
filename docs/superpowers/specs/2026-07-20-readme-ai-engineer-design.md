# README 中文重构设计

- 日期：2026-07-20
- 状态：已获用户批准
- 目标仓库：xllearn/8099
- 目标分支：codex/readme-ai-engineer-20260720
- 交付范围：仅重构根目录 README.md，不修改业务代码、配置或运行时行为

## 1. 背景

现有 README 以公司内网部署和接口调用说明为主，首屏无法快速说明项目解决的问题，也弱化了证据约束生成、结构化报告、质量门、修复、历史记录和失败恢复等 AI 工程能力。文档同时混合数据库选材主流程与默认关闭的 URL 兼容流程，并包含内网地址、应用标识、数据库名、个人绝对路径及公司专属内容，不适合作为公开求职作品集。

本次重构面向秋招 AI 应用开发、Agent 开发和 AI Engineer 岗位，以当前 main 分支代码和配置为事实来源，将 README 改为中文为主的项目说明文档。

## 2. 目标

1. 在首屏说明项目定位、业务问题、输入输出和核心价值。
2. 准确描述当前技术选型、主要功能与端到端流程。
3. 突出 Evidence Grounding、ReportIR、QA/Repair、质量门、运行状态和受控交付等 AI 工程能力。
4. 清楚区分默认启用、代码已实现但受开关控制、尚未实现的能力。
5. 提供可复现且脱敏的本地启动、API、测试和项目结构说明。
6. 将参考文档中的建议整理为按秋招收益排序的 Roadmap，不把规划写成现状。

## 3. 非目标

- 不新增 LangGraph、Redis、Celery、MinIO、向量数据库或监控组件。
- 不修改 FastAPI、Dify、附件解析、报告生成、质量门或部署逻辑。
- 不声称当前具备多模型自动切换、多 worker 横向扩展或生产级高可用。
- 不虚构准确率、幻觉率、测试通过数、延迟、Token 成本或线上可用性。
- 不保留公司内网操作手册；内部部署信息应由私有 Runbook 管理。

## 4. 目标读者与项目定位

主要读者是招聘方、面试官和希望理解项目架构的开发者。

README 首屏定位为：

> 面向医药器械公告的证据约束 AI 分析与报告交付系统：从数据库材料选择和多格式附件解析出发，构建可追溯 Evidence Pack，通过 Dify 驱动报告生成，并使用结构修复、质量门、运行诊断和受控发布保障交付质量。

定位中不使用“多 Agent 平台”“生产级高可用”等尚无充分实现证据的描述。

## 5. README 信息结构

根 README 按以下顺序组织：

1. 项目名称与一句话定位
2. 项目解决的问题、输入、输出和适用场景
3. 核心能力
4. 技术选型及选择理由
5. 系统架构
6. 端到端处理流程
7. AI 工程设计亮点
8. 功能状态与开关边界
9. 本地快速开始
10. 核心 API
11. 项目结构
12. 测试、可靠性与安全边界
13. 已知限制
14. 后续优化方向

不加入未经核验的截图、演示链接、徽章或效果数字。

## 6. 架构与流程表达

README 使用一张 Mermaid 流程图表达主要关系，保持单一主线：

数据库材料检索与主辅材料选择 → 正文和附件解析 → 完整 Evidence Pack → 分层压缩后的紧凑证据 → 后端代理 Dify → 结构化报告结果 → Repair 与 Quality Gate → 报告详情、修订、历史记录与受控 Word 导出。

图中组件分为：

- 交互层：原生 HTML、CSS、JavaScript 静态页面。
- API 与编排层：FastAPI、Pydantic、后台运行线程、运行状态与诊断。
- 证据层：MySQL 数据源、附件解析、Evidence Schema、完整包与紧凑包。
- 模型层：Dify Workflow；当前唯一实际报告生成 Provider。
- 质量与交付层：ReportIR、修复流水线、质量门、正文安全检查、DOCX 导出。
- 持久化层：本地卷中的 Evidence Pack、运行记录、检查点、历史、记忆和缓存。

## 7. 技术选型

技术选型表只描述仓库已有依赖：

- Python 3.11：主要开发语言和容器运行时。
- FastAPI、Uvicorn、Pydantic：HTTP API、请求校验和服务运行。
- MySQL、PyMySQL：公告及附件元数据来源。
- Dify Workflow、HTTPX：LLM 工作流执行与后端代理。
- BeautifulSoup：HTML 正文清洗。
- pdfplumber、pypdf、Poppler、Tesseract：PDF 文本、表格和可选 OCR。
- python-docx、LibreOffice、antiword：Word 解析、转换和导出。
- openpyxl、xlrd：Excel 解析。
- 原生 HTML、CSS、JavaScript：无需前端构建链的内部页面。
- Docker、Docker Compose：依赖封装、启动和受控发布。

LangGraph、Redis、Celery、MinIO、向量数据库、OpenTelemetry 等仅出现在 Roadmap。

## 8. 功能状态表达规则

### 8.1 当前基础流程

可以按已实现能力描述：

- 数据库公告检索、筛选、详情及主辅材料选择。
- 多格式附件下载、解析、临时文件清理和解析缓存。
- Evidence Pack 构建、Schema 校验、完整包保存和紧凑包输出。
- 通过后端代理调用 Dify，前端不持有模型工作流密钥。
- 创建 run_id、后台执行、进度查询、分层诊断和报告详情展示。
- 报告修订、版本记录、分析历史后端和可选报告记忆。
- ReportIR、Markdown 渲染、QA 解析、受控修复与正文安全检查。

### 8.2 已实现但受配置控制

必须明确说明默认值或运行时差异：

- Word 导出默认关闭。
- 旧 URL 分析流程默认关闭，仅保留兼容能力。
- 检查点和自动恢复在基础 Compose 中默认关闭，在 S4 运行时配置中启用。
- 历史后端默认启用，历史 UI 在基础配置中默认关闭。
- 严格质量门、证据索引、并发解析、紧凑缓存、OCR 等由功能开关控制。
- 文件格式支持代表具备解析路径，不代表任意文件都能成功解析；失败会返回告警或进入人工检查。

### 8.3 不得写成现有能力

- LangGraph 或标准化多 Agent 编排。
- Redis、Celery 或 Arq 队列。
- MinIO 对象存储。
- 多模型 Provider 自动切换和回退。
- 历史公告向量 RAG。
- Prometheus、Grafana、OpenTelemetry。
- Human-in-the-loop 状态节点和 Agent Trace UI。
- Prompt Registry、A/B 测试和已量化的模型评测指标。

## 9. 脱敏规则

README 不出现：

- 任何公司内网 IP、Dify 应用 UUID、内部数据库名或真实连接信息。
- 个人 Windows 绝对路径和固定服务器安装路径。
- 公司专属水印、免责声明正文或内部组织命名。
- API Key、Cookie、Token、密码或可推断真实环境的示例。

本地示例统一使用 127.0.0.1:8099。外部依赖使用环境变量名或明确的示例占位符。尖括号形式的主机名属于有意设计的配置占位符，不代表待补写内容。

## 10. 快速开始与 API

快速开始以 Docker Compose 为推荐路径：

1. 复制 .env.example 为 .env。
2. 配置数据库与 Dify 环境变量。
3. 执行 docker compose up -d --build。
4. 请求 http://127.0.0.1:8099/health。
5. 打开 http://127.0.0.1:8099/records-ui。

同时保留 start.ps1 作为非 Docker 开发入口，并说明 DOC 转换、OCR 和 Poppler 等系统依赖在 Docker 中更完整。

核心 API 只列通用相对路由，按材料、证据包、运行、历史、记忆和报告六组归类，不嵌入公司网络地址或大段 Dify 请求体。

## 11. Roadmap

Roadmap 采用“已有基础上的演进”表述。

### P0：提升 AI 与 Agent 岗位辨识度

1. 使用 LangGraph 将现有 prepare、attachments、evidence、compact、provider、repair、quality_gate 和 word_publish 阶段标准化为显式状态图。
2. 将现有离线回归与质量评估基础扩展为系统化 LLM Evaluation，覆盖 Faithfulness、Citation Accuracy、Completeness 和 Hallucination Rate。
3. 使用 Redis 与 Celery 或 Arq 替换进程内后台线程，补齐队列、重试、并发控制和可恢复任务。
4. 将 Evidence Grounding 扩展为 Claim、Evidence、Citation、Source Location 和 Confidence 的完整证据链。

### P1：提升平台与模型工程能力

1. 将任务元数据迁移到关系数据库，将大文件、证据包和报告迁移到 MinIO。
2. 完善 ReportGenerator 抽象，支持 Dify、OpenAI 兼容接口、DeepSeek 与本地模型的切换、回退、成本和效果对比。
3. 建立历史公告 RAG，用检索发现候选材料，再由 Evidence 系统确认事实。

### P2：提升可观测性与治理

1. 增加 OpenTelemetry、Prometheus 和 Grafana，监控队列、解析、模型、修复、质量和成本指标。
2. 增加 Human-in-the-loop、暂停审批和 Agent Trace 展示。
3. 增加权限、文件扫描、Prompt Injection 防护和数据脱敏。
4. 增加 Prompt Registry、版本回滚和 A/B 测试。

## 12. README 验证方案

提交 README 前执行以下检查：

1. 标题层级、表格、代码块和 Mermaid 语法完整。
2. 扫描内网 IP、应用 UUID、数据库名、个人路径、密钥及 Cookie。
3. 对照 requirements.txt、Dockerfile、docker-compose.yml、AGENTS.md、app/main.py 和 tests 目录核验技术栈与功能描述。
4. 确认 Dify 是当前唯一可用生成 Provider。
5. 确认基础配置和 S4 配置的开关差异没有被混写。
6. 确认 URL 兼容流程和 Word 导出没有被描述为默认可用。
7. 确认 Roadmap 中的能力没有出现在“当前能力”章节。
8. 不写“全部测试通过”等未经本次执行验证的结论。

## 13. 完成标准

- README 以中文为主，招聘方可在首屏理解项目价值。
- 技术选型、功能和流程均有当前仓库事实支撑。
- 默认能力、开关能力和 Roadmap 边界清晰。
- 不含公司内网和个人环境信息。
- 保留开发者可以实际执行的本地启动、API 和测试说明。
- Roadmap 能支持后续围绕 Agent、Evaluation、可靠任务和 Evidence Grounding 展开面试讨论。
