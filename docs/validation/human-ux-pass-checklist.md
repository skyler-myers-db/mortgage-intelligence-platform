# Human UX-pass checklist — Module 0

Hand-off checklist for a human evaluator (LO, marketing lead, Databricks
FS reviewer, or exec sponsor) walking Module 0 front-to-back the way a
prospective customer would. This is the bar Playwright can't reach:
how does the product *feel*, does the evidence story land, does the
approval gate read as credible, does nothing look "demo-y."

Run this after every substantive UI change, before every customer
preview, and as the last gate before a release rehearsal.

Target time budget: **15 minutes, end-to-end, unhurried.**

## Pre-flight (2 min)

- [ ] Deployed app reachable at the configured URL (or localhost if
      running against a dev backend).
- [ ] `/api/health` returns `status: "ok"` — if `"degraded"`, note which
      dependency is `"down"` before proceeding.
- [ ] Browser viewport set to **1440×900** (the design target). Close
      devtools unless you're actively checking console errors.
- [ ] Dark theme active (the default). The product's hero presentation
      mode.

## Route-by-route click-through (8 min)

Work through each route exactly once. For every route, ask three
questions out loud: **What am I looking at? Why should I care? What
would I do next?** If any answer is vague or requires context the UI
didn't provide, flag it.

### 1. Home (`/`)

- [ ] The four hero KPIs render real numbers (not `—`, not `Loading…`).
      Cross-check one KPI value against `/api/portfolio/preview` — they
      must match.
- [ ] The geography drill-down map draws all 6 states in the footprint
      (IL / CA / FL / TX / WA / CO). Hover shows the state name + count.
      Click IL — county view loads.
- [ ] The agent activity log shows at least 1 event — either a real
      audit row or the honest empty-state copy ("No activity yet…").
      No fake "Agent · Lead Portfolio" events from fixtures.
