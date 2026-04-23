# Module 0 — Executive Walkthrough

- **Title:** Mortgage Intelligence Platform — Module 0: Top-of-Funnel Lead Generation & Borrower Segmentation
- **Audience:** Business (Head of Growth / VP Mortgage Lending / Marketing / Sales Mgmt) + Technical (Databricks FS partner, Cotality product/data, Entrada delivery)
- **Runtime:** 45s open + 6–8 min main + 30s close (~8 min wall clock)
- **Walkthrough team:** Entrada delivery lead + Databricks FS partner (co-drive)
- **App URL:** deployed Databricks App at `https://mip-app-2543889327043640.aws.databricksapps.com`, 1440×900, dark theme, compact density. Auth is workspace-identity Bearer (the App mints short-lived creds via `databricks.sdk.core.Config` — no PAT baked into the runtime).
- **Data posture:** live Unity Catalog on the Cotality Delta Share — never synthesized. Resilience (warehouse warm-start, retries, circuit breakers, SWR-cached health probe, pre-joined gold for hot read paths, degraded-state UI) is how a flaky network is handled; the app fails visibly rather than silently substituting fake data.

---

## Pre-session setup — use the rehearsal checklist

Before stepping up, the operator runs through
[docs/module0-rehearsal-checklist.md](module0-rehearsal-checklist.md).
That checklist warms the serverless warehouse, primes Genie, and confirms
`/api/health` reports `mode:"live"` with every dependency `up` and every
circuit breaker `closed`. Do not start the pitch until that page is green.

---

## Opening — 45 seconds

> "One question, one module. **Who should we contact, why now, and with what offer?**"

> "This is the Mortgage Intelligence Platform on Databricks Apps. Module 0 is top-of-funnel: the real Cotality Delta Share, under Unity Catalog governance, with **5.16 million property snapshots** across six states — Illinois, California, Florida, Texas, Washington, Colorado. **3.1 million of those carry open liens.** Every screen you're about to see queries live gold tables — the data is production, the latency is production, the governance is production."

> "Three principles hold through every screen. *Every recommendation traces to a Cotality source through Unity Catalog. Every score has a rationale. Nothing is sent without human approval, and every approval writes to an immutable Lakebase audit log.* That's the whole contract."

> "Eight routes. Six minutes. Let's go."

---

## Beat 1 — Home · 60 seconds

**Route:** `/` **Clicks:** land on Home; hover each KPI to surface the evidence chip; glance at the right-rail Console.

> "Summit Mortgage's top-of-funnel snapshot against the live share. Every KPI renders from `mip.gold.lead_population` through a short-TTL cache in front of the serverless warehouse. Hover any KPI — an evidence chip cites the source. The ITM count cites `mip.gold.fn_in_the_money`, a real UC SQL function pinned by golden-fixture tests against a Python mirror. Cost per contact cites admin config, not gold; we don't pretend that's derived."

> "Right rail is the **Console**. See the telemetry strip in the footer: *Warehouse up · Genie up · probe 130 ms*. That's a real `/api/health` poll backed by a stale-while-revalidate cache — 2-second soft TTL, 10-second hard TTL, background refresh on a shared executor. A burst of callers shares one probe per dependency, so the banner stays snappy even when Genie takes a full second to reply upstream (p95 on `/api/health` dropped from 1,100 ms to 130 ms, see [docs/load-baseline.md](load-baseline.md)). If the warehouse is cold, you'll see a subtle warming-up banner — real-time honesty, not a stage trick. Audit events land in Lakebase, streamable to UC for compliance."

**Cite:** `mip.gold.lead_population` · `mip.gold.fn_in_the_money` · live `/api/health` (SWR-cached) · Cotality public records + Voluntary Lien + Owner Link.

---

## Beat 2 — Geography drill · 60 seconds

**Route:** `/` (map component) → pan across the six states → click **Illinois** → drill into Cook County → into Chicago ZIPs.

