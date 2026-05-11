# Module 0 — End-to-end audit, 2026-05-09

> **Internal validation artifact — not public release collateral.** This document includes workspace-specific deployment details, authenticated actor/header observations, and sandbox audit byproducts. Use it to track remediation and release gates; do not treat it as customer-facing proof or final public-release signoff.

**Auditor:** Claude (Cowork)
**Scope:** Live Databricks App at `https://mip-app-2543889327043640.aws.databricksapps.com`, the codebase at `/Users/entrada-mac/repos/mortgage-intelligence-platform`, and the underlying Unity Catalog (`mip.*`) + Lakebase (`mip_app_state`) data plane.
**Reference:** Module 0 Top-of-Funnel Lead Generation & Borrower Segmentation spec (uploaded `.docx`).
**Method:** Direct SQL via Statement Execution API on warehouse `81d08d4fa2d799e9`; live `/api/*` calls with the user's workspace OAuth token; live Genie space invocation (`01f13d4968af1b249dc388fd5b18b195`); visual screenshots of every route at 1373×898 (Chrome read-only).

---

## TL;DR

The product is in good shape for internal/partner demo rehearsal. Home, Portfolio Builder, Segment Intelligence, Lead Queue, Borrower 360, Offer Orchestrator, Ask Genie, and Admin all render real data sourced from `mip.gold.*`, every KPI on Home matches direct SQL to the exact value, the audit ledger is being written correctly with proper edge-auth identity attribution, and Genie answers are grounded in the trusted gold tables. Resilience hooks (warm-start, circuit breakers, retry, degraded banner) are in place and the app reports `mode: live` with all three breakers closed.

This is not unconditional ship/public-release signoff. Remaining release gates are explicit: the Databricks Apps edge-header policy decision, Cotality MLS/Listings and Building Permits feeds, and governance approval for retaining versus deleting sandbox audit byproducts.

This file records the original audit findings, the remediation status the engineering team reported, and an independent live re-validation Claude ran against the deployed Databricks App snapshot `01f14beaf1f01b4e95a5825ce5ebe3e6` (active deployment 2026-05-09T21:10:34Z, creator skyler@entrada.ai).

## Codex re-review addendum (2026-05-09 22:30-23:00 UTC)

Codex independently re-checked the pasted validation against the deployed app and source. The substantive fixes still hold: borrower truth flags match gold (`flag_mismatches=0`), lifecycle gold has no orphan borrowers (`orphan_rows=0`), unknown borrower approvals return 404, admin market rate is 0.0637 from the operating gold surface, `county_fips` is the canonical ZIP-rollup parameter, and `/api/workspace` hydrates saved leads without null required fields.

Adjustments made during this re-review:
- Corrected the audit's P2-1 live lead order to match the current `rank_overall` order: `B-102FL7THC6Q3L`, `B-1AT5CXZZ1NI2N`, `B-0FSL4B96HG6V4`, `B-0YVQH0ZJ6WDSQ`, `B-1ENPEPL260AU8`.
- Cleared the active stale saved draft through `DELETE /api/workspace/drafts/B-102FL7THC6Q3L?channel=email`; `/api/workspace` now returns `saved_drafts=0` and no `ALPHA BRAVO CHARLIE` draft.
- Marked this document as an internal validation artifact rather than public-release collateral.
- Tightened the live Playwright gate so it tolerates real Databricks latency, avoids loading-state selectors, and can refresh the local Databricks OAuth bearer between grouped runs.

Validation from this pass:
- `./.venv/bin/python -m pytest tests/unit/test_outreach_reject.py tests/integration/test_borrower_dossier_parity.py tests/unit/test_admin_rules.py tests/unit/test_workspace_api.py tests/unit/test_workspace_store_contract.py tests/unit/test_lifecycle_sync_contract.py tests/unit/test_public_api_schema_guards.py -q` passed (`55 passed, 3 skipped`).
- `npm --prefix frontend run test -- --run src/components/mortgage/BorrowerTruthFlags.test.ts src/lib/api.test.ts src/lib/drawerSources.test.ts src/components/mortgage/GenieAnswer.test.tsx` passed (`45 passed`).
- `scripts/e2e_live.sh` passed the live Playwright real-data suite against `https://mip-app-2543889327043640.aws.databricksapps.com` (`21 passed, 1 skipped`; the forced-503 degraded-banner case remains skipped without `MIP_FORCE_DEGRADED_TOKEN`).
- `git diff --check` and `bash -n scripts/e2e_live.sh` passed.

