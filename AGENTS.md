# AGENTS.md

This file is for Codex / automation agents working on this project. It records stable project rules, startup commands, and common pitfalls. Do not write real passwords, API keys, database credentials, or server secrets here.

## Project

- Project: `medical-notice-analyzer`
- Backend: FastAPI, entrypoint `app/main.py`
- Frontend: FastAPI static pages, no React/Vue/Vite build system
- Selection page: `/records-ui`
- Report detail page: `/analysis-runs/{run_id}`
- Main flow: database selection -> `evidence_pack` -> backend proxies Dify -> report detail page
- The old URL analysis flow is still kept. Do not delete it unless the user explicitly asks.

## Startup And Tests

Local Docker:

```powershell
docker compose up -d --build
```

Local Python:

```powershell
.\start.ps1
```

Health check:

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8099/health
```

Full local test run:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Server deployment directory:

```text
/opt/medical-notice-analyzer
```

Production backend:

```text
http://192.168.34.88:8099
```

## Secrets

- Real passwords, Dify API keys, cookies, and database connection strings must stay in `.env` or server environment variables.
- Do not put secrets in source code, tests, README, Dify DSL files, or this file.
- Logs must not print API keys, cookies, database passwords, full attachment text, or full report text.

## Database Rules

- Core tables: `sample_article_wide`, `sample_article_attach`
- Article identity must use `menu_code + articleid`.
- Do not use `articleid` alone.
- List queries should default to `status = 0`.
- List APIs must not return full `content`.
- Attachment counts should be aggregated to avoid duplicate article rows from joins.

## Dify Rules

- The frontend must not call Dify directly.
- Dify API keys belong only in backend environment variables.
- Dify HTTP nodes should call:

```text
GET http://192.168.34.88:8099/analysis/packs/{{pack_id}}
```

- Do not use `localhost` from inside Dify to reach the backend.
- Updating a local DSL file does not update Dify UI automatically. Import and publish it in Dify.
- LLM nodes should prefer JSON output, with backend parsing fallbacks kept.

## evidence_pack Rules

- The backend saves the full `evidence_pack`.
- Dify reads the compact `evidence_pack`.
- Do not directly truncate `evidence_pack` as a raw string.
- Do not send full attachment text or full Excel tables to Dify.
- Preserve primary material body text, key facts, and core attachment summaries first.
- Auxiliary materials are for background, comparison, and supplements only. They must not override primary materials.

## Attachment Parsing

Supported formats: PDF, DOC, DOCX, XLSX, XLSM, XLS, CSV, TXT, HTML/HTM, ZIP.

- In the company intranet, attachments should be downloaded and parsed by default.
- Do not store original attachment files long-term.
- Cache only parsed attachment summary JSON.
- Temporary files must be deleted after parsing.
- `.doc` depends on LibreOffice conversion to DOCX.
- Scanned PDFs may require OCR.
- Excel/CSV files should produce structured summaries, not full-table model input.
- ZIP handling must prevent path traversal.

## Report Rules

Reports must strictly rely on original materials and `evidence_pack`, while still providing restrained analysis.

Report body should not contain technical implementation notes such as:

- `Dify`
- `evidence_pack`
- `metadata`
- `元数据`
- `未解析`
- `OCR识别`
- `资料说明`
- `解析失败`
- `识别错误`
- `根据医学常识修正`
- `以正式文件为准`

Those details belong in diagnostics or warnings, not in the formal report body.

If Dify returns a report that is too short, only `...`, or starts from a middle section, the backend should treat it as a fragment and avoid saving it as the final report.

## Frontend Rules

- Static pages are under `app/static/`.
- Do not introduce a frontend build system unless the user explicitly asks.
- The selection page should not display full report content.
- After report generation starts, jump to the report detail page.
- The report detail page should show the report, diagnostics, progress, Word download, and Markdown copy actions.

## Common Pitfalls

- Dify cannot reach backend: check for `localhost`; use `192.168.34.88:8099`.
- Dify returns `Not found`: check workflow API key, Dify publish status, and backend deployment.
- `evidence_pack` exceeds 80000 chars: confirm Dify reads the compact pack.
- `.doc` parsing fails: check LibreOffice inside Docker.
- Scanned PDF has no content: check OCR dependencies and attachment cache.
- Word count differs from WPS: use visible text counting, not raw Markdown length.

## Change Discipline

- Read relevant code and tests before editing.
- Keep changes scoped; do not refactor unrelated code.
- Do not delete existing features.
- Add or update tests for new logic.
- Run tests before deployment.
- After deployment, verify `/health` and the relevant user flow.
