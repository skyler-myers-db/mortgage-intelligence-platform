# Data quality + parity audit

> **Internal validation artifact — not approved for public release.** End-to-end check that the live app's data plane and contract layer agree: every gold table row count, every parent-child FK, every refresh timestamp, every Pydantic-to-SQL projection. Designed to catch silent drift between what the warehouse holds and what the API returns.

**Auditor:** Claude (Cowork)
**App:** `https://mip-app-2543889327043640.aws.databricksapps.com`, deployment `01f14e554fcd12a9bfc8eab46332c320`
**Warehouse:** `81d08d4fa2d799e9` (DEFAULT profile, direct SQL Statements API)
**Method:** SQL probes against `mip.silver.*`, `mip.gold.*`, `mip.ref.*`, `mip.semantics.*`, `mip.first_party.*` + grep against `backend/services/repositories/databricks_repo.py` + `backend/schemas/lead.py` to compare projections vs Pydantic schemas vs DDL.
**Scope:** schema inventory, cardinality, null/FK integrity, refresh staleness, lifecycle mirror parity, value-range realism, projection-to-schema drift.

---

## Headline result

The data plane is in **good shape**. Cardinality is exact across all parent-child tables (5,156,184 borrowers everywhere it should be), FK integrity is perfect (zero orphans), refresh timestamps are coherent (single per-run boundary), and the segment rollup that I initially suspected of duplicating is actually accumulating one snapshot per day exactly as designed. **No P0 or P1 data integrity defects.**

Three **P2 cleanup items** worth filing:
1. ~15,400 borrowers (≈0.3% of population) have synthetic `current_rate` values in the 10–100% range, which is outside any defensible mortgage-rate distribution and creates ~700 borrowers with `rate_spread_bps ≥ 1000` (impossible in real life).
2. 808,702 borrowers (15.7%) have `avm_value = 0`. The offer engine correctly avoids equity-dependent recommendations for these (zero routed to `heloc` / `cash_out` / `refi_plus_heloc`), but the UI still happily shows `LTV: 0%` and `Equity: $0` without flagging the underlying missing-data condition.
3. Nine fields exist on `mip.gold.borrower_dossier` (and were intentionally added there for downstream use) but are not projected through the API or surfaced in the `Borrower360` Pydantic schema. This is a **feature gap, not a drift bug** — the data is there, the contract just doesn't expose it — but it's a backlog item worth tracking so the columns don't become dead weight.

---

## What I checked

### Cardinality and FK integrity

| Table | Row count | Distinct borrower_id | Notes |
|---|---|---|---|
| `mip.gold.borrower_360` | 5,156,184 | 5,156,184 | parent table |
| `mip.gold.borrower_dossier` | 5,156,184 | 5,156,184 | 1:1 mirror — every 360 row has a dossier row, no extras |
| `mip.gold.lead_scores` | 5,156,184 | 5,156,184 | 1:1 |
| `mip.gold.borrower_lifecycle_state` | 5,156,184 | 5,156,184 | 1:1, mirrored from Lakebase |
| `mip.gold.lead_population` | 282,867 | 282,867 | strict subset (eligible + filtered) |

Cross-checks: `LEFT JOIN borrower_360 → borrower_dossier` returns **0 missing**; reverse direction returns **0 extras**. Lead population is a strict subset of `borrower_360`. No orphan rows anywhere.

### Refresh staleness

All gold tables share a single coherent refresh boundary:

| Table | `refreshed_at` |
|---|---|
| `borrower_360` | `2026-05-12T05:07:33.858Z` |
| `borrower_dossier` | `2026-05-12T05:07:33.858Z` |
| `lead_scores` | `2026-05-12T05:07:33.858Z` |
| `lead_population` | `2026-05-12T05:07:33.858Z` |
| `borrower_lifecycle_state` | `2026-05-12T21:44:09.739Z` (mirrored later, expected) |

Single-source-of-truth: `mip.ref.refresh_run_state` captures the run timestamp once and every gold CTAS subqueries the same `MAX(captured_at)` row. The lifecycle table refreshes on its own cadence after the Lakebase audit ledger settles for the day — this is intentional and documented.

### Null rates on key columns