Independent review lanes returned PASS for data/API fixes, P1/P2 behavior, stale draft cleanup, audit-doc boundary, and audit pollution handling. The only remaining BLOCK is the Databricks Apps edge headers (`gap-auth`, `x-databricks-internal-pod-ip`) on authenticated responses; public/customer release still needs a Databricks platform exception or a header-stripping front door.

## Remediation status after Codex pass

| Finding | Engineering status | Independent live re-validation (2026-05-09 21:52 UTC) |
|---|---|---|
| P0-1 Borrower 360 missing boolean flags | Fixed in source; `_BORROWER_360_COLUMNS` now projects the Module 0 flags and tests assert schema/projection parity. | ✅ **Verified live.** `/api/borrowers/B-102FL7THC6Q3L` returns `is_investor=true, is_competitor_lien=true`; `/api/borrowers/B-0YVQH0ZJ6WDSQ` returns `is_investor=true, is_owner_occupied=true, is_former_customer=true, second_pos_amount=30300`; `/api/borrowers/B-1ENPEPL260AU8` returns `is_investor=true, is_owner_occupied=true`. All match `gold.borrower_dossier` exactly. SQL anti-join `WHERE d.is_investor != b.is_investor` returns 0. New "Relationship flags" / "Borrower flags" pill row visible on Borrower 360 and Offer pages. |
| P0-2 Approve/reject arbitrary borrower IDs | Fixed in source; both endpoints validate `repo.find_borrower(...)` before Lakebase writes and reject `request_id` reuse across actor/borrower/action. | ✅ **Verified live.** POST `/api/outreach/approve` and `/api/outreach/reject` for `B-DEFINITELY-NOT-REAL-ZZZ` both return `HTTP 404 {"detail":"Borrower B-DEFINITELY-NOT-REAL-ZZZ not found"}`. Re-using a `request_id` against a *different* borrower returns `HTTP 409 {"detail":"request_id already belongs to a different outreach decision"}`. Same `(borrower_id, request_id)` retry still returns the same `approval_id` with an empty `audit_event_id` (idempotent fast-path preserved). |
| P1-1 Stale custom draft | UI now has a reset-to-template control; the stale demo draft was cleared through the governed workspace draft API. | ✅ **Verified live.** Offer page shows a "Reset draft" button when a saved draft exists. `DELETE /api/workspace/drafts/B-102FL7THC6Q3L?channel=email` returned 200, and `/api/workspace` now has no saved draft for `B-102FL7THC6Q3L`, so the page falls back to the backend-generated governed template. |
| P1-2 Admin stale market rate | Fixed in source; Admin derives `mip_market_rate` from `mip.gold.borrower_360`, the operating gold surface that already consumed the latest FRED MORTGAGE30US row. | ✅ **Verified live.** `/api/admin/rules` now returns `mip_market_rate.value=0.0637` with description "Operating market rate used by gold rate-spread calculations; sourced from FRED MORTGAGE30US during gold refresh." Rules version bumped from `itm_77eddaa7d767` → `itm_4df231d5472f`, `last_updated=2026-05-09T21:11:33Z`. |
| P1-4 Stale saved leads | Fixed in source; `/api/workspace` hydrates from current lead data and omits unresolved borrower IDs. | ✅ **Verified live.** All 10 saved leads now have populated `city`, `state`, `zip`, `recommended_offer`, `opportunity_score`, `confidence`. The previously-null nine entries (`B-1LHO0JXLPYY15`, `B-1MSREXE8N8R8B`, `B-1QEAC5TYTUY1H`, `B-1KWV90MCQ6X9K`, `B-04FBCGC9EAYYE`, `B-0G1SUHI7QW608`, `B-1FPOPS3W3IO1Y`, `B-0AIODS229W33W`, `B-075G2XLSGZY9Q`) all hydrate to real Illinois borrowers with scores in 82–88. |
| P2-1 Lead tie-break order | Fixed in source; lead queries order by `rank_overall`. | ✅ **Verified live.** First five rows of `/api/leads?limit=5` are now `B-102FL7THC6Q3L`, `B-1AT5CXZZ1NI2N`, `B-0FSL4B96HG6V4`, `B-0YVQH0ZJ6WDSQ`, `B-1ENPEPL260AU8`, matching `SELECT borrower_id FROM mip.gold.lead_population ORDER BY rank_overall ASC, borrower_id ASC LIMIT 5`. |
| P2-5 ZIP parameter mismatch | Fixed in source; canonical query parameter is `county_fips`. | ✅ **Verified live.** `GET /api/geo/zip-rollups?county_fips=17031` → 200 with 25+ ZIPs of Cook County rollup. `GET /api/geo/zip-rollups?fips=17031` and `GET /api/geo/zip-rollups` (no param) → `HTTP 422 {"detail":[{"loc":["query","county_fips"],"msg":"Field required"}]}`. |
| P2-7 KPI source-chip copy | Fixed in source; chips cite gold precomputed assets rather than implying function execution at page render. | ✅ **Verified live.** Home KPI chips now read `cotality.public_records` (Marketable Population), `UC function - fn_in_the_money` (High-Intent — kept because in-the-money IS computed by the UC function), `mip.gold.lead_scores` (Top-Tier), `mip.gold.borrower_360` (Offers Recommended). The previously misleading `UC function - fn_lead_score` / `fn_next_best_offer` references are gone. Home page also reflects the latest gold refresh: "Refreshed May 9, 5:54 PM EDT". |
| **NEW** Lifecycle gold orphan mirror | New work; lifecycle gold mirror filters orphan Lakebase approval rows. | ✅ **Verified live.** `mip.gold.borrower_lifecycle_state` is now exactly 5,156,184 rows = `mip.gold.borrower_360` count. Anti-join `LEFT JOIN borrower_360 USING(borrower_id) WHERE b.borrower_id IS NULL` returns 0. Test rows matching `B-FUZZ%`, `B-AUDIT%`, `B-TEST%`, `B-DEFINITELY%` count = 0. |
| **NEW** Truth-flag UI on Borrower 360 / Offer | New work; pills surface real flags rather than relying on segment_codes alone. | ✅ **Verified live.** Borrower 360 "Relationship flags" panel renders `Not current customer / Former customer / Competitor lien / Owner occupied / Investor / Listing feed pending / Permit feed pending / Open 2nd lien` for `B-0YVQH0ZJ6WDSQ`, exactly matching the API. The pending-feed pills handle the unavailable Cotality MLS/Permits feeds gracefully without claiming `false`. |

