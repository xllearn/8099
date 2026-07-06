# PROJECT_MAP.md

## 项目定位

本项目从公司公告数据库选择医药公告及附件，生成 evidence pack，
通过后端代理调用 Dify Workflow，最终提供报告预览、诊断、修订和 Word 下载。

## 系统边界

- 业务服务：FastAPI，目录 `medical-notice-analyzer`
- 前端：`app/static/*.html`，无独立构建系统
- 公告数据库：外部 MySQL/MariaDB
- AI 工作流：外部 Dify
- 状态存储：本地 JSON 文件
- Dify 完整源码：仓库根目录 `dify/`，与业务服务独立部署

## 主要入口

- 后端入口：`app/main.py`
- 选材页面：`GET /records-ui`
- 报告页面：`GET /analysis-runs/{run_id}`
- 健康检查：`GET /health`

## 主业务链路

1. `GET /records`
2. `GET /records/{menu_code}/{articleid}`
3. `POST /analysis/prepare`
4. 保存 `EVIDENCE_PACK_DIR/{pack_id}.json`
5. `POST /analysis/run`
6. 保存 `ANALYSIS_RUN_DIR/{run_id}.json`
7. 后台调用 Dify `/workflows/run`
8. Dify 调用 `GET /analysis/packs/{pack_id}`
9. 前端轮询 `GET /analysis/runs/{run_id}`
10. 读取 `GET /analysis/runs/{run_id}/report`
11. 下载 `GET /analysis/runs/{run_id}/download`

## 数据库约束

- 公告表：`sample_article_wide`
- 附件表：`sample_article_attach`
- 唯一业务标识：`menu_code + articleid`
- 默认只读取 `status = 0`
- 列表接口不得返回完整 `content`

## 选材约束

- 主材料：1–3 条
- 辅助材料：0–10 条
- 同一公告不能同时属于主材料和辅助材料
- 辅助材料只用于背景、对比和补充

## Evidence Pack

完整包保存在 `EVIDENCE_PACK_DIR`。
Dify 默认读取 `/analysis/packs/{pack_id}` 返回的 compact pack。
`?full=true` 仅用于后端诊断。

优先保留：

1. 主材料正文
2. 主材料结构化规则和关键事实
3. 主材料核心附件摘要和表格结构
4. 辅助材料相关片段
5. 辅助附件摘要

## 附件模块

- 下载：`app/attachment_fetcher.py`
- 解析：`app/attachment_parser.py`
- 缓存：`app/attachment_cache.py`

支持 PDF、DOC、DOCX、XLSX、XLSM、XLS、CSV、TXT、HTML、ZIP。
DOC 依赖 LibreOffice。
扫描 PDF 依赖 Poppler 和 Tesseract。
原附件不得长期保存。

## Dify

当前 DSL：

`dify_workflow_pack_id_human_style.yml`

主要节点：

Start -> Fetch Pack -> Generate -> QA1 ->
可选 Revise -> QA2 -> Final

Dify HTTP 节点必须通过可访问的业务服务 IP 读取：

`GET /analysis/packs/{{pack_id}}`

禁止在前端暴露 Dify API Key。

## 状态和文件

- `EVIDENCE_PACK_DIR`：完整证据包
- `ANALYSIS_RUN_DIR`：run 状态和报告
- `ATTACHMENT_PARSE_CACHE_DIR`：附件解析缓存
- `REPORT_DIR`：临时 DOCX
- `MEMORY_DIR`：长期写作记忆

## 开发检查

```powershell
docker compose up -d --build
Invoke-WebRequest http://127.0.0.1:8099/health
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## 当前重点风险

- compact pack 仍可能超过 Dify 上限
- 修订接口与当前 DSL Start 变量不一致
- run 下载未强制经过质量门禁
- run/evidence JSON 没有清理策略
- 进度为估算值，不是 Dify 节点实时状态
- 服务重启会丢失进程内后台任务
