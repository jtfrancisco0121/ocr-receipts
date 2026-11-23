# PRD: Landing page, Carbon theme and app screen

**Status:** Approved for breakdown · **Date:** 2026-11-24 · **Version:** 3.1
**Surface:** `resibo/static/`, plus one additive summary field (`resibo/models.py`), the Excel Summary sheet (`resibo/exporters.py`) and the n8n summary email.
**Design reference:** theme A · Carbon in the private preview artifact https://claude.ai/artifact/881fC9kubjQv7KnwyzDFJZ (version 2). It opens only for people it has been shared with.

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
- Accounts, pricing, a waitlist, Filipino-language copy.
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

- **New:** `summary.total_excluding_errors_by_currency`, the sum of `total_amount` over OK and REVIEW invoices, per currency.
- **Unchanged:** `summary.total_amount_by_currency`, which keeps summing every invoice. The UI (`app.js`), the Excel Summary sheet (`exporters.py`) and the n8n "Code: build summary" node read it today. Changing its meaning would silently change the email for anyone running an older copy of the workflow.

The UI, the Excel Summary sheet and the n8n email all move to the new field.

## Carbon design system

**The idea:** a BIR invoice pad. A white original, a canary duplicate and a pink triplicate, written in carbon-blue ink, with serial numbers printed in red and a rubber stamp for rejects. In dark mode the page becomes the carbon sheet itself.

**Tokens** live in a new shared `static/theme.css`.

| Token | Light | Dark | Use |
|---|---|---|---|
| `--paper` | `#FDFDFB` | `#111331` | Page background |
| `--band` | `#F6EBAE` canary | `#1A1D47` | Sample rack, detail rows, code |
| `--ink` | `#1F2A7A` | `#DCDFF6` | Text, strong rules |
| `--ink-2` | `#4E5687` | `#9EA3D1` | Labels, secondary text |
| `--rule` | `#CED2E6` | `#2C3066` | Form lines |
| `--red` | `#CF1F2E` | `#FF6B76` | Serial numbers, stamps, flag notes |
| `--btn` / `--btn-fg` | `#1F2A7A` / `#FDFDFB` | `#F2E27A` / `#111331` | Primary button |
| `--status-ok-bg` / `-fg` | `#C6EFCE` / `#006100` | `rgba(127,214,154,.12)` / `#7FD69A` | OK badge |
| `--status-review-bg` / `-fg` | `#FFEB9C` / `#9C5700` | `rgba(242,194,90,.12)` / `#F2C25A` | Review badge |
| `--status-error-bg` / `-fg` | `#FFC7CE` / `#9C0006` | `rgba(255,107,118,.15)` / `#FF7D87` | Error badge |
| `--row-tint-review` | `#FFF8E1` | `rgba(242,194,90,.06)` | Review row |
| `--row-tint-error` | `#FDECEE` (the pink triplicate) | `#2A1736` | Rejected row |

The light status values equal `STATUS_FILLS` and `ROW_TINTS` in `resibo/exporters.py`. `test_status_colours_equal_the_workbook_fills` keeps enforcing that.

**Type.** Both faces are SIL OFL and self-hosted in `static/fonts/`, because the CSP blocks Google Fonts.

- **Archivo** (variable, width and weight axes):
  - condensed, 66% width, weight 850, in capitals for the wordmark, section titles and stamps
  - normal width for body text
  - 85% width, letterspaced capitals for form labels
- **Courier Prime** for every value: amounts, invoice numbers, TINs, dates and flag notes. Digits use tabular figures.

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
4. **What it checks.** A ruled list of 8 rules: the flag code in the margin, a plain sentence, and Error or Review. Wording must match SPEC.md §9.
5. **How it reads, and the tally.**
   - Left: the flow sentence, the Drive → Sheets automation and a curl example.
   - Right: a receipt-style tally with dotted leaders: fields correct 114/114, cost ~$0.08, time 25.0 s, 15 rules, 2 of 8 flagged.
   - The caption says these figures come from the 8-document sample set.
6. **Footer.** "This demo is not valid for claim of input tax" · GitHub · API docs · Built by Jan Terence Francisco.

## App screen (`/app`)

- **Masthead:** the shared masthead; the wordmark links back to `/`.
- **Upload panel:**
  - With no file chosen, **Try with sample invoices** is primary and Extract is hidden.
  - With a file chosen, **Extract** is primary.
  - The privacy and limits note sits under the drop zone.
- **Recorded-run line:** "Recorded run · 2026-11-23" with a **Run it live now** button. It replaces the Run live toggle.
- **Totals band** (one ruled row):
  - Documents.
  - Total, from `total_excluding_errors_by_currency`, with "leaves out N errors, ₱X" when there are errors.
  - Status counts OK / Review / Error, which double as filters.
  - Cost (~$) with tokens below.
  - Time with the model below.
- **Filter tabs:** All / OK / Review / Error with counts. The download buttons sit on the same line.
- **Table:**
  - Each row has a chevron and a stamped status badge, with the flag message in red under the seller.
  - The flagged field has a wavy red underline. Rejected rows sit on the pink tint.
  - Money and dates are set in Courier Prime.
  - An expanded row shows the flag (title, message, code) and then the line items, on the canary band.

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
- The Invoices and Line Items sheets and both CSVs still contain every invoice, ERROR rows included, with Status and Flags filled in.
- The n8n email reads "Total: PHP 155,640.00 (excludes 2 errors)". Update the "Code: build summary" node in `n8n/resibo-drive-to-sheets.json` and the field list in `n8n/README.md`.
- Update the Batch JSON example in SPEC.md §7.
- Tests: the new field in `tests/test_models.py`, plus exporter tests for the Summary rows and for kept ERROR rows.

### Epic T: Carbon foundation

**T1. Theme tokens and shared stylesheet (M)**
- Every colour on both pages comes from `theme.css` tokens. No hex values appear outside the token blocks.
- The dark palette applies in dark mode.
- Every text/background pair meets AA: 4.5:1 for body text, 3:1 for large text and UI.
- `test_status_colours_equal_the_workbook_fills` passes against `theme.css`.

**T2. Self-hosted fonts (S)**
- Archivo and Courier Prime load from `/static/fonts/` with `font-display: swap`.
- The fonts total at most 200 KB after Latin subsetting (estimate), and the subset includes ₱ (U+20B1).
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
- Update `REQUIRED_IDS` in `tests/test_static.py`: remove `run-live`, and replace the `card-*` ids with the totals-band ids.

**A6. Carbon applied to the app (M)**
- The app has the shared masthead, the totals band, stamped badges and the pink rejected rows.
- At 375px the page body does not scroll sideways. The table scrolls in its own container.

### Epic L: Landing page

**L1. Routes (S)**
- `/` serves the landing page and `/app` the tool, both under the existing UI CSP.
- `/api/*`, `/docs` and `/health` are unchanged.
- Tests cover both routes and their CSP. The no-inline-script/style and no-other-origin tests include `landing.html`.

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
- Each tally figure matches its source: the README eval table and the recorded run.
- The caption names the sample set.

**L6. Footer and links (S)**
- The GitHub, `/docs`, n8n guide and `docs/architecture.md` links resolve.
- The privacy and limits text (15 pages, 5 live runs/hour, 60 min) is defined once and used by both pages.

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
  1. **Foundation and honest numbers:** B1, T1, T2, T3, A1, A2.
  2. **App screen in Carbon:** A3, A4, A5, A6.
  3. **Landing page:** L1–L6.
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
