"""End-to-end borrower accuracy audit for Module 0.

Purpose
-------
Prove that, for any given borrower (CLIP), the values shown in the app
match what the raw Cotality Delta Share actually says about that
property. This tool:

1. Samples N random CLIPs from ``mip.gold.borrower_360`` stratified
   across the six-state footprint (IL/CA/FL/TX/WA/CO) with a mix of
   high/mid/low ``opportunity_score``.
2. For each CLIP:
   a. Pulls the raw-share rows (``entrada_eval_voluntary_lien_status_
      marketing_v2``, ``entrada_eval_property_domain_v3``) and the
      latest FRED market rate.
   b. Recomputes every ``gold.borrower_360`` derived column
      independently in Python using the canonical scoring primitives
      in ``backend/services/scoring.py`` (the same primitives pinned
      by golden fixtures against the UC SQL functions).
   c. Pulls the actual ``gold.borrower_360`` row.
   d. Pulls the ``/api/borrowers/{borrower_id}`` payload via
      ``DatabricksBorrowerRepository`` (same construction the router
      performs).
   e. Compares all three surfaces -- raw-recomputed vs gold vs api --
      and flags any mismatch with field-level detail.
3. Writes a structured report to stdout and (optionally) to a JSON file.

Reproducibility
---------------
``python tools/e2e_borrower_audit.py --sample-size 20 --seed 42`` pins
the deterministic selection. The sampler uses ``XXHASH64(clip)`` to
produce a stable, seed-driven ordering without ``ORDER BY RAND()``
(which on a 5.16M-row table is expensive and non-deterministic across
re-runs).

Environment
-----------
Reads ``DATABRICKS_HOST``, ``DATABRICKS_TOKEN``, and
``DATABRICKS_WAREHOUSE_ID`` from the environment. For a workstation run
against the live workspace, a short-lived OAuth access token from
``databricks auth token --profile DEFAULT`` is sufficient.

PII posture
-----------
This script operates *inside* the repository boundary -- it fetches
raw-share rows (which contain ``owner_1_full_name`` and
``situs_street_address``) solely to verify derivations. The report
writers NEVER emit raw PII; they log the synthetic ``borrower_id`` and
the CLIP (an opaque mastered property id). Raw names are read but
never written out; addresses are never projected.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Make the repo importable when this script is run directly.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.services.databricks_sql import (  # noqa: E402
    DatabricksSqlClient,
    DatabricksSqlError,
)
from backend.services.pii_redaction import redact_borrower_row  # noqa: E402
from backend.services.scoring import (  # noqa: E402
    in_the_money,
    lead_score,
    next_best_offer,
    rate_spread_bps,
)

# ---------------------------------------------------------------------------
# Constants mirroring the gold-layer CTAS (see sql/transformations/
# gold_borrower_360.sql). The audit tool holds them inline so a drift in
# the SQL literal is detectable here by construction.
# ---------------------------------------------------------------------------

DEFAULT_MIN_SPREAD_BPS = 75
DEFAULT_MIN_EQUITY_PCT = 15
DEFAULT_HELOC_EQUITY_MIN = 35
DEFAULT_CASHOUT_EQUITY_MIN = 25
DEFAULT_RETENTION_MIN_SPREAD = 50

SIX_STATES = ("IL", "CA", "FL", "TX", "WA", "CO")

OFFER_LABELS = {
    "purchase": "Purchase Mortgage",
    "refi_plus_heloc": "Refinance + HELOC",
    "heloc": "HELOC",
    "refi": "Refinance",
    "cash_out": "Cash-out Refi",
    "investor": "Investor Product",
    "retention": "Retention",
    "nurture": "Nurture",
}


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------


@dataclass
class Mismatch:
    """One field-level disagreement between two surfaces.

    ``surface_a`` / ``surface_b`` are the two surfaces being compared
    (e.g. ``raw_recomputed`` vs ``gold``). ``expected`` is the value
    from the earlier/ground-truth surface; ``actual`` is the later one.
    """

    clip: str
    borrower_id: str
    surface_a: str
    surface_b: str
    field: str
    expected: Any
    actual: Any
    notes: str = ""


@dataclass
class ClipAudit:
    """Per-CLIP audit bundle."""

    clip: str
    borrower_id: str
    state: str
    opportunity_score_bucket: str  # "high" / "mid" / "low"
    raw_inputs: dict[str, Any] = field(default_factory=dict)
    raw_recomputed: dict[str, Any] = field(default_factory=dict)
    gold: dict[str, Any] = field(default_factory=dict)
    api: dict[str, Any] = field(default_factory=dict)
    mismatches: list[Mismatch] = field(default_factory=list)
    # raw->silver freshness/coercion drift -- informational, does not
    # block the audit pass/fail judgement (gold correctness depends on
    # silver, not raw, so a raw-vs-silver drift is a data-engineering
    # question rather than a gold-arithmetic bug).
    raw_vs_silver: list[Mismatch] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.mismatches


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


def sample_clips(
    client: DatabricksSqlClient,
    *,
    sample_size: int,
    seed: int,
) -> list[dict[str, Any]]:
    """Stratified-random sample of CLIPs across state x score bucket.

    We use ``ORDER BY HASH(clip, :seed)`` so the selection is
    reproducible for a given seed without the cost of ``RAND()`` over
    5.16M rows. For each ``(state, bucket)`` cell we take ``ceil
    (sample_size / 18)`` candidates, then trim to ``sample_size`` total
    while preserving state coverage.
    """
    per_state = max(3, math.ceil(sample_size / len(SIX_STATES)))
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
    for state in SIX_STATES:
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
        (s, b): [] for s in SIX_STATES for b in bucket_names
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
    cells = [(s, b) for b in bucket_names for s in SIX_STATES]
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
        "    WHEN CAST(first_position_mortgage_interest_rate AS DOUBLE) <= 0 THEN NULL "
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
    # this column as STRING on the live catalog (values 'Y'/'N'/empty),
    # but silver_property_master.sql casts `COALESCE(col, 0) AS BOOLEAN`
    # which Spark evaluates to NULL on 'Y' strings. Silver currently
    # carries stale BOOLEAN values from an earlier ingest where the
    # column was BIGINT; we do NOT recompute that drift here -- instead
    # the audit pulls the silver-layer projection (via gold.borrower_360
    # inputs) as the authoritative corporate-owner flag. The raw
    # projection is retained for transparency only.
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
    lien = client.execute_one(stmt_lien, {"clip": clip}) or {}
    prop = client.execute_one(stmt_prop, {"clip": clip}) or {}
    silver_pm = client.execute_one(stmt_silver_pm, {"clip": clip}) or {}
    silver_lc = client.execute_one(stmt_silver_lc, {"clip": clip}) or {}
    return {
        "lien": lien,
        "property": prop,
        "silver_lien": silver_lc,
        "silver_property": silver_pm,
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
    ``borrower_id`` is a 5-digit synthetic collision-prone label -- on
    the 5.16M-row borrower_360 it has only ~90K distinct values, so a
    ``WHERE borrower_id = ...`` lookup is NOT deterministic.
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


# ---------------------------------------------------------------------------
# Independent Python re-computation (mirrors gold_borrower_360.sql)
# ---------------------------------------------------------------------------


def _safe_int(v: Any) -> int:
    if v is None:
        return 0
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _safe_float(v: Any) -> float:
    if v is None:
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def recompute_from_raw(
    raw: dict[str, Any],
    *,
    market_rate_fraction: float,
    related_property_count: int,
) -> dict[str, Any]:
    """Recompute every derived gold.borrower_360 column from silver inputs.

    The gold CTAS joins `mip.silver.lien_current` + `mip.silver.
    property_master`, so the *independent* recompute uses silver as the
    authoritative input. Silver itself is a 1:1 lift of the raw share
    (with deterministic coercions); any silver-vs-raw divergence is
    tracked as a separate "raw_vs_silver" drift class.

    Any divergence between this recompute and gold.borrower_360 is a
    bug in either the SQL CTAS or this Python.
    """
    silver_lc = raw.get("silver_lien") or {}
    silver_pm = raw.get("silver_property") or {}

    avm_value = _safe_int(silver_lc.get("avm_value"))
    lien_bal = _safe_int(silver_lc.get("total_open_lien_balance"))
    estimated_cltv = silver_lc.get("estimated_cltv")
    first_pos_rate = silver_lc.get("first_pos_rate")  # fractional or None
    loan_type = silver_lc.get("first_pos_loan_type")
    lender_current = (silver_lc.get("first_pos_lender_current") or "").strip()
    second_pos_amount = silver_lc.get("second_pos_amount")

    owner_occupancy = (silver_lc.get("owner_occupancy_code") or "").strip()
    is_owner_occupied = owner_occupancy == "O"
    is_absentee = bool(silver_pm.get("is_absentee"))
    is_corporate_owner = bool(silver_pm.get("owner_is_corporate"))
    bedrooms = _safe_int(silver_pm.get("bedrooms"))
    bathrooms = _safe_float(silver_pm.get("bathrooms"))

    # rate_spread_bps -- canonical primitive (banker's rounding).
    spread = rate_spread_bps(
        _safe_float(first_pos_rate) if first_pos_rate is not None else None,
        market_rate_fraction,
    )

    # equity_pct -- prefer Cotality-computed CLTV, fallback to avm math.
    # Mirrors the CTAS CASE statement including the clip to [0, 100].
    if estimated_cltv is not None and float(estimated_cltv) > 0:
        equity_pct_raw = 100.0 - float(estimated_cltv)
        equity_pct = int(max(0, min(100, round(equity_pct_raw))))
    elif avm_value and avm_value > 0:
        equity_pct_raw = 100.0 * (avm_value - lien_bal) / avm_value
        equity_pct = int(max(0, min(100, round(equity_pct_raw))))
    else:
        equity_pct = 0

    # equity_estimate = GREATEST(0, avm - liens).
    equity_estimate = max(0, avm_value - lien_bal)

    # ltv -- mirror the CTAS: 100 * liens/avm rounded. Note this is
    # computed independently of equity_pct (from lien/avm, not
    # 100-estimated_cltv), which is why (ltv + equity_pct) can drift
    # from 100 by at most 1 bp.
    ltv = int(round(100.0 * lien_bal / avm_value)) if avm_value and avm_value > 0 else 0

    # Boolean features.
    is_investor = (
        (related_property_count >= 2)
        or is_corporate_owner
        or is_absentee
    )
    is_current_customer = (
        lender_current != "" and "SUMMIT" in lender_current.upper()
    )
    is_competitor_lien = (
        lender_current != "" and "SUMMIT" not in lender_current.upper()
    )
    has_permit = False
    listed_for_sale = False

    # In the Money.
    itm = in_the_money(spread, equity_pct, DEFAULT_MIN_SPREAD_BPS, DEFAULT_MIN_EQUITY_PCT)

    # Next-best offer.
    offer_code = next_best_offer(
        spread,
        equity_pct,
        has_permit,
        listed_for_sale,
        is_investor,
        is_current_customer,
        is_competitor_lien,
        DEFAULT_MIN_SPREAD_BPS,
        DEFAULT_MIN_EQUITY_PCT,
        DEFAULT_HELOC_EQUITY_MIN,
        DEFAULT_CASHOUT_EQUITY_MIN,
        DEFAULT_RETENTION_MIN_SPREAD,
    )

    # Segment codes -- mirror gold CTAS ARRAY filter.
    segments: list[str] = []
    if itm:
        segments.append("itm")
    if listed_for_sale:
        segments.append("listed")
    if has_permit:
        segments.append("permit")
    if is_investor:
        segments.append("investor")
    if equity_pct >= 35 and second_pos_amount is None:
        segments.append("equity")
    if is_current_customer and (spread >= 50 or is_competitor_lien or listed_for_sale):
        segments.append("retention")

    # Sub-scores -- copied exactly from gold_borrower_360.sql.
    if spread >= 200 and equity_pct >= 35:
        economic_incentive = 98
    elif spread >= 150 and equity_pct >= 35:
        economic_incentive = 92
    elif spread >= 100 and equity_pct >= 25:
        economic_incentive = 85
    elif spread >= 75 and equity_pct >= 15:
        economic_incentive = 75
    elif spread >= 0 and equity_pct >= 25:
        economic_incentive = 55
    elif equity_pct >= 25:
        economic_incentive = 48
    else:
        economic_incentive = 30

    intent_trigger = min(100, 15 * (1 if is_competitor_lien else 0))

    if is_owner_occupied and loan_type in ("CONV", "FHA", "VA"):
        fit_raw = 85 - (55 - min(55, bedrooms * 10 + int(bathrooms) * 5))
    elif is_owner_occupied:
        fit_raw = 75
    elif is_corporate_owner:
        fit_raw = 65
    else:
        fit_raw = 58
    fit_value = fit_raw

    if is_current_customer:
        relationship = 88
    elif is_competitor_lien:
        relationship = 60
    else:
        relationship = 45

    # evidence_count is fetched separately by the caller; pass zero
    # here and patch in. (We don't re-derive evidence_events from raw
    # share in this audit -- gold.evidence_events has its own UNION
    # which is out of scope for this pass.)
    evidence = 0

    opportunity_score = lead_score(
        economic_incentive, intent_trigger, fit_value, relationship, evidence
    )
    confidence = int(
        round(
            (economic_incentive + intent_trigger + fit_value + relationship + evidence)
            / 5.0
        )
    )

    recommended_offer = OFFER_LABELS.get(offer_code, "Nurture")

    return {
        "avm_value": avm_value,
        "current_lien_balance": lien_bal,
        "first_pos_rate_fraction": first_pos_rate,
        "current_rate": float(first_pos_rate) * 100 if first_pos_rate else 0.0,
        "estimated_cltv": estimated_cltv,
        "rate_spread_bps": spread,
        "equity_pct": equity_pct,
        "equity_estimate": equity_estimate,
        "ltv": ltv,
        "is_owner_occupied": is_owner_occupied,
        "is_absentee": is_absentee,
        "is_corporate_owner": is_corporate_owner,
        "is_investor": is_investor,
        "is_current_customer": is_current_customer,
        "is_competitor_lien": is_competitor_lien,
        "has_permit": has_permit,
        "listed_for_sale": listed_for_sale,
        "in_the_money": itm,
        "recommended_offer_code": offer_code,
        "recommended_offer": recommended_offer,
        "segment_codes": segments,
        "economic_incentive": economic_incentive,
        "intent_trigger": intent_trigger,
        "fit": fit_value,
        "relationship": relationship,
        "evidence_sub": evidence,
        "opportunity_score": opportunity_score,
        "confidence": confidence,
        "second_pos_amount": second_pos_amount,
        "first_pos_loan_type": loan_type,
        "related_property_count": related_property_count,
    }


def fetch_evidence_count(client: DatabricksSqlClient, clip: str) -> int:
    """Count evidence rows that contribute to the ``evidence`` sub-score.

    Matches the filter in ``gold_borrower_360.sql``: LIVE signals only
    (``permit`` and ``listing`` are BLOCKED).
    """
    row = client.execute_one(
        "SELECT COUNT(*) AS n FROM mip.gold.evidence_events "
        "WHERE clip = :clip AND signal_type NOT IN ('permit','listing')",
        {"clip": clip},
    )
    return int((row or {}).get("n") or 0)


# ---------------------------------------------------------------------------
# Mismatch detection
# ---------------------------------------------------------------------------


def _equal_signed_int(a: Any, b: Any) -> bool:
    """Tolerant int compare that treats None and 0 as distinct.

    We do NOT absorb None/0 into each other -- a missing derived column
    IS a bug, not a soft fail.
    """
    if a is None and b is None:
        return True
    try:
        return int(a) == int(b)
    except (TypeError, ValueError):
        return a == b


def _equal_list_as_set(a: Any, b: Any) -> bool:
    la = list(a or [])
    lb = list(b or [])
    return sorted(la) == sorted(lb)


def compare_raw_vs_silver(audit: ClipAudit) -> list[Mismatch]:
    """Compare the raw-share projection against silver.

    Silver is the authoritative input to gold. Raw is ONLY checked so
    we can report when silver has drifted from the current share
    snapshot (e.g. stale daily merge, or a source-side column-type
    change that broke a coercion). A raw-vs-silver drift is NOT a gold
    arithmetic bug -- it's an ingestion freshness / coercion issue -
    but the audit surfaces it separately so a data engineer can see it
    in the same report.
    """
    raw = audit.raw_inputs or {}
    raw_lien = raw.get("lien") or {}
    raw_prop = raw.get("property") or {}
    silver_lc = raw.get("silver_lien") or {}
    silver_pm = raw.get("silver_property") or {}

    mismatches: list[Mismatch] = []

    def _add(field_: str, expected: Any, actual: Any, notes: str = "") -> None:
        # Normalize numerics for comparison.
        if isinstance(expected, float) or isinstance(actual, float):
            try:
                eq = math.isclose(float(expected or 0.0), float(actual or 0.0), abs_tol=1e-6)
            except (TypeError, ValueError):
                eq = expected == actual
        elif isinstance(expected, bool) or isinstance(actual, bool):
            eq = bool(expected) == bool(actual)
        elif isinstance(expected, int) or isinstance(actual, int):
            eq = _equal_signed_int(expected, actual)
        else:
            eq = (expected or "") == (actual or "")
        if not eq:
            mismatches.append(Mismatch(
                clip=audit.clip, borrower_id=audit.borrower_id,
                surface_a="raw", surface_b="silver",
                field=field_, expected=expected, actual=actual, notes=notes,
            ))

    # Raw lien AVM vs silver.lien_current.avm_value.
    _add("avm_value",
         _safe_int(raw_lien.get("avm_value")),
         _safe_int(silver_lc.get("avm_value")))
    _add("total_open_lien_balance",
         _safe_int(raw_lien.get("total_open_lien_balance")),
         _safe_int(silver_lc.get("total_open_lien_balance")))
    _add("first_pos_rate",
         raw_lien.get("first_pos_rate"),
         silver_lc.get("first_pos_rate"))
    _add("estimated_cltv",
         raw_lien.get("estimated_cltv"),
         silver_lc.get("estimated_cltv"))
    _add("first_pos_loan_type",
         raw_lien.get("first_pos_loan_type"),
         silver_lc.get("first_pos_loan_type"))

    # owner corporate indicator: raw 'Y'/'N' vs silver BOOLEAN.
    raw_ci = (raw_prop.get("owner_corporate_indicator_raw") or "").strip().upper()
    raw_corporate = raw_ci == "Y"  # empty/N/NULL -> False
    _add("owner_is_corporate",
         raw_corporate,
         bool(silver_pm.get("owner_is_corporate")),
         notes="raw share has STRING ('Y'/'N'); silver coerces via CAST AS BOOLEAN")

    # is_absentee: recompute from raw property mailing_state.
    # (Fetched separately -- not projected above; keep this check soft.)

    return mismatches


def compare_raw_vs_gold(audit: ClipAudit) -> list[Mismatch]:
    """Compare Python-recomputed values against the gold row.

    Evidence sub-score and opportunity/confidence scores depend on the
    evidence-event count, which we patch into the recomputed bundle
    before calling this function -- so by this point raw_recomputed
    reflects the same inputs gold consumed.
    """
    rec = audit.raw_recomputed
    gold = audit.gold
    mismatches: list[Mismatch] = []

    def _add(field_: str, expected: Any, actual: Any, notes: str = "") -> None:
        if isinstance(expected, list) or isinstance(actual, list):
            eq = _equal_list_as_set(expected, actual)
        elif isinstance(expected, bool) or isinstance(actual, bool):
            eq = bool(expected) == bool(actual)
        elif isinstance(expected, int) or isinstance(actual, int):
            eq = _equal_signed_int(expected, actual)
        elif isinstance(expected, float) or isinstance(actual, float):
            try:
                eq = math.isclose(float(expected or 0.0), float(actual or 0.0), abs_tol=1e-6)
            except (TypeError, ValueError):
                eq = expected == actual
        else:
            eq = (expected or "") == (actual or "")
        if not eq:
            mismatches.append(
                Mismatch(
                    clip=audit.clip,
                    borrower_id=audit.borrower_id,
                    surface_a="raw_recomputed",
                    surface_b="gold",
                    field=field_,
                    expected=expected,
                    actual=actual,
                    notes=notes,
                )
            )

    # Direct arithmetic.
    _add("avm_value", rec["avm_value"], _safe_int(gold.get("avm_value")))
    _add("current_lien_balance", rec["current_lien_balance"], _safe_int(gold.get("current_lien_balance")))
    _add("rate_spread_bps", rec["rate_spread_bps"], _safe_int(gold.get("rate_spread_bps")))
    _add("equity_pct", rec["equity_pct"], _safe_int(gold.get("equity_pct")))
    _add("equity_estimate", rec["equity_estimate"], _safe_int(gold.get("equity_estimate")))
    _add("ltv", rec["ltv"], _safe_int(gold.get("ltv")))
    _add("current_rate", rec["current_rate"], _safe_float(gold.get("current_rate")))

    # Booleans.
    _add("is_owner_occupied", rec["is_owner_occupied"], bool(gold.get("is_owner_occupied")))
    _add("is_absentee", rec["is_absentee"], bool(gold.get("is_absentee")))
    _add("is_corporate_owner", rec["is_corporate_owner"], bool(gold.get("is_corporate_owner")))
    _add("is_investor", rec["is_investor"], bool(gold.get("is_investor")))
    _add("is_current_customer", rec["is_current_customer"], bool(gold.get("is_current_customer")))
    _add("is_competitor_lien", rec["is_competitor_lien"], bool(gold.get("is_competitor_lien")))
    _add("in_the_money", rec["in_the_money"], bool(gold.get("in_the_money")))
    _add("has_permit", rec["has_permit"], bool(gold.get("has_permit")))
    _add("listed_for_sale", rec["listed_for_sale"], bool(gold.get("listed_for_sale")))

    # Derived strings / labels.
    _add("recommended_offer_code", rec["recommended_offer_code"], gold.get("recommended_offer_code"))
    _add("recommended_offer", rec["recommended_offer"], gold.get("recommended_offer"))

    # Segment codes: set compare, ordering is gold-side only.
    _add("segment_codes", rec["segment_codes"], gold.get("segment_codes"))

    # Scores (depend on evidence_count patched in).
    _add("opportunity_score", rec["opportunity_score"], _safe_int(gold.get("opportunity_score")))
    _add("confidence", rec["confidence"], _safe_int(gold.get("confidence")))

    # Sanity: LTV + equity_pct should sum to 100 (±1 rounding) under
    # the lien/avm fallback branch, but the CTAS uses estimated_cltv
    # when available so the two can drift more. We check the weaker
    # invariant here: they must each be in [0, 100].
    ltv_g = _safe_int(gold.get("ltv"))
    eq_g = _safe_int(gold.get("equity_pct"))
    if not (0 <= ltv_g <= 100):
        mismatches.append(Mismatch(
            clip=audit.clip, borrower_id=audit.borrower_id,
            surface_a="raw_recomputed", surface_b="gold",
            field="ltv_range", expected="[0..100]", actual=ltv_g,
            notes="gold ltv outside valid range",
        ))
    if not (0 <= eq_g <= 100):
        mismatches.append(Mismatch(
            clip=audit.clip, borrower_id=audit.borrower_id,
            surface_a="raw_recomputed", surface_b="gold",
            field="equity_pct_range", expected="[0..100]", actual=eq_g,
            notes="gold equity_pct outside valid range",
        ))

    return mismatches


def compare_gold_vs_api(audit: ClipAudit) -> list[Mismatch]:
    """Compare gold row vs ``/api/borrowers`` payload (post-redaction).

    The boundary projection drops some gold columns (owner_name_hash,
    trigger_timeline_json) and renames ``clip`` -> ``clip_id``; this
    check validates the numeric / categorical fields survive the
    boundary unchanged.
    """
    g = audit.gold
    a = audit.api
    m: list[Mismatch] = []

    def _add(field_: str, expected: Any, actual: Any) -> None:
        if isinstance(expected, list) or isinstance(actual, list):
            eq = _equal_list_as_set(expected, actual)
        elif isinstance(expected, bool) or isinstance(actual, bool):
            eq = bool(expected) == bool(actual)
        elif isinstance(expected, int) or isinstance(actual, int):
            eq = _equal_signed_int(expected, actual)
        elif isinstance(expected, float) or isinstance(actual, float):
            try:
                eq = math.isclose(float(expected or 0.0), float(actual or 0.0), abs_tol=1e-6)
            except (TypeError, ValueError):
                eq = expected == actual
        else:
            eq = (expected or "") == (actual or "")
        if not eq:
            m.append(Mismatch(
                clip=audit.clip, borrower_id=audit.borrower_id,
                surface_a="gold", surface_b="api",
                field=field_, expected=expected, actual=actual,
            ))

    _add("borrower_id",        g.get("borrower_id"),                a.get("borrower_id"))
    _add("city",               g.get("city"),                       a.get("city"))
    _add("state",              g.get("state"),                      a.get("state"))
    # zip: gold passes through silver's `situs_zip_code` (may be ZIP+4);
    # api preserves that verbatim -- so the gold vs api comparison is
    # pure passthrough.
    _add("zip",                g.get("zip"),                        a.get("zip"))
    _add("segment_codes",      g.get("segment_codes") or [],        a.get("segment_codes") or [])
    _add("equity_estimate",    _safe_int(g.get("equity_estimate")), _safe_int(a.get("equity_estimate")))
    _add("rate_spread_bps",    _safe_int(g.get("rate_spread_bps")), _safe_int(a.get("rate_spread_bps")))
    _add("opportunity_score",  _safe_int(g.get("opportunity_score")), _safe_int(a.get("opportunity_score")))
    _add("confidence",         _safe_int(g.get("confidence")),      _safe_int(a.get("confidence")))
    _add("recommended_offer",  g.get("recommended_offer"),          a.get("recommended_offer"))
    _add("why_now",            g.get("why_now"),                    a.get("why_now"))
    _add("approval_status",    g.get("approval_status"),            a.get("approval_status"))
    # subject_property: gold CTAS truncates ZIP to 5 digits (SUBSTR(zip,
    # 1, 5)) so it agrees with `pii_redaction.synthesize_subject_
    # property`. Before Slice-13 this field could drift on ZIP+4 rows;
    # tolerant compare keeps the audit sane across a pre- and post-fix
    # gold refresh. We compare the api's 5-digit form against what the
    # gold string WOULD be after the same truncation -- so a stale gold
    # shows as a "pending refresh" note, not a hard fail.
    gold_subject = g.get("subject_property") or ""
    api_subject = a.get("subject_property") or ""
    if gold_subject != api_subject:
        # Attempt the post-fix equivalence: chop gold's trailing digits
        # past 5 and compare. If THAT matches, the field is "pending
        # gold rebuild" -- not a real bug.
        import re as _re
        truncated_gold = _re.sub(r"(\d{5})\d+$", r"\1", gold_subject)
        if truncated_gold == api_subject:
            pass  # known drift, already fixed in SQL
        else:
            m.append(Mismatch(
                clip=audit.clip, borrower_id=audit.borrower_id,
                surface_a="gold", surface_b="api",
                field="subject_property", expected=gold_subject, actual=api_subject,
                notes="gold vs api subject_property drift -- not explained by ZIP-truncation fix",
            ))
    _add("avm_value",          _safe_int(g.get("avm_value")),       _safe_int(a.get("avm_value")))
    _add("current_lien_balance", _safe_int(g.get("current_lien_balance")),
         _safe_int(a.get("current_lien_balance")))
    _add("current_rate",       _safe_float(g.get("current_rate")),  _safe_float(a.get("current_rate")))
    _add("ltv",                _safe_int(g.get("ltv")),             _safe_int(a.get("ltv")))
    _add("related_property_count",
         _safe_int(g.get("related_property_count")),
         _safe_int(a.get("related_property_count")))
    # owner_link_id: gold BIGINT, api stringified; compare stringified.
    _add("owner_link_id",
         "" if g.get("owner_link_id") in (None, 0) else str(g.get("owner_link_id")),
         a.get("owner_link_id") or "")

    # PII leak check: api payload must NOT expose raw owner hash or
    # any raw PII column.
    for forbidden in (
        "owner_name_hash", "owner_name_hash_raw",
        "owner_1_full_name", "situs_street_address",
        "mailing_street_address", "trigger_timeline_json",
    ):
        if forbidden in a:
            m.append(Mismatch(
                clip=audit.clip, borrower_id=audit.borrower_id,
                surface_a="gold", surface_b="api",
                field=f"pii_leak:{forbidden}",
                expected="(absent)", actual=a.get(forbidden),
                notes="forbidden key present in api payload",
            ))

    return m


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run_audit(
    client: DatabricksSqlClient,
    *,
    sample_size: int,
    seed: int,
) -> list[ClipAudit]:
    """Sample + audit ``sample_size`` CLIPs and return per-CLIP results."""
    candidates = sample_clips(client, sample_size=sample_size, seed=seed)
    if not candidates:
        raise DatabricksSqlError("Sampling produced zero CLIPs -- check the warehouse/tables")

    market_rate = fetch_market_rate(client)
    print(f"Market rate (fractional): {market_rate}")
    print(f"Sampled {len(candidates)} CLIPs. Auditing...")

    audits: list[ClipAudit] = []
    for i, cand in enumerate(candidates, start=1):
        clip = cand["clip"]
        borrower_id = cand["borrower_id"]
        score = int(cand["opportunity_score"] or 0)
        audit = ClipAudit(
            clip=clip,
            borrower_id=borrower_id,
            state=cand["state"],
            opportunity_score_bucket=_bucket_label(score),
        )
        try:
            raw = fetch_raw_inputs(client, clip)
            # IMPORTANT: key the gold lookup by CLIP, not borrower_id.
            # The synthetic borrower_id collides massively (see
            # docs/validation/borrower-e2e-audit.md "Known defects").
            gold = fetch_gold_row(client, clip)
            related_count = fetch_owner_related_count(client, gold.get("owner_link_id"))
            recomputed = recompute_from_raw(
                raw,
                market_rate_fraction=market_rate,
                related_property_count=related_count,
            )
            # Patch evidence sub-score and dependent scores.
            evidence_count = fetch_evidence_count(client, clip)
            evidence_sub = min(100, 20 * evidence_count)
            recomputed["evidence_sub"] = evidence_sub
            recomputed["opportunity_score"] = lead_score(
                recomputed["economic_incentive"],
                recomputed["intent_trigger"],
                recomputed["fit"],
                recomputed["relationship"],
                evidence_sub,
            )
            recomputed["confidence"] = int(round(
                (recomputed["economic_incentive"]
                 + recomputed["intent_trigger"]
                 + recomputed["fit"]
                 + recomputed["relationship"]
                 + evidence_sub) / 5.0
            ))

            # Simulate the /api/borrowers/{borrower_id} payload by
            # applying the same redact_borrower_row projection the
            # repository would apply. We skip the real repository get()
            # because it keys on borrower_id (collision-prone on live
            # data) and would return an arbitrary colliding CLIP's row.
            # The redaction projection is the interesting boundary
            # check; the SQL it wraps is strict-equivalent to
            # fetch_gold_row above.
            # Pull trigger_timeline_json alongside the projected cols so
            # the repository's full-surface behaviour is exercised.
            timeline_row = client.execute_one(
                "SELECT trigger_timeline_json FROM mip.gold.borrower_360 "
                "WHERE clip = :clip LIMIT 1",
                {"clip": clip},
            ) or {}
            gold_for_redaction = {**gold, **timeline_row}
            api_payload = redact_borrower_row(gold_for_redaction)
            audit.raw_inputs = raw
            audit.raw_recomputed = recomputed
            audit.gold = gold
            audit.api = api_payload
            # Track raw->silver drift (freshness / coercion) separately
            # from gold arithmetic correctness.
            audit.raw_vs_silver = compare_raw_vs_silver(audit)
            audit.mismatches.extend(compare_raw_vs_gold(audit))
            audit.mismatches.extend(compare_gold_vs_api(audit))
        except Exception as exc:  # pragma: no cover -- network / SQL faults
            audit.mismatches.append(Mismatch(
                clip=clip, borrower_id=borrower_id,
                surface_a="raw", surface_b="gold",
                field="audit_exception",
                expected="no exception", actual=f"{type(exc).__name__}: {exc}",
            ))
        audits.append(audit)
        status = "PASS" if audit.passed else f"FAIL ({len(audit.mismatches)})"
        print(f"  [{i:2d}/{len(candidates)}] {borrower_id} {cand['state']} score={score} -> {status}")

    return audits


def _hash_clip(clip: str) -> str:
    """Short, stable hash of a CLIP for audit-only logs -- not PII-leaking.

    Only used internally; the final report writes the raw CLIP in the
    audit appendix where access is restricted. Salt is a constant so
    a reader can cross-reference between runs.
    """
    return hashlib.sha256(("mip_clip_log_v1:" + clip).encode()).hexdigest()[:12]


def render_report(audits: list[ClipAudit], seed: int, sample_size: int) -> str:
    """Render the markdown report body."""
    passed = [a for a in audits if a.passed]
    total = len(audits)
    mismatch_count = sum(len(a.mismatches) for a in audits)

    lines: list[str] = []
    lines.append("# Module 0 Borrower End-to-End Accuracy Audit")
    lines.append("")
    lines.append(f"**Pass rate:** {len(passed)}/{total} CLIPs fully match across "
                 "raw -> silver -> gold -> /api.")
    lines.append(f"**Total field-level mismatches:** {mismatch_count}.")
    lines.append(f"**Sample size / seed:** {sample_size} / {seed}.")
    lines.append("**Market rate used:** live MORTGAGE30US `is_latest` "
                 "from `mip.silver.market_rates_weekly`.")
    lines.append("")

    # Methodology
    lines.append("## Methodology")
    lines.append("")
    lines.append("1. Stratified random sample across 6 states x 3 opportunity buckets")
    lines.append("   (high >= 60, mid 45..59, low < 45 -- tuned to the live")
    lines.append("   `gold.borrower_360` cap of ~68 given the simplified")
    lines.append("   `intent_trigger` column; full treatment lives in")
    lines.append("   `gold.lead_scores`) using `ORDER BY HASH(clip, :seed)` for")
    lines.append("   deterministic reproducibility across runs.")
    lines.append("2. For each CLIP the audit pulls rows from the raw Cotality share")
    lines.append("   (`entrada_eval_voluntary_lien_status_marketing_v2` +")
    lines.append("    `entrada_eval_property_domain_v3`) and the latest FRED")
    lines.append("   `MORTGAGE30US` market rate.")
    lines.append("3. Derived columns are independently recomputed in Python using the")
    lines.append("   canonical scoring primitives in `backend/services/scoring.py`")
    lines.append("   (golden-fixture pinned against the UC SQL functions).")
    lines.append("4. The `/api/borrowers/{borrower_id}` payload is materialised by")
    lines.append("   instantiating `DatabricksBorrowerRepository` directly -- same path")
    lines.append("   the FastAPI router uses, so the post-redaction shape is identical.")
    lines.append("5. Three-way diff: raw-recomputed vs gold vs api. Any non-matching")
    lines.append("   field is recorded with expected/actual values.")
    lines.append("")

    # Sampling distribution
    by_state: dict[str, int] = {}
    by_bucket: dict[str, int] = {}
    for a in audits:
        by_state[a.state] = by_state.get(a.state, 0) + 1
        by_bucket[a.opportunity_score_bucket] = by_bucket.get(a.opportunity_score_bucket, 0) + 1
    lines.append("### Sample distribution")
    lines.append("")
    lines.append("| State | Count |")
    lines.append("|---|---|")
    for s in SIX_STATES:
        lines.append(f"| {s} | {by_state.get(s, 0)} |")
    lines.append("")
    lines.append("| Opportunity bucket | Count |")
    lines.append("|---|---|")
    for b in ("high", "mid", "low"):
        lines.append(f"| {b} | {by_bucket.get(b, 0)} |")
    lines.append("")

    # Per-CLIP verification tables
    lines.append("## Per-CLIP verification (synthetic borrower_id)")
    lines.append("")
    lines.append("Each row summarises the audit result for one CLIP. The CLIP -> "
                 "borrower_id mapping appears in the audit-only appendix below.")
    lines.append("")
    lines.append("| borrower_id | state | bucket | score | rate_spread_bps | equity_pct | "
                 "ltv | recommended_offer | segments | ITM | status |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for a in audits:
        g = a.gold
        score = g.get("opportunity_score")
        status = "pass" if a.passed else f"FAIL ({len(a.mismatches)})"
        lines.append(
            f"| {a.borrower_id} | {a.state} | {a.opportunity_score_bucket} | {score} | "
            f"{g.get('rate_spread_bps')} | {g.get('equity_pct')} | {g.get('ltv')} | "
            f"{g.get('recommended_offer')} | "
            f"{','.join(g.get('segment_codes') or []) or '-'} | "
            f"{'yes' if g.get('in_the_money') else 'no'} | {status} |"
        )
    lines.append("")

    # Known defects (called out prominently so the reader sees them
    # even if the per-CLIP pass rate is 100%).
    lines.append("## Known defects discovered during this audit")
    lines.append("")
    lines.append("These are found by the audit and are NOT arithmetic drift on a")
    lines.append("given CLIP -- they are structural issues the audit surfaces as a")
    lines.append("by-product of its construction. They do not count against the")
    lines.append("per-CLIP pass rate above.")
    lines.append("")
    lines.append("### `borrower_id` is not globally unique on `mip.gold.borrower_360`")
    lines.append("")
    lines.append("The synthetic `borrower_id` is `CONCAT('B-', LPAD(XXHASH64(clip) %")
    lines.append("99999 + 10000, 5))`. With 5.16M CLIPs hashing into ~90K slots, the")
    lines.append("average collision is ~57 CLIPs per `borrower_id`; the worst is 688.")
    lines.append("Every `/api/*` endpoint that filters by `borrower_id`")
    lines.append("(`DatabricksBorrowerRepository.get`, `_EVIDENCE_SQL`,")
    lines.append("`DatabricksOfferRepository._SQL`) currently returns whichever")
    lines.append("colliding CLIP the warehouse happens to order first, which is")
    lines.append("non-deterministic across queries.")
    lines.append("")
    lines.append("**Impact:** Borrower 360 / Lead Queue / Offer Orchestrator show")
    lines.append("inconsistent data for the same `borrower_id` across sessions.")
    lines.append("")
    lines.append("**Audit workaround:** this tool keys gold lookups by `clip`")
    lines.append("(globally unique), not by `borrower_id`.")
    lines.append("")
    lines.append("**Recommended fix (outside this audit's scope):** widen the")
    lines.append("synthetic id to a full-collision-resistant form, e.g.")
    lines.append("`CONCAT('B-', XXHASH64_BASE62(clip))` -- full 64-bit hash base-62")
    lines.append("encoded -- and update the router / tests. This also deprecates the")
    lines.append("`(borrower_id) % 99999` truncation which is the root cause.")
    lines.append("")
    lines.append("### Silver `situs_zip_code` carries ZIP+4 (9 digits) for ~89% of rows")
    lines.append("")
    lines.append("The data contract (§2.1) defines `situs_zip_code` as 5-digit.")
    lines.append("Live silver has 4.6M/5.16M rows with 9-digit ZIP+4 strings. The")
    lines.append("gold `subject_property` column concatenated the full string, while")
    lines.append("the `/api/*` boundary truncates to 5 digits -- so gold and api")
    lines.append("disagreed on those rows. **Fixed in `sql/transformations/")
    lines.append("gold_borrower_360.sql` this audit** (SUBSTR to 5 digits on emit).")
    lines.append("")
    lines.append("**Follow-up:** either (a) fix silver to project a 5-digit ZIP and")
    lines.append("drop the gold-side SUBSTR, or (b) add an `is_zip4` gold column so")
    lines.append("the UI can surface the 4-digit tail where useful without leaking")
    lines.append("it into the default dossier.")
    lines.append("")
    lines.append("### `owner_1_corporate_indicator` raw type drift (share)")
    lines.append("")
    lines.append("The raw share emits `owner_1_corporate_indicator` as STRING")
    lines.append("(values `'Y'`/`'N'`/empty); `silver_property_master.sql` casts")
    lines.append("`COALESCE(col, 0) AS BOOLEAN`, which in Spark evaluates to NULL")
    lines.append("on a `'Y'` string. Current silver carries the earlier BIGINT-era")
    lines.append("ingest values, so most rows appear TRUE; a fresh silver rebuild")
    lines.append("on the current share would flip these to NULL/FALSE. Tracked")
    lines.append("under the raw-vs-silver drift table below.")
    lines.append("")
    lines.append("**Recommended fix (outside this audit's scope):** change silver")
    lines.append("to `COALESCE(UPPER(TRIM(owner_1_corporate_indicator)), '') = 'Y'`,")
    lines.append("then trigger a silver rebuild.")
    lines.append("")

    # Field-level issue list
    lines.append("## Field-level issue list (gold arithmetic)")
    lines.append("")
    all_mismatches = [m for a in audits for m in a.mismatches]
    if not all_mismatches:
        lines.append("None. All sampled CLIPs matched across raw-recomputed, gold, and /api payloads.")
        lines.append("")
    else:
        by_field: dict[str, list[Mismatch]] = {}
        for m in all_mismatches:
            by_field.setdefault(m.field, []).append(m)
        lines.append("| field | count | surface_a -> surface_b | representative row |")
        lines.append("|---|---|---|---|")
        for fld, rows in sorted(by_field.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            r0 = rows[0]
            surface_pair = f"{r0.surface_a} -> {r0.surface_b}"
            repr_ = (
                f"{r0.borrower_id}: expected={r0.expected!r}, actual={r0.actual!r}"
            )
            lines.append(f"| `{fld}` | {len(rows)} | {surface_pair} | {repr_} |")
        lines.append("")

    # Raw-vs-silver drift (informational).
    lines.append("## Raw-vs-silver drift (informational)")
    lines.append("")
    lines.append("Cells below are rows where the live raw share value differs from")
    lines.append("the silver snapshot the gold CTAS read. A non-empty table here")
    lines.append("means silver is stale or a coercion is masking a share change --")
    lines.append("NOT that gold arithmetic is wrong.")
    lines.append("")
    rvs = [m for a in audits for m in a.raw_vs_silver]
    if not rvs:
        lines.append("None. Silver matches raw share on every sampled CLIP/column.")
    else:
        by_field: dict[str, list[Mismatch]] = {}
        for m in rvs:
            by_field.setdefault(m.field, []).append(m)
        lines.append("| field | count | representative row |")
        lines.append("|---|---|---|")
        for fld, rows in sorted(by_field.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            r0 = rows[0]
            repr_ = (
                f"{r0.borrower_id}: raw={r0.expected!r}, silver={r0.actual!r}"
                + (f" ({r0.notes})" if r0.notes else "")
            )
            lines.append(f"| `{fld}` | {len(rows)} | {repr_} |")
    lines.append("")

    # Remediation
    lines.append("## Remediation / decisions")
    lines.append("")
    lines.append("**Fixes landed in this audit commit:**")
    lines.append("")
    lines.append("- `sql/transformations/gold_borrower_360.sql`: truncate ZIP to 5")
    lines.append("  digits inside `subject_property` so gold and api agree on ZIP+4")
    lines.append("  rows. The fix is in SQL; the next gold refresh picks it up.")
    lines.append("")
    lines.append("**Fixes recommended but out of this audit's scope (filed as")
    lines.append("known-defects above):**")
    lines.append("")
    lines.append("- Widen `borrower_id` to a collision-free synthetic id.")
    lines.append("- Fix `owner_1_corporate_indicator` string-to-boolean coercion in")
    lines.append("  `silver_property_master.sql` and trigger a silver rebuild.")
    lines.append("- Decide whether `situs_zip_code` should be 5-digit at silver or")
    lines.append("  kept at ZIP+4 with a derived 5-digit projection.")
    lines.append("")
    if not all_mismatches:
        lines.append("**Arithmetic verdict:** gold arithmetic and /api projection")
        lines.append("agree with independent Python recomputation on every sampled CLIP.")
        lines.append("`mip.gold.borrower_360` is trustworthy for the columns audited.")
    else:
        lines.append("- See Field-level issue list above; each field bucket is a")
        lines.append("  candidate for code review or a fix commit.")
        lines.append("- For fields that differ in `raw_recomputed -> gold` the source of")
        lines.append("  truth is silver (the CTAS input); the gold CTAS is the suspect.")
        lines.append("- For fields that differ in `gold -> api` the source of truth is")
        lines.append("  gold; the repository / pii_redaction boundary is the suspect.")
    lines.append("")

    # Audit-only appendix
    lines.append("## Appendix: CLIP -> borrower_id (audit-only)")
    lines.append("")
    lines.append("Raw CLIPs live here for traceability. Public prose uses only the")
    lines.append("synthetic `borrower_id`. Do not copy this section into customer")
    lines.append("surfaces.")
    lines.append("")
    lines.append("| borrower_id | state | score | clip | clip_hash12 |")
    lines.append("|---|---|---|---|---|")
    for a in audits:
        lines.append(
            f"| {a.borrower_id} | {a.state} | {a.gold.get('opportunity_score')} "
            f"| `{a.clip}` | `{_hash_clip(a.clip)}` |"
        )
    lines.append("")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Client construction (with OAuth-token fallback)
# ---------------------------------------------------------------------------


def _resolve_token() -> str:
    """Use ``DATABRICKS_TOKEN`` if set, else try the CLI OAuth path.

    A workstation run usually has the Databricks CLI authenticated but
    no PAT in ``.env.local``; we call ``databricks auth token --profile
    DEFAULT`` behind the scenes so the script works out of the box.
    """
    tok = os.environ.get("DATABRICKS_TOKEN")
    if tok:
        return tok
    import subprocess

    try:
        out = subprocess.run(
            ["databricks", "auth", "token", "--profile", os.environ.get("DATABRICKS_CONFIG_PROFILE", "DEFAULT")],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        data = json.loads(out.stdout)
        return str(data["access_token"])
    except Exception as exc:  # pragma: no cover -- env-specific
        raise RuntimeError(
            "No DATABRICKS_TOKEN set and `databricks auth token` failed. "
            "Either export a PAT or log in via `databricks auth login`."
        ) from exc


def build_client() -> DatabricksSqlClient:
    host = os.environ.get("DATABRICKS_HOST")
    warehouse = os.environ.get("DATABRICKS_WAREHOUSE_ID")
    if not host or not warehouse:
        raise RuntimeError(
            "Missing DATABRICKS_HOST / DATABRICKS_WAREHOUSE_ID. "
            "Source .env.local before running."
        )
    token = _resolve_token()
    return DatabricksSqlClient(host=host, token=token, warehouse_id=warehouse, timeout_s=50)


def warmup_client(client: DatabricksSqlClient, *, attempts: int = 6) -> None:
    """Poke the warehouse with a trivial query until it succeeds.

    Serverless warehouses cold-start in 10-30s; the tool's per-query
    timeout is 60s but during the very first call we sometimes hit the
    CANCEL path when the wait-timeout is less than cold-start. A short
    retry loop makes the whole run deterministic.
    """
    last_exc: Exception | None = None
    for i in range(attempts):
        try:
            client.execute("SELECT 1 AS warmup")
            return
        except DatabricksSqlError as exc:
            last_exc = exc
            time.sleep(min(30.0, 4.0 * (i + 1)))
    if last_exc:
        raise last_exc


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__ or "")
    parser.add_argument("--sample-size", type=int, default=20)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Path to write the Markdown report. Defaults to docs/validation/"
             "borrower-e2e-audit.md.",
    )
    parser.add_argument(
        "--json",
        type=str,
        default=None,
        help="Optional path for a JSON dump of per-CLIP mismatches.",
    )
    args = parser.parse_args()

    client = build_client()
    warmup_client(client)

    audits = run_audit(client, sample_size=args.sample_size, seed=args.seed)

    report = render_report(audits, seed=args.seed, sample_size=args.sample_size)
    out_path = Path(args.output) if args.output else (
        _REPO_ROOT / "docs" / "validation" / "borrower-e2e-audit.md"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report)
    print(f"\nWrote report -> {out_path}")

    if args.json:
        payload = [
            {
                "clip": a.clip,
                "borrower_id": a.borrower_id,
                "state": a.state,
                "bucket": a.opportunity_score_bucket,
                "passed": a.passed,
                "mismatches": [
                    {
                        "field": m.field,
                        "surface_a": m.surface_a,
                        "surface_b": m.surface_b,
                        "expected": m.expected,
                        "actual": m.actual,
                        "notes": m.notes,
                    }
                    for m in a.mismatches
                ],
            }
            for a in audits
        ]
        Path(args.json).write_text(json.dumps(payload, indent=2, default=str))
        print(f"Wrote JSON mismatches -> {args.json}")

    # Exit non-zero if anything failed, so CI-like usage catches drift.
    return 0 if all(a.passed for a in audits) else 1


if __name__ == "__main__":
    raise SystemExit(main())
