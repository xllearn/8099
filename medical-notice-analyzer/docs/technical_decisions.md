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

**Evolution:** P0-4 enforces export blocking based on QA state.

**P0-4 result:** `/analysis/runs/{run_id}/download` now runs a quality export precheck before creating a docx. QA-passed reports are still not auto-exported; users must explicitly call the download action.

## TD-005: Memory Is Not Current Fact

**Background:** Long-term memory can improve style and analysis angle but can also pollute current notice facts.

**Decision:** Memory may guide style and quality rules only. It must never become a source for current dates, prices, products, volumes, regions, or policy facts.

**Why:** The evidence pack and current notice materials must remain the source of truth.

**Rejected alternatives:** Treating historical reports as retrievable facts would increase hallucination and stale-data risk.

**Limitations:** P0-1 does not change memory behavior.

**Evolution:** P1-1 adds scoped `MemoryItem` retrieval and records `used_memory_ids`.

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

## TD-013: Word Export Is Blocked At The Download Boundary

**Background:** Before P0-4, the analysis-run download endpoint created a Word file whenever `report_markdown` existed, even if `quality_check.passed` was false or the run carried a manual-review quality gate.

**Decision:** Add a reusable `analysis_run_export_precheck` under `app/core/quality` and call it from `/analysis/runs/{run_id}/download` before report file creation. The precheck blocks failed QA and manual-review quality gates with `QUALITY_GATE_BLOCKED`.

**Why:** The download endpoint is the last shared path before a report becomes a formal Word deliverable. Blocking there protects both direct API calls and static UI clicks.

**Rejected alternatives:** Only disabling frontend buttons would not protect API callers. Rewriting the entire QA system would exceed P0-4 scope. Auto-exporting after QA pass would violate the explicit user-action boundary.

**Limitations:** Historical run records without a stored quality gate and without a readable evidence pack can only be checked by their stored `quality_check`. Live server behavior was not exercised in P0-4.

**Evolution:** P1/P2 stages can enrich the quality gate inputs and add operational diagnostics, while keeping the download boundary as the final export guard.
