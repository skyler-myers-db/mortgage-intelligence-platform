# Segment-Count Parity Validation

**Slice:** 13 — Accuracy Validation.
**Date run:** 2026-04-21.
**Warehouse:** `da02d15a9490650b` (serverless).
**Catalog:** `mip`.
**Share:** `cotality_mortgage_data.corelogic` (last updated 2026-10-29 per gap analysis).
**Pass/fail summary:** **PASS on all 5 unblocked segments × 6 states = 30 cells** (exact match across the board). BLOCKED segments (`listed`, `permit`) correctly return 0 on both sides.

The test that locks this in place lives at
[tests/integration/test_segment_count_parity.py](../../tests/integration/test_segment_count_parity.py).
It is GATED on live warehouse credentials (same pattern as
`tests/integration/test_sql_python_parity.py`) and SKIPs cleanly when
they are absent.

---

## 1. Method

For each segment (`itm`, `equity`, `investor`, `retention`, `listed`,
`permit`) × each state (IL, CA, FL, TX, WA, CO) we ran TWO queries:

1. **Reference** — an INDEPENDENT SQL statement written against
   `cotality_mortgage_data.corelogic.*` that re-implements the segment
   rule from scratch. No `mip.silver.*` or `mip.gold.*` references.
   Independence is what makes this a validation rather than a
   tautology.
2. **Gold** — `SELECT state, COUNT(*) FROM mip.gold.borrower_360 WHERE
   array_contains(segment_codes, '<segment>') GROUP BY state`.

Segment rules are taken verbatim from
[sql/transformations/gold_borrower_360.sql](../../sql/transformations/gold_borrower_360.sql)
lines 179–194.

Thresholds (match data-contract §5):
- `min_spread_bps = 75`, `min_equity_pct = 15`
- `heloc_equity_min = 35`
- `retention_min_spread = 50`

Market rate: **MORTGAGE30US = 0.063 (6.30%)** — probed live from
`mip.silver.market_rates_weekly WHERE is_latest = TRUE` on the
validation run. Stored inline in the test as a constant so the
reference query does not re-use the silver market-rate path.

### Tolerance

- Segment count ≥ 1,000 → **0.5% relative tolerance**.
- Segment count < 1,000 → **exact match required** (relative-tolerance
  is misleading at small N; some retention-state cells are single
  digits).
- BLOCKED segments → both sides must be exactly **0**.

All 30 unblocked cells matched **exactly** (delta = 0). The tolerance
was not needed on this run; it's there so a small, explainable rounding
drift in a future refresh does not fail the test.

---

## 2. Reference queries (independent)

### 2.1 `itm` — in the money

`rate_spread_bps >= 75 AND equity_pct >= 15`.

```sql
WITH src AS (
  SELECT
    situs_state AS state,
    clip,
    CASE
      WHEN first_position_mortgage_interest_rate IS NULL THEN NULL
      WHEN CAST(first_position_mortgage_interest_rate AS DOUBLE) <= 0 THEN NULL
      ELSE CAST(first_position_mortgage_interest_rate AS DOUBLE) / 100.0
    END AS rate_frac,
    CAST(estimated_value_mktg AS BIGINT) AS avm,
    CAST(total_amount_of_open_mortgage_liens AS BIGINT) AS lien,
    CAST(estimated_combined_ltv_loan_to_value AS DOUBLE) AS cltv
  FROM cotality_mortgage_data.corelogic.entrada_eval_voluntary_lien_status_marketing_v2
  WHERE situs_state IN ('IL','CA','FL','TX','WA','CO') AND clip IS NOT NULL
),
calc AS (
  SELECT
    state, clip,
    CAST(ROUND((rate_frac - 0.063) * 10000.0) AS INT) AS rate_spread_bps,
    CAST(GREATEST(0, LEAST(100, CASE
      WHEN cltv IS NOT NULL AND cltv > 0 THEN ROUND(100 - cltv)
      WHEN avm  IS NOT NULL AND avm  > 0 THEN ROUND(100.0 * (avm - COALESCE(lien, 0)) / avm)
      ELSE 0
    END)) AS INT) AS equity_pct
  FROM src
)
SELECT state, COUNT(*) FROM calc
WHERE rate_spread_bps >= 75 AND equity_pct >= 15
GROUP BY state ORDER BY state;
```

