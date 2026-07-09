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

## P0 release smoke hardening: fallback key facts rendering

**Date:** 2026-07-06

**Context:** During standby-port smoke testing on `8100`, a fallback report showed raw Python dict literals such as `{'name': '...', 'value': '...'}` in the attachment key-facts section.

**Root cause:** `_fallback_report_from_pack` rendered attachment-level `key_facts` with `str(item)`. When parsed attachment facts were stored as dictionaries, the fallback Markdown leaked Python object formatting into the report body.

**Implemented state:**

- Added `_fallback_key_fact_text` to render dict facts as `name:value` style text and keep string facts compatible.
- Updated fallback attachment key-fact rendering to use the formatter and trim long facts with the existing snippet helper.
- Added a regression test for dict-based attachment key facts.

**Verification:**

- Red test observed:
  - `.\.venv\Scripts\python.exe -m unittest tests.test_records_api.RecordsApiTests.test_fallback_attachment_key_fact_dicts_render_as_named_values -v` -> failed because Markdown contained raw `{'name': ..., 'value': ...}`.
- After implementation:
  - `.\.venv\Scripts\python.exe -m unittest tests.test_records_api.RecordsApiTests.test_fallback_attachment_key_fact_dicts_render_as_named_values -v` -> Ran 1 test, OK.
  - `.\.venv\Scripts\python.exe -m unittest tests.test_records_api.RecordsApiTests.test_fallback_attachment_key_fact_dicts_render_as_named_values tests.test_records_api.RecordsApiTests.test_unusable_dify_report_gets_fallback_from_evidence_pack tests.test_records_api.RecordsApiTests.test_list_starting_dify_report_without_structure_gets_fallback tests.test_records_api.RecordsApiTests.test_fragmentary_dify_revision_gets_fallback_from_attachment_rich_pack tests.test_records_api.RecordsApiTests.test_report_starting_from_second_section_is_treated_as_fragment tests.test_records_api.RecordsApiTests.test_analysis_run_download_blocks_failed_quality_check tests.test_records_api.RecordsApiTests.test_analysis_run_download_blocks_quality_gate_manual_review -v` -> Ran 7 tests, OK.
  - `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` -> Ran 180 tests, OK.

**Metrics:** No existing quantitative evaluation metric applies to this rendering defect. No synthetic metric was added.

**Remaining risks:** This fix only changes backend fallback Markdown generation. Existing run JSON files generated before the fix will still contain old Markdown until the report is regenerated.

## P0 release smoke comparison: 8100 vs 8099 on five real records

**Date:** 2026-07-06

**Context:** After deploying the fallback key-fact rendering hotfix to standby port `8100`, two parallel subagents ran the same five real database records against `8100` and production `8099` for comparison.

**Sample records:**

- `ylsf/b10e3e1f-bea5-4d3b-ada6-124be038b8a9` 河源市麻醉类等医疗服务项目价格征求意见
- `lxzn/d6fae12f-20e2-4448-a194-c4cc3e8ece54` 南宁市16批医疗服务立项指南映射关系表
- `ylsf/bf514b7e-d665-4059-8a8a-eb6e76f93b8b` 广西药学类医疗服务价格征求意见
- `ylsf/805d2610-6610-41e9-98ac-1f54d65b05eb` 安徽十二类医疗服务价格项目映射关系表
- `ylsf/5340b5c6-6faf-4a7c-aa25-ac5445c95e8f` 贵州医疗服务价格项目映射表

**8100 standby result:**

- `/health` and `/records-ui` returned OK.
- All five records prepared evidence packs and completed analysis runs without timeout.
- All five runs ended as `needs_manual_review`.
- All five reports had `raw_dict_leak=false`.
- Diagnostics were available for all five runs.
- All five Word download attempts returned `409 QUALITY_GATE_BLOCKED`, which matches the P0-4 quality-gated export behavior.

**8099 production comparison:**

- `/health` and `/records-ui` returned OK.
- All five records prepared evidence packs and completed analysis runs without timeout.
- All five runs ended as `needs_manual_review`.
- The same five reports did not reproduce raw dict leakage in this sample set.
- Diagnostics were not available or not accepted by the subagent as OK.
- All five Word download attempts returned `200`, even though the runs were `needs_manual_review`.
- Case 4 and case 5 hit `DIFY_WORKFLOW_FAILED`, reported as `evidence_pack_json` exceeding the 80000 character workflow limit before fallback.

**Comparison summary:**

- 8100 improves export safety: manual-review reports are blocked from Word download.
- 8100 improves workflow resilience for the two large attachment-heavy samples: no `DIFY_WORKFLOW_FAILED` was reported in this run.
- The five real samples did not reproduce raw dict leakage on 8099, so this comparison does not prove the raw-dict bug exists in all production fallback runs. The hotfix remains covered by the local regression test and synthetic 8100 smoke run.
- Both environments still produced only `needs_manual_review` reports for this sample set, so report quality remains the main release risk.

**Remaining risks:** This comparison was a five-case smoke test, not a full regression or acceptance evaluation. It confirms export-gate behavior and basic generation flow but does not prove final report quality is acceptable for production rollout.

## P0 release behavior revision: allow manual-review Word download

**Date:** 2026-07-06

**Decision:** After the 8100/8099 five-record smoke comparison, the product export rule changed. Reports in `needs_manual_review`, including reports with failed QA or manual-review quality gates, must remain downloadable as Word files when the user explicitly clicks download.

**Why:** Operators need Word artifacts for manual review, offline correction, and circulation even when diagnostics show the report is not ready for direct delivery. Download availability must not be interpreted as quality approval.

**Planned implementation:**

