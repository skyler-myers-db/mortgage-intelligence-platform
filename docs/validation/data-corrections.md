# Data Corrections — Slice13 Accuracy

Two data-plane corrections landed on branch `slice13-accuracy-validation`.
Neither changes the Pydantic schema or the `/api/*` surface.

## GAP 1 — historical-lender dedup

### Bug

`sql/transformations/gold_lead_scores.sql` computed
`historical_mortgage_count_at_lender` as `COUNT(*)` over mortgage-event rows
per CLIP, grouped on `clip`. That is an **event** count, not a
**relationship** count. A single property with three Summit lien events
(purchase → refinance → release) was reported as `3`, inflating the
`relationship` sub-score into the `>= 2` branch on the strength of repeat
events at a single property.

The intent of the branch (data-contract §5) is to reward owners who have
previously financed **multiple distinct properties** with Summit, i.e. an
owner-level relationship signal.

### Fix

`historical_summit` CTE rewrites to:

```sql
SELECT pm.owner_link_id, COUNT(DISTINCT me.clip) AS historical_distinct_clips_at_lender
FROM mip.silver.mortgage_events me
JOIN mip.silver.property_master pm ON pm.clip = me.clip
WHERE UPPER(me.lender_name) LIKE '%SUMMIT%'
  AND me.situs_state IN ('IL','CA','FL','TX','WA','CO')
  AND pm.owner_link_id IS NOT NULL
GROUP BY pm.owner_link_id
```

The join in `base` then moves from `hs.clip = b.clip` to
`hs.owner_link_id = b.owner_link_id`. The relationship-branch threshold
stays at `>= 2`, now meaning "owner has financed at least two distinct
properties with Summit," which is what the sub-score documentation asserts.

### Sample (before / after)

| owner_link_id | distinct CLIPs at Summit | Summit lien events on sole CLIP | old count | new count |
|---|---|---|---|---|
| OL-001 | 1 | 3 (purchase + refi + release) | 3 | 1 |
| OL-002 | 2 | 1 each | 1 | 2 |
| OL-003 | 1 | 1 | 1 | 1 |

OL-001 previously hit the 95 branch on a single property with repeat events;
now it correctly lands in the 88 branch (current customer, single
relationship). OL-002 correctly earns the 95 branch.

### Files touched

- `sql/transformations/gold_lead_scores.sql` — CTE + base join + branch
  reference column renamed to `historical_summit_distinct_clips`.
- `docs/data-contract-module0.md` §5 — snippet updated to cite the new
  column name and the owner-level semantics.
- `tests/integration/test_sql_queries.py` — regression test added
  (`test_historical_summit_counts_distinct_clips_not_events`), gated on
  `DATABRICKS_HOST` / `DATABRICKS_TOKEN` / `DATABRICKS_WAREHOUSE_ID`.

### Downstream impact

Segment-count parity: because the relationship sub-score feeds
`opportunity_score`, and the `retention` segment definition uses
`is_current_customer AND (rate_spread_bps >= 50 OR is_competitor_lien OR
listed_for_sale)` (NOT the historical count), the segment *membership*
should not change — segment counts in `gold.segment_population` remain
stable. However, **borrower opportunity_score distributions will shift
downward** for owners who previously inflated via repeat events on a
single CLIP. The segment-parity agent will need a re-run to re-baseline
the per-segment score histograms.

## GAP 2 — `mip.ref.lender_dictionary`

### What changed

Promoted the inline Python `_LENDER_REF_MAP` dict (11 rows) to a governed
UC table `mip.ref.lender_dictionary` seeded with **23 rows** (11 canonical
+ 12 common US mortgage servicers verified from public CoreLogic lien
data and the MERS lender directory). New columns: `lender_type`,
`is_competitor`, `last_updated`, `source`.

### Deployment wiring

New bundle job `mip_ref_seed` runs two SQL tasks:

1. `sql/ddl/004_ref_tables.sql` — `CREATE SCHEMA IF NOT EXISTS mip.ref`
   + `CREATE TABLE IF NOT EXISTS mip.ref.lender_dictionary`.
2. `sql/ref/lender_dictionary_seed.sql` — `MERGE` on `raw_key`
   (idempotent).

The same two SQL tasks are also inlined in `mip_refresh_silver` between
`init_catalog_schemas` and `refresh_silver_pipeline`, so
`databricks bundle deploy -t dev` lands a populated dictionary on first
deploy with zero manual steps — consistent with the
"self-contained, zero-click deploy" posture in CLAUDE.md.

### Fallback behavior

`_LENDER_REF_MAP` stays in `backend/services/pii_redaction.py` as an
**intentional fallback**. The new `LenderRefResolver`:

