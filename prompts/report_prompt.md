# Report Generation Prompt

This workflow uses two prompt files:

- `report_system_prompt.md`: system role instructions for article-style medical procurement analysis and ReportIR output.
- `report_user_prompt.md`: user role template with Dify variables.
- `report_history_prompt.md`: optional history Word insight extraction. History can only be used as constrained background and must not become current announcement facts.
- `report_revision_prompt.md`: multi-turn report revision prompt based on source evidence, current ReportIR, current Markdown, and user feedback.
- `report_qa_prompt.md`: QA prompt that outputs fixed JSON for source-grounding, history-leakage, missing-rule, and language checks.
- `report_qa_fix_prompt.md`: one-pass repair prompt that only follows QA `fix_instructions`.

The LLM must output only ReportIR:

```text
<report_ir>
{ ... ReportIR JSON ... }
</report_ir>
```

The render/export layer parses `report_ir`, normalizes it, and renders Markdown for Dify display. If JSON parsing fails, the workflow returns a clear error instead of trying to write a partial model reply into Word. Raw model replies are rejected.

Required ReportIR fields now include `suggested_filename` and `notice_type`. The Word export service uses `suggested_filename`, then `title`, then `document_name`, then the Markdown H1 fallback to build the download filename.

The visible report must not render `notice_type` as a mechanical metadata line. The Word exporter appends a fixed `声  明` and ignores any LLM-generated disclaimer.

For procurement-file reports, preserve source rule branches such as quote rules, proposed selection rules, agreement volume, execution requirements, price linkage, non-selected product management, and term definitions when present in the source material.

Historical analysis documents are optional. They are only for history comparison, style reference, project-continuity observation, and short enterprise-attention supplements. Do not add a history heading, section, or table. Do not copy the history Word's rules, prices, cycle, company scope, product scope, region scope, purchase volume, or dates into the current report as facts.

QA summaries and repair notes are displayed in Dify only and must never be written into the Word document.
