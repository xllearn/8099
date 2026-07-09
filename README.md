# Medical Notice Analyzer

Local helper service for Dify workflows. It reads a government notice URL, discovers Word/Excel/PDF attachments, extracts text and table content, and returns a merged evidence package for LLM report generation.

## Company Server Deployment

Target company Dify app:

```text
http://192.168.34.86/app/b380094d-aa69-462e-a765-43d1afba862c/workflow
```

Target helper service:

```text
http://192.168.34.88:8099
```

For the company Dify app above, HTTP request nodes should call the helper service by server IP, not `localhost`, `127.0.0.1`, or `host.docker.internal`.

Required HTTP node URLs and JSON request bodies:

```text
POST http://192.168.34.88:8099/analyze
Content-Type: application/json

{"url":"{{#start_node.notice_url#}}","max_attachments":25,"max_combined_chars":60000}
```

```text
POST http://192.168.34.88:8099/report/render
Content-Type: application/json

{"markdown":"{{#generate_report.text#}}","strict_quality":true}
```

```text
POST http://192.168.34.88:8099/report/qa
Content-Type: application/json

{"qa_output":"{{#qa_report_first.text#}}","report_ir":{{#parse_render.report_ir#}},"report_text":"{{#parse_render.report_markdown#}}","history_text":"{{#conversation.history_insights#}}","evidence_text":"{{#conversation.source_evidence#}}"}
```

```text
POST http://192.168.34.88:8099/report/export_checked
Content-Type: application/json

{"report_ir":{{#conversation.current_report_ir#}},"markdown":"{{#conversation.current_final_report#}}","qa_output":"","qa_status":"","strict_quality":false,"history_text":"{{#conversation.history_insights#}}","evidence_text":"{{#conversation.source_evidence#}}"}
```

Health check for Dify connectivity:

```bash
curl -sS http://192.168.34.88:8099/health
```

Expected result includes `status: ok`, `service: medical-notice-analyzer`, and `public_base_url: http://192.168.34.88:8099`. Sensitive values such as API keys, cookies, tokens, and passwords are never returned by `/health`.

Server environment setup:

```bash
cd /opt/medical-notice-analyzer
cp .env.example .env
vi .env
docker compose up -d --build
docker logs --tail=100 medical-notice-analyzer
```

Set real secrets only in `.env` or exported environment variables. Do not hardcode them in Python, YAML, prompts, or tests.

## Database Record Selection

Phase 1 adds an internal article browser and material selection page. Phase 2 prepares a database-backed `evidence_pack` from selected primary and auxiliary materials. The current Stage 4 flow downloads and parses database attachments by default when the backend is inside the company network, stores only parsed-summary JSON, and never keeps original attachment files long term.

Open the page after the service starts:

```text
http://192.168.34.88:8099/records-ui
```

Backend endpoints used by the page:

```text
GET  /records
GET  /records/{menu_code}/{articleid}
POST /analysis/selection/preview
POST /analysis/prepare
GET  /analysis/packs/{pack_id}
GET  /analysis/packs/{pack_id}/summary
POST /analysis/run
GET  /analysis/runs/{run_id}
GET  /analysis/runs/{run_id}/report
```

Database settings are read from `.env` or environment variables:

```text
DB_HOST=192.168.36.36
DB_PORT=3306
DB_NAME=wangfanqi_test
DB_USER=
DB_PASSWORD=
DB_CHARSET=utf8mb4
EVIDENCE_PACK_DIR=/app/data/evidence_packs
ANALYSIS_RUN_DIR=/app/data/analysis_runs
```

Do not put database credentials in source files or Dify workflow YAML. The list endpoint reads from `sample_article_wide` with `status = 0`, counts attachments from `sample_article_attach`, and does not return full `content`. The prepare endpoint reads selected records, cleans article HTML into `content_text`, downloads and parses attachment summaries when enabled, saves full JSON packs under `EVIDENCE_PACK_DIR`, and returns a `pack_id`.

