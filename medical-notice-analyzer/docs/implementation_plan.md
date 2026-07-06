# MedNoticeAI Implementation Plan

## Goal

Upgrade `medical-notice-analyzer` from a Dify helper service into a resume-ready medical procurement notice AI analysis and report generation platform. The work must stay incremental: preserve the existing Dify-backed flow and old URL analysis flow while adding locally owned workflow, quality, memory, evaluation, evidence, and production capabilities in small tested stages.

## Stage Table

| Stage | Name | Goal | Do | Do Not Do | Depends On | Acceptance | Test Data | Expected Files | Risks |
|---|---|---|---|---|---|---|---|---|---|
| P0-1 | Workflow run persistence shell | Wrap the existing Dify run flow with persistent, queryable run/node status without changing business output. | Add `WorkflowBackend`, `WorkflowRun`, node status models, JSON run store with atomic writes, legacy backend markers, minimal tests, docs. | No retry/cancel/resume, no local workflow, no LLM provider abstraction, no export rewrite, no memory, no large `main.py` split. | Existing `/analysis/run` and Dify proxy. | Existing Dify flow still works; run JSON has backend/status/nodes/timestamps/errors; run status can be queried; tests pass. | One public notice/evidence source recorded; synthetic run fixture. | `app/core/workflow/*`, `tests/test_workflow_run_store.py`, existing `app/main.py`, docs. | Dirty worktree; existing run schema compatibility; concurrent writes. |
| P0-2 | Local workflow MVP | Run `prepare -> generate -> render -> qa -> final` locally without Dify. | Add `local_engine` backend and serial nodes with mock/template generation. | No full DAG, no advanced retry, no LLM provider platform. | P0-1. | `WORKFLOW_BACKEND=local_engine` can produce a draft report. | Synthetic evidence pack and one public pack fixture. | `app/core/workflow/engine.py`, node modules, tests. | Scope creep into full workflow engine. |
| P0-3 | LLM provider and prompt hash | Separate model calls from workflow execution and track prompt hashes. | Add `LLMProvider` interface, mock/provider implementation, prompt files, prompt SHA-256 in run metadata. | Do not treat Dify workflow as an LLM provider. | P0-2. | Run records model/provider/prompt refs; JSON parse errors handled consistently. | Synthetic LLM responses. | `app/core/llm/*`, `prompts/*/v1.md`, tests. | Provider secrets and non-deterministic tests. |
| P0-4 | Quality gate blocks export | Prevent unchecked reports from entering Word export. | Add quality gate result, blocking issue model, export precheck integration. | No broad QA rewrite or dashboard. | P0-3. | QA fail blocks export; QA pass does not auto-export. | Synthetic pass/fail reports. | `app/core/quality/*`, export tests, docs. | Breaking existing download behavior. |
| P1-1 | MemoryItem v1 | Add structured scoped memory retrieval and run-level `used_memory_ids`. | Approved/disabled status, scope match, minimal API/store, prompt separation. | No diff/rollback/audit/vector DB. | P0-4. | Only approved matching memory is injected; memory is not current fact. | Synthetic memory items. | `app/core/memory/*`, tests. | Fact pollution. |
| P1-2 | Quality smoke evaluation | Add small repeatable evaluation loop. | 3-5 fixed cases, smoke runner, JSON/Markdown report. | No large BI dashboard. | P0-4. | Smoke eval runs after workflow/prompt changes. | Public fixture summaries and synthetic fixtures. | `scripts/run_quality_smoke.py`, `tests/eval_fixtures/*`. | Unstable live URLs. |
| P1-3 | Evidence Pack v3 lite | Add traceable evidence IDs and coarse source spans. | `evidence_id`, paragraph/page/chunk/sheet/row spans, confidence, parse risks. | No character-level offset. | P1-2. | Key facts can trace to coarse source spans. | Public pack fixture and synthetic attachment summaries. | `app/core/evidence/*`, tests. | Over-expanding pack schema. |
| P2-1 | Retry/cancel/resume | Add controlled long-task operations. | State-checked retry/cancel/resume endpoints. | No queue platform replacement unless needed. | P0-2. | Failed tasks can retry; cancel/resume are state-safe. | Synthetic slow/failing runs. | Workflow API/tests. | Task interruption complexity. |
| P2-2 | Idempotency and recovery | Avoid duplicate report runs and recover pending runs. | Input hash, duplicate detection, startup recovery policy. | No multi-tenant system. | P2-1. | Duplicate submissions do not create uncontrolled duplicate runs. | Synthetic duplicate submissions. | Store/workflow tests. | Hash definition drift. |
| P2-3 | Cleanup, health, diagnostics | Improve operations visibility. | Cleanup job, `/health/db`, `/health/llm`, `/health/storage`, diagnostics API/page. | No BI dashboard. | P2-2. | Health identifies dependency class; cleanup is bounded. | Synthetic stale files. | Health routers/services/tests. | Touching live storage too broadly. |
| P2-4 | Modular refactor | Split only after behavior is stable. | Routers/services/repositories/models around established boundaries. | No feature rewrite. | P2-3. | Existing tests remain green. | Existing regression suite. | `app/routers`, `app/services`, `app/repositories`, `app/models`. | Large diff risk. |
| P3-1 | Resume and demo materials | Package the project for interviews. | README, diagrams, summary, STAR notes, sample metrics. | No new core features. | P1/P2 as available. | Project is explainable with docs and metrics. | Latest eval report. | README/docs/manual assets. | Documentation drift. |