All zero on the columns that participate in scoring (`opportunity_score`, `confidence`, `rate_spread_bps`, `recommended_offer_code`, `segment_codes`, `approval_status`, `marketing_eligible`). The only NULL-by-design fields are the discretionary ones (`current_lender_ref`, `last_touch_at`, `eligible_recontact_at`, `assigned_to_email`), and Pydantic accepts them as `str | None` / `datetime | None`.

### Segment rollup history (the "10x duplicates" red herring)

`mip.gold.segment_population_prior` has 280 rows. At first glance that looked like 10× duplication of 28 distinct `(segment_code, state)` pairs. Drilling in:

- 10 distinct `snapshot_date` values from `2026-04-21` through `2026-05-12`.
- Exactly 28 rows per `snapshot_date`.
- The current `segment_population` has 42 `(segment_code, state)` pairs because the meta `CROSS JOIN` grid always emits a row for the blocked `listed` and `permit` segments (`count=0`); the prior table is driven from `LATERAL VIEW EXPLODE(segment_codes)` which only emits rows for segments with non-zero membership, so blocked segments correctly never appear.

42 − 28 = 14 missing pairs = 7 states × 2 blocked segments. The `MERGE ... ON (segment_code, state, snapshot_date)` is idempotent within a day; cross-day rows are intentional history. This is **working as designed**, not a defect. Filed here so the next auditor doesn't repeat the false alarm.

---

## Defects

### 🟡 P2 — Defect 1: `current_rate` has unrealistic synthetic outliers

The silver source `mip.silver.lien_current.first_pos_rate` should be a fractional rate in `[0, 0.15]` (0–15% APR, covering even the most punishing subprime). The actual distribution:

| `first_pos_rate` bucket | Row count | % of population |
|---|---|---|
| NULL (no first lien) | 2,164,101 | 42.0% |
| `<0.01` (0–1% — implausibly low) | 146 | 0.003% |
| `0.01–0.10` (1–10% — realistic) | 2,976,505 | 57.7% |
| `0.10–1.00` (10–100% — **unrealistic**) | **15,432** | **0.30%** |

The `first_pos_rate * 100 → current_rate` transform in `gold_borrower_360.sql:667` propagates these outliers into `current_rate`, which feeds the Borrower 360 "current rate" pill and the `rate_spread_bps` calculation. Downstream effect:

| `rate_spread_bps` bucket | Row count | Notes |
|---|---|---|
| `<= 0` | 4,583,583 | at or below market — no opportunity |
| `1–74` | 381,121 | below ITM threshold |
| `75–149` | 99,276 | **ITM band** |
| `150–299` | 67,512 | strong refi |
| `300–999` | 23,997 | strong refi (high but possible) |
| `1000–4999` | 678 | **impossible-but-present** |
| `>= 5000` | 17 | **impossible-but-present** |

A user opening Borrower 360 on one of these ~700 rows sees `current_rate: 84.56%` and `rate_spread_bps: +7,819` next to a serious mortgage product. It looks like a bug to anyone shown the screen.

**Cause:** the synthetic data generator that seeded `mip.silver.lien_current` has a long-tail bug where ~0.3% of liens get a rate in `[0.1, 1.0)` instead of `[0.01, 0.15)`. This is a generator problem, not a transform problem — `fn_rate_spread` is mathematically correct given its inputs.

**Fix options (low effort):**

- Clamp upstream: add a `WHERE first_pos_rate < 0.15` guard in the silver-loading step, or `LEAST(first_pos_rate, 0.15)` in the gold CTAS. Conservative.
- Clamp downstream: cap `rate_spread_bps` at ±500 in `fn_rate_spread` or in the gold transform. Faster, less invasive.
- Fix at the source: patch the synthetic generator. Highest value because it also addresses anyone else downloading the silver table directly.

**Code refs:**
- `sql/transformations/gold_borrower_360.sql:667` — `current_rate` derivation
- `sql/uc_functions/fn_rate_spread.sql` — spread function (functionally correct)
- Wherever the synthetic data generator writes to `mip.silver.lien_current`

### 🟡 P2 — Defect 2: `avm_value = 0` for 808,702 borrowers (15.7%) is invisible in the UI

