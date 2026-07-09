# Testing And Evaluation

## P0-1 Test Plan

**Stage:** Workflow run persistence shell

**Automated commands planned:**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_workflow_run_store -v
.\.venv\Scripts\python.exe -m unittest tests.test_records_api.RecordsApiTests.test_analysis_run_calls_dify_and_persists_report -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

If the local virtualenv is unavailable, use `python -m unittest ...` from the project environment.

**Public real-data check:**

- Source type: public medical/medical-device procurement notice URL or minimized public evidence pack.
- P0-1 expectation: the legacy Dify-backed run flow still creates a run record and the status endpoint can read it.
- No public full attachment file will be committed if it is large or unstable.
- Result: not run against a live public URL in P0-1 because this stage did not deploy or call live Dify. Compatibility is covered by the mocked legacy Dify API regression below.

**Synthetic AI fixture check:**

- Fixture: `tests/fixtures/synthetic_evidence_pack_basic.json`
- P0-1 expectation: synthetic run metadata can be persisted and read without a live LLM call.
- Result: passed. `tests.test_workflow_run_store.WorkflowRunStoreTests.test_synthetic_evidence_pack_fixture_can_anchor_run_metadata` persisted and read a `WorkflowRun` using `pack_synthetic_basic`.

**Legacy Dify flow regression:**

- Existing `/analysis/run` tests will remain the primary automated regression.
- Result: passed. `.\.venv\Scripts\python.exe -m unittest tests.test_records_api.RecordsApiTests.test_analysis_run_calls_dify_and_persists_report -v` ran 1 test and returned OK.

**Old URL `/analyze` regression:**

- P0-1 does not modify `/analyze`.
- Existing tests will be run as part of full discovery when feasible.
- Result: passed as part of full discovery. `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` ran 167 tests and returned OK in the P0-1 baseline run.

**P0-1 final focused results:**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_workflow_run_store -v
```

Result: Ran 8 tests, OK.

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_records_api.RecordsApiTests.test_analysis_run_calls_dify_and_persists_report -v
```

Result: Ran 1 test, OK.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Result: Ran 170 tests, OK.

## P0-2 Test Plan

**Stage:** Local workflow MVP

**Automated commands planned:**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_local_workflow_engine tests.test_workflow_run_store -v
.\.venv\Scripts\python.exe -m unittest tests.test_records_api.RecordsApiTests.test_analysis_run_uses_local_engine_when_configured tests.test_records_api.RecordsApiTests.test_analysis_run_calls_dify_and_persists_report -v
.\.venv\Scripts\python.exe -m unittest tests.test_records_api -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

**Before baseline:**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_workflow_run_store -v
```

Result: Ran 8 tests, OK.

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_records_api.RecordsApiTests.test_analysis_run_calls_dify_and_persists_report -v
```

Result: Ran 1 test, OK.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Result: Ran 170 tests, OK.

**TDD red checks:**

- `tests.test_local_workflow_engine` failed with `ModuleNotFoundError: No module named 'app.core.workflow.engine'` before the local workflow engine existed.
- The local backend API test failed while `WORKFLOW_BACKEND=local_engine` still reached the Dify call path.
- Node completion assertions failed until the `final` node and per-node `finished_at` timestamps were persisted.

**Synthetic fixture check:**

- Fixture: `tests/fixtures/synthetic_evidence_pack_basic.json`
- P0-2 expectation: `run_local_workflow` returns a finished `local_engine` result with node order `prepare -> generate -> render -> qa -> final`, draft title/Markdown, and passing local QA.
- Result: passed. `.\.venv\Scripts\python.exe -m unittest tests.test_local_workflow_engine tests.test_workflow_run_store -v` ran 9 tests and returned OK.

**Local backend API regression:**

- P0-2 expectation: with `WORKFLOW_BACKEND=local_engine`, `/analysis/run` finishes locally, persists local nodes, and does not call `_call_dify_workflow`.
- Result: passed. `.\.venv\Scripts\python.exe -m unittest tests.test_records_api.RecordsApiTests.test_analysis_run_uses_local_engine_when_configured tests.test_records_api.RecordsApiTests.test_analysis_run_calls_dify_and_persists_report -v` ran 2 tests and returned OK.

**Legacy Dify compatibility regression:**

- P0-2 expectation: when `WORKFLOW_BACKEND` is unset or unsupported, the existing Dify legacy path remains the default.
- Result: passed as part of the 2-test focused API run and `tests.test_records_api`.

**Records API/UI regression:**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_records_api -v
```

Result: Ran 88 tests, OK.

**Full suite regression:**

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Result: Ran 172 tests, OK.

**Public real-data check:**

- The P0-2 stage table requested a synthetic evidence pack and one public pack fixture.
- Repository search found only `tests/fixtures/synthetic_evidence_pack_basic.json`; no existing minimized public pack fixture was available in `tests/fixtures`.
- P0-2 did not browse for a new public source, did not call live Dify, did not deploy, and did not run against a live public URL.
- Remaining action: add or derive a minimized public evidence pack fixture in a later stage before claiming public-real-data coverage for local workflow output.

## P0-3 Test Plan

**Stage:** LLM provider and prompt hash

**Automated commands planned:**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_llm_provider tests.test_local_workflow_engine tests.test_workflow_run_store -v
.\.venv\Scripts\python.exe -m unittest tests.test_records_api.RecordsApiTests.test_analysis_run_uses_local_engine_when_configured tests.test_records_api.RecordsApiTests.test_analysis_run_calls_dify_and_persists_report -v
.\.venv\Scripts\python.exe -m unittest tests.test_records_api -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

**Before baseline:**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_local_workflow_engine tests.test_workflow_run_store -v
```