### 2.2 `equity` — HELOC / cash-out

`equity_pct >= 35 AND second_pos_amount IS NULL`.

```sql
WITH src AS (
  SELECT
    situs_state AS state, clip,
    CAST(estimated_value_mktg AS BIGINT) AS avm,
    CAST(total_amount_of_open_mortgage_liens AS BIGINT) AS lien,
    CAST(estimated_combined_ltv_loan_to_value AS DOUBLE) AS cltv,
    CAST(second_position_mortgage_amount AS BIGINT) AS second_pos
  FROM cotality_mortgage_data.corelogic.entrada_eval_voluntary_lien_status_marketing_v2
  WHERE situs_state IN ('IL','CA','FL','TX','WA','CO') AND clip IS NOT NULL
),
calc AS (
  SELECT
    state, clip, second_pos,
    CAST(GREATEST(0, LEAST(100, CASE
      WHEN cltv IS NOT NULL AND cltv > 0 THEN ROUND(100 - cltv)
      WHEN avm  IS NOT NULL AND avm  > 0 THEN ROUND(100.0 * (avm - COALESCE(lien, 0)) / avm)
      ELSE 0
    END)) AS INT) AS equity_pct
  FROM src
)
SELECT state, COUNT(*) FROM calc
WHERE equity_pct >= 35 AND second_pos IS NULL
GROUP BY state ORDER BY state;
```

### 2.3 `investor` — multi-property / corporate / absentee

`related_property_count >= 2 OR owner_is_corporate OR is_absentee`.

```sql
WITH prop6 AS (
  SELECT
    clip, situs_state AS state,
    owner_1_identifier AS owner_link,
    (UPPER(TRIM(COALESCE(owner_1_corporate_indicator, ''))) = 'Y') AS is_corp,
    (mailing_state IS NOT NULL
     AND UPPER(TRIM(mailing_state)) <> UPPER(TRIM(situs_state))) AS is_absentee
  FROM cotality_mortgage_data.corelogic.entrada_eval_property_domain_v3
  WHERE situs_state IN ('IL','CA','FL','TX','WA','CO') AND clip IS NOT NULL
),
bridge AS (
  SELECT owner_1_identifier AS owner_link, COUNT(*) AS related_n
  FROM cotality_mortgage_data.corelogic.entrada_eval_property_domain_v3
  WHERE clip IS NOT NULL AND owner_1_identifier IS NOT NULL
  GROUP BY owner_1_identifier
)
SELECT p.state, COUNT(*)
FROM cotality_mortgage_data.corelogic.entrada_eval_voluntary_lien_status_marketing_v2 l
JOIN prop6 p ON p.clip = l.clip
LEFT JOIN bridge b ON b.owner_link = p.owner_link
WHERE l.situs_state IN ('IL','CA','FL','TX','WA','CO') AND l.clip IS NOT NULL
  AND (COALESCE(b.related_n, 1) >= 2 OR p.is_corp OR p.is_absentee)
GROUP BY p.state ORDER BY p.state;
```

**Independence note on `owner_is_corporate`:** the raw share has
`owner_1_corporate_indicator` as a STRING with values `{'Y', NULL}`
(probed 2026-04-21 — no `'N'` values exist). Silver's
`CAST(COALESCE(owner_1_corporate_indicator, 0) AS BOOLEAN)` relies on
Spark column-expression coercion that yields the right answer column-
wise but FAILS on literals. My reference uses the semantic expression
`UPPER(TRIM(...)) = 'Y'` which is unambiguously correct and
independent.

### 2.4 `retention` — current customer at risk

`is_current_customer AND (rate_spread_bps >= 50 OR is_competitor_lien OR listed_for_sale)`.

```sql
WITH calc AS (
  SELECT
    situs_state AS state,
    (first_position_currently_assigned_lender_company_name IS NOT NULL
     AND UPPER(first_position_currently_assigned_lender_company_name) LIKE '%SUMMIT%') AS is_summit,
    CAST(ROUND((
      CASE
        WHEN first_position_mortgage_interest_rate IS NULL THEN NULL
        WHEN CAST(first_position_mortgage_interest_rate AS DOUBLE) <= 0 THEN NULL
        ELSE CAST(first_position_mortgage_interest_rate AS DOUBLE) / 100.0
      END - 0.063) * 10000.0
    ) AS INT) AS spread_bps
  FROM cotality_mortgage_data.corelogic.entrada_eval_voluntary_lien_status_marketing_v2
  WHERE situs_state IN ('IL','CA','FL','TX','WA','CO') AND clip IS NOT NULL
)
SELECT state, COUNT(*) FROM calc
WHERE is_summit AND spread_bps >= 50
GROUP BY state ORDER BY state;
```

