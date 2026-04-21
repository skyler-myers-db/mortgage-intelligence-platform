# Module 0 — DAIS 2026 Booth Talk Track

- **Title:** Mortgage Intelligence Platform — Module 0: Top-of-Funnel Lead Generation & Borrower Segmentation
- **Audience:** Business (Head of Growth / VP Mortgage Lending / Marketing / Sales Mgmt) + Technical (Databricks FS partner, Cotality product/data, Entrada delivery)
- **Runtime:** 45s open + 6–8 min main + 30s close (~8 min wall clock)
- **Presenters:** Entrada delivery lead + Databricks FS partner (co-drive)
- **Date / Room:** DAIS 2026 — Entrada booth
- **App URL:** `http://localhost:5173` at 1440×900, dark theme, compact density
- **Backend:** `http://localhost:8000/api/health` (mock mode)

---

## Pre-demo setup checklist

- `uvicorn backend.main:app --reload --port 8000` — confirm `GET /api/health` returns `{"status":"ok"}`.
- `npm --prefix frontend run dev` — confirm `http://localhost:5173` renders the Home hero.
- Browser at **1440×900**, zoom 100%, dark theme, compact density (`[data-theme="dark"][data-density="compact"]`).
- Theme toggle tested once (prove light mode still ships clean).
- Floating Genie FAB visible on `/` bottom-right; open + close once.
- `MIP_MOCK_MODE=true` in the shell env — booth posture is mock-first.
- Second browser tab pre-loaded on `/borrower-360/B-48291` as a hot backup.
- Phone hotspot ready; WiFi is not on the demo path.

---

## Opening — 45 seconds

> "One question, one module. **Who should we contact, why now, and with what offer?**"

> "This is the Mortgage Intelligence Platform on Databricks Apps. Module 0 is top-of-funnel: building a marketable population out of Cotality's public records, voluntary liens, listings, building permits, AVM, mortgage market analytics, and Owner Link / CLIP identity graph — then ranking, explaining, recommending, and routing every lead to human approval before a single outreach goes out."

> "Three principles hold through every screen. *Every recommendation traces to a Cotality source through Unity Catalog. Every score has a rationale. Nothing is sent without human approval, and every approval writes to an immutable audit log.* That's the whole contract."

> "Eight routes. Six minutes. Let's go."

---

## Beat 1 — Home · 60 seconds

**Route:** `/` **Clicks:** land on Home; hover each KPI to surface the evidence chip; click the audit tile.

> "This is Summit Mortgage's top-of-funnel snapshot. **89,553** marketable borrowers, **12,840** currently in the money, **$2.18** cost per contact trending down, **9.7%** projected contact-to-app."

> "Every KPI has an evidence chip. The population number cites `mip_demo.gold.lead_population`, built from Cotality public records + Owner Link. The 12,840 cites `mip_demo.gold.fn_in_the_money` — a real Unity Catalog SQL function, golden-fixture pinned against the Python implementation. The $2.18 cites admin config. Nothing here is a magic number."

> "Right rail is the *Console* — agent activity log, portfolio filters, approval queue. Floating bottom-right is **Genie**, one click from every page. That's the shell."

> "Three years of audit events land in Lakebase, streamable to Unity Catalog for compliance. Let's narrow to where the opportunity actually lives."

**Cite:** `mip_demo.gold.lead_population` · `mip_demo.gold.fn_in_the_money` · Cotality public records + Owner Link.

---

## Beat 2 — Map drill · 45 seconds

**Route:** `/` (map component) → click **Georgia**. **Clicks:** GA → Fulton County → ZIP 30309.

> "The choropleth is fed from `mip_demo.semantics.lead_generation_metric_view`. Fill intensity joins **CLIP** (Cotality's mastered property id) to **Owner Link** (the mastered owner/entity id), against current voluntary lien, AVM, and recent permits."