Result: Ran 9 tests, OK.

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_records_api.RecordsApiTests.test_analysis_run_uses_local_engine_when_configured tests.test_records_api.RecordsApiTests.test_analysis_run_calls_dify_and_persists_report -v
```

Result: Ran 2 tests, OK.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Result: Ran 172 tests, OK.

**TDD red checks:**

- `tests.test_llm_provider` failed with `ModuleNotFoundError: No module named 'app.core.llm'` before the provider package existed.
- Local workflow provider metadata tests failed until provider/model/prompt hash fields were returned by `run_local_workflow`.
- The local backend API metadata regression failed with missing `llm_provider` until the local run result exposed provider/prompt metadata through the existing status endpoint.

**Synthetic LLM response check:**

- Fixture source: deterministic `MockLLMProvider` responses in `tests/test_llm_provider.py` and `tests/test_local_workflow_engine.py`.
- P0-3 expectation: prompt refs load from `prompts/local_report_generation/v1.md`, SHA-256 is 64 hex characters, model calls record provider/model/prompt metadata, and provider JSON parses into report fields.
- Result: passed. `.\.venv\Scripts\python.exe -m unittest tests.test_llm_provider tests.test_local_workflow_engine tests.test_workflow_run_store -v` ran 14 tests and returned OK.

**Invalid provider JSON regression:**

- P0-3 expectation: malformed provider output is converted to a consistent `LLM_JSON_PARSE_ERROR` issue and the local workflow result enters `needs_manual_review`.
- Result: passed as part of `tests.test_llm_provider` and `tests.test_local_workflow_engine`.

**Local backend API metadata regression:**

- P0-3 expectation: with `WORKFLOW_BACKEND=local_engine`, `/analysis/run` and `/analysis/runs/{run_id}` expose `llm_provider`, `llm_model`, `prompt_ref`, `prompt_sha256`, `prompt_refs`, and `model_calls`.
- Result: passed. `.\.venv\Scripts\python.exe -m unittest tests.test_records_api.RecordsApiTests.test_analysis_run_uses_local_engine_when_configured tests.test_records_api.RecordsApiTests.test_analysis_run_calls_dify_and_persists_report -v` ran 2 tests and returned OK.

**Legacy Dify compatibility regression:**

- P0-3 expectation: default Dify behavior remains `dify_legacy`, and Dify is not treated as an `LLMProvider`.
- Result: passed as part of the focused 2-test API run and `tests.test_records_api`.

**Records API/UI regression:**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_records_api -v
```

Result: Ran 88 tests, OK.

**Full suite regression:**

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Result: Ran 177 tests, OK.

**Public real-data check:**

- P0-3 used the existing synthetic evidence pack plus synthetic provider responses.
- No live public URL, live Dify call, external LLM call, or server deployment was run.
- The same public fixture gap from P0-2 remains: no existing minimized public evidence pack fixture was available in `tests/fixtures`.

## P0-4 Test Plan

**Stage:** Quality gate marks manual-review export

**Automated commands planned:**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_report_export tests.test_report_quality -v
.\.venv\Scripts\python.exe -m unittest tests.test_records_api.RecordsApiTests.test_analysis_run_download_allows_failed_quality_check_manual_review tests.test_records_api.RecordsApiTests.test_analysis_run_download_allows_quality_gate_manual_review tests.test_records_api.RecordsApiTests.test_analysis_run_download_exports_markdown_docx tests.test_records_api.RecordsApiTests.test_analysis_run_download_reuses_existing_docx_for_same_run_version tests.test_records_api.RecordsApiTests.test_analysis_run_uses_local_engine_when_configured -v
.\.venv\Scripts\python.exe -m unittest tests.test_16case_regression_script -v
.\.venv\Scripts\python.exe -m unittest tests.test_records_api -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

**Before baseline:**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_report_export tests.test_report_quality -v
```

Result: Ran 36 tests, OK.

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_records_api.RecordsApiTests.test_analysis_run_download_exports_markdown_docx tests.test_records_api.RecordsApiTests.test_analysis_run_download_reuses_existing_docx_for_same_run_version tests.test_records_api.RecordsApiTests.test_analysis_run_download_rejects_not_ready_report tests.test_records_api.RecordsApiTests.test_run_diagnostics_reports_source_fidelity_and_blocks_unsupported_facts tests.test_records_api.RecordsApiTests.test_run_diagnostics_detects_summary_only_report tests.test_records_api.RecordsApiTests.test_local_quality_gate_downgrades_finished_result_with_unsupported_fact -v
```

Result: Ran 6 tests, OK.

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_16case_regression_script -v
```

Result: Ran 12 tests, OK.

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_records_api -v
```

Result: Ran 88 tests, OK.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Result: Ran 177 tests, OK.

**TDD red checks:**

- `test_analysis_run_download_blocks_failed_quality_check` failed with HTTP 200 because a run with `quality_check.passed=false` still created a docx.
- `test_analysis_run_download_blocks_quality_gate_manual_review` failed with HTTP 200 because a run with `quality_gate.deliverable_status=needs_manual_review` still created a docx.

**Export precheck regression:**

