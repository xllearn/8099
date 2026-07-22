# .87 Dify Near-Limit Hardcode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the obsolete `.87` diagnostic constants `75000/80000` with fixed `220000/240000` character values and deploy only the `.87` analyzer.

**Architecture:** Keep the existing diagnostic calculation and warning code. Define two module constants in `app/diagnostics.py`, use the fixed near-limit constant in the existing strict comparison, and derive the message from the fixed hard-limit constant. Build from the exact `.87` source branch and deploy through the existing Compose project.

**Tech Stack:** Python 3, Docker, Docker Compose, PowerShell, SSH.

---

### Task 1: Apply the fixed diagnostic thresholds

**Files:**
- Modify: `medical-notice-analyzer/app/diagnostics.py`

- [ ] **Step 1: Add fixed constants**

Add at module scope:

```python
DIFY_PACK_NEAR_LIMIT_CHARS = 220_000
DIFY_PACK_HARD_LIMIT_CHARS = 240_000
```

- [ ] **Step 2: Replace the legacy comparison and message**

Use:

```python
if dify_compact_pack_chars > DIFY_PACK_NEAR_LIMIT_CHARS:
    diagnosis.append(
        _diagnosis(
            "DIFY_PACK_NEAR_LIMIT",
            "warning",
            f"传给 Dify 的精简证据包接近 {DIFY_PACK_HARD_LIMIT_CHARS} 字符限制，后续可能需要进一步压缩。",
        )
    )
```

- [ ] **Step 3: Inspect the source diff without running tests**

Run:

```powershell
git diff -- medical-notice-analyzer/app/diagnostics.py
git diff --check
```

Confirm that no production file other than `app/diagnostics.py` changed. Per the user's explicit instruction, do not run test commands.

- [ ] **Step 4: Commit the source change**

```powershell
git add medical-notice-analyzer/app/diagnostics.py
git commit -m "fix: update quality87 Dify near-limit warning"
```

### Task 2: Build and deploy the immutable `.87` image

**Files:**
- Create remotely: a timestamped directory under `/opt/medical-notice-analyzer-releases/`
- Create remotely: a copied and mechanically updated Compose release overlay

- [ ] **Step 1: Package the committed source**

Create a Git archive from `HEAD:medical-notice-analyzer`, transfer it to a new timestamped `.87` release directory, and extract it there. Reuse the verified `Dockerfile.offline-overlay-r2` from the preceding release.

- [ ] **Step 2: Build a new image from the current rollback image**

Use `medical-notice-analyzer:legacy-capacity87-4c5e646a93d7-r2` as `BASE_IMAGE`. Tag the new image with the new Git revision and set the image revision labels to that revision.

- [ ] **Step 3: Create the release overlay**

Copy the current `docker-compose.release-4c5e646a93d7-r4.yml` into the new release directory, then mechanically replace only the image tag, `APP_GIT_SHA`, revision labels, and release label. Preserve every existing feature flag.

- [ ] **Step 4: Recreate only the `.87` analyzer**

Run the existing Compose project `medical-notice-analyzer-development-87-r7` with the same three base files, the current release overlay, and the new release overlay. Use `up -d --no-deps medical-notice-analyzer`.

- [ ] **Step 5: Observe deployment state without functional testing**

Read `docker inspect` for the `.87` container and confirm only that it is running with the new image and revision. Read the `.88` container ID and confirm it was not recreated. Do not call health, diagnostics, Dify, or business endpoints.

- [ ] **Step 6: Report the explicit verification limitation**

State that the source and deployment were changed but no functional or regression tests were run, so the diagnostic behavior is not claimed as tested.