- Keep quality diagnostics, warnings, remaining issues, and `needs_manual_review` status visible.
- Allow `/analysis/runs/{run_id}/download` when `report_markdown` exists.
- Continue to block not-ready runs with no report body.
- Update static UI buttons so manual-review reports with report Markdown can be downloaded.
- Deploy the revised behavior only to standby port `8100` first and rerun the same five-record comparison against `8100` and `8099`.

**Implemented state:**

- `analysis_run_export_precheck` now blocks only missing report bodies.
- `quality_check.passed=false`, `quality_gate.deliverable_status=needs_manual_review`, and run `status=needs_manual_review` no longer block `/analysis/runs/{run_id}/download`.
- `analysis_run.html` and `records.html` no longer disable Word download buttons solely because quality status is manual-review or failed.

**Verification:**

- Red tests observed:
  - `.\.venv\Scripts\python.exe -m unittest tests.test_records_api.RecordsApiTests.test_analysis_run_download_allows_failed_quality_check_manual_review tests.test_records_api.RecordsApiTests.test_analysis_run_download_allows_quality_gate_manual_review -v` -> failed with HTTP 409 under the old blocking behavior.
- After implementation:
  - `.\.venv\Scripts\python.exe -m unittest tests.test_records_api.RecordsApiTests.test_analysis_run_download_allows_failed_quality_check_manual_review tests.test_records_api.RecordsApiTests.test_analysis_run_download_allows_quality_gate_manual_review tests.test_records_api.RecordsApiTests.test_analysis_run_download_rejects_not_ready_report -v` -> Ran 3 tests, OK.
  - `.\.venv\Scripts\python.exe -m unittest tests.test_records_api.RecordsApiTests.test_analysis_run_download_allows_failed_quality_check_manual_review tests.test_records_api.RecordsApiTests.test_analysis_run_download_allows_quality_gate_manual_review tests.test_records_api.RecordsApiTests.test_analysis_run_download_exports_markdown_docx tests.test_records_api.RecordsApiTests.test_analysis_run_download_reuses_existing_docx_for_same_run_version tests.test_records_api.RecordsApiTests.test_analysis_run_download_rejects_not_ready_report tests.test_records_api.RecordsApiTests.test_records_ui_serves_static_page tests.test_records_api.RecordsApiTests.test_records_ui_matches_three_column_reference_layout tests.test_records_api.RecordsApiTests.test_fallback_attachment_key_fact_dicts_render_as_named_values -v` -> Ran 8 tests, OK.
  - `.\.venv\Scripts\python.exe -m unittest tests.test_report_export tests.test_report_quality -v` -> Ran 36 tests, OK.
  - `.\.venv\Scripts\python.exe -m unittest tests.test_16case_regression_script -v` -> Ran 12 tests, OK.
  - `.\.venv\Scripts\python.exe -m unittest tests.test_records_api -v` -> Ran 91 tests, OK.
  - `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` -> Ran 180 tests, OK.

**Remaining risks:** Users can download low-quality reports. The UI and diagnostics must continue to communicate that these reports require review.

## P0 release smoke comparison after manual-review download revision

**Date:** 2026-07-06

**Deployment:** The revised manual-review download behavior was deployed only to standby port `8100`.

- Package: `medical-notice-analyzer-p0-manual-review-download-20260706131343-a732fa1bfc21-worktree.tar.gz`
- SHA256: `C3014905B514C415D391EDF5B13C941D46FD5D704E1D25F3EDC6D9082AE4BAE6`
- Server release directory: `/opt/medical-notice-analyzer/releases/p0-manual-review-download-20260706131343-a732fa1bfc21-worktree`
- Production port `8099` was not changed.

**8100 standby result on five real records:**

- `/health` and `/records-ui` returned OK.
- All five records prepared packs and completed runs without timeout.
- All five runs ended as `needs_manual_review`.
- All five reports had `raw_dict_leak=false`.
- All five diagnostics endpoints returned OK.
- All five Word downloads returned `200` with docx content, proving the revised explicit-download behavior.

**8099 production comparison on the same five records:**

- `/health` and `/records-ui` returned OK.
- All five records prepared packs and completed runs without timeout.
- All five runs ended as `needs_manual_review`.
- All five reports had `raw_dict_leak=false`.
- All five diagnostics endpoints returned OK.
- All five Word downloads returned `200`.
- Case 4 and case 5 still reported Dify workflow failure due to `evidence_pack_json` exceeding the 80000-character workflow limit.

**Manual mojibake verification:** Both subagents reported possible mojibake in displayed report text. A direct Python UTF-8 fetch of all ten `/analysis/runs/{run_id}/report` responses showed normal Chinese starts such as `## 导语`, `mojibake_marker=false`, and `raw_dict=false`. The mojibake finding is therefore treated as a client/terminal display artifact, not a confirmed service-output defect.

**Comparison summary:**

- 8100 now matches the revised product requirement: manual-review reports can be downloaded explicitly.
- 8100 still avoids the Dify workflow failure observed on 8099 for the two large attachment-heavy samples.
- Neither environment produced direct-delivery quality reports in this sample set; all five remained `needs_manual_review`.
- The main remaining release risk is report quality, not Word export availability.

## Deployment policy update: 8100-only iteration before production cutover

**Date:** 2026-07-07

**Decision:** Until the full staged implementation plan is completed and a separate production cutover is explicitly approved, all further implementation validation, Dify workflow experiments, smoke testing, and fixes must target standby port `8100` only. Production port `8099` remains unchanged and serves as the stable comparison baseline.

**Operational rule:**