---

## What works (positive findings)

| Area | Verified |
|---|---|
| **Home KPIs** | All 4 KPI cards match SQL to the integer (`5,156,184` / `134,534` / `4,320` / `4,472,648`). |
| **Top-tier definition** | `opportunity_score >= 75` reproduces the 4,320 figure exactly (per `_FUNNEL_BUCKET_SQL` in `databricks_repo.py:248`). |
| **Offers definition** | `recommended_offer_code <> 'nurture'` reproduces 4,472,648 (5,156,184 − 683,536 nurture). |
| **State rollups** | `/api/geo/state-rollups` matches the current live Cotality geography footprint; per-state addressable counts match `borrower_360`. |
| **Genie grounding** | Direct ask "How many borrowers are in the in-the-money segment?" → 134,534 (matches), Genie writes a clean SQL against `mip.gold.borrower_360 WHERE in_the_money = TRUE`. |
| **Genie known-gap handling** | "How many in-the-money borrowers also have a building permit?" → "Cotality MLS/listing and Building Permits feeds are pending… Source: `mip.gold.source_readiness`." It does not silently return 0. |
| **Audit ledger** | Every API call I made (`view_borrower_360`, `view_leads_ranked`, `recommend_offer`, `draft_outreach`, `outreach.approve`, `outreach.reject`) wrote a structured row to `mip_app.audit_events` with the correct `actor=skyler@entrada.ai` (sourced from `gap-auth` / X-Forwarded-Email), evidence_ids, payload_json, subject_clip, and request_id. |
| **Idempotent approvals** | Re-POSTing `/api/outreach/approve` with the same `request_id` returns the **same** `approval_id` and an **empty** `audit_event_id` (no double-write). The partial unique index on `mip_app.approvals.request_id` is doing its job. |
| **Lifecycle mirror** | `mip.gold.borrower_lifecycle_state` carries exactly 5,156,184 rows, matching `mip.gold.borrower_360`; orphan anti-join count is 0 after the lifecycle sync fix. |
| **Resilience telemetry** | `/api/health` reports `dependencies: warehouse=up, lakebase=up, genie=up`, all three circuit breakers `closed`, `breaker_state_changes_last_hour: 0`, `recent_errors_count: 0`. |
| **Trust boundary** | Response headers include `gap-auth: skyler@entrada.ai`; the backend uses `resolve_actor(request)` against `X-Forwarded-Email`, not request body fields, so an attacker can't spoof `payload.actor`. |
| **Source coverage** | 12 of 19 source assets `live`, 5 `demo_synthetic` (first-party Summit feeds), 2 `roadmap` (MLS, Permits — explicitly disclosed). |
| **Evidence richness** | 18,263,605 rows across 11 signal types (`market_trend`, `equity`, `competitor_lien`, `rate_spread`, `multi_property`, `corporate_owner`, `recent_sale`, `absentee_mailing`, `recent_payoff`, `recent_refi`, `foreclosure_stage`). |

