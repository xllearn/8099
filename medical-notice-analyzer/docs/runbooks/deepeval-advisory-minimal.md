# Minimal DeepEval advisory sidecar

This sidecar reads completed local analysis runs and evidence packs, then
publishes advisory scores on a separate loopback-only endpoint. It never
changes a source run, evidence pack, deliverable, report status, release
decision, or user response.

## Safe default

The compose file starts with runtime evaluation disabled. Discovery and
enrollment still run, so eligible reports are persisted as paused jobs without
calling a Judge.

Create only the sidecar state directory. The analysis-run and evidence-pack
directories belong to the primary service: verify that they already exist and
are readable, but do not create them or change their ownership or mode. On
Linux, use the same variables and defaults as the compose file:

```bash
test -d "${DEEPEVAL_ANALYSIS_RUN_SOURCE:-./data/analysis_runs}" &&
test -r "${DEEPEVAL_ANALYSIS_RUN_SOURCE:-./data/analysis_runs}" &&
test -d "${DEEPEVAL_EVIDENCE_PACK_SOURCE:-./data/evidence_packs}" &&
test -r "${DEEPEVAL_EVIDENCE_PACK_SOURCE:-./data/evidence_packs}" &&
sudo install -d -m 0700 -o 10001 -g 10001 \
  "${DEEPEVAL_ADVISORY_STATE_SOURCE:-./deepeval-advisory-state}"
```

The bind definitions set `create_host_path: false`. A missing directory must
make Compose fail instead of being silently created as root.

```powershell
docker compose -f docker-compose.deepeval-advisory.yml up -d --build
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8100/health
```

The compose project contains only `deepeval-advisory`. It does not declare,
restart, or depend on the 8099 service. The analysis-run and evidence-pack
mounts are read-only; only `deepeval-advisory-state` is writable.
The sidecar is capped at 1 CPU, 2 GiB memory, and 128 processes.

## Stage C boundary and future evaluation

The base compose is Stage C runtime-disabled. It does not inject a Judge API
key and attaches the sidecar to an `internal: true` network that mechanically
blocks external Judge traffic. Do not reuse any Dify generation or
`EVIDENCE_SUMMARY_LLM` credential for advisory scoring.

Future real-Judge evaluation requires a separate approval covering all of:

- a dedicated Judge credential injected only into the worker environment;
- an approved egress override and immutable Judge model/version;
- a reviewed enrollment count, cost ceiling, and stop condition;
- a smoke run that keeps the hard per-scan evaluation limit enabled.

Never place that credential in this compose file, a repository file, command
argument, log, or support message.

Every scan first enrolls all eligible reports, including safe unavailable jobs
for missing or invalid evidence projections. It then handles cache hits
without consuming the limit and performs at most
`DEEPEVAL_MAX_JUDGE_EVALUATIONS_PER_SCAN` real evaluations. The base compose
fixes that limit to `1` and fixes the scan interval at 300 seconds. Runtime
settings reject intervals below 300 seconds and reject any per-scan Judge
limit other than `1`; these are hard cost ceilings, not deployment tuning
knobs. Remaining jobs stay `pending` for a later scan; they are not marked
failed or unavailable.

If any Judge setting is absent, enrollment continues but the job remains
paused with availability reported as unavailable. No placeholder score is
created.

## Runtime control

These commands write only the sidecar control directory:

```powershell
docker compose -f docker-compose.deepeval-advisory.yml exec deepeval-advisory python scripts/run_deepeval_advisory.py --pause
docker compose -f docker-compose.deepeval-advisory.yml exec deepeval-advisory python scripts/run_deepeval_advisory.py --status
docker compose -f docker-compose.deepeval-advisory.yml exec deepeval-advisory python scripts/run_deepeval_advisory.py --resume
```

Never start a one-shot worker alongside the resident sidecar. Stop the
resident process first, run the one-shot, and restore the resident process
after it exits:

```powershell
docker compose -f docker-compose.deepeval-advisory.yml stop deepeval-advisory
docker compose -f docker-compose.deepeval-advisory.yml run --rm --no-deps deepeval-advisory python scripts/run_deepeval_advisory.py --once
docker compose -f docker-compose.deepeval-advisory.yml up -d --no-deps deepeval-advisory
```

## Read-only reporting

- `GET /health` returns sidecar health only.
- `GET /metrics.json` returns coverage, metric sample counts/means, Judge
  evaluation and cache-savings counts, and recent pseudonymous run references.
- `GET /` returns the same safe aggregate in HTML and refreshes every 30
  seconds.

The reporting surface does not include raw run IDs, pack IDs, report or
evidence text, URLs, source paths, or credentials. It binds to
`127.0.0.1:8100` on the host by default. An approved deployment can set the
non-sensitive `DEEPEVAL_ADVISORY_BIND_IP` to a specific host address; do not
use a wildcard bind.

## Failure and rollback

Malformed or missing packs increment discovery errors and do not stop 8099.
Judge, cache, or reporting failures stay in sidecar state. Stop or remove only
the sidecar to roll back:

```powershell
docker compose -f docker-compose.deepeval-advisory.yml stop deepeval-advisory
```

Stopping the sidecar does not remove its state. Delete state only under a
separate, explicitly approved retention operation.

Run exactly one sidecar process. This minimal implementation deliberately has
no cross-process lease or single-flight protocol; do not scale the service to
multiple replicas.