Because `is_current_customer` and `is_competitor_lien` are mutually
exclusive by construction (both derive from the same servicer string —
one contains "SUMMIT", the other is NOT-NULL-and-not-containing), the
OR-branch inside the segment rule collapses to
`is_current_customer AND rate_spread_bps >= 50` once `listed_for_sale`
is held at FALSE (BLOCKED).

### 2.5 `listed`, `permit` — BLOCKED

Per [docs/data-contract-module0.md](../data-contract-module0.md) §9
these are hardcoded `FALSE` in `gold.borrower_360` until Cotality's MLS
Listings and Building Permits shares land. Reference value is
definitionally `0`; gold must also be `0`. The test enforces both
directions so a regression here (removing the `CAST(FALSE AS BOOLEAN)
AS listed_for_sale` literal without wiring MLS first) fails loudly.

---

## 3. Results (2026-04-21 run)

### 3.1 Unblocked segments — all PASS, exact match

| Segment     | State | Reference | Gold (`borrower_360`) | Δ | Verdict |
|-------------|:-----:|----------:|----------------------:|:---:|:---:|
| `itm`       | CA | 18,724 | 18,724 | 0 | PASS |
| `itm`       | CO | 1,582 | 1,582 | 0 | PASS |
| `itm`       | FL | 21,528 | 21,528 | 0 | PASS |
| `itm`       | IL | 70,939 | 70,939 | 0 | PASS |
| `itm`       | TX | 19,323 | 19,323 | 0 | PASS |
| `itm`       | WA | 15,646 | 15,646 | 0 | PASS |
| **itm total** | | **147,742** | **147,742** | 0 | PASS |
| `equity`    | CA | 663,859 | 663,859 | 0 | PASS |
| `equity`    | CO | 78,022 | 78,022 | 0 | PASS |
| `equity`    | FL | 511,478 | 511,478 | 0 | PASS |
| `equity`    | IL | 969,048 | 969,048 | 0 | PASS |
| `equity`    | TX | 484,080 | 484,080 | 0 | PASS |
| `equity`    | WA | 435,180 | 435,180 | 0 | PASS |
| **equity total** | | **3,141,667** | **3,141,667** | 0 | PASS |
| `investor`  | CA | 252,238 | 252,238 | 0 | PASS |
| `investor`  | CO | 47,779 | 47,779 | 0 | PASS |
| `investor`  | FL | 257,156 | 257,156 | 0 | PASS |
| `investor`  | IL | 701,639 | 701,639 | 0 | PASS |
| `investor`  | TX | 249,961 | 249,961 | 0 | PASS |
| `investor`  | WA | 240,435 | 240,435 | 0 | PASS |
| **investor total** | | **1,749,208** | **1,749,208** | 0 | PASS |
| `retention` | CA | 20 | 20 | 0 | PASS |
| `retention` | CO | 9 | 9 | 0 | PASS |
| `retention` | FL | 18 | 18 | 0 | PASS |
| `retention` | IL | 320 | 320 | 0 | PASS |
| `retention` | TX | 346 | 346 | 0 | PASS |
| `retention` | WA | 36 | 36 | 0 | PASS |
| **retention total** | | **749** | **749** | 0 | PASS |

### 3.2 BLOCKED segments — all 0 on both sides

| Segment  | State | Reference (contract) | Gold | Verdict |
|----------|:-----:|:---:|:---:|:---:|
| `listed` | CA/CO/FL/IL/TX/WA | 0 | 0 | PASS |
| `permit` | CA/CO/FL/IL/TX/WA | 0 | 0 | PASS |

### 3.3 Total-row parity — PASS

| Metric | Count |
|---|---:|
| `cotality_mortgage_data.corelogic.entrada_eval_voluntary_lien_status_marketing_v2` (6-state, clip NOT NULL) | 5,156,184 |
| `mip.gold.borrower_360` | 5,156,184 |
| **Δ** | **0** |