## Current Stage

`P0-4: Quality gate blocks export` completed locally on 2026-07-06. P1-1 has not started.

### P0-1 Scope

- Define `WorkflowBackend` with `dify_legacy` and reserved `local_engine`.
- Add focused workflow run/node models.
- Add JSON run store with temporary file, flush/fsync, and `os.replace`.
- Add minimal per-run in-process locks.
- Preserve existing `/analysis/run`, `/analysis/runs/{run_id}`, `/analysis/runs/{run_id}/report`, and old `/analyze` behavior.
- Add node/backend metadata to existing analysis run records.
- Add targeted tests before implementation.

### P0-1 Out Of Scope

- Retry, cancel, resume.
- Local workflow execution.
- LLM provider abstraction.
- Prompt hash tracking.
- Memory system changes.
- Evidence Pack v3.
- Export behavior rewrite.
- Large `app/main.py` router split.

### P0-1 Test Plan

- Targeted unit tests for `WorkflowRunStore`.
- Existing analysis run API regression tests around `/analysis/run` and `/analysis/runs/{run_id}`.
- Full unittest discovery when feasible: `python -m unittest discover -s tests -v`.
- Public data check recorded in `docs/testing_and_evaluation.md`.
- Synthetic fixture check using a minimal synthetic run/evidence fixture.

### P0-2 Scope

- Add a deterministic `local_engine` workflow that runs the serial MVP node order `prepare -> generate -> render -> qa -> final`.
- Keep `dify_legacy` as the default backend unless `WORKFLOW_BACKEND=local_engine` is configured.
- Produce a draft report locally from an evidence pack without calling Dify.
- Persist local workflow run metadata, node names, node statuses, timestamps, elapsed time, warnings, report title, report Markdown, and quality check output in the existing run JSON shape.
- Add focused unit/API tests before implementation.

### P0-2 Out Of Scope

- Full DAG orchestration.
- Advanced retry, cancel, or resume behavior.
- LLM provider abstraction.
- Prompt hash tracking.
- Export gate enforcement.
- Memory item schema changes.
- Broad `app/main.py` refactor.

### P0-2 Test Plan

- Targeted unit test for `run_local_workflow` using `tests/fixtures/synthetic_evidence_pack_basic.json`.
- API regression test proving `WORKFLOW_BACKEND=local_engine` creates a finished draft report and does not call `_call_dify_workflow`.
- Legacy Dify API regression test proving the default backend remains `dify_legacy`.
- Full unittest discovery when feasible: `python -m unittest discover -s tests -v`.
- Public fixture availability check recorded in `docs/testing_and_evaluation.md`.

