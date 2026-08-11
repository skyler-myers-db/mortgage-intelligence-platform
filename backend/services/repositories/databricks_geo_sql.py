"""SQL text for the segment + geography repositories.

Split out of ``databricks_geo.py`` so the repository module stays about
behaviour (cache keys, coercion, degraded paths) and this module stays
about the statements. The repository classes re-export each constant as
a private class attribute, so ``DatabricksGeoRepository._STATE_SQL``
keeps working for the tests that pin the derivation of a disclosed
number.

**Addressable vs contactable.** Every headline count in this file is the
*addressable* population — every borrower the Cotality share can describe.
The Lead Queue applies the contact-eligibility predicate, so it is always
a strict subset: live 2026-08-11, only 216,403 of 5,156,184
``gold.borrower_360`` rows are contactable (4.2%), which put every segment
card and state tile ~23x above the queue it links to. Both numbers were
individually correct and nothing stated the relationship, so a reader
picked up one number and landed on another.

The fix is a second aggregate, never a second definition. The
contactability predicate has exactly one owner —
``backend.services.eligibility.eligible_sql_predicate`` — and these
statements are Python-generated, so they call it rather than restating
its six conditions. Inlining the predicate into the gold CTAS files would
fork the semantics a consent-source swap has to be able to change in one
place (see the module docstring of ``backend/services/eligibility.py``),
which is also why ``sql/transformations/gold_lead_population.sql``
deliberately carries only the RAW eligibility columns forward. Computing
contactable here needs no gold rebuild.
"""

from __future__ import annotations

from backend.services.databricks_sql_helpers import qualify
from backend.services.eligibility import eligible_sql_predicate
from backend.services.repositories.databricks_shared import _SEGMENT_COLUMNS
from backend.services.scoring import HIGH_OPPORTUNITY_THRESHOLD

# The one contactability predicate, unaliased (every statement below
# reads borrower_360 without a table alias) and aliased for the tiles
# that join it back to a precomputed rollup.
_ELIGIBLE = eligible_sql_predicate()

# ``_SEGMENT_COLUMNS`` qualified for the unfiltered list, which now joins
# gold.segment_population against a live aggregate and therefore needs
# every metadata column disambiguated. Derived from the shared constant
# so a new gold column cannot be added to one and missed by the other.
_SEGMENT_COLUMNS_QUALIFIED = ", ".join(
    f"p.{column.strip()}" for column in _SEGMENT_COLUMNS.split(",")
)


# ---------------------------------------------------------------------------
# Segment rollups
# ---------------------------------------------------------------------------

# S1.6: facet-mix lambda shared by the two dynamic mix CTEs below. Same
# sort contract as the gold_segment_population CTAS: count desc then value.
FACET_MIX_EXPR = (
    "TRANSFORM(ARRAY_SORT(COLLECT_LIST(STRUCT(cnt, value)), "
    "(a, b) -> CASE WHEN a.cnt > b.cnt THEN -1 WHEN a.cnt < b.cnt THEN 1 "
    "WHEN a.value < b.value THEN -1 WHEN a.value > b.value THEN 1 ELSE 0 END), "
    "x -> STRUCT(x.value AS value, x.cnt AS count))"
)

# Unfiltered national list. ``count`` is precomputed by the gold CTAS;
# ``contactable`` cannot be, because the predicate lives in Python. So the
# precomputed row is LEFT JOINed to a live per-segment eligible aggregate
# over borrower_360 — same grain (one row per segment code), same source
# table the CTAS aggregated, one extra scan. LEFT JOIN + COALESCE keeps a
# segment whose eligible count is zero (live: payoff_loss_leads) rendering
# its real addressable count instead of dropping off the card grid.
SEGMENT_LIST_SQL = (
    "WITH contactable_by_segment AS ( "
    "  SELECT sc AS segment_code, CAST(COUNT(DISTINCT clip) AS INT) AS contactable "
    f"  FROM {qualify('gold', 'borrower_360')} "
    "  LATERAL VIEW EXPLODE(segment_codes) s AS sc "
    f"  WHERE sc IS NOT NULL AND {_ELIGIBLE} "
    "  GROUP BY sc "
    ") "
    f"SELECT {_SEGMENT_COLUMNS_QUALIFIED}, "
    "  CAST(COALESCE(c.contactable, 0) AS INT) AS contactable "
    f"FROM {qualify('gold', 'segment_population')} AS p "
    "LEFT JOIN contactable_by_segment AS c ON c.segment_code = p.segment_code "
    "WHERE p.state = '_ALL' "
    "ORDER BY p.count DESC"
)

