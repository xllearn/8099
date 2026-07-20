# Dify Quality Mode Capacity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the backend and Dify workflow to one quality-only profile with 160k/220k/240k character tiers, an 850 KiB UTF-8 guard, safe revision inputs, consistent timeouts, and verified integration with the new Dify application.

**Architecture:** Keep the complete evidence pack persisted by the backend and derive a deterministic compact pack for every Dify read. A single capacity policy owns character, byte, timeout, and retry limits; compaction uses value-weighted budgets and must fail closed if either hard limit remains exceeded. Initial generation and user-feedback revision both fetch the same compact pack by `pack_id`; secrets remain in runtime environment files only.

**Tech Stack:** Python 3, FastAPI, Pydantic, httpx, unittest, YAML-based Dify workflow, Docker Compose, PowerShell.

---

### Task 1: Add the quality capacity policy and UTF-8 metrics

**Files:**
- Modify: `medical-notice-analyzer/tests/test_records_api.py`
- Modify: `medical-notice-analyzer/app/main.py`

- [ ] **Step 1: Write failing policy tests**

Update the test imports:

```python
from typing import Any
from unittest.mock import MagicMock, patch
```

Add these reusable helpers to `RecordsApiTests`:

```python
def _quality_pack(
    self,
    *,
    primary_text: str = "主材料正文",
    core_summary: str = "核心附件摘要",
    primary_count: int = 1,
) -> dict[str, Any]:
    primary_materials = []
    for index in range(primary_count):
        primary_materials.append(
            {
                "menu_code": f"menu{index}",
                "articleid": f"article{index}",
                "title": f"主材料{index + 1}",
                "content_text": primary_text,
                "content_summary": primary_text[:1000],
                "key_facts": [],
                "attachments": [
                    {
                        "articleattid": f"att{index}",
                        "filename": "核心价格附件.pdf",
                        "fileext": ".pdf",
                        "core_attachment": True,
                        "parse_status": "parsed",
                        "summary": core_summary,
                        "key_facts": [],
                        "important_sections": [],
                        "table_summaries": [],
                    }
                ],
            }
        )
    return {
        "pack_id": "pack_quality_capacity",
        "primary_materials": primary_materials,
        "auxiliary_materials": [],
        "combined_key_facts": [],
        "warnings": [],
        "generation_guidance": {},
    }

def _dify_test_config(
    self,
    *,
    timeout_seconds: float = 600,
    max_attempts: int = 3,
) -> dict[str, Any]:
    return {
        "base_url": "http://dify.test/v1",
        "api_key": "test-api-key",
        "endpoint": "/workflows/run",
        "response_mode": "blocking",
        "user": "test-user",
        "timeout_seconds": timeout_seconds,
        "large_input_timeout_seconds": 900.0,
        "max_attempts": max_attempts,
        "retry_backoff_seconds": 0.0,
        "staged_timeout_seconds": 900.0,
        "staged_max_attempts": 1,
    }
```

Add tests that define the public behavior:

```python
def test_dify_input_thresholds_default_to_quality_profile(self) -> None:
    with patch.dict(main_module.os.environ, {}, clear=False):
        for name in [
            "DIFY_PLATFORM_STRING_MAX_CHARS",
            "DIFY_FULL_INPUT_MAX_CHARS",
            "DIFY_SAFE_COMPACT_MAX_CHARS",
            "DIFY_EVIDENCE_PACK_HARD_MAX_CHARS",
            "DIFY_VARIABLE_HARD_MAX_CHARS",
            "DIFY_EVIDENCE_PACK_HARD_MAX_BYTES",
        ]:
            main_module.os.environ.pop(name, None)
        thresholds = main_module._dify_input_thresholds()
    self.assertEqual(thresholds["platform_limit"], 400000)
    self.assertEqual(thresholds["full_input"], 160000)
    self.assertEqual(thresholds["safe_compact"], 220000)
    self.assertEqual(thresholds["hard_limit"], 240000)
    self.assertEqual(thresholds["hard_limit_bytes"], 870400)

def test_refresh_dify_size_fields_counts_utf8_bytes(self) -> None:
    compact = {"text": "中😀"}
    chars = main_module._refresh_dify_size_fields(compact)
    serialized = json.dumps(compact, ensure_ascii=False, sort_keys=True)
    self.assertEqual(chars, len(serialized))
    self.assertEqual(compact["final_dify_input_bytes"], len(serialized.encode("utf-8")))
    self.assertGreater(compact["final_dify_input_bytes"], compact["final_dify_input_chars"])
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest `
  tests.test_records_api.RecordsApiTests.test_dify_input_thresholds_default_to_quality_profile `
  tests.test_records_api.RecordsApiTests.test_refresh_dify_size_fields_counts_utf8_bytes -v
```