1. Loads from `mip.ref.lender_dictionary` on first `resolve()` call.
2. Caches the result in-process via `TTLCache` for 15 minutes.
3. On UC unavailable (missing creds, breaker open, `DatabricksSqlError`,
   socket error): falls back to `_LENDER_REF_MAP` and logs a single
   WARNING. Redaction never breaks.
4. On unknown raw string (not in UC, not in fallback): title-cases the
   raw string (existing behavior preserved).

A UC glitch therefore produces a 15-minute window of slightly-stale
vocabulary (missing any analyst contribution that landed since the last
warehouse fetch) but never leaks a raw uppercase lender string to the
API surface.

### Files touched

- `sql/ddl/004_ref_tables.sql` — new.
- `sql/ref/lender_dictionary_seed.sql` — new; 23 rows.
- `backend/services/pii_redaction.py` — added `LenderRefResolver`,
  `get_lender_resolver()`, `_reset_lender_resolver_for_tests()`;
  `generalize_lender` delegates to the resolver.
- `databricks.yml` — added `mip_ref_seed` job + inlined ref init tasks
  into `mip_refresh_silver` between catalog init and silver pipeline.
- `resources/jobs.yml` — mirror block per existing convention.
- `tests/unit/test_pii_redaction.py` — added 9 resolver tests (UC load,
  fallback on UC failure, TTL cache, invalidate, unknown title-case,
  None/empty passthrough, singleton swap, fallback row-count floor).

### Validation

- `pytest -q tests/unit/` — all unit tests pass, including 9 new
  resolver tests.
