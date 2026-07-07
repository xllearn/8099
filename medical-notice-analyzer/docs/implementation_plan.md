# MedNoticeAI Implementation Plan

## Goal

Upgrade `medical-notice-analyzer` from a Dify helper service into a resume-ready medical procurement notice AI analysis and report generation platform. The work must stay incremental: preserve the existing Dify-backed flow and old URL analysis flow while adding locally owned workflow, quality, memory, evaluation, evidence, and production capabilities in small tested stages.

## Stage Table

| Stage | Name | Goal | Do | Do Not Do | Depends On | Acceptance | Test Data | Expected Files | Risks |
|---|---|---|---|---|---|---|---|---|---|
| P0-1 | Workflow run persistence shell | Wrap the existing Dify run flow with persistent, queryable run/node status without changing business output. | Add `WorkflowBackend`, `WorkflowRun`, node status models, JSON run store with atomic writes, legacy backend markers, minimal tests, docs. | No retry/cancel/resume, no local workflow, no LLM provider abstraction, no export rewrite, no memory, no large `main.py` split. | Existing `/analysis/run` and Dify proxy. | Existing Dify flow still works; run JSON has backend/status/nodes/timestamps/errors; run status can be queried; tests pass. | One public notice/evidence source recorded; synthetic run fixture. | `app/core/workflow/*`, `tests/test_workflow_run_store.py`, existing `app/main.py`, docs. | Dirty worktree; existing run schema compatibility; concurrent writes. |
| P0-2 | Local workflow MVP | Run `prepare -> generate -> render -> qa -> final` locally without Dify. | Add `local_engine` backend and serial nodes with mock/template generation. | No full DAG, no advanced retry, no LLM provider platform. | P0-1. | `WORKFLOW_BACKEND=local_engine` can produce a draft report. | Synthetic evidence pack and one public pack fixture. | `app/core/workflow/engine.py`, node modules, tests. | Scope creep into full workflow engine. |
| P0-3 | LLM provider and prompt hash | Separate model calls from workflow execution and track prompt hashes. | Add `LLMProvider` interface, mock/provider implementation, prompt files, prompt SHA-256 in run metadata. | Do not treat Dify workflow as an LLM provider. | P0-2. | Run records model/provider/prompt refs; JSON parse errors handled consistently. | Synthetic LLM responses. | `app/core/llm/*`, `prompts/*/v1.md`, tests. | Provider secrets and non-deterministic tests. |
| P0-4 | Quality gate marks manual-review export | Keep quality diagnostics visible while allowing users to explicitly download Word for manual-review reports. | Add quality gate result, issue model, export precheck integration, and explicit-download behavior. | No broad QA rewrite or dashboard. | P0-3. | `needs_manual_review`/QA-failed reports remain visible and downloadable only by explicit user action; not-ready reports still block; QA pass does not auto-export. | Synthetic pass/fail/manual-review reports. | `app/core/quality/*`, export tests, docs. | Users may download low-quality reports without reading diagnostics. |
| P1-1 | MemoryItem v1 | Add structured scoped memory retrieval and run-level `used_memory_ids`. | Approved/disabled status, scope match, minimal API/store, prompt separation. | No diff/rollback/audit/vector DB. | P0-4. | Only approved matching memory is injected; memory is not current fact. | Synthetic memory items. | `app/core/memory/*`, tests. | Fact pollution. |
| P1-2 | Quality smoke evaluation | Add small repeatable evaluation loop. | 3-5 fixed cases, smoke runner, JSON/Markdown report. | No large BI dashboard. | P0-4. | Smoke eval runs after workflow/prompt changes. | Public fixture summaries and synthetic fixtures. | `scripts/run_quality_smoke.py`, `tests/eval_fixtures/*`. | Unstable live URLs. |
| P1-3 | Evidence Pack v3 lite | Add traceable evidence IDs and coarse source spans. | `evidence_id`, paragraph/page/chunk/sheet/row spans, confidence, parse risks. | No character-level offset. | P1-2. | Key facts can trace to coarse source spans. | Public pack fixture and synthetic attachment summaries. | `app/core/evidence/*`, tests. | Over-expanding pack schema. |
| P2-1 | Retry/cancel/resume | Add controlled long-task operations. | State-checked retry/cancel/resume endpoints. | No queue platform replacement unless needed. | P0-2. | Failed tasks can retry; cancel/resume are state-safe. | Synthetic slow/failing runs. | Workflow API/tests. | Task interruption complexity. |
| P2-2 | Idempotency and recovery | Avoid duplicate report runs and recover pending runs. | Input hash, duplicate detection, startup recovery policy. | No multi-tenant system. | P2-1. | Duplicate submissions do not create uncontrolled duplicate runs. | Synthetic duplicate submissions. | Store/workflow tests. | Hash definition drift. |
| P2-2.5 | Material Compression Cache | Reduce `/analysis/prepare` wait time by prebuilding reusable DB-backed material compression views. | Add cache schema/repository/hash, build `material/full`, `primary/light`, `primary/safe`, and `auxiliary/summary` views, add prebuild script, read `material/full` during prepare, add selected-material cache check/build, and expose diagnostics. | Do not replace full evidence packs with high-compression views, do not add independent attachment/table cache rows in v1, do not cache final evidence packs, do not add a FastAPI resident scanner, do not change Dify/server config. | P2-2 and P1-3. | Cache can be disabled globally; `material/full` hits speed prepare and fall back safely; role-specific fields are recalculated per request; input strategy and quality metrics do not regress. | Fixed `full_input`, `attachment_led`, `table_heavy`, and `staged_generation` samples plus synthetic cache records. | `db/migrations/*`, `app/material_compression_cache.py`, `app/material_compressor.py`, `scripts/prebuild_material_compression_cache.py`, focused tests, minimal `app/main.py`, optional `records.html`. | Cache staleness, schema drift, duplicate builds, accidentally losing evidence detail. |
| P2-3 | Cleanup, health, diagnostics | Improve operations visibility. | Cleanup job, `/health/db`, `/health/llm`, `/health/storage`, diagnostics API/page. | No BI dashboard. | P2-2. | Health identifies dependency class; cleanup is bounded. | Synthetic stale files. | Health routers/services/tests. | Touching live storage too broadly. |
| P2-4 | Modular refactor | Split only after behavior is stable. | Routers/services/repositories/models around established boundaries. | No feature rewrite. | P2-3. | Existing tests remain green. | Existing regression suite. | `app/routers`, `app/services`, `app/repositories`, `app/models`. | Large diff risk. |
| P3-1 | QA repair contract and guardrails | Turn QA issues into structured, auditable repair contracts. | Add repair contract generation, allowed/blocked actions, minimal `evidence_ref`, and initial `execution_trace` nodes. | No LLM repair call, no automatic report overwrite, no memory-as-current-fact. | P2-4 and recorded 10-case regression baseline. | QA issues produce stable repair contracts with evidence refs and guardrails; trace nodes are persisted. | Synthetic QA issues plus fixed 10-case regression records. | `app/core/quality/*`, workflow/run models, diagnostics tests. | Contract too vague to control repair; evidence refs becoming empty placeholders. |
| P3-2 | QA auto-repair MVP | Run at most one controlled repair round and re-run QA. | Add `repair -> qa_retry`, v1/v2 version retention, `qa_before`, `qa_after`, `repair_diff_summary`, and `repair_prompt_version`. | No infinite repair loop, no silent deletion to pass QA, no Word download rule change. | P3-1. | v2 does not trigger hard gates; failed repair keeps v1 and marks `needs_manual_review`. | Unsupported fact, fragmentary report, missing coverage, technical-note fixtures, and 10-case regression. | Repair workflow nodes, prompt files, run store/version tests. | Model shrinks reports or removes useful content to pass QA. |
| P3-3 | Multi-primary staged synthesis | Generate per-primary analysis and synthesize a combined report. | Serial per-primary briefs, synthesis step, auxiliary-as-background handling, evidence refs/source basis per brief. | No parallel execution, no complex DAG, no LangGraph, no agent tool loop. | P3-2 and P1-3. | Multi-primary cases generate per-material analysis plus a combined report without adding unsupported facts. | 2-3 primary-material samples plus fixed staged-generation cases. | Workflow nodes, synthesis prompt/tests, diagnostics. | Synthesis adds facts not present in briefs or evidence pack. |
| P3-4 | staged_generation workflow paths | Promote input strategies into explicit generation paths. | Implement distinct paths for `full_input`, `attachment_led`, `table_heavy`, `safe_compact`, and `staged_generation`; record chosen path in trace. | No LangGraph migration, no agent autonomy, no production cutover. | P3-3. | Different input strategies take distinguishable execution paths and preserve quality metrics. | Fixed `full_input`, `attachment_led`, `table_heavy`, and `staged_generation` samples. | Workflow routing, strategy tests, diagnostics. | Strategy split increases complexity without quality gain. |
| P3-5 | Human review interrupt and resume | Let users review/edit failed repair output and resume safely. | Add `human_review_waiting`, review/resume APIs, review audit fields, version hashes, and `resume_from_node`. | No replacement of workflow core, no assumed login system, no production 8099 changes. | P3-2 and P3-4. | Reviewed/submitted versions are auditable; runs can resume to QA retry, final, or export-ready states. | Synthetic review decisions and edited report fixtures. | API routes, run store, static UI, tests. | Ambiguous reviewer identity or version being approved. |
| P3-6 | Execution visualization and LangGraph spike | Show execution traces and separately evaluate whether LangGraph is worth migrating to. | Add trace UI/diagnostics; run a 2-3 sample LangGraph spike for checkpoint/interrupt/resume comparison. | Spike does not touch 8099, does not replace 8100 main path, does not migrate `local_engine`, and does not change production config. | P3-5. | UI shows node path/failure points; LangGraph produces only an evaluation report and migration recommendation. | 2-3 fixed samples covering repair, staged generation, and human review resume. | `analysis_run.html`, diagnostics, spike scripts/docs. | Premature framework migration; duplicate state stores. |
| P4-1 | Resume and demo materials | Package the project for interviews and handoff. | README, diagrams, summary, STAR notes, sample metrics, demo script. | No new core features. | P1/P2/P3 as available. | Project is explainable with docs, screenshots, workflow diagrams, and metrics. | Latest eval/regression reports. | README/docs/manual assets. | Documentation drift. |
| P4-2 | Agent/tool loop exploration | Explore model-driven fact checking, evidence retrieval, and report repair after the controlled workflow is stable. | Define tool boundaries, permission controls, audit logs, cost limits, and mis-operation prevention. | Do not start before P3 proves workflow quality/recovery; do not grant unbounded DB/attachment/report editing authority. | P3-6. | Agent loop is evaluated behind explicit controls and does not replace deterministic workflow by default. | Restricted synthetic tools and fixed regression cases. | Experimental tool adapters, audit tests, evaluation docs. | Fact reliability, cost, permissions, and audit risk. |

