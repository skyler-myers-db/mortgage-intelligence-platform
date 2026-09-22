"""SQL fetchers for the borrower E2E audit: sampling, raw-share inputs, gold rows.

Split out of ``tools/e2e_borrower_audit.py`` (2026-09-08); every function moved
verbatim. The main script still owns orchestration and the CLI.
"""

from __future__ import annotations

import math
from typing import Any

from backend.services.databricks_sql import DatabricksSqlClient, DatabricksSqlError

# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------


def _bucket_label(score: int) -> str:
    """Bucket labels tuned to the live ``borrower_360`` score distribution.

    `gold.borrower_360.opportunity_score` is capped around 68 because
    the full `intent_trigger` treatment (recent_refi/payoff counts,
    listed/permit, AVM uplift) lives in `gold.lead_scores`, not here.
    The three buckets therefore split the observed [0..68] range into
    roughly-equal-sized groups so a stratified sample lands a real
    mix:

      - high >= 60
      - mid  45..59
      - low  < 45

    Adjust alongside any future change to `gold_borrower_360.sql`
    subscore composition.
    """
    if score >= 60:
        return "high"
    if score >= 45:
        return "mid"
    return "low"


def load_coverage_states(client: DatabricksSqlClient) -> list[str]:
    """Return the current state coverage from refreshed gold geography.

    ``mip.gold.county_rollup`` is the authoritative coverage surface. If
    that rollup has not refreshed yet, fall back to distinct states present
    in ``mip.gold.borrower_360`` so the audit remains data-driven.
    """

    statements = (
        """
        SELECT DISTINCT state
        FROM mip.gold.county_rollup
        WHERE snapshot_date = (
          SELECT MAX(snapshot_date) FROM mip.gold.county_rollup
        )
          AND state RLIKE '^[A-Z]{2}$'
        ORDER BY state
        """,
        """
        SELECT DISTINCT state
        FROM mip.gold.borrower_360
        WHERE state RLIKE '^[A-Z]{2}$'
        ORDER BY state
        """,
    )
    for stmt in statements:
        rows = client.execute(stmt)
        states = sorted(
            {
                str(row.get("state") or "").upper()[:2]
                for row in rows
                if str(row.get("state") or "").upper()[:2].isalpha()
                and len(str(row.get("state") or "").upper()[:2]) == 2
            }
        )
        if states:
            return states
    raise DatabricksSqlError(
        "No refreshed state coverage found in gold geography or borrower tables"
    )