- Original P0-4 expectation: failed QA and manual-review quality gates returned HTTP 409 `QUALITY_GATE_BLOCKED` before Word file creation.
- Revised 2026-07-06 expectation after real-environment smoke feedback: failed QA and manual-review quality gates keep diagnostics visible but return HTTP 200 on explicit Word download when report Markdown exists. Not-ready reports still return 409.
- Revised result:
  - `.\.venv\Scripts\python.exe -m unittest tests.test_records_api.RecordsApiTests.test_analysis_run_download_allows_failed_quality_check_manual_review tests.test_records_api.RecordsApiTests.test_analysis_run_download_allows_quality_gate_manual_review tests.test_records_api.RecordsApiTests.test_analysis_run_download_rejects_not_ready_report -v` -> Ran 3 tests, OK.
  - `.\.venv\Scripts\python.exe -m unittest tests.test_records_api.RecordsApiTests.test_analysis_run_download_allows_failed_quality_check_manual_review tests.test_records_api.RecordsApiTests.test_analysis_run_download_allows_quality_gate_manual_review tests.test_records_api.RecordsApiTests.test_analysis_run_download_exports_markdown_docx tests.test_records_api.RecordsApiTests.test_analysis_run_download_reuses_existing_docx_for_same_run_version tests.test_records_api.RecordsApiTests.test_analysis_run_download_rejects_not_ready_report tests.test_records_api.RecordsApiTests.test_records_ui_serves_static_page tests.test_records_api.RecordsApiTests.test_records_ui_matches_three_column_reference_layout tests.test_records_api.RecordsApiTests.test_fallback_attachment_key_fact_dicts_render_as_named_values -v` -> Ran 8 tests, OK.
  - `.\.venv\Scripts\python.exe -m unittest tests.test_records_api -v` -> Ran 91 tests, OK.
  - `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` -> Ran 180 tests, OK.

**Existing checked export regression:**

- P0-4 expectation: `/report/export_checked` strict-quality behavior remains intact.
- Result: passed. `.\.venv\Scripts\python.exe -m unittest tests.test_report_export tests.test_report_quality -v` ran 36 tests and returned OK.

**Records API/UI regression:**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_records_api -v
```

Result: Ran 90 tests, OK.

**Local 16-case script unit regression:**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_16case_regression_script -v
```

Result: Ran 12 tests, OK.

**Full suite regression:**

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Result: Ran 179 tests, OK.

**Evaluation script check:**

- `scripts/run_16case_report_regression.py` exists but defaults to `http://192.168.34.88:8099` and exercises the company service.
- `scripts/run_chatflow_acceptance.py` exists and uses live public URLs / Dify-style calls.
- Neither script was run in P0-4 because this stage explicitly avoided deployment, live Dify, external URLs, and company-server changes.

**Public real-data check:**

- P0-4 did not run live public URL checks.
- The same fixture gap remains: no existing minimized public evidence pack fixture was available in `tests/fixtures`.

## P1-1 Test Plan

**Stage:** MemoryItem v1

**Automated commands planned:**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_memory_items -v
.\.venv\Scripts\python.exe -m unittest tests.test_report_memory.ReportMemoryServiceTests.test_memory_items_api_enforces_token_and_persists_structured_items -v
.\.venv\Scripts\python.exe -m unittest tests.test_records_api.RecordsApiTests.test_analysis_run_injects_scoped_memory_items_and_records_used_ids -v
.\.venv\Scripts\python.exe -m unittest tests.test_memory_items tests.test_report_memory -v
.\.venv\Scripts\python.exe -m unittest tests.test_records_api.RecordsApiTests.test_analysis_run_with_memory_explicitly_false_keeps_legacy_dify_behavior tests.test_records_api.RecordsApiTests.test_analysis_run_passes_formal_memory_to_dify_when_requested tests.test_records_api.RecordsApiTests.test_analysis_run_truncates_oversized_existing_memory_without_blocking_generation tests.test_records_api.RecordsApiTests.test_call_dify_workflow_sends_memory_as_independent_start_variables tests.test_records_api.RecordsApiTests.test_analysis_run_injects_scoped_memory_items_and_records_used_ids -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

**Before baseline:**

- `.\.venv\Scripts\python.exe -m unittest tests.test_report_memory -v` -> Ran 5 tests, OK.
- Existing records memory regression command -> Ran 4 tests, OK.
- `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` -> Ran 180 tests, OK.

**TDD red checks:**

- Structured memory unit tests failed before `app.core.memory` existed.
- Memory item API test failed with HTTP 404 before `/memory/items/{item_id}` existed.
- Analysis-run injection test failed before scoped memory retrieval and `used_memory_ids` metadata existed.

**After implementation results:**

- `.\.venv\Scripts\python.exe -m unittest tests.test_memory_items -v` -> Ran 2 tests, OK.
- `.\.venv\Scripts\python.exe -m unittest tests.test_report_memory.ReportMemoryServiceTests.test_memory_items_api_enforces_token_and_persists_structured_items -v` -> Ran 1 test, OK.
- `.\.venv\Scripts\python.exe -m unittest tests.test_records_api.RecordsApiTests.test_analysis_run_injects_scoped_memory_items_and_records_used_ids -v` -> Ran 1 test, OK.
- `.\.venv\Scripts\python.exe -m unittest tests.test_memory_items tests.test_report_memory -v` -> Ran 8 tests, OK.
- Records memory regression command -> Ran 5 tests, OK.
- `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` -> Ran 184 tests, OK.

**Evaluation script check:**

- No P1-1-specific existing quantitative evaluation script was found.
- P1-2 is the planned stage for repeatable quality smoke metrics.

**Public real-data check:**

- P1-1 did not call live company services or external URLs because the stage is a local storage/API/prompt-injection shell.

## P1-2 Test Plan

**Stage:** Quality smoke evaluation