## Current Stage

`P0-4: Quality gate blocks export` completed locally on 2026-07-06. P1-1 has not started.

## Deployment Policy During Remaining Plan

Effective 2026-07-07, all subsequent implementation, Dify workflow experiments, real-environment smoke tests, and staged fixes must target the standby environment on port `8100` only until the full staged plan is completed and a separate production cutover is explicitly approved.

- Do not modify production port `8099`, the `medical-notice-analyzer` production container, or the production Dify workflow during P1/P2/P3/P4 implementation unless a separate production cutover is explicitly approved.
- Keep `8099` available as the stable comparison baseline for fixed-case regression and smoke testing.
- Configure 8100-only Dify workflow keys through server runtime configuration or untracked environment override files, never through git-tracked source, docs, or committed compose changes containing secrets.
- Promotion from `8100` to `8099` requires a separate approval after the planned stages, fixed-case comparison evidence, rollback notes, and production cutover checklist are complete.

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
- Allow `/analysis/runs/{run_id}/download` to create Word files for reports in `needs_manual_review`, including reports with `quality_check.passed=false` or `quality_gate.deliverable_status=needs_manual_review`.
- Block `/analysis/runs/{run_id}/download` only when there is no report body ready to export.
- Recompute the existing diagnostics quality gate during download when the run's evidence pack is available.
- Keep reports, warnings, remaining issues, and diagnostics visible through status/report APIs so users can review quality risks before downloading.
- Keep static UI download buttons enabled for manual-review reports that have report Markdown.