def sample_clips(
    client: DatabricksSqlClient,
    *,
    sample_size: int,
    seed: int,
    states: list[str],
) -> list[dict[str, Any]]:
    """Stratified-random sample of CLIPs across state x score bucket.

    We use ``ORDER BY HASH(clip, :seed)`` so the selection is
    reproducible for a given seed without the cost of ``RAND()``. For
    each ``(state, bucket)`` cell we take a dynamic share of candidates,
    then trim to ``sample_size`` total while preserving state coverage.
    """
    if not states:
        raise DatabricksSqlError("No states supplied for borrower audit sampling")
    per_state = max(3, math.ceil(sample_size / len(states)))
    # Thresholds tuned to the live borrower_360 score distribution --
    # see _bucket_label for the rationale (capped at ~68 because the
    # full intent_trigger lives in gold.lead_scores, not here).
    buckets = [
        ("high", "opportunity_score >= 60"),
        ("mid", "opportunity_score >= 45 AND opportunity_score < 60"),
        ("low", "opportunity_score < 45"),
    ]
    # One row per (state, bucket) per call: we ask for ceil(per_state/3)
    # from each bucket so every state has the 3-bucket mix represented.
    per_cell = max(1, math.ceil(per_state / len(buckets)))

    rows: list[dict[str, Any]] = []
    for state in states:
        for bucket_name, pred in buckets:
            stmt = (
                "SELECT clip, borrower_id, state, opportunity_score "
                "FROM mip.gold.borrower_360 "
                f"WHERE state = '{state}' AND {pred} "
                f"ORDER BY HASH(clip, {seed}) ASC "
                f"LIMIT {per_cell}"
            )
            fetched = client.execute(stmt)
            for r in fetched:
                r["_bucket"] = bucket_name
                rows.append(r)

    # Rotate across (state, bucket) so the trimmed sample covers every
    # combination rather than filling high-score slots first. We build
    # a per-(state, bucket) queue and pick round-robin.
    bucket_names = ("high", "mid", "low")
    by_cell: dict[tuple[str, str], list[dict[str, Any]]] = {
        (s, b): [] for s in states for b in bucket_names
    }
    # Sort rows into cells while keeping the deterministic HASH order
    # from the per-cell LIMITs intact (ordering by clip for stability).
    rows.sort(key=lambda r: (r["state"], r["_bucket"], r["clip"]))
    for r in rows:
        by_cell[(r["state"], r["_bucket"])].append(r)

    selected: list[dict[str, Any]] = []
    idx = 0
    # Interleave: first pass hits every (state, bucket) once, then
    # repeats until we run out or hit sample_size.
    cells = [(s, b) for b in bucket_names for s in states]
    while len(selected) < sample_size:
        cell = cells[idx % len(cells)]
        if by_cell[cell]:
            selected.append(by_cell[cell].pop(0))
        idx += 1
        # Guard: if all cells are empty, stop.
        if idx > len(cells) * max(1, sample_size) and not any(by_cell.values()):
            break
        if idx > 100000:  # pragma: no cover -- defensive
            break
    return selected[:sample_size]


# ---------------------------------------------------------------------------
# Raw-share fetch
# ---------------------------------------------------------------------------