> "The choropleth is fed from `mip.semantics.lead_generation_metric_view`, aggregated by state and CBSA. Six states light up because that's the real share — IL 1.86M, CA 0.90M, FL 0.76M, TX 0.75M, WA 0.74M, CO 0.16M properties. No synthetic shading."

> "Illinois leads on every dimension that matters here: 1.13M properties with open liens, the **highest average 1st-position rate in the share at 4.75%**, and the broadest cohort mix. That's why Chicago is our anchor metro — we get both stories: the 565K-strong post-2023 cohort at 6–6.7% that needs a refi *right now*, and the **669,320-borrower sub-3% lock-in cohort** from 2020–2022 that won't refi but is wide open for HELOC and cash-out. Drill Cook County, drill a Chicago ZIP — same sub-second query path whether you're national or a single ZIP."

**Cite:** `mip.semantics.lead_generation_metric_view` · Cotality **Voluntary Lien** + **Property Domain** + **Owner Link**; state/cohort counts from `docs/data-sources-gap-analysis.md §1`.

---

## Beat 3 — Portfolio Builder · 45 seconds

**Route:** `/portfolio-builder`. **Clicks:** change one filter in GEO, one in EQUITY; watch KPI grid update; point at the approval-required chip.

> "Six filter dimensions — geography, occupancy, lien characteristics, Owner Link, product fit, equity bands. Every change re-queries `mip.semantics.lead_generation_metric_view`. Chips are parameterized enums, not free text — SQL-injection and prompt-injection off the table before we even talk about agents."

> "Watch the *Generate approval-required outreach* chip — every downstream outreach lands in a human queue. Explicit product promise, not a setting."

**Cite:** `mip.semantics.lead_generation_metric_view` · Cotality **Voluntary Lien + AVM + Property Domain**.

---

## Beat 4 — Segments · 60 seconds

**Route:** `/segment-intelligence`. **Clicks:** click the **In the Money** card; let the ranked table populate; point at the compliance chip.

> "Five shippable segments, each defined by a UC rule — not a vague ML cluster. **In the Money** fires when `fn_in_the_money(rate_spread_bps, equity_pct, 75, 15)` is true: lien rate ≥ 75 bps above par AND equity ≥ 15%. Counts come straight from `mip.gold.segment_population` with a state-level breakdown."

> "**Retention / Recapture** is the sleeper — 263K borrowers where the current servicer ≠ the originator. **Investor / Multi-Property** — 833K corporate owners + 1.80M absentee mailings. **Home Equity** — anchored on the 669,320-borrower sub-3% lock-in cohort materialized in `mip.gold.lockin_cohort`; they won't refi, but their equity is shoppable."

> "Two segments are honestly stubbed. **Listed for Sale** needs MLS data; on the Cotality roadmap, shows zero today. **Permit Activity** — needs the Permits product. We show the card, we tell you it's blocked, we do not fake the count. Top-right: *PII suppressed · CLIP-MCP drill-down*. Real owner names never cross the gold boundary."

**Cite:** `mip.gold.segment_population` · `mip.gold.fn_in_the_money` · Cotality **Voluntary Lien + Property Domain + Owner Link + Mortgage Domain**; cohort sizes from gap-analysis §1.

---

## Beat 5 — Lead Queue + Borrower 360 · 90 seconds

**Route:** `/lead-queue` → expand top row → `/borrower-360/B-48291`.

> "Ranked-borrower table, sorted by opportunity score against the live top-N in `mip.gold.lead_population`. Top row is our canonical example — borrower ID **B-48291**, Chicago 60611, opportunity score **94**, segments *In the Money + Home Equity*. Score decomposes into five weighted components — economic incentive 0.35, intent trigger 0.30, fit 0.15, relationship 0.10, evidence 0.10. That's `fn_lead_score`, golden-fixture tested, banker's-rounding pinned."

> *(Click through to Borrower 360.)*

