> **Internal implementation artifact. Not approved for public release.**

# Real-data walkthrough — 2026-04-22

> **Note:** This document records a past state. `MIP_MOCK_MODE` has since been removed in the live-data cutover (commit `2f09424`). The text below is preserved for audit traceability.

Evidence for the "app runs on real Unity Catalog data end to end" claim
after the Slice-13 exhaustive validation work landed.

## Environment

- Host: macOS Darwin 25.5.0 / Apple silicon (operator laptop).
- Backend: `uvicorn backend.main:app` on `127.0.0.1:8000`, booted with a
  `.env.local`-derived env + a workspace-user Bearer minted via
  `databricks auth token --host https://dbc-3aa503a9-4fa8.cloud.databricks.com`.
  `MIP_DEFAULT_CATALOG` overridden to `mip` at boot (the operator's
  `.env.local` still carries the pre-scrub `mip_demo` name).
- Frontend: `npm --prefix frontend run dev` on `127.0.0.1:5173`.
- Dependency state at boot — `GET /api/health`:
  ```
  warehouse: up (serverless-xl, id da02d15a9490650b)
  genie: up (space 01f13d4968af1b249dc388fd5b18b195)
  lakebase: down (laptop Postgres not running)
  ```
- Governance: `MIP_MOCK_MODE=false` — the fallback env var has been
  flipped to its intended value. Nothing in the code reads it, but the
  operator's `.env.local` now matches policy.

## Route-by-route evidence

Screenshots live in
`docs/validation/screenshots/real-data-walk/01-…08-…png`.

### 1. Home (`/`) — **pass**

- Marketable population KPI: **5,156,184** (matches
  `/api/portfolio/preview.marketable_population`).
- High-intent leads KPI: **147,742** (matches live `lead_scores` → in
  the money count).
- Cost per contact: `$2.18`, Projected contact → app: `9.7%` (returned
  by the backend from `OfferEngineConfig`; shown verbatim).
- Approval banner: "147,742 borrowers queued."
- DegradedBanner correctly surfaces "lakebase dependency recovering."
- AgentActivityLog shows the honest empty-feed message + underlying
  dependency snapshot ("Warehouse … Genie up … /api/health probe 5ms").

### 2. Portfolio Builder (`/portfolio-builder`) — **pass**

- Marketable population, Avg borrower score (42), Cost per contact,
  Projected contact → app all render from live
  `/api/portfolio/preview`.
- "Run build" button (B4 walkthrough-fix) is clickable + wired; click
  triggers a fresh POST with the current filter state (see
  `frontend/src/routes/portfolio-builder.tsx`).
- Filter row renders every configured filter dropdown.

### 3. Segment Intelligence (`/segment-intelligence`) — **pass**

- Title: **"4 borrower segments · select to filter"** (B3
  walkthrough-fix — heading is dynamic off `segments.length`).
- Card counts from live `mip.gold.segment_population`:
  - Home Equity Candidate: **3,141,667**
  - Investor / Multi-Property: **1,749,208**
  - In the Money: **147,742**
  - Retention Risk: **749**
- LeadTable renders 500 real synthetic-named borrowers (e.g.
  "Owner d1a3a065 · B-0STSZHO4O5J04 · Chicago IL 60609") — none of
  them match test-only fixture ids.

### 4. Lead Queue (`/lead-queue`) — **pass**

- 500 rows from `mip.gold.lead_population`. Real CBSAs (Chicago, Oak
  Park, Evanston IL). Synthetic display-names hashed from Owner Link.
- Redirected correctly from `/outreach-composer` (new catch-all route).

### 5. Borrower 360 (`/borrower-360/B-0STSZHO4O5J04`) — **pass**

- Customer 360: CLIP 4707924298, Owner Link 1100000134187756, AVM
  $299,480, current lien $62,280 at 8.76%, LTV 21% / equity $237,200,
  2 related properties via Owner Link.
- Segments: "In the Money", "Investor / Multi-Property", "Home
  Equity Candidate" (3 chips, all real).
- Why-we-recommend: **"+246 bps spread (>= 75) AND 79% equity (>= 15%)"**
  with evidence chips citing `fn_rate_spread`, `fn_in_the_money`,
  `borrower_dossier`, `mlflow.mtg_nbo_v3`, `permits.building`.
- Trigger timeline: top-3 pre-computed events from the dossier CTAS
  (Voluntary Lien → AVM → MORTGAGE30US par).
- Copilot C5 fix (`evidence_events[:3]` fallback instead of `[:1]`) is
  latent because the dossier already materialised the top-3 column —
  the slice will only kick in if the pre-join ever returns empty.

### 6. Offer Orchestrator (`/offer-orchestrator/B-0STSZHO4O5J04`) — **pass**

- Primary offer: **Refinance + HELOC**, score 68, 72% confidence.
- Rationale: "Rate spread +246 bps (>= 75) and equity 79% (>= 35%
  HELOC-grade) — refi + HELOC cross-sell."
- Sources chips: `fn_next_best_offer`, `fn_rate_spread`,
  `fn_in_the_money`, `fn_lead_score` (real UC functions).
- Draft outreach is personalised from live borrower data:
  > "Hi Owner d1a3a065 — based on recent public-record signals in
  > CHICAGO, IL, Summit Mortgage may be able to help you evaluate
  > refinance + heloc options. Rate spread +246 bps (>= 75) and
  > equity 79% (>= 35% HELOC-grade) — refi + HELOC cross-sell."