- Do not modify the `8099` production container, production compose/runtime configuration, production Dify workflow, or production Dify key during P1/P2/P3 work.
- Use untracked server-side runtime configuration for 8100-only Dify workflow keys; never commit keys, tokens, or server secrets to source or docs.
- Continue comparing fixed-case 8099 vs 8100 behavior before any future cutover recommendation.

**Immediate action:** Connect 8100 to the new Dify workflow copy and rerun the fixed 10-case 8099-vs-8100 real-environment smoke comparison without changing 8099.

## Dify workflow copy on 8100 and fixed 10-case comparison

**Date:** 2026-07-07

**Action:** Standby port `8100` was connected to the Dify workflow copy with App ID `9a5dc7e6-5400-4891-a995-d809c5e0dbca` by adding an untracked server-side runtime env override in the current 8100 release directory. Production port `8099` was not changed.

**Verification artifacts:**

- Full JSONL result: `C:\Users\admin\Documents\htmldataconclusion\deploy-packages\fixed10_compare_20260707_084233.jsonl`
- Markdown summary: `C:\Users\admin\Documents\htmldataconclusion\deploy-packages\fixed10_compare_20260707_084233.summary.md`

**Health check:**

- `http://192.168.34.88:8099/health` returned `200 ok` with `public_base_url=http://192.168.34.88:8099`.
- `http://192.168.34.88:8100/health` returned `200 ok` with `public_base_url=http://192.168.34.88:8100`.

**Fixed 10-case result summary:**

- `8099`: 10/10 runs ended in `needs_manual_review`; 10/10 Word downloads succeeded; `evidence_pack has no primary_materials` appeared 0 times; `Q_DIFY_FRAGMENTARY_REPORT` appeared 2 times; `UNSUPPORTED_FACT` appeared 3 times; `OUTPUT_MAY_BE_TRUNCATED` appeared 2 times; raw dict leak and mojibake markers appeared 0 times.
- `8100`: 10/10 runs ended in `needs_manual_review`; 10/10 Word downloads succeeded; `evidence_pack has no primary_materials` appeared 0 times; `Q_DIFY_FRAGMENTARY_REPORT` appeared 1 time; `UNSUPPORTED_FACT` appeared 6 times; `OUTPUT_MAY_BE_TRUNCATED` appeared 0 times; raw dict leak and mojibake markers appeared 0 times.

**Interpretation:** The 8100 Dify workflow copy removed the previous no-primary-materials failure and reduced fragmentary/truncation indicators in this 10-case run, but it did not eliminate `Q_DIFY_FRAGMENTARY_REPORT` and produced more `UNSUPPORTED_FACT` diagnostics than 8099. All generated reports still require manual review, so this is not ready for production cutover.

**Rollback note:** Remove the 8100-only env override from the smoke compose env_file list, remove the untracked override file, and recreate only the `medical-notice-analyzer-p0-smoke` service. This returns 8100 to inheriting the original workflow key from `/opt/medical-notice-analyzer/.env` without touching 8099.

## P0 completion GitHub push record

**Date:** 2026-07-07

**Decision:** Push the P0-complete working state to GitHub on a new branch named `codex/p0-complete-20260707` instead of updating `main` directly.

**Scope:** The branch represents the completed P0 local implementation, P0 release hardening, manual-review Word download revision, 8100-only deployment policy, and real-environment 8099-vs-8100 smoke evidence. Runtime secrets, Dify API keys, `.env`, server private configuration, and temporary deployment/test result packages are excluded from the commit.

**Verification basis:** Full local unittest discovery and secret-leak scanning are required immediately before push. The push must use Python `dulwich` so it does not depend on Git for Windows HTTPS credential helper behavior.

## P1-1: MemoryItem v1

**Date:** 2026-07-07

**Goal:** Add a minimal structured memory item layer so long-term writing guidance can be scoped, approved, injected into Dify only when requested, and recorded on each run without treating memory as current notice facts.

**Scope confirmed:**

- Add `MemoryItem` v1 JSON storage under the existing memory directory.
- Add approved/disabled status, explicit scope matching, prompt separation, and run-level `used_memory_ids`.
- Add a minimal token-protected `/memory/items` API.
- Keep existing `report_memory.md` behavior compatible.

**Not done:**

- No vector database, semantic retrieval, diff/rollback/audit workflow, or broad memory UI rebuild.
- No production `8099` change, Dify workflow change, server secret change, or deployment.
- No use of memory as current factual evidence.

**Operation log:**

- Reviewed P1-1 plan scope, P0-4 entry state, existing report-memory code, and current worktree status.
- Established before baseline from focused memory/report tests and full unittest discovery.
- Added failing tests for memory-item retrieval, prompt separation, token-protected API persistence, and run-level injection metadata.
- Implemented `app/core/memory` with JSON store, scoped retrieval, prompt formatting, and minimal FastAPI integration.
- Reran focused and full tests.
- Updated implementation, testing, architecture, and technical-decision docs.

**TDD red checks:**

- `tests.test_memory_items` initially failed with `ModuleNotFoundError: No module named 'app.core.memory'`.
- `test_memory_items_api_enforces_token_and_persists_structured_items` initially failed with HTTP 404 for `PUT /memory/items/{item_id}`.
- `test_analysis_run_injects_scoped_memory_items_and_records_used_ids` initially failed because the structured memory module and run metadata did not exist.

**Verification:**

- Before baseline:
  - `.\.venv\Scripts\python.exe -m unittest tests.test_report_memory -v` -> Ran 5 tests, OK.
  - `.\.venv\Scripts\python.exe -m unittest tests.test_records_api.RecordsApiTests.test_analysis_run_with_memory_explicitly_false_keeps_legacy_dify_behavior tests.test_records_api.RecordsApiTests.test_analysis_run_passes_formal_memory_to_dify_when_requested tests.test_records_api.RecordsApiTests.test_analysis_run_truncates_oversized_existing_memory_without_blocking_generation tests.test_records_api.RecordsApiTests.test_call_dify_workflow_sends_memory_as_independent_start_variables -v` -> Ran 4 tests, OK.
  - `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` -> Ran 180 tests, OK.