Attachment parsing currently supports PDF, DOC, DOCX, XLSX, XLSM, XLS, CSV, TXT, HTML/HTM, and ZIP. Legacy `.doc` files are converted to DOCX with LibreOffice in a temporary directory, then parsed through the DOCX parser. If conversion fails, the attachment is marked as `parse_failed` and report generation continues with warnings.

Dify should call `GET /analysis/packs/{pack_id}`. This returns a compact `evidence_pack_for_dify` designed to stay below Dify variable-size limits. Use `GET /analysis/packs/{pack_id}?full=true` only for backend diagnostics or debugging because it returns the full saved `evidence_pack`.

Phase 3 adds a backend-only Dify proxy for report generation. The frontend calls `POST /analysis/run` with a `pack_id`; the backend calls the Dify Workflow API in blocking mode, saves the run result under `ANALYSIS_RUN_DIR`, and the frontend reads the report from `/analysis/runs/{run_id}/report`. The frontend never receives the Dify API key.

Dify settings are read from `.env` or environment variables:

```text
DIFY_BASE_URL=http://192.168.34.86/v1
DIFY_WORKFLOW_API_KEY=
DIFY_REPORT_WORKFLOW_ENDPOINT=/workflows/run
DIFY_RESPONSE_MODE=blocking
DIFY_USER=analysis_frontend
DIFY_TIMEOUT_SECONDS=600
```

The pack-based Dify workflow should start with `pack_id`, then use an HTTP node to fetch:

```text
GET http://192.168.34.88:8099/analysis/packs/{{pack_id}}
```

Do not use `localhost` from inside Dify for this HTTP node. The final Dify output should include `report_title`, `report_markdown`, `quality_check`, `generation_warnings`, and `remaining_issues` as JSON.

## Start with Docker

```powershell
cd C:\Users\admin\Documents\htmldataconclusion\medical-notice-analyzer
docker compose up -d --build
```

Health check:

```powershell
Invoke-WebRequest http://127.0.0.1:8099/health
```

Analyze a notice:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8099/analyze `
  -ContentType application/json `
  -Body '{"url":"https://example.com/notice.html"}'