> "Georgia lights up. Drill Fulton, drill 30309 — that's Atlanta's Midtown corridor, roughly 1,420 in-the-money borrowers in a single ZIP. Same drill works across all fifty states when the Cotality Delta Share is live; for the booth we're running precomputed gold tables so the map opens instantly."

**Cite:** `mip_demo.semantics.lead_generation_metric_view` · Cotality **CLIP + Owner Link** + voluntary lien + AVM + permits.

---

## Beat 3 — Portfolio Builder · 45 seconds

**Route:** `/portfolio-builder`. **Clicks:** change one filter in GEO, one in EQUITY; watch KPI grid update; point at the approval-required chip.

> "Six filter dimensions — geography, occupancy, lien characteristics, Owner Link relationships, product fit, equity bands. Every change re-queries `mip_demo.semantics.lead_generation_metric_view` through a serverless Databricks SQL Warehouse. The filter chips are parameterized enums, not free text — which is how we keep SQL-injection and prompt-injection off the table."

> "Watch the *Generate approval-required outreach* chip. The filters shape the population; the chip anchors the governance story. Every downstream outreach lands in a human queue, and that's an explicit product promise, not a setting."

**Cite:** `mip_demo.semantics.lead_generation_metric_view` · Cotality **Customer 360** + Voluntary Lien + AVM.

---

## Beat 4 — Segments · 60 seconds

**Route:** `/segment-intelligence`. **Clicks:** click the **In the Money** card; let the ranked table populate; point at the compliance chip.

> "Six mortgage-specific segments, each defined by a Unity Catalog rule — not a vague ML cluster. In the Money fires when `fn_in_the_money(rate_spread_bps, equity_pct, 75, 15)` is true: lien rate **at least 75 bps above par AND equity at least 15%**. Today that's 12,840 borrowers, average score 82. Golden fixtures pin the inclusive `>=` on both thresholds — the same SQL function the map just used."

> "Permit Activity — 4,108. Listed for Sale — 2,614. Retention Risk — 3,471, highest average score because existing relationships convert best. Home Equity Candidate — 6,320. Investor / Multi-Property — 1,892."

> "Compliance chip, top-right: *PII suppressed · CLIP-MCP on-demand drill-down*. Real borrower names only materialize when a human clicks into a specific record. Before that, everything is CLIP and Owner Link."

**Cite:** `mip_demo.gold.fn_in_the_money` · `mip_demo.gold.lead_segment_membership` · Cotality **Voluntary Lien + AVM**.

---

## Beat 5 — Lead Queue + Borrower 360 · 90 seconds

**Route:** `/lead-queue` → expand top row → `/borrower-360/B-48291`.

> "Ranked-borrower table. Top row is **James & Maria Rodriguez**, borrower ID **B-48291**, Atlanta 30309, opportunity score **94**, segments *In the Money + Home Equity*. Score decomposes into five weighted components — economic incentive 0.35, intent trigger 0.30, fit 0.15, relationship 0.10, evidence 0.10. That's `fn_lead_score`, golden-fixture tested, banker's-rounding pinned."

> *(Click through to Borrower 360.)*

> "Customer 360: **CLIP** `clip_demo_48291`, **Owner Link** `ol_demo_48291`, AVM **$625K**, current lien **$340K**, equity **$285K**, LTV **54%**, current rate 5.75%."

> "Why panel — this is the explainability surface. **+88 bps spread above par** (our market par is 4.875%), **46% equity**. Both clear their thresholds, so In the Money is true. Source chips: `fn_rate_spread`, `fn_in_the_money`. Both are real UC SQL functions — the Python implementation in `backend/services/scoring.py` is pinned to them by golden-fixture JSON, so a drift between the two breaks CI."

> "Trigger timeline below — voluntary lien update, AVM refresh, local refi-activity signal. Every event has a Cotality source table and an ISO timestamp."

**Cite:** `mip_demo.gold.fn_lead_score` · `mip_demo.gold.fn_rate_spread` · `mip_demo.gold.fn_in_the_money` · `mip_demo.gold.borrower_360` · Cotality **Voluntary Lien + AVM + Mortgage Market Analytics**.

