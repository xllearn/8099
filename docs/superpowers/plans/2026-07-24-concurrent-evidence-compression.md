# Concurrent Evidence Compression Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compress long evidence with four through six concurrent DeepSeek requests while preserving main/auxiliary material roles and the existing Dify `primary_materials` contract.

**Architecture:** Replace the untyped linear chunk list with role-aware chunk records, execute requests through a bounded `ThreadPoolExecutor`, and aggregate ordered summaries back into their source materials. Keep the existing all-or-nothing rule fallback and deploy only to `.87`.

**Tech Stack:** Python 3, `concurrent.futures.ThreadPoolExecutor`, `httpx`, FastAPI, Docker Compose, Dify 1.14.2.

---

### Task 1: Add bounded concurrent compression and contract-compatible aggregation

**Files:**
- Modify: `medical-notice-analyzer/app/generation_payload.py`

- [ ] **Step 1: Add concurrency and timing dependencies**

Add:

```python
import logging
import time
from concurrent.futures import ThreadPoolExecutor
```

Create the module logger:

```python
logger = logging.getLogger("medical_notice_analyzer")
```

- [ ] **Step 2: Make chunks retain their source material**

Change `_payload_chunks` to return dictionaries containing `role`,
`material_index`, `chunk_index`, and `text`. Split each material independently;
do not combine primary and auxiliary material text into one chunk.

```python
def _payload_chunks(payload: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for collection_key, role, role_label in (
        ("primary_materials", "primary", "主材料"),
        ("auxiliary_materials", "auxiliary", "辅助材料"),
    ):
        for material_index, material in enumerate(
            list(payload.get(collection_key) or [])
        ):
            document = (
                f"{role_label}{material_index + 1}\n"
                + json.dumps(material, ensure_ascii=False, separators=(",", ":"))
            )
            for chunk_index, part in enumerate(_split_text(document, limit)):
                chunks.append(
                    {
                        "role": role,
                        "material_index": material_index,
                        "chunk_index": chunk_index,
                        "text": part,
                    }
                )
    return chunks
```

- [ ] **Step 3: Add the bounded concurrent request helper**

Clamp the configured worker count to four through six, while never creating
more workers than chunks. `executor.map` preserves input order.

```python
def _request_long_summaries(
    chunks: list[dict[str, Any]],
    output_chars: int,
) -> tuple[list[str], int]:
    configured = _env_int("LONG_EVIDENCE_LLM_CONCURRENCY", 5)
    worker_limit = max(4, min(6, configured))
    workers = min(worker_limit, len(chunks))
    if not chunks:
        return [], 0
    started = time.perf_counter()
    with ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix="evidence-summary",
    ) as executor:
        summaries = list(
            executor.map(
                lambda chunk: _request_long_summary(chunk["text"], output_chars),
                chunks,
            )
        )
    logger.info(
        "long_evidence_llm_compression_completed chunk_count=%s concurrency=%s "
        "success_count=%s elapsed_ms=%s",
        len(chunks),
        workers,
        sum(bool(summary) for summary in summaries),
        round((time.perf_counter() - started) * 1000),
    )
    return summaries, workers
```

- [ ] **Step 4: Aggregate summaries into Dify-compatible material arrays**

Copy material metadata, remove uncompressed `body` and `attachments`, and join
the ordered summaries belonging to the material into its new `body`.

```python
def _compressed_materials(
    payload: dict[str, Any],
    chunks: list[dict[str, Any]],
    summaries: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[tuple[str, int], list[str]] = {}
    for chunk, summary in zip(chunks, summaries):
        grouped.setdefault(
            (str(chunk["role"]), int(chunk["material_index"])),
            [],
        ).append(summary)

    result: dict[str, list[dict[str, Any]]] = {
        "primary": [],
        "auxiliary": [],
    }
    for collection_key, role in (
        ("primary_materials", "primary"),
        ("auxiliary_materials", "auxiliary"),
    ):
        for material_index, material in enumerate(
            list(payload.get(collection_key) or [])
        ):
            compressed = {
                key: copy.deepcopy(value)
                for key, value in material.items()
                if key not in {"body", "attachments"}
            }
            compressed["body"] = "\n\n".join(
                grouped.get((role, material_index), [])
            )
            result[role].append(compressed)
    return result["primary"], result["auxiliary"]
```

- [ ] **Step 5: Replace the linear call and incompatible output**