**Automated commands planned:**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_quality_smoke -v
.\.venv\Scripts\python.exe -m unittest tests.test_quality_smoke tests.test_16case_regression_script tests.test_report_quality -v
.\.venv\Scripts\python.exe -m unittest tests.test_memory_items tests.test_report_memory -v
.\.venv\Scripts\python.exe scripts\run_quality_smoke.py --fixtures-dir tests\eval_fixtures --output-dir reports\quality_smoke_p1_2_20260707_095340
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

**Before baseline:**

- `.\.venv\Scripts\python.exe -m unittest tests.test_16case_regression_script -v` -> Ran 12 tests, OK.
- `.\.venv\Scripts\python.exe -m unittest tests.test_report_quality -v` -> Ran 23 tests, OK.
- `.\.venv\Scripts\python.exe -m unittest tests.test_memory_items tests.test_report_memory -v` -> Ran 8 tests, OK.
- `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` -> Ran 184 tests, OK.

**TDD red checks:**

- `tests.test_quality_smoke` failed with `FileNotFoundError` before `scripts/run_quality_smoke.py` existed.
- Direct CLI test failed with `ModuleNotFoundError: No module named 'app'` before the runner inserted the repository root into `sys.path`.

**After implementation results:**

- `.\.venv\Scripts\python.exe -m unittest tests.test_quality_smoke -v` -> Ran 3 tests, OK.
- `.\.venv\Scripts\python.exe scripts\run_quality_smoke.py --fixtures-dir tests\eval_fixtures --output-dir reports\quality_smoke_p1_2_20260707_095340` -> wrote `quality_smoke_summary.json` and `quality_smoke_report.md`.
- `.\.venv\Scripts\python.exe -m unittest tests.test_quality_smoke tests.test_16case_regression_script tests.test_report_quality -v` -> Ran 38 tests, OK.
- `.\.venv\Scripts\python.exe -m unittest tests.test_memory_items tests.test_report_memory -v` -> Ran 8 tests, OK.
- `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` -> Ran 187 tests, OK.

**P1-2 smoke metrics:**

| Metric | Before P1-2 | After P1-2 |
|---|---:|---:|
| Offline smoke runner available | 0 | 1 |
| Fixed smoke cases | 0 | 3 |
| Deliverable count | N/A | 1 |
| Needs manual review count | N/A | 2 |
| Failed count | N/A | 0 |
| Deliverable rate | N/A | 0.3333 |
| Average coverage score | N/A | 100.0 |
| Average analysis depth score | N/A | 65.0 |
| Unsupported fact count | N/A | 1 |
| Summary-only count | N/A | 2 |

Blocking issue counts after P1-2: `SUMMARY_ONLY_REPORT=2`, `UNSUPPORTED_FACT=1`.

**Evaluation script check:**

- `scripts/run_16case_report_regression.py` and `scripts/run_chatflow_acceptance.py` still exist but were not run because they call live company services or external/public URLs.
- P1-2 added an offline runner for local prompt/workflow smoke checks.

**Public real-data check:**

- P1-2 did not call live public URLs or company services.

## P1-3 Test Plan

**Stage:** Evidence Pack v3 lite

**Automated commands planned:**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_evidence_v3_lite tests.test_records_api.RecordsApiTests.test_analysis_prepare_builds_and_persists_database_evidence_pack -v
.\.venv\Scripts\python.exe -m unittest tests.test_evidence_v3_lite tests.test_records_api tests.test_quality_smoke tests.test_report_quality -v
.\.venv\Scripts\python.exe scripts\run_quality_smoke.py --fixtures-dir tests\eval_fixtures --output-dir reports\quality_smoke_p1_3_before_20260707_101523
.\.venv\Scripts\python.exe scripts\run_quality_smoke.py --fixtures-dir tests\eval_fixtures --output-dir reports\quality_smoke_p1_3_after_<timestamp>
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

**Before baseline:**

- `.\.venv\Scripts\python.exe -m unittest tests.test_quality_smoke tests.test_16case_regression_script tests.test_report_quality tests.test_records_api.RecordsApiTests.test_analysis_prepare_builds_and_persists_database_evidence_pack -v` -> Ran 39 tests, OK.
- `.\.venv\Scripts\python.exe scripts\run_quality_smoke.py --fixtures-dir tests\eval_fixtures --output-dir reports\quality_smoke_p1_3_before_20260707_101523` -> wrote `quality_smoke_summary.json` and `quality_smoke_report.md`.
- `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` -> Ran 187 tests, OK.

**TDD red checks:**

- `tests.test_evidence_v3_lite` failed with `ModuleNotFoundError` before `app/core/evidence` existed.
- The `/analysis/prepare` focused regression failed with `KeyError: 'evidence_schema_version'` before generated packs were annotated.

**After implementation results so far:**

- `.\.venv\Scripts\python.exe -m unittest tests.test_evidence_v3_lite tests.test_records_api.RecordsApiTests.test_analysis_prepare_builds_and_persists_database_evidence_pack -v` -> Ran 3 tests, OK.
- `.\.venv\Scripts\python.exe -m unittest tests.test_evidence_v3_lite tests.test_records_api tests.test_quality_smoke tests.test_report_quality -v` -> Ran 120 tests, OK.
- `.\.venv\Scripts\python.exe scripts\run_quality_smoke.py --fixtures-dir tests\eval_fixtures --output-dir reports\quality_smoke_p1_3_after_20260707_102318` -> wrote `quality_smoke_summary.json` and `quality_smoke_report.md`.
- `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` -> Ran 189 tests, OK.

**P1-3 smoke metrics:**