### P0-4 Out Of Scope

- No broad QA scoring rewrite.
- No new dashboard or BI view.
- No automatic export after QA pass.
- No retry/cancel/resume or recovery behavior.
- No real LLM provider or Dify configuration change.
- No deployment to the company server.
- No frontend build-system migration.

### P0-4 Test Plan

- Red API tests proving failed quality checks and manual-review quality gates now allow explicit Word creation.
- Focused analysis-run download tests proving not-ready runs return 409 and manual-review runs return 200 with a docx.
- Existing positive download tests proving QA-passed reports still require explicit download and can export.
- Existing `/report/export_checked` quality tests.
- Records API/UI static regression tests.
- Local 16-case regression-script unit tests.
- Full unittest discovery when feasible.

### P2-2.5 Material Compression Cache Scope

P2-2.5 is inserted after idempotency/recovery and before cleanup/health because it adds persistent derived data and needs the duplicate-build/recovery rules from P2-2 before it is safe to prebuild at scale. The goal is to reduce repeated `/analysis/prepare` latency while keeping the original company database records, downloaded attachments, parsed text, and final evidence pack assembly as the source of truth.

The first version stores reusable material-level compression views in a new database table instead of process memory. This prevents large cache payloads from consuming FastAPI worker memory across requests and allows scheduled prebuild jobs to reuse the same cache as user-triggered prepare calls. The cache is derived data only: when it is disabled, missing, stale, corrupt, or failed, the system must fall back to the current dynamic construction path.

