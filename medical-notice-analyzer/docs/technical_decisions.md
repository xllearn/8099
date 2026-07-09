# Technical Decisions

## TD-001: Separate WorkflowBackend From LLMProvider

**Background:** The current system calls Dify as an external workflow that fetches a pack, generates, checks, and returns report fields. That is a different abstraction level from a single model completion call.

**Decision:** Model workflow execution as `WorkflowBackend` with `dify_legacy` and reserved `local_engine`. Model calls will later be represented as `LLMProvider` with `openai`, `local`, and `mock`.

**Why:** This keeps P0-1 compatible with the existing Dify flow and prevents mixing a full workflow backend with a low-level model provider.

**Rejected alternatives:** Treating Dify as an `LLMProvider` would blur boundaries and make local workflow migration harder to explain and test.

**Limitations:** P0-1 only records the backend. P0-2 implements `local_engine` as a deterministic workflow backend, not as an LLM provider.

**Evolution:** P0-2 adds `local_engine`; P0-3 adds real `LLMProvider`.

**P0-1 result:** Implemented as workflow run/node state models and `dify_legacy` metadata only. The Dify workflow remains a workflow backend marker, not an LLM provider.

**P0-2 result:** Implemented `local_engine` as a workflow backend selected by `WORKFLOW_BACKEND=local_engine`. The default remains `dify_legacy`, and Dify is still not treated as an `LLMProvider`.

**P0-3 result:** Implemented `LLMProvider` only for local single-generation calls. The local engine records provider/model/prompt metadata, while Dify remains a workflow backend and is still not treated as an `LLMProvider`.

## TD-002: Use JSON Run Store In P0-1

**Background:** Existing analysis runs are already saved as local JSON records under `ANALYSIS_RUN_DIR`.

**Decision:** Keep JSON storage for P0-1 and formalize it behind `WorkflowRunStore`.

**Why:** It preserves deployment simplicity and avoids introducing a database migration before the workflow boundary is stable.

**Rejected alternatives:** SQLite/MySQL would provide stronger concurrency semantics but increases the blast radius for the first stage.

**Limitations:** JSON storage needs careful atomic writes and only supports lightweight concurrency.

**Evolution:** P2 can move to SQLite/MySQL if job recovery and idempotency require stronger guarantees.

**P0-1 result:** `WorkflowRunStore` now owns read/write/update operations for analysis run JSON records. It writes via temporary files, flush/fsync, `os.replace`, and per-run in-process locks.

## TD-003: Atomic Writes For run.json

**Background:** Long-running report jobs can update the same run record several times. A partial write would break status polling and report access.

**Decision:** Write to a temporary file, flush/fsync it, then use `os.replace` to atomically replace `run.json`.

**Why:** It prevents corrupted partial files from replacing the last good run record on normal filesystems.

**Rejected alternatives:** Direct `Path.write_text` is simpler but can leave truncated files if the process dies mid-write.

**Limitations:** This does not provide distributed locking across processes.

**Evolution:** P2 can add database transactions or cross-process file locks.

## TD-004: QA Pass Does Not Mean Automatic Export

**Background:** The product flow previews the generated report and diagnostics before users download Word.

**Decision:** Keep report generation and Word export as separate actions. Later quality states such as `qa_passed` and `export_ready` must not auto-export.

**Why:** Medical procurement reports should not become formal deliverables without an explicit user action.

**Rejected alternatives:** Auto-export after QA pass would reduce clicks but weakens the quality gate and human confirmation boundary.

**Limitations:** P0-1 does not implement the export gate; it records the decision for later stages.

**Evolution:** P0-4 initially enforced export blocking based on QA state. After standby-port smoke testing on 2026-07-06, the product decision changed: QA/manual-review state must remain visible, but users may still explicitly download Word for manual review.

**P0-4 result:** `/analysis/runs/{run_id}/download` now runs a quality export precheck before creating a docx. QA-passed reports are still not auto-exported; users must explicitly call the download action.

## TD-005: Memory Is Not Current Fact

**Background:** Long-term memory can improve style and analysis angle but can also pollute current notice facts.

**Decision:** Memory may guide style and quality rules only. It must never become a source for current dates, prices, products, volumes, regions, or policy facts.

**Why:** The evidence pack and current notice materials must remain the source of truth.

**Rejected alternatives:** Treating historical reports as retrievable facts would increase hallucination and stale-data risk.

**Limitations:** P1-1 does not add semantic search, relevance scoring, audit history, rollback, or UI-heavy memory management.

**Evolution:** P1-1 adds scoped `MemoryItem` retrieval and records `used_memory_ids`.

**P1-1 result:** Structured memory items are stored in `memory_items.json`, retrieved only when `status=approved` and scope matches the current evidence pack, and appended to `report_memory` in a separate section that says memory is guidance only and must not override the evidence pack.

