# Dify Debug Passthrough Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a reversible `.87`-only debug mode that displays and exports Dify output without backend rewriting, material fallback generation, deletion, or repair.

**Architecture:** A single environment switch controls three existing boundaries: provider-output normalization, post-generation repair, and Word export. In debug mode, the backend preserves non-empty Dify Markdown byte-for-byte, runs `QualityGate` directly for diagnostics only, and replaces empty/error responses with a Dify diagnostic document that never contains evidence-pack material.

**Tech Stack:** Python 3.11, FastAPI, existing `ReportGenerationResult`/`QualityGate`, unittest, Docker Compose.

---

## File Map

- Modify `app/main.py`: define the debug switch, preserve raw Dify Markdown, provide diagnostic-only error output, bypass body-mutating repair, and permit draft Word export without sanitizing the stored Markdown.
- Modify `tests/test_records_api.py`: add focused contract tests for raw-body preservation, diagnostic-only fallback, and debug Word export.
- Create a release-only Compose overlay on `.87`: set the new image, revision, and `ENABLE_DIFY_DEBUG_PASSTHROUGH=true`.
- Do not modify `.88`, Dify itself, evidence-pack construction, quality rules, report templates, or frontend rendering code.

### Task 1: Add focused debug-passthrough contract tests

**Files:**
- Modify: `medical-notice-analyzer/tests/test_records_api.py`

- [ ] **Step 1: Add a raw-normalization test**

Add a test that sets `ENABLE_DIFY_DEBUG_PASSTHROUGH=true`, supplies a Dify workflow response whose `report_markdown` contains a technical marker and unusual whitespace, and asserts that `_normalize_dify_result` returns the exact original string.

```python
def test_dify_debug_passthrough_preserves_raw_report_markdown(self) -> None:
    raw_markdown = "调试信息：保留本行\n\n- 原始列表\n"
    response = make_dify_response(report_markdown=raw_markdown)
    with patch.dict(os.environ, {"ENABLE_DIFY_DEBUG_PASSTHROUGH": "true"}):
        result = main_module._normalize_dify_result(response, "pack_debug")
    self.assertEqual(result["report_markdown"], raw_markdown)
    self.assertTrue(result["dify_debug_passthrough"])
```

- [ ] **Step 2: Add a postprocess immutability test**

Pass a non-empty result through `_postprocess_generation_with_stages` and assert:

```python
with patch.dict(os.environ, {"ENABLE_DIFY_DEBUG_PASSTHROUGH": "true"}):
    processed = main_module._postprocess_generation_with_stages(result, pack)
self.assertEqual(processed["report_markdown"], result["report_markdown"])
self.assertEqual(processed["candidate_source"], "dify_raw_output")
self.assertFalse(processed["fallback_used"])
self.assertIn("quality_gate", processed)
```

The test must use content that the normal `ForbiddenPhraseRepairer` would change so the assertion proves the repair pipeline was skipped.

- [ ] **Step 3: Add an error-diagnostic test**

Call `_fallback_result_from_dify_error` with a pack containing a unique sentinel and assert:

```python
with patch.dict(os.environ, {"ENABLE_DIFY_DEBUG_PASSTHROUGH": "true"}):
    diagnostic = main_module._fallback_result_from_dify_error(error, pack, "pack_debug")
self.assertEqual(diagnostic["candidate_source"], "dify_debug_diagnostic")
self.assertTrue(diagnostic["diagnostic_output_used"])
self.assertFalse(diagnostic["fallback_used"])
self.assertNotIn("UNIQUE_EVIDENCE_SENTINEL", diagnostic["report_markdown"])
self.assertIn("OUTPUT_EMPTY", diagnostic["report_markdown"])
```

- [ ] **Step 4: Add a debug Word-export test**

Persist a debug-mode run whose Markdown contains a phrase rejected by the normal safety path. Patch `_publish_markdown_docx` and assert the download endpoint returns 200 and passes the unchanged Markdown into the renderer.

```python
self.assertEqual(response.status_code, 200)
publish_mock.assert_called_once()
self.assertEqual(publish_mock.call_args.args[0], raw_markdown)
```

- [ ] **Step 5: Do not run the project locally**

Per user instruction, only review syntax and diff locally. Execute the focused tests after the code is copied to the `.87` release directory.

### Task 2: Implement the reversible debug mode

**Files:**
- Modify: `medical-notice-analyzer/app/main.py`

- [ ] **Step 1: Add the environment-switch helper**

Near the existing environment helpers, add:

```python
def _dify_debug_passthrough_enabled() -> bool:
    return _env_bool("ENABLE_DIFY_DEBUG_PASSTHROUGH", False)
```

- [ ] **Step 2: Preserve provider Markdown**

Change `_normalize_dify_result` to choose between the raw Dify string and the existing cleanup:

```python
raw_report_markdown = str(output.get("report_markdown") or "")
report_markdown = (
    raw_report_markdown
    if _dify_debug_passthrough_enabled()
    else _clean_model_output(raw_report_markdown)
)
```

Add `dify_debug_passthrough` and `candidate_source` metadata without changing the title or body.

- [ ] **Step 3: Add a diagnostic-result builder**

Create a helper that accepts a provider error code, message, detail, pack ID, optional workflow run ID, and optional raw excerpt. It must:

- truncate detail/excerpt to bounded lengths;
- produce Markdown containing only Dify diagnostic fields;
- set `status=needs_manual_review`;
- set `candidate_source=dify_debug_diagnostic`;
- set `diagnostic_output_used=true`;
- set `fallback_used=false`;
- add the provider failure code to quality and generation diagnostics;
- never read material fields from `pack`.

- [ ] **Step 4: Bypass mutating repair but retain diagnostics**

At the beginning of `_postprocess_generation_with_stages`, branch on the debug switch:

```python
if _dify_debug_passthrough_enabled():
    staged = _apply_debug_quality_diagnostics(result, pack, input_stages, compact, timing)
    staged["dify_debug_passthrough"] = True
    staged["candidate_source"] = (
        "dify_raw_output"
        if str(staged.get("report_markdown") or "")
        else "dify_debug_diagnostic"
    )
    staged["fallback_used"] = False
    return staged
```

`_apply_debug_quality_diagnostics` must run `QualityGate().run(...)` directly and restore the original `report_title`, `report_markdown`, and `report_ir` afterward. It must still write provider, cleanup, and quality stage diagnostics.

- [ ] **Step 5: Route all Dify errors and watchdog timeouts to diagnostics**

At the top of `_fallback_result_from_dify_error`, return the diagnostic result when the switch is enabled. This covers synchronous provider errors and `_save_analysis_timeout_fallback_if_running` without duplicating logic.

- [ ] **Step 6: Permit draft Word export in debug mode**

In `_normalize_analysis_run_schema`, calculate:

```python
debug_export = (
    _dify_debug_passthrough_enabled()
    and raw_status in {"finished", "needs_manual_review"}
    and body_safety.has_body
    and _word_export_enabled()
)
word_export_available = normal_word_export_available or debug_export
draft_word_export_available = word_export_available
final_word_export_available = bool(
    normal_word_export_available and deliverable and not debug_export
)
```

Keep body-safety hits in diagnostics even when debug export is allowed.

- [ ] **Step 7: Export stored Markdown without sanitizing it**

In `download_analysis_run_report`, when the saved run has `dify_debug_passthrough=true`, use the stored Markdown directly as `report_source`. Otherwise retain `_safe_markdown_formal_body`.

```python
if report_markdown:
    if record.get("dify_debug_passthrough"):
        report_source = report_markdown
    else:
        report_markdown = _safe_markdown_formal_body(report_markdown)
        report_source = report_markdown
```

- [ ] **Step 8: Review the diff**

Run:

```powershell
git diff --check
git diff --stat
git diff -- medical-notice-analyzer/app/main.py medical-notice-analyzer/tests/test_records_api.py
```

Expected: no whitespace errors; only the debug-mode code and focused tests change.

### Task 3: Commit, deploy only to `.87`, and run minimal server verification

**Files:**
- Create on `.87`: `/opt/medical-notice-analyzer-releases/<release>/docker-compose.release-<sha>-r1.yml`
- Preserve on `.87`: current Compose file list, current image ID, and `/opt/medical-notice-analyzer/.env.development-87`

- [ ] **Step 1: Commit the implementation**

```powershell
git add -- medical-notice-analyzer/app/main.py medical-notice-analyzer/tests/test_records_api.py
git commit -m "feat: add Dify debug passthrough mode"
```

- [ ] **Step 2: Capture rollback state read-only**

Record:

```text
current container image ID
current APP_GIT_SHA
current Compose config file list
current /health response
```

Do not print API keys or full environment contents.

- [ ] **Step 3: Copy the exact committed source to a new `.87` release directory**

Create a timestamped release directory, copy the committed project snapshot, and create a Compose overlay that sets:

```yaml
services:
  medical-notice-analyzer:
    image: medical-notice-analyzer:dify-debug-passthrough-<short-sha>-r1
    environment:
      APP_GIT_SHA: <full-sha>
      ENABLE_DIFY_DEBUG_PASSTHROUGH: "true"
```

- [ ] **Step 4: Build the `.87` image**

Run Docker Compose using the existing `.87` Compose file stack plus the new release overlay. Do not run or modify `.88`.

- [ ] **Step 5: Run focused tests on `.87`**

Run only the four new debug-passthrough tests inside the built image or release source environment. Expected: all four pass.

- [ ] **Step 6: Recreate the `.87` backend**

Use the full current `.87` Compose stack plus the new overlay and recreate only the `medical-notice-analyzer` service.

- [ ] **Step 7: Verify deployment**

Confirm:

```text
/health returns 200
container is healthy
APP_GIT_SHA equals the implementation commit
ENABLE_DIFY_DEBUG_PASSTHROUGH=true
.87 still routes to the new Dify
```

- [ ] **Step 8: Verify behavior without a real announcement**

Use server-side synthetic records to verify:

- non-empty Dify Markdown hash is unchanged after postprocessing;
- quality diagnostics are present;
- empty/error output becomes `dify_debug_diagnostic`;
- diagnostic body excludes a unique evidence sentinel;
- both records can render a Word file.

- [ ] **Step 9: Verify `.88` remained unchanged**

Read `.88` container image ID, creation timestamp, and relevant capacity/Dify environment values. Compare to the pre-change state when available; do not recreate or restart it.

- [ ] **Step 10: Push the branch to `xllearn/8099`**

Use Python `dulwich` if Git for Windows HTTPS credential handling fails. Push only:

```text
codex/new-server-87-medical-notice-analyzer-20260722
```

Do not merge into `main`.