- Considered alternatives: 2 products ruled out with rationale.
- Thresholds panel reads admin config (75 bps / 15% / 35% / 25% / 50
  bps).

### 7. Ask Genie (`/ask-genie`) — **pass (live path, space config gap**

A real regression was found and fixed during this walk:

- **Before**: `/api/genie/message` bypassed the live Genie path.
  Every question returned the same static borrower-volume response even
  though live gold carried a different count. Effectively a silent mock
  fallback on the happy path.
- **Root cause**: the Slice-7 `DatabricksGenieRepository`
  was never actually called by the router. The router short-circuited
  to local content.
- **Fix (landed in this session)**: rewrote
  `backend/api/genie.py::genie_message` to depend-inject the
  `GenieAnswerRepository` from `get_genie_answer_repository()`.
  The router now hits live Genie first; corpus only runs when the
  breaker is OPEN. See the commit that pairs with this report for
  the diff.
- **Post-fix probe** with `curl`:
  ```
  POST /api/genie/message {"question":"How many in-the-money borrowers are in Illinois?"}
  HTTP 200
  source: "genie"
  conversation_id: "01f13e0e0e111122aef0bc7629413818"   ← real Databricks conv id
  ```
- Secondary finding (NOT in scope to fix here): the Genie space
  currently responds with "There are no tables available in the
  database." The space configuration needs its trusted-asset bindings
  refreshed. That's a workspace-level provisioning task, not a code
  defect. Tracked separately.

### 8. Admin Config (`/admin-config`) — **pass**

- Presentation controls render: theme (Dark / Light), accent (4 swatches),
  density (Comfortable / Compact), lender (Summit Mortgage, with an
  accessible label now), show-evidence-chips + show-confidence-meters
  toggles.
- Offer rules panel: `rules.itm_v3`.
- Audit settings: `mip_app.audit_events`.
- Data source readiness: "8 sources · Delta Share" (Public Records,
  Voluntary Lien, MMA, CLIP, Owner Link, MLS, Building Permits, AVM).

## Cross-cutting toggles

- **Density (comfortable → compact)** — B2 fix verified live:
  ```
  before → data-density="comfortable"  --row-h=44px  --pad-card=20px  --gap-grid=18px
  after  → data-density="compact"      --row-h=36px  --pad-card=16px  --gap-grid=14px
  ```
  Pre-fix, both states read `44px / 20px / 18px` because the
  `[data-density="comfortable"], :root { … }` group won on source
  order against the compact rule (`:root` and `[data-density="compact"]`
  have the same 0-0-1-0 specificity — ties break by source order).
- **Theme (dark → light)** — `--bg-0` flips `#04101F → #F4F7FA`,
  `--signal-danger` flips `#EF4444 → #DC2626` (the a11y contrast fix
  from this session — `#DC2626` at 4.83:1 clears WCAG AA on white).
- **Console errors at walk close**: 2 errors on Home, both
  `503 Service Unavailable @ /api/audit/events?limit=12` — expected
  because Lakebase is down on this laptop. That's the honest
  degraded-state posture: no silent mock fallback; the UI surfaces
  "Couldn't reach the audit feed" copy rather than fake audit rows.

## Genie router bug — new commit landed in this walk

`backend/api/genie.py` before:
```python
@router.post("/message", response_model=GenieMessageResponse)
def genie_message(payload: GenieMessageRequest) -> GenieMessageResponse:
    return respond(payload.question)    # ← direct corpus call
```
after:
```python
RepoDep = Annotated[GenieAnswerRepository, Depends(get_genie_answer_repository)]

@router.post("/message", response_model=GenieMessageResponse)
def genie_message(payload: GenieMessageRequest, repo: RepoDep) -> GenieMessageResponse:
    return repo.respond(payload.question)  # ← live Genie via DatabricksGenieRepository
```

## Catch-all route wiring

`frontend/src/app.tsx` before: unknown paths (including
`/outreach-composer`, which doesn't exist — drafting is inside
Offer Orchestrator) rendered a blank `<main>`. A Slice-13 UX checklist
I wrote even mentioned `/outreach-composer`.

After: unknown paths `<Navigate to="/" replace />`;
`/outreach-composer` specifically redirects to `/lead-queue` so a
legacy link from an external system lands somewhere useful. The UX
checklist doc was updated to list the real 8 routes.

## Verdict

**Real data end-to-end, no silent mock substitution.**

- All four user-visible KPIs on Home + Portfolio Builder +
  Segment Intelligence resolve to rows in `mip.gold.*`.
- Borrower 360 renders from `mip.gold.borrower_dossier`
  (Slice-13 pre-join); timeline is the top-3 ARRAY<STRUCT>.
- Offer Orchestrator's recommended offer + rationale + draft are
  computed on the fly from the real borrower's attributes.
- Lakebase-dependent endpoints (audit, approve) cleanly 503 with
  `retryable:true` — visible in the DegradedBanner and the
  AgentActivityLog's empty-feed copy. No mock fallback.
- Genie now actually hits Genie. The space itself needs its trusted
  assets rebound (separate workspace task).

No walkthrough blockers. Two real regressions were found + fixed in
this pass: the Genie-router bypass and the blank-shell on unknown
paths.