- After implementation:
  - `.\.venv\Scripts\python.exe -m unittest tests.test_memory_items -v` -> Ran 2 tests, OK.
  - `.\.venv\Scripts\python.exe -m unittest tests.test_report_memory.ReportMemoryServiceTests.test_memory_items_api_enforces_token_and_persists_structured_items -v` -> Ran 1 test, OK.
  - `.\.venv\Scripts\python.exe -m unittest tests.test_records_api.RecordsApiTests.test_analysis_run_injects_scoped_memory_items_and_records_used_ids -v` -> Ran 1 test, OK.
  - `.\.venv\Scripts\python.exe -m unittest tests.test_memory_items tests.test_report_memory -v` -> Ran 8 tests, OK.
  - `.\.venv\Scripts\python.exe -m unittest tests.test_records_api.RecordsApiTests.test_analysis_run_with_memory_explicitly_false_keeps_legacy_dify_behavior tests.test_records_api.RecordsApiTests.test_analysis_run_passes_formal_memory_to_dify_when_requested tests.test_records_api.RecordsApiTests.test_analysis_run_truncates_oversized_existing_memory_without_blocking_generation tests.test_records_api.RecordsApiTests.test_call_dify_workflow_sends_memory_as_independent_start_variables tests.test_records_api.RecordsApiTests.test_analysis_run_injects_scoped_memory_items_and_records_used_ids -v` -> Ran 5 tests, OK.
  - `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` -> Ran 184 tests, OK.

**Metrics:** No applicable existing P1-1 quantitative evaluation metric was found, and no synthetic metric was added. Test-count movement is recorded only as regression coverage evidence: full discovery changed from 180 tests before P1-1 to 184 tests after P1-1.

**Remaining risks:**

- Scope matching is deliberately explicit and coarse; it does not rank memory items by relevance.
- The minimal API has no UI affordance for editing structured items beyond direct API usage.
- Memory item text can still be poorly written; P1-2 quality smoke evaluation should catch prompt-quality regressions after memory changes.

## P1-2: Quality smoke evaluation

**Date:** 2026-07-07

**Goal:** Add a small, repeatable offline quality smoke loop that can be run after prompt/workflow changes without calling live Dify or company services.

**Scope confirmed:**

- Add `scripts/run_quality_smoke.py`.
- Add 3 fixed JSON fixtures under `tests/eval_fixtures`.
- Reuse existing `build_run_diagnostics` for coverage, source fidelity, unsupported facts, analysis depth, and quality gate status.
- Emit JSON and Markdown reports.

**Not done:**

- No live 8099/8100 calls, production deployment, Dify configuration change, 16-case live regression, public URL fetch, or BI/dashboard.
- No new independent quality scoring model.

**Operation log:**

- Reviewed P1-2 plan row, P1-1 entry criteria, existing diagnostics, existing 16-case regression helper, fixtures, and worktree status.
- Established before baseline from quality script tests, local report quality tests, P1-1 memory tests, and full unittest discovery.
- Added failing smoke-runner tests before implementation; initial failure was missing `scripts/run_quality_smoke.py`.
- Implemented the offline runner and 3 fixtures.
- Found direct CLI execution failed with `ModuleNotFoundError: No module named 'app'`; added a failing CLI regression test and fixed the script path setup.
- Ran the smoke runner once and recorded metrics.
- Reran focused and full tests.
- Updated implementation, testing, architecture, technical-decision, and stage execution logs.

**TDD red checks:**

- `tests.test_quality_smoke` initially failed with `FileNotFoundError` for `scripts/run_quality_smoke.py`.
- `test_quality_smoke_script_runs_directly_from_repo_root` failed with `ModuleNotFoundError: No module named 'app'` before adding repo-root path setup in the script.

**Verification:**

- Before baseline:
  - `.\.venv\Scripts\python.exe -m unittest tests.test_16case_regression_script -v` -> Ran 12 tests, OK.
  - `.\.venv\Scripts\python.exe -m unittest tests.test_report_quality -v` -> Ran 23 tests, OK.
  - `.\.venv\Scripts\python.exe -m unittest tests.test_memory_items tests.test_report_memory -v` -> Ran 8 tests, OK.
  - `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` -> Ran 184 tests, OK.
- After implementation:
  - `.\.venv\Scripts\python.exe -m unittest tests.test_quality_smoke -v` -> Ran 3 tests, OK.
  - `.\.venv\Scripts\python.exe -m unittest tests.test_quality_smoke tests.test_16case_regression_script tests.test_report_quality -v` -> Ran 38 tests, OK.
  - `.\.venv\Scripts\python.exe -m unittest tests.test_memory_items tests.test_report_memory -v` -> Ran 8 tests, OK.
  - `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` -> Ran 187 tests, OK.

**Smoke metrics:** `.\.venv\Scripts\python.exe scripts\run_quality_smoke.py --fixtures-dir tests\eval_fixtures --output-dir reports\quality_smoke_p1_2_20260707_095340` produced:

- case_count: 3
- deliverable_count: 1
- needs_manual_review_count: 2
- failed_count: 0
- deliverable_rate: 0.3333
- average_coverage_score: 100.0
- average_analysis_depth_score: 65.0
- unsupported_fact_count: 1
- summary_only_count: 2
- blocking_issue_counts: `SUMMARY_ONLY_REPORT=2`, `UNSUPPORTED_FACT=1`