| Metric | Before P1-3 | After P1-3 |
|---|---:|---:|
| Case count | 3 | 3 |
| Deliverable count | 1 | 1 |
| Needs manual review count | 2 | 2 |
| Failed count | 0 | 0 |
| Deliverable rate | 0.3333 | 0.3333 |
| Average coverage score | 100.0 | 100.0 |
| Average analysis depth score | 65.0 | 65.0 |
| Unsupported fact count | 1 | 1 |
| Summary-only count | 2 | 2 |
| Blocking issue counts | `SUMMARY_ONLY_REPORT=2`, `UNSUPPORTED_FACT=1` | `SUMMARY_ONLY_REPORT=2`, `UNSUPPORTED_FACT=1` |

**Evaluation script check:**

- P1-3 uses the offline `scripts/run_quality_smoke.py` metric runner added in P1-2.
- Live `scripts/run_16case_report_regression.py` and `scripts/run_chatflow_acceptance.py` were not run because this local stage does not call company services, public URLs, Dify, 8099, or 8100.

**Public real-data check:**

- P1-3 did not call live public URLs or company services. The stage used synthetic attachment summaries and existing offline fixtures only.

## P2-1 Test Plan

**Stage:** Retry/cancel/resume

**Automated commands planned:**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_workflow_run_store tests.test_local_workflow_engine tests.test_records_api.RecordsApiTests.test_analysis_run_cancel_marks_running_run_cancelled tests.test_records_api.RecordsApiTests.test_analysis_run_retry_failed_run_starts_linked_new_run tests.test_records_api.RecordsApiTests.test_analysis_run_resume_cancelled_run_starts_linked_new_run_and_checks_state tests.test_records_api.RecordsApiTests.test_analysis_run_calls_dify_and_persists_report tests.test_records_api.RecordsApiTests.test_analysis_run_uses_local_engine_when_configured tests.test_records_api.RecordsApiTests.test_analysis_run_injects_scoped_memory_items_and_records_used_ids tests.test_records_api.RecordsApiTests.test_analysis_run_download_exports_markdown_docx tests.test_records_api.RecordsApiTests.test_run_diagnostics_reports_source_fidelity_and_blocks_unsupported_facts -v
.\.venv\Scripts\python.exe scripts\run_quality_smoke.py --fixtures-dir tests\eval_fixtures --output-dir reports\quality_smoke_p2_1_before_20260707_162806
.\.venv\Scripts\python.exe scripts\run_quality_smoke.py --fixtures-dir tests\eval_fixtures --output-dir reports\quality_smoke_p2_1_after_20260707_163421
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

**Before baseline:**

- Focused workflow/store/local-engine/API baseline -> Ran 15 tests, OK.
- `.\.venv\Scripts\python.exe scripts\run_quality_smoke.py --fixtures-dir tests\eval_fixtures --output-dir reports\quality_smoke_p2_1_before_20260707_162806` -> wrote `quality_smoke_summary.json` and `quality_smoke_report.md`.
- `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` -> Ran 189 tests, OK.

**TDD red checks:**

- `test_analysis_run_cancel_marks_running_run_cancelled` failed with HTTP 404 before `/analysis/runs/{run_id}/cancel` existed.
- `test_analysis_run_retry_failed_run_starts_linked_new_run` failed with HTTP 404 before `/analysis/runs/{run_id}/retry` existed.
- `test_analysis_run_resume_cancelled_run_starts_linked_new_run_and_checks_state` failed with HTTP 404 before `/analysis/runs/{run_id}/resume` existed.

**After implementation results:**

- New P2-1 control API tests -> Ran 3 tests, OK.
- Focused workflow/run regression command -> Ran 19 tests, OK.
- `.\.venv\Scripts\python.exe scripts\run_quality_smoke.py --fixtures-dir tests\eval_fixtures --output-dir reports\quality_smoke_p2_1_after_20260707_163421` -> wrote `quality_smoke_summary.json` and `quality_smoke_report.md`.
- `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` -> Ran 192 tests, OK.

**P2-1 smoke metrics:**

| Metric | Before P2-1 | After P2-1 |
|---|---:|---:|
| Case count | 3 | 3 |
| Deliverable count | 1 | 1 |
| Needs manual review count | 2 | 2 |
| Failed count | 0 | 0 |
| Deliverable rate | 0.3333 | 0.3333 |
| Average coverage score | 100.0 | 100.0 |
| Average analysis depth score | 65.0 | 65.0 |
| Unsupported fact count | 1 | 1 |
| Summary-only count | 2 | 2 |
| Blocking issue counts | `SUMMARY_ONLY_REPORT=2`, `UNSUPPORTED_FACT=1` | `SUMMARY_ONLY_REPORT=2`, `UNSUPPORTED_FACT=1` |

**Evaluation script check:**

- P2-1 reused the offline `scripts/run_quality_smoke.py` metric runner added in P1-2.
- Live `scripts/run_16case_report_regression.py` and `scripts/run_chatflow_acceptance.py` were not run because this local stage does not call company services, public URLs, Dify, 8099, or 8100.

**Public real-data check:**

- P2-1 did not call live public URLs or company services. The stage used synthetic run records and existing offline quality fixtures.

## P2-2 Test Plan

**Stage:** Idempotency and recovery