---

## Original P0 — Correctness defects found and fixed in source

### P0-1. `/api/borrowers/{id}` returned the wrong `is_investor` / `is_owner_occupied` / `is_current_customer` / `is_competitor_lien` / `is_former_customer` / `has_permit` / `listed_for_sale`

**Status:** fixed, deployed, and verified live after this audit.

At audit time, the Borrower 360 endpoint backed onto `mip.gold.borrower_dossier`, but the projection list `_BORROWER_360_COLUMNS` at `backend/services/repositories/databricks_repo.py:96-104` did **not** include any of those boolean flag columns. The Pydantic `Borrower360` model had those fields declared with default `False`, so when the row dict was unpacked the defaults took over silently.

**Verified mismatch (`B-102FL7THC6Q3L`):**

| Field | dossier (truth) | `/api/borrowers/B-102FL7THC6Q3L` |
|---|---|---|
| `is_investor` | `true` | `false` ❌ |
| `is_owner_occupied` | `false` | `false` ✅ (lucky coincidence) |
| `segment_codes` | `["itm","investor","equity"]` | `["itm","investor","equity"]` ✅ |

**Confirmed for two more borrowers (`B-0YVQH0ZJ6WDSQ`, `B-1ENPEPL260AU8`)** — both show `is_investor=false` from the API while `lead_population.is_investor=true` and `borrower_dossier.is_investor=true`.

`/api/leads` (which had its own projection at `databricks_repo.py:925-926` that selected `is_investor`) returned the correct value, so the bug was contained to the Borrower 360 endpoint.

**Why it matters:** the Offer Orchestrator's "Considered alternatives" section, the Why-now ribbon, and any future is_investor-specific gating (e.g., investor-product offers, retention rules that depend on `is_current_customer`) can read these flags as `false` for every borrower. The page renders correctly today only because it reads segment_codes (which IS projected) for the visible badges.

**Fix:** add `is_owner_occupied, is_investor, is_current_customer, is_former_customer, is_competitor_lien, has_permit, listed_for_sale, second_pos_amount` to `_BORROWER_360_COLUMNS`. Verify the `gold.borrower_dossier` schema has all of them (DESCRIBE confirms `is_owner_occupied`, `is_absentee`, `is_corporate_owner`, `has_permit` exist; need to confirm the rest are present and add to the dossier CTAS if not).

**Repro:**
```bash
curl -s -H "Authorization: Bearer $TOK" \
  https://mip-app-2543889327043640.aws.databricksapps.com/api/borrowers/B-102FL7THC6Q3L | \
  jq '{is_investor, is_owner_occupied, segment_codes}'
# → { "is_investor": false, "is_owner_occupied": false, "segment_codes": ["itm","investor","equity"] }

databricks api post /api/2.0/sql/statements --json '{
  "statement":"SELECT is_investor, is_owner_occupied FROM mip.gold.borrower_dossier WHERE borrower_id='\''B-102FL7THC6Q3L'\''",
  "warehouse_id":"81d08d4fa2d799e9","wait_timeout":"30s"}'
# → [["true","false"]]
```

### P0-2. `/api/outreach/approve` and `/api/outreach/reject` accepted arbitrary `borrower_id` with no existence check

**Status:** fixed, deployed, and verified live after this audit. Both routes now validate borrower existence before Lakebase writes; idempotency now scopes `request_id` reuse to the same actor, borrower, and action, including the atomic insert-conflict path.

At audit time, both POSTs returned 200 OK and wrote a row to `mip_app.approvals` + an audit event for any string passed as `borrower_id`. Confirmed by approving four nonexistent borrowers (`B-AUDIT-PROBE-001`, `B-FUZZ-NORID`, `B-FUZZ-IDEM-1`) and rejecting one (`B-FUZZ-REJECT`). Those rows were audit byproducts from the live test run.