**Metrics:** Before P1-2 there was no existing offline P1-2 smoke metric runner. After P1-2, the fixed fixture smoke runner produced the metrics above. Test-count movement is recorded as regression coverage evidence: full discovery changed from 184 tests before P1-2 to 187 tests after P1-2.

**Remaining risks:**

- Fixtures are synthetic and intentionally small; they prove the smoke loop, not real-world report quality.
- `coverage_score` is high for the current fixtures because the packs are compact and have few coverage entries.
- Live 8100/8099 comparison remains outside this local stage and should be handled only when explicitly requested.

## P1-3: Evidence Pack v3 lite

**Date:** 2026-07-07

**Goal:** Make generated database evidence packs traceable enough for future report repair and citations by adding stable evidence IDs, coarse source spans, confidence, and parse-risk metadata without expanding into a full evidence database.

**Scope confirmed:**

- Add `app/core/evidence` as a small annotation module.
- Keep the existing database evidence pack shape and `pack_version: 2.0` compatible.
- Add `evidence_schema_version: 3_lite`, `evidence_items`, and pack-level `parse_risks`.
- Annotate material key facts, important passages, attachment summaries, attachment facts/sections, and table summaries.
- Preserve lightweight evidence fields in the Dify compact pack.

**Not done:**

- No character-level offsets, row-level extraction, evidence database, vector search, citation UI, Dify workflow change, deployment, or server secret/config change.
- No broad `app/main.py` refactor.
- No production `8099` or standby `8100` modification.

**Operation log:**

- Reviewed P1-3 plan row, P1-2 entry criteria, evidence pack construction, compact-pack generation, diagnostics, tests, and worktree status.
- Established before baseline from quality smoke tests, evidence pack API tests, full unittest discovery, and the offline smoke runner.
- Added failing tests for v3 lite evidence annotation and `/analysis/prepare` persistence before implementing the module.
- Implemented `app/core/evidence` annotation and minimal FastAPI integration at database pack creation.
- Added lightweight evidence fields to compact material/attachment/table summaries without sending the full `evidence_items` index to Dify.
- Reran focused and related API/quality tests.
- Updated implementation, testing, architecture, technical-decision, and stage execution logs.

**TDD red checks:**

- `tests.test_evidence_v3_lite` initially failed with `ModuleNotFoundError: No module named 'app.core.evidence'`.
- `test_analysis_prepare_builds_and_persists_database_evidence_pack` initially failed with `KeyError: 'evidence_schema_version'`.

**Verification:**

- Before baseline:
  - `.\.venv\Scripts\python.exe -m unittest tests.test_quality_smoke tests.test_16case_regression_script tests.test_report_quality tests.test_records_api.RecordsApiTests.test_analysis_prepare_builds_and_persists_database_evidence_pack -v` -> Ran 39 tests, OK.
  - `.\.venv\Scripts\python.exe scripts\run_quality_smoke.py --fixtures-dir tests\eval_fixtures --output-dir reports\quality_smoke_p1_3_before_20260707_101523` -> wrote JSON/Markdown smoke outputs.
  - `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` -> Ran 187 tests, OK.
- After implementation:
  - `.\.venv\Scripts\python.exe -m unittest tests.test_evidence_v3_lite tests.test_records_api.RecordsApiTests.test_analysis_prepare_builds_and_persists_database_evidence_pack -v` -> Ran 3 tests, OK.
  - `.\.venv\Scripts\python.exe -m unittest tests.test_evidence_v3_lite tests.test_records_api tests.test_quality_smoke tests.test_report_quality -v` -> Ran 120 tests, OK.
  - `.\.venv\Scripts\python.exe scripts\run_quality_smoke.py --fixtures-dir tests\eval_fixtures --output-dir reports\quality_smoke_p1_3_after_20260707_102318` -> wrote JSON/Markdown smoke outputs.
  - `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` -> Ran 189 tests, OK.

**Smoke metrics:** Before P1-3, `reports\quality_smoke_p1_3_before_20260707_101523\quality_smoke_summary.json` recorded 3 cases, 1 deliverable, 2 manual-review, 0 failed, deliverable rate 0.3333, average coverage 100.0, average analysis depth 65.0, unsupported fact count 1, summary-only count 2, and blocking issue counts `SUMMARY_ONLY_REPORT=2`, `UNSUPPORTED_FACT=1`. After P1-3, `reports\quality_smoke_p1_3_after_20260707_102318\quality_smoke_summary.json` recorded the same values, so no quality-smoke metric drift was observed.

**Remaining risks:**

- Evidence spans are coarse by design and cannot locate exact character offsets inside long paragraphs.
- Existing historical packs are not automatically backfilled; P1-3 annotates newly generated database packs.
- Evidence IDs are stable for a generated pack shape but can change if material order or attachment order changes.

## P2-1: Retry/cancel/resume

**Date:** 2026-07-07

**Goal:** Add controlled long-task operations around analysis runs without replacing the current background-thread execution model or JSON run store.

**Scope confirmed:**

- Add state-checked `/analysis/runs/{run_id}/cancel`, `/analysis/runs/{run_id}/retry`, and `/analysis/runs/{run_id}/resume` endpoints.
- Keep existing `/analysis/run` behavior compatible while sharing its run-start logic with retry/resume.
- Persist lightweight control metadata such as `control_events`, `retried_by`, `retry_of`, `resumed_by`, and `resume_of`.
- Keep cancellation local: mark the run as cancelled and rely on the existing late-result guard to ignore background results after state changes.

**Not done:**

- No queue platform replacement, durable worker scheduler, idempotency, startup recovery, Dify remote cancellation API, UI controls, production `8099` change, standby `8100` deployment, Dify workflow change, or server secret/config change.

