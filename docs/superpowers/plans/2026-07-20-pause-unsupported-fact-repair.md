# Pause Unsupported Fact Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pause the destructive backend unsupported-fact repair for new 8099 runs while retaining evidence indexing, the VBP quality gate, and the strict delivery gate.

**Architecture:** This is a runtime-configuration-only behavior change. The existing `QualityGate` continues to detect unsupported facts and drive manual-review state, while `UnsupportedFactRepairer` is disabled by the Compose environment. The deployed application code, image, and application revision remain pinned to `88309d57dcda056efa250cf37c2d77757536445e`.

**Tech Stack:** Python `unittest`, YAML/Docker Compose, existing S4 runtime deployment layout

---

## Scope and stop conditions

- Do not change production Python code, report content rules, Word safety rules, Dify integrations, compact/PDF/OCR behavior, `EvidenceItem`, history, checkpoint, or diagnostics semantics.
- Do not generate reports or run fixed3/fixed10, real-data, Dify, or Word tests.
- Do not merge or update GitHub `main`.
- Do not rebuild the image; reuse `medical-notice-analyzer:s4-88309d5`.
- Do not modify 8100, prior backups, or runtime data.
- Stop before deployment if the live image, application SHA, deployment SHA, or GitHub `main` no longer equals the approved baseline, or if an unknown concurrent operation is changing 8099.

## Task 1: Lock the runtime configuration contract with a failing test

**Files:**

- Modify: `tests/test_release_tools.py`

- [ ] In `ReleaseCliTests.test_s4_runtime_compose_preserves_s3_gates_and_enables_recovery`, assert:

  ```python
  self.assertEqual("false", environment["ENABLE_UNSUPPORTED_FACT_REPAIR"])
  self.assertEqual("true", environment["ENABLE_EVIDENCE_INDEX"])
  ```

- [ ] Preserve the existing assertions that the VBP quality gate and strict delivery gate remain enabled.

- [ ] Run the focused test and confirm it fails because the current Compose value is `"true"`:

  ```powershell
  py -3 -m unittest tests.test_release_tools.ReleaseCliTests.test_s4_runtime_compose_preserves_s3_gates_and_enables_recovery -v
  ```

## Task 2: Pause unsupported-fact repair in the S4 runtime Compose file

**Files:**

- Modify: `docker-compose.s4-runtime.yml`

- [ ] Change only:

  ```yaml
  ENABLE_UNSUPPORTED_FACT_REPAIR: "false"
  ```

- [ ] Re-run the focused test and confirm it passes.

- [ ] Run the bounded configuration tests:

  ```powershell
  py -3 -m unittest `
    tests.test_release_tools.ReleaseCliTests.test_s4_runtime_compose_preserves_s3_gates_and_enables_recovery `
    tests.test_controlled_repair.ControlledRepairPipelineTests.test_s2_repair_flags_are_default_off `
    -v
  ```

- [ ] Inspect the parsed Compose environment and confirm the exact four-flag matrix:

  | Variable | Expected |
  |---|---|
  | `ENABLE_UNSUPPORTED_FACT_REPAIR` | `"false"` |
  | `ENABLE_EVIDENCE_INDEX` | `"true"` |
  | `ENABLE_VBP_QUALITY_GATE` | `"true"` |
  | `ENABLE_STRICT_DELIVERY_GATE` | `"true"` |

## Task 3: Review, verify, commit, and push the isolated branch

**Files:**

- Review: `docker-compose.s4-runtime.yml`
- Review: `tests/test_release_tools.py`
- Review: `docs/superpowers/specs/2026-07-20-pause-unsupported-fact-repair-design.md`
- Review: `docs/superpowers/plans/2026-07-20-pause-unsupported-fact-repair.md`

- [ ] Confirm the diff contains no production-code change and no unrelated file.
- [ ] Run `git diff --check`.
- [ ] Scan the staged diff for secrets, `.env`, caches, backups, temporary files, and runtime artifacts.
- [ ] Obtain an independent read-only review of the change and deployment procedure.
- [ ] Commit with message `config: pause unsupported fact repair`.
- [ ] Push `codex/pause-unsupported-fact-repair` using Dulwich and verify the remote branch SHA.
- [ ] Verify GitHub `main` remains `88309d57dcda056efa250cf37c2d77757536445e`.

## Task 4: Back up and deploy the configuration-only change

**Production target:** `/opt/medical-notice-analyzer` on `192.168.34.88`

- [ ] Read-only preflight:
  - container name, image, state, OOM flag, restart count;
  - image revision and `APP_GIT_SHA`;
  - `.deployed_commit`;
  - current four-flag matrix;
  - 8099 `/health` and `/records-ui`;
  - 8100 container identity/state for before/after comparison;
  - conflicting deployment/build processes.
- [ ] Require the approved baseline before mutation:
  - GitHub `main`: `88309d57dcda056efa250cf37c2d77757536445e`;
  - deployed/image/application SHA: the same full SHA;
  - image: `medical-notice-analyzer:s4-88309d5`.
- [ ] Create a new timestamped backup under:

  ```text
  /opt/medical-notice-analyzer/deploy_backups/<timestamp>-unsupported-fact-repair-pause
  ```

- [ ] Back up the live Compose file and record its SHA-256 plus a sanitized preflight manifest.
- [ ] Upload the reviewed Compose file to a temporary path, verify its SHA-256, and atomically replace only `/opt/medical-notice-analyzer/docker-compose.s4-runtime.yml`.
- [ ] Recreate only the 8099 application service with `--no-deps --force-recreate`, explicitly retaining:
  - `S4_IMAGE=medical-notice-analyzer:s4-88309d5`;
  - `S4_GIT_SHA=88309d57dcda056efa250cf37c2d77757536445e`.
- [ ] Do not build, pull, or modify the 8100 service.
- [ ] If recreation or health verification fails, restore the backed-up Compose file, recreate the same 8099 service with the same image/SHA, and verify the original repair setting is restored.

## Task 5: Perform bounded post-deployment verification

- [ ] Confirm `GET /health` is healthy and `GET /records-ui` returns HTTP 200.
- [ ] Confirm the 8099 container is running, not OOM-killed, and has no unexpected restart loop.
- [ ] Confirm the deployed four-flag matrix exactly matches Task 2.
- [ ] Confirm image, image revision, `APP_GIT_SHA`, and `.deployed_commit` remain pinned to the approved baseline.
- [ ] Confirm the 8100 container identity/state is unchanged.
- [ ] Inspect only the deployment log delta for severe startup errors; do not run report generation or data tests.
- [ ] Confirm the new backup and rollback source are readable and locatable.
- [ ] Confirm GitHub `main` remains unchanged and the feature branch points to the committed configuration change.
- [ ] Record the deployment evidence and exact rollback command in `C:\tmp\8099-handoffs\unsupported-fact-repair-pause-handoff.md`.
