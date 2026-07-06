# Project Journal

## P0-1: Workflow run persistence shell

**Date:** 2026-07-03

**Goal:** Add a persistent, queryable run-state shell around the current Dify-backed analysis flow without changing the report generation result.

**Planned file changes:**

- `app/core/workflow/state.py`: workflow backend, run status, and node status models.
- `app/core/workflow/store.py`: JSON run store with atomic write and safe read behavior.
- `app/core/workflow/__init__.py`: package exports.
- `app/main.py`: replace direct run JSON read/write with the store and add Dify legacy backend/node metadata.
- `tests/test_workflow_run_store.py`: focused unit tests for store behavior.
- `tests/fixtures/synthetic_evidence_pack_basic.json`: minimal synthetic fixture for stage documentation/tests.
- `docs/*`: plan, journal, technical decisions, testing/evaluation, and architecture evolution.

**Planned non-changes:**

- No retry/cancel/resume because P0-1 only creates the persistence shell.
- No local workflow engine because Dify legacy must stay the default until P0-2.
- No export rewrite because quality-gated export is P0-4.
- No large `app/main.py` split because the first stage should avoid broad refactors.

**Expected effect:** Existing users still see the same analysis run behavior, while operators can inspect a richer `run.json` containing backend and node status metadata.

**Open risks:**

- The worktree already contains unrelated local changes; P0-1 must avoid reverting or rewriting them.
- Existing tests may encode assumptions about run records, so new fields must be additive.

### 2026-07-06 Execution Result

**Completed:** P0-1 local implementation and verification.

**Implemented state:**

- `WorkflowBackend`, `WorkflowRunStatus`, `WorkflowNodeStatus`, `WorkflowNode`, and `WorkflowRun` define the workflow persistence shell.
- `WorkflowRunStore` persists run JSON with temp-file write, flush/fsync, `os.replace`, and per-run in-process locks.
- Existing analysis run writes go through `WorkflowRunStore` and add `backend`, `workflow_backend`, `nodes`, timestamps, elapsed time, error, and artifact placeholders without changing the Dify legacy business output.
- A synthetic evidence pack fixture anchors repeatable persistence tests without live LLM calls.

**Verification:**

- Baseline before final model patch:
  - `.\.venv\Scripts\python.exe -m unittest tests.test_workflow_run_store -v` -> Ran 5 tests, OK.
  - `.\.venv\Scripts\python.exe -m unittest tests.test_records_api.RecordsApiTests.test_analysis_run_calls_dify_and_persists_report -v` -> Ran 1 test, OK.
  - `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` -> Ran 167 tests, OK.
- After model/test completion:
  - `.\.venv\Scripts\python.exe -m unittest tests.test_workflow_run_store -v` -> Ran 8 tests, OK.
  - `.\.venv\Scripts\python.exe -m unittest tests.test_records_api.RecordsApiTests.test_analysis_run_calls_dify_and_persists_report -v` -> Ran 1 test, OK.
  - `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` -> Ran 170 tests, OK.

**Not done:** P0-2 local workflow engine, retry/cancel/resume, LLM provider abstraction, prompt hash tracking, quality-gated export, memory system changes, Evidence Pack v3, and broad `app/main.py` refactor remain out of scope.

**Remaining risks:**

- Live Dify/server deployment was not exercised in this stage because deployment was out of scope.
- The repository still contains unrelated dirty worktree changes from earlier work; P0-1 did not revert them.

## P0-2: Local workflow MVP

**Date:** 2026-07-06

**Goal:** Add a local workflow backend that can produce a draft report without calling Dify, while keeping the existing Dify-backed path as the default.

**Implemented state:**

- `app/core/workflow/engine.py` implements the deterministic local serial nodes `prepare`, `generate`, `render`, `qa`, and `final`.
- `run_local_workflow(pack, run_id)` builds a draft Markdown report from evidence pack material titles, body text, attachment filenames, and attachment summaries.
- Local run results include `backend` and `workflow_backend` set to `local_engine`, `workflow_run_id`, `nodes`, `report_title`, `report_markdown`, `quality_check`, `quality_gate`, and draft warnings.
- `/analysis/run` selects `local_engine` only when `WORKFLOW_BACKEND=local_engine`; otherwise it continues to use `dify_legacy`.
- The local API regression patches `_call_dify_workflow` to raise if called, proving the local backend does not invoke Dify in that path.

**Not changed:**

- No full DAG engine.
- No retry/cancel/resume.
- No LLM provider abstraction or prompt hash.
- No export blocking change.
- No memory item schema change.
- No broad split of `app/main.py`.

**Verification:**

- Before baseline inherited from P0-2 start:
  - `.\.venv\Scripts\python.exe -m unittest tests.test_workflow_run_store -v` -> Ran 8 tests, OK.
  - `.\.venv\Scripts\python.exe -m unittest tests.test_records_api.RecordsApiTests.test_analysis_run_calls_dify_and_persists_report -v` -> Ran 1 test, OK.
  - `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` -> Ran 170 tests, OK.