The offer engine handles this correctly today — zero of those 808,702 borrowers are routed to equity-dependent offers (`heloc`, `cash_out`, `refi_plus_heloc`):

| `recommended_offer_code` for `avm_value=0` | Row count |
|---|---|
| `investor` | 589,560 |
| `nurture` | 217,902 |
| `retention` | 1,240 |

But on Borrower 360, the LTV / Equity panel renders `LTV: 0%`, `Equity: $0`, `AVM: $0` with no indicator that the underlying property valuation simply isn't present. An operator looking at one of these rows will assume the borrower is genuinely zero-equity, not that the AVM feed didn't cover this CLIP.

**Fix:** add an "AVM unavailable" badge (or render `—` in place of `$0`) when `avm_value = 0`. Same pattern the Lead Queue already uses for blocked segments. This is a UI honesty fix; the data is correct.

**Code refs:**
- `frontend/src/routes/borrower-360.tsx` — LTV/Equity panel
- `backend/schemas/lead.py:124` — `avm_value: int` (no special handling needed in schema; keep it `int` and let the frontend branch on `== 0`)

### 🟡 P2 — Defect 3: 9 dossier columns are not projected through the API or surfaced in `Borrower360`

`mip.gold.borrower_dossier` has 55 columns. The repository projection `_BORROWER_DOSSIER_COLUMNS` in `backend/services/repositories/databricks_repo.py:121` projects 46 of them (via `_BORROWER_360_COLUMNS` + 3 evidence/timeline fields). The 9 unprojected columns:

| Column | Source | Pydantic? | Projection? |
|---|---|---|---|
| `is_absentee` | Cotality occupancy | ❌ | ❌ |
| `is_corporate_owner` | Cotality entity | ❌ | ❌ |
| `has_first_party_relationship` | First-party CRM | ❌ | ❌ |
| `first_party_relationship_depth` | First-party CRM | ❌ | ❌ |
| `first_party_recent_interactions` | First-party CRM | ❌ | ❌ |
| `first_party_recent_application` | First-party CRM | ❌ | ❌ |
| `first_party_synthetic_demo` | First-party CRM | ❌ | ❌ |
| `first_pos_loan_type` | Cotality lien | ❌ | ❌ |
| `situs_cbsa_code` | Cotality property | ❌ | ❌ |

This is a **feature gap, not a drift bug** — the data isn't wrong, the API contract just doesn't surface it. The columns landed in the dossier table for forward-looking work (CBSA-level rollups, first-party signals to enrich Borrower 360 storytelling, occupancy-aware offer routing) and are exercised by SQL/Genie surfaces but never by the React app.

**Two valid resolutions:**

1. **Surface them.** Add the 9 fields to `Borrower360` (Pydantic), the `_BORROWER_360_COLUMNS` projection, and the relevant UI surfaces (occupancy chip on Borrower 360, "current customer for N years" badge from first-party depth, CBSA-aware segment intelligence rollups). This is the right move if the product roadmap calls for them.
2. **Drop them from gold.** If they're aspirational and not on the next 3 commits' roadmap, removing them from the dossier CTAS keeps the schema honest. Right now their presence in gold but absence in the API is a "stage left, never delivered" pattern that will quietly rot.

The safer recommendation is **option 1**, because (a) the first-party fields are already populated from a real upstream join in `gold_borrower_dossier.sql`, (b) `is_absentee` / `is_corporate_owner` are already encoded into the `investor` segment predicate so the underlying signal is being used, and (c) `situs_cbsa_code` would unlock the metro-level geography drill-down that the prototype implies.

**Code refs:**
- `backend/services/repositories/databricks_repo.py:101` — `_BORROWER_360_COLUMNS`
- `backend/schemas/lead.py:120` — `Borrower360` Pydantic
- `sql/transformations/gold_borrower_dossier.sql` — source of truth

---

## What works well