#### P2-2.5 Do

- Add a `material_compression_cache` schema with stable keys, status, hashes, versions, timestamps, build source, diagnostics, nullable `payload_json`, and error fields.
- Add a `material_compression_cache_build_run` schema or equivalent run record for batch prebuild visibility and later cleanup/diagnostics.
- Track invalidation inputs including source content hash, attachment parse hash, parser version, compression version, and view type.
- Prebuild independent v1 views: `material/full`, `primary/light`, `primary/safe`, and `auxiliary/summary`.
- Keep `attachment/summary` and `table/summary` embedded inside material cache payloads in v1. Do not create separate attachment/table cache rows until a later schema adds `scope_type` and `scope_key`.
- Make `material/full` compatible with the existing full material shape so it can accelerate complete evidence pack construction without lowering detail.
- Implement repository behavior for hit, miss, stale, corrupt, failed, building timeout, force refresh, and atomic status transitions.
- Add a prebuild script supporting `--all`, `--only-missing`, `--only-stale`, `--since-date`, `--days`, `--limit`, `--dry-run`, `--force-refresh`, `--roles`, `--levels`, `--sleep-ms`, and `--max-failures`.
- In `/analysis/prepare`, read only `material/full` in v1. A hit may replace the expensive full material construction step, but misses and unsafe cache states must fall back to dynamic construction.
- After a `material/full` hit, still apply the current request context: set `material_role = primary` or `auxiliary`, recompute primary-specific fields, and recompute auxiliary fields from the current selected primary keywords.
- Specifically recalculate primary fields such as `important_passages`, `policy_rules`, `price_rules`, `time_requirements`, `product_scope`, `enterprise_requirements`, and `execution_requirements`.
- Specifically recalculate auxiliary fields such as `relation_to_primary`, `relevance_score`, and `relevant_snippets` based on the current `primary_keywords`.
- Prebuild and test `primary/light`, `primary/safe`, and `auxiliary/summary`, but do not read them from `/analysis/prepare` in v1. Compact-stage use requires a later MC-4.5 design.
- Add selected-material frontend/API support first: check cache status for currently selected materials and build selected materials, with the existing 1-3 primary and up to 10 auxiliary limit.
- Defer "current filter result top N" prebuild to a later async build-run slice after build-run polling and failure statistics are stable.
- Implement automatic new-data coverage through a scheduled prebuild script, for example `python scripts/prebuild_material_compression_cache.py --only-missing --days 7 --limit 500`, not through a resident background scanner inside the FastAPI process.
- Put cache hit/miss/stale/corrupt/build metadata in diagnostics only. Do not write cache implementation details into `report_markdown` or Word report content.