---

## Beat 6 — Offer Orchestrator + Human Approval · 90 seconds

**Route:** click *Build outreach draft* → `/offer-orchestrator/B-48291`. **Clicks:** review primary, alternatives, thresholds, then *Approve outreach*.

> "Primary offer: **Refinance + HELOC**. Score **94**. Rationale is deterministic — *+88 bps spread clears the 75 bps floor AND 46% equity clears the 35% HELOC cushion, so branch 2 of `fn_next_best_offer` fires: refi plus HELOC cross-sell.* One outreach, two products."

> "**Considered Alternatives** card. *Refi alone* — ruled out because equity crosses the HELOC bar; pure HELOC would leave refi revenue on the table. *Pure HELOC* — ruled out because rate economics also qualify. *Cash-out* — ruled out, dominated by both. The decision tree has eight branches total — purchase, refi+HELOC, HELOC, refi, cash-out, investor, retention, nurture — and every borrower lands in exactly one."

> "**Thresholds Applied** card. If your growth lead raised the HELOC equity floor from 35% to 50%, Rodriguez would route to *Refinance alone* — and golden-fixture case 15 proves that shift on the SQL side. **These knobs are admin-tunable without a SQL deploy.**"

> *(Click Approve.)*

> "Green chip flips, audit event ID surfaces. That just wrote an immutable row to `mip_app.action_audit` in Lakebase — actor, action, entity id, evidence ids, timestamp, request id. If Compliance asks us to show our work three months from now, the answer is one SQL query."

**Cite:** `mip_demo.gold.fn_next_best_offer` · `mip_demo.gold.recommended_offers` · `mip_app.action_audit` · Lakebase.

---

## Beat 7 — Ask Genie · 45 seconds

**Action:** click the floating Genie FAB (bottom-right, persistent across every route).

> "Genie is one click from every page, not buried behind a route. Let's ask — *How many HELOC candidates with strong permits and equity?*"

> *(Response renders.)*

> "Structured response: big metric **4,108**, top ZIPs table, trusted-asset chips — `mip_demo.gold.borrower_360`, `mip_demo.gold.evidence_events`. Follow-up chips on the right: *What if we raised the HELOC equity floor to 50%?*"

> *(Click the follow-up chip.)*

> "Round-trip. **~1,640** borrowers remain, average score lifts from 71 to 78 — a precision/recall trade, quantified in real time. Genie is grounded on the curated metric views only; every answer cites the table. If the Genie API is unavailable, a deterministic catalog answers the sixteen canonical questions without a visible hiccup."

**Cite:** `mip_demo.semantics.borrower_opportunity_metric_view` · `mip_demo.gold.evidence_events` · Genie Space (trusted-asset-scoped).

---

## Beat 8 — Module 1–4 forward-look · 30 seconds

**Route:** back to `/` — scroll to the *Future modules* row.

> "Module 0 is the spine. **M1 Pipeline Optimization** — lead-to-app throughput and stalls. **M2 LO Workbench** — officer assist with explainable next-best-action. **M3 Underwriting Copilot** — condition handling and exception triage. **M4 Risk & Retention** — portfolio-level retention and recapture."

> "Same Cotality primitives, same UC governance, same Lakebase audit, same human-approval anchor. Build the governance muscle once, extend it four times."

---

## Close — 30 seconds

> "Three asks."

> "**One.** A partner technical review with Databricks FS and Cotality on the Genie space and our Agent Bricks / MCP roadmap — property intelligence, segment analyst, offer strategy, outreach writer, supervisor — all behind the same approval gate."

> "**Two.** Two or three design-partner lenders for a thirty-day pilot on synthetic data. We'll bring the architecture; you bring the book."

> "**Three.** Come by the Entrada booth for the deep dive — we'll walk the bundle, the metric views, and the Lakebase schema end-to-end."

> "Who should we contact, why now, with what offer. Thank you."

---