## TD-006: Use Public Notice Data For Real Data Checks

**Background:** The user explicitly allows public government/platform notice data and wants real-data checks every stage.

**Decision:** Stage records may cite public notice URLs or minimized public evidence fixtures, without committing secrets or private full attachment content.

**Why:** Public data makes regression checks more realistic while staying shareable.

**Rejected alternatives:** Only synthetic tests would miss real parsing and notice-shape issues.

**Limitations:** Live public URLs can change.

**Evolution:** Unstable public URLs should be converted into minimized structured fixtures.

## TD-007: Synthetic AI Data Must Be Fixed Fixtures

**Background:** Automated tests need repeatable input and output expectations.

**Decision:** Synthetic AI data used in tests must be committed as fixtures or deterministic generated objects, not produced by live LLM calls.

**Why:** Live generation makes tests non-deterministic and expensive.

**Rejected alternatives:** Calling an LLM during tests would make failures hard to reproduce.

**Limitations:** Fixtures need updates when schema changes.

**Evolution:** P1-2 adds smoke evaluation cases and metrics.

**P1-2 result:** `scripts/run_quality_smoke.py` runs offline against `tests/eval_fixtures`, reuses `build_run_diagnostics`, and writes JSON/Markdown reports. It records deliverable rate, coverage, analysis depth, unsupported facts, summary-only risk, blocking issue counts, and diagnosis counts without calling live company services.

## TD-008: Do Not Prioritize React/Vue/Vite

**Background:** The current app is a FastAPI static-page monolith.

**Decision:** Keep the static-page frontend during workflow and quality work.

**Why:** The resume value is in backend workflow, evidence reliability, quality gates, and production behavior, not a frontend rewrite.

**Rejected alternatives:** Introducing a build system now would increase complexity and deployment risk.

**Limitations:** Static pages may become harder to maintain if UI complexity grows.

**Evolution:** A frontend migration can be reconsidered only after backend capabilities stabilize.

## TD-009: Do Not Start With Large app/main.py Refactor

**Background:** `app/main.py` currently owns many routes and helpers, and existing tests cover that shape.

**Decision:** P0-1 adds focused modules and minimal integration, but does not split routers or services broadly.

**Why:** Refactoring before behavior is stable would create a large diff and risk breaking existing flows.

**Rejected alternatives:** A full modular split first would be cleaner structurally but poor for incremental delivery.

**Limitations:** `main.py` remains large during early stages.

**Evolution:** P2-4 performs modular refactor after workflow, quality, and production boundaries are proven.

## TD-010: Local Engine MVP Uses Deterministic Template Generation

**Background:** P0-2 needs a local workflow path that can run without Dify, but P0-3 is the stage reserved for LLM provider abstraction and prompt hashing.

**Decision:** The local engine MVP runs serial deterministic nodes and creates a template draft from the evidence pack rather than calling any model.

**Why:** It proves the workflow boundary, run metadata, node ordering, and API integration without introducing secrets, nondeterministic tests, or prompt/version management too early.

**Rejected alternatives:** Calling an LLM directly in P0-2 would overlap with P0-3 and make tests dependent on model credentials. Building a full DAG runner in P0-2 would exceed the MVP scope.

**Limitations:** The output is a draft report and carries a manual-review warning. It is not a final AI-generated analysis report.

**Evolution:** P0-3 can replace the deterministic generate step with a provider-backed implementation while preserving the serial workflow/run metadata boundary.

**P0-3 result:** The deterministic template remains available as fallback context, but the generate node now calls a mock provider through the `LLMProvider` boundary and records prompt hash metadata.

## TD-011: Record Prompt Hashes With Model Call Metadata

**Background:** Once local workflow generation goes through a provider boundary, operators need to know which prompt version produced a run. File names alone are not enough because prompt contents can drift.

**Decision:** Load prompts as `PromptRef` objects containing name, version, path, and SHA-256. Persist prompt ref, prompt SHA-256, provider name, model name, and model call metadata in local workflow run results.

**Why:** The hash makes run output traceable to exact prompt content without storing provider secrets or relying on mutable prompt labels.

**Rejected alternatives:** Storing only prompt names would not detect edited prompt contents. Calling a real provider in P0-3 would introduce credentials and nondeterminism before the quality gate and evaluation loop exist.

**Limitations:** P0-3 only includes a deterministic mock provider and a minimal `local_report_generation:v1` prompt. It does not define real provider credentials or live health checks.

**Evolution:** A later provider stage can add real model clients behind the same interface, and P1-2 can compare prompt changes using repeatable quality smoke results.

## TD-012: Invalid LLM JSON Becomes Manual Review

**Background:** The local workflow expects provider output to be machine-readable JSON so downstream fields can be persisted consistently.