Expected: failures because the new defaults and `_refresh_dify_size_fields` do not exist.

- [ ] **Step 3: Implement the policy and stable size calculation**

Implement:

```python
def _dify_input_thresholds() -> dict[str, int]:
    platform_limit = max(10_000, _env_int("DIFY_PLATFORM_STRING_MAX_CHARS", 400_000))
    legacy_hard = _env_int("DIFY_VARIABLE_HARD_MAX_CHARS", 240_000)
    hard_limit = min(
        platform_limit,
        max(10_000, _env_int("DIFY_EVIDENCE_PACK_HARD_MAX_CHARS", legacy_hard)),
    )
    full_input = min(hard_limit, max(1_000, _env_int("DIFY_FULL_INPUT_MAX_CHARS", 160_000)))
    safe_compact = min(hard_limit, max(full_input, _env_int("DIFY_SAFE_COMPACT_MAX_CHARS", 220_000)))
    hard_limit_bytes = max(16_384, _env_int("DIFY_EVIDENCE_PACK_HARD_MAX_BYTES", 870_400))
    return {
        "platform_limit": platform_limit,
        "hard_limit": hard_limit,
        "hard_limit_bytes": hard_limit_bytes,
        "full_input": full_input,
        "safe_compact": safe_compact,
        "light_compact": safe_compact,
    }

def _refresh_dify_size_fields(compact: dict[str, Any]) -> int:
    previous: tuple[int, int] | None = None
    for _ in range(8):
        serialized = json.dumps(compact, ensure_ascii=False, sort_keys=True)
        current = (len(serialized), len(serialized.encode("utf-8")))
        compact["compact_pack_chars"] = current[0]
        compact["final_dify_input_chars"] = current[0]
        compact["compact_pack_bytes"] = current[1]
        compact["final_dify_input_bytes"] = current[1]
        if current == previous:
            return current[0]
        previous = current
    return int(compact["final_dify_input_chars"])
```

Retain `_refresh_dify_char_fields` as a compatibility wrapper that calls the new function.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Step 2 command. Expected: both tests pass.

- [ ] **Step 5: Commit**

```powershell
git add medical-notice-analyzer/app/main.py medical-notice-analyzer/tests/test_records_api.py
git commit -m "feat: add Dify quality capacity policy"
```

### Task 2: Replace 80k fixed caps with value-weighted compaction

**Files:**
- Modify: `medical-notice-analyzer/tests/test_records_api.py`
- Modify: `medical-notice-analyzer/app/main.py`

- [ ] **Step 1: Write failing budget and boundary tests**

Add tests for dynamic budget reflow and dual hard limits:

```python
def test_quality_budget_prioritizes_primary_and_core_attachment(self) -> None:
    budget = main_module._dify_quality_budget(
        target_chars=160000,
        primary_count=1,
        auxiliary_count=3,
    )
    self.assertEqual(budget["target_chars"], 160000)
    self.assertGreaterEqual(budget["primary_body_chars"], 50000)
    self.assertGreaterEqual(budget["primary_core_attachment_chars"], 70000)
    self.assertLessEqual(budget["auxiliary_chars"], 16000)
    self.assertEqual(sum(budget[key] for key in budget["allocated_keys"]), 160000)

def test_compact_pack_stays_below_char_and_byte_hard_limits(self) -> None:
    pack = self._quality_pack(primary_text="中😀" * 140000, core_summary="核心证据" * 60000)
    with patch.dict(
        main_module.os.environ,
        {
            "DIFY_FULL_INPUT_MAX_CHARS": "160000",
            "DIFY_SAFE_COMPACT_MAX_CHARS": "220000",
            "DIFY_EVIDENCE_PACK_HARD_MAX_CHARS": "240000",
            "DIFY_EVIDENCE_PACK_HARD_MAX_BYTES": "870400",
        },
        clear=False,
    ):
        compact = main_module._compact_evidence_pack_for_dify(pack)
    serialized = json.dumps(compact, ensure_ascii=False, sort_keys=True)
    self.assertLessEqual(len(serialized), 240000)
    self.assertLessEqual(len(serialized.encode("utf-8")), 870400)
    self.assertEqual(compact["quality_profile"], "quality")

def test_two_small_primary_materials_are_not_forced_to_staged_generation(self) -> None:
    pack = self._quality_pack(primary_count=2, primary_text="规则" * 1000)
    compact = main_module._compact_evidence_pack_for_dify(pack)
    self.assertNotEqual(compact["input_strategy"], "staged_generation")
```