**Automated commands planned:**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_workflow_run_store.WorkflowRunStoreTests.test_list_runs_returns_valid_records_and_skips_corrupt_files tests.test_records_api.RecordsApiTests.test_analysis_run_reuses_active_duplicate_submission_by_input_hash tests.test_records_api.RecordsApiTests.test_recover_pending_analysis_runs_marks_stale_active_runs_failed -v
.\.venv\Scripts\python.exe -m unittest tests.test_workflow_run_store tests.test_records_api.RecordsApiTests.test_analysis_run_reuses_active_duplicate_submission_by_input_hash tests.test_records_api.RecordsApiTests.test_recover_pending_analysis_runs_marks_stale_active_runs_failed tests.test_records_api.RecordsApiTests.test_analysis_run_cancel_marks_running_run_cancelled tests.test_records_api.RecordsApiTests.test_analysis_run_retry_failed_run_starts_linked_new_run tests.test_records_api.RecordsApiTests.test_analysis_run_resume_cancelled_run_starts_linked_new_run_and_checks_state tests.test_records_api.RecordsApiTests.test_analysis_run_calls_dify_and_persists_report tests.test_records_api.RecordsApiTests.test_analysis_run_uses_local_engine_when_configured -v
.\.venv\Scripts\python.exe scripts\run_quality_smoke.py --fixtures-dir tests\eval_fixtures --output-dir reports\quality_smoke_p2_2_before_20260707_164809
.\.venv\Scripts\python.exe scripts\run_quality_smoke.py --fixtures-dir tests\eval_fixtures --output-dir reports\quality_smoke_p2_2_after_20260707_165527
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

**Before baseline:**

- Focused workflow/run baseline -> Ran 13 tests, OK.
- `.\.venv\Scripts\python.exe scripts\run_quality_smoke.py --fixtures-dir tests\eval_fixtures --output-dir reports\quality_smoke_p2_2_before_20260707_164809` -> wrote `quality_smoke_summary.json` and `quality_smoke_report.md`.
- `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` -> Ran 192 tests, OK.

**TDD red checks:**

- `test_list_runs_returns_valid_records_and_skips_corrupt_files` failed with `AttributeError` before `WorkflowRunStore.list_runs` existed.
- `test_analysis_run_reuses_active_duplicate_submission_by_input_hash` failed because duplicate active submissions returned different run IDs before input-hash reuse existed.
- `test_recover_pending_analysis_runs_marks_stale_active_runs_failed` failed because stale pending-run recovery did not exist.

**After implementation results:**

- New P2-2 idempotency/recovery tests -> Ran 3 tests, OK.
- Focused workflow/run regression command -> Ran 16 tests, OK.
- `.\.venv\Scripts\python.exe scripts\run_quality_smoke.py --fixtures-dir tests\eval_fixtures --output-dir reports\quality_smoke_p2_2_after_20260707_165527` -> wrote `quality_smoke_summary.json` and `quality_smoke_report.md`.
- `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` -> Ran 195 tests, OK.
- `git diff --check` -> no whitespace errors; only Windows LF-to-CRLF warnings.

**P2-2 smoke metrics:**

| Metric | Before P2-2 | After P2-2 |
|---|---:|---:|
| Case count | 3 | 3 |
| Deliverable count | 1 | 1 |
| Needs manual review count | 2 | 2 |
| Failed count | 0 | 0 |
| Deliverable rate | 0.3333 | 0.3333 |
| Average coverage score | 100.0 | 100.0 |
| Average analysis depth score | 65.0 | 65.0 |
| Unsupported fact count | 1 | 1 |
| Summary-only count | 2 | 2 |
| Blocking issue counts | `SUMMARY_ONLY_REPORT=2`, `UNSUPPORTED_FACT=1` | `SUMMARY_ONLY_REPORT=2`, `UNSUPPORTED_FACT=1` |

**Evaluation script check:**

- P2-2 reused the offline `scripts/run_quality_smoke.py` metric runner added in P1-2.
- Live `scripts/run_16case_report_regression.py` and `scripts/run_chatflow_acceptance.py` were not run because this local stage does not call company services, public URLs, Dify, 8099, or 8100.

**Public real-data check:**

- P2-2 did not call live public URLs or company services. The stage used synthetic run records and existing offline quality fixtures.

## P2-2.5 Test Plan

**Stage:** Material Compression Cache

**Automated commands planned:**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_material_compression_cache tests.test_material_compression_prebuild_script tests.test_records_api.RecordsApiTests.test_analysis_prepare_uses_material_full_cache_hit_and_recomputes_role_context tests.test_records_api.RecordsApiTests.test_analysis_prepare_falls_back_when_material_cache_is_corrupt tests.test_records_api.RecordsApiTests.test_material_cache_check_and_build_selected_materials_api tests.test_records_api.RecordsApiTests.test_records_ui_defaults_to_cached_prepare_and_has_reparse_button -v
.\.venv\Scripts\python.exe -m unittest tests.test_material_compression_cache tests.test_material_compression_prebuild_script tests.test_records_api.RecordsApiTests.test_analysis_prepare_builds_and_persists_database_evidence_pack tests.test_records_api.RecordsApiTests.test_analysis_prepare_downloads_attachment_by_default_without_cookie tests.test_records_api.RecordsApiTests.test_analysis_prepare_can_disable_attachment_download_per_request tests.test_records_api.RecordsApiTests.test_analysis_prepare_uses_request_cookie_to_download_and_parse_attachment tests.test_records_api.RecordsApiTests.test_pack_diagnostics_marks_short_body_with_rich_core_attachment_as_attachment_led tests.test_records_api.RecordsApiTests.test_material_cache_check_and_build_selected_materials_api tests.test_records_api.RecordsApiTests.test_records_ui_defaults_to_cached_prepare_and_has_reparse_button tests.test_workflow_run_store tests.test_quality_smoke -v
.\.venv\Scripts\python.exe scripts\run_quality_smoke.py --fixtures-dir tests\eval_fixtures --output-dir reports\quality_smoke_p2_2_5_before_20260707_170231
.\.venv\Scripts\python.exe scripts\run_quality_smoke.py --fixtures-dir tests\eval_fixtures --output-dir reports\quality_smoke_p2_2_5_after_20260707_172530
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