By contrast, the borrower-scoped reads (`/api/borrowers/{id}`, `/api/borrowers/{id}/evidence`, `/api/offers/recommend`, `/api/outreach/draft`) already did `repo.find_borrower(payload.borrower_id)` and 404ed on a missing borrower. The two write endpoints skipped that lookup until the remediation pass.

**Why it matters:** the audit ledger is the governance contract for this product. Letting any string through means an attacker (or a buggy client) can pollute the ledger with phantom approvals that won't reconcile against `borrower_360`. The `lifecycle_sync_job` already filters them out (the test rows from today don't appear in `gold.borrower_lifecycle_state`), but the audit table itself loses integrity.

**Fix:** add a `repo.find_borrower(payload.borrower_id)` lookup at the top of both `approve_outreach` and `reject_outreach` and 404 on miss. Same `OutreachRepository` is already imported in those handlers.

**Repro:**
```bash
curl -s -H "Authorization: Bearer $TOK" -X POST \
  -H 'Content-Type: application/json' \
  -d '{"borrower_id":"B-DOES-NOT-EXIST","offer_code":"OFFER-x","request_id":"'$(uuidgen)'"}' \
  https://mip-app-2543889327043640.aws.databricksapps.com/api/outreach/approve
# → {"approved":true,"approval_id":"...","audit_event_id":"..."}
```

---

## P1 — Accuracy / polish

### P1-1. Stale "Governed Lakebase draft ALPHA BRAVO CHARLIE" persisted for `B-102FL7THC6Q3L`

**Status:** fixed after this audit. Offer Orchestrator now exposes a Reset draft control so users can return to the governed backend template without DB access, and the stale `B-102FL7THC6Q3L` saved draft was cleared through the app API.

The Offer Orchestrator's draft pane reads from saved workspace drafts first and falls back to the auto-generated template. A previous testing session left literally `"Governed Lakebase draft ALPHA BRAVO CHARLIE"` for this borrower, so the remediation added a user-visible reset path and the active saved draft was cleared.

**Why it matters:** the headliner sample borrower (`B-102FL7THC6Q3L` — first row in the lead queue, used in the talk-track) shows test placeholder copy instead of the persona-specific template. A live demo to the Head of Growth lands on this borrower first.

**Fix:** clear the active saved-draft row through `DELETE /api/workspace/drafts/B-102FL7THC6Q3L?channel=email` and keep the "Reset draft" affordance in the Offer page so users can revert to the template without DB access. Also worth a soft check in a future pass: when a saved draft was authored before the latest data refresh, surface a "Draft was saved against an older signal" hint.

### P1-2. Admin market-rate config row was stale and disagreed with the operating market rate

**Status:** fixed in source after this audit. The Admin API now derives the market-rate reference from `mip.gold.borrower_360`, the operating gold surface that already consumed the latest live `mip.silver.market_rates_weekly` `MORTGAGE30US` row, and the seed removes the stale config key.

| Source | Market rate |
|---|---|
| `mip.ref.offer_rules_config` (admin row, exposed by `/api/admin/rules`) | **4.875%** |
| `mip.silver.market_rates_weekly` where `is_latest=true` (FRED MORTGAGE30US 2026-05-04) | **6.37%** |
| `mip.gold.borrower_360.market_rate_fraction` (every row) | **6.37%** |
| Borrower 360 evidence chip ("Market rate comparison") | **6.37%** |

The 4.875% baseline is documented in `sql/uc_functions/fn_rate_spread.sql` as a fictional Module 0 convention used by the Python golden fixtures, but it was embedded as a live row in the offer-rules config table and surfaced in the Admin API at audit time. The function itself takes `market_rate` as a parameter and the gold pipeline correctly threaded 6.37% through, so scoring was right while the admin surface was wrong.

**Why it matters:** Admin/Config is the page where Compliance and Heads of Growth go to verify "what does the system actually use." Showing 4.875% there contradicts the 6.37% that's driving every spread number on every borrower card.

**Fix:** either (a) drop the `mip_market_rate` row from `offer_rules_config` and have the Admin API source it from `silver.market_rates_weekly` `is_latest=true`, or (b) update the seed/refresh job to pin the row to the latest FRED reading on every refresh.

### P1-3. Listings + Permits triggers from the spec are still pending and the gap is wider than the disclosure suggests