- [ ] **Step 2: Run tests and verify RED**

Run the three new tests with `python -m unittest ... -v`.

Expected: failures because `_dify_quality_budget`, `quality_profile`, byte enforcement, and the new two-primary policy do not exist.

- [ ] **Step 3: Implement dynamic budgets**

Add `_dify_quality_budget` with 35/45/8/12 initial allocation and deterministic remainder assignment:

```python
def _dify_quality_budget(*, target_chars: int, primary_count: int, auxiliary_count: int) -> dict[str, Any]:
    usable = max(10_000, int(target_chars))
    values = {
        "primary_body_chars": usable * 35 // 100,
        "primary_core_attachment_chars": usable * 45 // 100,
        "auxiliary_chars": usable * (8 if auxiliary_count else 0) // 100,
    }
    values["metadata_reserve_chars"] = usable - sum(values.values())
    values.update(
        {
            "target_chars": usable,
            "primary_count": max(1, primary_count),
            "auxiliary_count": max(0, auxiliary_count),
            "allocated_keys": [
                "primary_body_chars",
                "primary_core_attachment_chars",
                "auxiliary_chars",
                "metadata_reserve_chars",
            ],
        }
    )
    return values
```

Use the budget to derive per-material content and attachment caps. Preserve core attachments first, then move unused auxiliary/metadata capacity back to core attachments and primary body. Remove the unconditional `len(primary_source) >= 2` staged decision.

- [ ] **Step 4: Enforce both hard limits and fail closed**

Update the compaction loop to continue while either serialized characters or UTF-8 bytes exceed the selected target. The final fallback removes auxiliary detail and low-value attachment tails. If the output remains above a hard limit, raise:

```python
class DifyEvidencePackLimitError(ValueError):
    def __init__(self, *, chars: int, bytes_size: int, char_limit: int, byte_limit: int):
        super().__init__("compact evidence pack exceeds Dify quality limits")
        self.chars = chars
        self.bytes_size = bytes_size
        self.char_limit = char_limit
        self.byte_limit = byte_limit
```

`GET /analysis/packs/{pack_id}` converts this to HTTP 413 with a bounded diagnostic message; it never returns an oversized payload.

- [ ] **Step 5: Run focused tests and existing pack tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_records_api.RecordsApiTests -v
```

Expected: all records API tests pass after updating old 65k/75k/80k assertions to use returned configured limits.

- [ ] **Step 6: Commit**

```powershell
git add medical-notice-analyzer/app/main.py medical-notice-analyzer/tests/test_records_api.py
git commit -m "feat: expand quality evidence compaction"
```

### Task 3: Enrich core attachment extraction without expanding ordinary files

**Files:**
- Modify: `medical-notice-analyzer/tests/test_records_api.py`
- Modify: `medical-notice-analyzer/app/attachment_parser.py`
- Modify: `medical-notice-analyzer/app/main.py`

- [ ] **Step 1: Write failing core PDF tests**

```python
def test_core_pdf_uses_quality_extract_limit(self) -> None:
    reader = MagicMock()
    page = MagicMock()
    page.extract_text.return_value = "证据" * 50000
    reader.pages = [page]
    with patch.object(attachment_parser_module, "PdfReader", return_value=reader):
        with patch.dict(
            attachment_parser_module.os.environ,
            {
                "ATTACHMENT_PDF_MAX_EXTRACT_CHARS": "60000",
                "ATTACHMENT_CORE_PDF_MAX_EXTRACT_CHARS": "120000",
            },
            clear=False,
        ):
            text = attachment_parser_module._parse_pdf(b"%PDF", core_attachment=True)
    self.assertGreater(len(text), 60000)
    self.assertLessEqual(len(text), 120000)