- **Cardinality lock**: 5,156,184 across `borrower_360` / `borrower_dossier` / `lead_scores` / `borrower_lifecycle_state`, exact, no drift.
- **FK integrity**: zero orphans across every parent-child boundary I tested.
- **Refresh staleness**: every gold table shares a single `refreshed_at` value sourced from `mip.ref.refresh_run_state`, so the "as-of" boundary the UI shows is honest.
- **AVM-guarded offer routing**: borrowers without an AVM value are correctly never routed to equity-dependent offers — the offer engine is doing its job even when the underlying data is incomplete.
- **Segment rollup snapshot history**: idempotent MERGE on `(segment_code, state, snapshot_date)`, 10 days of history accumulated correctly, delta calculation reads yesterday's snapshot for "% change" copy.
- **Marketing suppression propagation**: `marketing_eligible`, `consent_status`, `suppression_reason`, `last_touch_at`, `eligible_recontact_at` are present in every relevant projection and Pydantic schema. The earlier "183,671 opt-outs not propagating" defect from the persona audits is fully closed.
- **Pydantic / projection alignment on the surfaced fields**: every column in `_BORROWER_360_COLUMNS` and `_LEAD_POPULATION_COLUMNS` has a matching field in `LeadSummary` or `Borrower360`. The drift only goes one way (dossier has more than the contract), never the dangerous direction (contract claims a field that doesn't exist).

---

## Summary verdict

- **Schema inventory tested**: 15 gold tables, 6 silver, 4 ref, 3 semantic views, 5 first-party.
- **Defects**: 0 P0, 0 P1, **3 P2** (rate outliers, AVM=0 UI, dossier feature gap).
- **False alarm cleared**: `segment_population_prior` is not duplicating — it's accumulating 10 days of snapshots exactly as designed.

The data plane is production-quality. The three P2 items are honest-UX polish and feature-gap cleanup, not integrity bugs.

---

## Sources

- Live SQL probes against warehouse `81d08d4fa2d799e9` via Databricks Statements API
- `backend/services/repositories/databricks_repo.py` lines 101–141, 1699–1703
- `backend/schemas/lead.py` lines 31–158
- `sql/transformations/gold_borrower_360.sql` lines 121, 266–270, 666–667
- `sql/transformations/gold_segment_population.sql` lines 32–80
- `sql/uc_functions/fn_rate_spread.sql`
- Statement IDs: `01f14e66-b5af-1299-b2e0-a7980b6c80ad`, `01f14e66-b675-1af8-8430-64d6736b9c37` (segment prior); plus follow-up rate / AVM / FK probes documented in `/tmp/segpop.sh`, `/tmp/segpop2.sh`, `/tmp/rate_check.sh`, `/tmp/rate_real.sh`, `/tmp/rate_source.sh`, `/tmp/rate_units.sh`, `/tmp/avm_check.sh`

---

## Re-validation — 2026-05-13

After the engineering team shipped fixes for all three P2 defects, re-ran the original probes plus live API + UI checks against the new deployment.

**Active deployment:** `01f14e6f026a161e95c88e798a8096cc`
**Gold refresh run:** `623227038704667` (refresh_at `2026-05-13T01:46:40.218Z`, applied uniformly across `borrower_360`, `borrower_dossier`, `lead_scores`, `lead_population`)

### Claim-by-claim verdict

| Claim | Probe | Expected | Actual | Verdict |
|---|---|---|---|---|
| `current_rate` is bounded ≤ 15% | `SELECT MAX(current_rate)` | `15.0` | `15.0` | ✅ PASS |
| No `current_rate > 15` rows | `COUNT(*) WHERE current_rate > 15` | `0` | `0` | ✅ PASS |
| No `current_rate ∈ (0, 1)` outliers (sub-1% noise clamped) | `COUNT(*) WHERE current_rate > 0 AND current_rate < 1` | `0` | `0` | ✅ PASS |
| No impossible `rate_spread_bps ≥ 1000` | `COUNT(*) WHERE rate_spread_bps >= 1000` | `0` | `0` | ✅ PASS (was 695) |
| AVM=0 borrowers still route safely | `COUNT(*) WHERE avm_value=0 AND offer IN (heloc, cash_out, refi_plus_heloc)` | `0` | `0` | ✅ PASS |
| B-0OXOBYLW8MNCK borrower math fixed | `SELECT current_rate, rate_spread_bps` | `15.0 / 863` | `15.0 / 863` | ✅ PASS |
| B-0OXOBYLW8MNCK evidence chip parity (no stale +9238) | `evidence_events[0].signal_value` | `+863 bps` | `+863 bps` | ✅ PASS |
| B-0OXOBYLW8MNCK why-panel uses fresh spread | `why_panel.rate_spread_bps` | `863` | `863` | ✅ PASS |
| API exposes all 8 previously-hidden dossier fields | `/api/borrowers/B-0OXOBYLW8MNCK` | all present, valid types | `situs_cbsa_code=16980, first_pos_loan_type=CNV, is_absentee=False, is_corporate_owner=False, has_first_party_relationship=True, depth=1, recent_interactions=0, recent_application=False` | ✅ PASS |
| UI renders AVM-unavailable chrome (B-1EU6J79FEJGFS) | DOM scrape | `"AVM unavailable"` + `"Not a zero-equity signal"` | exact match | ✅ PASS |
| UI renders Metro / loan-type pair | DOM scrape | `"16980 · CNV"` for Chicago borrower | `"16980 · CNV"` | ✅ PASS |
| UI renders is_absentee/is_corporate_owner badges | DOM scrape | `"Not absentee" / "Individual owner"` | exact match on both test borrowers | ✅ PASS |
| UI renders First-party signals block | DOM scrape | `"1 first-party links / Summit demo synthetic"` | exact match for has_relationship=True borrower; `"No first-party signal"` empty state for the negative case | ✅ PASS |
| equity_pct serializes as `null` when AVM=0 (UI branch signal) | `/api/borrowers/B-1EU6J79FEJGFS` | `equity_pct: null` | `equity_pct: null` | ✅ PASS |
| Cardinality preserved | `COUNT(*)` per gold table | `5,156,184` | `5,156,184` exact on borrower_360 / borrower_dossier / lead_scores / borrower_lifecycle_state | ✅ PASS |
| Lead population shrinks marginally (expected — clamped rates push ~42 borrowers below ITM) | `COUNT(*) lead_population` | `~282,825` | `282,825` | ✅ PASS |
| FK integrity (1:1 mirror, zero orphans) | LEFT JOIN both directions | `0 / 0` | `0 / 0` | ✅ PASS |
| Scoring refresh timestamps share single boundary | `MAX(refreshed_at)` per scoring gold table | identical ISO ts | `2026-05-13T01:46:40.218Z` across borrower_360 / borrower_dossier / lead_scores / lead_population | ✅ PASS |
| Lifecycle mirror carries its own refresh boundary | `COUNT(refreshed_at), MIN/MAX(refreshed_at), MIN/MAX(synced_at)` on borrower_lifecycle_state | all rows non-null; one mirror timestamp | `5,156,184` non-null rows; `2026-05-13T02:53:10.232Z` for min/max refreshed_at and synced_at | ✅ PASS |
| segment_population unaffected | `SELECT count FROM segment_population WHERE state='_ALL'` | 6 segments, ITM 134,534, listed/permit blocked at 0 | exact match | ✅ PASS |
| No regression in Lead Queue rendering | DOM scrape, 1030 rows, no error banners, no rates ≥ 1000 bps anywhere visible | clean | clean | ✅ PASS |
| No regression on Home (KPIs) | DOM scrape: 5,156,184 portfolio / 134,534 ITM | exact | exact | ✅ PASS |
| No regression on Segment Intelligence | DOM scrape: 6 segment cards with `Awaiting feed` for listed/permit | clean | clean | ✅ PASS |

**23 of 23 checks pass.** No regressions surfaced.

### Lifecycle refresh-boundary follow-up

- Closed after follow-up: `borrower_lifecycle_state` now includes `refreshed_at` as the Lakebase mirror refresh boundary. It intentionally equals the sync-run timestamp, not the scoring gold `refresh_run_state` timestamp.

### Sign-off

All three P2 defects from the original audit are **closed live on deployment `01f14e6f026a161e95c88e798a8096cc`**. The parity bug found mid-fix (evidence chips showing stale `+9238 bps` while borrower math showed `+863 bps`) is also closed — chip, why-panel, supporting-evidence section, and trigger timeline all agree on `+863 bps`.

The op note about `databricks bundle deploy -t dev` returning 403 on app-update (recovered via direct `databricks apps deploy`) is a tooling story for the bundle workflow, not a data-quality issue, and doesn't affect this sign-off.