```

From Dify Docker containers, call:

```text
http://host.docker.internal:8099/analyze
```

## 新版 Chatflow 主流程

当前保留的 Dify 配置为 `dify_workflow_pack_id_human_style.yml`，用于 pack_id 证据包分析工作流。

- 首轮必须输入公告 URL。
- 可选上传关联项目 Word，系统只将其提炼为串联分析参考，不作为本次公告事实来源。
- 系统先抓取网页正文和附件，再生成 `ReportIR`，由后端渲染 Markdown。
- 质检会检查事实依据、表格一致性、历史泄漏和人工分析稿结构接近度。
- 质检合格后先展示报告和质检摘要，不立即导出 Word。
- 用户确认下载后才调用 `/report/export_checked` 生成 Word。
- 用户反馈修订分支排在后续阶段实现；当前先完成生成质量、质检和确认后下载。

## Optional Firecrawl

Set `FIRECRAWL_API_KEY` before starting the service. The service will use Firecrawl for page markdown extraction and still use local parsing for attachments.

```powershell
$env:FIRECRAWL_API_KEY="fc-..."
docker compose up -d --build
```

`start.ps1` is kept for non-Docker local development, but Docker is the recommended runtime.

## Report Generation Rules

The Dify LLM prompt now asks the model to produce one explicit `ReportIR` region:

```text
<report_ir>
{ ... structured ReportIR JSON ... }
</report_ir>
```

`ReportIR` is the canonical report format. Dify display Markdown is generated by `POST /report/render`; Word output is generated only after approval through `POST /report/export_checked`.

```json
{
  "title": "",
  "suggested_filename": "",
  "notice_type": "",
  "publish_date": "",
  "source_agency": "",
  "document_name": "",
  "lead_paragraphs": [],
  "sections": [
    {
      "heading": "",
      "paragraphs": [],
      "tables": [
        {
          "title": "",
          "headers": [],
          "rows": [],
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

`suggested_filename` is optional for backward compatibility, but the current prompt should always output it. `notice_type` should be one of: `挂网流程调整类`, `价格联动/价格治理类`, `集采/接续采购类`, `征求意见稿类`, or `其他公告类`.

The export endpoint rejects raw model replies. If `ReportIR` JSON cannot be parsed, the service only accepts content inside `<final_report>...</final_report>` as a fallback. It never writes reasoning fields such as `reasoning`, `analysis`, `scratchpad`, `thought`, `debug`, `raw_response`, or chain-of-thought into the Word file.

The prompt intentionally avoids a fixed five-part structure. Reports should follow the source document naturally: title, short lead paragraphs, and source-driven sections such as procurement basics, product scope, demand volume, quote rules, selection rules, volume allocation, non-selected product management, and enterprise tips. Missing source content should not be forced into the report.

## Chatflow Enhancements

The retained Dify DSL is `dify_workflow_pack_id_human_style.yml`. It is the current pack_id workflow used for evidence-pack based report generation.

Recommended Dify variables:

- `notice_url`: required text input for the source announcement.
- `history_report`: optional Word upload for a previous human analysis report.
- `source_evidence`: conversation variable containing `/analyze` evidence.
- `history_insights`: conversation variable generated from the optional history Word.
- `current_report_ir`: latest structured ReportIR.
- `current_final_report`: latest Markdown report.
- `last_export_filename`: latest exported Word filename.
- `last_qa_summary`: latest QA and repair summary for Dify result display.

Historical analysis reports are strictly constrained. They are only for history comparison, style reference, project-continuity observation, and short enterprise-attention supplements. They must not be treated as current announcement evidence. Do not add a history heading, section, or table. If historical context is used, it should be at most one short bridging paragraph naturally merged into a relevant existing paragraph, with limiting language such as `结合既往同类项目经验` or `从历史项目看`. Do not copy the history Word's rules, prices, cycle, company scope, product scope, region scope, purchase volume, or dates into the current report as facts.

After the first report is generated, later user messages should enter the revision branch. The revision prompt uses the original evidence, current ReportIR, current Markdown, optional history insights, and the user's requested change. It must not add unsupported facts.

## Dify 内置 token 与耗时统计

Dify 已记录总 token、总耗时和节点级 token/耗时，本项目不另行估算 token，避免和 Dify 真实计费统计不一致。

常用查看方式：

- Dify 页面：进入应用 Run History / Tracing，可查看每次运行和每个节点的耗时。
- Dify API：`GET /workflows/run/{workflow_run_id}` 返回 `total_tokens` 和 `elapsed_time`。
- 本地数据库：`workflow_runs.total_tokens`、`workflow_runs.elapsed_time` 保存总量；`workflow_node_executions.execution_metadata` 保存节点 token，`workflow_node_executions.elapsed_time` 保存节点耗时。

本项目提供只读导出脚本：

```powershell
.\scripts\dify_run_stats.ps1
.\scripts\dify_run_stats.ps1 -Latest 3
.\scripts\dify_run_stats.ps1 -WorkflowRunId "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
```

脚本会输出 `Dify workflow run total` 和 `Dify node totals` 两段结果，分别对应总 token/总耗时和每个节点的 token/耗时。

## QA And Repair

The Chatflow should run a QA LLM node after each generation or revision. The QA node outputs JSON with:

```json
{
  "status": "pass | needs_fix | block",
  "issues": [],
  "unsupported_claims": [],
  "history_leakage": [],
  "missing_rules": [],
  "language_issues": [],
  "fix_instructions": [],
  "summary": ""
}
```

Send that JSON to the local helper endpoint:

```text
POST http://host.docker.internal:8099/report/qa
```

The endpoint parses the QA JSON, adds a lightweight local history-leakage check, and returns `blocked`, `qa`, and `qa_summary`. If QA JSON is invalid, the endpoint returns `422`; the Chatflow should not export Word. If `status=needs_fix`, run the QA-fix prompt once and then run QA again. If the second QA still reports severe unsupported claims, history leakage, invalid JSON, or clear source mismatch, do not export Word. Show the issue list in the Dify result area.

QA and repair summaries are for Dify display only. They are never written into Word.

Tables should be split by category. Do not build one wide table containing all facts. Typical tables include procurement demand, price rules, gradient volume allocation, price-increase deduction coefficients, key dates, and submission/execution requirements.

For procurement-file reports, rule completeness takes priority over brevity. When source text includes sections such as `企业报价要求`, `拟中选产品确定`, `协议采购量`, `采购执行`, `价格联动`, `非中选产品管理`, or `名词解释`, the evidence package flags those phrases for the LLM and the prompt asks it to preserve them as independent sections or focused tables.

## Word Filename Rules

The Word exporter builds a `.docx` filename in this order:

1. `report_ir.suggested_filename`
2. `report_ir.title`
3. `report_ir.document_name`
4. the first Markdown H1 when only `<final_report>` is available
5. `医药器械采购项目分析报告_YYYYMMDDHHmmss`

Filename cleanup removes Windows-illegal characters (`\ / : * ? " < > |`), Markdown/code-fence residue, Dify variables such as `{{#node.xxx#}}`, newlines/tabs, duplicate spaces, and the word `水印`. Filenames are capped to a safe length and deduplicated with `(1)`, `(2)`, etc.

The download endpoint returns a Word MIME type and a UTF-8 `Content-Disposition` header so Chinese filenames work in browser downloads.

## DOCX Style Configuration

The Word exporter applies a formal report style:

- A4 page size with moderate margins.
- Chinese font: Songti; English font: Calibri.
- Main title centered and bold.
- Header with the document name or default report name.
- Footer with generated date and page number.
- A text watermark `易联器械` is written to the generated Word file.
- `notice_type` is internal metadata only and is not rendered as a mechanical `公告类型 | 发布日期 | 发布主体` line in the Word body.
- Table header shading, bold centered header text, grid borders, repeated header rows.
- `sections[].highlights` rendered as bold red key rules.
- A fixed disclaimer is appended at the end. The disclaimer heading uses Kaiti size 2; the disclaimer body uses Songti size 3. Set `DISCLAIMER_SEPARATE_PAGE=true` to place the disclaimer on a separate page.

Default disclaimer:

```text
声  明

本文基于互联网公开资料进行整理，目的在于传递分享信息，仅供读者参考之用。本网站不保证信息的准确性、有效性、及时性和完整性。本公司及其雇员一概毋须以任何方式就任何信息传递或传送的失误、不准确或错误，对用户或任何其他人士负任何直接或间接责任。在法律允许的范围内，本公司在此声明，不承担用户或任何人士就使用或未能使用本网站所提供的信息或任何链接所引致的任何直接、间接、附带、从属、特殊、惩罚性或惩戒性的损害赔偿。
```

The exporter ignores any LLM-provided `report_ir.disclaimer` and writes the fixed disclaimer above.

## Quality Checks

Before exporting, the service checks for:

- Title.
- Residual structural tags, Markdown code fences, or Dify variables.
- Informal or over-assertive terms such as `我认为`, `大概`, `显然`, `必然`, `全面利好`.
- Reasoning/debug markers such as `analysis`, `scratchpad`, `debug`, and `<think>`.
- Empty table headers or row/column mismatch.

Quality checks run by default, but they are intentionally lightweight so short notices without attachments can still produce a Word report. Set `strict_quality=false` on `/report/export` to bypass blocking behavior after upstream debugging.

## Prompt Synchronization

The repository stores the recommended Dify prompts in:

- `prompts/report_system_prompt.md`
- `prompts/report_user_prompt.md`
- `prompts/report_history_prompt.md`
- `prompts/report_revision_prompt.md`
- `prompts/report_qa_prompt.md`
- `prompts/report_qa_fix_prompt.md`

If your live Dify workflow was edited in the web UI, manually copy these prompts into the LLM node. The local helper service cannot directly update Dify's database prompt configuration.

## Tests

Run regression tests in Docker:

```powershell
cd C:\Users\admin\Documents\htmldataconclusion\medical-notice-analyzer
docker compose up -d --build
docker exec medical-notice-analyzer python -m unittest discover -s tests -v
```