#### P2-2.5 Do Not Do

- Do not use `primary/light`, `primary/safe`, or `auxiliary/summary` to replace the full evidence pack in v1.
- Do not cache final assembled evidence packs, because final pack content depends on the current material combination, roles, compact strategy, and user options.
- Do not add independent attachment/table cache rows in v1. If that becomes necessary later, introduce scoped keys instead of overloading material keys.
- Do not scan the entire database from the web UI in v1.
- Do not add a FastAPI in-process long-running scanner or scheduler thread.
- Do not deploy schema migrations, change company server configuration, or change Dify workflow settings as part of local implementation.

#### P2-2.5 Milestones

- MC-0: Freeze this design and document rollback, cache keys, invalidation rules, and v1 view boundaries.
- MC-1: Add schema, repository, and hash/version logic with tests.
- MC-2: Add deterministic material compression builders for `material/full`, `primary/light`, `primary/safe`, and `auxiliary/summary`.
- MC-3: Add prebuild script and build-run tracking for missing/stale/all modes.
- MC-4: Integrate `/analysis/prepare` read-only cache usage for `material/full` with dynamic fallback and per-request role enhancement.
- MC-5: Add selected-material cache check/build API and minimal records UI controls.
- MC-6: Add stats, cleanup hooks, and health/diagnostic output.
- MC-7: Run fixed-sample before/after checks in a real-like environment before any company-server deployment request.

#### P2-2.5 Test Plan

- Repository tests for cache key uniqueness, hash/version invalidation, nullable payload on building/failed rows, stale detection, corrupt payload handling, building timeout, and force refresh.
- Compressor tests proving all four v1 views are generated deterministically and keep required fields, including embedded attachment/table summaries where applicable.
- Prepare tests proving `material/full` hits are used, misses/stale/corrupt/failed states fall back, and `primary/light`, `primary/safe`, and `auxiliary/summary` are not read by prepare in v1.
- Role-context tests proving cached `material/full` still gets current `material_role`, primary-specific fields, and auxiliary `relation_to_primary`/`relevance_score`/`relevant_snippets`.
- Equivalence tests comparing cache off versus cache on for fixed `full_input`, `attachment_led`, `table_heavy`, and `staged_generation` samples. `input_strategy`, key material fields, and quality diagnostics must remain unchanged or have documented expected differences.
- Script tests for `--dry-run`, `--all`, `--only-missing`, `--only-stale`, `--limit`, stale refresh, failed item continuation, and `--max-failures`.
- API/UI tests for selected-material check/build only. The `/records` listing must not do per-row cache checks.

#### P2-2.5 Acceptance