Module 0 spec ("Key Capabilities"):
- "Listing trigger overlay: identify homeowners who have listed their property for sale and may be entering the market for a purchase mortgage"
- "Building permit trigger overlay: identify homeowners with recent high-value permits who may be strong HELOC or cash-out refinance candidates"

Today:
- `mip.silver.listings_overlay` and `mip.silver.permits_overlay` **don't exist** (`TABLE_OR_VIEW_NOT_FOUND`).
- `borrower_360.listed_for_sale = false` for **all** 5,156,184 rows.
- `borrower_360.has_permit = false` for **all** 5,156,184 rows.
- `gold.segment_population` for `listed` and `permit` shows `count: 0, avg_score: 0` and a description that explicitly says "Pending Cotality MLS share / Building Permits share; predicates are blocked false until the feed lands."
- The Home page has chips "Cotality MLS/Listings Delta Share is pending" and "Cotality Building Permits Delta Share is pending."

The disclosure is honest, but two of the seven "Key Capabilities" listed in the Module 0 spec are inert in the current product. Combined with first-party CRM/LOS feeds being `demo_synthetic`, the Module 0 promise of seven trigger overlays currently delivers five working overlays plus four Summit-synthetic first-party feeds.

**Why it matters:** spec parity. A buyer reading Module 0 expects all seven to be live, or to see a roadmap with a date.

**Fix:** add the contracted-but-pending Cotality feeds to a roadmap section in `docs/module0-talk-track.md`, with the contract counterparty and an ETA chip; or scope Module 0's MVP to the five live triggers and re-frame Listings/Permits as a near-term extension.

### P1-4. `workspace.saved_leads` carried borrower IDs that no longer resolve

**Status:** fixed in source after this audit. The workspace API now hydrates saved leads from current lead data and omits unresolved borrower IDs rather than rendering null fields.

`/api/workspace` returns ten saved leads from 2026-05-05; nine of them have `city`, `state`, `zip`, `recommended_offer`, `opportunity_score`, and `confidence` all `null`. Only the first one (`B-102FL7THC6Q3L`) is hydratable.

The other nine borrower IDs (`B-1LHO0JXLPYY15`, `B-1MSREXE8N8R8B`, `B-1QEAC5TYTUY1H`, `B-1KWV90MCQ6X9K`, `B-04FBCGC9EAYYE`, `B-0G1SUHI7QW608`, `B-1FPOPS3W3IO1Y`, `B-0AIODS229W33W`, `B-075G2XLSGZY9Q`) appear to be stale references that no longer exist in `gold.borrower_360`, so the LEFT JOIN that hydrates them returns nulls.

**Why it matters:** the Console workspace pane will render saved-lead rows with mostly empty fields, which looks broken even though the underlying mechanism is right.

**Fix:** either (a) prune saved-leads rows whose borrower no longer exists when serving the response (return only resolvable leads), or (b) display a "borrower no longer in current population" badge so the user can clean it up. Option (a) is simpler. Option (b) preserves audit history.

---

## P2 — Items to tighten

### P2-1. Lead-queue tie-break order didn't match `lead_population.rank_overall`

**Status:** fixed in source after this audit. Lead queries now use `rank_overall` as the authoritative order.

`gold.lead_population` ships precomputed `rank_overall` (1, 2, 3, …) where ties at score=86 are broken by some criterion (likely equity_estimate or borrower_id alpha). But `/api/leads` and the rendered Lead Queue table sort by `opportunity_score DESC, borrower_id ASC` (line 930 of `databricks_repo.py`). Result for the first five 86-tie rows:

| `rank_overall` | `borrower_id` | UI position |
|---|---|---|
| 1 | B-102FL7THC6Q3L (88) | row 1 ✅ |
| 2 | B-1AT5CXZZ1NI2N (86) | row 4 ❌ |
| 3 | B-0FSL4B96HG6V4 (86) | row 2 |
| 4 | B-0YVQH0ZJ6WDSQ (86) | row 3 |
| 5 | B-1ENPEPL260AU8 (86) | row 5 ✅ |

Either the precomputed `rank_overall` should be the authoritative ORDER BY in `_LIST_BY_GEO_SQL_TEMPLATE` (and its sibling), or `rank_overall` should be removed from the projection so it doesn't suggest a ranking that the UI doesn't use.

### P2-2. 131 phantom/test rows leaked into `gold.borrower_lifecycle_state`