# Filtered path: already live over borrower_360, so contactable is one more
# aggregate over the same scan. Carried as a per-borrower 0/1 flag through
# the explode so a multi-segment borrower is counted once per segment, the
# same way ``count`` is (COUNT DISTINCT clip, not COUNT rows).
SEGMENT_LIST_FILTERED_SQL_TPL = (
    "WITH meta AS ( "
    f"  SELECT {_SEGMENT_COLUMNS} "
    f"  FROM {qualify('gold', 'segment_population')} "
    "  WHERE state = '_ALL' "
    "), base AS ( "
    "  SELECT clip, segment_codes, opportunity_score, loan_product_type, "
    "    origination_channel, "
    f"    CASE WHEN {_ELIGIBLE} THEN 1 ELSE 0 END AS is_contactable "
    f"  FROM {qualify('gold', 'borrower_360')} "
    "  WHERE {filter_clause} "
    "), exploded_segments AS ( "
    "  SELECT DISTINCT clip, sc AS segment_code, opportunity_score, "
    "    loan_product_type, origination_channel, is_contactable "
    "  FROM base "
    "  LATERAL VIEW EXPLODE(segment_codes) s AS sc "
    "  WHERE sc IS NOT NULL "
    "), rollup AS ( "
    "  SELECT "
    "    segment_code, "
    "    CAST(COUNT(DISTINCT clip) AS INT) AS count, "
    "    CAST(COUNT(DISTINCT CASE WHEN is_contactable = 1 THEN clip END) AS INT) "
    "      AS contactable, "
    "    CAST(ROUND(AVG(opportunity_score)) AS INT) AS avg_score "
    "  FROM exploded_segments "
    "  GROUP BY segment_code "
    "), product_mix AS ( "
    f"  SELECT segment_code, {FACET_MIX_EXPR} AS loan_product_mix "
    "  FROM ( "
    "    SELECT segment_code, COALESCE(loan_product_type, 'unknown') AS value, "
    "      CAST(COUNT(DISTINCT clip) AS INT) AS cnt "
    "    FROM exploded_segments "
    "    GROUP BY segment_code, COALESCE(loan_product_type, 'unknown') "
    "  ) GROUP BY segment_code "
    "), channel_mix AS ( "
    f"  SELECT segment_code, {FACET_MIX_EXPR} AS origination_channel_mix "
    "  FROM ( "
    "    SELECT segment_code, COALESCE(origination_channel, 'unknown') AS value, "
    "      CAST(COUNT(DISTINCT clip) AS INT) AS cnt "
    "    FROM exploded_segments "
    "    GROUP BY segment_code, COALESCE(origination_channel, 'unknown') "
    "  ) GROUP BY segment_code "
    ") "
    "SELECT "
    "  m.segment_code, "
    "  m.name, "
    "  COALESCE(r.count, 0) AS count, "
    "  COALESCE(r.contactable, 0) AS contactable, "
    "  m.delta_vs_prior, "
    "  COALESCE(r.avg_score, 0) AS avg_score, "
    "  m.description, "
    "  m.color, "
    "  COALESCE(pm.loan_product_mix, CAST(ARRAY() AS ARRAY<STRUCT<value: STRING, count: INT>>)) AS loan_product_mix, "
    "  COALESCE(cm.origination_channel_mix, CAST(ARRAY() AS ARRAY<STRUCT<value: STRING, count: INT>>)) AS origination_channel_mix "
    "FROM meta AS m "
    "LEFT JOIN rollup AS r ON r.segment_code = m.segment_code "
    "LEFT JOIN product_mix AS pm ON pm.segment_code = m.segment_code "
    "LEFT JOIN channel_mix AS cm ON cm.segment_code = m.segment_code"
)


# ---------------------------------------------------------------------------
# Geography rollups
# ---------------------------------------------------------------------------

