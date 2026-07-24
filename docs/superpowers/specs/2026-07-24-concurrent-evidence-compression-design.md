# Evidence Compression Concurrency and Dify Contract Design

## Scope

Only the new `.87` medical-notice-analyzer environment is changed. The `.88`
legacy environment, Dify workflow nodes, quality gates, DeepSeek endpoint,
model, byte limits, and character limits remain unchanged.

## Required behavior

Long evidence is split by source material and then by the existing chunk-size
limit. Each chunk retains its source role and material index. The server sends
up to five DeepSeek requests concurrently by default. The
`LONG_EVIDENCE_LLM_CONCURRENCY` setting may change this value, but runtime
clamps it to the user-approved range of four through six workers.

Results are placed back in original material and chunk order, independent of
request completion order. Each material's summaries are joined into that
material's `body`. Metadata needed to identify the material remains present.

The resulting generation payload continues to use:

- `primary_materials` for primary evidence;
- `auxiliary_materials` for auxiliary evidence;
- `input_strategy=llm_compressed`;
- the existing generation guidance and hard limits.

It does not emit a standalone `compressed_evidence` structure that the current
Dify `Parse Evidence Pack` node cannot accept.

## Failure behavior

If any required chunk is empty or an LLM request fails, the whole long-evidence
compression attempt is treated as unavailable. The existing deterministic
rule-compression fallback is used. Partial LLM summaries are not mixed with
rule-compressed evidence.

No retry, rate-limit policy, Dify workflow change, or unrelated refactor is
added in this change.

## Verification

The project is not run or tested locally, per the user's deployment constraint.
After deployment to `.87`, one browser-driven run reuses the selected Anhui
long material.

Acceptance requires:

1. the stored generation payload uses `llm_compressed`;
2. it contains at least one `primary_materials` entry and no standalone
   `compressed_evidence` field;
3. Dify's Evidence Pack gate selects the success branch;
4. the Generate Report node executes with nonzero model tokens;
5. the run no longer fails with `evidence_pack has no primary_materials` or
   provider `OUTPUT_EMPTY` caused by that contract error;
6. the elapsed evidence-build time and chunk count/concurrency are recorded;
7. `.87` is healthy after deployment and `.88` remains untouched.