## Backup path — "if something breaks"

| Failure | Fallback | What the presenter says |
|---|---|---|
| Internet dies mid-demo | Mock mode is the primary path — zero change | *No change — this demo runs on precomputed gold tables locally by design.* |
| Genie API unreachable / slow | Deterministic catalog in `backend/services/genie_answers.py` answers 16 canonical intents | *We ship Genie with a deterministic fallback for booth reliability. The audience cannot tell.* |
| Map tile fails to load | Skip map beat, go directly from KPI row to `/segment-intelligence` — story still holds | *Let's go straight to the segments — that's where the action is anyway.* |
| Offer Orchestrator stalls | `/borrower-360/B-48291` still shows Why panel, thresholds, evidence; audit can be written from the API | *The Why panel is where the economics live. Approval is one API call.* |
| Frontend refuses to start | `curl http://localhost:8000/api/health` then `curl .../api/leads` and `.../api/borrowers/B-48291` — have endpoints ready on a second monitor | *Let me show the API directly — the UI is the skin, not the substance.* |
| Audio cuts out | Continue visual demo, then recap verbally from Beat 6 | *(silent progression)* |

---

## Appendix — canonical click path (muscle memory)

1. Open `http://localhost:5173/` — verify KPI row animates.
2. Hover *High-intent leads* KPI — evidence chip renders.
3. Click **Georgia** on the map → Fulton → 30309.
4. Nav → **Portfolio Builder** (`/portfolio-builder`). Toggle one GEO filter, one EQUITY filter.
5. Nav → **Segment Intelligence** (`/segment-intelligence`). Click **In the Money** card.
6. Nav → **Lead Queue** (`/lead-queue`). Expand row 1 (B-48291).
7. Click into **Borrower 360** (`/borrower-360/B-48291`). Scroll to Why panel.
8. Click *Build outreach draft* → **Offer Orchestrator** (`/offer-orchestrator/B-48291`).
9. Review Primary → Alternatives → Thresholds. Click **Approve outreach**.
10. Click **Genie FAB** (bottom-right). Ask *HELOC candidates with strong permits and equity*. Click first follow-up chip.
11. Nav → **Home** (`/`). Scroll to Future Modules row.
12. Close.

---

## Appendix — key numbers to remember

| Number | Where it comes from |
|---|---|
| **89,553** | `mip_demo.gold.lead_population` — marketable population |
| **12,840** | In the Money count — `fn_in_the_money` over gold |
| **$2.18** | Cost per contact — trending from $2.71 → $2.18 |
| **9.7%** | Projected contact → application rate |
| **+88 bps** | B-48291 rate spread (0.0575 − 0.04875), `fn_rate_spread` |
| **46% equity** | B-48291 AVM $625K minus lien $340K, clears 35% HELOC cushion |
| **Score 94** | B-48291 `fn_lead_score` output, golden case_03 |
| **+188 bps** | B-48294 David Park spread — refi+HELOC label shift case_12 |
| **162 bps** | B-48295 Lisa Thompson spread — banker's-rounding pin case_05 |
| **23 borrowers** | synthetic ranked sample spanning every NBO branch |
| **8 states** | GA, TX, CA, WA, CO, TN, IL, FL |
| **8 NBO branches** | purchase → refi+HELOC → HELOC → refi → cash-out → investor → retention → nurture |
| **10 Python test modules** | scoring, rate spread, in-the-money, next-best-offer, offer rules, API routes, evidence, Genie, config, Genie provisioning |
| **1 Playwright e2e** | `tests/e2e/module0.spec.ts` — full booth click path |
| **4 UC SQL UDFs** | `fn_rate_spread`, `fn_in_the_money`, `fn_lead_score`, `fn_next_best_offer` |
| **3 metric views** | `lead_generation_metric_view`, `segment_performance_metric_view`, `borrower_opportunity_metric_view` |

---

*End of talk track. Dress rehearsal: read aloud twice, click path once, backup table reviewed — then commit.*