# State rollup: join the funnel snapshot (counts) with the top-segment
# table (dominant SegmentCode). LEFT JOIN on state so an empty
# state_top_segment (first deploy before the CTAS has run) still
# returns state counts -- top_segment_code just stays NULL.
# 2026-08-08 UX walk: the ZIP layer is keyed on a 5-digit ZIP and
# gold.zip_rollup filters `LENGTH(zip) = 5`, so borrowers whose share row
# carries no usable ZIP vanish between the state tile and the ZIP tiles
# (live: CO 8.7%, WA 5.8%). `zip_unassigned` is derived as the difference
# between the state total and what the ZIP layer will actually render, so
# the disclosed number IS the drill gap by construction — it cannot drift
# from the tiles the way a separately-computed count would. Both sides
# come from the same refresh anchor, so there is no snapshot skew to
# absorb. GREATEST(0, ...) keeps the field non-negative if a partial
# zip_rollup rebuild ever overshoots.
# 2026-08-11: `contactable` joins from a live per-state eligible aggregate
# for the same reason `contactable_by_segment` exists above — the snapshot
# CTAS cannot precompute a predicate that Python owns.
STATE_SQL = (
    "SELECT "
    "  f.state                         AS state, "
    "  f.addressable_borrowers         AS addressable, "
    "  f.in_the_money_borrowers        AS in_the_money, "
    "  f.high_opportunity_borrowers    AS top_tier_opportunities, "
    "  f.avg_opportunity_score         AS avg_score, "
    "  f.snapshot_date                 AS snapshot_date, "
    "  ts.top_segment_code             AS top_segment_code, "
    "  CAST(COALESCE(cb.contactable, 0) AS INT) AS contactable, "
    "  CAST(GREATEST(0, f.addressable_borrowers - COALESCE(zc.zip_covered, 0)) AS INT) "
    "    AS zip_unassigned "
    f"FROM {qualify('gold', 'funnel_snapshot_daily')} AS f "
    "LEFT JOIN ( "
    "  SELECT state, top_segment_code "
    f"  FROM {qualify('gold', 'state_top_segment')} "
    f"  WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM {qualify('gold', 'state_top_segment')}) "
    ") AS ts ON ts.state = f.state "
    "LEFT JOIN ( "
    "  SELECT state, CAST(SUM(addressable_borrowers) AS BIGINT) AS zip_covered "
    f"  FROM {qualify('gold', 'zip_rollup')} "
    f"  WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM {qualify('gold', 'zip_rollup')}) "
    "  GROUP BY state "
    ") AS zc ON zc.state = f.state "
    "LEFT JOIN ( "
    "  SELECT state, CAST(COUNT(*) AS BIGINT) AS contactable "
    f"  FROM {qualify('gold', 'borrower_360')} "
    f"  WHERE {_ELIGIBLE} "
    "  GROUP BY state "
    ") AS cb ON cb.state = f.state "
    "WHERE f.state <> '_ALL' "
    "  AND f.segment_code = '_ALL' "
    f"  AND f.snapshot_date = (SELECT MAX(snapshot_date) FROM {qualify('gold', 'funnel_snapshot_daily')}) "
    "ORDER BY f.addressable_borrowers DESC"
)

COUNTY_SQL = (
    "SELECT "
    "  fips_5, "
    "  state, "
    "  county_name, "
    "  addressable_borrowers, "
    "  in_the_money_borrowers, "
    "  high_opportunity_borrowers, "
    "  avg_opportunity_score, "
    "  top_segment_code, "
    "  snapshot_date "
    f"FROM {qualify('gold', 'county_rollup')} "
    "WHERE state = :state "
    f"  AND snapshot_date = (SELECT MAX(snapshot_date) FROM {qualify('gold', 'county_rollup')}) "
    "ORDER BY addressable_borrowers DESC"
)

# 2026-08-08: the ZIP layer is keyed on STATE, not county. The Cotality
# share carries exactly one county FIPS per state (audit C2), so the
# fabricated county keys were removed and `county_fips_5` is NULL on
# every zip_rollup row — a county-keyed read returns zero rows for every
# county, always. `ZIP_SQL` (FIPS-keyed) stays for a future licensed
# county dataset; `ZIP_STATE_SQL` is what the map actually drills.
ZIP_SELECT = (
    "SELECT "
    "  zip, "
    "  state, "
    "  county_fips_5, "
    "  addressable_borrowers, "
    "  avg_opportunity_score, "
    "  top_segment_code, "
    "  sample_borrower_id, "
    "  snapshot_date "
    f"FROM {qualify('gold', 'zip_rollup')} "
)

ZIP_SNAPSHOT_AND_ORDER = (
    f"  AND snapshot_date = (SELECT MAX(snapshot_date) FROM {qualify('gold', 'zip_rollup')}) "
    "ORDER BY addressable_borrowers DESC"
)

