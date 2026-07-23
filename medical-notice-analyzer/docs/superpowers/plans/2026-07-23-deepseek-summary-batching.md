# `.87` DeepSeek Summary Batching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single large ambiguous-table summary request with bounded four-table batches and one-table recovery for partial responses, then validate only on `.87`.

**Architecture:** Keep `_request_summaries` as the single-request transport and contract validator. Add a small orchestration helper that sends at most four complete table payloads per call, retries only missing tables from a partially successful batch, and stops after a completely empty batch to prevent cascaded timeouts. Preserve program-owned row projection and every existing report safety gate.

**Tech Stack:** Python 3, FastAPI application code, `httpx`, `unittest`, Docker, Docker Compose, PowerShell, SSH, browser automation.

---

## Local execution boundary

Do not start the project, import application modules, run Python, run unit tests, call local HTTP endpoints, or build/run Docker locally. Local work is limited to reading files, applying patches, Git diff checks, secret scans, commits, and pushing the branch. All executable verification occurs after deployment inside `.87`.

### Task 1: Add the server-run regression specification

**Files:**
- Create: `medical-notice-analyzer/tests/test_evidence_summary_llm_batching.py`

- [ ] **Step 1: Add a partial-batch recovery test**

Create a `unittest.TestCase` that patches `app.evidence_summary_llm._request_summaries`. Supply six table payloads. Make the first four-table response omit one ID, return that ID from the one-table retry, and return both IDs from the final two-table batch. Assert call sizes are `[4, 1, 2]` and all six IDs are present.

```python
from __future__ import annotations

import unittest
from unittest.mock import patch

from app import evidence_summary_llm as summary


def _tables(count: int) -> list[dict[str, object]]:
    return [{"table_id": f"table-{index}"} for index in range(count)]


def _result(table_id: str) -> dict[str, object]:
    return {"summary": f"summary-{table_id}", "column_map": {"enterprise": 0, "product": 1}}


class EvidenceSummaryBatchingTests(unittest.TestCase):
    def test_retries_only_missing_tables_from_partial_batch(self) -> None:
        calls: list[list[str]] = []

        def fake_request(batch: list[dict[str, object]]) -> dict[str, dict[str, object]]:
            ids = [str(table["table_id"]) for table in batch]
            calls.append(ids)
            if ids == ["table-0", "table-1", "table-2", "table-3"]:
                return {table_id: _result(table_id) for table_id in ids if table_id != "table-2"}
            return {table_id: _result(table_id) for table_id in ids}

        with patch.object(summary, "_request_summaries", side_effect=fake_request):
            result = summary._request_summaries_batched(_tables(6))

        self.assertEqual([len(call) for call in calls], [4, 1, 2])
        self.assertEqual(set(result), {f"table-{index}" for index in range(6)})
```

- [ ] **Step 2: Add an empty-batch circuit-breaker test**

Add a second test where the first four-table request returns `{}`. Assert that the helper returns `{}` and makes exactly one call, proving it does not expand an upstream outage into individual retries.

```python
    def test_stops_after_completely_empty_batch(self) -> None:
        calls: list[list[str]] = []

        def fake_request(batch: list[dict[str, object]]) -> dict[str, dict[str, object]]:
            calls.append([str(table["table_id"]) for table in batch])
            return {}

        with patch.object(summary, "_request_summaries", side_effect=fake_request):
            result = summary._request_summaries_batched(_tables(8))

        self.assertEqual(result, {})
        self.assertEqual([len(call) for call in calls], [4])
```

- [ ] **Step 3: Record the already observed RED evidence**

Do not run the test locally. The pre-implementation `.87` evidence is already RED:

```text
20-table current request: 45.131 seconds, 0 DeepSeek tables, EVIDENCE_SUMMARY_LLM_UNAVAILABLE=true
8-table request: 7.541 seconds, 7/8 valid tables
4-table sweep: one batch returned 2/4; each omitted table succeeded individually in 0.8-1.6 seconds
```

The new test also cannot import because `_request_summaries_batched` does not yet exist.

### Task 2: Implement bounded batching

**Files:**
- Modify: `medical-notice-analyzer/app/evidence_summary_llm.py`

- [ ] **Step 1: Increase the bounded candidate count and define the batch size**

Replace the candidate limit and add the request batch constant:

```python
_MAX_AMBIGUOUS_TABLES = 40
_MAX_MODEL_TABLES_PER_REQUEST = 4
```

Keep `_MAX_MODEL_ROWS_PER_TABLE = 40` and `_MAX_MODEL_INPUT_CHARS = 60_000` unchanged.

- [ ] **Step 2: Add the batching helper after `_request_summaries`**

```python
def _request_summaries_batched(tables: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    combined: dict[str, dict[str, Any]] = {}
    for offset in range(0, len(tables), _MAX_MODEL_TABLES_PER_REQUEST):
        batch = tables[offset : offset + _MAX_MODEL_TABLES_PER_REQUEST]
        expected = {
            str(table.get("table_id") or ""): table
            for table in batch
            if str(table.get("table_id") or "")
        }
        batch_results = _request_summaries(batch)
        accepted = {
            table_id: batch_results[table_id]
            for table_id in expected
            if table_id in batch_results
        }
        combined.update(accepted)
        if not accepted:
            break

        for table_id, table in expected.items():
            if table_id in accepted:
                continue
            retry_result = _request_summaries([table])
            if table_id in retry_result:
                combined[table_id] = retry_result[table_id]
    return combined
```

- [ ] **Step 3: Route enrichment through the helper**

Replace:

```python
model_results = _request_summaries(request_tables)
```

with:

```python
model_results = _request_summaries_batched(request_tables)
```

Do not change the existing rule-first mapping, model/rule conflict rejection, `_project_rows`, self-contained validation, or warning condition.

- [ ] **Step 4: Perform static-only local checks**

Run only:

```powershell
git diff --check
git diff -- medical-notice-analyzer/app/evidence_summary_llm.py medical-notice-analyzer/tests/test_evidence_summary_llm_batching.py
rg -n "sk-llm-|APIKey：|EVIDENCE_SUMMARY_LLM_API_KEY=" medical-notice-analyzer/app medical-notice-analyzer/tests
```

Expected: clean diff; no real credential in source or tests. Do not run Python, tests, HTTP, or Docker locally.

- [ ] **Step 5: Commit the implementation**

```powershell
git add medical-notice-analyzer/app/evidence_summary_llm.py medical-notice-analyzer/tests/test_evidence_summary_llm_batching.py
git commit -m "fix: batch DeepSeek evidence summaries"
```

### Task 3: Push the isolated new-server branch

**Files:** none.

- [ ] **Step 1: Confirm branch and scope**

```powershell
git status --short --branch
git log -3 --oneline
```

Expected branch: `codex/new-server-87-medical-notice-analyzer-20260722`; no merge into `main`.

- [ ] **Step 2: Push the current branch**

Push to `xllearn/8099` using the existing remote/connector path. Do not create or merge a pull request.

### Task 4: Back up and deploy only `.87`

**Files:**
- Create remotely: timestamped release directory under `/opt/medical-notice-analyzer-releases/`
- Create remotely: `.87` release Compose overlay derived from the current active overlay

- [ ] **Step 1: Read current `.87` provenance without exposing environment values**

Record the current image, revision, Compose project, config file list, working directory, port, mounts, network, HTTP health, and Docker health. The target must remain `medical-notice-analyzer-development-87-r7` and `192.168.34.87:8099`.

- [ ] **Step 2: Create a protected backup**

Create `/opt/medical-notice-analyzer/deploy_backups/<timestamp>-pre-summary-batching/` with mode `0700`. Copy the active `.87` environment file and Compose overlays with mode `0600`, archive the current release source, record the old image/revision, and retain the existing rollback image. Do not print the environment contents.

- [ ] **Step 3: Build an immutable image from the exact committed source**

Create a Git archive from the implementation commit, verify SHA-256 after transfer, extract into a new release directory, and build a unique image tag. Do not overwrite `latest` or the currently running image.

- [ ] **Step 4: Recreate only the analyzer service**

Use the exact current `.87` Compose project, environment file, and config-file ordering, with the new release overlay last:

```text
docker compose -p medical-notice-analyzer-development-87-r7 --env-file <current-.87-env> <exact-current-files> -f <new-overlay> up -d --no-deps medical-notice-analyzer
```

Do not execute `down`, prune images, change Dify, or connect to `.88`.

### Task 5: Run all executable verification on `.87`

**Files:** none locally.

- [ ] **Step 1: Verify deployment readiness**

Confirm Docker health is `healthy`, `GET /health` returns HTTP 200, the image/revision is the implementation commit, the summary feature remains enabled, and capacity remains `240000` characters / `870400` UTF-8 bytes. Check only key presence/length, never its value.

- [ ] **Step 2: Run the targeted regression tests inside `.87`**

Run the new batching test in the deployed release/container. Expected:

```text
test_retries_only_missing_tables_from_partial_batch ... ok
test_stops_after_completely_empty_batch ... ok
```

- [ ] **Step 3: Verify the clear-header path inside `.87`**

Run a server-side one-off Python check that patches `httpx.Client` to fail if instantiated, then processes a clear table with enterprise, product, specification, and purchase volume headers. Expected: `semantic_summary_source=rules`, no model HTTP client, and original zero/nonzero values preserved.

- [ ] **Step 4: Re-prepare the Zhejiang material three times**

For each attempt call `POST /analysis/prepare` with:

```json
{
  "primary_materials": [
    {
      "menu_code": "project_notice",
      "articleid": "c1d2fc19-1269-42e7-b8db-dddbe2b1e711"
    }
  ],
  "auxiliary_materials": [],
  "enable_attachment_download": true,
  "force_refresh_attachments": true
}
```

For every new pack assert: 30 total tables, 6 rule tables, 24 DeepSeek tables, no unavailable warning, all projected row values equal independent projection from original rows, and all summaries pass the self-contained filter. Record only counts, durations, run IDs, and non-reversible hashes.

- [ ] **Step 5: Generate three reports through the browser**

Open `http://192.168.34.87:8099/records-ui`, search exact area `浙江省`, select the identified procurement notice as the sole primary material, and click `生成分析报告`. Repeat three times with fresh prepare/run cycles. Capture each `run_id` from `/analysis-runs/{run_id}` and wait for `finished`, `needs_manual_review`, or `failed`.

- [ ] **Step 6: Validate each formal report on `.87`**

For every non-failed run verify the report body exists and contains no appendix, attachment-reading instruction, page number, table number, row/column trace, unprocessed JSON block, or technical implementation note. Confirm Word download is governed by the server response and record whether each report is `finished` or `needs_manual_review`.

- [ ] **Step 7: Report acceptance honestly**

Separate technical deployment from business-quality acceptance. Any missing DeepSeek table, failed run, forbidden phrase, malformed JSON, or non-self-contained instruction means the three-run Zhejiang acceptance is not complete. Keep rollback assets and leave `.88` unchanged.