- `MATERIAL_COMPRESSION_CACHE_ENABLED=false` fully returns the system to the pre-cache dynamic path.
- With cache enabled, `material/full` cache hits reduce or at least do not materially regress `/analysis/prepare` time on fixed samples. If no speedup is observed, the reason is documented with elapsed time evidence.
- Cache-enabled and cache-disabled fixed samples preserve `input_strategy` and do not increase unsupported facts or quality blocking issues.
- The report body and exported Word content do not mention cache internals.
- Diagnostics show material cache hit/miss/stale/corrupt/failed counts and per-material cache metadata.
- Existing focused tests, relevant API/UI regressions, and full unittest discovery pass when feasible.

## P3 Quality Workflow Enhancement Mainline

P3 turns the existing workflow and quality diagnostics into a controlled report-quality improvement loop. The goal is not to introduce a new framework first. The goal is to make QA issues, repair contracts, version history, execution traces, fixed-case regression, and human review boundaries solid enough that a later framework spike can be judged with evidence.

### P3 Baseline And Environment Rules

- Fixed 10 real records are the `10-case regression` set for P3.
- Port `8099` is the read-only production baseline. It must not be modified by P3 work.
- Port `8100` is the candidate validation environment. It carries P3 implementation, Dify workflow experiments, prompt changes, and smoke/regression runs.
- Every 10-case regression run must record test time, 8099/8100 deployment versions, run IDs, pack IDs, prompt/workflow versions, repair versions when applicable, key diagnostics codes, Word download status, and elapsed time.
- Candidate 8100 behavior should not regress versus the recorded 8099 baseline unless a difference is explicitly explained and accepted.

### P3 Report Quality Regression Gates

P3 has two separate comparison axes:

- `v2 vs v1`: an auto-repaired report must not be materially worse than the source report.
- `8100 vs 8099`: the candidate environment must not regress against the production baseline on fixed cases.

Hard gates for repaired report promotion:

- `UNSUPPORTED_FACT` increases.
- `Q_DIFY_FRAGMENTARY_REPORT` increases.
- Repaired report length is less than 75% of the source report length.
- Repair produces no exportable report body.
- Core report body is empty or only contains metadata/technical notes.

Warnings that must be recorded but do not always block:

- Report length is below the 10-case baseline average.
- `MISSING_CORE_COVERAGE` does not improve.
- `TECHNICAL_NOTE_IN_REPORT_BODY` does not improve.
- `coverage_score` does not improve.
- `analysis_depth_score` does not improve.

### P3 execution_trace Minimum Shape

`execution_trace` starts in P3-1, before UI visualization work. P3-6 should display existing trace data rather than reconstruct old history.

Minimum trace node shape:

```json
{
  "node": "qa",
  "status": "failed",
  "started_at": "2026-07-07 10:00:00",
  "finished_at": "2026-07-07 10:00:01",
  "elapsed_ms": 1200,
  "issues": ["UNSUPPORTED_FACT"],
  "version": 1,
  "output_ref": "path-or-artifact-id"
}
```

### P3-1 Scope: QA Repair Contract And Guardrails

- Convert existing `quality_gate`, `quality_check`, diagnostics, and remaining issues into a structured `repair_contract`.
- Define allowed actions and blocked actions per issue.
- Include minimal evidence references for repairable issues.
- Start persisting `execution_trace` for generate, QA, contract generation, and final status nodes.
- Keep repair contract generation deterministic and testable without an LLM call.

Minimum `evidence_ref`:

```json
{
  "material_id": "menu_code/articleid",
  "source_type": "notice_body|attachment|table",
  "title": "证据来源标题或附件名",
  "quote_or_summary": "可引用原文片段或结构化摘要",
  "quote_hash": "sha256...",
  "max_quote_chars": 500,
  "confidence": "high|medium|low"
}
```

Minimum repair issue contract:

```json
{
  "issue_code": "UNSUPPORTED_FACT",
  "source_basis": "evidence_pack",
  "evidence_refs": [],
  "allowed_actions": [
    "delete_unsupported_fact",
    "rewrite_with_supported_evidence",
    "add_missing_evidence_backed_rule",
    "remove_technical_note_from_body"
  ],
  "blocked_actions": [
    "add_new_fact_not_in_evidence_pack",
    "use_memory_as_current_fact",
    "overwrite_original_report",
    "drop_version_history"
  ]
}
```