- [ ] The "Review approval required" banner renders with the live
      queued count (or the approval-required framing without a count
      if `/api/portfolio/preview` hasn't returned yet).

### 2. Portfolio Builder (`/portfolio-builder`)

- [ ] Filter row renders six filter dropdowns (GEO, OCCUPANCY, LIEN,
      RELATIONSHIP, PRODUCT, EQUITY). All open and change state.
- [ ] "Run build" button is **enabled and clickable**. Click it —
      button label becomes "Running…" briefly, then settles, and the
      KPI row re-populates.
- [ ] KPI cards show real values. No suspiciously-clean "89,553" style
      placeholders on a route we just forced to re-fetch.
- [ ] "Next: segment intelligence" forward-link routes correctly.

### 3. Segment Intelligence (`/segment-intelligence`)

- [ ] Heading reads "N borrower segments · select to filter" where N
      matches the count of rendered SegmentCard components (not hard-
      coded to "Six").
- [ ] Each card shows: icon, name, count, delta, avg score, short
      description, evidence chip. Click a card — LeadTable filters to
      that segment.
- [ ] LeadTable renders real borrower rows (not `B-48291` / `James &
      Maria Rodriguez` fixture IDs).
- [ ] Expand a lead row (`.tbl__expand`) — inline dossier preview
      opens and shows real CLIP, Owner Link, segments, why-now.
- [ ] MapPlaceholder on the right refreshes as you change segment
      selection.

### 4. Lead Queue (`/lead-queue`)

- [ ] Table populated with real borrowers. Filters narrow the result
      set. Empty state appears if a filter yields zero matches.
- [ ] Click a borrower row — navigates to `/borrower-360/:id` with a
      real id (not `B-48291`).

### 5. Borrower 360 (`/borrower-360/:id`)

- [ ] Customer 360 block shows CLIP, Owner Link, subject property,
      AVM, current lien, LTV, related properties, segment chips.
- [ ] Trigger timeline renders at least one event per borrower (most
      have 2-4).
- [ ] "Why we recommend" panel shows rate-spread, in-the-money
      status, and cites trusted assets (`mip.gold.fn_rate_spread`,
      `mip.gold.fn_in_the_money`).
- [ ] "Why we trust" panel lists evidence chips with source product
      names. Click an evidence chip — EvidenceDrawer slides in with
      lineage + signals + updated_at.
- [ ] "Build outreach draft" button routes to
      `/offer-orchestrator/:id`.
- [ ] Navigate directly to `/borrower-360` (no id) — redirects to
      `/lead-queue` (no fixture id fallback).

### 6. Offer Orchestrator (`/offer-orchestrator/:id`)

- [ ] Recommended offer card shows product, confidence, rationale,
      and evidence chips.
- [ ] Draft textarea populated with offer copy referencing the real
      borrower — not a canned template.
- [ ] Approve chip writes to Lakebase — after clicking, the banner
      updates and a new row appears in `/api/audit/events`
      (verify in Admin Config or via `curl`).
- [ ] Reject chip also fires and audit-logs.
- [ ] Navigate directly to `/offer-orchestrator` (no id) — redirects
      to `/lead-queue`.

### 7. Outreach Composer (`/outreach-composer`)

- [ ] Table of recent draft outreach items renders.
- [ ] Status chips (pending / approved / rejected) match what the
      Lakebase `action_audit` table says.

### 8. Ask Genie standalone (`/ask-genie`)

- [ ] Textarea labeled ("Ask Genie — question" accessible name).
- [ ] Type one of the 10 curated questions from
      `genie/sample_questions.md`, click Ask. Answer returns in < 45s
      (first-ever call can be slower). Answer cites trusted gold
      tables. Evidence drawer opens from the evidence chips.
- [ ] Ask an out-of-scope question ("Weather in NYC?"). Genie
      politely declines — it doesn't hallucinate.

## Floating Genie FAB (1 min)

- [ ] Every route shows the floating Genie bubble in the bottom-right.
- [ ] Click to open, type a question, click Ask. Answer returns inline.
      Evidence chips open the EvidenceDrawer from within the FAB.
- [ ] Close the FAB. The app returns to normal flow without
      re-rendering the route.

## Cross-cutting toggles (2 min)

- [ ] **Theme toggle** (topbar) dark ↔ light round-trips. Every route
      re-renders cleanly — no color bleed, no unreadable text.
- [ ] **Density toggle** (console) comfortable ↔ compact changes
      `.kpi__value` / row-height / card padding. Re-open a KPI card's
      evidence drawer in compact — still readable.
- [ ] **Accent toggles** (console) bright / navy / red / teal each
      repaint the accent without visual regressions.
- [ ] **Keyboard navigation**: Tab through the top nav. ESC closes the
      open drawer / FAB. Enter submits the Genie chat.

## Degraded-state sanity (2 min)

- [ ] Stop the backend (`pkill -f uvicorn` locally, or flip a
      dependency via `tools/kill_drill/run_drill.sh --target token`).
- [ ] The DegradedBanner appears inside 10s (SWR hard-TTL bound).
- [ ] Every KPI shows `—`, not a stale cached value.
- [ ] Tables show the "Couldn't reach backend" banner, not an empty
      Loading spinner forever.
- [ ] Restore the backend. The banner clears within one health-poll
      interval. No page reload required.

## Things a customer evaluator WILL notice

These failed past rehearsals and are worth double-checking:

- **Fixture borrower IDs**: `B-48291` / `B-48294` / `B-48295` are
  test-only. They should never appear in production UI.
- **Fixture names**: "James & Maria Rodriguez" / "David Park" / "Lisa
  Thompson" are fixtures. The app's real surface uses whatever
  `mip.gold.borrower_360.display_name` returns, which is synthetic but
  NOT those three.
- **Suspiciously clean KPI numbers**: a number ending in exactly
  `,000` or `,553` that never changes on a re-build is a clue you're
  looking at a hardcoded literal, not the live `/api/portfolio/preview`
  response.
- **Evidence chips with no drawer content**: every chip must open to a
  drawer with lineage / signals / sources. A dead chip is a blocker.
- **Approval that "succeeds" but never writes to Lakebase**: confirm
  via `/api/audit/events` that a new row is there within 1s of the
  approve click.

## Reporting

Each checkpoint is pass / fail / not-applicable (NA when a feature
isn't expected to light up on this deploy — e.g. `delta_vs_prior_*`
widgets on a < 8-day-old deploy per `docs/dashboards.md`).

Log findings in a fresh file under
`docs/validation/human-pass-runs/<YYYY-MM-DD>_<initials>.md`. Include
the deploy version (git SHA), warehouse id, and a one-paragraph
overall verdict: **pass / non-blocking issues / release-blocker**.

Escalate anything release-blocking immediately. Non-blocking issues
file as GitHub issues tagged `ux-polish`.

## See also

- `docs/validation/ux-walkthrough-report.md` — automated Playwright +
  axe walkthrough, complementary to this human pass.
- `docs/validation/dashboard-verification.md` — human-eyeball
  dashboard checks the Playwright layer can't make.
- `docs/validation/credential-kill-drill.md` — the degraded-state
  drill this checklist references.
- `docs/runbook.md` §2 — operator recovery procedures if a drill or
  this checklist exposes a regression.