**Before baseline:**

- P2-2/prepare focused baseline -> Ran 15 tests, OK.
- `.\.venv\Scripts\python.exe scripts\run_quality_smoke.py --fixtures-dir tests\eval_fixtures --output-dir reports\quality_smoke_p2_2_5_before_20260707_170231` -> wrote `quality_smoke_summary.json` and `quality_smoke_report.md`.
- `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` -> Ran 195 tests, OK.

**TDD red checks:**

- Repository/compressor tests failed with missing `app.material_compression_cache` and `app.material_compressor`.
- Prebuild script test failed because `scripts/prebuild_material_compression_cache.py` did not exist.
- Selected-material cache API test failed with HTTP 404 before `/analysis/material-cache/check` and `/analysis/material-cache/build` existed.

**After implementation results:**

- New P2-2.5 focused tests -> Ran 10 tests, OK.
- Related prepare/diagnostics/UI/workflow-store/quality-smoke tests -> Ran 25 tests, OK.
- `.\.venv\Scripts\python.exe scripts\run_quality_smoke.py --fixtures-dir tests\eval_fixtures --output-dir reports\quality_smoke_p2_2_5_after_20260707_172530` -> wrote `quality_smoke_summary.json` and `quality_smoke_report.md`.
- `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` -> Ran 204 tests, OK.
- `git diff --check` -> no whitespace errors; only Windows LF-to-CRLF warnings.

**P2-2.5 smoke metrics:**

| Metric | Before P2-2.5 | After P2-2.5 |
|---|---:|---:|
| Case count | 3 | 3 |
| Deliverable count | 1 | 1 |
| Needs manual review count | 2 | 2 |
| Failed count | 0 | 0 |
| Deliverable rate | 0.3333 | 0.3333 |
| Average coverage score | 100.0 | 100.0 |
| Average analysis depth score | 65.0 | 65.0 |
| Unsupported fact count | 1 | 1 |
| Summary-only count | 2 | 2 |
| Blocking issue counts | `SUMMARY_ONLY_REPORT=2`, `UNSUPPORTED_FACT=1` | `SUMMARY_ONLY_REPORT=2`, `UNSUPPORTED_FACT=1` |

**Local timing check:**

| Check | Result |
|---|---:|
| Synthetic dynamic prepare | 158.70 ms |
| Synthetic prebuilt `material/full` cache-hit prepare | 22.03 ms |
| Material cache hit count | 1 |
| Material cache dynamic fallback count | 0 |

The timing check used a local synthetic material and patched attachment metadata delay. It does not replace real 8100 fixed-case performance validation.

**Evaluation script check:**

- P2-2.5 reused the offline `scripts/run_quality_smoke.py` metric runner added in P1-2.
- Live `scripts/run_16case_report_regression.py` and `scripts/run_chatflow_acceptance.py` were not run because this local stage does not call company services, public URLs, Dify, 8099, or 8100.

**Public real-data check:**

- P2-2.5 did not call live public URLs or company services. The stage used synthetic rows, local SQLite, and existing offline quality fixtures.

## P2-3 Test Plan

**Stage:** Cleanup, health, diagnostics

**Automated commands planned:**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_report_export.DeploymentConfigTests tests.test_records_api.RecordsApiTests.test_pack_diagnostics_reports_evidence_level_and_attachment_status tests.test_records_api.RecordsApiTests.test_analysis_run_page_progress_and_diagnostics tests.test_workflow_run_store tests.test_material_compression_cache tests.test_material_compression_prebuild_script -v
.\.venv\Scripts\python.exe scripts\run_quality_smoke.py --fixtures-dir tests\eval_fixtures --output-dir reports\quality_smoke_p2_3_before_20260708_1000
.\.venv\Scripts\python.exe -m unittest tests.test_operations_health -v
.\.venv\Scripts\python.exe -m unittest tests.test_operations_health tests.test_report_export.DeploymentConfigTests tests.test_records_api.RecordsApiTests.test_pack_diagnostics_reports_evidence_level_and_attachment_status tests.test_records_api.RecordsApiTests.test_analysis_run_page_progress_and_diagnostics tests.test_workflow_run_store tests.test_material_compression_cache tests.test_material_compression_prebuild_script tests.test_quality_smoke -v
.\.venv\Scripts\python.exe scripts\run_quality_smoke.py --fixtures-dir tests\eval_fixtures --output-dir reports\quality_smoke_p2_3_after_20260708_1006
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

**Before baseline:**

- P2-3 focused health/diagnostics/run-store/material-cache baseline -> Ran 19 tests, OK.
- `.\.venv\Scripts\python.exe scripts\run_quality_smoke.py --fixtures-dir tests\eval_fixtures --output-dir reports\quality_smoke_p2_3_before_20260708_1000` -> wrote `quality_smoke_summary.json` and `quality_smoke_report.md`.
- `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` -> Ran 204 tests, OK.

**TDD red checks:**

- New P2-3 operations tests initially failed with HTTP 404 for `/health/db`, `/health/llm`, `/health/storage`, `/ops/cleanup`, `/ops/diagnostics`, and `/ops-diagnostics-ui`.
- One post-implementation test fixture was corrected after proving the endpoint properly returned `unconfigured` when the DB check was requested without DB configuration.

**After implementation results:**