> "Borrower 360: AVM **$625K**, lien **$340K**, equity **$285K**, LTV **54%**, rate 5.75%. CLIP and Owner Link are live fields from `mip.silver.property_master` — Compliance can join back to the share row. This page used to fan out into two serialized warehouse queries; it now reads a single row from `mip.gold.borrower_dossier`, a pre-join CTAS (borrower_360 × top-20 evidence events per CLIP, 5.16M rows) that dropped the dossier p95 from 3,300 ms to 1,200 ms."

> "Why panel — the explainability surface. **+88 bps spread** (par comes from the weekly FRED MORTGAGE30US pull into `mip.silver.market_rates_weekly`, not a magic constant), **46% equity**. Both clear threshold, In the Money is true. Source chips: `fn_rate_spread`, `fn_in_the_money`. The Python mirror in `backend/services/scoring.py` is pinned to the UDFs by golden JSON — drift breaks CI."

> "Trigger timeline — voluntary lien update, AVM refresh, local refi-activity signal. Every event has a Cotality source and an ISO timestamp. Permit and listing events are stubbed until Cotality ships those products."

**Cite:** `mip.gold.fn_lead_score` · `mip.gold.fn_rate_spread` · `mip.gold.fn_in_the_money` · `mip.gold.borrower_dossier` (pre-join CTAS over `borrower_360` + top-20 `evidence_events`) · `mip.silver.market_rates_weekly` · Cotality **Voluntary Lien + AVM + Mortgage Domain**.

---

## Beat 6 — Offer Orchestrator + Human Approval · 90 seconds

**Route:** click *Build outreach draft* → `/offer-orchestrator/B-48291`. **Clicks:** review primary, alternatives, thresholds, then *Approve outreach*.

> "Primary offer: **Refinance + HELOC**. Score **94**. Rationale is deterministic — *+88 bps spread clears the 75 bps floor AND 46% equity clears the 35% HELOC cushion, so branch 2 of `fn_next_best_offer` fires: refi plus HELOC cross-sell.* One outreach, two products."

> "**Considered Alternatives** — *Refi alone* ruled out, equity clears HELOC; *Pure HELOC* ruled out, rate economics qualify; *Cash-out* dominated by both. Eight branches total — purchase, refi+HELOC, HELOC, refi, cash-out, investor, retention, nurture — and every borrower lands in exactly one."

> "**Thresholds Applied** — if your growth lead raises the HELOC floor from 35% to 50%, this borrower routes to *Refinance alone*. Golden-fixture case 15 proves that shift on the SQL side. Admin-tunable, no SQL deploy."

> *(Click Approve.)*

> "Green chip flips, audit event ID surfaces. Row just landed in `mip_app.action_audit` in Lakebase — actor, action, entity, evidence ids, timestamp, request id. Compliance asks three months from now, the answer is one SQL query."

**Cite:** `mip.gold.fn_next_best_offer` · `mip.gold.borrower_360.recommended_offer` · `mip_app.action_audit` · Lakebase.

---

## Beat 7 — Ask Genie · 60 seconds

**Action:** click the floating Genie FAB (bottom-right, persistent across every route). Ask a canonical question from `genie/sample_questions.md`.

> "Genie is one click from every page. Our space is grounded on `mip.semantics.*` metric views — not raw gold, not arbitrary SQL. Config, trusted-asset list, sample questions all live in `genie/` in the repo."

> *(Ask sample question 5 from [genie/sample_questions.md](../genie/sample_questions.md): "How big is the 2020–2022 sub-3% lock-in cohort across all six states?")*

> "Structured response: **669,320 borrowers**, per-state table across IL / CA / FL / TX / WA / CO, trusted-asset chip pointing at `mip.gold.lockin_cohort` — a purpose-built gold table added this slice so the single hottest retention question answers from a pre-materialized rollup instead of scanning silver. Independent raw-share reference query came back Δ=0 against that table. That's the HELOC and cash-out pool the lender will *never* win back on rate alone — won't refi, but equity is shoppable."