def fetch_raw_inputs(
    client: DatabricksSqlClient,
    clip: str,
) -> dict[str, Any]:
    """Fetch the raw Cotality share rows for ``clip``.

    Projects only the columns the gold CTAS consumes. NO PII is
    projected: we explicitly omit ``owner_1_full_name``,
    ``situs_street_address``, ``mailing_street_address``, etc.
    """
    stmt_lien = (
        "SELECT "
        "  clip, "
        "  situs_state, "
        "  situs_zip_code, "
        "  owner_occupancy_code, "
        "  CAST(total_number_of_open_mortgage_liens AS INT)         AS total_open_liens, "
        "  CAST(total_amount_of_open_mortgage_liens AS BIGINT)      AS total_open_lien_balance, "
        "  CAST(estimated_value_mktg AS BIGINT)                     AS avm_value, "
        "  CAST(estimated_combined_ltv_loan_to_value AS DOUBLE)     AS estimated_cltv, "
        "  CASE "
        "    WHEN first_position_mortgage_interest_rate IS NULL THEN NULL "
        "    WHEN CAST(first_position_mortgage_interest_rate AS DOUBLE) < 1 THEN NULL "
        "    WHEN CAST(first_position_mortgage_interest_rate AS DOUBLE) > 15 THEN 0.15 "
        "    ELSE CAST(first_position_mortgage_interest_rate AS DOUBLE) / 100.0 "
        "  END                                                      AS first_pos_rate, "
        "  first_position_mortgage_loan_type_code                   AS first_pos_loan_type, "
        "  first_position_currently_assigned_lender_company_name    AS first_pos_lender_current, "
        "  CAST(second_position_mortgage_amount AS BIGINT)          AS second_pos_amount "
        "FROM cotality_mortgage_data.corelogic.entrada_eval_voluntary_lien_status_marketing_v2 "
        "WHERE clip = :clip "
        "LIMIT 1"
    )
    # Note on `owner_1_corporate_indicator`: the raw share schema emits
    # this column as STRING on the live catalog (values 'Y'/'N'/empty).
    # The current silver transformation normalizes it with
    # UPPER(TRIM(...)) = 'Y'; the audit recomputes the same boolean below.
    stmt_prop = (
        "SELECT "
        "  clip, "
        "  situs_city, "
        "  situs_state, "
        "  situs_core_based_statistical_area_cbsa AS situs_cbsa_code, "
        "  owner_1_identifier                     AS owner_link_id, "
        "  owner_1_corporate_indicator            AS owner_corporate_indicator_raw, "
        "  CASE "
        "    WHEN mailing_state IS NOT NULL "
        "     AND UPPER(TRIM(mailing_state)) <> UPPER(TRIM(situs_state)) "
        "    THEN TRUE ELSE FALSE "
        "  END                                    AS is_absentee, "
        "  CAST(year_built AS INT)                AS year_built, "
        "  CAST(total_number_of_bedrooms_all_bldgs AS INT) AS bedrooms, "
        "  CAST(total_number_of_bathrooms AS DOUBLE)       AS bathrooms "
        "FROM cotality_mortgage_data.corelogic.entrada_eval_property_domain_v3 "
        "WHERE clip = :clip "
        "LIMIT 1"
    )
    # Silver projection: the gold CTAS joins silver, not raw, so for
    # the "gold matches its inputs" audit we use silver values where
    # they diverge from the raw share (e.g. the `owner_is_corporate`
    # BOOLEAN coercion we know drifts under the current raw-share
    # STRING type).
    stmt_silver_pm = (
        "SELECT clip, situs_city, situs_state, owner_link_id, "
        "       owner_is_corporate, is_absentee, year_built, "
        "       bedrooms, bathrooms "
        "FROM mip.silver.property_master "
        "WHERE clip = :clip LIMIT 1"
    )
    stmt_silver_lc = (
        "SELECT clip, situs_state, situs_zip_code, owner_occupancy_code, "
        "       total_open_liens, total_open_lien_balance, "
        "       avm_value, estimated_cltv, first_pos_rate, "
        "       first_pos_loan_type, first_pos_lender_current, "
        "       second_pos_amount "
        "FROM mip.silver.lien_current "
        "WHERE clip = :clip LIMIT 1"
    )
    stmt_silver_listing = (
        "SELECT clip, listing_status_category, listing_status_description, "
        "       listing_date, listing_status_date, listing_price, "
        "       days_on_market AS listing_days_on_market, listing_service, "
        "       is_active_listing "
        "FROM mip.silver.listing_activity "
        "WHERE clip = :clip AND is_current_listing = TRUE "
        "QUALIFY ROW_NUMBER() OVER ("
        "  PARTITION BY clip "
        "  ORDER BY is_active_listing DESC, "
        "           COALESCE(listing_status_date, listing_date, DATE(source_updated_at), DATE(ingest_ts)) DESC"
        ") = 1 "
        "LIMIT 1"
    )
    stmt_silver_heloc = (
        "SELECT clip, heloc_propensity_score, heloc_propensity_run_date "
        "FROM mip.silver.heloc_propensity "
        "WHERE clip = :clip LIMIT 1"
    )
    stmt_silver_refi = (
        "SELECT clip, refi_propensity_score, refi_propensity_run_date "
        "FROM mip.silver.refi_propensity "
        "WHERE clip = :clip LIMIT 1"
    )
    lien = client.execute_one(stmt_lien, {"clip": clip}) or {}
    prop = client.execute_one(stmt_prop, {"clip": clip}) or {}
    silver_pm = client.execute_one(stmt_silver_pm, {"clip": clip}) or {}
    silver_lc = client.execute_one(stmt_silver_lc, {"clip": clip}) or {}
    silver_listing = client.execute_one(stmt_silver_listing, {"clip": clip}) or {}
    silver_heloc = client.execute_one(stmt_silver_heloc, {"clip": clip}) or {}
    silver_refi = client.execute_one(stmt_silver_refi, {"clip": clip}) or {}
    return {
        "lien": lien,
        "property": prop,
        "silver_lien": silver_lc,
        "silver_property": silver_pm,
        "silver_listing": silver_listing,
        "silver_heloc_propensity": silver_heloc,
        "silver_refi_propensity": silver_refi,
    }