**Status:** fixed, deployed, and verified live after this audit. The lifecycle sync job now mirrors only Lakebase decisions whose borrower IDs still exist in `mip.gold.borrower_360`.

At original audit time, `SELECT COUNT(*) FROM mip.gold.borrower_lifecycle_state` returned 5,156,313 — 129 more than `borrower_360.COUNT(*) = 5,156,184`. The deployed lifecycle sync fix now filters the gold mirror to real borrowers only; the live anti-join count is 0. Historical sandbox test decisions may still remain in `mip_app.approvals` and the audit ledger unless governance approves deleting them.

**Fix:** clean up the test rows from `mip_app.approvals` and `mip_app.audit_events` (sandbox-only — preserve the ledger in any non-sandbox env), and once P0-2 lands, the surface for accidentally creating these is closed.

### P2-3. Genie warehouse vs. app warehouse split

`/api/2.0/apps/mip-app` shows the app bound to `81d08d4fa2d799e9` ([dev skyler] mip_serverless_sql). The Genie space is configured with `warehouse_id: da02d15a9490650b`. Two serverless warehouses warming up independently doubles cold-start exposure and DBU spend during a presentation. Either pin Genie to the app's warehouse (CAN_USE permission already covers it) or document the split.

### P2-4. Response headers leak internal infrastructure detail

The HTML root response includes:
```
gap-auth: skyler@entrada.ai
x-databricks-internal-pod-ip: MTAuMTUyLjExOC4yMTE6NzE3Mg==     (base64 of 10.152.118.211:7172)
```

Both are appended by Databricks' edge — not by the FastAPI app — but they would be worth checking against your customer's policy on response-header information disclosure for a production deploy.

### P2-5. `/api/geo/zip-rollups` parameter naming mismatch

This was fixed after the audit. `/api/geo/zip-rollups` now accepts canonical `?county_fips=` so the county drill-down API matches `geography_scope.counties[].fips_5` and the frontend no longer needs a differently named follow-up parameter.

### P2-6. Borrower 360 page initial render is ~2 seconds of skeleton

The first navigation to `/borrower-360/{id}` paints a skeleton for the title plus six card placeholders for ~2s before the dossier query returns. Since `gold.borrower_dossier` is pre-joined and indexed on `borrower_id`, the warehouse round-trip itself is sub-second once warm. The skeleton may be over-conservative (e.g., gating on multiple `useQuery` hooks). Worth a profile.

### P2-7. KPI sparkline tooltip claimed `UC function – fn_lead_score` for top-tier and offers

**Status:** fixed in source after this audit. Drawer/source copy now cites the gold precomputed assets behind the summary metrics.

On Home, the Top-Tier Opportunities card chip says `UC function – fn_lead_score` and Offers Recommended says `UC function – fn_next_best_offer`. Both are misleading: the values are precomputed in `gold.lead_scores` / `gold.borrower_360` (the funnel snapshot SQL just COUNTs and filters), not invoked at request time. This is a small thing but the chip currently reads as if the function ran for the home page summary, which it didn't.

### P2-8. Cotality coverage follows the current live footprint

`mip.gold.county_rollup` carries whatever county/state coverage is present in the latest Cotality refresh. This is not a bug: the product follows live Cotality coverage per CLAUDE.md's "Do not filter real data to a single metro" rule. The demo narrative should describe the current live footprint and avoid national-reach claims until the underlying share actually contains national coverage.

---

## Requirements parity matrix (Module 0 spec → today's product)

| Module 0 capability | Status | Notes |
|---|---|---|
| Lead portfolio builder (geography / occupancy / lien / lender / customer) | ✅ live | All filter dropdowns wired; preview returns real KPIs. |
| Public-record Customer 360 (CLIP + Owner Link + related properties) | ✅ live | `subject_property`, `owner_link_id`, `related_property_count`, evidence chips. |
| In-the-money refinance detection | ✅ live | `fn_in_the_money` + `fn_rate_spread`. 134,534 borrowers surfaced. |
| Related-property opportunity detection | ✅ live | "Investor / Multi-Property" segment, 1.7M borrowers. |
| Listing trigger overlay | ⚠️ pending feed | `listed_for_sale=false` for all rows. Honestly disclosed. |
| Building permit trigger overlay | ⚠️ pending feed | `has_permit=false` for all rows. Honestly disclosed. |
| Investor / multi-property segmentation | ✅ live | 1,749,208 borrowers. |
| Home equity propensity | ✅ live | 3,141,667 borrowers. |
| Lead scoring + next-best-offer | ✅ live | `fn_lead_score`, `fn_next_best_offer`; six offer codes (cash_out / investor / nurture / refi_plus_heloc / refi / retention). |
| Drill-down to named borrowers + Cotality source | ✅ live | Borrower 360 has 8-row evidence panel + 3-row trigger timeline, every row cites `mip.silver.*` / `mip.gold.*`. |
| Genie / Agent Bricks integration | ✅ live | Genie space `mortgage_lead_intelligence` answers grounded; declines pending feeds correctly. |
| Approval gate + audit log | ✅ live | Lakebase `mip_app.approvals` + `mip_app.audit_events`. Borrower-existence validation and scoped idempotency are deployed and verified live. |
| Synthetic-only contact data | ✅ live | `display_name = "Owner <hash8>"`, `subject_property = "Synthetic property…"`. No real PII observed. |
| No automatic outreach | ✅ live | Approve sets `outreach_status='queued'`; nothing sent externally. |

