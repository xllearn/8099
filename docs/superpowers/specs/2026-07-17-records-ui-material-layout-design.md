# Records UI Material Selection Layout Design

## Goal

Optimize `/records-ui` for material selection without changing backend routes,
request payloads, data, selection limits, or report-generation behavior. At
1366x768, the result rows must show both material actions without horizontal
scrolling, while pagination and the fixed action bar remain unobstructed.

## Scope

The implementation remains in `app/static/records.html`, matching the current
FastAPI single-file static-page architecture. Related static contract tests in
`tests/test_records_api.py` will be updated.

No backend endpoint, database query, evidence processing, Dify integration,
report logic, or 8100 code is changed.

## Layout

- Replace the three-column workspace with a two-column grid of approximately
  75% results and 25% context.
- Merge "Selected materials" and "Article details" into tabs in the right
  context panel.
- Keep the selected-material tab active by default. Clicking a result title or
  row background opens the article-detail tab.
- Make the result card a fixed-height flex column: header, independently
  scrolling table, and pagination footer.
- Reserve document space for the fixed bottom action bar so it cannot cover
  pagination, the final result row, or the table scroll range.

## Result Rows

- Remove the row selector and standalone detail action.
- Limit titles to two lines.
- Use compact cell padding, metadata spacing, tags, and buttons.
- Render region as plain text; render phase and type as lightweight tags.
- Omit source, menu, or organization metadata when its value is empty or `-`.
- Keep the action column fixed on the right.
- Selected primary rows use a green accent and `✓主材`; selected auxiliary
  rows use a purple accent and `✓辅助`.
- Material action clicks retain their existing selection behavior and do not
  trigger article detail loading.

## Filters

- Keep keyword, region, and project phase in the primary filter row.
- Move project type, menu name/code, start date, and end date into a collapsible
  "More filters" area.
- Show the number of non-empty fields in the collapsible area on the "More
  filters" control.
- Preserve the existing form names and `/records` query parameters.

## Pagination And Generation

- Use the format `共240页　< 上一页　1 / 240　下一页 >`.
- Preserve the existing previous/next page requests and project-notice-first
  sort behavior.
- Keep report generation disabled with no primary material.
- Wrap the disabled button with a focusable tooltip target displaying
  `请至少选择1条主材料`.
- Restore the existing enabled behavior immediately after at least one primary
  material is selected.

## Responsive Behavior

- Use the two-column layout on desktop.
- Stack the context panel below results on narrower screens.
- Hide lower-priority table columns progressively on small screens while
  keeping title and material actions visible.
- Collapse filters to two columns and then one column as width decreases.
- Keep the bottom summary and actions usable without overlapping content.

## Interaction And Accessibility

- Tabs use `role=tab`, `aria-selected`, `aria-controls`, and keyboard-focusable
  buttons.
- Clickable result rows support Enter and Space.
- Selection buttons, remove buttons, links, and other controls do not trigger
  row-detail navigation.
- The disabled generation tooltip is available by pointer hover and keyboard
  focus.

## Verification

- Update the records UI static contract test for the two-column tab layout,
  compact row actions, pagination, more-filter count, and tooltip.
- Run the relevant records UI/API tests and the affected test module.
- Serve the actual FastAPI page with real backend data when available.
- Capture and inspect desktop 1366x768 and narrow-screen screenshots.
- Check table and page horizontal overflow, action visibility, pagination
  clearance, tab switching, row detail navigation, selection highlights,
  selection counts, and generation-button state.