**Decision:** Parse provider output through a shared helper and raise `LLM_JSON_PARSE_ERROR` for malformed JSON or non-object JSON. The local workflow converts that error into a quality issue and `needs_manual_review` result instead of crashing the run.

**Why:** Operators should be able to inspect failed or malformed model output, and P0-4 can later use the same quality state to block export.

**Rejected alternatives:** Letting JSON exceptions bubble out would produce inconsistent failure records. Silently accepting malformed output would make report state ambiguous.

**Limitations:** This is a local workflow behavior only; the legacy Dify path still follows the existing response handling rules.

**Evolution:** P0-4 can treat this issue as an export-blocking quality gate condition.

## TD-013: Word Export Keeps Quality State But Allows Manual-Review Download

**Background:** Before P0-4, the analysis-run download endpoint created a Word file whenever `report_markdown` existed, even if `quality_check.passed` was false or the run carried a manual-review quality gate.

**Decision:** Keep a reusable `analysis_run_export_precheck` under `app/core/quality` and call it from `/analysis/runs/{run_id}/download` before report file creation. The precheck blocks only not-ready reports that have no exportable body. Reports in `needs_manual_review`, including failed QA or manual-review quality-gate results, remain downloadable by explicit user action.

**Why:** Real-environment smoke testing showed operators still need a Word artifact for manual review and offline correction, even when diagnostics mark the report as unsafe for direct delivery. Keeping diagnostics/status visible while allowing explicit download matches that workflow better than hard blocking.

**Rejected alternatives:** Auto-exporting after QA pass would violate the explicit user-action boundary. Hiding quality warnings in the Word path would make low-quality reports look safe. Rewriting the entire QA system would exceed P0-4 scope.

**Limitations:** Word download availability no longer means report quality passed. Users must treat `needs_manual_review`, failed QA issues, and diagnostics as delivery warnings rather than export blockers.

**Evolution:** P1/P2 stages can enrich the quality gate inputs and add operational diagnostics, while keeping Word export as an explicit user action rather than an automatic delivery signal.

## TD-014: Evidence Pack v3 Lite Uses Coarse Traceability Metadata

**Background:** Later repair, citation, and staged synthesis work need facts and table summaries to be traceable to their source material. The current database evidence pack already contains useful structured fields, but they did not carry stable evidence IDs or source spans.

**Decision:** Add a small `app/core/evidence` annotation layer for generated database packs. It preserves existing `pack_version: 2.0` compatibility and adds `evidence_schema_version: 3_lite`, `evidence_items`, `evidence_id`, coarse `source_span`, `confidence`, and `parse_risks`.

**Why:** Coarse spans give enough provenance for diagnostics and future repair contracts without increasing parser complexity or forcing a data migration. Keeping the full evidence index out of the Dify compact payload avoids unnecessary prompt bloat while inline IDs remain visible on facts, passages, attachments, and tables.

**Rejected alternatives:** Character-level offsets were rejected for P1-3 because existing HTML/PDF/Excel parsing paths do not preserve stable offsets. A separate evidence database was rejected because JSON pack persistence is still the current storage boundary.

**Limitations:** Historical packs are not backfilled automatically. Evidence IDs are stable for a generated pack but can change if selected material order or attachment order changes. Coarse spans do not identify exact character positions inside long paragraphs.

**Evolution:** P3 repair contracts can reference these IDs directly, and a later evidence browser or citation renderer can build on the same fields without changing P1-3 pack generation.

## TD-015: Retry And Resume Create Linked New Runs

**Background:** P2-1 adds user-facing control operations while the current execution model still uses in-process background threads and JSON run files. A cancelled or failed source run may still have a background worker returning late.

**Decision:** `cancel` updates the current run state in place, but `retry` and `resume` create a new linked run ID instead of reusing the source run ID.

**Why:** Reusing a cancelled run ID could let an old background result write into a resumed run if the status is changed back to `running`. A new linked run preserves the original outcome, keeps audit history simple, and uses the existing late-result guard safely.

**Rejected alternatives:** Reusing the same run ID for resume was rejected because it needs stronger worker identity/checkpoint semantics than P2-1 provides. Introducing a queue or durable worker platform was rejected because P2-1 scope is only a controlled shell; recovery and idempotency are P2-2.

**Limitations:** This does not deduplicate repeated user clicks and does not recover pending runs on startup. It also does not call a remote Dify cancellation endpoint; cancellation is local run-state cancellation.

**Evolution:** P2-2 should add input hashes, duplicate detection, and startup recovery policy around the linked-run behavior.

## TD-016: Input-Hash Idempotency And Stale Pending Recovery

**Background:** P2-1 created safe retry/cancel/resume operations, but a repeated click on `/analysis/run` could still start multiple background workers for the same evidence pack. Interrupted service processes could also leave `created` or `running` JSON run files indefinitely.