---

## Dataset health snapshot

| Asset | Rows | Status |
|---|---|---|
| `mip.silver.property_master` | 5,192,913 | live, May 8 |
| `mip.silver.lien_current` | 5,156,184 | live, May 8 |
| `mip.silver.mortgage_events` | 26,624,795 | live, May 8 |
| `mip.silver.owner_property_bridge` | 3,438,056 | live, May 8 |
| `mip.silver.market_rates_weekly` | 279 (1 `is_latest`) | live, May 7 |
| `mip.first_party.*` (5 tables) | 9,296,331 total | demo_synthetic |
| `mip.gold.borrower_360` | 5,156,184 | live, May 9 |
| `mip.gold.lead_scores` | 5,156,184 | live, May 9 |
| `mip.gold.lead_population` | 282,907 | live, May 9 |
| `mip.gold.evidence_events` | 18,263,605 | live |
| `mip.gold.segment_population` | 42 (rollup) | live, May 9 |
| `mip.gold.borrower_lifecycle_state` | 5,156,184 (matches borrower_360; orphan anti-join = 0) | live, synced after lifecycle filter fix |

---

## Suggested fix order

1. **Keep MLS + Permits as explicit roadmap chips** until Cotality provides the two pending Delta Shares.
2. **Decide the Databricks Apps edge-header policy** (`gap-auth`, `x-databricks-internal-pod-ip`) before public/customer release: accept as a platform exception for internal review, get Databricks suppression support, or put a header-stripping front door in front of the app URL.
3. **Clean up sandbox audit/approvals test pollution** only in the dev workspace if governance confirms deletion is acceptable; otherwise retain append-only audit rows and rely on lifecycle filtering.
4. **Profile Borrower 360 cold navigation** if the ~2s skeleton remains noticeable during rehearsals.

---

## Audit byproducts (sandbox state changes)

The user authorized state-changing test calls. The following rows now exist in the live Lakebase and won't tie back to a real borrower:

| Borrower ID | Action | approval_id | When |
|---|---|---|---|
| `B-AUDIT-PROBE-001` | approve | `8c77fc38-f58f-4139-b429-936a34fce4f9` | 2026-05-09T18:27:40Z |
| `B-FUZZ-NORID` | approve (no client request_id; auto-derived) | `79261af1-2a81-42e3-812f-f5059487f209` | 2026-05-09T18:30:??Z |
| `B-FUZZ-IDEM-1` | approve (idempotent — same `approval_id` returned twice) | `9a05d777-2154-4a09-930f-7387d29ae154` | 2026-05-09T18:30:??Z |
| `B-FUZZ-REJECT` | reject | `e406e80d-19d2-491b-92f9-70a764b88770` | 2026-05-09T18:30:??Z |
| `B-0FSL4B96HG6V4` | draft_outreach | n/a (no approval) | 2026-05-09T18:27:39Z |

Optional cleanup after the now-deployed P0-2 fix, only if governance approves deleting sandbox audit pollution:

```sql
-- in mip_app (Lakebase)
DELETE FROM mip_app.audit_events  WHERE entity_id IN ('B-AUDIT-PROBE-001','B-FUZZ-NORID','B-FUZZ-IDEM-1','B-FUZZ-REJECT');
DELETE FROM mip_app.approvals     WHERE borrower_id IN ('B-AUDIT-PROBE-001','B-FUZZ-NORID','B-FUZZ-IDEM-1','B-FUZZ-REJECT');
```