def test_ordinary_pdf_keeps_existing_extract_limit(self) -> None:
    reader = MagicMock()
    page = MagicMock()
    page.extract_text.return_value = "证据" * 50000
    reader.pages = [page]
    with patch.object(attachment_parser_module, "PdfReader", return_value=reader):
        with patch.dict(attachment_parser_module.os.environ, {"ATTACHMENT_PDF_MAX_EXTRACT_CHARS": "60000"}, clear=False):
            text = attachment_parser_module._parse_pdf(b"%PDF", core_attachment=False)
    self.assertLessEqual(len(text), 60000)
```

- [ ] **Step 2: Run tests and verify RED**

Expected: `_parse_pdf` rejects the `core_attachment` argument.

- [ ] **Step 3: Implement role-aware extraction**

Change the signatures to:

```python
def _parse_pdf(content: bytes, *, core_attachment: bool = False) -> str:
    max_pages = max(1, _int_env("ATTACHMENT_PDF_MAX_PAGES", 120))
    env_name = "ATTACHMENT_CORE_PDF_MAX_EXTRACT_CHARS" if core_attachment else "ATTACHMENT_PDF_MAX_EXTRACT_CHARS"
    default_chars = 120000 if core_attachment else 60000
    max_chars = max(5000, _int_env(env_name, default_chars))
    ...

def parse_attachment_bytes(
    content: bytes,
    filename: str,
    fileext: str,
    filesize: int | str | None = None,
    *,
    core_attachment: bool = False,
) -> dict[str, Any]:
    ...
```

Pass the already calculated `core_attachment` flag from `_build_database_attachment` in `app/main.py`. Keep ZIP child parsing ordinary unless the child itself is classified as core.

- [ ] **Step 4: Increase only compact core summaries**

Set core summary/key-fact/section limits to the quality values while leaving auxiliary limits unchanged. Preserve `evidence_id`, `source_span`, table structure, and parse risks.

- [ ] **Step 5: Run focused and attachment tests**

Run all `RecordsApiTests` and confirm both existing large-PDF fallbacks and new role-aware limits pass.

- [ ] **Step 6: Commit**

```powershell
git add medical-notice-analyzer/app/attachment_parser.py medical-notice-analyzer/app/main.py medical-notice-analyzer/tests/test_records_api.py
git commit -m "feat: enrich core attachment evidence"
```

### Task 4: Make revision and timeout policies use the quality compact pack

**Files:**
- Modify: `medical-notice-analyzer/tests/test_records_api.py`
- Modify: `medical-notice-analyzer/app/main.py`

- [ ] **Step 1: Write failing revision payload test**

```python
def test_dify_revision_payload_does_not_send_full_evidence_pack(self) -> None:
    captured: dict[str, Any] = {}

    def fake_post(url, config, payload, timeout_seconds):
        captured.update(payload)
        return {
            "workflow_run_id": "wf-quality-revision",
            "data": {
                "status": "succeeded",
                "outputs": {
                    "pack_id": "pack_quality_revision",
                    "status": "finished",
                    "report_title": "修订报告",
                    "report_markdown": "## 导语\n\n这是根据用户反馈完成的完整修订报告正文。",
                    "quality_check": {"passed": True, "issues": []},
                    "generation_warnings": [],
                },
            },
        }

    with patch.object(main_module, "_post_dify_workflow_once", side_effect=fake_post), patch.object(
        main_module, "_dify_config", return_value=self._dify_test_config()
    ):
        main_module._call_dify_revision_workflow(
            pack_id="pack_quality_revision",
            run_id="run_quality_revision",
            feedback="补充企业影响",
            current_report="## 报告",
            evidence_pack={"must_not_be_sent": "完整证据" * 10000},
            analysis_highlight=True,
        )
    self.assertNotIn("evidence_pack", captured["inputs"])
    self.assertEqual(captured["inputs"]["pack_id"], "pack_quality_revision")
```

- [ ] **Step 2: Write failing timeout tests**

```python
def test_large_quality_pack_uses_900_second_single_attempt_policy(self) -> None:
    config = self._dify_test_config(timeout_seconds=600, max_attempts=3)
    pack = {
        "input_strategy": "safe_compact",
        "final_dify_input_chars": 230000,
        "final_dify_input_bytes": 820000,
    }
    with patch.dict(main_module.os.environ, {"DIFY_LARGE_INPUT_TIMEOUT_SECONDS": "900"}, clear=False):
        policy = main_module._dify_request_policy(config, pack)
    self.assertEqual(policy["timeout_seconds"], 900)
    self.assertEqual(policy["max_attempts"], 1)