ZIP_SQL = ZIP_SELECT + "WHERE county_fips_5 = :fips_5 " + ZIP_SNAPSHOT_AND_ORDER

ZIP_STATE_SQL = ZIP_SELECT + "WHERE state = :state " + ZIP_SNAPSHOT_AND_ORDER

# 2026-05-04 (FIX G, round 3): segment-aware per-state counts.
# Computed live off mip.gold.borrower_360 (the FULL addressable
# population — same source the unfiltered funnel snapshot reads),
# with scalar `array_contains` predicates so Databricks SQL parameter
# binding never has to coerce a Python list into ARRAY<STRING>.
# Multi-segment borrowers are still distinct-counted exactly once even
# when the filter selects two of their segments.
#
# Round-2 (FIX G) queried lead_population, which has a score >= 50
# floor baked in. After round-3 reverted the rollup filters (FIX α)
# the unfiltered map shows the FULL addressable count per state.
# Querying lead_population for the filtered path would have been
# inconsistent: filter ON = score-quality subset, filter OFF =
# full population. Switching to borrower_360 here keeps both paths
# at the same "addressable" definition, so toggling a segment
# filter always cuts the count down from the same baseline.
STATE_FILTER_SQL_TPL = (
    "SELECT "
    "  state                                        AS state, "
    "  CAST(COUNT(*) AS INT)                         AS addressable, "
    # Contactable within the SAME filtered universe: the tooltip states a
    # relationship ("N contactable of M"), so both sides must come from one
    # scan or the ratio is a comparison of two different cohorts.
    f"  CAST(SUM(CASE WHEN {_ELIGIBLE} "
    "                THEN 1 ELSE 0 END) AS INT)      AS contactable, "
    "  CAST(SUM(CASE WHEN in_the_money "
    "                THEN 1 ELSE 0 END) AS INT)      AS in_the_money, "
    "  CAST(SUM(CASE WHEN opportunity_score "
    f"                >= {HIGH_OPPORTUNITY_THRESHOLD} "
    "                THEN 1 ELSE 0 END) AS INT)      AS top_tier_opportunities, "
    "  CAST(ROUND(AVG(opportunity_score)) AS INT)    AS avg_score, "
    # Same ZIP-coverage disclosure as STATE_SQL, computed against the
    # same filtered universe the tiles will render — the ZIP layer
    # applies the identical `zip IS NOT NULL AND LENGTH(zip) = 5` gate.
    "  CAST(SUM(CASE WHEN zip IS NULL OR LENGTH(zip) <> 5 "
    "                THEN 1 ELSE 0 END) AS INT)      AS zip_unassigned "
    f"FROM {qualify('gold', 'borrower_360')} "
    "WHERE {filter_clause} "
    "GROUP BY state"
)

COUNTY_FILTER_SQL_TPL = (
    "WITH base AS ( "
    "  SELECT "
    "    county_fips_5 AS fips_5, "
    "    state, "
    "    opportunity_score, "
    "    in_the_money, "
    "    segment_codes "
    f"  FROM {qualify('gold', 'borrower_360')} "
    "  WHERE state = :state "
    "    AND county_fips_5 IS NOT NULL "
    "    AND LENGTH(county_fips_5) = 5 "
    "    AND {filter_clause} "
    "), aggregates AS ( "
    "  SELECT "
    "    fips_5, "
    "    ANY_VALUE(state) AS state, "
    "    CAST(COUNT(*) AS INT) AS addressable_borrowers, "
    "    CAST(SUM(CASE WHEN in_the_money THEN 1 ELSE 0 END) AS INT) AS in_the_money_borrowers, "
    f"    CAST(SUM(CASE WHEN opportunity_score >= {HIGH_OPPORTUNITY_THRESHOLD} THEN 1 ELSE 0 END) AS INT) AS high_opportunity_borrowers, "
    "    CAST(ROUND(AVG(opportunity_score)) AS INT) AS avg_opportunity_score "
    "  FROM base "
    "  GROUP BY fips_5 "
    "), exploded_segments AS ( "
    "  SELECT fips_5, sc AS segment_code "
    "  FROM base "
    "  LATERAL VIEW EXPLODE(segment_codes) s AS sc "
    "  WHERE sc IS NOT NULL "
    "), segment_counts AS ( "
    "  SELECT "
    "    fips_5, "
    "    segment_code, "
    "    COUNT(*) AS cnt, "
    "    ROW_NUMBER() OVER (PARTITION BY fips_5 ORDER BY COUNT(*) DESC, segment_code ASC) AS rn "
    "  FROM exploded_segments "
    "  GROUP BY fips_5, segment_code "
    "), top_segment_per_county AS ( "
    "  SELECT fips_5, segment_code AS top_segment_code "
    "  FROM segment_counts "
    "  WHERE rn = 1 "
    ") "
    "SELECT "
    "  a.fips_5, "
    "  a.state, "
    "  CAST(NULL AS STRING) AS county_name, "
    "  a.addressable_borrowers, "
    "  a.in_the_money_borrowers, "
    "  a.high_opportunity_borrowers, "
    "  a.avg_opportunity_score, "
    "  ts.top_segment_code, "
    "  CAST(NULL AS STRING) AS snapshot_date "
    "FROM aggregates AS a "
    "LEFT JOIN top_segment_per_county AS ts ON ts.fips_5 = a.fips_5 "
    "ORDER BY a.addressable_borrowers DESC"
)

