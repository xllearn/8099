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

**Stage:** Quality gate blocks export

**Automated commands planned:**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_report_export tests.test_report_quality -v
.\.venv\Scripts\python.exe -m unittest tests.test_records_api.RecordsApiTests.test_analysis_run_download_blocks_failed_quality_check tests.test_records_api.RecordsApiTests.test_analysis_run_download_blocks_quality_gate_manual_review tests.test_records_api.RecordsApiTests.test_analysis_run_download_exports_markdown_docx tests.test_records_api.RecordsApiTests.test_analysis_run_download_reuses_existing_docx_for_same_run_version tests.test_records_api.RecordsApiTests.test_analysis_run_uses_local_engine_when_configured -v
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

- P0-4 expectation: failed QA and manual-review quality gates return HTTP 409 `QUALITY_GATE_BLOCKED` before Word file creation.
- Result: passed. The focused download run returned 409 for both blocked cases and 200 for QA-passed explicit download cases.

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

## Metrics

Detailed quality metrics such as QA pass rate, hallucination count, attachment coverage, table accuracy, history leakage, elapsed time, repair success, and export success start in P1-2. P0-1 and P0-2 only record persistence, compatibility, and local workflow pass/fail results.

P0-1 did not find an applicable existing quantitative evaluation metric to run, so no synthetic metric was added.

P0-2 did not find an applicable existing quantitative evaluation metric to run, so no synthetic metric was added. Test-count movement is recorded only as regression coverage evidence: full discovery changed from 170 tests before P0-2 to 172 tests after P0-2.

P0-3 did not find an applicable existing quantitative evaluation metric to run, so no synthetic metric was added. Test-count movement is recorded only as regression coverage evidence: full discovery changed from 172 tests before P0-3 to 177 tests after P0-3.

P0-4 did not run a standalone quantitative evaluation metric. Existing live evaluation scripts were inspected but not executed because they require external/company services. Test-count movement is recorded only as regression coverage evidence: full discovery changed from 177 tests before P0-4 to 179 tests after P0-4.