- Red tests observed during TDD:
  - `tests.test_local_workflow_engine` initially failed because `app.core.workflow.engine` did not exist.
  - The local API regression initially failed because `WORKFLOW_BACKEND=local_engine` still reached the Dify call path.
- After implementation:
  - `.\.venv\Scripts\python.exe -m unittest tests.test_local_workflow_engine tests.test_workflow_run_store -v` -> Ran 9 tests, OK.
  - `.\.venv\Scripts\python.exe -m unittest tests.test_records_api.RecordsApiTests.test_analysis_run_uses_local_engine_when_configured tests.test_records_api.RecordsApiTests.test_analysis_run_calls_dify_and_persists_report -v` -> Ran 2 tests, OK.
  - `.\.venv\Scripts\python.exe -m unittest tests.test_records_api -v` -> Ran 88 tests, OK.
  - `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` -> Ran 172 tests, OK.

**Public fixture note:** The repository currently contains `tests/fixtures/synthetic_evidence_pack_basic.json`, but no existing minimized public evidence pack fixture was found during P0-2. The local workflow was therefore verified with the synthetic fixture and mocked API data only; no live public URL, Dify call, or server deployment was run.

**Remaining risks:**

- The local report is a deterministic template draft and is explicitly marked for manual review; it is not a final quality-scored AI report.
- P0-4 must still enforce export blocking; P0-2 only records local `quality_gate` metadata.
- Public real-data coverage remains to be added by a later stage or by introducing a minimized public pack fixture.

## P0-3: LLM provider and prompt hash

**Date:** 2026-07-06

**Goal:** Separate local workflow model calls from workflow execution and record prompt/version/hash metadata for traceability.

**Implemented state:**

- `app/core/llm/provider.py` defines `PromptRef`, `LLMRequest`, `LLMResult`, `LLMProvider`, `LLMProviderError`, `MockLLMProvider`, prompt loading, provider selection, and JSON parsing.
- `prompts/local_report_generation/v1.md` is the first versioned local-generation prompt.
- `run_local_workflow(..., llm_provider=None)` now runs the local generate step through the provider boundary and records `llm_provider`, `llm_model`, `prompt_ref`, `prompt_sha256`, `prompt_refs`, and `model_calls`.
- The default provider remains deterministic mock behavior, so tests do not need provider secrets or network calls.
- Invalid provider JSON is converted to a `LLM_JSON_PARSE_ERROR` quality issue and a `needs_manual_review` local run result.
- Dify remains only a workflow backend through `dify_legacy`; it was not modeled as an LLM provider.

**Not changed:**

- No real external LLM provider credentials, tokens, or network calls.
- No Dify workflow configuration or server deployment.
- No export blocking change; P0-4 owns quality-gated export behavior.
- No retry/cancel/resume, idempotency, recovery, memory schema, or broad `app/main.py` refactor.

**Verification:**

- Before baseline inherited from P0-3 start:
  - `.\.venv\Scripts\python.exe -m unittest tests.test_local_workflow_engine tests.test_workflow_run_store -v` -> Ran 9 tests, OK.
  - `.\.venv\Scripts\python.exe -m unittest tests.test_records_api.RecordsApiTests.test_analysis_run_uses_local_engine_when_configured tests.test_records_api.RecordsApiTests.test_analysis_run_calls_dify_and_persists_report -v` -> Ran 2 tests, OK.
  - `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` -> Ran 172 tests, OK.
- Red tests observed during TDD:
  - `tests.test_llm_provider` initially failed because `app.core.llm` did not exist.
  - New local workflow provider metadata tests initially failed because `run_local_workflow` did not expose provider/model/prompt hash fields.
  - The local API metadata regression initially failed with missing `llm_provider`/prompt metadata in the status response.
- After implementation:
  - `.\.venv\Scripts\python.exe -m unittest tests.test_llm_provider tests.test_local_workflow_engine tests.test_workflow_run_store -v` -> Ran 14 tests, OK.
  - `.\.venv\Scripts\python.exe -m unittest tests.test_records_api.RecordsApiTests.test_analysis_run_uses_local_engine_when_configured tests.test_records_api.RecordsApiTests.test_analysis_run_calls_dify_and_persists_report -v` -> Ran 2 tests, OK.
  - `.\.venv\Scripts\python.exe -m unittest tests.test_records_api -v` -> Ran 88 tests, OK.
  - `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` -> Ran 177 tests, OK.

**Metrics:** No existing P0-3 quantitative evaluation metric was found. No synthetic metric was added. Test-count movement is recorded only as regression coverage evidence: full discovery changed from 172 tests before P0-3 to 177 tests after P0-3.

**Remaining risks:**