ZIP_FILTER_SQL_TPL = (
    "WITH base AS ( "
    "  SELECT "
    "    zip, "
    "    state, "
    "    county_fips_5, "
    "    borrower_id, "
    "    opportunity_score, "
    "    segment_codes "
    f"  FROM {qualify('gold', 'borrower_360')} "
    # {geo_predicate} is `state = :state` (the live drill) or
    # `county_fips_5 = :fips_5` (reserved for licensed county data).
    # Both sides are repo-owned literals — never caller text.
    "  WHERE {geo_predicate} "
    "    AND zip IS NOT NULL "
    "    AND LENGTH(zip) = 5 "
    "    AND {filter_clause} "
    "), aggregates AS ( "
    "  SELECT "
    "    state, "
    "    county_fips_5, "
    "    zip, "
    "    CAST(COUNT(*) AS INT) AS addressable_borrowers, "
    "    CAST(ROUND(AVG(opportunity_score)) AS INT) AS avg_opportunity_score "
    "  FROM base "
    "  GROUP BY state, county_fips_5, zip "
    "), exploded_segments AS ( "
    "  SELECT state, county_fips_5, zip, sc AS segment_code "
    "  FROM base "
    "  LATERAL VIEW EXPLODE(segment_codes) s AS sc "
    "  WHERE sc IS NOT NULL "
    "), segment_counts AS ( "
    "  SELECT "
    "    state, "
    "    county_fips_5, "
    "    zip, "
    "    segment_code, "
    "    COUNT(*) AS cnt, "
    "    ROW_NUMBER() OVER (PARTITION BY state, county_fips_5, zip ORDER BY COUNT(*) DESC, segment_code ASC) AS rn "
    "  FROM exploded_segments "
    "  GROUP BY state, county_fips_5, zip, segment_code "
    "), top_segment_per_zip AS ( "
    "  SELECT state, county_fips_5, zip, segment_code AS top_segment_code "
    "  FROM segment_counts "
    "  WHERE rn = 1 "
    "), ranked_borrowers AS ( "
    "  SELECT "
    "    state, "
    "    county_fips_5, "
    "    zip, "
    "    borrower_id, "
    "    ROW_NUMBER() OVER (PARTITION BY state, county_fips_5, zip ORDER BY opportunity_score DESC, borrower_id ASC) AS rn "
    "  FROM base "
    "), sample_borrower_per_zip AS ( "
    "  SELECT state, county_fips_5, zip, borrower_id AS sample_borrower_id "
    "  FROM ranked_borrowers "
    "  WHERE rn = 1 "
    ") "
    "SELECT "
    "  a.zip, "
    "  a.state, "
    "  a.county_fips_5, "
    "  a.addressable_borrowers, "
    "  a.avg_opportunity_score, "
    "  ts.top_segment_code, "
    "  sb.sample_borrower_id, "
    "  CAST(NULL AS STRING) AS snapshot_date "
    "FROM aggregates AS a "
    "LEFT JOIN top_segment_per_zip AS ts ON ts.state = a.state AND COALESCE(ts.county_fips_5, '') = COALESCE(a.county_fips_5, '') AND ts.zip = a.zip "
    "LEFT JOIN sample_borrower_per_zip AS sb ON sb.state = a.state AND COALESCE(sb.county_fips_5, '') = COALESCE(a.county_fips_5, '') AND sb.zip = a.zip "
    "ORDER BY a.addressable_borrowers DESC"
)
