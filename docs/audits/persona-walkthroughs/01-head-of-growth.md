# Persona walkthrough 1 — Head of Growth

> **Internal validation artifact — not approved for public release.** This document contains deployment identifiers, workspace/warehouse references, and implementation notes intended for engineering review.

> *In-character audit. I am "Pat," Head of Growth at Summit Mortgage. It is Monday morning; the CEO and CMO want a 15-minute top-of-funnel recap at 11am. Below is what I actually did in the app, what I noticed, and where the experience helped me or fought me.*

**Auditor:** Claude (Cowork) acting as Pat, Head of Growth
**App:** `https://mip-app-2543889327043640.aws.databricksapps.com`, deployment `01f14d00b90b15bba16e412e31a8edbd`
**Method:** Read-only Chrome screenshots + live `/api/*` calls with my workspace OAuth token + direct UC SQL on warehouse `81d08d4fa2d799e9`. Borrower IDs are synthetic; no real PII touched.

## Post-remediation independent re-validation (Claude, 2026-05-11 02:40 UTC, deployment `01f14d00b90b15bba16e412e31a8edbd`)

Every claimed fix below was independently re-exercised against the live deployment after the remediation pass. All pass.

| Finding | Engineering status | Independent live re-validation |
|---|---|---|
| P0-G1 Em-dash KPIs + "50 states" loading flash | Skeleton classes + chip fallback rewrite. | ✅ `KpiCard.tsx:76-77` now renders `skeleton kpi__value-skeleton` / `kpi__delta-skeleton` while loading; `USChoroplethMap.tsx:1105` renders `"Loading coverage…"` instead of `"50 states · click to drill"` until `footprint.dataScope` is set. Cold-load screenshot taken at 02:40 UTC shows hydrated values + "6 counties · click to drill". |
| P0-G2 Misleading trend deltas | Backend emits per-trend `note` for step changes; series alignment fixed; UI renders `kpi__note`. | ✅ Live `/api/portfolio/preview`: top-tier series is now **7 points** (was 6); `comparison_label` is `"vs 2026-04-23"` on every card (consistent). High-intent `note = "Material step change on 2026-05-07; verify rules or refresh context before presenting this as market movement."`; top-tier `note` analogous for 2026-05-06. UI renders the note under the delta — screenshotted live. |
| P1-G3 Unknown POST fields | `extra='forbid'` on `PortfolioCriteria` + outer request model. | ✅ `POST /api/portfolio/preview {"geography_states":["CA"],"occupancy":"Owner-occupied","equity_threshold":"gte 25%"}` → **HTTP 422** with three `extra_forbidden` entries citing each unknown field. Nested unknown (`{"criteria":{"foo":"bar"}}`) → 422 too. Canonical body still 200s. |
| P1-G4 Single-state filter only | `criteria.states: list[str]` added. | ✅ `POST /api/portfolio/preview {"criteria":{"states":["CA","WA"]}}` → 200 with `marketable_population=1,638,053` — exactly `CA (900,371) + WA (737,682)`. Legacy `geography_states` correctly rejected as `extra_forbidden` (single canonical key). |
| P1-G5 No save-build / no audit | Portfolio create endpoint wired with audit + PII name guard. | ✅ `POST /api/portfolio/create {"name":"Alice Smith"}` → **HTTP 422** "name cannot contain PII, raw identifiers, or street addresses". Same for "Alice Q Smith pilot". Safe name "Q3 CA cash-out pilot" → 200 with `portfolio_id` AND `audit_event_id` populated — audit trail intact. |
| P1-G7 CSV missing context | Metadata + truth flags added to export. | ✅ `LeadTable.tsx:120-125` now prepends 4 `# generated_at|filters|refreshed_at|rules_version` comment lines. New columns include `is_owner_occupied`, `is_investor`, `is_current_customer`, `is_former_customer`, `is_competitor_lien`, `current_lender_ref`, `second_pos_amount`, `has_permit`, `listed_for_sale`, `related_property_count`. |
| P1-G8 Cotality lane labeled `pending` | `laneStatusSummary` derives a composite "N live · M roadmap" chip. | ✅ `DataEstatePanel.tsx:37-54` computes live/synthetic/roadmap/blocked counts and renders the joined string. Live screenshot shows Cotality lane chip reading **"7 live · 2 roadmap"** instead of the prior `pending`. |
| P2-G11 Approval queue has no CTA | `<Link to="/lead-queue?segment=itm">Open review queue</Link>` added. | ✅ `home.tsx:324-327`. Live render shows the **"Open review queue"** button; sub-text enriched to "134,534 high-intent borrowers ready for loan-officer review. 3 approved and 0 in outreach in the latest snapshot." |
| P2-G12 Genie cash-out → equity segment | Action criteria now carries the offer/product, not a segment fallback. | ✅ Fresh Genie conversation against the cash-out question returned `open_cohort.route = "/lead-queue?states=IL&product=Cash-out"` and `criteria.portfolio_criteria = {"product":"Cash-out"}`. Loading the resulting URL lands on a queue with `states=IL` and `product=Cash-out` chips and rows whose next-best-offer is "Cash-out Refi". |
| P2-G13 Segment cards "+0%" | UI swap to "first snapshot · deltas pending". | ✅ `SegmentCard.tsx:131-132`. Live Segment Intelligence screenshot shows the new copy on all four non-pending segment cards. API still returns `"+0%"` — that's intentional; the UI translates it. |