The 2 rows dropped relative to the raw share (5,156,186 → 5,156,184)
are rows outside the 6-state footprint (the raw share is already ~99.9%
inside IL/CA/FL/TX/WA/CO).

---

## 4. Gap analysis — nothing to fix for segment counts

**No SQL fixes applied.** All five unblocked segment counts match the
raw share byte-for-byte per state. That means:

- `silver_lien_current.sql` filters and rate-conversion are consistent
  with the raw share.
- `silver_property_master.sql` corporate / absentee boolean coercions
  hold despite the share's STRING-typed `owner_1_corporate_indicator`
  column (the CAST expression works column-wise even though it fails on
  literals — confirmed by direct count: 839,258 true rows).
- `gold.property_owner_bridge` rollup of `owner_1_identifier` matches
  the raw-share rollup exactly.
- `gold_borrower_360.sql` segment-code FILTER construction (lines
  179–194) evaluates the thresholds identically on live data.

---

## 5. Outstanding accuracy risks (OUT of segment-count scope, but worth
      flagging)

These are not segment-count parity failures. They're adjacent accuracy
issues that surfaced during validation. Flagging here so the master
agent can route them.

### 5.1 `mip.gold.lead_population` has a legacy 10,000-row cap still in
     the warehouse table

- **Observed:** 10,000 rows in `mip.gold.lead_population`, MIN/MAX
  opportunity_score = 64 / 68. `refreshed_at = 2026-04-21 20:42:18 UTC`.
- **Expected (per current SQL):** `WHERE opportunity_score >= 50`, no
  cap → approximately **194,990 rows** on today's `borrower_360`.
- **Cause:** commit `b0ad03c` ("drop lead_population row cap") removed
  the `rank_overall <= 10000` predicate from
  `sql/transformations/gold_lead_population.sql`, but the live table
  was last CTAS-refreshed by an older warehouse job definition. The
  authoritative gold materialisation path is the CTAS chain in the
  `mip_refresh_scores` job (the retired `mip_gold_pipeline` DLT was a
  dual-write mirror and has been removed).
- **Impact:** the Leads page shows 10k rows when ~195k should qualify;
  real "top borrowers by opportunity" views truncate at an arbitrary
  row count. The SCORE floor is still enforced (all 10k rows ≥ 50), so
  rank order within the 10k is correct — just truncated.
- **Action:** NOT a segment-count concern; flagged for the master agent
  and sql-data-modeling agent to re-run the lead_population CTAS after
  the bundle redeploys. Test
  `test_lead_population_score_floor` catches any further regression
  (e.g. if a row sneaks in with score < 50).

### 5.2 `silver_property_master.sql` CAST expression on
      `owner_1_corporate_indicator` is brittle

- **Observed:** silver's `CAST(COALESCE(owner_1_corporate_indicator, 0)
  AS BOOLEAN)` works on columns (counts match exactly) but fails when
  evaluated on literals in isolation (`CAST_INVALID_INPUT` on `'Y'`).
- **Impact:** no downstream impact today — silver populates correctly.
- **Risk:** any future query that reuses this pattern on a constant (a
  threshold admin-config lookup, a test fixture) will explode at
  runtime. Worth replacing with `UPPER(TRIM(COALESCE(...,''))) = 'Y'`
  next time `silver_property_master.sql` is touched.
- **Action:** tracked as a data-corrections item; not a segment-count
  fix. Not urgent.

---

## 6. Re-running this validation

```bash
# Ensure env vars are set (or rely on the CLI OAuth fallback the test
# ships with):
export DATABRICKS_HOST=https://dbc-3aa503a9-4fa8.cloud.databricks.com
export DATABRICKS_TOKEN=<PAT>                # or rely on `databricks auth token -p DEFAULT`
export DATABRICKS_WAREHOUSE_ID=da02d15a9490650b

# Run just the parity test (~15s on a warm warehouse):
pytest tests/integration/test_segment_count_parity.py -v

# Or run all integration tests:
pytest tests/integration/ -v
```

**Expected output on a clean pass:** `39 passed` (6 states × 6 segments
= 36 parametrized cases + 3 bonus tests: total-row parity, segment_
population consistency, lead_population score floor).

When creds are not present, the test SKIPs with a descriptive message
and CI stays green.
