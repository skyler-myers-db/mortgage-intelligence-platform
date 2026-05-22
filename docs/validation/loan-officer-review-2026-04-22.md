> **Internal implementation artifact. Not approved for public release.**

# Loan-Officer & Light-Theme Review — 2026-04-22

Branch: `fix/copilot-batch-post-merge`
Reviewer: qa-test-engineer (combined light-theme audit + LO persona walkthrough)
Stack: backend `127.0.0.1:8000` live UC (5.16M borrowers, 147K ITM), frontend `127.0.0.1:5173`
Screenshots: `docs/validation/screenshots/light-theme-audit/*.png`

## Executive summary

**Blockers found: 5. Fixed in-flight: 5.** **Polish: 4** (not implemented, flagged below).
**LO friction points: 5** (one is a blocker also counted above).

| # | Severity | Area | Finding | Status |
|---|----------|------|---------|--------|
| 1 | Blocker | Backend | `GET /api/leads` returns 500 (Pydantic `city=None` / `zip=None` from real UC rows) | Fixed |
| 2 | Blocker | Light theme | Segment chips on Borrower 360 fail WCAG AA (1.57/1.91/2.65:1) | Fixed |
| 3 | Blocker | Light theme | Active nav tab / filter chip / density toggle text 1.78–1.91:1 | Fixed |
| 4 | Blocker | Light theme | `--signal-error` typo (5 files) → hard-coded `#EF4444`, ignores light override, fails AA at 3.5:1 on error banners | Fixed |
| 5 | Blocker | Light theme | `.kpi__delta.up` green 2.54:1 (dark-theme `#10B981` bleeds into light) | Fixed |
| 6 | LO | Workflow | Blocker #1 means every LO surface on `/lead-queue` + `/segment-intelligence` shows "Couldn't load leads" — LO cannot do her job | Fixed via #1 |
| 7 | LO | Workflow | No bulk "approve all high-confidence" on `/lead-queue` | Flagged |
| 8 | LO | Workflow | Row→Borrower 360→Offer Orchestrator→approve is 4 clicks + 3 route changes; no "approve from row" shortcut | Flagged |
| 9 | LO | Workflow | Approve in `/offer-orchestrator` requires scrolling past Considered Alternatives + Thresholds Applied before reaching the button | Polish |
| 10 | LO | Trust | Dossier rationale shows "91% equity" + bps but no last-updated timestamp on the Cotality source chip | Polish |
| 11 | Polish | Light theme | Geography map state polygons are near-invisible light blue | Flagged |
| 12 | Polish | Light theme | Primary-CTA panels (`Build outreach draft`, `Approve outreach`, `Run build`) contrast 1.5:1 vs card; text inside passes AA but button recedes visually | Flagged |
| 13 | Polish | Light theme | Tweak-row `switch` toggles (Show evidence chips / Show confidence meters on Admin panel body) look like tiny grey dashes | Flagged |
| 14 | Polish | Light theme | Topbar appears translucent when content scrolls under it in light mode (glass effect designed for dark reads as smeared in light) | Flagged |

## Part 1 — Light theme audit

Measured via live WCAG contrast scan in-browser against real rendered colors (WCAG 2.1 formula, alpha-composited over the effective background). Threshold: 4.5:1 normal text, 3.0:1 large text (≥24px or ≥18.67px bold). Nav tab mode was `data-theme="light"`, default `data-accent="bright"`, `data-density="comfortable"`.

### Route-by-route findings (light theme)

