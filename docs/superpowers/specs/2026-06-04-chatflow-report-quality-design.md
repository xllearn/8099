# Chatflow Report Quality Design

## Background

The current `medical-notice-analyzer` project is a local helper service used by Dify. It can fetch medical procurement notices, parse attachments, generate `ReportIR`, render Markdown, run QA, repair once, run second QA, and export Word. The live Dify workflow currently has 20 nodes, while the repository also contains older workflow YAML and a Chatflow blueprint.

The current weakness is report depth. Generated reports can be around 500 characters for procurement-file notices, while the two human reference reports in `C:\Users\admin\Documents\htmldataconclusion\sample-report-assets` are much richer:

- National coronary stent reference report: about 2,673 Chinese characters, 52 effective paragraphs, 5 tables, and 47 table rows.
- Shandong consumables continuation procurement reference report: about 1,456 Chinese characters, 33 effective paragraphs, 3 tables, and 17 table rows.

The target product is not a single-run report generator. It is a stateful report workflow: users enter a required notice URL, may upload an optional Word report from a related project, review the generated report, give feedback across turns, and only download Word after explicit approval.

## Goals

1. Require a notice URL as the primary input.
2. Allow an optional Word file as a related-project reference.
3. Fetch webpage text and attachments from the notice URL and use them as the only source of current-notice facts.
4. Generate a professional analysis report with structure and depth closer to human-written reports.
5. Use the optional Word file only for project-continuity framing and style reference.
6. Insert any related-project connection naturally into an existing paragraph or half paragraph, without adding a separate history title, section, or table.
7. Run QA after generation to check source grounding, table consistency, history leakage, and human-report style fit.
8. Send QA failures back to the generation or repair path through explicit `fix_instructions`.
9. Show the report only after QA passes or after the user needs to review unresolved issues.
10. Support user feedback and revise the current report within the same conversation.
11. Export Word only after the user explicitly approves or asks to download.

## Non-Goals

- Do not continue treating the current 20-node Workflow as the final interaction model.
- Do not use the optional Word file as a factual source for the current notice.
- Do not create a separate custom web application outside Dify.
- Do not generate Word immediately after first QA passes.
- Do not hard-code facts from any single notice or reference report.

## Recommended Architecture

The final interaction should move to a Dify `advanced-chat` Chatflow. The current 20-node Workflow remains a compatibility or temporary single-run flow. Chatflow is required because the target workflow needs conversation variables, optional file input, user feedback, and a delayed export decision.

The high-level flow is:

```text
notice_url + optional history_report
-> /analyze fetches webpage and attachments
-> optional history Word extraction
-> history_insights summarization
-> ReportIR generation
-> /report/render Markdown rendering
-> QA for source grounding, table consistency, history leakage, and style depth
-> conditional repair only when QA needs fixing
-> display report and QA summary without Word export
-> user feedback revision branch
-> explicit user approval download branch
-> /report/export_checked
```

## Chatflow Conversation Variables

- `source_evidence`: Full evidence package returned by `/analyze`. It is the only source for current-notice facts.
- `history_insights`: Optional related-project observations extracted from uploaded Word.
- `current_report_ir`: Latest normalized `ReportIR`.
- `current_final_report`: Latest Markdown report rendered from `ReportIR`.
- `last_qa_summary`: Latest QA summary shown to the user.
- `last_export_filename`: Word filename after approved export.
- `revision_count`: Number of report revision turns.
- `download_approved`: Boolean-like state set only when the user explicitly approves export.

## Chatflow Branches

### First Generate

The first-generate branch runs when the conversation has no `source_evidence` or when the user provides a new `notice_url`.

Steps:

1. Validate that `notice_url` is present.
2. Call `/analyze` with `max_attachments` set to 25 and `max_combined_chars` around 180000.
3. Save `evidence_for_llm` into `source_evidence`.
4. If `history_report` exists, extract Word text and summarize it with `prompts/report_history_prompt.md`.
5. Generate `ReportIR` using the source evidence and optional `history_insights`.
6. Render the generated `ReportIR` through `/report/render`.
7. Run QA.
8. If QA returns `needs_fix`, run repair once, render again, and run second QA.
9. Show the report and QA summary. Do not export Word in this branch.