**False positive I corrected myself:** my original P1-G4 wrote that the lead queue accepts `?states=IL,WA` as comma-separated; the canonical field on `/api/portfolio/preview` is actually a JSON array `states: ["CA","WA"]`. The fix landed cleanly on the array path and the legacy `geography_states` alias is correctly rejected.

**Engineering remediation message I was given that I also confirmed:**
- Deployment ID = `01f14d00b90b15bba16e412e31a8edbd`, `status=SUCCEEDED`, `app_status=RUNNING`, `compute_status=ACTIVE` (verified via `databricks api get /api/2.0/apps/mip-app`).
- The engineering caveat about Cotality MLS/Listings + Building Permits feeds still being on `roadmap` is correctly disclosed on the data-estate panel (`Listings overlay → roadmap`, `Permit overlay → roadmap`) and the lane summary ("7 live · 2 roadmap").
- The platform-edge `gap-auth` + `x-databricks-internal-pod-ip` headers from the earlier audit pass are unchanged on this deployment — still the right gating decision for public release.

---

## Pat's actual journey

1. Open Home → glance at headline KPIs and trends.
2. Skim the AI data estate panel to confirm we can defend the numbers to compliance.
3. Look at geography drill-down to see if any state is over/under-indexing.
4. Pop into Segment Intelligence to find a growth wedge ("which segment is moving the needle?").
5. Ask Genie a strategic question I couldn't answer from the cards alone.
6. Take Genie's "Open this cohort in Lead Queue" action to set up a pilot.
7. Run a what-if in Portfolio Builder ("CA-only, equity ≥ 25%, competitor-owned").
8. Check Admin to confirm rules version + governance for the deck.
9. Drill into one named borrower so I have a concrete story to tell.
10. Export the cohort to share with the CMO.

---

## What works well (Pat-perspective wins)

- **Data lineage I can defend.** The "AI data estate under the hood" panel on Home (First-party · Cotality · Databricks · Entrada) is exactly the slide my CFO will want. Row counts and freshness dates are visible. I can cite specific UC tables in a board memo.
- **Genie is grounded and shows its work.** Asking "Which state has the most cash-out opportunity right now?" returned **Illinois: 1,114,730 cash-out borrowers, avg score 39.2**, with the generated SQL, the source asset (`mip.gold.borrower_360`), the data freshness, and a `trusted: true` flag — verified against direct SQL, the number is exact.
- **Genie → cohort handoff works.** The "Open this cohort in Lead Queue" suggested action produced a deep-link `/lead-queue?states=IL&product=Cash-out` and, after confirmation, a governed `cohort_id` route that genuinely landed on a filtered queue with visible `states = IL` and `product = Cash-out` chips. The confirmation token is a signed action token — governance-clean.
- **Rules version is auditable.** Admin shows the active offer-rules version (`rules.itm_4df231d5472f`), "Edited May 9, 2026", and an `Active` badge. Last audit event timestamp and actor are visible. I can answer "are these the rules you presented to the credit committee?" in one click.
- **Borrower 360 storytelling.** When I drill into B-102FL7THC6Q3L (top-ranked lead) I get: synthetic name, location, AVM, LTV, related-properties, segment chips, why-now ribbon, trigger timeline, and 8 evidence chips that cite Cotality silver tables (`mip.silver.lien_current`, `mip.silver.property_master`, etc.). One named story I can read in 30 seconds at the exec sync.
- **Honest disclosure on pending feeds.** "Awaiting Cotality MLS Delta Share" and "Awaiting Cotality Building Permits Delta Share" pills on the Listed-for-sale and Permit-Activity segment cards. Saves me from making claims I can't back up.

