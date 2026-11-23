# PRD: Landing page, Carbon theme and app screen

**Status:** Approved for breakdown · **Date:** 2026-11-24 · **Version:** 3.2
**Surface:** `resibo/static/`, plus one additive summary field (`resibo/models.py`), the Excel Summary sheet (`resibo/exporters.py`) and the n8n summary email.
**Design reference:** theme A · Carbon in the private preview artifact https://claude.ai/artifact/881fC9kubjQv7KnwyzDFJZ (version 2). It opens only for people it has been shared with, and build agents cannot open it. Where this PRD and the artifact differ, **this PRD wins**: the tokens, the rack layout and the contrast fix below are the build spec.

## Decisions

| # | Decision |
|---|---|
| D1 | Theme **Carbon**, one theme in light and dark. Themes B (Tape) and C (Columnar) are dropped. |
| D2 | Landing page at `/`, tool at `/app`. |
| D3 | Headline: "Stop encoding invoices by hand." |
| D4 | Cost and tokens stay in the results totals band. |
| D5 | Summaries (UI, Excel Summary sheet, n8n email) leave out ERROR invoices. The ERROR rows stay in every export, so users can check and delete them themselves. |
| D6 | The total is a contract field, so a new field is added and the old one keeps its meaning. |
| D7 | In light mode the Carbon status colours equal the Excel fills exactly, so the parity test holds. |

## Summary

Today Resibo opens on a bare upload box, shows a total that counts rejected invoices, and hides why rows failed. This release does three things:

1. Adds a landing page that shows the real sample batch working.
2. Gives the product an identity taken from Philippine BIR invoice pads.
3. Makes the results screen honest and its flags visible.

Extraction and validation do not change.

## Problem

| # | Issue | Evidence |
|---|---|---|
| P1 | The total is overstated by ₱20,460. It includes a duplicate of SI-004217 and an invoice whose total doesn't add up. | `summarize()` in `resibo/models.py`; the recorded sample |
| P2 | The counts contradict each other: the "Needs review" card says 2 and the "Review" chip says 0. | `resibo/static/app.js` (`renderSummary` vs `renderChips`) |
| P3 | Flag reasons appear only on hover, and on mobile never. | `flagCell()` in `app.js`; `.tooltip` hidden under 640px in `styles.css` |
| P4 | Rows expand on click, but nothing shows that they do. | `app.js` row click handler |
| P5 | The first thing a visitor sees is a greyed-out Extract button, and a "Run live" toggle changes what a different button does. | `resibo/static/index.html` |
| P6 | Nothing explains what Resibo checks, how accurate it is, or that an API and an n8n automation exist. | `index.html`; SPEC.md §9, §13 |
| P7 | The page has no identity: system font, one teal and flat cards. Two design reviews judged it templated. | `styles.css`; owner feedback |

**Evidence gap:** this rests on code review and owner judgement. There are no user sessions or analytics yet.

## Users

- **Hiring reviewer (primary):** spends under 2 minutes deciding whether the AI output can be trusted.
- **PH bookkeeper (secondary):** has a folder of invoices to encode and needs to know which ones to fix and why.

## Goals

1. Within 30 s on `/`, a visitor can say what Resibo does and name at least one thing it checks.
2. The landing page shows a real result with no click, and the full app is one click away.
3. Every number on screen is consistent across the UI, Excel and email, and traces to a source.
4. Carbon is applied consistently on both pages, in light and dark.
5. Accessibility is at least as good as today and meets WCAG AA in both modes.

## Non-goals

- API, export and validation changes, **except** the additive summary field and the Excel Summary rows in B1. The Excel header colour stays teal.
- Editing extracted data in the UI.
- More than one theme, or a theme picker. Light/dark is the only switch.
- Invented marketing: no testimonials, logos or user counts. Accuracy claims cite the sample eval only.
- A framework, a build step, a CMS, analytics or third-party scripts. The CSP stays as it is.
- Accounts, pricing, a waitlist, Filipino-language copy. Accounts, the free trial and paid runs are covered in [prd-trial-and-payments.md](prd-trial-and-payments.md), which builds on this PRD after wave 3.
- Re-recording `samples/sample-result.json`.

## Information architecture