**Decision:** Compute an analysis-run `input_hash` from the pack ID, selected workflow backend, report-memory flag, report-memory hash, and scoped structured memory IDs. For normal `/analysis/run` requests, scan active JSON run records under a process-local creation lock and reuse an existing active run with the same hash. On startup and before new normal run creation, scan persisted runs and mark stale `created` or `running` records as `failed` with `RUN_RECOVERED_STALE_PENDING` metadata.

**Why:** This matches the current JSON-store architecture and prevents the highest-risk duplicate-submission case without introducing a queue service, database migration, or Dify configuration change. Marking stale runs failed keeps recovery explicit and lets users retry through the P2-1 linked-run path.

**Rejected alternatives:** A distributed lock or durable job queue was rejected because the project still runs as a single FastAPI service with local JSON state. Reusing stale run IDs was rejected because late worker writes and partial state need stronger checkpoint identity than P2-2 provides.

**Limitations:** Duplicate protection is process-local plus JSON scan; it is not a cross-container distributed lock. The hash must evolve if future stages add user-selectable generation parameters. Recovery does not resume partially completed work; it only makes stale state visible and retryable.

**Evolution:** P2-2.5 material compression cache can reuse this pattern for duplicate-build avoidance and safe fallback, while P2-3 can expose recovery counts and stale-run diagnostics through health/operations endpoints.

## TD-017: Material Compression Cache Is Derived SQLite State

**Background:** `/analysis/prepare` repeatedly rebuilds material objects from company database rows, attachment metadata, parsed attachment summaries, and role-specific context. P2-2.5 needs reusable material views without changing company database schema during local implementation.

**Decision:** Store material compression cache rows in a local SQLite database selected by `MATERIAL_COMPRESSION_CACHE_DB_PATH`, guarded by `MATERIAL_COMPRESSION_CACHE_ENABLED`. Each row is derived data keyed by material identity, view type, source hash, attachment metadata hash, attachment options hash, parser version, compression version, and schema version. The first implementation includes `material/full`, `primary/light`, `primary/safe`, and `auxiliary/summary`, but `/analysis/prepare` reads only `material/full`.

**Why:** Local SQLite preserves the current JSON/local-data deployment style and avoids changing the company announcement database during this stage. Reading only `material/full` keeps evidence detail equivalent to the dynamic path and prevents lightweight views from accidentally reducing report evidence. Miss, stale, corrupt, failed, building, or force-refresh states fall back to dynamic construction.

**Rejected alternatives:** Process-memory caching was rejected because material payloads can be large and would grow with FastAPI worker lifetime. Caching assembled evidence packs was rejected because final pack content depends on selection combination, material role, report options, and compact strategy. Independent attachment/table rows were deferred because v1 can embed attachment/table summaries inside material payloads.

**Limitations:** The prebuild script currently provides local-safe option parsing and dry-run behavior; real DB candidate scanning needs a later real-environment validation. Cache invalidation must be reviewed whenever source fields, parser inputs, or compression payload versions change.

**Evolution:** P2-3 can surface storage/cache health and cleanup behavior. A later MC slice can decide whether compact-stage use of `primary/light`, `primary/safe`, or `auxiliary/summary` is safe after fixed-case quality comparison.

## TD-018: Operations Health Is Read-Mostly And Cleanup Is Bounded

**Background:** P2-3 needs better operational visibility for database configuration, workflow/model provider configuration, local JSON storage, generated Word files, attachment parse cache, and material compression cache state.

**Decision:** Add read-mostly health and diagnostics endpoints in the FastAPI monolith: `/health/db`, `/health/llm`, `/health/storage`, `/ops/diagnostics`, and `/ops-diagnostics-ui`. Add `/ops/cleanup` for generated report files and expired attachment parse-cache entries only. Cleanup defaults to dry-run, requires an explicit scope, and enforces a `max_delete` limit.

**Why:** Operators need quick dependency classification without exposing secrets or accidentally calling external systems. Generated Word files and expired attachment parse cache are safe local derived artifacts to clean; evidence packs and analysis run records are user-visible workflow state and should not be deleted by a generic cleanup endpoint.

**Rejected alternatives:** A BI dashboard was rejected as out of scope. Polling Dify or the company database by default was rejected because health pages should not create unnecessary external load. Deleting packs/runs was rejected because it could break report detail pages and auditability.

**Limitations:** `/health/db?check=true` performs a real database check and should be used intentionally. Storage health uses bounded directory snapshots and does not prove every file is readable. The static diagnostics page is intentionally small and does not replace real 8100 smoke testing.

**Evolution:** P2-4 can move these routes into dedicated routers/services during modular refactor without changing the API contract.