def test_watchdog_is_request_timeout_plus_grace(self) -> None:
    with patch.object(main_module, "_dify_config", return_value=self._dify_test_config()), patch.dict(
        main_module.os.environ,
        {"DIFY_WATCHDOG_GRACE_SECONDS": "30", "DIFY_WATCHDOG_MAX_SECONDS": "0"},
        clear=False,
    ):
        self.assertEqual(main_module._analysis_watchdog_timeout_seconds({"final_dify_input_chars": 230000}), 930)
```

- [ ] **Step 3: Run tests and verify RED**

Expected: revision still sends `evidence_pack`, large policy is capped by the old staged timeout, and watchdog does not return 930.

- [ ] **Step 4: Implement revision and timeout changes**

Remove `evidence_pack` from the revision payload and production caller. Retain it only as an optional ignored compatibility parameter on `_call_dify_revision_workflow`, so older callers cannot accidentally forward the full pack. Use `_post_dify_workflow_with_wall_timeout` for revision too. Add `large_input_timeout_seconds` to `_dify_config`; select it when final characters exceed 220000, byte use exceeds 90%, or real staging is requested. Force `max_attempts=1` for large inputs. Calculate watchdog strictly from the selected request timeout plus grace.

- [ ] **Step 5: Run focused tests and all records API tests**

Expected: revision payload, retry, wall-timeout, watchdog, and existing user-feedback tests pass.

- [ ] **Step 6: Commit**

```powershell
git add medical-notice-analyzer/app/main.py medical-notice-analyzer/tests/test_records_api.py
git commit -m "fix: align revision and timeout quality policy"
```

### Task 5: Update diagnostics, configuration, documentation, and workflow contract

**Files:**
- Modify: `medical-notice-analyzer/tests/test_records_api.py`
- Modify: `medical-notice-analyzer/app/diagnostics.py`
- Modify: `medical-notice-analyzer/.env.example`
- Modify: `medical-notice-analyzer/docker-compose.yml`
- Modify: `medical-notice-analyzer/README.md`
- Modify: `medical-notice-analyzer/AGENTS.md`
- Modify: `medical-notice-analyzer/dify_workflow_pack_id_human_style.yml`
- Modify: `medical-notice-analyzer/scripts/build_dify_chatflow_dsl.py`
- Modify: `medical-notice-analyzer/scripts/enhance_dify_workflow_graph.py`
- Modify: `medical-notice-analyzer/scripts/refactor_workflow_v2.py`

- [ ] **Step 1: Write failing diagnostics test**

```python
def test_diagnostics_exposes_quality_char_and_byte_usage(self) -> None:
    pack = self._quality_pack(primary_text="证据" * 5000)
    compact = main_module._compact_evidence_pack_for_dify(pack)
    diagnostics = build_pack_diagnostics(pack, compact)
    self.assertEqual(diagnostics["final_dify_input_bytes"], compact["final_dify_input_bytes"])
    self.assertEqual(diagnostics["hard_limit_bytes"], 870400)
    self.assertIn("byte_usage_ratio", diagnostics)
    self.assertIn("quality_profile", diagnostics)
```

- [ ] **Step 2: Run test and verify RED**

Expected: byte diagnostics fields are absent.

- [ ] **Step 3: Implement dynamic diagnostics**

Remove 75000/80000 literals. Generate near-limit warnings when either character or byte use is at least 90%. Expose the quality profile, capacity snapshot, budget allocation, final bytes, and byte usage ratio.

- [ ] **Step 4: Update runtime configuration and docs**

Add these non-secret values to `.env.example` and Compose:

```dotenv
DIFY_PLATFORM_STRING_MAX_CHARS=400000
DIFY_FULL_INPUT_MAX_CHARS=160000
DIFY_SAFE_COMPACT_MAX_CHARS=220000
DIFY_EVIDENCE_PACK_HARD_MAX_CHARS=240000
DIFY_EVIDENCE_PACK_HARD_MAX_BYTES=870400
DIFY_LARGE_INPUT_TIMEOUT_SECONDS=900
DIFY_WATCHDOG_MAX_SECONDS=0
DIFY_WATCHDOG_GRACE_SECONDS=30
ATTACHMENT_CORE_PDF_MAX_EXTRACT_CHARS=120000
```

Update README and AGENTS with the complete-pack/compact-pack distinction and the new capacity table. Update generator scripts so regenerating the workflow cannot restore 60k/80k limits.

- [ ] **Step 5: Update the Dify start contract**

Keep one quality workflow. Define these exact start variables:

```yaml
- variable: pack_id
  label: pack_id
  type: text-input
  required: true
  max_length: 128