---

## Issues found, severity-tagged

### P0 — Things that would burn me in front of the CEO

#### P0-G1. Initial home-page load shows em-dash placeholders for ~4 seconds before KPI numbers paint

Pat-impact: "I open the bookmark, I'm screen-sharing, the four headline numbers are blank for four seconds." On a Monday-morning rehearsal that's the moment a junior exec on the call thinks "is the system broken?"

Repro: cold-load `https://mip-app-2543889327043640.aws.databricksapps.com/`. Screenshots at t=0s show all four KPI cards rendering "—". The same load shows the geography chip on the map reading **"50 states · click to drill"** (the loading-state fallback) before it switches to "6 counties · click to drill" once `footprint.dataScope.county_count` hydrates.

Code refs:
- KPI chip uses fallback to `Object.keys(supportedCountyStates).length` until `footprint.dataScope?.county_count` is set — `frontend/src/components/mortgage/USChoroplethMap.tsx:1102-1105`
- KPI cards render `—` while the portfolio-preview query is in flight

Fix proposals:
1. Show a skeleton-shimmer or a "warming up" badge on each KPI card rather than `—`. Today `—` is also the legitimate render for genuinely-unknown values; collapsing two states into one symbol is dangerous.
2. Suppress the "50 states" fallback chip until `dataScope` is loaded; show "Loading coverage…" or just hide the chip until it resolves.

#### P0-G2. The two most prominent trend deltas on Home are misleading without context

The cards display:

| KPI | Value | Delta | Comparison label |
|---|---:|---:|---|
| Marketable Population | 5,156,184 | **0.0%** | vs 2026-04-22 |
| High-Intent Leads | 134,534 | **−8.9%** | vs 2026-04-22 |
| Top-Tier Opportunities | 4,320 | **+40.2%** | vs 2026-04-23 |
| Offers Recommended | 4,472,648 | **+0.1%** | vs 2026-04-22 |

Pat reads this as: "Marketable is flat, high-intent dropped 9%, top-tier exploded 40%, offers flat." It's the lead story. But pulling the underlying `series` from `/api/portfolio/preview` reveals:

- **High-Intent series:** `[147742, 147742, 147742, 147742, 134534, 134534, 134534]` — a **one-time step-down** on May 7 because the offer-rules version flipped (rules version was `itm_77eddaa7d767` per the earlier admin endpoint snapshot; today's is `itm_4df231d5472f`). The −8.9% isn't market movement; it's a config change.
- **Top-Tier series:** `[3081, 3074, 4542, 4320, 4320, 4320]` — only **six points** instead of seven (note the comparison label is "vs 2026-04-23" instead of "vs 2026-04-22"). The jump from 3,074 to 4,542 is the same config flip.
- **Avg score series:** `[42, 36, 36, 37, 37, 37, 37]` — **−14% in one day**, no card displays this even though it materially affects pipeline quality talking points.
- **Approved series:** `[1, 1, 2, 3, 3, 3, 3]` — **3 approvals over 7 days** against 134,534 in-the-money borrowers. Either LO adoption is critically low or the lifecycle mirror is undercounting.
- **In-outreach series:** `[0, 0, 0, 0, 0, 0, 0]` — literally zero outreach has gone out through this system this week.

If I present "+40% in top-tier" at the exec sync without the rules-change footnote, I am misleading the room. The dashboard does not surface the version change anywhere on the home cards.

Code refs:
- Trend series and labels: `backend/services/repositories/databricks_repo.py` `_FUNNEL_BUCKET_SQL` and the trend-history block (`top_tier_opportunities` reads from a different bucket than the others, producing the off-by-one series length).
- Rules version is exposed on `/api/admin/rules` but never joined to the trend response.

Fix proposals:
1. Detect a "step change" in any KPI's series (e.g. ratio between adjacent points > 1.2× while others stay flat) and overlay a "config change on YYYY-MM-DD" footnote on the card.
2. Make all KPI series cover the same window — the missing top-tier point at 2026-04-22 is a real pipeline bug, not just a UI nit.
3. Add an "Approvals & outreach this week" card sourced from the same trend response so the 3-approvals / 0-outreach reality is visible alongside the bigger numbers (today these counts ride hidden on the response but only render on the approval banner).

### P1 — Friction that costs me time or credibility

#### P1-G3. Portfolio Builder accepts unknown POST fields silently, returning unfiltered KPIs

If I (or a future integration) POST `{"geography_states": ["CA"], "occupancy": "Owner-occupied", "equity_threshold": ">= 25%"}` to `/api/portfolio/preview`, the response is the **default unfiltered preview** — marketable 5,156,184, exactly what an empty body returns. The keys I sent aren't `geography` / `occupancy` / `min_equity_pct_label`, and Pydantic's default `extra='ignore'` swallows them.

The visible UI does work — it sends the right shape — but the API will silently mislead anyone hitting it directly.

Code refs:
- Schema: `backend/schemas/portfolio.py` `PortfolioCriteria` (keys `geography`, `occupancy`, `lien_status`, `min_equity_pct_label`, etc., wrapped in `PortfolioPreviewRequest{criteria}`)
- The endpoint accepts a bare `criteria` body or even an empty body without rejecting unknown keys.

Fix: set `model_config = ConfigDict(extra='forbid')` on `PortfolioCriteria` so unknown keys 422 with a clear error. Costs nothing, prevents real customer support calls.

#### P1-G4. Single-state filter only

The portfolio-preview `geography` field is a single string. A real growth pilot is "CA + NY + WA, owner-occupied, competitor-owned." `/api/leads?states=IL,WA` accepts a list, and the geo rollups expose the current refreshed footprint dynamically — but the portfolio preview can't combine them. So I have to run three previews and add them in my head.

Fix: make `geography` `list[str] | None` on `PortfolioCriteria` (mirror the lead-queue behaviour) and update the dropdown to a multi-select.

#### P1-G5. "Share this build" only copies a URL — no save, no name, no expiry

I can run a pilot ("CA only, equity ≥ 25%") and click Share — that copies the URL to my clipboard. There's no "Save this build as 'Q3 CA Pilot — competitor recapture'", no shared library, no way for the CMO to see "all open pilots." For a Head of Growth juggling 5–10 scenarios this is friction.

Code ref: `frontend/src/routes/portfolio-builder.tsx:377-390` — the button only calls `onCopyLink()`. No persistence.

Fix proposals: add `POST /api/portfolio/create` (already exists in `PortfolioCreateRequest` schema!) wiring to the Share button, surfacing a "Saved builds" list under the page header.

#### P1-G6. Portfolio Builder hides trends for any filtered build, with no in-product way to backfill

"Trend lines are hidden for this filtered build because daily snapshots are not stored at this custom filter grain." Honest, but it means I can't see "is my CA-equity pilot population growing or shrinking" — which is THE Head of Growth question.

Fix: surface a "Pin this build to start collecting daily snapshots" affordance. The save-build path from P1-G5 is the natural place to trigger snapshot accrual.

#### P1-G7. No "as-of" or filter-context lines in the CSV export

`Export list` in the lead queue produces `mip-leads-YYYY-MM-DD.csv` with a header row but **no comment lines** about (a) which filters were applied, (b) the source warehouse refresh timestamp, (c) the rules version. If I email this to the CRM team, they'll have to come back and ask "what filter was this?" Code: `frontend/src/components/mortgage/LeadTable.tsx:593-642`.

Fix: prepend three `#` comment lines: `# generated_at`, `# filters`, `# refreshed_at`, `# rules_version`. CSV readers ignore them; receivers won't.

#### P1-G8. "Cotality and market enrichment" lane labeled `pending` on Home even though 7 of 9 assets are live

The home page data-estate panel shows the Cotality lane with a `pending` chip. Looking at the underlying detail, 6 of the 8 assets are `live` (Public Records, Voluntary Lien, MMA, CLIP, Owner Link, AVM, FRED) and only 2 are `roadmap` (MLS, Building Permits). The lane status reflects the worst child; for a Head of Growth presenting "we have Cotality coverage today," the lane chip undersells what's working.

Fix: derive the lane status from `majority(asset.status)`, or split into two chips (e.g. "6 live · 2 roadmap").

### P2 — Polish / quality of life

#### P2-G9. No time-period selector on Home KPIs

Deltas span only 7 days. The Head of Growth wants WoW / MoM / QoQ. Today there is no toggle.

#### P2-G10. No "export Home as PDF / screenshot" affordance

For board decks I'd want a one-click "save this view as PDF" or "share this snapshot." None exists.

#### P2-G11. The "Approval queue" callout has no CTA

"134,534 borrowers awaiting loan-officer approval." Where do I go to see them? There's no link from the callout to the Lead Queue. Today I have to navigate via the top nav.

Code ref: `frontend/src/routes/home.tsx` — the Approval-queue section is text-only.

#### P2-G12. Genie "Open this cohort" mapped a cash-out question to the wrong cohort

Original finding: when I asked "which state has the most cash-out opportunity," the suggested action route used an equity-segment cohort instead of the cash-out offer cohort. Current remediation: the action now opens `/lead-queue?states=IL&product=Cash-out` and materializes a governed `cohort_id`, with visible `states = IL` and `product = Cash-out` chips in Lead Queue.

Code refs: the Genie action criteria mapping is in `backend/services/repositories/databricks_repo.py`; `backend/api/genie.py` validates and materializes the confirmed cohort.

#### P2-G13. Segment cards all show "+0% avg X" — no real WoW deltas

Every non-pending segment card displays `+0% avg N`. With only one daily snapshot in `gold.segment_population_prior`, there is nothing to compare against. Honest, but the "+0%" framing looks like genuine zero movement. Use a "first snapshot — deltas pending" pill instead, mirroring the listed/permit "Awaiting feed" treatment.

#### P2-G14. Geography chip swaps from "50 states" to "6 counties" with no transition

Cosmetic — but the snap from one to the other on hydration draws the eye. A loading skeleton on the chip would be cleaner. (Same root cause as P0-G1.)

#### P2-G15. No "next refresh" hint

Pat looking at "Refreshed May 9, 6:56 PM EDT" wonders "and the next refresh runs… when?" The bundle schedules a daily refresh job; surface its next-run timestamp on Home so I know when the numbers will move.

#### P2-G16. "Build a portfolio" CTA top-right competes with nav

The primary CTA on the Home page header is "Build a portfolio." But the page is fundamentally a status dashboard — Pat's job-to-be-done on Home is "scan, then drill," not "build." Consider demoting the CTA to a secondary surface and making the Approval-queue callout the primary action.

#### P2-G17. CSV export ships borrower IDs but no contactability hint

The CSV does not include `is_owner_occupied`, `is_competitor_lien`, `current_lender_ref` even though these now correctly project on the borrower API (per P0-1 fix in the May 9 audit). For Pat handing this to a marketing-ops team, "is this an owner I can mail vs. a corporate entity I can't" is a critical column. Re-confirm exported columns include the truth flags.

#### P2-G18. No "compare to peer / industry" benchmarking

A pure Head of Growth ask: "is 134K in-the-money against a 5.1M base low or high vs. peers?" The product is a single-tenant Summit Mortgage view. Out of scope for Module 0, but worth noting as a Module-1+ roadmap item.

---

## Rehearsal guidance after remediation

This audit artifact is not the public talk track. Before recording or external
review, Pat should read values directly from the deployed Home, Genie proof,
Admin, and Borrower 360 screens at rehearsal time. The remediated product now
supports the intended story shape without hardcoded narration: live addressable
population, live high-intent and top-tier counts with trend caveats where
applicable, a SQL-backed cash-out state answer from Genie, a confirmed
Genie-to-Lead-Queue cohort handoff, explicit pending-feed disclosure for MLS
Listings and Building Permits, and one masked Borrower 360 evidence story.

P0-G1 and P0-G2 are remediated in the active deployment: KPI cards no longer
render ambiguous em-dashes during warm-up, geography coverage no longer flashes
the prior 50-state fallback, and material KPI step changes now carry
config/refresh caveats instead of relying on presenter memory.

---

## Sources

- Live `/api/portfolio/preview` body inspection (trend series lengths, comparison labels)
- Live `/api/admin/rules` (rules version, market rate)
- Live `/api/genie/message` flow (real Genie SQL, source assets, reasoning trace, action suggestions)
- Live `/api/leads?states=IL&product=Cash-out` and browser-confirmed governed `cohort_id` route (Lead Queue deep-link via Genie action)
- Direct SQL on `mip.gold.borrower_360`, `mip.gold.lead_population`, `mip.gold.segment_population`
- Code refs cited inline above