**Operation log:**

- Reviewed P2-1 plan row, P1-3 entry state, workflow store, local engine, analysis run APIs, and current dirty worktree.
- Established before baseline from workflow/store/local-engine/API tests, offline quality smoke, and full unittest discovery.
- Added failing tests for cancelling a running run, retrying a failed run, resuming a cancelled run, and rejecting invalid state transitions.
- Implemented `AnalysisRunControlResponse`, shared analysis-run start helper, control-event persistence, and the three state-checked FastAPI endpoints.
- Reran focused retry/cancel/resume tests, related API/workflow regressions, quality smoke before/after, and full unittest discovery.
- Updated implementation, testing, architecture, technical-decision, and stage execution logs.

**TDD red checks:**

- `test_analysis_run_cancel_marks_running_run_cancelled` initially failed with HTTP 404 because `/analysis/runs/{run_id}/cancel` did not exist.
- `test_analysis_run_retry_failed_run_starts_linked_new_run` initially failed with HTTP 404 because `/analysis/runs/{run_id}/retry` did not exist.
- `test_analysis_run_resume_cancelled_run_starts_linked_new_run_and_checks_state` initially failed with HTTP 404 because `/analysis/runs/{run_id}/resume` did not exist.

**Verification:**

- Before baseline:
  - `.\.venv\Scripts\python.exe -m unittest tests.test_workflow_run_store tests.test_local_workflow_engine tests.test_records_api.RecordsApiTests.test_analysis_run_calls_dify_and_persists_report tests.test_records_api.RecordsApiTests.test_analysis_run_uses_local_engine_when_configured tests.test_records_api.RecordsApiTests.test_analysis_run_rejects_missing_pack tests.test_records_api.RecordsApiTests.test_analysis_run_returns_structured_error_when_dify_not_configured -v` -> Ran 15 tests, OK.
  - `.\.venv\Scripts\python.exe scripts\run_quality_smoke.py --fixtures-dir tests\eval_fixtures --output-dir reports\quality_smoke_p2_1_before_20260707_162806` -> wrote JSON/Markdown smoke outputs.
  - `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` -> Ran 189 tests, OK.
- After implementation:
  - New P2-1 red/green tests -> Ran 3 tests, OK.
  - Focused workflow/run regression command -> Ran 19 tests, OK.
  - `.\.venv\Scripts\python.exe scripts\run_quality_smoke.py --fixtures-dir tests\eval_fixtures --output-dir reports\quality_smoke_p2_1_after_20260707_163421` -> wrote JSON/Markdown smoke outputs.
  - `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` -> Ran 192 tests, OK.

**Smoke metrics:** Before P2-1, `reports\quality_smoke_p2_1_before_20260707_162806\quality_smoke_summary.json` recorded 3 cases, 1 deliverable, 2 manual-review, 0 failed, deliverable rate 0.3333, average coverage 100.0, average analysis depth 65.0, unsupported fact count 1, summary-only count 2, and blocking issue counts `SUMMARY_ONLY_REPORT=2`, `UNSUPPORTED_FACT=1`. After P2-1, `reports\quality_smoke_p2_1_after_20260707_163421\quality_smoke_summary.json` recorded the same values, so no quality-smoke metric drift was observed.

**Remaining risks:**

- Cancellation does not call a remote Dify cancellation endpoint; it is local run-state cancellation plus late-result ignore.
- Retry/resume intentionally create new linked run IDs rather than reusing old run IDs, so full idempotency and duplicate suppression remain P2-2 scope.
- Existing historical runs do not gain control metadata unless a new control operation is executed.

## P2-2: Idempotency and recovery

**Date:** 2026-07-07

**Goal:** Prevent repeated `/analysis/run` submissions from creating uncontrolled duplicate workers, and recover stale pending run files left by interrupted service processes.

**Scope confirmed:**

- Add input-hash based duplicate detection for normal analysis-run creation.
- Reuse an active duplicate run when pack/backend/memory inputs match.
- Add JSON run-store listing so recovery can scan persisted run files.
- Mark stale `created` or `running` runs as failed with retryable recovery metadata.
- Run startup recovery without changing deployment, Dify, or server runtime configuration.

**Not done:**

- No queue platform, durable worker scheduler, multi-tenant lock manager, material compression cache, UI changes, Dify workflow/API-key change, deployment, production `8099`, standby `8100`, or server secret/config change.
- Retry/resume continue to create linked new runs and intentionally bypass active-run duplicate reuse.

**Operation log:**

- Reviewed P2-2 plan row, P2-2.5 boundary, workflow store, run creation/background execution, startup hook, P2-1 control endpoints, and dirty worktree status.
- Established before baseline from workflow/run focused tests, offline quality smoke, and full unittest discovery.
- Added failing tests for run-store listing, duplicate active-run reuse, and stale pending-run recovery before implementation.
- Implemented `WorkflowRunStore.list_runs`, input hash generation, active-run lookup, duplicate reuse metadata, stale-run recovery, and startup recovery.
- Updated Dify/local background completion to merge into the latest persisted run record so duplicate counters and control metadata are not overwritten by late worker writes.
- Reran focused idempotency/recovery tests, P2-1 run-control regressions, quality smoke before/after, full unittest discovery, and diff whitespace check.
- Updated implementation, testing, architecture, technical-decision, and stage execution logs.

**TDD red checks:**

- `test_list_runs_returns_valid_records_and_skips_corrupt_files` initially failed with `AttributeError: 'WorkflowRunStore' object has no attribute 'list_runs'`.
- `test_analysis_run_reuses_active_duplicate_submission_by_input_hash` initially failed because the second identical `/analysis/run` request created a different run ID.
- `test_recover_pending_analysis_runs_marks_stale_active_runs_failed` initially failed because `_recover_stale_pending_analysis_runs` did not exist.

