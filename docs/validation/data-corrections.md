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