- New P2-3 focused operations tests -> Ran 5 tests, OK.
- Related operations/deployment/pack-run diagnostics/workflow-store/material-cache/quality-smoke tests -> Ran 27 tests, OK.
- `.\.venv\Scripts\python.exe scripts\run_quality_smoke.py --fixtures-dir tests\eval_fixtures --output-dir reports\quality_smoke_p2_3_after_20260708_1006` -> wrote `quality_smoke_summary.json` and `quality_smoke_report.md`.
- `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` -> Ran 209 tests, OK.

**P2-3 smoke metrics:**

| Metric | Before P2-3 | After P2-3 |
|---|---:|---:|
| Case count | 3 | 3 |
| Deliverable count | 1 | 1 |
| Needs manual review count | 2 | 2 |
| Failed count | 0 | 0 |
| Deliverable rate | 0.3333 | 0.3333 |
| Average coverage score | 100.0 | 100.0 |
| Average analysis depth score | 65.0 | 65.0 |
| Unsupported fact count | 1 | 1 |
| Summary-only count | 2 | 2 |
| Blocking issue counts | `SUMMARY_ONLY_REPORT=2`, `UNSUPPORTED_FACT=1` | `SUMMARY_ONLY_REPORT=2`, `UNSUPPORTED_FACT=1` |

**Evaluation script check:**

- P2-3 reused the offline `scripts/run_quality_smoke.py` metric runner added in P1-2.
- Live `scripts/run_16case_report_regression.py` and `scripts/run_chatflow_acceptance.py` were not run because this local stage does not call company services, public URLs, Dify, 8099, or 8100.

**Public real-data check:**

- P2-3 did not call live public URLs, company services, Dify, production `8099`, or standby `8100`. The stage used synthetic stale report/cache files and existing offline quality fixtures.

## Metrics

Detailed quality metrics such as QA pass rate, hallucination count, attachment coverage, table accuracy, history leakage, elapsed time, repair success, and export success start in P1-2. P0-1 and P0-2 only record persistence, compatibility, and local workflow pass/fail results.

P0-1 did not find an applicable existing quantitative evaluation metric to run, so no synthetic metric was added.

P0-2 did not find an applicable existing quantitative evaluation metric to run, so no synthetic metric was added. Test-count movement is recorded only as regression coverage evidence: full discovery changed from 170 tests before P0-2 to 172 tests after P0-2.

P0-3 did not find an applicable existing quantitative evaluation metric to run, so no synthetic metric was added. Test-count movement is recorded only as regression coverage evidence: full discovery changed from 172 tests before P0-3 to 177 tests after P0-3.

P0-4 did not run a standalone quantitative evaluation metric. Existing live evaluation scripts were inspected but not executed because they require external/company services. Test-count movement is recorded only as regression coverage evidence: full discovery changed from 177 tests before P0-4 to 179 tests after P0-4.

P1-1 did not find an applicable existing quantitative evaluation metric to run, so no synthetic metric was added. Test-count movement is recorded only as regression coverage evidence: full discovery changed from 180 tests before P1-1 to 184 tests after P1-1.

P1-2 added the first offline quality smoke metric runner. Before P1-2 there was no local P1-2 smoke metric output. After P1-2, the 3-case fixture smoke run produced deliverable rate 0.3333, unsupported fact count 1, average coverage score 100.0, and average analysis depth score 65.0. Test-count movement is recorded as regression coverage evidence: full discovery changed from 184 tests before P1-2 to 187 tests after P1-2.

P1-3 reuses the P1-2 offline smoke metric runner to check for drift after adding evidence traceability metadata. Before P1-3, the smoke run produced deliverable rate 0.3333, unsupported fact count 1, average coverage score 100.0, and average analysis depth score 65.0. After P1-3, the same metrics remained unchanged. Test-count movement is recorded as regression coverage evidence: full discovery changed from 187 tests before P1-3 to 189 tests after P1-3.

P2-1 reuses the P1-2 offline smoke metric runner to check for drift after adding run-control endpoints. Before P2-1, the smoke run produced deliverable rate 0.3333, unsupported fact count 1, average coverage score 100.0, and average analysis depth score 65.0. After P2-1, the same metrics remained unchanged. Test-count movement is recorded as regression coverage evidence: full discovery changed from 189 tests before P2-1 to 192 tests after P2-1.

P2-2 reuses the P1-2 offline smoke metric runner to check for drift after adding input-hash idempotency and stale-run recovery. Before P2-2, the smoke run produced deliverable rate 0.3333, unsupported fact count 1, average coverage score 100.0, and average analysis depth score 65.0. After P2-2, the same metrics remained unchanged. Test-count movement is recorded as regression coverage evidence: full discovery changed from 192 tests before P2-2 to 195 tests after P2-2.

P2-2.5 reuses the P1-2 offline smoke metric runner to check for drift after adding the material compression cache shell. Before P2-2.5, the smoke run produced deliverable rate 0.3333, unsupported fact count 1, average coverage score 100.0, and average analysis depth score 65.0. After P2-2.5, the same metrics remained unchanged. A local synthetic prepare timing check measured dynamic prepare at 158.70 ms and prebuilt `material/full` cache-hit prepare at 22.03 ms. Test-count movement is recorded as regression coverage evidence: full discovery changed from 195 tests before P2-2.5 to 204 tests after P2-2.5.

P2-3 reuses the P1-2 offline smoke metric runner to check for drift after adding operations health, bounded cleanup, and diagnostics endpoints. Before P2-3, the smoke run produced deliverable rate 0.3333, unsupported fact count 1, average coverage score 100.0, and average analysis depth score 65.0. After P2-3, the same metrics remained unchanged. Test-count movement is recorded as regression coverage evidence: full discovery changed from 204 tests before P2-3 to 209 tests after P2-3.