### P0-3 Scope

- Add an `LLMProvider` boundary for single model-generation calls used by the local workflow.
- Add deterministic mock provider behavior for automated tests.
- Add versioned prompt files under `prompts/*/v1.md`.
- Record provider name, model name, prompt ref, prompt SHA-256, prompt refs, and model call metadata in local workflow run results.
- Convert invalid provider JSON into a consistent `LLM_JSON_PARSE_ERROR` path and manual-review result.
- Keep Dify as a workflow backend, not as an LLM provider.

### P0-3 Out Of Scope

- Real external LLM provider integration, credentials, or network calls.
- Treating Dify Workflow as an `LLMProvider`.
- Export blocking or quality gate enforcement changes.
- Retry, cancel, resume, idempotency, or recovery behavior.
- Memory item schema changes.
- Broad `app/main.py` or frontend refactor.

### P0-3 Test Plan

- Targeted unit tests for prompt loading, SHA-256 calculation, mock provider metadata, and JSON parse error handling.
- Local workflow tests proving provider/model/prompt metadata is recorded and invalid provider JSON becomes manual review.
- API regression proving `WORKFLOW_BACKEND=local_engine` returns prompt/provider metadata through the existing status endpoint.
- Legacy Dify regression proving the default backend remains `dify_legacy`.
- Records API regression and full unittest discovery when feasible.

### P0-4 Scope

- Add a reusable analysis-run export precheck under `app/core/quality`.
- Block `/analysis/runs/{run_id}/download` before Word creation when `quality_check.passed` is false.
- Block `/analysis/runs/{run_id}/download` before Word creation when `quality_gate.deliverable_status` requires manual review or failed state.
- Recompute the existing diagnostics quality gate during download when the run's evidence pack is available.
- Keep reports visible through status/report APIs even when Word export is blocked.
- Disable static UI download buttons when the current report state is quality-blocked.

### P0-4 Out Of Scope

- No broad QA scoring rewrite.
- No new dashboard or BI view.
- No automatic export after QA pass.
- No retry/cancel/resume or recovery behavior.
- No real LLM provider or Dify configuration change.
- No deployment to the company server.
- No frontend build-system migration.

### P0-4 Test Plan

- Red API tests proving failed quality checks and manual-review quality gates previously allowed Word creation.
- Focused analysis-run download tests proving blocked runs return 409 and do not create report files.
- Existing positive download tests proving QA-passed reports still require explicit download and can export.
- Existing `/report/export_checked` quality tests.
- Records API/UI static regression tests.
- Local 16-case regression-script unit tests.
- Full unittest discovery when feasible.

## Completed Stages

- P0-1 completed locally on 2026-07-06. The legacy Dify-backed run flow remains the active backend, run records are persisted through `WorkflowRunStore`, and focused/full unittest verification passed.
- P0-2 completed locally on 2026-07-06. `WORKFLOW_BACKEND=local_engine` now runs the local serial MVP and produces a draft report from the synthetic evidence pack without calling Dify. The default backend remains `dify_legacy`.
- P0-3 completed locally on 2026-07-06. The local engine now calls a deterministic mock `LLMProvider`, records provider/model/prompt SHA-256 metadata, and routes invalid provider JSON to manual review without changing the legacy Dify default.
- P0-4 completed locally on 2026-07-06. Analysis-run Word downloads now run a quality export precheck and return `QUALITY_GATE_BLOCKED` before docx creation when QA or quality-gate state is unsafe. QA-passed reports still require an explicit download action.

## Entry Criteria For P1-1

- P0-4 focused quality/export tests and full regression pass.
- Failed QA and manual-review quality gates block Word export before docx creation.
- QA-passed reports remain downloadable only through explicit user action.
- Existing Dify legacy and local-engine report generation flows remain compatible.
- Required docs contain stage results and architecture state.
- Remaining risks are documented and do not block memory-item work.
- P1-1 may start only after an explicit "continue next stage" instruction.