> "If Genie cold-starts mid-session, the circuit breaker trips and we fall through to a deterministic safe corpus of 10 canonical answers pinned to the sample questions. Audience sees a correct answer with a provenance chip — not a spinner, not a hallucination."

**Cite:** `mip.gold.lockin_cohort` · `mip.semantics.borrower_opportunity_metric_view` · `mip.gold.evidence_events` · Mortgage Lead Intelligence Genie Space (trusted-asset-scoped) · `backend/services/genie_answers.py` safe-corpus fallback.

---

## Beat 8 — Module 1–4 forward-look · 30 seconds

**Route:** back to `/` — scroll to the *Future modules* row.

> "Module 0 is the spine. M1 Pipeline Optimization, M2 LO Workbench, M3 Underwriting Copilot, M4 Risk & Retention — same Cotality primitives, same UC governance, same Lakebase audit, same human-approval anchor. Build the governance muscle once, extend it four times."

---

## Close — 30 seconds

> "Three asks."

> "**One.** A partner technical review with Databricks FS and Cotality on two specific data products: **MLS Listings** and **Building Permits**. Those unlock the Listed-for-Sale segment and upgrade the HELOC segment from equity-only to intent-driven. Everything else is already in the share."

> "**Two.** Two or three design-partner lenders for a thirty-day pilot on the six-state footprint. We'll bring the architecture; you bring the book."

> "**Three.** Come find us for the deep dive — we'll walk the bundle, the metric views, and the Lakebase schema end-to-end."

> "Who should we contact, why now, with what offer. Thank you."

---

## Backup path — "if something breaks"

| Failure | What happens | What the presenter says |
|---|---|---|
| Warehouse cold-starts mid-session | DegradedBanner appears at top of page; the retry/breaker logic re-arms within 30s | *You'll notice the banner — the warehouse is warming up. This is real-time honesty, not a stage trick. Back in a moment.* |
| Genie API times out | Circuit breaker opens; `/api/genie` falls through to the safe corpus in `backend/services/genie_answers.py`; answer still cites a UC table | *Our safe corpus answered this one — ten canonical questions deterministic, even if the Genie space is cold. The provenance chip is real.* |
| Lakebase unreachable on Approve | Approval banner shows an error toast; the breaker opens; no row written; borrower stays `pending` | *Approval didn't write — the breaker just told us Lakebase is unreachable. That's the audit guarantee doing its job: we'd rather fail visibly than fake success.* |
| Map tile fails to load | Skip the drill-down, go directly from KPI row to `/segment-intelligence` — story still holds | *Let's go straight to segments — that's where the action is anyway.* |
| Frontend refuses to start | `curl /api/health` then `/api/leads` and `/api/borrowers/B-48291` — endpoints pre-loaded on the second monitor | *Let me show the API directly — the UI is the skin, not the substance.* |

---

## Appendix — canonical click path (muscle memory)

1. Run the [rehearsal checklist](module0-rehearsal-checklist.md) — confirm `/api/health` is fully green.
2. Open `/` — verify KPI row animates; glance at the telemetry strip in the Console footer.
3. Hover *In-the-money* KPI — evidence chip renders.
4. Click **Illinois** on the map → Cook County → a Chicago ZIP.
5. Nav → **Portfolio Builder** (`/portfolio-builder`). Toggle one GEO filter, one EQUITY filter.
6. Nav → **Segment Intelligence** (`/segment-intelligence`). Click **In the Money** card.
7. Nav → **Lead Queue** (`/lead-queue`). Expand row 1 (B-48291).
8. Click into **Borrower 360** (`/borrower-360/B-48291`). Scroll to Why panel.
9. Click *Build outreach draft* → **Offer Orchestrator** (`/offer-orchestrator/B-48291`).
10. Review Primary → Alternatives → Thresholds. Click **Approve outreach**.
11. Click **Genie FAB** (bottom-right). Ask *"How big is the 2020–2022 sub-3% lock-in cohort across all six states?"* — expect **669,320** from `mip.gold.lockin_cohort`.
12. Nav → **Home** (`/`). Scroll to Future Modules row.
13. Close.

