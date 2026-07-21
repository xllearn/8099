# 8099 Regression Baselines

`P0.2-b2.5` introduces three versioned JSON contracts:

- `8099.regression-manifest/v2`: fixed case identity, role, source-preserving body hash, attachment metadata plus streamed raw-byte or text-only content verification, subsets, capability labels, exclusions, and the replay request. Replay identities must exactly match the frozen material roles.
- `8099.fixed-regression/v2`: one release-node run containing environment, subset, repeat count, samples, failures, snapshot references, and their SHA-256 values.
- `8099.regression-baseline/v2`: baseline `B`, evaluator/rules hashes, and the `server_test` fixed-10 quality plus fixed-3 performance metrics.

The committed manifest is `tests/fixtures/8099_regression_cases.json`. The runner embeds only a bounded, field-whitelisted `manifest_contract`, never the source manifest or arbitrary extension fields. `fixed3` is a strict subset of `fixed10`. A database title, body, attachment metadata, or identity change fails before `/analysis/prepare`; refreshing the manifest requires a new `manifest_version` and explicit review.

Each successful sample stores replayable material verification receipts, prepare/evidence, compact pack, run, report, diagnostics, history detail/list, Word-state observations, and downloaded Word snapshots. Original attachment bytes are streamed and rehashed for every attempt; they are not cached or persisted in the baseline artifact. The summary is written atomically and never embeds full report or evidence text; it references snapshots by relative path and SHA-256. The evidence-pack SHA-256, input strategy, provider ids, failure-code sets, report structure, material identity/record-hash binding, and phase timings are part of each sample contract.

The evaluator treats `summary.json` as an index, not as quality evidence. It verifies every referenced hash, reloads the frozen snapshots, recomputes FormalBody/DOCX forbidden-expression hits, reruns deterministic `unsupported_eval_v1`, and rechecks run/history/compact/Word state before aggregating metrics. A summary field cannot override the recomputed value.

## Commands

```powershell
python scripts/run_fixed_regression.py --base-url http://127.0.0.1:8099 --manifest tests/fixtures/8099_regression_cases.json --subset fixed3 --repeat 3 --stage S0 --environment server_test --output-dir artifacts/history-baseline/server-fixed3 --report-dir <report-dir> --poll-seconds 2
python scripts/run_fixed_regression.py --base-url http://127.0.0.1:8099 --manifest tests/fixtures/8099_regression_cases.json --subset fixed10 --repeat 1 --stage S0 --environment server_test --output-dir artifacts/history-baseline/server-fixed10 --report-dir <report-dir> --poll-seconds 2
python scripts/evaluate_regression_baseline.py --artifact artifacts/history-baseline/server-fixed3/summary.json --output artifacts/history-baseline/server-fixed3/evaluation.json --verify-only
python scripts/evaluate_regression_baseline.py --artifact artifacts/history-baseline/server-fixed10/summary.json --output artifacts/history-baseline/server-fixed10/evaluation.json --verify-only
python scripts/freeze_regression_baseline.py --baseline-id B --manifest tests/fixtures/8099_regression_cases.json --evaluation artifacts/history-baseline/server-fixed3/evaluation.json --evaluation artifacts/history-baseline/server-fixed10/evaluation.json --output docs/quality-baselines/s0.json
```

Evaluation is read-only and deterministic. A release fails when any run fails, a forbidden expression is found, run/history/compact/Word state differs, a Dify workflow id is missing, identity/metric coverage is incomplete, a non-deliverable run lacks exactly one scalar primary failure code, a deliverable run carries a primary failure code, a snapshot hash is invalid, or a matching fixed-3 p95 exceeds `max(baseline * 1.30, baseline + 30 seconds)`.

Baseline `B` can only be frozen from the two required `server_test` cells: fixed-10 (all available cases, repeat 1) and fixed-3 (3 cases x 3 attempts). The current manifest declares 10 identities and explicitly excludes one case as `SOURCE_ATTACHMENT_UNAVAILABLE`, so fixed-10 runs 9 selected cases while retaining the excluded identity, reason, and observation time. All inputs must have zero execution failures, forbidden expressions, state contradictions, and identity/metric gaps. Fixed-10 quality totals are not mixed with repeated fixed-3 performance samples, and later comparisons fail on environment drift, unsupported-fact regression, or deliverable/manual-review regression.