#### P3-1 Out Of Scope

- No LLM-based repair.
- No automatic replacement of the current report.
- No use of report memory as current factual evidence.
- No production `8099` changes.

#### P3-1 Acceptance

- QA-failed sample reports produce stable repair contracts.
- `allowed_actions`, `blocked_actions`, and `evidence_refs` are present for supported issue codes.
- `execution_trace` is persisted for the contract path.
- Focused unit/API tests and relevant diagnostics regressions pass.

### P3-2 Scope: QA Auto-Repair MVP

- Add a single automatic repair round: `generate -> qa -> repair -> qa_retry`.
- Preserve the original report as v1 and the repaired candidate as v2.
- Store `qa_before`, `qa_after`, `repair_contract`, `repair_diff_summary`, and `repair_prompt_version`.
- Promote v2 only when hard gates are not triggered.
- If repair fails or remains risky, keep v1 and mark the run `needs_manual_review`.

#### P3-2 Out Of Scope

- No infinite repair loops.
- No silent deletion of core content to pass QA.
- No change to explicit Word download behavior.
- No production `8099` changes.

#### P3-2 Acceptance

- QA-passed reports do not enter repair.
- QA-failed reports create v2 only through the repair contract.
- v2 hard-gate failures are not promoted.
- v1 and v2 are both inspectable.
- 10-case regression records before/after diagnostics and Word download status.

### P3-3 Scope: Multi-Primary Staged Synthesis

- For multi-primary selections, generate a brief for each primary material.
- Synthesize primary briefs into a combined report.
- Treat auxiliary materials as background/context, not as replacements for primary evidence.
- Require each primary brief to carry evidence refs or source basis.
- Prevent synthesis from adding hard facts not present in the briefs or evidence pack.

#### P3-3 Out Of Scope

- No parallel execution in v1.
- No complex DAG engine.
- No LangGraph migration.
- No agent tool loop.

#### P3-3 Acceptance

- Multi-primary fixed cases produce per-primary analysis plus a combined report.
- Synthesis diagnostics show which primary briefs contributed to the final report.
- Unsupported facts do not increase versus the pre-synthesis path.

### P3-4 Scope: staged_generation Workflow Paths

- Promote input strategies from guidance flags into explicit workflow paths.
- Distinguish at least these paths: `full_input`, `attachment_led`, `table_heavy`, `safe_compact`, and `staged_generation`.
- Persist the chosen path and node sequence in `execution_trace`.
- Keep quality gates and repair behavior compatible with all paths.

#### P3-4 Out Of Scope

- No LangGraph migration.
- No autonomous model tool choice.
- No production cutover.

#### P3-4 Acceptance

- Fixed samples for each strategy take distinguishable execution paths.
- Path-specific outputs preserve or improve quality diagnostics compared with baseline.
- Word report export remains explicit and unchanged.

### P3-5 Scope: Human Review Interrupt And Resume

- Add a `human_review_waiting` state for reports that need operator review after failed repair or explicit review request.
- Add review and resume APIs for analysis runs.
- Persist review audit fields.
- Support resume to `qa_retry`, `final`, or `export_ready` according to review decision and state.

Minimum review audit fields:

```json
{
  "review_decision": "approve|request_changes|reject",
  "review_comment": "...",
  "reviewed_by": "manual reviewer name or operator id",
  "reviewed_at": "...",
  "reviewed_version": 2,
  "submitted_version": 3,
  "edited_report_markdown_hash": "sha256...",
  "resume_from_node": "qa_retry|final|export_ready"
}
```

#### P3-5 Out Of Scope

- No replacement of the workflow core.
- No assumption that a full login/user system already exists.
- No production `8099` changes.

