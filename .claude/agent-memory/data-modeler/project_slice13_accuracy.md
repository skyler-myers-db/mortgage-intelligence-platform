---
name: slice13-accuracy two-gap landing
description: GAP 1 (historical-lender event→relationship dedup in gold_lead_scores) + GAP 2 (mip.ref.lender_dictionary promotion from inline _LENDER_REF_MAP)
type: project
---

Landed on branch `slice13-accuracy-validation` on 2026-04-21.

**Why:** Two data-correctness gaps flagged by segment-parity + PII agents.

- GAP 1: `gold_lead_scores.historical_summit` CTE counted lien events per CLIP (`COUNT(*)`). A single property with 3 Summit events hit the `>= 2` relationship-score boost. Fixed to `COUNT(DISTINCT me.clip)` grouped by `owner_link_id`, joined in `base` on `hs.owner_link_id = b.owner_link_id`. Column renamed `historical_summit_count` → `historical_summit_distinct_clips` to make the semantic shift grep-able.
- GAP 2: `_LENDER_REF_MAP` (11 rows) promoted to `mip.ref.lender_dictionary` (23 rows: 11 canonical + 12 public-data servicers). `_LENDER_REF_MAP` stays as a fallback. `LenderRefResolver` loads from UC, caches 15 min via `resilience.TTLCache`, falls back silently on breaker-open / DatabricksSqlError / OSError. One-time WARNING on first fallback so ops notice.

**How to apply:**

- Any future change to the `relationship` sub-score rules must preserve the owner-level semantic. Do NOT re-group by CLIP.
- Keep `_LENDER_REF_MAP` in sync with `sql/ref/lender_dictionary_seed.sql` until a mechanical assertion lands. A future slice should parse the seed SQL and assert row-count + raw_key parity.
- `mip_ref_seed` job is inlined in `databricks.yml` (canonical) + mirrored in `resources/jobs.yml` per the existing fred_rates_ingest convention; also inlined as tasks in `mip_refresh_silver` between `init_catalog_schemas` and `refresh_silver_pipeline`.
- Segment-parity agent needs a re-run after GAP 1 since opportunity_score distributions shift for owners who previously inflated via repeat events on one CLIP. Segment MEMBERSHIP should be stable (retention segment doesn't use the historical count) but score histograms do shift.