- P0-3 only provides a mock provider boundary; a real provider still needs explicit configuration and tests in a future stage.
- Prompt `local_report_generation:v1` is intentionally minimal and should be evolved only with repeatable evaluation coverage.
- P0-4 must still enforce export blocking; P0-3 only records provider/prompt metadata and manual-review state.

## P0-4: Quality gate blocks export

**Date:** 2026-07-06

**Goal:** Prevent reports with failed QA or manual-review quality gates from entering Word export, while keeping report viewing and explicit download flow intact.

**Implemented state:**

- `app/core/quality/gate.py` implements `analysis_run_export_precheck(record, quality_gate=None)` for run-level Word export decisions.
- `/analysis/runs/{run_id}/download` calls the precheck before creating or returning a docx.
- If an evidence pack is available, the download path recomputes the existing diagnostics quality gate before making the export decision.
- Failed `quality_check.passed=false`, `quality_gate.deliverable_status=needs_manual_review`, and failed quality-gate states return HTTP 409 with `QUALITY_GATE_BLOCKED`.
- Blocked download attempts do not create a Word file in `REPORT_DIR`.
- Existing QA-passed reports still export only when the user explicitly calls the download endpoint.
- `analysis_run.html` and `records.html` now disable their download buttons when local report state is quality-blocked.

**Not changed:**

- No broad QA rewrite.
- No new dashboard.
- No automatic export after QA pass.
- No Dify workflow, server, token, or deployment change.
- No retry/cancel/resume behavior.
- No frontend build-system migration.

**Verification:**

- Before baseline:
  - `.\.venv\Scripts\python.exe -m unittest tests.test_report_export tests.test_report_quality -v` -> Ran 36 tests, OK.
  - `.\.venv\Scripts\python.exe -m unittest tests.test_records_api.RecordsApiTests.test_analysis_run_download_exports_markdown_docx tests.test_records_api.RecordsApiTests.test_analysis_run_download_reuses_existing_docx_for_same_run_version tests.test_records_api.RecordsApiTests.test_analysis_run_download_rejects_not_ready_report tests.test_records_api.RecordsApiTests.test_run_diagnostics_reports_source_fidelity_and_blocks_unsupported_facts tests.test_records_api.RecordsApiTests.test_run_diagnostics_detects_summary_only_report tests.test_records_api.RecordsApiTests.test_local_quality_gate_downgrades_finished_result_with_unsupported_fact -v` -> Ran 6 tests, OK.
  - `.\.venv\Scripts\python.exe -m unittest tests.test_16case_regression_script -v` -> Ran 12 tests, OK.
  - `.\.venv\Scripts\python.exe -m unittest tests.test_records_api -v` -> Ran 88 tests, OK.
  - `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` -> Ran 177 tests, OK.
- Red tests observed during TDD:
  - `test_analysis_run_download_blocks_failed_quality_check` failed with HTTP 200 and created a docx.
  - `test_analysis_run_download_blocks_quality_gate_manual_review` failed with HTTP 200 and created a docx.
- After implementation:
  - `.\.venv\Scripts\python.exe -m unittest tests.test_records_api.RecordsApiTests.test_analysis_run_download_blocks_failed_quality_check tests.test_records_api.RecordsApiTests.test_analysis_run_download_blocks_quality_gate_manual_review tests.test_records_api.RecordsApiTests.test_analysis_run_download_exports_markdown_docx tests.test_records_api.RecordsApiTests.test_analysis_run_download_reuses_existing_docx_for_same_run_version tests.test_records_api.RecordsApiTests.test_analysis_run_uses_local_engine_when_configured tests.test_records_api.RecordsApiTests.test_records_ui_serves_static_page tests.test_records_api.RecordsApiTests.test_records_ui_matches_three_column_reference_layout -v` -> Ran 7 tests, OK.
  - `.\.venv\Scripts\python.exe -m unittest tests.test_report_export tests.test_report_quality -v` -> Ran 36 tests, OK.
  - `.\.venv\Scripts\python.exe -m unittest tests.test_records_api -v` -> Ran 90 tests, OK.
  - `.\.venv\Scripts\python.exe -m unittest tests.test_16case_regression_script -v` -> Ran 12 tests, OK.
  - `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` -> Ran 179 tests, OK.

**Evaluation scripts:** `scripts/run_16case_report_regression.py` and `scripts/run_chatflow_acceptance.py` were inspected but not run because they call the company service or external public URLs. P0-4 did not deploy or call live services.

**Metrics:** No standalone P0-4 quantitative evaluation metric was run. Test-count movement is recorded only as regression coverage evidence: full discovery changed from 177 tests before P0-4 to 179 tests after P0-4.

**Remaining risks:**

- Existing historical run records without `quality_gate` and without readable evidence packs can only be checked by their stored `quality_check`.
- Revision flows keep existing run status and quality-gate fields unless the backend recomputes them at download time from the evidence pack.
- Live server behavior was not exercised because deployment was out of scope.