- `ruff check backend` — clean.
- `databricks bundle validate -t dev` — recommended before deploy (not
  run in this edit cycle; bundle YAML changes require warehouse-id env
  var to validate in full, per CLAUDE.md's bundle convention).

### Follow-ups

- Segment-parity agent must re-run to pick up the `relationship` score
  shift from GAP 1.
- Future slice: add a unit test that parses
  `sql/ref/lender_dictionary_seed.sql` and asserts its row count matches
  `len(_LENDER_REF_MAP) + 12`, mechanically enforcing fallback/UC sync.

## Wave 2 — data-accuracy P0s (2026-04-21)

Three P0 bugs surfaced by Wave 1 spot-checks. None of these change the
Pydantic schema or the `/api/*` surface — they only fix the data-plane
transformation. All three require a silver + gold refresh because the
live tables were produced under the old definitions.

### Wave-2 GAP 1 — `borrower_id` collisions in `mip.gold.borrower_360`

#### Bug

The formula
`CONCAT('B-', LPAD(CAST((ABS(XXHASH64(clip)) % 99999) + 10000 AS STRING), 5, '0'))`
collapsed ~5.16M CLIPs into ~90K synthetic `B-#####` ids (avg 57
collisions per id, worst observed 688). The router's
`get(borrower_id)` path in
`backend/services/repositories/databricks_repo.py` queries
`WHERE borrower_id = :id LIMIT 1` and returned a non-deterministic CLIP
per request. Clicking a borrower from the Lead Queue into the Borrower
360 page showed different borrowers to different users for the same id.

#### Fix

Widen to base36 of the absolute 64-bit hash, padded to 13 chars:

```sql
CONCAT('B-', LPAD(CONV(CAST(ABS(XXHASH64(clip)) AS STRING), 10, 36), 13, '0'))
```

Slot count: `36^13 ≈ 1.7e20` for 5.16M rows — collision probability
negligible. `CONV(..., 10, 36)` is Spark's standard base converter;
`LPAD(..., 13, '0')` stabilises the string length so the `B-` + 13-char
format is consistent across the population.

#### Files touched

- `sql/transformations/gold_borrower_360.sql` — the formula, plus a
  long comment explaining the collision history.
- `sql/ddl/gold_borrower_360.sql` — updated the `borrower_id` column
  comment to describe the base36 / width-13 formula.
- `tests/integration/test_borrower_id_uniqueness.py` — new; gated
  regression that asserts
  `COUNT(*) == COUNT(DISTINCT borrower_id)` and a format RLIKE check
  for `B-[13-char base36]`.

Everything else keeps working: the router, the
`_BORROWER_360_COLUMNS` projection, the `LeadSummary` / `Borrower360`
Pydantic schemas, and `gold.lead_population` all already treat
`borrower_id` as an opaque `str`. The Python test fixture IDs
(`B-48291` etc.) are hand-authored golden fixtures — they are NOT
derived from CLIP hashing and are deliberately left alone to preserve
`tests/fixtures/*_golden.json` pinning.

### Wave-2 GAP 2 — `owner_is_corporate` BOOLEAN cast collapsed to NULL

#### Bug

`sql/transformations/silver_property_master.sql` had
`CAST(COALESCE(owner_1_corporate_indicator, 0) AS BOOLEAN)`. An earlier
share probe saw BIGINT 1/0; the current share emits STRING `'Y'` / `'N'`.
`CAST('Y' AS BOOLEAN)` returns NULL in Spark (not TRUE), so
`owner_is_corporate` was NULL on every row and the `is_investor`
segment predicate lost its corporate-owner leg.

#### Fix

Explicit string match with normalisation:

```sql
(UPPER(TRIM(COALESCE(CAST(owner_1_corporate_indicator AS STRING), ''))) = 'Y')
  AS owner_is_corporate
```

`CAST(... AS STRING)` defends against the column ever drifting back to
BIGINT — `CAST(1 AS STRING)` yields `'1'` which compares `!= 'Y'` and
gives FALSE, so the legacy BIGINT path at least doesn't produce a false
TRUE; the share-level fix has to happen upstream if the type flips
again.

#### Files touched

- `sql/transformations/silver_property_master.sql` — the coercion, plus
  an updated header-comment block explaining the Spark BOOLEAN-cast
  trap.
- `tests/integration/test_silver_coercion.py` — new; gated regression
  that asserts at least one row in `mip.silver.property_master` has
  `owner_is_corporate = TRUE` (a floor the 6-state footprint easily
  clears when the coercion works).

### Wave-2 GAP 3 — silver `situs_zip_code` was 9-digit (ZIP+4)

#### Bug

Data contract §2.1 / §2.2 specify 5-digit STRING. The share emits
9-digit ZIP+4 (with or without a dash) on ~89% of rows.
`gold_borrower_360.sql` had added a defensive `SUBSTR(..., 1, 5)` in
`subject_property`, but silver itself was still non-contract — so any
consumer reading silver directly (the forthcoming geography drill-down
queries + Genie joins) would see 9-digit codes. Silver should be
authoritative, not gold.

#### Fix

Truncate at silver, strip non-digits first to tolerate the dashed
variant:

```sql
SUBSTR(REGEXP_REPLACE(CAST(situs_zip_code AS STRING), '[^0-9]', ''), 1, 5)
  AS situs_zip_code
```

Applied in both `silver_property_master.sql` and
`silver_lien_current.sql`. The defensive SUBSTR in
`gold_borrower_360.sql` was then simplified to a plain `COALESCE` —
redundant once silver is clean.

#### Files touched

- `sql/transformations/silver_property_master.sql` — truncation on
  `situs_zip_code`.
- `sql/transformations/silver_lien_current.sql` — truncation on
  `situs_zip_code`.
- `sql/transformations/gold_borrower_360.sql` — removed the now-
  redundant `SUBSTR(w.zip, 1, 5)` in `subject_property`; updated the
  associated comment.
- `tests/integration/test_silver_zip_5_digit.py` — new; gated
  regression that parameterises over both silver tables and asserts
  `MAX(LENGTH(situs_zip_code)) <= 5`.

### §REFRESH-AFTER-WAVE-2 — operator rebuild commands

The live silver + gold tables are stale after these edits. An operator
must run the two bundle jobs in order; they must not run from the
subagent session. `lead_population` in particular is stale and the
second job rebuilds it from the newly-widened `borrower_id`.

```bash
# 1. Silver refresh: picks up the BOOLEAN-cast and ZIP truncation fixes.
databricks bundle run mip_refresh_silver -t dev

# 2. Score / gold refresh: picks up the widened borrower_id in
#    mip.gold.borrower_360 and re-materialises mip.gold.lead_population
#    from it.
databricks bundle run mip_refresh_scores -t dev
```

Post-rebuild verification: run the three new integration tests
against the refreshed tables (set `DATABRICKS_HOST`, `DATABRICKS_TOKEN`,
`DATABRICKS_WAREHOUSE_ID`):

```bash
pytest -q tests/integration/test_borrower_id_uniqueness.py \
          tests/integration/test_silver_coercion.py \
          tests/integration/test_silver_zip_5_digit.py
```

All three should pass. A failure on the uniqueness test means the gold
job ran against a stale DDL — redeploy the bundle. A failure on the
coercion test means the share type flipped again — upstream probe
required. A failure on the ZIP test means silver was not rebuilt.

### Wave-2 validation (subagent edit cycle)

- `pytest -q tests/unit/` — passes (no unit-test surface changed;
  Python fixtures hold their pinned ids).
- `ruff check backend tests tools jobs pipelines` — clean.
- Frontend: no `.ts/.tsx` touched; no lint/test run required.
- `databricks bundle validate -t dev` — NOT run in this cycle; edits
  are SQL-only and the bundle YAML is unchanged.