| Route | After this release |
|---|---|
| `/` | Landing page (new `static/landing.html`) |
| `/app` | The tool (today's `index.html` with the fixes) |
| `/app#sample` | The tool with the cached sample loaded |
| `/api/*`, `/docs`, `/health`, `/static/*` | Unchanged |

`/app` is an additive route. SPEC.md §11 gets one new line.

## Contract change (additive)

- **New:** `summary.total_excluding_errors_by_currency: dict[str, Money]`, the sum of `total_amount` over OK and REVIEW invoices, per currency. It uses the same type as `total_amount_by_currency`, so it serialises as JSON numbers. A currency whose invoices are all ERROR is absent from it.
- **Unchanged:** `summary.total_amount_by_currency`, which keeps summing every invoice. The UI (`app.js`), the Excel Summary sheet (`exporters.py`) and the n8n "Code: build summary" node read it today. Changing its meaning would silently change the email for anyone running an older copy of the workflow.

The UI, the Excel Summary sheet and the n8n email all move to the new field.

## Carbon design system

**The idea:** a BIR invoice pad. A white original, a canary duplicate and a pink triplicate, written in carbon-blue ink, with serial numbers printed in red and a rubber stamp for rejects. In dark mode the page becomes the carbon sheet itself.

**Tokens** live in a new shared `static/theme.css`. `static/styles.css` stays as the app's layout stylesheet and holds no colour values. A new `static/landing.css` does the same for the landing page. `tests/test_static.py` keeps reading `styles.css` at import, so that file must not be removed.

| Token | Light | Dark | Use |
|---|---|---|---|
| `--paper` | `#FDFDFB` | `#111331` | Page background |
| `--band` | `#F6EBAE` canary | `#1A1D47` | Sample rack, detail rows, code |
| `--ink` | `#1F2A7A` | `#DCDFF6` | Text, strong rules |
| `--ink-2` | `#4E5687` | `#9EA3D1` | Labels, secondary text |
| `--rule` | `#CED2E6` | `#2C3066` | Form lines |
| `--red` | `#C41C2A` | `#FF6B76` | Serial numbers, stamps, flag notes |
| `--btn` / `--btn-fg` | `#1F2A7A` / `#FDFDFB` | `#F2E27A` / `#111331` | Primary button |
| `--status-ok-bg` / `-fg` | `#C6EFCE` / `#006100` | `rgba(127,214,154,.12)` / `#7FD69A` | OK badge |
| `--status-review-bg` / `-fg` | `#FFEB9C` / `#9C5700` | `rgba(242,194,90,.12)` / `#F2C25A` | Review badge |
| `--status-error-bg` / `-fg` | `#FFC7CE` / `#9C0006` | `rgba(255,107,118,.15)` / `#FF7D87` | Error badge |
| `--row-tint-review` | `#FFF8E1` | `rgba(242,194,90,.06)` | Review row |
| `--row-tint-error` | `#FDECEE` (the pink triplicate) | `#2A1736` | Rejected row |

The light status values equal `STATUS_FILLS` and `ROW_TINTS` in `resibo/exporters.py`. `test_status_colours_equal_the_workbook_fills` keeps enforcing that (see "Tests that change").

**Contrast.** Light `--red` is `#C41C2A`, not the `#CF1F2E` in the design reference: `#CF1F2E` on `--band` is 4.48:1, under AA, and flag messages sit on the band. `--rule` is about 1.5:1 in both modes, so it is for decoration only. Any edge that is the only boundary of a control (drop zone, tab underline, form box, focus ring) uses `--ink-2` or stronger, which is at least 3:1.

**Type.** Both faces are SIL OFL and self-hosted in `static/fonts/`, because the CSP blocks Google Fonts.

- **Archivo** (variable, width and weight axes):
  - condensed, 66% width, weight 850, in capitals for the wordmark, section titles and stamps
  - normal width for body text
  - 85% width, letterspaced capitals for form labels
- **Courier Prime** for every value: amounts, invoice numbers, TINs, dates and flag notes. Digits use tabular figures.
- **The ₱ sign (U+20B1).** Courier Prime may not have this glyph. Flag messages arrive from the server with ₱ already in them (for example "Computed ₱8,960.00 but document says ₱9,260.00."), so writing "PHP" in the money formatter is not enough. The monospace stack must draw ₱ from a self-hosted font. If Courier Prime lacks it, add a `@font-face` rule for Archivo restricted to `unicode-range: U+20B1` and list it before Courier Prime in the mono stack.

**Rules of use**

- Corners are square, with no soft shadows. The one exception is the hard offset under the redrawn document.
- Condensed capitals appear only in the wordmark, section titles and stamps.
- Red marks only serial numbers and problems. It is never decoration.
- Status always pairs colour with its word. The Error badge is tilted 3° like a stamp.

## Landing page (`/`), top to bottom

1. **Masthead.** Wordmark "RESIBO", the label "Invoice & receipt reader · VAT 12% · ₱", the nav (How it reads · What it checks · API · GitHub) and a red serial "No. 0000001", over a 2px ink rule.
2. **Lede.** The headline, a one-sentence description, and two buttons: **Open the sample batch** (scrolls to the rack) and **Upload a PDF** (goes to `/app`). Beside them sits a form box with four rows: Sold to · Accepts · Gives back · Terms.
3. **Sample rack** (on the canary band):
   - The 8 sample documents appear as slips. Each shows its page, invoice number, seller, and OK or REJECTED.
   - Selecting a slip shows the document redrawn from its fields next to "What Resibo read" (10 fields).
   - A flagged slip shows the problem field marked, the flag message in red, and a stamp.
   - INV-006 is selected by default.
   - Data comes from `GET /api/sample/result` (cached, no model cost). Nothing is hard-coded.
   - Layout (the design reference is private, so this is the build spec):

     ```
     ┌ slips (tablist, one row, scrolls sideways under 768px) ──────────────────────┐
     │ [p.1 SI-004217 … OK] [pp.2–3 …] … [p.6 SI-009120 … REJECTED] [p.7 …] [p.8 …]  │
     └───────────────────────────────────────────────────────────────────────────────┘
     ┌ redrawn document (paper, hard offset) ─────┐  ┌ What Resibo read ───────────┐
     │ SELLER NAME              No. <invoice_no>  │  │ Seller           <value>    │
     │ TIN <seller_tin>                Date <date>│  │ Seller TIN       <value>    │
     │ <seller_address>                           │  │ Buyer            <value>    │
     │ SOLD TO <buyer_name>                       │  │ Invoice no.      <value>    │
     │ ─ line items: description · qty · amount ─ │  │ Date             <value>    │
     │ Vatable sales                    <amount>  │  │ Document type    <value>    │
     │ VAT 12%                          <amount>  │  │ Vatable sales    <value>    │
     │ TOTAL AMOUNT DUE                 <amount>  │  │ VAT              <value>    │
     │                        [REJECTED stamp, 3°]│  │ Total            <value>    │
     └────────────────────────────────────────────┘  │ Status           <badge>    │
                                                     └─────────────────────────────┘
     ```

     A slip's page label comes from the invoice's `source_pages`, formatted by the existing `formatPages` helper in `app.js` (moved to a shared file if the landing page needs it), never from the slip's position. In the sample, INV-002 covers pages 2–3, and INV-004 and INV-005 share page 5.

     The 10 fields are `seller_name`, `seller_tin`, `buyer_name`, `invoice_number`, `invoice_date`, `document_type`, `vatable_sales`, `vat_amount`, `total_amount` and `status`. On a flagged slip, the `flag.field` row gets the wavy red underline on both sides, and the flag message shows in red under the field list. An absent value shows "—". The two columns stack under 768px.
4. **What it checks.** A ruled list of 8 of the 15 rules: the flag code in the margin, a plain sentence, and Error or Review. Wording and severity come from SPEC.md §9. The 8 are `TOTAL_MISMATCH`, `VAT_MISMATCH`, `LINE_ITEMS_MISMATCH`, `DUPLICATE`, `TIN_FORMAT`, `MISSING_REQUIRED`, `FUTURE_DATE` and `LOW_CONFIDENCE`. The heading reads "8 of the 15 checks", with a link to SPEC.md §9 on GitHub. `MISSING_REQUIRED` is "error / warning" in §9 (which one depends on the field), so its row shows "Error or Review" and says which fields make it an Error.
5. **How it reads, and the tally.**
   - Left: the flow sentence, the Drive → Sheets automation and a curl example.
   - Right: a receipt-style tally with dotted leaders, in two labelled groups, so that no figure mixes runs:
     - **Eval, 8 sample documents** (README Accuracy table): fields correct 114/114, cost ~$0.08, time 22.5 s.
     - **Recorded sample run** (read live from `/api/sample/result`): documents 8, flagged 2, run time from `duration_ms`.
     - A last line: "15 checks", from SPEC.md §9.
   - The caption says these figures come from the 8-document synthetic sample set.
6. **Footer.** "This demo is not valid for claim of input tax" · GitHub · API docs · Built by Jan Terence Francisco.

## App screen (`/app`)

- **Masthead:** the shared masthead; the wordmark links back to `/`. The app keeps today's tagline, "Turn a PDF of invoices into a clean, validated spreadsheet." The D3 headline appears only on the landing page.
- **Upload panel:**
  - With no file chosen, **Try with sample invoices** is primary and Extract is hidden.
  - With a file chosen, **Extract** is primary.
  - The privacy and limits note sits under the drop zone.
- **Recorded-run line:** "Recorded run · <date>" with a **Run it live now** button. The date is the batch's `recorded_at`, shown in Asia/Manila time, never fixed text. This line replaces the Run live toggle.
- **Totals band** (one ruled row):
  - Documents.
  - Total, from `total_excluding_errors_by_currency`, with "leaves out N errors, ₱X" when there are errors. Each currency gets its own line (see A1).
  - Status counts OK / Review / Error, which double as filters.
  - Cost (~$) with tokens below.
  - Time with the model below.
- **Filter tabs:** All / OK / Review / Error with counts. The download buttons sit on the same line.
- **Table:**
  - Each row has a chevron and a stamped status badge, with the flag message in red under the seller.
  - The flagged field has a wavy red underline. Rejected rows sit on the pink tint.
  - Money and dates are set in Courier Prime.
  - An expanded row shows each flag, then the line items, on the canary band. Each flag shows the plain sentence for its code (the same code → sentence map as the L4 list, kept in the UI only, since `Flag` has no title field: `resibo/models.py:124`), then `flag.message`, then the code. No field is added to the API.

## User stories

### Epic B: Honest totals across UI, Excel and email

**B1. Summaries leave out rejected invoices; rows stay (M)**

As a bookkeeper, I want totals that leave out rejected invoices while the rows stay in my file, so I can check and delete them myself.

- Given ERROR invoices, then `summary.total_excluding_errors_by_currency` sums OK and REVIEW invoices only, and `total_amount_by_currency` is unchanged. For the sample: `{"PHP": 155640.00}` and `{"PHP": 176100.00}`.
- Given no ERROR rows, then the two fields are equal.
- Given every invoice is ERROR, then the new field is `{}`.
- The Excel Summary sheet shows:
  - "Total, excluding errors (PHP)"
  - "Total, all documents (PHP)"
  - the note "N error rows are kept on the Invoices sheet so you can check or delete them."

  With no ERROR rows, it shows a single "Total (PHP)" row, as today.
- With more than one currency, there is one pair of rows per currency, ordered as in `total_amount_by_currency`. A currency that has only ERROR invoices shows 0.00 on its "excluding errors" row, so every currency in the all-documents total has both rows. With every invoice in ERROR, every "excluding errors" row shows 0.00.
- The Invoices and Line Items sheets and both CSVs still contain every invoice, ERROR rows included, with Status and Flags filled in.
- The n8n email reads "Total: PHP 155,640.00 (excludes 2 errors)". Amounts use thousands separators and two decimals (`toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})`), replacing today's `toFixed(2)`. The email loops over the keys of `total_amount_by_currency` and uses 0.00 for any currency missing from the new field, so it shows the same currencies as Excel. With more than one currency, the amounts are joined with ", " and the count of errors appears once at the end. With no errors, the "(excludes …)" part is left out. With every invoice in ERROR it reads "Total: PHP 0.00 (excludes N errors)", never "n/a". Update the "Code: build summary" node in `n8n/resibo-drive-to-sheets.json` and the field list in `n8n/README.md`.
- Update the Batch JSON example in SPEC.md §7.
- Tests: the new field in `tests/test_models.py`, plus exporter tests for the Summary rows and for kept ERROR rows.

### Epic T: Carbon foundation

**T1. Theme tokens and shared stylesheet (M)**
- Every colour on both pages comes from `theme.css` tokens. No hex values appear outside the token blocks.
- The dark palette applies in dark mode.
- Every text/background pair meets AA: 4.5:1 for body text, 3:1 for large text and UI.
- `test_status_colours_equal_the_workbook_fills` passes against `theme.css` (see "Tests that change").
- Light `--red` on `--band` and on `--row-tint-error` is at least 4.5:1, because flag messages sit on both.

**T2. Self-hosted fonts (S)**
- Archivo and Courier Prime load from `/static/fonts/` with `font-display: swap`.
- The fonts total at most 200 KB after Latin subsetting (estimate), and the subset includes ₱ (U+20B1).
- Every ₱ on screen, in amounts and in server-written flag messages, is drawn from a self-hosted font. It never falls back to a system font in the middle of a value. Check with `fontTools` on the shipped files that the mono stack covers U+20B1, and record the result in the PR.
- `pyproject.toml` package-data adds `static/fonts/*`, because `static/*` does not include subfolders.
- If fonts are blocked, the page stays readable with system fallbacks.
- `test_nothing_is_loaded_from_another_origin` still passes.

**T3. Light/dark toggle (S)**
- With no saved choice, the page follows the OS.
- A choice persists across reloads and across `/` and `/app`.
- There is no flash of the wrong theme. An external `theme.js` runs in `<head>`, because the CSP forbids inline scripts.
- The page works when `localStorage` throws.

### Epic A: App screen

**A1. Honest total in the UI (S)** (depends on B1)
- The total reads `total_excluding_errors_by_currency`.
- The note "leaves out N errors, ₱X" uses the difference between the two fields.
- With no ERROR rows there is no note. With all rows in ERROR the total shows "—" with the note.
- With more than one currency, each currency has its own total and its own note, for example "leaves out 1 error, $120.00". The amount left out is the per-currency difference `total_amount_by_currency[c] − total_excluding_errors_by_currency.get(c, 0)`. N counts the ERROR invoices in that currency. An ERROR invoice with no `total_amount` still counts in N but adds nothing to the amount.

**A2. Consistent counts (S)**
- The status counts equal the tab counts, and clicking a count filters the table.
- The screen-reader announcer uses the same numbers.

**A3. Flag reasons visible (S)**
- The first flag message shows under the seller at every width, without hover.
- A row with more flags shows "+N more".
- The cell for `flag.field` is marked when the field has a column: `total_amount`, `invoice_number`, `invoice_date`, `seller_name` or `vat_amount`.

**A4. Expandable rows look expandable (S)**
- A chevron rotates when a row opens.
- The open row shows flags before line items.
- `aria-expanded` is kept.

**A5. Clear first action (M)**
- When idle, **Try with sample invoices** is primary. With a file chosen, **Extract** is primary.
- The Run live toggle is removed, and **Run it live now** in the recorded-run line does its job.
- `/app#sample` loads the cached sample.
- Update `tests/test_static.py` as described in "Tests that change".

**A6. Carbon applied to the app (M)**
- The app has the shared masthead, the totals band, stamped badges and the pink rejected rows.
- At 375px the page body does not scroll sideways. The table scrolls in its own container.

### Epic L: Landing page

**L1. Routes (S)**
- `/` serves the landing page and `/app` the tool, both under the existing UI CSP. Today the middleware applies the CSP only when `path == "/" or path.startswith("/static/")` (`resibo/api.py:77`), so `/app` must be added there. Without that change `/app` is served with no CSP at all.
- `/app` answers both GET and HEAD, like `/`. It returns 404 "The web page is not installed." when `index.html` is missing, as `/` does today.
- `/api/*`, `/docs` and `/health` are unchanged.
- Tests cover both routes and their CSP. Extend `test_ui_routes_carry_the_strict_csp_when_present` (`tests/test_api.py`) and `test_index_and_assets_are_served_under_the_strict_csp` to cover `/app`. The no-inline-script/style and no-other-origin tests include `landing.html`.

**L2. Masthead, lede and form box (S)**
- At 1280×800 the headline, both buttons and the form box are visible without scrolling.
- **Upload a PDF** opens `/app`.

**L3. Sample rack (M)**
- When `/api/sample/result` returns, 8 slips render with INV-006 selected.
- Selecting a slip updates the redrawn document and the field list.
- A flagged slip shows its stamp, the marked field and the message.
- Slips work as tabs with arrow-key movement.
- If the sample is missing or the call fails, the rack shows "Sample not available right now" with a link to `/app`.
- Every value is inserted with `textContent`.

**L4. What it checks (S)**
- 8 rows, each mapped to a real flag code.
- Severity matches SPEC.md §9: Error for an error flag, Review for a warning.

**L5. How it reads, and the tally (S)**
- The curl example is correct for `POST /api/extract` with `X-API-Key` and has no real key.
- Each tally figure sits in the group of its source, and no group mixes runs:
  - the eval group matches the README Accuracy row for `claude-sonnet-5-5`;
  - the recorded-run group is read from `/api/sample/result` at page load;
  - "15 checks" matches the count of flag codes in SPEC.md §9.
- The caption names the sample set.

**L6. Footer and links (S)**
- The GitHub, `/docs`, n8n guide and `docs/architecture.md` links resolve.
- The privacy and limits text (15 pages, 5 live runs/hour, 60 min) appears word for word in both `index.html` and `landing.html`, so it shows without JavaScript. One constant in `tests/test_static.py` (`LIMITS_TEXT`) holds the text, and a test asserts both files contain exactly that string. The payments PRD (W2) later changes the constant and both files together.

## Tests that change

These existing tests break by design. Each change lands in the story that breaks the test. **Rule: replace each assertion with its Carbon equivalent. Never delete one without a replacement, and never weaken one to make it pass.**

| Test | Today it asserts | After | Story |
|---|---|---|---|
| `tests/test_static.py` `REQUIRED_IDS` | `run-live`, `card-documents`, `card-total`, `card-review`, `card-cost`, `card-time`, `summary-cards`, `filter-chips` | Remove `run-live`. Replace the card ids with the totals-band ids (`totals-band`, `total-documents`, `total-amount`, `total-errors-note`, `count-ok`, `count-review`, `count-error`, `total-cost`, `total-time`). Replace `filter-chips` with `filter-tabs`. Add `run-live-btn` and `recorded-run`. | A5, A6 |
| `test_index_has_no_inline_script_or_style` | `script_srcs == ["/static/app.js"]` | `["/static/theme.js", "/static/app.js"]` in that order, with `theme.js` inside `<head>`. Same check for `landing.html`, with its own script list. | T3, L1 |
| `test_status_colours_equal_the_workbook_fills` | Reads `styles.css`; asserts `--accent: #0f766e;` | Reads `theme.css`. The STATUS_FILLS and ROW_TINTS assertions stay. Drop the `--accent` assertion, and assert that `HEADER_FILL` (`0F766E`) is still the Excel header colour in `exporters.py`, since the header stays teal. | T1 |
| `test_nothing_is_loaded_from_another_origin` | `index.html`, `styles.css`, `app.js` | Also `landing.html`, `theme.css`, `theme.js` and any landing JS. `@font-face` `src` URLs must be relative. | T2, L1 |
| `test_spec_strings_are_present` | `"Run live"`, `role="switch"`, `aria-checked`, "turn on Run live to process it now", the privacy note as a literal | `"Run it live now"`, and the cached-result message reworded to point at that button. The tagline and other labels stay. The privacy-note literal moves to the `LIMITS_TEXT` constant, asserted in both `index.html` and `landing.html` (L6). | A5, L6 |
| `tests/test_exporters.py:167` (Summary sheet) | `cells["Total (PHP)"] == approx(11200 + 55 + 1300)`; the fixture includes an ERROR invoice | `cells["Total, excluding errors (PHP)"] == approx(11200 + 55)`, `cells["Total, all documents (PHP)"] == approx(11200 + 55 + 1300)`, and the "1 error rows are kept…" note row is present. Add a no-ERROR case that still has a single `Total (PHP)` row. | B1 |
| `test_layout_is_responsive_to_360px` | `repeat(2, 1fr)` (the card grid) | The viewport meta, a `max-width` media query, and `overflow-x: auto` on the table container. The totals-band layout replaces the card grid. | A6 |
| `test_index_and_assets_are_served_under_the_strict_csp` | `/`, `/static/*` | Also `/app`, `/static/theme.css` and `/static/theme.js`; `/` now serves `landing.html`. | L1 |
| `test_javascript_never_inserts_html` | `app.js` | Also `theme.js` and any landing JS. | T3, L3 |
| `tests/test_n8n.py` `test_referenced_batch_fields_exist_in_the_batch_model` | Fields the workflow reads | Still passes once `total_excluding_errors_by_currency` exists on `Summary`. | B1 |

## Requirements

### Functional

- **FR1** The UI total uses `summary.total_excluding_errors_by_currency` (A1).
- **FR2** Status counts equal filter counts, and both filter the table (A2).
- **FR3** Flag messages are visible inline on every flagged row (A3).
- **FR4** The flagged field is marked when it has a column (A3).
- **FR5** Rows show that they expand (A4).
- **FR6** The sample and live controls work as described in A5.
- **FR7** `/` serves the landing page and `/app` serves the tool (L1).
- **FR8** The landing rack renders from `/api/sample/result` (L3).
- **FR9** Carbon tokens, fonts and the toggle are shared across both pages (T1–T3).
- **FR10** Every claim on the landing page traces to a source in the repo (L5).
- **FR11** Extracted values are inserted only with `textContent` (SPEC.md §12).
- **FR12** The Excel Summary sheet and the n8n email show the total excluding errors and say what was left out. ERROR rows stay in all exports (B1).

### Non-functional

- **Performance:**
  - The landing page is at most 350 KB including fonts (estimate), with no third-party requests.
  - Lighthouse Performance is at least 90 on mobile.
  - The rack's single sample request stays inside the existing 30/min/IP sample limiter in `resibo/api.py`.
- **Accessibility:**
  - AA contrast in both modes.
  - Visible focus.
  - Slips follow the tab pattern.
  - Status is never shown by colour alone.
  - Reduced motion turns off the slip lift and the chevron animation.
- **Security:** the CSP is unchanged (`default-src 'self'`, no `unsafe-inline`). No keys appear in examples.
- **Responsive:** checked at 375, 768 and 1280px.

## Success metrics

| Metric | Baseline | Target | How measured |
|---|---|---|---|
| Visitor names what Resibo checks after 30 s on `/` | Not measured | 4 of 5 testers name at least 2 checks | Moderated test, 5 people |
| Time to say why each rejected row failed | Not measured (run on the current UI first) | ≤ 15 s | Same test |
| Contradictory numbers on the sample (UI, Excel, email) | 2 in the UI | 0 | Manual check against `samples/expected.json` |
| Clicks from landing to a real result | 1 | 0 (the rack shows it) | Observation |
| Lighthouse accessibility / performance, mobile, both pages | Not measured | ≥ 95 / ≥ 90 | Lighthouse |
| AA contrast failures, light and dark | Not measured | 0 | axe |

## Rollout

- **Deploy:** static assets, one new route and one additive field, in a single Railway deploy. Rollback is a redeploy of the previous commit.
- **Waves:**
  1. **Foundation, routes and honest numbers:** L1, B1, T1, T2, T3, A1, A2.
     - L1 comes first. It adds `/app` (with the CSP middleware change) and serves a **placeholder** `landing.html` at `/`: the masthead, the headline and an "Open the app" link, in Carbon, with no rack.
     - Then T1–T3 can check "both pages", theme persistence across `/` and `/app`, and the no-other-origin tests from the start.
  2. **App screen in Carbon:** A3, A4, A5, A6.
  3. **Landing page:** L2–L6, replacing the placeholder.
- **Owner checks, not build gates:** Lighthouse scores, axe runs and the 5-person moderated test are run by the owner after wave 3. Build agents cannot run them, so they are not acceptance criteria for any story.
- **Doc updates in the same PRs:**
  - SPEC.md §7 (new field), §11 (route `/app`), §12 (landing, `/app`, Carbon, toggle removed).
  - The README screenshot.
  - Remove the stale "TODO (human step 7)" line in the README Accuracy section.
- **Demo video:** re-record after wave 3.
- **Release notes:** n8n users must re-import the workflow JSON to get the new email total. The old field still works unchanged.

## Risks and dependencies

- **Claims drift.** Tally figures and limits can go stale. Define them once, and FR10 requires a source for each.
- **Accuracy overreach.** 114/114 comes from 8 synthetic documents, so the caption must say so.
- **The rack depends on the recorded sample.** A re-recorded `sample-result.json` changes what the rack shows, which is intended. If the file is missing, the L3 fallback applies.
- **Font weight.** Variable Archivo is large unless subset. Subset it to Latin plus ₱ and check the ₱ glyph.
- **Excel and UI colours are coupled.** Light mode equals the Excel fills, so changing one changes the other. The parity test catches drift.
- **Old n8n workflow copies** keep showing the all-documents total until they are re-imported.
- **Overused condensed capitals.** Enforce the rules of use in review.

## Open questions

None blocking.