- **`/` (Home)** — `01-home-1440-light.png`. KPI values read cleanly; source chips navy-on-white; Reconnecting banner (yellow) borderline but legible. No failing elements above AA after fixes.
- **`/segment-intelligence`** — `02-segment-1440-light.png`, `02-segment-1920-light.png`. Pre-fix: error banner 3.5:1, filter chip "US" 1.84:1, ITM segment card top-border teal vs white card border faint. Post-fix: banner uses `--signal-danger` (#DC2626, 4.83:1), active filter uses `--accent-ink` navy (~9:1), segment-card top borders still faint (polish).
- **`/lead-queue`** — `03-lead-queue-1440-light.png`. Pre-fix: empty table because of blocker #1 plus the error banner at 3.5:1. Post-fix: leads load, banner red passes AA. Row density + score chips can't be scanned until backend bounce (HMR picks up frontend but uvicorn isn't hot-reloading — see "Runtime status" below).
- **`/borrower-360/B-102FL7THC6Q3L`** — `04-borrower360-1440-light.png`, `11-borrower360-final-fix-1440-light.png`. Pre-fix: three segment chips (In the Money teal, Investor pink, Home Equity bright-blue) all failing AA on white card (1.57, 2.65, 1.91). Post-fix: navy text on segment-hued fill — ~11:1.
- **`/offer-orchestrator/B-102FL7THC6Q3L`** — `05-offer-orchestrator-1440-light.png`, `05c-offer-orchestrator-scrolled-1440-light.png`. Approval banner + "Approve outreach" visible after scroll. Primary-CTA panel contrast polish issue (flagged, not fixed).
- **`/ask-genie`** — `06-ask-genie-1440-light.png`. "Ask Genie" CTA — same CTA-panel polish issue. Trusted-asset table list + suggested questions read cleanly.
- **`/admin-config`** — `07-admin-1440-light.png`. Tweak-row segmented controls (Theme / Density) pre-fix 1.91:1 on active state — fixed. Toggle switches (Show evidence chips) still visually weak in light (polish).
- **`/portfolio-builder`** — `08-portfolio-1440-light.png`. Clean. "Run build" CTA polish issue.
- **Focus states** — `09-focus-state-1440-light.png`, `09b-focus-nav-1440-light.png`. `:focus-visible` ring (`--accent` outline) is visible and sufficient in light. No blocker.

### What I changed

- `backend/services/pii_redaction.py` — coerce `city` / `state` / `zip` to `""` when `NULL` in UC rows. Eliminates the `/api/leads` 500 that came from real rural/PO-box borrower rows.
- `frontend/src/design-system/tokens.css` — added `--accent-ink` token (resolves to accent in dark, navy in light-with-bright/teal accent). Darkened `--signal-success` in light to `#047857` (5.1:1). Documented why.
- `frontend/src/design-system/components.css` — active filter chip, active segmented button now pull text from `--accent-ink`. Added `.chip--segment` rule that keeps segment hue in dark, navy in light.
- `frontend/src/routes/borrower-360.tsx` — segment chips render with `.chip--segment` class + `--chip-hue` CSS var instead of inline bright text.
- `frontend/src/routes/{lead-queue,segment-intelligence,portfolio-builder,offer-orchestrator,ask-genie}.tsx` + `components/mortgage/AgentActivityLog.tsx` — fixed `--signal-error` typo → `--signal-danger` (5 routes, 8 sites). Also `--signal-success, #10B981` fallback removed so the light-theme override takes effect.

Effect: WCAG scans on `/segment-intelligence` + `/borrower-360` after fix flag only `ENT` (false positive — dark logo bg) and four borderline `--text-3` eyebrow labels at 4.32–4.47:1 (below 4.5 by a hair — flagged polish).

## Part 2 — Loan-officer persona walkthrough

Persona: Rachel, LO at Summit Mortgage. 40 approvals to clear by EOD. Success = approve/reject confidently in <60 seconds per lead.

### Step-by-step flow

1. **Bookmark `/lead-queue` deep link** — works (route is stable). One click from address bar.
2. **Scan the queue** — **BLOCKED pre-fix** (500 error, zero rows). Post-fix Rachel sees 500 ranked borrowers, tabular columns: borrower / location / segments / equity / rate Δ / NBO / score / confidence / approval.
3. **Pick a lead + expand row** — 1 click to expand. Row preview shows score, confidence bars, segments. Sufficient for a "do I trust this?" gut check, but no timeline events or rationale sentence in the expanded preview — Rachel must drill.
4. **Drill to Borrower 360** — 1 more click (row action). Route change. ~2s load (real dossier gold read).
5. **Drill to Offer Orchestrator** — "Build outreach draft" button, 1 click, route change.
6. **Approve** — scroll down past considered alternatives + thresholds, 1 click on "Approve outreach."
7. **Back to queue** — browser back or Leads nav, 1 click.

**Click count per approval: 5–6** (expand row → Borrower 360 → Offer Orchestrator → scroll → approve → back).
**Route changes: 3** (queue → borrower-360 → offer-orchestrator → back to queue).
**Realistic time per lead: 45–90s** once backend works. 40 in an hour = 90s each — tight but feasible.

### Top 5 LO friction points

1. **Leads 500 blocks everything.** Blocker #1 — fixed. Without this, Rachel literally has no queue.
2. **No bulk "approve all ≥90 confidence" action.** Every approval is a 3-route drill. For batch-clearing a queue of 40, Rachel needs a bulk-approve-filtered-subset with one confirmation. Compliance argument to keep single-touch exists (each row is a different borrower / offer / channel), but a bulk path with "attested as reviewed" would halve her day. Flagged, not implemented (design/compliance discussion needed).
3. **No "approve from Borrower 360" or "approve from row."** Approve button lives only on Offer Orchestrator. Rachel who trusts the NBO could save one route change per lead. Flagged.
4. **Approve button buried below the fold** on Offer Orchestrator. "Approve outreach" sits after Considered Alternatives + Thresholds Applied sections. Rachel scrolls ~400px to reach it. Moving the Approval banner to above-fold (or making it sticky) saves 1–2s per lead = ~60s per day. Flagged.
5. **No freshness timestamp on Cotality source chips** in the dossier rationale. Rachel sees "397 bps voluntary lien" but not "as of 2026-04-20" — for a stale-quote call she has to open the evidence drawer. Adding `signal.observed_at` to the rationale chip tooltip would remove the drawer detour on many leads. Flagged.

### Bulk-approve decision

Not a bug — it's an intentional single-touch compliance design (every outreach is a human-approved audit event). For a 40-a-day queue this is the right posture; for a 400-a-day queue it would break. Recommend surfacing the design choice in copy ("Each approval writes one audit event — bulk actions are intentionally disabled") so LOs stop hunting for the bulk button.

## Prioritized fix list

### Implemented (blockers)

1. `/api/leads` 500 from NULL city/zip. `pii_redaction.py`.
2. `--signal-error` typo breaks light-theme error banner contrast. 5 files.
3. Segment chip text unreadable in light. `.chip--segment` + component.
4. Active filter/segmented-control text unreadable in light. `--accent-ink` token.
5. `.kpi__delta.up` green unreadable in light. Token darkened.

### Flagged (polish / future)

6. Bulk "approve all ≥N confidence" gated by compliance review.
7. Add approve-from-row shortcut (one-click approve with inline confirm).
8. Move Approval banner above the fold on Offer Orchestrator (or make it sticky footer).
9. Geography map state polygons: bump stroke in light to `rgba(2,80,128,0.28)` = `--line-3`.
10. Primary-CTA visual weight in light: add stronger `box-shadow` or border so buttons don't recede even when text contrast is fine.
11. Toggle switches on Admin/Console body need `--line-2` outline in light.
12. Topbar opacity: set to `var(--bg-1)` (solid white) when light theme is active.
13. Source-chip freshness timestamp tooltip.
14. Tighten `--text-3` from `#5A7890` (4.32) to `#557087` (≥4.6) for borderline eyebrow labels.

## Runtime status

The running uvicorn (PID 67040) is not in `--reload` mode, so the `pii_redaction.py` fix is not yet live on port 8000 — the Lead Queue / Segment Intelligence banner will continue to show `Couldn't load leads: 500` until the backend is bounced. The repo-side fix is verified in-process via the venv: `r.list() → 500 rows, first B-102FL7THC6Q3L`.

## Verification gate

- `npx tsc -b frontend` — clean
- `npm --prefix frontend run lint` — clean (0 warnings)
- `npm --prefix frontend run build` — passes (340.6 kB main bundle, 40.5 kB CSS)
- `pytest tests/unit/ -k "redact or pii or lead_row"` — 67 passed, 312 deselected

## Files touched

Backend:
- `backend/services/pii_redaction.py`

Frontend:
- `frontend/src/design-system/tokens.css`
- `frontend/src/design-system/components.css`
- `frontend/src/routes/borrower-360.tsx`
- `frontend/src/routes/lead-queue.tsx`
- `frontend/src/routes/segment-intelligence.tsx`
- `frontend/src/routes/portfolio-builder.tsx`
- `frontend/src/routes/offer-orchestrator.tsx`
- `frontend/src/routes/ask-genie.tsx`
- `frontend/src/components/mortgage/AgentActivityLog.tsx`

Screenshots:
- `docs/validation/screenshots/light-theme-audit/01-home-1440-light.png` … `/11-borrower360-final-fix-1440-light.png`