def fetch_owner_related_count(
    client: DatabricksSqlClient,
    owner_link_id: str | int | None,
) -> int:
    """Look up the property count behind the Owner Link.

    Reads from ``mip.gold.property_owner_bridge`` -- the same table gold
    joins during CTAS. We could recompute against
    ``silver.property_master`` directly, but the gold CTAS itself joins
    through the bridge so reading the bridge keeps the audit faithful
    to the actual derivation path. (A separate audit for the bridge's
    rollups is out of scope here.)
    """
    if owner_link_id in (None, "", 0):
        return 1
    row = client.execute_one(
        "SELECT related_property_count FROM mip.gold.property_owner_bridge "
        "WHERE owner_link_id = :owner_link_id",
        {"owner_link_id": str(owner_link_id)},
    )
    if not row:
        return 1
    return int(row.get("related_property_count") or 1)


def fetch_market_rate(client: DatabricksSqlClient) -> float:
    """Latest ``MORTGAGE30US`` rate in FRACTIONAL form (0.063 == 6.3%)."""
    row = client.execute_one(
        "SELECT rate_fraction FROM mip.silver.market_rates_weekly "
        "WHERE series_id = 'MORTGAGE30US' AND is_latest = TRUE LIMIT 1"
    )
    if not row:
        raise DatabricksSqlError("No latest MORTGAGE30US row in silver.market_rates_weekly")
    return float(row["rate_fraction"])


def fetch_gold_row(
    client: DatabricksSqlClient,
    clip: str,
) -> dict[str, Any]:
    """Projected gold row -- every column the audit compares against.

    Keyed by ``clip`` (the only globally-unique key in the table).
    ``borrower_id`` is display-safe, but ``clip`` is the mastered key for
    this audit; the report never assumes borrower_id uniqueness.
    """
    stmt = (
        "SELECT "
        "  clip, borrower_id, city, state, zip, situs_cbsa_code, "
        "  segment_codes, equity_estimate, equity_pct, rate_spread_bps, "
        "  market_rate_fraction, opportunity_score, confidence, "
        "  recommended_offer_code, recommended_offer, why_now, "
        "  evidence_ids, approval_status, owner_link_id, subject_property, "
        "  avm_value, current_lien_balance, current_rate, ltv, "
        "  related_property_count, is_owner_occupied, is_absentee, "
        "  is_corporate_owner, has_permit, listed_for_sale, is_investor, "
        "  listing_status_category, listing_status_description, listing_date, "
        "  listing_status_date, listing_price, listing_days_on_market, listing_service, "
        "  heloc_propensity_score, heloc_propensity_run_date, has_heloc_propensity_trigger, "
        "  refi_propensity_score, refi_propensity_run_date, has_refi_propensity_trigger, "
        "  is_current_customer, is_competitor_lien, second_pos_amount, "
        "  first_pos_loan_type, min_spread_bps_applied, "
        "  min_equity_pct_applied, in_the_money "
        "FROM mip.gold.borrower_360 "
        "WHERE clip = :clip "
        "LIMIT 1"
    )
    row = client.execute_one(stmt, {"clip": clip})
    if not row:
        raise DatabricksSqlError(f"borrower_360 row missing for clip={clip}")
    return row


def fetch_evidence_count(client: DatabricksSqlClient, clip: str) -> int:
    """Count evidence rows that contribute to the ``evidence`` sub-score.

    Matches the filter in ``gold_borrower_360.sql``: live evidence signals
    contribute, while true filed permits remain disabled until a source lands
    and loan_type_fit is an explanatory row rather than a source trigger.
    """
    row = client.execute_one(
        "SELECT COUNT(*) AS n FROM mip.gold.evidence_events "
        "WHERE clip = :clip AND signal_type NOT IN ('permit','loan_type_fit')",
        {"clip": clip},
    )
    return int((row or {}).get("n") or 0)