#### P3-5 Acceptance

- The system records which version the reviewer saw and which version was submitted.
- Edited report hashes are stored when manual edits are submitted.
- Resume behavior is state-checked and covered by API tests.

### P3-6 Scope: Execution Visualization And LangGraph Spike

- Add frontend/diagnostics display for `execution_trace`.
- Show node path, status, elapsed time, failed issues, repair version, review wait points, and resume points.
- Run a limited LangGraph spike using 2-3 fixed samples that cover QA repair, staged generation, and human review resume.
- Compare LangGraph against the existing local engine for checkpoint/resume, interrupts, event/trace output, persistence fit, test complexity, and deployment risk.

#### P3-6 Out Of Scope

- The spike does not touch production `8099`.
- The spike does not replace the `8100` main path.
- The spike does not migrate `local_engine`.
- The spike does not change production config.
- The spike does not introduce agent/tool autonomy.

#### P3-6 Acceptance

- The report detail page can show execution trace for new runs.
- Spike output is an evaluation report, not a runtime migration.
- If LangGraph does not clearly simplify checkpoint/resume/human interrupt or materially increases state/test/deployment complexity, the recommendation is not to migrate.

## P4 Packaging And Agent Exploration

### P4-1 Scope: Resume And Demo Materials

- Produce resume-ready and handoff-ready materials after P1/P2/P3 evidence exists.
- Update README, architecture diagrams, stage summaries, STAR notes, demo script, and sample metric snapshots.
- Include latest fixed-case regression results and known risks.

#### P4-1 Out Of Scope

- No new core product behavior.
- No production deployment unless separately approved.

#### P4-1 Acceptance

- The project can be explained through docs, diagrams, demo flow, and measured outcomes.
- Documentation reflects the current architecture and known limitations.

### P4-2 Scope: Agent/tool Loop Exploration

- Explore model-driven fact checking, evidence retrieval, and report repair only after controlled workflow quality and review boundaries are stable.
- Define strict tool permissions, audit logs, cost controls, and mis-operation prevention before any agent loop can act on database records, attachments, or reports.
- Keep the deterministic workflow as the default path unless a later evaluation explicitly justifies replacement.

#### P4-2 Out Of Scope

- No unbounded database access.
- No autonomous report overwrite.
- No hidden tool calls without audit records.
- No production rollout by default.

#### P4-2 Acceptance

- Agent/tool loop remains an explicitly controlled exploration.
- Fixed regression cases and audit tests prove it does not reduce fact reliability or operator control.

## Completed Stages

- P0-1 completed locally on 2026-07-06. The legacy Dify-backed run flow remains the active backend, run records are persisted through `WorkflowRunStore`, and focused/full unittest verification passed.
- P0-2 completed locally on 2026-07-06. `WORKFLOW_BACKEND=local_engine` now runs the local serial MVP and produces a draft report from the synthetic evidence pack without calling Dify. The default backend remains `dify_legacy`.
- P0-3 completed locally on 2026-07-06. The local engine now calls a deterministic mock `LLMProvider`, records provider/model/prompt SHA-256 metadata, and routes invalid provider JSON to manual review without changing the legacy Dify default.
- P0-4 completed locally on 2026-07-06, then revised on 2026-07-06 after real-environment smoke feedback. Analysis-run Word downloads keep quality diagnostics and manual-review status, but `needs_manual_review` reports are allowed to export by explicit user download. Not-ready reports still return 409. QA-passed reports still require an explicit download action.

## Entry Criteria For P1-1

- P0-4 focused quality/export tests and full regression pass.
- Failed QA and manual-review quality gates mark risk but do not block explicit Word download.
- QA-passed reports remain downloadable only through explicit user action.
- Existing Dify legacy and local-engine report generation flows remain compatible.
- Required docs contain stage results and architecture state.
- Remaining risks are documented and do not block memory-item work.
- P1-1 may start only after an explicit "continue next stage" instruction.