---

## Appendix — key numbers to remember

All share-level numbers trace to `docs/data-sources-gap-analysis.md §1` (Apr 2026 point-in-time probe against `cotality_mortgage_data.corelogic`). Borrower-level numbers trace to `mip.gold.*` live queries + golden fixtures. Perf numbers trace to [docs/load-baseline.md](load-baseline.md) (2026-04-22 warm-UC run against the deployed app).

| Number | Where it comes from |
|---|---|
| **5.16M** | Total property snapshots in the share across IL/CA/FL/TX/WA/CO |
| **3.1M** | Properties with at least one open lien (marketable universe) |
| **6 states** | IL 1.86M · CA 0.90M · FL 0.76M · TX 0.75M · WA 0.74M · CO 0.16M |
| **4.75%** | Avg IL 1st-position rate — the highest in the share, anchors the Chicago story |
| **565K** | 2023–2026 cohort at 6.0–6.7% (active in-the-money refi pool) |
| **669,320** | Sub-3% lock-in cohort (2020–2022 originations under 3%), materialized as `mip.gold.lockin_cohort`; answer to Genie sample question 5. This is the slice of the 2020–2022 origination window that won't rate-and-term refi (see gap-analysis §1 for the full origination-window breakdown). |
| **263K** | Servicer-transferred loans (recapture universe) |
| **833K** | Corporate-owned properties (investor segment input) |
| **1.80M** | Absentee mailings, mailing-state ≠ situs-state (investor signal) |
| **+88 bps** | B-48291 rate spread (5.75% − market par from FRED MORTGAGE30US), `fn_rate_spread` |
| **46% equity** | B-48291 AVM $625K minus lien $340K, clears 35% HELOC cushion |
| **Score 94** | B-48291 `fn_lead_score` output, golden case_03 |
| **+188 bps** | B-48294 David Park spread — refi+HELOC label shift case_12 |
| **162 bps** | B-48295 Lisa Thompson spread — banker's-rounding pin case_05 |
| **8 NBO branches** | purchase → refi+HELOC → HELOC → refi → cash-out → investor → retention → nurture |
| **4 UC SQL UDFs** | `fn_rate_spread`, `fn_in_the_money`, `fn_lead_score`, `fn_next_best_offer` |
| **3 metric views** | `lead_generation_metric_view`, `segment_performance_metric_view`, `borrower_opportunity_metric_view` |
| **10 Genie sample questions** | `genie/sample_questions.md` — drives space suggestions + safe-corpus fallback |
| **5/5 p95 under threshold** | `/api/health` 130 ms · `/api/portfolio/preview` 110 ms · `/api/segments` 190 ms · `/api/leads` 1,300 ms · `/api/borrowers/{id}` 1,200 ms. Aggregate 7.93 req/s, 0 failures on 714 requests. From [docs/load-baseline.md](load-baseline.md). |

---

## Honest-to-the-audience callouts

These are the lines that separate this from a slideware session. Use them when you're asked "is this real?" — which you will be.

- "Zero mock mode in the running app. This is live Unity Catalog. The safe corpus for Genie is a fallback catalog of 10 canonical answers that activates only when the Genie circuit breaker opens."
- "Two segments — Listed for Sale and Permit Activity — return zero on real data today because Cotality hasn't licensed MLS and Permits to us yet. We show the card, we don't fake the count."
- "All names, all borrower IDs on screen are synthetic. The CLIP and Owner Link identifiers are real but non-identifying — they're mastered IDs, not PII."
- "The $2.18 cost per contact and 9.7% projected contact-to-app are admin-config values, not gold table outputs. We label those explicitly in the evidence chip."

---

*End of talk track. Dress rehearsal: read aloud twice, walk the click path once, run the rehearsal checklist end-to-end — then present.*