### Revise

The revise branch runs when the conversation already has `current_report_ir` and the user gives feedback that is not an export approval.

Steps:

1. Use `source_evidence`, `history_insights`, `current_report_ir`, `current_final_report`, and the user message.
2. Revise only the relevant parts of `ReportIR`.
3. Render through `/report/render`.
4. Run QA.
5. Repair only when QA returns `needs_fix`.
6. Show the updated report and QA summary. Do not export Word in this branch.

### Approve Download

The approve-download branch runs only when the user explicitly says they agree, approve, confirm, export, or download Word.

Steps:

1. Confirm `current_report_ir` and `current_final_report` exist.
2. Call `/report/export_checked` with the latest QA output, report text, history text, and source evidence.
3. If blocked, show QA summary and do not return a download link.
4. If passed, return the Word download link and filename.

## Report Generation Requirements

The generation prompt should keep the current `ReportIR` format but become stricter about report depth. It should instruct the model to write a publishable industry analysis report, not a summary.

For procurement-file, centralized procurement, continuation procurement, and price-linkage notices:

- Use 2 to 3 natural lead paragraphs.
- Include publication time, issuing body, file name, procurement scope, and core enterprise actions when available.
- Preserve source-rule branches as sections or tables when present.
- Use topic-specific tables instead of one large mixed table.
- Preserve key definitions such as non-selected products, reference price, quotation unit, and price-linkage terms when the source contains them.
- Include short enterprise attention points, but do not let them replace the main analysis.
- Avoid unsupported trend claims or broad policy background unless present in the source evidence or the optional history insights.

The optional `history_insights` may only produce one short connection in an existing paragraph. It must not create a heading, independent section, table, or current-notice facts.

## QA Design

QA has two layers: local deterministic checks and LLM-based review.

### Local QA

Add or extend helper logic in `app/main.py` to inspect `ReportIR` against `source_evidence` and `history_text`.

Local QA should detect:

- Procurement-file reports that are too short when evidence is rich.
- Too few meaningful sections for rich procurement documents.
- Empty table headers and row-column mismatches.
- Table key cells containing dates, prices, percentages, file numbers, or product names that do not appear in source evidence and are not marked as calculated or undisclosed.
- History headings such as `历史对照`, `历史分析`, or `历史知识`.
- Historical prices, cycles, dates, purchase volumes, product ranges, enterprise ranges, or region ranges copied into the current report when absent from current evidence.
- Model-like, exaggerated, or unsupported language already covered by existing cleanup and QA rules.

The local QA output should merge into the same `ReportQA` structure used by `/report/qa`, so Dify receives a single QA summary and blocking state.

### LLM QA

The QA prompt should require the model to inspect:

- Whether each important statement is grounded in webpage or attachment evidence.
- Whether report tables preserve the original source table meaning after splitting.
- Whether each table's title, headers, key values, units, dates, and price terminology match the source evidence.
- Whether the report resembles the reference reports structurally: natural lead, source-rule sections, topic tables, concise enterprise attention points.
- Whether optional history content is used only as a limited connection and not as a current-notice fact.

QA status semantics:

- `pass`: report can be shown to the user.
- `needs_fix`: repair node should receive concrete `fix_instructions`.
- `block`: report should not be exported and may require user or operator review.

The repair node must be conditional. If QA is `pass`, the report must not be rewritten. This prevents a valid long report from being compressed by unnecessary repair.

## Backend Scope

The existing backend remains the source of truth for parsing, rendering, QA parsing, and Word export.

Keep:

- `POST /analyze`
- `POST /report/render`
- `POST /report/qa`
- `POST /report/export_checked`
- `GET /download/{filename}`

Enhance:

- `/report/qa` should merge LLM QA JSON with local evidence-based QA.
- `/report/export_checked` should remain export-only and should block when QA is not safe.
- `ReportQA` should continue to expose `issues`, `unsupported_claims`, `history_leakage`, `missing_rules`, `language_issues`, `fix_instructions`, and `summary`.

Likely helper functions:

- `_quality_check_report_against_evidence(report, evidence_text, history_text) -> ReportQA`
- `_extract_evidence_markers(text) -> set[str]`
- `_report_quality_metrics(report) -> dict[str, int]`
- `_looks_like_rich_procurement_evidence(evidence_text) -> bool`
- `_table_cells_missing_from_evidence(report, evidence_text) -> list[ReportQAIssue]`

These helpers should stay deterministic and conservative. They should not try to prove every semantic claim; they should catch high-signal issues that are cheap to verify.

## Prompt Scope

Update:

- `prompts/report_system_prompt.md`
- `prompts/report_user_prompt.md`
- `prompts/report_qa_prompt.md`
- `prompts/report_qa_fix_prompt.md`
- `prompts/report_revision_prompt.md`
- `prompts/report_history_prompt.md`

Prompt changes should:

- Use the two human reference reports as structure and style references, not as fact sources.
- Explicitly reject short summary-style reports when the source evidence is rich.
- Require procurement-file rule branches to be preserved.
- Require table-splitting to preserve source meaning and units.
- Require optional history to be one integrated connection only.
- Require revisions to preserve previously valid sections unless the user asks to change them.

## Dify Blueprint Scope

Update `dify-chatflow-medical-notice-report.yml` so it becomes the authoritative blueprint for the new main flow.

The blueprint must include:

- Required `notice_url` input.
- Optional `history_report` file input.
- Conversation variables listed in this design.
- `classify_turn` for `first_generate`, `revise`, and `approve_download`.
- `fetch_source`, `extract_history_doc`, `summarize_history`, `generate_report`, `render_report`, `qa_report`, `parse_qa`, conditional repair, answer, and export nodes.
- Export only in the approve-download branch.
- No immediate Word export after first generation or feedback revision.

The current 20-node Workflow graph can remain as a compatibility asset. If live Dify is later updated, the live graph must be backed up before writing changes.

## Test Plan

Add tests in `tests/test_report_export.py` or a focused adjacent test file.

Required tests:

1. Short procurement report is flagged when source evidence is rich.
2. Short simple notice is not blocked solely by length when source evidence is limited.
3. Table value absent from source evidence creates an `unsupported_claims` issue.
4. Table value present in source evidence passes local evidence checks.
5. Legal integrated history connection passes.
6. History heading or copied historical price/cycle is flagged.
7. QA parser merges local QA issues with model QA output.
8. Chatflow blueprint includes `first_generate`, `revise`, and `approve_download`.
9. Chatflow blueprint exports Word only in the approve-download branch.
10. Prompt files mention human-report structure reference and forbid using reference reports as facts.
11. Repair path is conditional and not run when QA passes.

Existing tests for Word export, cleanup, `enterprise_tips` coercion, history leakage, and workflow render behavior must continue to pass.

## Acceptance Criteria

The work is acceptable when:

- The Chatflow blueprint supports required URL input and optional Word upload.
- The optional Word file is constrained to integrated related-project context only.
- Generation prompts push procurement-file reports toward human-report depth and structure.
- QA can flag short summary-style output for rich procurement evidence.
- QA can flag table values that are absent from source evidence.
- QA can flag historical facts copied into current-notice facts.
- QA pass does not trigger repair rewriting.
- Word export is available only after explicit user approval.
- Unit tests cover the new behavior.
- Container tests pass with `python -m unittest discover -s tests -v`.
- Health check passes after rebuild.

## Operational Notes

The project directory is not a Git repository, so design and implementation changes cannot be committed locally with `git commit` unless the project is later initialized as a repository or copied into one.

When writing changes back to live Dify:

1. Regenerate the relevant graph or Chatflow blueprint.
2. Back up live workflow or app configuration first.
3. Apply the new configuration.
4. Restart Dify services if needed.
5. Verify Dify UI, backend health, and tests.