- variable: mode
  label: mode
  type: text-input
  required: false
  max_length: 32
- variable: run_id
  label: run_id
  type: text-input
  required: false
  max_length: 128
- variable: feedback
  label: feedback
  type: paragraph
  required: false
  max_length: 4000
- variable: current_report
  label: current_report
  type: paragraph
  required: false
  max_length: 20000
- variable: analysis_highlight
  label: analysis_highlight
  type: text-input
  required: false
  max_length: 8
- variable: use_report_memory
  label: use_report_memory
  type: text-input
  required: false
  max_length: 8
- variable: report_memory
  label: report_memory
  type: paragraph
  required: false
  max_length: 15000
```

Both branches first fetch and parse evidence through the existing `pack_id` HTTP node. A condition node routes `mode == "user_feedback_revision"` to a user-feedback revision prompt that receives `feedback`, `current_report`, and the parsed compact evidence; all other values route to the current initial generation and QA chain. The revision branch returns the same output contract (`report_title`, `report_markdown`, `quality_check`, `generation_warnings`, `status`, and `pack_id`) and must not define or consume a raw `evidence_pack` start variable.

- [ ] **Step 6: Validate YAML and run tests**

Run:

```powershell
.\.venv\Scripts\python.exe -c "import yaml; yaml.safe_load(open('dify_workflow_pack_id_human_style.yml', encoding='utf-8')); print('yaml ok')"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Expected: valid YAML and zero test failures.

- [ ] **Step 7: Commit**

```powershell
git add medical-notice-analyzer
git commit -m "docs: align Dify quality workflow configuration"
```

Before committing, inspect staged files and verify no `.env` or API key is staged.

### Task 6: Configure, publish, and verify the new Dify integration

**Files:**
- Modify but never commit: `medical-notice-analyzer/.env`
- Modify on the backend server: `/opt/medical-notice-analyzer/.env`
- Publish on the Dify server: the existing copied application

- [ ] **Step 1: Verify secret exclusions**

Run:

```powershell
git check-ignore -v medical-notice-analyzer/.env
git grep -n -E "app-[A-Za-z0-9]{16,}|sk-[A-Za-z0-9]{16,}" -- medical-notice-analyzer
```

Expected: `.env` is ignored and tracked files contain no real secret.

- [ ] **Step 2: Configure the local runtime**

Set `DIFY_BASE_URL` to the new Dify API base and `DIFY_WORKFLOW_API_KEY` to the user-provided new application key in the ignored `.env`. Do not print the key.

- [ ] **Step 3: Validate the API key belongs to the published target app**

Call the new Dify `/workflows/run` endpoint with an existing safe `pack_id`. Record only HTTP status, workflow run ID, status, elapsed time, and bounded error text. Never log request headers or the key.

- [ ] **Step 4: Publish the reviewed DSL**

Back up the current Dify application graph, import the validated quality workflow into the existing copied app, publish it, and confirm the app ID is unchanged. Verify the HTTP node still calls `http://192.168.34.88:8099/analysis/packs/{pack_id}`.

- [ ] **Step 5: Deploy backend changes**

Back up `/opt/medical-notice-analyzer/.env` and the deployed application directory, update code and non-secret configuration, set the new Dify base URL/key in the server environment, rebuild/restart the service, and retain the old Dify values for immediate rollback.

- [ ] **Step 6: Run production checks**

Verify:

```text
GET http://192.168.34.88:8099/health
GET http://192.168.34.88:8099/health/llm
GET http://192.168.34.88:8099/records-ui
GET http://192.168.34.88:8099/analysis/packs/{pack_id}
```

Then run real 160k, 220k, and near-240k quality cases. Confirm final character and byte metrics, evidence-tail sentinel, report completeness, quality checks, and elapsed time.

- [ ] **Step 7: Run final secret and repository checks**

Run full tests, `git diff --check`, `git status --short`, and the tracked-secret pattern scan. Confirm `.env` remains unstaged and no secret appears in logs or generated artifacts.