**Verification:**

- Before baseline:
  - P2-2 focused workflow/run baseline -> Ran 13 tests, OK.
  - `.\.venv\Scripts\python.exe scripts\run_quality_smoke.py --fixtures-dir tests\eval_fixtures --output-dir reports\quality_smoke_p2_2_before_20260707_164809` -> wrote JSON/Markdown smoke outputs.
  - `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` -> Ran 192 tests, OK.
- After implementation:
  - New P2-2 red/green tests -> Ran 3 tests, OK.
  - Focused workflow/run regression command -> Ran 16 tests, OK.
  - `.\.venv\Scripts\python.exe scripts\run_quality_smoke.py --fixtures-dir tests\eval_fixtures --output-dir reports\quality_smoke_p2_2_after_20260707_165527` -> wrote JSON/Markdown smoke outputs.
  - `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` -> Ran 195 tests, OK.
  - `git diff --check` -> no whitespace errors; only existing Windows LF-to-CRLF warnings were reported.

**Smoke metrics:** Before P2-2, `reports\quality_smoke_p2_2_before_20260707_164809\quality_smoke_summary.json` recorded 3 cases, 1 deliverable, 2 manual-review, 0 failed, deliverable rate 0.3333, average coverage 100.0, average analysis depth 65.0, unsupported fact count 1, summary-only count 2, and blocking issue counts `SUMMARY_ONLY_REPORT=2`, `UNSUPPORTED_FACT=1`. After P2-2, `reports\quality_smoke_p2_2_after_20260707_165527\quality_smoke_summary.json` recorded the same values, so no quality-smoke metric drift was observed.

**Remaining risks:**

- Input hash currently covers pack ID, backend, report-memory flag, report-memory hash, and selected structured memory IDs; if future stages add generation options, the hash definition must be extended.
- Duplicate suppression is an in-process lock over JSON files, not a distributed lock across multiple app containers.
- Recovery marks stale pending runs failed but does not resume from an intermediate checkpoint; users should retry the recovered run.

## P2-2.5: Material Compression Cache

**Date:** 2026-07-07

**Goal:** Add a local derived-data cache for material-level compression views so repeated `/analysis/prepare` can reuse safe `material/full` payloads while preserving current evidence pack behavior and dynamic fallback.

**Scope confirmed:**

- Add a local SQLite-backed `material_compression_cache` repository with stable keys, hashes, status, nullable payloads, diagnostics, errors, and build-run records.
- Build deterministic v1 views: `material/full`, `primary/light`, `primary/safe`, and `auxiliary/summary`.
- Integrate `/analysis/prepare` read-only use of `material/full` only.
- Recompute current request role context after a cache hit, including primary analysis fields and auxiliary relation/snippets.
- Add selected-material cache check/build APIs and minimal `records.html` controls.
- Add a local-safe prebuild script option parser for scheduled prebuild entry.

**Not done:**

- No company database schema deployment, final evidence-pack caching, independent attachment/table cache rows, resident FastAPI scanner, full DB prebuild, production `8099`, standby `8100`, Dify workflow, or server secret/config change.
- `primary/light`, `primary/safe`, and `auxiliary/summary` are built and tested but not read by `/analysis/prepare` in v1.

**Operation log:**

- Reviewed the P2-2.5 plan section, P2-2 entry criteria, prepare/material construction flow, diagnostics, records UI, and current dirty worktree.
- Established before baseline from P2-2/prepare focused tests, quality smoke, and full unittest discovery.
- Added failing tests for cache repository states, hash invalidation, v1 view builders, selected-material APIs, `material/full` prepare hits/fallbacks, UI strings, and prebuild script options.
- Implemented `app/material_compression_cache.py`, `app/material_compressor.py`, selected-material cache endpoints, prepare integration, diagnostics, records UI controls, and `scripts/prebuild_material_compression_cache.py`.
- Reran focused P2-2.5 tests, related prepare/diagnostics/UI regressions, quality smoke before/after, full unittest discovery, synthetic prepare timing, and diff whitespace check.
- Updated implementation, testing, architecture, technical-decision, and stage execution logs.

**TDD red checks:**

- Material cache tests initially failed with `ModuleNotFoundError: No module named 'app.material_compression_cache'`.
- Material compressor tests initially failed with `ModuleNotFoundError: No module named 'app.material_compressor'`.
- Prebuild script test initially failed because `scripts/prebuild_material_compression_cache.py` did not exist.
- Selected-material cache API test initially failed with HTTP 404 for `/analysis/material-cache/check` and `/analysis/material-cache/build`.

**Verification:**

- Before baseline:
  - P2-2/prepare focused baseline -> Ran 15 tests, OK.
  - `.\.venv\Scripts\python.exe scripts\run_quality_smoke.py --fixtures-dir tests\eval_fixtures --output-dir reports\quality_smoke_p2_2_5_before_20260707_170231` -> wrote JSON/Markdown smoke outputs.
  - `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` -> Ran 195 tests, OK.
- After implementation:
  - New P2-2.5 focused tests -> Ran 10 tests, OK.
  - Related prepare/diagnostics/UI/workflow-store/quality-smoke tests -> Ran 25 tests, OK.
  - `.\.venv\Scripts\python.exe scripts\run_quality_smoke.py --fixtures-dir tests\eval_fixtures --output-dir reports\quality_smoke_p2_2_5_after_20260708_0947` -> wrote JSON/Markdown smoke outputs.
  - `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` -> Ran 204 tests, OK.
  - `git diff --check` -> no whitespace errors; only Windows LF-to-CRLF warnings were reported.