In `prepare_generation_payload`, replace the list comprehension that calls
DeepSeek linearly:

```python
summaries, _ = _request_long_summaries(chunks, per_chunk_output)
```

When every chunk succeeds, build the compatible arrays and emit:

```python
compressed_primary, compressed_auxiliary = _compressed_materials(
    payload,
    chunks,
    summaries,
)
compressed = {
    "pack_variant": "generation_payload",
    "input_strategy": "llm_compressed",
    "effective_content_chars": effective_chars,
    "primary_materials": compressed_primary,
    "auxiliary_materials": compressed_auxiliary,
    "generation_guidance": copy.deepcopy(payload["generation_guidance"]),
    "generation_warnings": [],
}
```

Do not retain the standalone `compressed_evidence` field.

- [ ] **Step 6: Perform static checks only**

Per the user's explicit constraint, do not import, run, or test the project
locally. Run:

```powershell
git diff --check
git diff -- medical-notice-analyzer/app/generation_payload.py
```

Expected: no whitespace errors; the diff contains only the approved
concurrency, role-aware chunking, aggregation, and logging changes.

- [ ] **Step 7: Commit the implementation**

```powershell
git add medical-notice-analyzer/app/generation_payload.py
git commit -m "fix: parallelize compatible evidence compression"
```

### Task 2: Deploy only to the `.87` server

**Files:**
- Create on `.87`: a timestamped release directory and release Compose overlay.
- Do not modify `.88`.

- [ ] **Step 1: Record the current rollback target**

Read the running image, revision, health state, and Compose configuration file
list for `medical-notice-analyzer-development-87`. Expected rollback image:
`medical-notice-analyzer:quality87-deepseek-official-no-thinking-656b411-r1`.

- [ ] **Step 2: Transfer the committed source to `.87`**

Create a new timestamped directory under
`/opt/medical-notice-analyzer-releases/`, transfer only the committed project
source required by the Docker build, and do not transfer `.git`, data, reports,
site cache, or secrets.

- [ ] **Step 3: Build a uniquely tagged image**

Build the image on `.87` with the implementation commit SHA in the tag and
`APP_GIT_SHA`. The existing official DeepSeek API key remains in
`/opt/medical-notice-analyzer/.env.development-87`; do not print or copy it.

- [ ] **Step 4: Create and apply the release overlay**

Add a release Compose overlay that changes only the service image and
`APP_GIT_SHA`, then recreate the `.87` analyzer service with the complete
existing Compose file chain plus the new overlay.

- [ ] **Step 5: Verify deployment health**

Require both:

```text
Docker state: running
Docker health: healthy
HTTP GET http://192.168.34.87:8099/health: 200
```

If either check fails, restore the recorded image/overlay chain.

### Task 3: Run one server-side browser acceptance test

**Files:**
- Read only: `.87` run JSON, evidence-pack JSON, Docker logs, and Dify
  PostgreSQL workflow records.

- [ ] **Step 1: Prepare the Anhui long material in the browser**

Use the existing `.87` browser UI, select the previously tested Anhui medical
consumables payment-directory notice as the sole primary material, disable
report memory, and start one analysis.

- [ ] **Step 2: Record preparation metrics**

Record `pack_id`, `effective_content_chars`, `generation_payload_chars`,
`input_strategy`, preparation elapsed time, and the server log line containing
`chunk_count`, `concurrency`, `success_count`, and compression elapsed time.

- [ ] **Step 3: Verify the stored payload contract**

Read the new pack on `.87` and require:

```text
input_strategy == "llm_compressed"
primary_materials count >= 1
compressed_evidence field absent
generation_warnings empty
```

- [ ] **Step 4: Verify the Dify success branch**

Read the Dify `workflow_runs` and `workflow_node_executions` rows for the new
pack. Require:

```text
Evidence Pack OK? result == true
Generate Report JSON node executed
total_tokens > 0
no "evidence_pack has no primary_materials"
```

- [ ] **Step 5: Record final report status without overstating acceptance**

Record `run_id`, Dify elapsed time, total elapsed time, report characters,
fallback state, quality-gate status, and blocking issue codes. Contract repair
passes if the old missing-`primary_materials`/`OUTPUT_EMPTY` path is absent;
report business quality remains a separate result.

- [ ] **Step 6: Reconfirm environment isolation**

Read `.87` image/SHA/health and confirm no command, deployment, or configuration
change was applied to `.88`.