**Smoke metrics:** Before P2-2.5, `reports\quality_smoke_p2_2_5_before_20260707_170231\quality_smoke_summary.json` recorded 3 cases, 1 deliverable, 2 manual-review, 0 failed, deliverable rate 0.3333, average coverage 100.0, average analysis depth 65.0, unsupported fact count 1, summary-only count 2, and blocking issue counts `SUMMARY_ONLY_REPORT=2`, `UNSUPPORTED_FACT=1`. After P2-2.5, `reports\quality_smoke_p2_2_5_after_20260708_0947\quality_smoke_summary.json` recorded the same values, so no quality-smoke metric drift was observed.

**Local prepare timing check:** A local synthetic selected-material prepare check with eight attachment metadata rows and no external service calls measured dynamic prepare at 158.70 ms and prebuilt `material/full` cache-hit prepare at 22.03 ms. This is not a real company-data performance benchmark, but it verifies the local cache-hit path avoids repeated dynamic material construction in the test harness.

**Remaining risks:**

- The cache is local SQLite derived data. Deployment still needs an explicit 8100-only configuration decision for `MATERIAL_COMPRESSION_CACHE_DB_PATH`.
- The prebuild script currently validates local-safe options and dry-run behavior; real DB candidate scanning should be exercised in a later real-environment validation before relying on scheduled coverage.
- Cache invalidation depends on selected source and attachment metadata hashes; if parser inputs or material fields expand, hash inputs must be reviewed.

## P2-3: Cleanup, health, diagnostics

**Date:** 2026-07-08

**Goal:** Improve operational visibility without changing report generation behavior, production deployment, Dify configuration, or server secrets.

**Scope confirmed:**

- Add dependency health endpoints for database, workflow/model provider, and local storage.
- Keep database live checks explicit through `check=true`; default health responses inspect configuration only.
- Add bounded local cleanup for generated Word reports and expired attachment parse-cache JSON files.
- Add an operations diagnostics API and a small static diagnostics page.
- Keep diagnostics free of passwords, API keys, cookies, and tokens.

**Not done:**

- No BI dashboard, authentication system, production `8099`, standby `8100`, Dify workflow, live Dify call, server deployment, or secret/config change.
- No cleanup of evidence packs or analysis run JSON records, because those are user-visible workflow state and deleting them is too broad for this stage.
- No router/service refactor beyond a small `app/ops_health.py` helper; large modular split remains P2-4.

**Operation log:**

- Reviewed the P2-3 plan row, AGENTS rules, existing `/health`, pack/run diagnostics, attachment cache cleanup, workflow run store, and current dirty worktree.
- Established before baseline from health/diagnostics/run-store/material-cache focused tests, offline quality smoke, and full unittest discovery.
- Added failing tests for `/health/db`, `/health/llm`, `/health/storage`, `/ops/cleanup`, `/ops/diagnostics`, and `/ops-diagnostics-ui`.
- Implemented `app/ops_health.py`, P2-3 health endpoints, bounded cleanup endpoint, operations diagnostics API, and static diagnostics page.
- Reran focused P2-3 tests, related health/diagnostics/run-store/material-cache regressions, quality smoke before/after, and full unittest discovery.
- Updated implementation, testing, architecture, technical-decision, and stage execution logs.

**TDD red checks:**

- New P2-3 operations tests initially failed with HTTP 404 for `/health/db`, `/health/llm`, `/health/storage`, `/ops/cleanup`, `/ops/diagnostics`, and `/ops-diagnostics-ui`.
- After the first implementation pass, one DB health test exposed a test-fixture gap: the `check=true` request did not keep DB config in scope and correctly returned `unconfigured`. The fixture was fixed before final verification.

**Verification:**

- Before baseline:
  - P2-3 focused health/diagnostics/run-store/material-cache baseline -> Ran 19 tests, OK.
  - `.\.venv\Scripts\python.exe scripts\run_quality_smoke.py --fixtures-dir tests\eval_fixtures --output-dir reports\quality_smoke_p2_3_before_20260708_1000` -> wrote JSON/Markdown smoke outputs.
  - `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` -> Ran 204 tests, OK.
- After implementation:
  - New P2-3 focused operations tests -> Ran 5 tests, OK.
  - Related operations/deployment/pack-run diagnostics/workflow-store/material-cache/quality-smoke tests -> Ran 27 tests, OK.
  - `.\.venv\Scripts\python.exe scripts\run_quality_smoke.py --fixtures-dir tests\eval_fixtures --output-dir reports\quality_smoke_p2_3_after_20260708_1006` -> wrote JSON/Markdown smoke outputs.
  - `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` -> Ran 209 tests, OK.

**Smoke metrics:** Before P2-3, `reports\quality_smoke_p2_3_before_20260708_1000\quality_smoke_summary.json` recorded 3 cases, 1 deliverable, 2 manual-review, 0 failed, deliverable rate 0.3333, average coverage 100.0, average analysis depth 65.0, unsupported fact count 1, summary-only count 2, and blocking issue counts `SUMMARY_ONLY_REPORT=2`, `UNSUPPORTED_FACT=1`. After P2-3, `reports\quality_smoke_p2_3_after_20260708_1006\quality_smoke_summary.json` recorded the same values, so no quality-smoke metric drift was observed.

**Remaining risks:**

- Health endpoints report configuration and local filesystem state; they do not replace real 8100 smoke testing.
- `/health/db?check=true` can touch the configured company database, so operators should use it intentionally rather than polling it aggressively.
- Cleanup intentionally avoids evidence packs and analysis runs; a future retention policy for those records needs explicit product approval.
