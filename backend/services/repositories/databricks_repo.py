"""Live Databricks-backed repository implementations.

Each class below implements one Protocol from
``backend.services.repositories.protocols`` against the ``mip.gold.*``
tables materialised by the Slice-3 Lakeflow pipeline. The constructor
takes a ``DatabricksSqlClient`` so the same pool/keep-alive is reused
across every repository in the process.

PII posture (non-negotiable, enforced by ``backend/services/pii_redaction``):

- No raw owner names, mailing addresses, or street addresses leave
  these classes.
- The ``clip`` -> ``clip_id`` and ``delta_vs_prior`` -> ``delta`` renames
  documented in ``docs/data-contract-module0.md`` §12 happen here, at
  the repository boundary, not in the SQL view and not in the router.
- Every ``SELECT`` lists columns explicitly -- never ``SELECT *`` -- so
  a future gold schema expansion cannot silently surface new PII.
- Every WHERE clause uses named / positional parameters, never string
  interpolation. A caller-supplied borrower id is always bound.

Evidence ordering: ``ORDER BY signal_rank ASC`` per the data-contract
§3.4. Chronological order is a display concern handled in the UI; the
canonical order for the evidence drawer is the gold-defined priority.

Slice-7+: ``DatabricksGenieRepository`` serves ``/api/genie`` from the
real Mortgage Lead Intelligence Genie space. If the ``genie`` circuit
breaker is OPEN, the repository returns an honest "warming up" message
rather than fabricating or replaying analytic content.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

from backend.schemas.common import EvidenceEvent, validate_public_borrower_id
from backend.schemas.geo import (
    CountyRollup,
    CountyRollupResponse,
    StateRollup,
    StateRollupResponse,
    ZipRollup,
    ZipRollupResponse,
)
from backend.schemas.lead import Borrower360, LeadSummary, SegmentSummary
from backend.schemas.portfolio import (
    CampaignListResponse,
    CampaignStatusPatchRequest,
    CampaignSummary,
    KpiTrend,
    PortfolioCreateRequest,
    PortfolioCreateResponse,
    PortfolioCriteria,
    PortfolioPreview,
    PortfolioPreviewRequest,
)
from backend.schemas.why import WhyPanel, WhyPanelSource
from backend.services.county_names import county_fips_for_name, county_name_for_fips
from backend.services.databricks_sql import DatabricksSqlClient, DatabricksSqlError
from backend.services.databricks_sql_helpers import qualify
from backend.services.genie_answers import (
    GenieActionSuggestion,
    GenieDataFreshness,
    GenieMessageResponse,
    GenieProof,
    GenieVisualizationSpec,
    default_follow_up_questions,
)
from backend.services.genie_client import (
    GenieClientError,
    GenieResponse,
    ResilientGenieClient,
)
from backend.services.geography_scope import GeographyScope, load_geography_scope
from backend.services.lakebase import LakebaseError, get_lakebase_client
from backend.services.pii_redaction import (
    _FORBIDDEN_OUTPUT_KEYS,
    redact_borrower_row,
    redact_evidence_row,
    redact_lead_row,
)
from backend.services.resilience import DependencyDownError, TTLCache
from backend.services.scoring import (
    NBO_PRODUCT_LABELS,
    in_the_money,
    source_display_label,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Column projections -- one source of truth per query. Explicitly enumerated
# so ``SELECT *`` drift cannot leak PII.
# ---------------------------------------------------------------------------

_BORROWER_360_COLUMNS: str = (
    "clip, borrower_id, owner_name_hash, city, state, zip, segment_codes, "
    "equity_estimate, equity_pct, rate_spread_bps, market_rate_fraction, "
    "opportunity_score, confidence, recommended_offer_code, recommended_offer, "
    "why_now, evidence_ids, approval_status, owner_link_id, subject_property, "
    "avm_value, current_lien_balance, current_rate, ltv, related_property_count, "
    "situs_cbsa_code, first_pos_loan_type, "
    "is_owner_occupied, is_absentee, is_corporate_owner, is_investor, "
    "is_current_customer, is_former_customer, "
    "is_competitor_lien, has_permit, listed_for_sale, second_pos_amount, "
    "has_first_party_relationship, first_party_relationship_depth, "
    "first_party_recent_interactions, first_party_recent_application, "
    "first_party_synthetic_demo, "
    "marketing_eligible, consent_status, suppression_reason, last_touch_at, "
    "eligible_recontact_at, "
    "min_spread_bps_applied, min_equity_pct_applied, in_the_money, "
    "current_lender_ref"
)

# Slice13-accuracy perf: mip.gold.borrower_dossier is a pre-joined superset
# of borrower_360 + top-20 evidence events per CLIP. /api/borrowers/{id}
# reads one indexed row here instead of fanning out to two warehouse
# statements (borrower_360 + evidence_events). Keep this list in sync with
# sql/transformations/gold_borrower_dossier.sql's SELECT and with the DDL
# in sql/ddl/003_gold_tables.sql §10.
_BORROWER_DOSSIER_COLUMNS: str = (
    _BORROWER_360_COLUMNS
    + ", trigger_timeline_json, evidence_events, trigger_timeline"
)

_LEAD_POPULATION_COLUMNS: str = (
    # `clip` is projected first so the repository boundary can derive the
    # display-safe LeadSummary.clip surrogate. Raw Cotality CLIP stays below
    # the redaction boundary by default.
    "clip, borrower_id, display_name, city, state, zip, segment_codes, "
    "equity_estimate, rate_spread_bps, opportunity_score, confidence, "
    "recommended_offer_code, recommended_offer, why_now, evidence_ids, approval_status, "
    "current_lender_ref, "
    # Secondary-filter fields (2026-04-23) -- carried through from
    # gold.borrower_360 into gold.lead_population so /segment-intelligence
    # can run real client-side predicates against occupancy, owner-link
    # (related properties), lien state, and purchase intent. Ordering
    # matches the gold DDL + CTAS (see sql/ddl/gold_lead_population.sql).
    "is_owner_occupied, is_investor, is_current_customer, "
    "is_former_customer, is_competitor_lien, related_property_count, "
    "current_lien_balance, second_pos_amount, has_permit, listed_for_sale, "
    "marketing_eligible, consent_status, suppression_reason, last_touch_at, "
    "eligible_recontact_at"
)

_LEAD_POPULATION_SELECT_FROM_LP: str = (
    "lp.clip, lp.borrower_id, lp.display_name, lp.city, lp.state, lp.zip, lp.segment_codes, "
    "lp.equity_estimate, lp.rate_spread_bps, lp.opportunity_score, lp.confidence, "
    "lp.recommended_offer_code, lp.recommended_offer, lp.why_now, lp.evidence_ids, "
    "COALESCE(ls.approval_status, lp.approval_status, 'pending') AS approval_status, "
    "COALESCE(ls.outreach_status, 'none') AS outreach_status, "
    "ls.approved_at, ls.outreach_at, "
    "lp.current_lender_ref, "
    "lp.is_owner_occupied, lp.is_investor, lp.is_current_customer, "
    "lp.is_former_customer, lp.is_competitor_lien, lp.related_property_count, "
    "lp.current_lien_balance, lp.second_pos_amount, lp.has_permit, lp.listed_for_sale, "
    "lp.marketing_eligible, lp.consent_status, lp.suppression_reason, lp.last_touch_at, "
    "lp.eligible_recontact_at"
)

_LEAD_POPULATION_SELECT_FROM_B360: str = (
    "b.clip, b.borrower_id, "
    "CONCAT('Owner ', SUBSTR(b.owner_name_hash, 1, 8)) AS display_name, "
    "b.city, b.state, b.zip, b.segment_codes, "
    "b.equity_estimate, b.rate_spread_bps, b.opportunity_score, b.confidence, "
    "b.recommended_offer_code, b.recommended_offer, b.why_now, b.evidence_ids, "
    "COALESCE(ls.approval_status, b.approval_status, 'pending') AS approval_status, "
    "COALESCE(ls.outreach_status, 'none') AS outreach_status, "
    "ls.approved_at, ls.outreach_at, "
    "b.current_lender_ref, "
    "b.is_owner_occupied, b.is_investor, b.is_current_customer, "
    "b.is_former_customer, b.is_competitor_lien, b.related_property_count, "
    "b.current_lien_balance, b.second_pos_amount, b.has_permit, b.listed_for_sale, "
    "b.marketing_eligible, b.consent_status, b.suppression_reason, b.last_touch_at, "
    "b.eligible_recontact_at"
)

_EVIDENCE_COLUMNS: str = (
    "evidence_id, source_product, source_table, signal_type, signal_value, "
    "display_text, confidence, `timestamp`, signal_rank"
)

_SEGMENT_COLUMNS: str = (
    "segment_code, name, count, delta_vs_prior, avg_score, description, color"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_timeline(raw: Any) -> list[EvidenceEvent]:
    """Parse the ``trigger_timeline_json`` ARRAY<STRUCT> payload.

    The warehouse emits it as a JSON string via ``to_json(collect_list
    (struct(...)))``. Upstream tests already pin the struct field set;
    we map 1:1 into ``EvidenceEvent``. Unknown / empty input -> [].
    """
    if raw is None:
        return []
    if isinstance(raw, list):
        rows = raw
    elif isinstance(raw, str):
        try:
            rows = json.loads(raw)
        except json.JSONDecodeError:
            return []
    else:
        return []
    events: list[EvidenceEvent] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        events.append(
            EvidenceEvent(
                evidence_id=r.get("evidence_id") or "",
                source_product=r.get("source_product") or "",
                source_table=r.get("source_table") or "",
                signal_type=r.get("signal_type") or "",
                signal_value=r.get("signal_value") or "",
                display_text=r.get("display_text") or "",
                confidence=float(r.get("confidence") or 0.0),
                timestamp=str(r.get("timestamp") or ""),
            )
        )
    return events


def _redact_evidence_list(raw: Any) -> list[EvidenceEvent]:
    """Redact + hydrate an ARRAY<STRUCT<...>> evidence column.

    The dossier carries ``evidence_events`` + ``trigger_timeline`` as
    pre-joined struct arrays. ``databricks_sql._coerce`` decodes them
    from the JSON_ARRAY disposition into Python ``list[dict]``. Each
    struct dict needs the same redaction pipeline the standalone
    ``/api/borrowers/{id}/evidence`` path runs so lender strings stay
    generalised.
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        # Defensive fallback: if a future disposition change emits the
        # array as JSON text, still parse it.
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []
    events: list[EvidenceEvent] = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        events.append(EvidenceEvent(**redact_evidence_row(r)))
    return events


# ---------------------------------------------------------------------------
# Repositories
# ---------------------------------------------------------------------------


class DatabricksPortfolioRepository:
    """Portfolio preview rollup over ``gold.borrower_360``.

    Slice-4 scope: the criteria on the request don't yet shift the
    rollup -- the whole population is the portfolio preview. A later
    slice adds criteria push-down.

    Slice-6: wraps the ``preview`` read in a short-TTL cache. Portfolio
    aggregates change slowly; a 30s stale read during a user click-
    through is invisible and saves two or three warehouse round trips
    per route transition.
    """

    def __init__(
        self,
        client: DatabricksSqlClient,
        *,
        cache: TTLCache | None = None,
        cache_ttl_s: float = 30.0,
    ) -> None:
        self._client = client
        self._cache = cache if cache is not None else TTLCache()
        self._cache_ttl_s = cache_ttl_s

    _PREVIEW_SQL_TEMPLATE = (
        "SELECT "
        "  COUNT(*)                                                              AS marketable_population, "
        "  SUM(CASE WHEN in_the_money THEN 1 ELSE 0 END)                         AS high_intent_leads, "
        "  SUM(CASE WHEN opportunity_score >= 75 THEN 1 ELSE 0 END)              AS top_tier_opportunities, "
        "  SUM(CASE WHEN recommended_offer_code <> 'nurture' THEN 1 ELSE 0 END)  AS offers_recommended, "
        "  CAST(ROUND(AVG(opportunity_score)) AS INT)                            AS avg_score "
        f"FROM {qualify('gold', 'borrower_360')} "
        "{where}"
    )

    # Translate display labels from the portfolio-builder UI into
    # mip.gold.borrower_360 predicates. Every value is a short enum the
    # frontend emits verbatim; we keep the mapping here rather than ship
    # the strings to SQL so a typo in a dropdown can't open a SQL-injection
    # vector.
    #
    # Geography labels are computed from current live coverage. The broad
    # option is exactly "all N states" for the current N; individual state
    # names come from StateFootprintResolver. No fixed MSA or demo-only
    # shortcuts are accepted here.

    @classmethod
    def _state_sets(cls) -> dict[str, list[str]]:
        """Build the active _STATE_SETS dict from live coverage.

        Active labels:

          1. Per-state-name entries from ``state_name_to_codes()``.
          2. ``all N states`` computed from current coverage.

        Called once per `_build_preview_predicates` invocation; the
        resolver caches the UC result for 300s so this is cheap.
        """
        from backend.services.state_footprint import get_state_footprint_resolver

        resolver = get_state_footprint_resolver()
        footprint_codes = resolver.state_codes()
        state_name_map = resolver.state_name_to_codes()
        all_key = f"all {len(footprint_codes)} states"
        return {
            **state_name_map,
            all_key: list(footprint_codes),
        }

    # Canonical `recommended_offer_code` values emitted by fn_next_best_offer
    # (see sql/uc_functions/fn_next_best_offer.sql). Keep in sync.
    _PRODUCT_CODES: dict[str, list[str]] = {
        "refi":       ["refi", "refi_plus_heloc"],
        "heloc":      ["heloc", "refi_plus_heloc"],
        "cash-out":   ["cash_out"],
        "purchase":   ["purchase"],
        "retention":  ["retention"],
    }

    _EQUITY_THRESHOLDS: dict[str, int] = {
        "≥ 15%": 15,
        "≥ 25%": 25,
        "≥ 40%": 40,
    }

    @classmethod
    def _build_preview_predicates(
        cls, criteria: PortfolioCriteria | None,
    ) -> tuple[str, dict[str, Any]]:
        """Convert validated PortfolioCriteria into a (WHERE clause, params)
        pair. Returns `("", {})` when no predicates apply so the caller can
        run the criteria-free SELECT."""
        if criteria is None:
            return "", {}

        clauses: list[str] = []
        params: dict[str, Any] = {}

        # Geography — map reviewed display labels or explicit reviewed USPS
        # state codes to predicates. Broad "all N states" stays equivalent
        # to no geography predicate; gold.borrower_360 is already scoped to
        # rows with a non-null state, and skipping the redundant state IN
        # keeps broad builds aligned to the national funnel snapshots.
        states: list[str] = []
        if criteria.states:
            states.extend(criteria.states)
        if criteria.geography:
            key = criteria.geography.lower()
            state_sets = cls._state_sets()
            if key == "all" or (key.startswith("all ") and key in state_sets):
                pass
            else:
                states.extend(state_sets.get(key) or [])
        states = list(dict.fromkeys(states))
        if states:
            placeholders = ", ".join(f":geo_state_{i}" for i in range(len(states)))
            clauses.append(f"state IN ({placeholders})")
            for i, s in enumerate(states):
                params[f"geo_state_{i}"] = s

        # Occupancy.
        if criteria.occupancy == "Owner-occupied":
            clauses.append("is_owner_occupied = TRUE")
        elif criteria.occupancy == "Non-owner-occupied":
            clauses.append("is_owner_occupied = FALSE")

        # Lien status. `second_pos_amount` is the backed HELOC / 2nd-lien
        # signal in gold, so "Open HELOC" must not collapse into the generic
        # first-lien predicate.
        lien_status = (criteria.lien_status or "").strip().lower()
        if lien_status in {"free & clear", "free and clear"}:
            clauses.append("current_lien_balance = 0")
        elif lien_status in {"open 1st lien", "open first lien"}:
            clauses.append("current_lien_balance > 0")
            clauses.append("COALESCE(second_pos_amount, 0) = 0")
        elif lien_status in {"open heloc", "open 2nd lien / heloc", "multiple liens"}:
            clauses.append("COALESCE(second_pos_amount, 0) > 0")

        owner_link = (criteria.owner_link or "").strip().lower()
        if owner_link == "single-property owner":
            clauses.append("COALESCE(related_property_count, 1) <= 1")
        elif owner_link == "multi-property (2-4)":
            clauses.append("COALESCE(related_property_count, 1) BETWEEN 2 AND 4")
        elif owner_link == "portfolio investor (5+)":
            clauses.append("COALESCE(related_property_count, 1) >= 5")

        purchase_intent = (criteria.purchase_intent or "").strip().lower()
        if purchase_intent == "listed for sale":
            clauses.append("listed_for_sale = TRUE")
        elif purchase_intent == "recent permit activity":
            clauses.append("has_permit = TRUE")
        elif purchase_intent == "both":
            clauses.append("listed_for_sale = TRUE")
            clauses.append("has_permit = TRUE")

        # Lender relationship. These predicates only use backed gold flags:
        # current-servicer tenant match, historical tenant relationship with
        # no current tenant lien, and current competitor servicer.
        relationship = (criteria.lender_relationship or "").strip().lower()
        if relationship == "current customer":
            clauses.append("is_current_customer = TRUE")
        elif relationship == "former customer":
            clauses.append("is_former_customer = TRUE")
        elif relationship in {"competitor customer", "competitor"}:
            clauses.append("is_competitor_lien = TRUE")

        # Governed current-lender targeting. Values are display-safe refs
        # from mip.ref.lender_dictionary (`Summit Mortgage`, `Competitor A`,
        # etc.), never raw Cotality lender strings.
        target_lender_ref = (criteria.target_lender_ref or "").strip()
        if target_lender_ref and target_lender_ref.lower() != "all":
            clauses.append("current_lender_ref = :target_lender_ref")
            params["target_lender_ref"] = target_lender_ref

        # Product. "All products" / missing = no predicate.
        if criteria.product and criteria.product != "All products":
            codes = cls._PRODUCT_CODES.get(criteria.product.lower())
            if codes:
                placeholders = ", ".join(f":product_{i}" for i in range(len(codes)))
                clauses.append(f"recommended_offer_code IN ({placeholders})")
                for i, code in enumerate(codes):
                    params[f"product_{i}"] = code

        # Equity threshold. Prefer explicit float; fall back to the label.
        equity_floor: int | None = None
        if criteria.min_equity_pct is not None:
            equity_floor = int(criteria.min_equity_pct)
        elif criteria.min_equity_pct_label:
            equity_floor = cls._EQUITY_THRESHOLDS.get(criteria.min_equity_pct_label)
        if equity_floor is not None and equity_floor > 0:
            clauses.append("equity_pct >= :equity_floor")
            params["equity_floor"] = equity_floor

        marketing_eligibility = (criteria.marketing_eligibility or "").strip()
        if marketing_eligibility == "Eligible only":
            clauses.append("marketing_eligible = TRUE")
        elif marketing_eligibility == "Suppressed only":
            clauses.append("marketing_eligible = FALSE")

        consent_status = (criteria.consent_status or "").strip()
        if consent_status == "Opt-in":
            clauses.append("consent_status = 'opt_in'")
        elif consent_status == "Opt-out":
            clauses.append("consent_status = 'opt_out'")
        elif consent_status == "Unknown":
            clauses.append("consent_status = 'unknown'")

        recency = (criteria.recency or "").strip()
        recency_days = {
            "Untouched 30d": 30,
            "Untouched 60d": 60,
            "Untouched 90d": 90,
        }.get(recency)
        if recency_days:
            clauses.append(
                f"(last_touch_at IS NULL OR last_touch_at < CURRENT_TIMESTAMP() - INTERVAL {recency_days} DAYS)"
            )

        if not clauses:
            return "", {}
        return "WHERE " + " AND ".join(clauses), params

    # 7-day history for KPI sparklines + the two real funnel counts that
    # replaced the old hardcoded cost_per_contact / projected_contact_to_app
    # placeholders. Reads the national rollup row (state='_ALL',
    # segment_code='_ALL') from the daily funnel snapshot. Returns 0-7 rows
    # ordered newest-first (repository reverses to oldest-first before
    # sparkline rendering).
    _TREND_SQL = (
        "SELECT "
        "  snapshot_date, "
        "  snapshot_at, "
        "  addressable_borrowers          AS marketable_population, "
        "  in_the_money_borrowers         AS high_intent_leads, "
        "  high_opportunity_borrowers     AS top_tier_opportunities, "
        "  offer_recommended_borrowers    AS offers_recommended, "
        "  avg_opportunity_score          AS avg_score, "
        "  approved_borrowers             AS approved_count, "
        "  actioned_borrowers             AS in_outreach_count "
        f"FROM {qualify('gold', 'funnel_snapshot_daily')} "
        "WHERE state = '_ALL' AND segment_code = '_ALL' "
        "ORDER BY snapshot_date DESC "
        "LIMIT 7"
    )

    _PREVIEW_CACHE_KEY = "portfolio.preview.all"
    _DAY_ZERO_CACHE_KEY = "portfolio.day_zero"

    # Authoritative "this workspace has never had a gold refresh" signal
    # (R5-20). Unfiltered population count on mip.gold.lead_population,
    # because the day-zero state is workspace-wide and must not shift
    # with the caller's PortfolioCriteria (a criteria that happens to
    # match zero borrowers is NOT day-zero). LIMIT 1 + EXISTS-style CASE
    # so the warehouse returns one row no matter how large the table
    # becomes. The result is cached longer than the preview -- day-zero
    # flips at most once in a workspace's lifetime.
    _DAY_ZERO_SQL = (
        "SELECT CASE WHEN COUNT(*) = 0 THEN TRUE ELSE FALSE END AS day_zero "
        f"FROM {qualify('gold', 'lead_population')}"
    )

    _CAMPAIGN_INSERT_SQL = """
    WITH inserted_campaign AS (
      INSERT INTO mip_app.campaigns (
        name, owner_email, status, criteria, suppression_policy,
        message_variants, channel_cascade, send_window, holdout,
        roi_assumptions, updated_at
      )
      VALUES (
        %(name)s, %(owner_email)s, 'draft', %(criteria)s::jsonb,
        %(suppression_policy)s::jsonb, %(message_variants)s::jsonb,
        %(channel_cascade)s::jsonb, %(send_window)s::jsonb,
        %(holdout)s::jsonb, %(roi_assumptions)s::jsonb, now()
      )
      RETURNING campaign_id
    ),
    inserted_audit AS (
      INSERT INTO mip_app.action_audit (
        event_type, actor_email, entity_type, entity_id,
        request_id, evidence_ids, metadata
      )
      SELECT
        'PORTFOLIO_CREATE',
        %(owner_email)s,
        'campaign',
        inserted_campaign.campaign_id::text,
        %(request_id)s,
        ARRAY[]::TEXT[],
        %(metadata)s::jsonb
      FROM inserted_campaign
      RETURNING audit_id
    )
    SELECT
      inserted_campaign.campaign_id,
      inserted_audit.audit_id
    FROM inserted_campaign
    LEFT JOIN inserted_audit ON TRUE
    """

    _CAMPAIGN_VARIANT_UPSERT_SQL = """
    INSERT INTO mip_app.campaign_message_variants (
      campaign_id, variant_name, channel, subject, body, weight_pct
    ) VALUES (
      %(campaign_id)s, %(variant_name)s, %(channel)s, %(subject)s, %(body)s, %(weight_pct)s
    )
    ON CONFLICT (campaign_id, variant_name, channel)
    DO UPDATE SET
      subject = EXCLUDED.subject,
      body = EXCLUDED.body,
      weight_pct = EXCLUDED.weight_pct
    """

    _CAMPAIGN_LIST_SQL = """
    SELECT campaign_id::text, name, owner_email, status, criteria,
           suppression_policy, message_variants, channel_cascade, send_window,
           holdout, roi_assumptions, created_at, updated_at
    FROM mip_app.campaigns
    WHERE (%(owner_email)s::text IS NULL OR owner_email = %(owner_email)s::text)
      AND (%(status)s::text IS NULL OR status = %(status)s::text)
    ORDER BY updated_at DESC, created_at DESC
    LIMIT %(limit)s
    """

    _CAMPAIGN_GET_SQL = """
    SELECT campaign_id::text, name, owner_email, status, criteria,
           suppression_policy, message_variants, channel_cascade, send_window,
           holdout, roi_assumptions, created_at, updated_at
    FROM mip_app.campaigns
    WHERE campaign_id = %(campaign_id)s::uuid
    LIMIT 1
    """

    _CAMPAIGN_PATCH_SQL = """
    UPDATE mip_app.campaigns
    SET status = %(status)s, updated_at = now()
    WHERE campaign_id = %(campaign_id)s::uuid
    RETURNING campaign_id::text, name, owner_email, status, criteria,
              suppression_policy, message_variants, channel_cascade, send_window,
              holdout, roi_assumptions, created_at, updated_at
    """

    @staticmethod
    def _build_trend(points: list[tuple[str, float]]) -> KpiTrend:
        """Compute KpiTrend from oldest-first (date label, value) points.

        The live funnel table currently has a bootstrap row where some
        later-added metrics are 0 before the metric existed. A percent
        change from that row is mathematically undefined and visually
        misleading. Drop leading zero bootstrap points when later non-zero
        rows exist, then label the comparison date explicitly so the UI
        never says "7d ago" unless the data really represents that grain.
        """
        notes: list[str] = []
        original_start = points[0][0] if points else None
        while len(points) > 1 and points[0][1] == 0 and any(p[1] != 0 for p in points[1:]):
            points = points[1:]
        if original_start and points and points[0][0] != original_start:
            notes.append(
                f"Comparison starts on {points[0][0]} because earlier snapshots predate this metric."
            )
        series = [value for _, value in points]
        comparison_label = f"vs {points[0][0]}" if len(points) >= 2 else None
        if len(series) < 2 or series[0] == 0:
            return KpiTrend(
                series=series,
                delta_pct=None,
                direction="flat",
                comparison_label=comparison_label,
                note=" ".join(notes) or None,
            )
        for index in range(1, len(points)):
            previous = points[index - 1][1]
            current = points[index][1]
            if previous == 0:
                continue
            step_pct = abs((current - previous) / previous) * 100.0
            if step_pct >= 8.0:
                notes.append(
                    f"Material step change on {points[index][0]}; verify rules or refresh context before presenting this as market movement."
                )
                break
        delta_pct = ((series[-1] - series[0]) / series[0]) * 100.0
        direction = "up" if delta_pct > 0.5 else "down" if delta_pct < -0.5 else "flat"
        return KpiTrend(
            series=series,
            delta_pct=round(delta_pct, 1),
            direction=direction,
            comparison_label=comparison_label,
            note=" ".join(notes) or None,
        )

    def _load_funnel(
        self,
        *,
        include_trends: bool,
    ) -> tuple[dict[str, KpiTrend], dict[str, Any], str, str | None]:
        """Query the funnel snapshot and return trends + latest metadata.

        `trends` is a dict keyed by KPI field; series are oldest-first.
        `latest` is the newest row (keys include `approved_count`,
        `in_outreach_count`, `snapshot_at`) — used by the preview to
        surface the real current counts + data_refreshed_at timestamp.

        Trend lines are only cohort-correct for the unfiltered portfolio
        because ``funnel_snapshot_daily`` currently snapshots the national
        _ALL/_ALL row, not arbitrary filter combinations. For filtered
        requests we still read the latest refresh metadata but deliberately
        return no sparkline series and a note for the UI.
        """
        try:
            rows = self._client.execute(self._TREND_SQL) or []
        except Exception as exc:  # noqa: BLE001 -- surface unavailable, don't invent trends
            log.warning("portfolio funnel snapshot query failed: %s", exc)
            return {}, {}, "unavailable", "Trend snapshots are unavailable; headline KPIs still come from live borrower_360."
        if not rows:
            return {}, {}, "empty", "No daily funnel snapshots have been written yet."
        # Query is DESC; the FIRST row is newest. Reverse for oldest-first
        # sparkline rendering.
        latest = rows[0]
        if not include_trends:
            return (
                {},
                latest,
                "not_applicable",
                "Trend lines are hidden for this filtered build because daily snapshots are not stored at this custom filter grain.",
            )
        ordered = list(reversed(rows))
        trends: dict[str, KpiTrend] = {}
        for key in (
            "marketable_population",
            "high_intent_leads",
            "top_tier_opportunities",
            "offers_recommended",
            "avg_score",
            "approved_count",
            "in_outreach_count",
        ):
            points = [
                (str(r.get("snapshot_date") or "prior snapshot"), float(r.get(key) or 0))
                for r in ordered
            ]
            trends[key] = self._build_trend(points)
        return trends, latest, "live", None

    @staticmethod
    def _coerce_datetime(value: Any) -> datetime | None:
        """Normalise ``MAX(snapshot_at)`` into a tz-aware UTC ``datetime``.

        The Databricks SQL connector returns TIMESTAMP as a tz-naive Python
        ``datetime`` (no ``tzinfo``). Pydantic would serialise that without
        a ``Z`` / ``+00:00`` suffix, so ``new Date(...)`` in the browser
        interprets it as local time — and a European viewer sees the wrong
        hour on ``data_refreshed_at``. Stamp UTC on the way out so the wire
        contract is unambiguous (hole-finder round 2 #4, 2026-04-23).
        """
        if value is None:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=UTC)
            return value.astimezone(UTC)
        # Defensive: a future connector change may emit an ISO string.
        try:
            # Accept the "...Z" suffix that some drivers emit.
            raw = str(value).replace("Z", "+00:00")
            parsed = datetime.fromisoformat(raw)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _preview_cache_key(cls, where_clause: str, params: dict[str, Any]) -> str:
        """Deterministic cache key for ``preview`` results.

        R5-08: the prior key embedded ``str(sorted(params.items()))``,
        which produced semantically-equivalent-but-different strings
        depending on dict iteration order, Python version, and repr of
        edge-case values. That was a minor cache-miss waste today and a
        500-risk if a non-hashable value ever slipped in. We now hash
        the canonical JSON form of ``(where_clause, params)`` -- stable
        regardless of insertion order (``sort_keys=True``), string-safe
        for everything pydantic emits (``default=str``), and bounded in
        length via SHA-256.
        """
        canonical = json.dumps(
            {"where": where_clause, "params": params},
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]  # noqa: S324 -- not a secret
        return f"{cls._PREVIEW_CACHE_KEY}:{digest}"

    def _load_day_zero(self) -> bool:
        """Return True when ``mip.gold.lead_population`` is empty.

        R5-20: authoritative day-zero signal. Cached under its own key
        so it's shared across every criteria variant (day-zero doesn't
        depend on the filter).

        R6-06/R6-07: exceptions propagate. The prior implementation
        swallowed any failure into ``return False``, which yielded a
        misleading preview -- the frontend would say "there IS data"
        (day_zero=False) alongside KPIs of 0 (because the preview
        execute_one ALSO failed, but differently) and show a degraded
        banner on top. Letting the exception bubble out means the
        preview route's surrounding ``DependencyDownError`` -> 503
        path fires cleanly and the UI shows a single honest "warming
        up" message instead of a misleading empty grid.

        The ``execute_one`` here runs through ``ResilientSqlClient``,
        so transient warehouse failures already surface as
        ``DependencyDownError``. Any non-resilience exception (e.g.
        schema drift) is also a legitimate 503 signal -- we are not
        in the business of quietly rendering zeros for unknown
        failure modes.

        R6-17: skip the cache get/set when ``_cache_ttl_s`` is 0.
        ``TTLCache.set`` already short-circuits on ttl<=0 but the ``get``
        acquires a lock for no benefit; bypassing both keeps the
        tests-with-caching-disabled path allocation-free.
        """
        if self._cache_ttl_s <= 0:
            row = self._client.execute_one(self._DAY_ZERO_SQL) or {}
            return bool(row.get("day_zero"))
        cached = self._cache.get(self._DAY_ZERO_CACHE_KEY)
        if cached is not None:
            return bool(cached)
        row = self._client.execute_one(self._DAY_ZERO_SQL) or {}
        day_zero = bool(row.get("day_zero"))
        self._cache.set(self._DAY_ZERO_CACHE_KEY, day_zero, self._cache_ttl_s)
        return day_zero

    def preview(self, request: PortfolioPreviewRequest | None) -> PortfolioPreview:
        criteria = request.criteria if request is not None else None
        where_clause, params = self._build_preview_predicates(criteria)
        # R6-17: when caching is disabled (MIP_CACHE_TTL_S=0, test
        # defaults, some dev loops), skip the SHA-256 hash + dict
        # serialisation that build the cache key. Saves one hashlib
        # invocation per request on the hottest route without changing
        # the caller contract.
        caching_enabled = self._cache_ttl_s > 0
        cache_key = (
            self._preview_cache_key(where_clause, params)
            if caching_enabled
            else ""
        )
        if caching_enabled:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached
        sql = self._PREVIEW_SQL_TEMPLATE.format(where=where_clause)
        row = self._client.execute_one(sql, params) or {}
        trends, latest, trend_status, trend_note = self._load_funnel(
            include_trends=not bool(where_clause),
        )
        preview = PortfolioPreview(
            marketable_population=int(row.get("marketable_population") or 0),
            high_intent_leads=int(row.get("high_intent_leads") or 0),
            top_tier_opportunities=(
                int(row["top_tier_opportunities"])
                if row.get("top_tier_opportunities") is not None
                else None
            ),
            offers_recommended=(
                int(row["offers_recommended"])
                if row.get("offers_recommended") is not None
                else None
            ),
            avg_score=(
                int(row["avg_score"])
                if row.get("avg_score") is not None
                else None
            ),
            approved_count=(
                int(latest["approved_count"])
                if not where_clause and latest.get("approved_count") is not None
                else None
            ),
            in_outreach_count=(
                int(latest["in_outreach_count"])
                if not where_clause and latest.get("in_outreach_count") is not None
                else None
            ),
            data_refreshed_at=self._coerce_datetime(latest.get("snapshot_at")),
            trends=trends,
            trend_status=trend_status,
            trend_note=trend_note,
            day_zero=self._load_day_zero(),
        )
        if caching_enabled:
            self._cache.set(cache_key, preview, self._cache_ttl_s)
        return preview

    def create(
        self,
        payload: PortfolioCreateRequest,
        *,
        actor: str | None = None,
    ) -> PortfolioCreateResponse:
        preview = self.preview(PortfolioPreviewRequest(criteria=payload.criteria))
        row = get_lakebase_client().fetchone(
            self._CAMPAIGN_INSERT_SQL,
            {
                "name": payload.name,
                "owner_email": actor or "unknown",
                "criteria": json.dumps(payload.criteria.model_dump(exclude_none=True)),
                "suppression_policy": json.dumps(payload.suppression_policy, sort_keys=True),
                "message_variants": json.dumps(payload.message_variants, sort_keys=True),
                "channel_cascade": json.dumps(payload.channel_cascade, sort_keys=True),
                "send_window": json.dumps(payload.send_window, sort_keys=True),
                "holdout": json.dumps(payload.holdout, sort_keys=True) if payload.holdout is not None else "null",
                "roi_assumptions": json.dumps(payload.roi_assumptions, sort_keys=True) if payload.roi_assumptions is not None else "null",
                "request_id": f"portfolio-create-{uuid.uuid4()}",
                "metadata": json.dumps(
                    {
                        "source": "portfolio_builder",
                        "criteria": payload.criteria.model_dump(exclude_none=True),
                        "suppression_policy": payload.suppression_policy,
                        "channel_cascade": payload.channel_cascade,
                        "send_window": payload.send_window,
                        "holdout": payload.holdout,
                        "roi_assumptions": payload.roi_assumptions,
                        "marketable_population": preview.marketable_population,
                    },
                    sort_keys=True,
                ),
            },
        )
        if row is None or not row.get("campaign_id"):
            raise LakebaseError("campaign insert returned no row")
        campaign_id = str(row["campaign_id"])
        variant_rows = [
            {
                "campaign_id": campaign_id,
                "variant_name": str(variant.get("variant_name") or variant.get("name") or "default")[:64],
                "channel": str(variant.get("channel") or "email"),
                "subject": variant.get("subject"),
                "body": str(variant.get("body") or ""),
                "weight_pct": variant.get("weight_pct"),
            }
            for variant in payload.message_variants
            if str(variant.get("body") or "").strip()
        ]
        if variant_rows:
            get_lakebase_client().executemany(self._CAMPAIGN_VARIANT_UPSERT_SQL, variant_rows)
        return PortfolioCreateResponse(
            portfolio_id=campaign_id,
            campaign_id=campaign_id,
            name=payload.name,
            marketable_population=preview.marketable_population,
            audit_event_id=str(row["audit_id"]) if row.get("audit_id") else None,
        )

    @staticmethod
    def _json_value(value: Any, fallback: Any) -> Any:
        if value is None:
            return fallback
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return fallback
        return value

    @classmethod
    def _campaign_from_row(cls, row: dict[str, Any]) -> CampaignSummary:
        criteria = cls._json_value(row.get("criteria"), {})
        suppression_policy = cls._json_value(row.get("suppression_policy"), {})
        message_variants = cls._json_value(row.get("message_variants"), [])
        channel_cascade = cls._json_value(row.get("channel_cascade"), [])
        send_window = cls._json_value(row.get("send_window"), {})
        holdout = cls._json_value(row.get("holdout"), None)
        roi_assumptions = cls._json_value(row.get("roi_assumptions"), None)
        return CampaignSummary(
            campaign_id=str(row.get("campaign_id")),
            name=str(row.get("name") or "Campaign"),
            owner_email=str(row.get("owner_email") or "unknown"),
            status=str(row.get("status") or "draft"),  # type: ignore[arg-type]
            criteria=criteria if isinstance(criteria, dict) else {},
            suppression_policy=suppression_policy if isinstance(suppression_policy, dict) else {},
            message_variants=message_variants if isinstance(message_variants, list) else [],
            channel_cascade=channel_cascade if isinstance(channel_cascade, list) else [],
            send_window=send_window if isinstance(send_window, dict) else {},
            holdout=holdout if isinstance(holdout, dict) or holdout is None else None,
            roi_assumptions=roi_assumptions if isinstance(roi_assumptions, dict) or roi_assumptions is None else None,
            created_at=cls._coerce_datetime(row.get("created_at")),
            updated_at=cls._coerce_datetime(row.get("updated_at")),
        )

    def list_campaigns(
        self,
        *,
        owner_email: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> CampaignListResponse:
        rows = get_lakebase_client().fetchall(
            self._CAMPAIGN_LIST_SQL,
            {"owner_email": owner_email, "status": status, "limit": max(1, min(limit, 200))},
            limit=max(1, min(limit, 200)),
        )
        return CampaignListResponse(campaigns=[self._campaign_from_row(row) for row in rows])

    def get(self, portfolio_id: str) -> dict[str, object]:
        row = get_lakebase_client().fetchone(
            self._CAMPAIGN_GET_SQL,
            {"campaign_id": portfolio_id},
        )
        if row is None:
            return {}
        return self._campaign_from_row(row).model_dump()

    def patch_status(
        self,
        portfolio_id: str,
        payload: CampaignStatusPatchRequest,
        *,
        actor: str | None = None,
    ) -> CampaignSummary:
        existing = get_lakebase_client().fetchone(
            self._CAMPAIGN_GET_SQL,
            {"campaign_id": portfolio_id},
        )
        if existing is None:
            raise LakebaseError("campaign status update returned no row")
        if payload.status in {"pending_review", "approved", "live", "active"}:
            criteria = self._json_value(existing.get("criteria"), {})
            suppression_policy = self._json_value(existing.get("suppression_policy"), {})
            criteria_ok = (
                isinstance(criteria, dict)
                and criteria.get("marketing_eligibility") == "Eligible only"
            )
            policy_ok = isinstance(suppression_policy, dict) and (
                suppression_policy.get("default") == "eligible_only"
                or suppression_policy.get("require_marketing_eligible") is True
                or str(suppression_policy.get("marketing_eligibility") or "")
                .strip()
                .lower()
                .replace(" ", "_")
                == "eligible_only"
            )
            if not (criteria_ok and policy_ok):
                raise ValueError(
                    "campaign cannot advance without an Eligible only contactability policy"
                )
        row = get_lakebase_client().fetchone(
            self._CAMPAIGN_PATCH_SQL,
            {"campaign_id": portfolio_id, "status": payload.status},
        )
        if row is None:
            raise LakebaseError("campaign status update returned no row")
        campaign = self._campaign_from_row(row)
        get_lakebase_client().execute(
            """
            INSERT INTO mip_app.action_audit (
              event_type, actor_email, entity_type, entity_id, evidence_ids, metadata
            ) VALUES (
              'CAMPAIGN_STATUS_UPDATE', %(actor)s, 'campaign', %(campaign_id)s,
              ARRAY[]::TEXT[], %(metadata)s::jsonb
            )
            """,
            {
                "actor": actor or "unknown",
                "campaign_id": portfolio_id,
                "metadata": json.dumps(
                    {"status": payload.status, "rationale": payload.rationale},
                    sort_keys=True,
                ),
            },
        )
        return campaign


class DatabricksSegmentRepository:
    """Segment rollup serves the national ``state='_ALL'`` row.

    Slice-6: wraps the ``list`` read in a short-TTL cache. Segment
    counts are a national aggregate recomputed by the gold pipeline on
    a fixed cadence; a 30s stale read is fine and removes a
    per-request warehouse round-trip from the segment-intelligence
    route.
    """

    def __init__(
        self,
        client: DatabricksSqlClient,
        *,
        cache: TTLCache | None = None,
        cache_ttl_s: float = 30.0,
    ) -> None:
        self._client = client
        self._cache = cache if cache is not None else TTLCache()
        self._cache_ttl_s = cache_ttl_s

    _LIST_SQL = (
        f"SELECT {_SEGMENT_COLUMNS} "
        f"FROM {qualify('gold', 'segment_population')} "
        "WHERE state = '_ALL' "
        "ORDER BY count DESC"
    )

    _LIST_FILTERED_SQL_TPL = (
        "WITH meta AS ( "
        f"  SELECT {_SEGMENT_COLUMNS} "
        f"  FROM {qualify('gold', 'segment_population')} "
        "  WHERE state = '_ALL' "
        "), base AS ( "
        "  SELECT segment_codes, opportunity_score "
        f"  FROM {qualify('gold', 'borrower_360')} "
        "  WHERE {filter_clause} "
        "), exploded_segments AS ( "
        "  SELECT sc AS segment_code, opportunity_score "
        "  FROM base "
        "  LATERAL VIEW EXPLODE(segment_codes) s AS sc "
        "  WHERE sc IS NOT NULL "
        "), rollup AS ( "
        "  SELECT "
        "    segment_code, "
        "    CAST(COUNT(*) AS INT) AS count, "
        "    CAST(ROUND(AVG(opportunity_score)) AS INT) AS avg_score "
        "  FROM exploded_segments "
        "  GROUP BY segment_code "
        ") "
        "SELECT "
        "  m.segment_code, "
        "  m.name, "
        "  COALESCE(r.count, 0) AS count, "
        "  m.delta_vs_prior, "
        "  COALESCE(r.avg_score, 0) AS avg_score, "
        "  m.description, "
        "  m.color "
        "FROM meta AS m "
        "LEFT JOIN rollup AS r ON r.segment_code = m.segment_code"
    )

    # Canonical FE display order matching the prototype's seg-grid layout
    # (`design_files/Module 0 Prototype.html` lines 1546–1551 + the gold
    # `meta` VALUES table in `sql/transformations/gold_segment_population.sql`).
    # Used to re-sort the SQL result after fetch so that pending-source
    # segments (count=0 because of an upstream Cotality data dependency)
    # are NOT buried at the end of the list by `ORDER BY count DESC`.
    # Prototype-parity-audit P0-2 (2026-05-04): the gold rollup now always
    # emits 6 rows; this constant ensures the FE always renders them in the
    # same predictable order regardless of cardinality.
    _CANONICAL_ORDER: tuple[str, ...] = (
        "itm",
        "listed",
        "permit",
        "investor",
        "equity",
        "retention",
    )

    def _list_cache_key(
        self,
        portfolio_id: str | None,
        *,
        segment_codes: list[str] | None = None,
        segment_mode: str = "any",
        portfolio_criteria: PortfolioCriteria | None = None,
    ) -> str:
        portfolio_key = DatabricksGeoRepository._portfolio_cache_key(portfolio_criteria)
        segment_key = ",".join(segment_codes or [])
        return f"segments.list.{portfolio_id or '_ALL'}:{segment_mode}:{segment_key}:{portfolio_key}"

    def list(
        self,
        portfolio_id: str | None,
        segment_codes: list[str] | None = None,
        segment_mode: str = "any",
        portfolio_criteria: PortfolioCriteria | None = None,
    ) -> list[SegmentSummary]:
        normalised_segments = DatabricksGeoRepository._normalise_geo_segments(segment_codes)
        key = self._list_cache_key(
            portfolio_id,
            segment_codes=normalised_segments,
            segment_mode=segment_mode,
            portfolio_criteria=portfolio_criteria,
        )
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        if normalised_segments or portfolio_criteria is not None:
            filter_clause, params = DatabricksGeoRepository._geo_filter_clause(
                normalised_segments,
                segment_mode=segment_mode,
                portfolio_criteria=portfolio_criteria,
            )
            rows = self._client.execute(
                self._LIST_FILTERED_SQL_TPL.format(filter_clause=filter_clause),
                params,
            )
        else:
            rows = self._client.execute(self._LIST_SQL)
        segments = [
            SegmentSummary(
                code=row["segment_code"],
                name=row["name"],
                count=int(row.get("count") or 0),
                # Rename at boundary: gold column is delta_vs_prior.
                delta=row.get("delta_vs_prior") or "+0%",
                avg_score=int(row.get("avg_score") or 0),
                description=row.get("description") or "",
                color=row.get("color") or "#999999",
            )
            for row in rows
        ]
        # Re-sort into the canonical FE display order. Without this the SQL's
        # `ORDER BY count DESC` would push pending-source segments (count=0,
        # currently `listed` and `permit`) to the end of the list, which
        # defeats the "you always see 6 segments" UX promise. Unknown segment
        # codes (a future addition that landed in gold but not yet in this
        # constant) are appended after the canonical set in their original
        # SQL order so they're still visible.
        order_index: dict[str, int] = {
            code: i for i, code in enumerate(self._CANONICAL_ORDER)
        }
        unknown_tail_index = len(self._CANONICAL_ORDER)
        segments.sort(
            key=lambda s: order_index.get(s.code, unknown_tail_index)
        )
        self._cache.set(key, segments, self._cache_ttl_s)
        return segments


class DatabricksLeadRepository:
    """Ranked leads from ``gold.lead_population``.

    The per-request ``limit`` is bounded by ``MAX_LIMIT`` (5000) so a
    pathological caller can't pull the whole gold table onto one page.
    Default is 500 — the size a VP of Lending can scroll in one sitting
    and the threshold the LeadTable footer renders "Showing N of M" at.
    Hole-finder round 2 #24, 2026-04-23.
    """

    DEFAULT_LIMIT: int = 500
    MAX_LIMIT: int = 5000

    def __init__(self, client: DatabricksSqlClient) -> None:
        self._client = client

    _LIST_BASE_SQL_TEMPLATE = (
        f"SELECT {_LEAD_POPULATION_SELECT_FROM_LP} "
        f"FROM {qualify('gold', 'lead_population')} lp "
        f"LEFT JOIN {qualify('gold', 'borrower_lifecycle_state')} ls "
        "  ON ls.borrower_id = lp.borrower_id "
        "WHERE 1=1 {lifecycle_clause} "
        "ORDER BY lp.rank_overall ASC, lp.borrower_id ASC "
        "LIMIT {limit}"
    )

    _LIST_BY_SEGMENT_SQL_TEMPLATE = (
        f"SELECT {_LEAD_POPULATION_SELECT_FROM_LP} "
        f"FROM {qualify('gold', 'lead_population')} lp "
        f"LEFT JOIN {qualify('gold', 'borrower_lifecycle_state')} ls "
        "  ON ls.borrower_id = lp.borrower_id "
        "WHERE array_contains(segment_codes, :segment) {lifecycle_clause} "
        "ORDER BY lp.rank_overall ASC, lp.borrower_id ASC "
        "LIMIT {limit}"
    )

    _LIST_FILTERED_SQL_TEMPLATE = (
        f"SELECT {_LEAD_POPULATION_SELECT_FROM_LP} "
        f"FROM {qualify('gold', 'lead_population')} lp "
        f"LEFT JOIN {qualify('gold', 'borrower_lifecycle_state')} ls "
        "  ON ls.borrower_id = lp.borrower_id "
        "WHERE {segment_clause} {lifecycle_clause} "
        "ORDER BY lp.rank_overall ASC, lp.borrower_id ASC "
        "LIMIT {limit}"
    )

    _COUNT_BASE_SQL = (
        f"SELECT COUNT(*) AS n FROM {qualify('gold', 'lead_population')} lp "
        f"LEFT JOIN {qualify('gold', 'borrower_lifecycle_state')} ls "
        "  ON ls.borrower_id = lp.borrower_id "
        "WHERE 1=1 {lifecycle_clause}"
    )

    _COUNT_FILTERED_SQL_TEMPLATE = (
        f"SELECT COUNT(*) AS n FROM {qualify('gold', 'lead_population')} lp "
        f"LEFT JOIN {qualify('gold', 'borrower_lifecycle_state')} ls "
        "  ON ls.borrower_id = lp.borrower_id "
        "WHERE {segment_clause} {lifecycle_clause}"
    )

    _COUNT_BY_GEO_SQL_TEMPLATE = (
        f"SELECT COUNT(*) AS n FROM {qualify('gold', 'borrower_360')} b "
        f"LEFT JOIN {qualify('gold', 'borrower_lifecycle_state')} ls "
        "  ON ls.borrower_id = b.borrower_id "
        "WHERE 1=1 {state_clause} {zip_clause} {county_clause} {borrower_clause} "
        "{segment_clause} {lender_clause} {portfolio_clause} {lifecycle_clause}"
    )

    # 2026-05-04 FIX β: when the caller filters by state and/or zip we
    # bypass lead_population (which has the score >= 50 quality floor
    # baked into its CTAS) and read borrower_360 directly. Rationale:
    # the home-page map ZIP/county/state tooltips report the FULL
    # addressable population per geo (no score filter), and the user
    # expectation is that drilling INTO a geo from the map shows
    # everyone the map counted. Pre-fix: ZIP 33073 showed 19 marketable
    # on the map, then 0 in the Lead Queue because the API returned
    # the top-500 nationally-ranked from lead_population and none of
    # those 500 lived in 33073. Post-fix: the same /api/leads call,
    # narrowed to (state='FL', zip='33073'), runs against borrower_360
    # and returns the 19 borrowers the map promised. ORDER BY score
    # still surfaces the highest-opportunity ones first; LIMIT applies
    # AFTER the geo filter so the cap doesn't pre-truncate.
    _LIST_BY_GEO_SQL_TEMPLATE = (
        # Project the lead_population columns directly off borrower_360.
        # Every column in _LEAD_POPULATION_COLUMNS exists in borrower_360
        # except `display_name` (synthesized in the lead_population CTAS).
        # We re-synthesize it here with the same formula so LeadSummary
        # rows stay shape-compatible whether they came from
        # lead_population or borrower_360.
        f"SELECT {_LEAD_POPULATION_SELECT_FROM_B360} "
        f"FROM {qualify('gold', 'borrower_360')} b "
        f"LEFT JOIN {qualify('gold', 'borrower_lifecycle_state')} ls "
        "  ON ls.borrower_id = b.borrower_id "
        "WHERE 1=1 {state_clause} {zip_clause} {county_clause} {borrower_clause} "
        "{segment_clause} {lender_clause} {portfolio_clause} {lifecycle_clause} "
        "ORDER BY b.opportunity_score DESC, b.borrower_id ASC "
        "LIMIT {limit}"
    )

    def list(
        self,
        segment: str | None,
        portfolio_id: str | None,
        limit: int | None = None,
        state: str | None = None,
        zip_code: str | None = None,
        county_fips: str | None = None,
        county_fipses: list[str] | None = None,
        state_codes: list[str] | None = None,
        zip_codes: list[str] | None = None,
        borrower_ids: list[str] | None = None,
        segment_codes: list[str] | None = None,
        segment_mode: str = "any",
        target_lender_ref: str | None = None,
        cohort_id: str | None = None,
        portfolio_criteria: PortfolioCriteria | None = None,
        approval_status: str | None = None,
        outreach_status: str | None = None,
        aged_days: int | None = None,
    ) -> list[LeadSummary]:
        _ = (portfolio_id, cohort_id)
        bounded = self._bound_limit(limit)
        segment_clause, segment_params = self._segment_filter_clause(
            segment=segment,
            segment_codes=segment_codes,
            segment_mode=segment_mode,
        )

        # FIX β: geo-filtered path bypasses lead_population so the queue
        # row count matches the map tooltip. See the
        # _LIST_BY_GEO_SQL_TEMPLATE docstring above for the full rationale.
        normalised_states = self._normalise_states(state, state_codes)
        normalised_zips = self._normalise_zips(zip_code, zip_codes)
        normalised_county = self._normalise_county_fips(county_fips, county_fipses)
        normalised_borrower_ids = self._normalise_borrower_ids(borrower_ids)
        lifecycle_clause, lifecycle_params = self._lifecycle_filter_clause(
            source_alias="b",
            approval_status=approval_status,
            outreach_status=outreach_status,
            aged_days=aged_days,
        )
        lender_clause = ""
        lender_params: dict[str, object] = {}
        if target_lender_ref and target_lender_ref.strip().lower() != "all":
            lender_clause = "AND b.current_lender_ref = :target_lender_ref"
            lender_params["target_lender_ref"] = target_lender_ref.strip()
        portfolio_where, portfolio_params = DatabricksPortfolioRepository._build_preview_predicates(
            portfolio_criteria,
        )
        portfolio_clause = (
            "AND " + portfolio_where.removeprefix("WHERE ").strip()
            if portfolio_where
            else ""
        )
        if "target_lender_ref" in portfolio_params:
            lender_clause = ""
            lender_params = {}

        if (
            normalised_states
            or normalised_zips
            or normalised_county
            or normalised_borrower_ids
            or lender_clause
            or portfolio_clause
        ):
            params: dict[str, object] = dict(segment_params)
            params.update(lender_params)
            params.update(portfolio_params)
            params.update(lifecycle_params)
            state_clause = self._in_clause(
                column="b.state",
                prefix="state",
                values=normalised_states,
                params=params,
            )
            zip_clause = self._in_clause(
                column="b.zip",
                prefix="zip",
                values=normalised_zips,
                params=params,
            )
            county_clause = self._in_clause(
                column="b.county_fips_5",
                prefix="county",
                values=normalised_county,
                params=params,
            )
            borrower_clause = self._in_clause(
                column="b.borrower_id",
                prefix="borrower_id",
                values=normalised_borrower_ids,
                params=params,
            )
            geo_segment_clause = f"AND {segment_clause}" if segment_clause else ""
            sql = self._LIST_BY_GEO_SQL_TEMPLATE.format(
                state_clause=state_clause,
                zip_clause=zip_clause,
                county_clause=county_clause,
                borrower_clause=borrower_clause,
                segment_clause=geo_segment_clause,
                lender_clause=lender_clause,
                portfolio_clause=portfolio_clause,
                lifecycle_clause=lifecycle_clause,
                limit=bounded,
            )
            rows = self._client.execute(sql, params)
            return [LeadSummary(**redact_lead_row(r)) for r in rows]

        lifecycle_clause, lifecycle_params = self._lifecycle_filter_clause(
            source_alias="lp",
            approval_status=approval_status,
            outreach_status=outreach_status,
            aged_days=aged_days,
        )
        if segment_clause:
            if lender_clause:
                segment_clause = f"{segment_clause} {lender_clause}"
                segment_params = {**segment_params, **lender_params}
            segment_params = {**segment_params, **lifecycle_params}
            sql = self._LIST_FILTERED_SQL_TEMPLATE.format(
                segment_clause=segment_clause,
                lifecycle_clause=lifecycle_clause,
                limit=bounded,
            )
            rows = self._client.execute(sql, segment_params)
        else:
            sql = self._LIST_BASE_SQL_TEMPLATE.format(
                lifecycle_clause=lifecycle_clause,
                limit=bounded,
            )
            rows = self._client.execute(sql, lifecycle_params)
        return [LeadSummary(**redact_lead_row(r)) for r in rows]

    def count(
        self,
        segment: str | None,
        portfolio_id: str | None,
        state: str | None = None,
        zip_code: str | None = None,
        county_fips: str | None = None,
        county_fipses: list[str] | None = None,
        state_codes: list[str] | None = None,
        zip_codes: list[str] | None = None,
        borrower_ids: list[str] | None = None,
        segment_codes: list[str] | None = None,
        segment_mode: str = "any",
        target_lender_ref: str | None = None,
        cohort_id: str | None = None,
        portfolio_criteria: PortfolioCriteria | None = None,
        approval_status: str | None = None,
        outreach_status: str | None = None,
        aged_days: int | None = None,
    ) -> int:
        _ = (portfolio_id, cohort_id)
        segment_clause, segment_params = self._segment_filter_clause(
            segment=segment,
            segment_codes=segment_codes,
            segment_mode=segment_mode,
        )
        normalised_states = self._normalise_states(state, state_codes)
        normalised_zips = self._normalise_zips(zip_code, zip_codes)
        normalised_county = self._normalise_county_fips(county_fips, county_fipses)
        normalised_borrower_ids = self._normalise_borrower_ids(borrower_ids)
        lifecycle_clause_geo, lifecycle_params_geo = self._lifecycle_filter_clause(
            source_alias="b",
            approval_status=approval_status,
            outreach_status=outreach_status,
            aged_days=aged_days,
        )
        lender_clause = ""
        lender_params: dict[str, object] = {}
        if target_lender_ref and target_lender_ref.strip().lower() != "all":
            lender_clause = "AND b.current_lender_ref = :target_lender_ref"
            lender_params["target_lender_ref"] = target_lender_ref.strip()
        portfolio_where, portfolio_params = DatabricksPortfolioRepository._build_preview_predicates(
            portfolio_criteria,
        )
        portfolio_clause = (
            "AND " + portfolio_where.removeprefix("WHERE ").strip()
            if portfolio_where
            else ""
        )
        if "target_lender_ref" in portfolio_params:
            lender_clause = ""
            lender_params = {}
        if (
            normalised_states
            or normalised_zips
            or normalised_county
            or normalised_borrower_ids
            or lender_clause
            or portfolio_clause
        ):
            params: dict[str, object] = dict(segment_params)
            params.update(lender_params)
            params.update(portfolio_params)
            params.update(lifecycle_params_geo)
            sql = self._COUNT_BY_GEO_SQL_TEMPLATE.format(
                state_clause=self._in_clause(column="b.state", prefix="state", values=normalised_states, params=params),
                zip_clause=self._in_clause(column="b.zip", prefix="zip", values=normalised_zips, params=params),
                county_clause=self._in_clause(column="b.county_fips_5", prefix="county", values=normalised_county, params=params),
                borrower_clause=self._in_clause(column="b.borrower_id", prefix="borrower_id", values=normalised_borrower_ids, params=params),
                segment_clause=f"AND {segment_clause}" if segment_clause else "",
                lender_clause=lender_clause,
                portfolio_clause=portfolio_clause,
                lifecycle_clause=lifecycle_clause_geo,
            )
            row = self._client.execute_one(sql, params)
            return int((row or {}).get("n") or 0)
        lifecycle_clause, lifecycle_params = self._lifecycle_filter_clause(
            source_alias="lp",
            approval_status=approval_status,
            outreach_status=outreach_status,
            aged_days=aged_days,
        )
        if segment_clause:
            segment_params = {**segment_params, **lifecycle_params}
            row = self._client.execute_one(
                self._COUNT_FILTERED_SQL_TEMPLATE.format(
                    segment_clause=segment_clause,
                    lifecycle_clause=lifecycle_clause,
                ),
                segment_params,
            )
            return int((row or {}).get("n") or 0)
        row = self._client.execute_one(
            self._COUNT_BASE_SQL.format(lifecycle_clause=lifecycle_clause),
            lifecycle_params,
        )
        return int((row or {}).get("n") or 0)

    @staticmethod
    def _normalise_states(
        state: str | None,
        state_codes: list[str] | None,
    ) -> list[str]:
        raw = ([state] if state else []) + (state_codes or [])
        out: list[str] = []
        for value in raw:
            code = str(value or "").upper()[:2]
            if len(code) == 2 and code.isalpha() and code not in out:
                out.append(code)
        return out

    @staticmethod
    def _normalise_zips(
        zip_code: str | None,
        zip_codes: list[str] | None,
    ) -> list[str]:
        raw = ([zip_code] if zip_code else []) + (zip_codes or [])
        out: list[str] = []
        for value in raw:
            code = str(value or "").strip()[:5]
            if len(code) == 5 and code.isdigit() and code not in out:
                out.append(code)
        return out

    @staticmethod
    def _normalise_county_fips(
        county_fips: str | None,
        county_fipses: list[str] | None = None,
    ) -> list[str]:
        raw = ([county_fips] if county_fips else []) + (county_fipses or [])
        out: list[str] = []
        for value in raw:
            code = str(value or "").strip()[:5]
            if len(code) == 5 and code.isdigit() and code not in out:
                out.append(code)
        return out

    @staticmethod
    def _normalise_borrower_ids(
        borrower_ids: list[str] | None,
    ) -> list[str]:
        out: list[str] = []
        for value in borrower_ids or []:
            try:
                borrower_id = validate_public_borrower_id(str(value or ""))
            except ValueError:
                continue
            if borrower_id not in out:
                out.append(borrower_id)
        return out

    @staticmethod
    def _lifecycle_filter_clause(
        *,
        source_alias: str,
        approval_status: str | None,
        outreach_status: str | None,
        aged_days: int | None,
    ) -> tuple[str, dict[str, object]]:
        clauses: list[str] = []
        params: dict[str, object] = {}
        if approval_status:
            clauses.append(
                f"COALESCE(ls.approval_status, {source_alias}.approval_status, 'pending') = :approval_status"
            )
            params["approval_status"] = approval_status
        if outreach_status:
            clauses.append("COALESCE(ls.outreach_status, 'none') = :outreach_status")
            params["outreach_status"] = outreach_status
        if aged_days is not None:
            bounded_days = max(1, min(int(aged_days), 90))
            clauses.append(
                f"COALESCE(ls.approval_status, {source_alias}.approval_status, 'pending') = 'approved'"
            )
            clauses.append("ls.approved_at <= current_timestamp() - INTERVAL " f"{bounded_days} DAYS")
            clauses.append("ls.outreach_at IS NULL")
        if not clauses:
            return "", {}
        return "AND " + " AND ".join(clauses), params

    @staticmethod
    def _in_clause(
        *,
        column: str,
        prefix: str,
        values: list[str],
        params: dict[str, object],
    ) -> str:
        if not values:
            return ""
        placeholders: list[str] = []
        for i, value in enumerate(values):
            key = f"{prefix}_{i}"
            params[key] = value
            placeholders.append(f":{key}")
        if len(placeholders) == 1:
            return f"AND {column} = {placeholders[0]}"
        return f"AND {column} IN ({', '.join(placeholders)})"

    @staticmethod
    def _normalise_segment_codes(
        segment: str | None,
        segment_codes: list[str] | None,
    ) -> list[str]:
        raw = segment_codes if segment_codes else ([segment] if segment else [])
        # Preserve caller order for deterministic parameter names while
        # dropping duplicates and blanks.
        out: list[str] = []
        seen: set[str] = set()
        for code in raw:
            normalised = str(code or "").strip()
            if not normalised or normalised in seen:
                continue
            seen.add(normalised)
            out.append(normalised)
        return out

    @classmethod
    def _segment_filter_clause(
        cls,
        *,
        segment: str | None,
        segment_codes: list[str] | None,
        segment_mode: str,
    ) -> tuple[str, dict[str, object]]:
        codes = cls._normalise_segment_codes(segment, segment_codes)
        if not codes:
            return "", {}
        if len(codes) == 1:
            return "array_contains(segment_codes, :segment)", {"segment": codes[0]}
        if segment_mode == "all":
            params = {f"segment_{i}": code for i, code in enumerate(codes)}
            clause = " AND ".join(
                f"array_contains(segment_codes, :segment_{i})"
                for i in range(len(codes))
            )
            return clause, params
        params = {f"segment_{i}": code for i, code in enumerate(codes)}
        clause = " OR ".join(
            f"array_contains(segment_codes, :segment_{i})"
            for i in range(len(codes))
        )
        return f"({clause})", params

    @classmethod
    def _bound_limit(cls, limit: int | None) -> int:
        """Clamp a caller-supplied ``limit`` to [1, MAX_LIMIT].

        ``None`` / 0 / negative values collapse to ``DEFAULT_LIMIT`` so the
        SQL stays a literal integer (no binding for LIMIT) and can't be
        spoofed into pulling the whole table.
        """
        if limit is None or limit <= 0:
            return cls.DEFAULT_LIMIT
        return min(int(limit), cls.MAX_LIMIT)


class DatabricksBorrowerRepository:
    """Borrower-360 + evidence reads from ``gold.borrower_dossier``.

    Slice13-accuracy perf: the dossier table pre-joins ``borrower_360`` +
    the top-20 ``evidence_events`` per CLIP into a single row keyed on
    ``borrower_id`` (Delta liquid cluster). The prior implementation fanned
    two warehouse statements out on a ``ThreadPoolExecutor`` to drop the
    p95 from ~4.6 s to ~3.3 s; folding them into one indexed row read
    collapses the warehouse round-trip count from 2 to 1 and pushes the
    p95 toward the 2-s load-test target.
    """

    def __init__(self, client: DatabricksSqlClient) -> None:
        self._client = client

    _GET_SQL = (
        f"SELECT {_BORROWER_DOSSIER_COLUMNS} "
        f"FROM {qualify('gold', 'borrower_dossier')} "
        "WHERE borrower_id = :borrower_id "
        "LIMIT 1"
    )

    _SEARCH_SQL_TEMPLATE = (
        "WITH latest_counties AS ( "
        "  SELECT fips_5, state, county_name "
        f"  FROM {qualify('gold', 'county_rollup')} "
        f"  WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM {qualify('gold', 'county_rollup')}) "
        ") "
        "SELECT "
        "  b.clip, b.borrower_id, "
        "  CONCAT('Owner ', SUBSTR(b.owner_name_hash, 1, 8)) AS display_name, "
        "  b.city, b.state, b.zip, b.segment_codes, "
        "  b.equity_estimate, b.rate_spread_bps, b.opportunity_score, b.confidence, "
        "  b.recommended_offer_code, b.recommended_offer, b.why_now, b.evidence_ids, b.approval_status, "
        "  b.current_lender_ref, "
        "  b.is_owner_occupied, b.is_investor, b.is_current_customer, "
        "  b.is_former_customer, b.is_competitor_lien, b.related_property_count, "
        "  b.current_lien_balance, b.second_pos_amount, b.has_permit, b.listed_for_sale, "
        "  b.marketing_eligible, b.consent_status, b.suppression_reason, b.last_touch_at, "
        "  b.eligible_recontact_at "
        f"FROM {qualify('gold', 'borrower_360')} AS b "
        "LEFT JOIN latest_counties AS cr "
        "  ON cr.fips_5 = b.county_fips_5 "
        "WHERE UPPER(b.borrower_id) LIKE :borrower_prefix "
        "   OR b.zip = :zip_exact "
        "   OR b.zip LIKE :zip_prefix "
        "   OR UPPER(b.city) LIKE :term_contains "
        "   OR b.clip = :clip_exact "
        "   OR b.state = :state_exact "
        "   OR UPPER(cr.county_name) LIKE :county_contains "
        "{county_fips_clause} "
        "ORDER BY "
        "  CASE "
        "    WHEN UPPER(b.borrower_id) = :borrower_exact THEN 0 "
        "    WHEN b.zip = :zip_exact THEN 1 "
        "    WHEN b.state = :state_exact THEN 2 "
        "    ELSE 3 "
        "  END, "
        "  b.opportunity_score DESC, b.borrower_id ASC "
        "LIMIT {limit}"
    )

    # Fallback evidence fetch — preserved so /api/borrowers/{id}/evidence
    # can still serve a borrower whose dossier row was rebuilt between
    # refreshes AND whose evidence_events array is somehow empty (schema
    # drift, upstream outage). The primary path reads from the dossier's
    # evidence array below.
    _EVIDENCE_SQL = (
        f"SELECT {_EVIDENCE_COLUMNS} "
        f"FROM {qualify('gold', 'evidence_events')} "
        "WHERE clip = ("
        f"  SELECT clip FROM {qualify('gold', 'borrower_dossier')} "
        "  WHERE borrower_id = :borrower_id LIMIT 1"
        ") "
        "ORDER BY signal_rank ASC"
    )

    def get(self, borrower_id: str) -> Borrower360 | None:
        # Single-statement indexed lookup on the dossier cluster key
        # (borrower_id). Evidence + trigger timeline are both pre-joined
        # as ARRAY<STRUCT> columns, so no fan-out is needed.
        row = self._client.execute_one(
            self._GET_SQL, {"borrower_id": borrower_id}
        )
        if row is None:
            return None

        redacted = redact_borrower_row(row)

        # Evidence: dossier carries up to 20 rows per CLIP as a parsed
        # ARRAY<STRUCT> (``_coerce`` in databricks_sql.py decodes JSON
        # arrays into list-of-dict). Apply the same redaction pipeline
        # used by the standalone evidence() endpoint so PII posture is
        # preserved.
        raw_evidence = row.get("evidence_events") or []
        evidence_events = _redact_evidence_list(raw_evidence)

        # Trigger timeline: dossier pre-materialised the top-3 as its own
        # ARRAY<STRUCT>. Prefer that; fall back to the JSON string form
        # (old borrower_360 path) then to the top-3 of full-evidence so
        # the dossier stays defensible against an empty-array column
        # (raised by Copilot 2026-04-22 — [:1] under-populated the UI).
        raw_timeline = row.get("trigger_timeline") or []
        timeline_events = _redact_evidence_list(raw_timeline)
        if not timeline_events:
            timeline_events = _parse_timeline(row.get("trigger_timeline_json"))
        if not timeline_events and evidence_events:
            timeline_events = evidence_events[:3]

        # Plain-English "why now" string for the dossier rationale box.
        # Updated 2026-04-22 (fix/copilot-batch-post-merge) to drop
        # rule-engine phrasing like "+246 bps spread (>= 75) AND 79%
        # equity (>= 15%)" in favour of language a VP of Lending or
        # compliance reviewer reads fluidly. The numbers still ground
        # the claim (an approver wants concrete detail) but without bps
        # or ">=" syntax.
        itm_flag = _coerce_bool(row.get("in_the_money"))
        spread_bps = int(row.get("rate_spread_bps") or 0)
        equity_pct = int(row.get("equity_pct") or 0)
        if itm_flag:
            # Translate bps to a qualitative phrase that still carries
            # the magnitude signal. Keeping the literal percentage for
            # equity because LOs / analysts naturally read "79% equity".
            if spread_bps >= 200:
                spread_descriptor = "well above market rates"
            elif spread_bps >= 100:
                spread_descriptor = "meaningfully above market rates"
            else:
                spread_descriptor = "above market rates"
            itm_reason = (
                f"Current rate sits {spread_descriptor} and the home has "
                f"{equity_pct}% equity -- both refinance triggers are met."
            )
        else:
            itm_reason = (
                f"Rate and equity (currently {equity_pct}%) have not yet "
                "cleared the refinance trigger; keep in nurture."
            )

        why_sources = [
            qualify("gold", "fn_rate_spread"),
            qualify("gold", "fn_in_the_money"),
            qualify("gold", "borrower_dossier"),
        ]
        why = WhyPanel(
            rate_spread_bps=spread_bps,
            market_rate=float(row.get("market_rate_fraction") or 0.0),
            equity_pct=equity_pct,
            in_the_money=itm_flag,
            in_the_money_reason=itm_reason,
            min_spread_bps=int(row.get("min_spread_bps_applied") or 75),
            min_equity_pct=int(row.get("min_equity_pct_applied") or 15),
            sources=why_sources,
            source_labels=[
                WhyPanelSource(name=s, display_label=source_display_label(s))
                for s in why_sources
            ],
        )

        # Enrich the redacted projection with the Borrower360 extras.
        # Note: every dependent construction works on the *redacted*
        # dict -- no raw PII is ever composed into the Pydantic object.
        borrower = Borrower360(
            **redacted,
            trigger_timeline=timeline_events,
            evidence_events=evidence_events,
            why_panel=why,
        )
        # Defence in depth: belt-and-suspenders ITM check against the
        # Python primitive so a row where gold drifted from Python
        # can't serialise without being caught in dev.
        _ = in_the_money(
            borrower.rate_spread_bps,
            int(row.get("equity_pct") or 0),
            why.min_spread_bps,
            why.min_equity_pct,
        )
        return borrower

    def evidence(self, borrower_id: str) -> list[EvidenceEvent] | None:
        # Prefer reading from the dossier's pre-joined evidence array —
        # one round-trip instead of two. If the dossier row carries
        # evidence (it almost always will; the CTAS emits an empty
        # ARRAY() only when a CLIP has zero live signals), serve it
        # directly. Otherwise fall back to the standalone gold.evidence_
        # events query so an empty-array dossier row (brand-new CLIP,
        # schema drift) still resolves.
        dossier_row = self._client.execute_one(
            f"SELECT clip, evidence_events FROM {qualify('gold', 'borrower_dossier')} "
            "WHERE borrower_id = :borrower_id LIMIT 1",
            {"borrower_id": borrower_id},
        )
        if dossier_row is None:
            return None  # router 404s for unknown borrower
        raw = dossier_row.get("evidence_events") or []
        if raw:
            return _redact_evidence_list(raw)
        # Dossier row present but evidence empty — fall back to direct
        # evidence_events lookup (belt-and-suspenders for upstream drift).
        rows = self._client.execute(self._EVIDENCE_SQL, {"borrower_id": borrower_id})
        if not rows:
            return []
        return [EvidenceEvent(**redact_evidence_row(r)) for r in rows]

    def search(self, query: str, limit: int = 10) -> list[LeadSummary]:
        term = str(query or "").strip()
        if len(term) < 2:
            return []
        bounded = max(1, min(int(limit or 10), 25))
        upper = term.upper()
        state_exact = self._state_search_code(term) or "__NO_STATE_MATCH__"
        zip_exact = term if term.isdigit() and len(term) == 5 else "__NO_ZIP_MATCH__"
        zip_prefix = f"{term}%" if term.isdigit() and 2 <= len(term) <= 5 else "__NO_ZIP_PREFIX__"
        county_term = re.sub(r"\bcounty\b", "", term, flags=re.IGNORECASE).strip()
        county_contains = f"%{county_term.upper()}%" if len(county_term) >= 2 else "__NO_COUNTY_MATCH__"
        county_fipses = county_fips_for_name(county_term, limit=25)
        county_fips_clause = ""
        county_params: dict[str, object] = {}
        if county_fipses:
            placeholders: list[str] = []
            for i, fips in enumerate(county_fipses):
                key = f"county_fips_{i}"
                county_params[key] = fips
                placeholders.append(f":{key}")
            county_fips_clause = f"   OR b.county_fips_5 IN ({', '.join(placeholders)}) "
        rows = self._client.execute(
            self._SEARCH_SQL_TEMPLATE.format(limit=bounded, county_fips_clause=county_fips_clause),
            {
                "borrower_exact": upper,
                "borrower_prefix": f"{upper}%",
                "term_contains": f"%{upper}%",
                "zip_exact": zip_exact,
                "zip_prefix": zip_prefix,
                "clip_exact": term,
                "state_exact": state_exact,
                "county_contains": county_contains,
                **county_params,
            },
        )
        return [LeadSummary(**redact_lead_row(r)) for r in rows]

    @staticmethod
    def _state_search_code(term: str) -> str | None:
        normalized = re.sub(r"[^a-z\s]+", " ", term.lower()).strip()
        normalized = re.sub(r"\s+", " ", normalized)
        upper = term.strip().upper()
        codes = {code for _name, code in _US_STATE_FILTERS}
        if len(upper) == 2 and upper in codes:
            return upper
        if len(normalized) < 3:
            return None
        for name, code in _US_STATE_FILTERS:
            if name == normalized or name.startswith(normalized):
                return code
        return None


class DatabricksOfferRepository:
    """Offer-input bundle used by the offers router to build a recommendation."""

    def __init__(self, client: DatabricksSqlClient) -> None:
        self._client = client

    _SQL = (
        "SELECT "
        "  rate_spread_bps, equity_pct, has_permit, listed_for_sale, "
        "  is_investor, is_current_customer, is_competitor_lien, "
        "  recommended_offer_code "
        f"FROM {qualify('gold', 'borrower_360')} "
        "WHERE borrower_id = :borrower_id "
        "LIMIT 1"
    )

    def get_offer_inputs(self, borrower_id: str) -> dict[str, object] | None:
        row = self._client.execute_one(self._SQL, {"borrower_id": borrower_id})
        if row is None:
            return None
        code = row.get("recommended_offer_code") or "nurture"
        # Defence in depth: contract expects a known code; surface an
        # obvious label if gold drifted.
        if code not in NBO_PRODUCT_LABELS:
            code = "nurture"
        return {
            "rate_spread_bps": int(row.get("rate_spread_bps") or 0),
            "equity_pct": int(row.get("equity_pct") or 0),
            "has_permit": _coerce_bool(row.get("has_permit")),
            "listed_for_sale": _coerce_bool(row.get("listed_for_sale")),
            "is_investor": _coerce_bool(row.get("is_investor")),
            "is_current_customer": _coerce_bool(row.get("is_current_customer")),
            "is_competitor_lien": _coerce_bool(row.get("is_competitor_lien")),
            "offer_code": code,
        }


class DatabricksOutreachRepository:
    """Borrower lookup for outreach draft composition -- same projection
    as ``BorrowerRepository.get`` but carved separately so outreach can
    grow draft-specific columns (opt-out, channel-preference) without
    widening the borrower surface.
    """

    def __init__(self, client: DatabricksSqlClient, borrower_repo: DatabricksBorrowerRepository) -> None:
        self._client = client
        self._borrower_repo = borrower_repo

    def find_borrower(self, borrower_id: str) -> Borrower360 | None:
        return self._borrower_repo.get(borrower_id)


class DatabricksGeoRepository:
    """Geography rollups for the USChoroplethMap.

    Reads three gold tables:

    * ``mip.gold.funnel_snapshot_daily`` (state rollup) -- latest
      snapshot, per-state ``_ALL`` segment row. Powers the hover
      tooltip (addressable / in-the-money / top-tier / avg_score) plus
      the state-fill level on the choropleth.
    * ``mip.gold.state_top_segment`` -- LEFT JOIN on state, latest
      snapshot, to surface the dominant SegmentCode per state on the
      ``StateRollup.top_segment_code`` extension.
    * ``mip.gold.county_rollup`` -- filtered to the given state at the
      latest snapshot.
    * ``mip.gold.zip_rollup`` -- filtered to the given county FIPS at
      the latest snapshot.

    Short-TTL cached (60s default) per-method so a presenter clicking
    between segment-intelligence and home pays one warehouse round-trip
    per minute, not per navigation. The data refreshes daily upstream
    so 60s is a non-issue for correctness.
    """

    def __init__(
        self,
        client: DatabricksSqlClient,
        *,
        cache: TTLCache | None = None,
        cache_ttl_s: float = 60.0,
    ) -> None:
        self._client = client
        self._cache = cache if cache is not None else TTLCache()
        self._cache_ttl_s = cache_ttl_s

    # State rollup: join the funnel snapshot (counts) with the top-segment
    # table (dominant SegmentCode). LEFT JOIN on state so an empty
    # state_top_segment (first deploy before the CTAS has run) still
    # returns state counts -- top_segment_code just stays NULL.
    _STATE_SQL = (
        "SELECT "
        "  f.state                         AS state, "
        "  f.addressable_borrowers         AS addressable, "
        "  f.in_the_money_borrowers        AS in_the_money, "
        "  f.high_opportunity_borrowers    AS top_tier_opportunities, "
        "  f.avg_opportunity_score         AS avg_score, "
        "  f.snapshot_date                 AS snapshot_date, "
        "  ts.top_segment_code             AS top_segment_code "
        f"FROM {qualify('gold', 'funnel_snapshot_daily')} AS f "
        "LEFT JOIN ( "
        "  SELECT state, top_segment_code "
        f"  FROM {qualify('gold', 'state_top_segment')} "
        f"  WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM {qualify('gold', 'state_top_segment')}) "
        ") AS ts ON ts.state = f.state "
        "WHERE f.state <> '_ALL' "
        "  AND f.segment_code = '_ALL' "
        f"  AND f.snapshot_date = (SELECT MAX(snapshot_date) FROM {qualify('gold', 'funnel_snapshot_daily')}) "
        "ORDER BY f.addressable_borrowers DESC"
    )

    _COUNTY_SQL = (
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

    _ZIP_SQL = (
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
        "WHERE county_fips_5 = :fips_5 "
        f"  AND snapshot_date = (SELECT MAX(snapshot_date) FROM {qualify('gold', 'zip_rollup')}) "
        "ORDER BY addressable_borrowers DESC"
    )

    _STATE_CACHE_KEY = "geo.state_rollups"
    _SCOPE_CACHE_KEY = "geo.scope"

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
    _STATE_FILTER_SQL_TPL = (
        "SELECT "
        "  state                                        AS state, "
        "  CAST(COUNT(*) AS INT)                         AS addressable, "
        "  CAST(SUM(CASE WHEN in_the_money "
        "                THEN 1 ELSE 0 END) AS INT)      AS in_the_money, "
        "  CAST(SUM(CASE WHEN opportunity_score >= 75 "
        "                THEN 1 ELSE 0 END) AS INT)      AS top_tier_opportunities, "
        "  CAST(ROUND(AVG(opportunity_score)) AS INT)    AS avg_score "
        f"FROM {qualify('gold', 'borrower_360')} "
        "WHERE {filter_clause} "
        "GROUP BY state"
    )

    _COUNTY_FILTER_SQL_TPL = (
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
        "    CAST(SUM(CASE WHEN opportunity_score >= 75 THEN 1 ELSE 0 END) AS INT) AS high_opportunity_borrowers, "
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

    _ZIP_FILTER_SQL_TPL = (
        "WITH base AS ( "
        "  SELECT "
        "    zip, "
        "    state, "
        "    county_fips_5, "
        "    borrower_id, "
        "    opportunity_score, "
        "    segment_codes "
        f"  FROM {qualify('gold', 'borrower_360')} "
        "  WHERE county_fips_5 = :fips_5 "
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

    def state_rollups(
        self,
        segment_codes: list[str] | None = None,
        segment_mode: str = "any",
        portfolio_criteria: PortfolioCriteria | None = None,
    ) -> StateRollupResponse:
        if segment_codes or portfolio_criteria is not None:
            # Filtered path. Cache key includes the sorted tuple so two
            # callers asking the same filter share the cache while a
            # different filter doesn't poison the result. The unfiltered
            # `_ALL` path keeps its own _STATE_CACHE_KEY so the most
            # common request (no filter) stays warm.
            normalised = self._normalise_geo_segments(segment_codes)
            portfolio_key = self._portfolio_cache_key(portfolio_criteria)
            cache_key = (
                f"{self._STATE_CACHE_KEY}:filtered:{segment_mode}:"
                f"{','.join(normalised)}:{portfolio_key}"
            )
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached
            filter_clause, params = self._geo_filter_clause(
                normalised,
                segment_mode=segment_mode,
                portfolio_criteria=portfolio_criteria,
            )
            sql = self._STATE_FILTER_SQL_TPL.format(
                filter_clause=filter_clause,
            )
            rows = self._client.execute(
                sql,
                params,
            ) or []
            # in_the_money + top_segment_code aren't carried by the
            # filtered query (they would require an extra join the
            # tooltip doesn't surface). Set sentinel zeros so the
            # response shape stays stable; the FE only reads
            # `addressable` for the filtered tooltip path.
            rollups = [
                StateRollup(
                    state=str(r.get("state") or "").upper()[:2],
                    addressable=int(r.get("addressable") or 0),
                    in_the_money=int(r.get("in_the_money") or 0),
                    top_tier_opportunities=int(r.get("top_tier_opportunities") or 0),
                    avg_score=int(r.get("avg_score") or 0),
                    top_segment_code=None,
                )
                for r in rows
                if r.get("state") and str(r.get("state")) != "_ALL"
            ]
            response = StateRollupResponse(rollups=rollups, snapshot_date=None)
            self._cache.set(cache_key, response, self._cache_ttl_s)
            return response

        # Unfiltered path — unchanged behaviour.
        cached = self._cache.get(self._STATE_CACHE_KEY)
        if cached is not None:
            return cached
        rows = self._client.execute(self._STATE_SQL) or []
        rollups = [
            StateRollup(
                state=str(r.get("state") or "").upper()[:2],
                addressable=int(r.get("addressable") or 0),
                in_the_money=int(r.get("in_the_money") or 0),
                top_tier_opportunities=int(r.get("top_tier_opportunities") or 0),
                avg_score=int(r.get("avg_score") or 0),
                top_segment_code=(
                    str(r["top_segment_code"]) if r.get("top_segment_code") else None
                ),
            )
            for r in rows
            if r.get("state") and str(r.get("state")) != "_ALL"
        ]
        snapshot_date: str | None = None
        if rows:
            raw = rows[0].get("snapshot_date")
            snapshot_date = str(raw) if raw is not None else None
        response = StateRollupResponse(rollups=rollups, snapshot_date=snapshot_date)
        self._cache.set(self._STATE_CACHE_KEY, response, self._cache_ttl_s)
        return response

    @staticmethod
    def _state_segment_filter_clause(
        segment_codes: list[str],
        *,
        segment_mode: str,
    ) -> tuple[str, dict[str, object]]:
        if len(segment_codes) == 1:
            return "array_contains(segment_codes, :segment)", {"segment": segment_codes[0]}
        if segment_mode == "all":
            params = {f"segment_{i}": code for i, code in enumerate(segment_codes)}
            clause = " AND ".join(
                f"array_contains(segment_codes, :segment_{i})"
                for i in range(len(segment_codes))
            )
            return clause, params
        params = {f"segment_{i}": code for i, code in enumerate(segment_codes)}
        clause = " OR ".join(
            f"array_contains(segment_codes, :segment_{i})"
            for i in range(len(segment_codes))
        )
        return f"({clause})", params

    @staticmethod
    def _portfolio_cache_key(portfolio_criteria: PortfolioCriteria | None) -> str:
        if portfolio_criteria is None:
            return "_none"
        return json.dumps(portfolio_criteria.model_dump(exclude_none=True), sort_keys=True)

    @classmethod
    def _geo_filter_clause(
        cls,
        segment_codes: list[str],
        *,
        segment_mode: str,
        portfolio_criteria: PortfolioCriteria | None,
    ) -> tuple[str, dict[str, object]]:
        clauses: list[str] = []
        params: dict[str, object] = {}
        if segment_codes:
            segment_clause, segment_params = cls._state_segment_filter_clause(
                segment_codes,
                segment_mode=segment_mode,
            )
            clauses.append(segment_clause)
            params.update(segment_params)
        portfolio_where, portfolio_params = DatabricksPortfolioRepository._build_preview_predicates(
            portfolio_criteria,
        )
        if portfolio_where:
            clauses.append(portfolio_where.removeprefix("WHERE ").strip())
            params.update(portfolio_params)
        return (" AND ".join(clauses) if clauses else "1 = 1"), params

    @staticmethod
    def _normalise_geo_segments(segment_codes: list[str] | None) -> list[str]:
        if not segment_codes:
            return []
        out: list[str] = []
        seen: set[str] = set()
        for code in segment_codes:
            normalised = str(code or "").strip()
            if not normalised or normalised in seen:
                continue
            seen.add(normalised)
            out.append(normalised)
        return sorted(out)

    @staticmethod
    def _filtered_geo_cache_key(
        prefix: str,
        grain: str,
        *,
        segment_codes: list[str],
        segment_mode: str,
    ) -> str:
        return (
            f"{prefix}:{grain}:filtered:{segment_mode}:"
            f"{','.join(segment_codes)}"
        )

    def county_rollups(
        self,
        state: str,
        segment_codes: list[str] | None = None,
        segment_mode: str = "any",
        portfolio_criteria: PortfolioCriteria | None = None,
    ) -> CountyRollupResponse:
        """Fetch per-county rollups for the given state.

        ``state`` is normalised to 2-char uppercase before the warehouse
        call so the response is stable regardless of UI casing. Returns
        an empty list + ``snapshot_date=None`` when the state has no
        Cotality-backed county coverage or the CTAS hasn't run yet.
        """
        normalised = str(state or "").upper()[:2]
        normalised_segments = self._normalise_geo_segments(segment_codes)
        use_filtered_path = bool(normalised_segments) or portfolio_criteria is not None
        portfolio_key = self._portfolio_cache_key(portfolio_criteria)
        cache_key = (
            self._filtered_geo_cache_key(
                "geo.county_rollups",
                normalised,
                segment_codes=normalised_segments,
                segment_mode=segment_mode,
            )
            + f":{portfolio_key}"
            if use_filtered_path
            else f"geo.county_rollups:{normalised}"
        )
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        if use_filtered_path:
            filter_clause, params = self._geo_filter_clause(
                normalised_segments,
                segment_mode=segment_mode,
                portfolio_criteria=portfolio_criteria,
            )
            params["state"] = normalised
            sql = self._COUNTY_FILTER_SQL_TPL.format(
                filter_clause=filter_clause,
            )
            rows = self._client.execute(sql, params) or []
        else:
            rows = self._client.execute(self._COUNTY_SQL, {"state": normalised}) or []
        rollups = [
            CountyRollup(
                fips_5=str(r.get("fips_5") or "")[:5],
                state=str(r.get("state") or "").upper()[:2] or normalised,
                county_name=(
                    str(r["county_name"]) if r.get("county_name") else None
                )
                or county_name_for_fips(str(r.get("fips_5") or "")[:5]),
                addressable_borrowers=int(r.get("addressable_borrowers") or 0),
                in_the_money_borrowers=int(r.get("in_the_money_borrowers") or 0),
                high_opportunity_borrowers=int(r.get("high_opportunity_borrowers") or 0),
                avg_opportunity_score=int(r.get("avg_opportunity_score") or 0),
                top_segment_code=(
                    str(r["top_segment_code"]) if r.get("top_segment_code") else None
                ),
            )
            for r in rows
            if r.get("fips_5") and len(str(r.get("fips_5"))) == 5
        ]
        snapshot_date: str | None = None
        if rows:
            raw = rows[0].get("snapshot_date")
            snapshot_date = str(raw) if raw is not None else None
        scope_note = self._scope_note_for_state(
            normalised,
            returned_count=len(rollups),
        )
        response = CountyRollupResponse(
            state=normalised,
            rollups=rollups,
            snapshot_date=snapshot_date,
            scope_note=scope_note if rollups else None,
        )
        self._cache.set(cache_key, response, self._cache_ttl_s)
        return response

    def _geography_scope(self) -> GeographyScope | None:
        cached = self._cache.get(self._SCOPE_CACHE_KEY)
        if cached is not None:
            return cached
        try:
            scope = load_geography_scope(self._client)
        except Exception as exc:  # noqa: BLE001 -- scope copy is non-critical
            log.warning("geo scope discovery failed: %s", exc)
            return None
        self._cache.set(self._SCOPE_CACHE_KEY, scope, self._cache_ttl_s)
        return scope

    def _scope_note_for_state(self, state: str, *, returned_count: int) -> str | None:
        scope = self._geography_scope()
        if scope is not None:
            return scope.state_scope_label(state, returned_count=returned_count)
        if returned_count <= 0:
            return None
        county_word = "county" if returned_count == 1 else "counties"
        state_uc = str(state or "").upper()[:2]
        return f"Cotality data coverage for {state_uc}: {returned_count:,} {county_word} returned"

    def zip_rollups(
        self,
        fips_5: str,
        segment_codes: list[str] | None = None,
        segment_mode: str = "any",
        portfolio_criteria: PortfolioCriteria | None = None,
    ) -> ZipRollupResponse:
        """Fetch per-ZIP rollups for the given 5-char county FIPS."""
        normalised = str(fips_5 or "")[:5]
        normalised_segments = self._normalise_geo_segments(segment_codes)
        use_filtered_path = bool(normalised_segments) or portfolio_criteria is not None
        portfolio_key = self._portfolio_cache_key(portfolio_criteria)
        cache_key = (
            self._filtered_geo_cache_key(
                "geo.zip_rollups",
                normalised,
                segment_codes=normalised_segments,
                segment_mode=segment_mode,
            )
            + f":{portfolio_key}"
            if use_filtered_path
            else f"geo.zip_rollups:{normalised}"
        )
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        if use_filtered_path:
            filter_clause, params = self._geo_filter_clause(
                normalised_segments,
                segment_mode=segment_mode,
                portfolio_criteria=portfolio_criteria,
            )
            params["fips_5"] = normalised
            sql = self._ZIP_FILTER_SQL_TPL.format(
                filter_clause=filter_clause,
            )
            rows = self._client.execute(sql, params) or []
        else:
            rows = self._client.execute(self._ZIP_SQL, {"fips_5": normalised}) or []
        rollups = [
            ZipRollup(
                zip=str(r.get("zip") or "")[:5],
                state=str(r.get("state") or "").upper()[:2],
                county_fips_5=(
                    str(r["county_fips_5"]) if r.get("county_fips_5") else None
                ),
                addressable_borrowers=int(r.get("addressable_borrowers") or 0),
                avg_opportunity_score=int(r.get("avg_opportunity_score") or 0),
                top_segment_code=(
                    str(r["top_segment_code"]) if r.get("top_segment_code") else None
                ),
                sample_borrower_id=(
                    str(r["sample_borrower_id"]) if r.get("sample_borrower_id") else None
                ),
            )
            for r in rows
            if r.get("zip") and len(str(r.get("zip"))) == 5
        ]
        snapshot_date: str | None = None
        if rows:
            raw = rows[0].get("snapshot_date")
            snapshot_date = str(raw) if raw is not None else None
        response = ZipRollupResponse(
            fips_5=normalised,
            rollups=rollups,
            snapshot_date=snapshot_date,
        )
        self._cache.set(cache_key, response, self._cache_ttl_s)
        return response


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _coerce_bool(value: Any) -> bool:
    """Warehouse booleans arrive as Python bool via the coercion in
    ``databricks_sql._coerce`` -- but defend against raw strings just
    in case a future API change emits 'true' / 'false' literals.
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in ("true", "1", "t", "yes")


class DatabricksGenieRepository:
    """Real Genie, then an honest "warming up" degraded message.

    Control flow:

    1. If the ``genie`` circuit breaker is OPEN, return the honest
       "Genie is warming up — try again in a few seconds" message.
    2. Otherwise call ``ResilientGenieClient.ask(question)``. On
       success, adapt ``GenieResponse`` into the ``GenieMessageResponse``
       wire contract (``source="genie"``).
    3. If the call fails with ``DependencyDownError`` (breaker just
       opened on us), return the warming-up message — same path as (1).
    4. On any other exception, re-raise so the router's 503 translation
       engages — we never swallow to a mock answer.

    No path ever fabricates data. When Genie is unreachable we show the
    user a single honest message and let them retry; no answer body,
    metric, table row, or recommendation is generated without live Genie
    or trusted SQL proof.
    """

    _WARMING_MESSAGE = (
        "Genie is warming up — try that question again in a few seconds. "
        "Live answers come straight from the Mortgage Lead Intelligence "
        "Genie space once the connection is ready."
    )

    def __init__(
        self,
        genie: ResilientGenieClient,
        sql_client: DatabricksSqlClient | None = None,
    ) -> None:
        self._genie = genie
        self._sql_client = sql_client

    def respond(
        self,
        question: str,
        conversation_id: str | None = None,
    ) -> GenieMessageResponse:
        breaker_state = self._genie.resilient.breaker.state
        if breaker_state == "open":
            return self._degraded(question)
        try:
            result = self._genie.ask(question, conversation_id=conversation_id)
            if _needs_genie_sql_repair(question, result):
                result = self._repair_text_only_genie_answer(
                    question=question,
                    original=result,
                    conversation_id=conversation_id,
                )
        except DependencyDownError:
            return self._degraded(question)
        except GenieClientError:
            # Underlying Genie surfaced an unrecoverable response (401,
            # 500, malformed JSON). Re-raise so the router translates
            # to 503 + degraded UI. No silent mock fallback.
            raise
        return _adapt_genie_response(question, result, sql_client=self._sql_client)

    def _repair_text_only_genie_answer(
        self,
        *,
        question: str,
        original: GenieResponse,
        conversation_id: str | None,
    ) -> GenieResponse:
        """Retry once when a data question returns narrative without a query.

        This is not an answer-specific override. It asks the Genie space to
        regenerate any data-bearing response as a governed SELECT attachment so
        the normal SQL/source/freshness policy can validate it. If the repair
        turn still lacks proof, the original policy-block path remains in force.
        """
        try:
            repaired = self._genie.ask(
                _trusted_sql_repair_prompt(question),
                conversation_id=conversation_id or original.conversation_id,
            )
        except (DependencyDownError, GenieClientError):
            return original
        if _genie_response_has_query_proof(repaired):
            if original.conversation_id:
                repaired.conversation_id = original.conversation_id
            return repaired
        return original

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _degraded(self, question: str) -> GenieMessageResponse:
        """Honest "Genie is warming up" message with no fabricated content.

        Prompt suggestions are questions only. The degraded path does not
        inspect local analytics and cannot return rows, counts, or
        borrower examples.
        """
        return GenieMessageResponse(
            conversation_id="",
            question=question,
            answer=self._WARMING_MESSAGE,
            source="degraded",
            trusted_assets=[],
            question_hash=_genie_question_hash(question),
            proof=GenieProof(
                source_assets=[],
                row_count=0,
                trusted=False,
                known_data_gaps=_known_data_gaps(question, []),
                conversation_id=None,
                generated_at=datetime.now(UTC).isoformat(),
            ),
            follow_up_questions=default_follow_up_questions(),
        )


def _merge_trusted_assets(*asset_lists: list[str] | None) -> list[str]:
    merged: list[str] = []
    for assets in asset_lists:
        for raw in assets or []:
            asset = str(raw).replace("`", "").replace(" ", "").lower()
            if asset and asset not in merged:
                merged.append(asset)
    return merged


def _genie_response_has_query_proof(result: GenieResponse) -> bool:
    assets = _merge_trusted_assets(
        _extract_asset_refs(result.sql_query),
        result.trusted_assets,
    )
    return bool(result.sql_query and assets)


def _likely_data_question(question: str) -> bool:
    q = re.sub(r"[^a-z0-9\s-]+", " ", question.lower())
    q = re.sub(r"\s+", " ", q).strip()
    data_terms = (
        "how many",
        "count",
        "top",
        "highest",
        "which",
        "show",
        "list",
        "break down",
        "broken down",
        "compare",
        "average",
        "avg",
        "mean",
        "trend",
        "map",
        "where should",
    )
    domain_terms = (
        "borrower",
        "borrowers",
        "customer",
        "customers",
        "lead",
        "leads",
        "zip",
        "zips",
        "state",
        "segment",
        "score",
        "equity",
        "rate",
        "offer",
        "retention",
        "recapture",
        "risk",
        "heloc",
        "refi",
        "refinance",
        "lien",
        "msa",
        "cbsa",
    )
    return any(term in q for term in data_terms) and any(term in q for term in domain_terms)


def _needs_genie_sql_repair(question: str, result: GenieResponse) -> bool:
    if not _likely_data_question(question):
        return False
    if _answer_text_contains_pii(result.answer_text):
        return False
    if _sql_uses_stale_evidence_signal_enum(result.sql_query):
        return True
    if _sql_uses_impossible_retention_conjunction(question, result.sql_query):
        return True
    return not _genie_response_has_query_proof(result)


def _retention_risk_question(question: str) -> bool:
    q = question.lower()
    if _retention_competitor_lien_list_question(question):
        return False
    has_customer_scope = bool(re.search(r"\b(current|summit|customer|customers)\b", q))
    has_retention_risk_phrase = bool(re.search(r"\bretention[-\s]?risk\b", q))
    has_risk_intent = bool(
        re.search(
            r"\b(retention|recapture|at risk|risk of going|going to a competitor|"
            r"shop(?:ping)?(?: a)? competitor|competitor recapture)\b",
            q,
        )
    )
    if has_retention_risk_phrase:
        return True
    return has_customer_scope and has_risk_intent


def _retention_competitor_lien_list_question(question: str) -> bool:
    q = question.lower()
    asks_for_rows = bool(
        re.search(r"\b(which|show|list|find|who are|give me)\b", q)
        and re.search(r"\bborrowers?\b", q)
    )
    retention_scope = bool(
        re.search(
            r"\b(retention list|retention cohort|retention-risk|retention risk|recapture)\b",
            q,
        )
    )
    competitor_signal = "competitor lien" in q or "competitor-lien" in q
    return asks_for_rows and retention_scope and competitor_signal


def _sql_uses_stale_evidence_signal_enum(sql_query: str | None) -> bool:
    if not sql_query:
        return False
    sql = re.sub(r"\s+", " ", sql_query.strip().lower())
    if not re.search(r"\bevidence_events\b", sql):
        return False
    for literal in ("lien-change", "lien_change", "competitor"):
        if re.search(rf"\bsignal_type\s*=\s*['\"]{re.escape(literal)}['\"]", sql):
            return True
        if re.search(rf"\bsignal_type\s+in\s*\([^)]*['\"]{re.escape(literal)}['\"]", sql):
            return True
    return False


def _sql_uses_impossible_retention_conjunction(question: str, sql_query: str | None) -> bool:
    if not sql_query or not _retention_risk_question(question):
        return False
    sql = re.sub(r"\s+", " ", sql_query.strip().lower())
    return bool(
        re.search(r"\bis_current_customer\s*=\s*(true|1)\b", sql)
        and re.search(r"\bis_competitor_lien\s*=\s*(true|1)\b", sql)
    )


def _trusted_sql_repair_prompt(question: str) -> str:
    return (
        "Regenerate the following Mortgage Intelligence Platform data question "
        "as a governed analytics answer. Produce a read-only SQL SELECT query "
        "attachment over the trusted mip.gold or mip.semantics assets, execute "
        "it, return the result rows, and cite the source asset. Do not answer "
        "from narrative alone, do not use PII or protected-class criteria, and "
        "do not use catalogs outside mip. For current-customer retention or "
        "recapture-risk questions, use the retention risk signal already modeled "
        "in mip.gold.borrower_360 (segment_codes contains 'retention' or "
        "recommended_offer_code = 'retention') instead of requiring "
        "is_current_customer and is_competitor_lien to both be true. For evidence "
        "trigger questions, use the governed signal_type enum exactly as modeled; "
        "competitor-lien evidence is signal_type = 'competitor_lien', never "
        "'lien-change' or 'competitor'. User question: "
        f"{question}"
    )


def _adapt_genie_response(
    question: str,
    result: GenieResponse,
    *,
    sql_client: DatabricksSqlClient | None = None,
) -> GenieMessageResponse:
    """Wrap a live ``GenieResponse`` into the wire contract the UI
    already consumes. We derive ``trusted_assets`` from the SQL query
    when one is available (best-effort regex for ``mip.*``
    references); empty otherwise -- the UI tolerates an empty list.

    PII posture (Genie audit finding, 2026-04-23): Genie's Space ``instructions``
    block forbids returning PII columns, but that's model-compliance, not a
    guaranteed output-side filter. The repository boundary enforces defence-
    in-depth by stripping any row keys that match the governance denylist
    (owner names, raw CLIP, owner_link_id, owner_name_hash, street addresses)
    regardless of what the model decided to select. Customers see zero PII
    columns in Ask Genie results even if the Space drifts.
    """
    trusted_assets = _merge_trusted_assets(
        _extract_asset_refs(result.sql_query),
        result.trusted_assets,
    )
    rows = _redact_genie_rows(result.sql_result_rows)
    trusted_sql = _trusted_sql_policy(result.sql_query, trusted_assets)
    question_hash = _genie_question_hash(question)
    text_contains_pii = _answer_text_contains_pii(result.answer_text)
    lacks_trusted_proof = not result.sql_query or not trusted_assets
    gaps = _known_data_gaps_for_result(
        question=question,
        assets=trusted_assets,
        sql_query=result.sql_query,
        rows=rows,
    )
    source_readiness_gap_disclosure = (
        trusted_sql and _source_readiness_only_assets(trusted_assets)
    )
    depends_on_pending_feeds = bool(
        not source_readiness_gap_disclosure
        and (
            _pending_feed_gaps_from_material(result.sql_query or "")
            or _pending_feed_gaps_from_rows(rows)
        )
    )
    # Trusted SQL overlays are allowed to answer known grain-sensitive
    # questions only when the live Genie turn is not unsafe. They must not
    # mask PII, untrusted SQL, or pending-feed dependence. Text-only turns can
    # still be answered through this explicit `trusted_sql` source so users see
    # that the app used a governed canonical query rather than the raw Genie
    # narrative.
    unsafe_live_sql = bool(result.sql_query and not trusted_sql)
    stale_evidence_enum_only = bool(
        result.sql_query and _trusted_sql_policy_allowing_stale_evidence_enum(result.sql_query, trusted_assets)
    )
    if (
        not text_contains_pii
        and (not unsafe_live_sql or stale_evidence_enum_only)
        and not depends_on_pending_feeds
    ):
        canonical = _canonical_genie_answer(
            question=question,
            result=result,
            sql_client=sql_client,
        )
        if canonical is not None:
            return canonical
    if (
        text_contains_pii
        or lacks_trusted_proof
        or unsafe_live_sql
        or depends_on_pending_feeds
    ):
        if depends_on_pending_feeds:
            gaps = _known_data_gaps_for_result(
                question=" ".join([question, "permit listing mls"]),
                assets=trusted_assets,
                sql_query=result.sql_query,
                rows=rows,
            )
        blocked_answer = (
            "Genie did not return trusted SQL and source assets for this answer, "
            "so the app did not display the result. Ask a scoped question over "
            "the trusted mortgage lead assets without PII or protected-class criteria."
        )
        if gaps:
            blocked_answer = f"{blocked_answer} Known data gap: {' '.join(gaps)}"
        proof = _build_genie_proof(
            sql_query=None,
            trusted_assets=trusted_assets,
            rows=[],
            question=question,
            conversation_id=result.conversation_id,
            message_id=result.message_id,
            elapsed_ms=result.elapsed_ms,
            reasoning_trace=result.thoughts,
        )
        if gaps:
            proof = proof.model_copy(update={"known_data_gaps": gaps})
        return GenieMessageResponse(
            conversation_id=result.conversation_id,
            message_id=result.message_id,
            elapsed_ms=result.elapsed_ms,
            question_hash=question_hash,
            question=question,
            answer=blocked_answer,
            source="policy_blocked",
            trusted_assets=trusted_assets,
            sql_query=None,
            row_count=0,
            proof=proof,
            table_rows=[],
        )
    if result.sql_query and trusted_sql and not rows and sql_client is not None:
        rows = _redact_genie_rows(_execute_trusted_genie_sql(sql_client, result.sql_query))
    proof = _build_genie_proof(
        sql_query=result.sql_query,
        trusted_assets=trusted_assets,
        rows=rows,
        question=question,
        conversation_id=result.conversation_id,
        message_id=result.message_id,
        elapsed_ms=result.elapsed_ms,
        reasoning_trace=result.thoughts,
    )
    visualization = _plan_genie_visualization(question, rows)
    actions = _suggest_genie_actions(
        question=question,
        rows=rows,
        trusted_assets=trusted_assets,
        visualization=visualization,
        conversation_id=result.conversation_id,
        message_id=result.message_id,
        question_hash=question_hash,
        sql_query=result.sql_query,
    )
    return GenieMessageResponse(
        conversation_id=result.conversation_id,
        message_id=result.message_id,
        elapsed_ms=result.elapsed_ms,
        question_hash=question_hash,
        question=question,
        answer=result.answer_text or "",
        source="genie",
        trusted_assets=trusted_assets,
        sql_query=result.sql_query,
        row_count=len(rows) if rows else 0,
        proof=proof,
        visualization=visualization,
        actions=actions,
        table_rows=rows,
    )


_CANONICAL_ITM_COUNT_SQL = """
SELECT COUNT(*) AS in_the_money_borrowers
     , MAX(refreshed_at) AS refreshed_at
FROM mip.gold.borrower_360
WHERE in_the_money = TRUE
""".strip()

_CANONICAL_ITM_COUNT_BY_STATE_SQL = """
SELECT COUNT(*) AS in_the_money_borrowers
     , MAX(refreshed_at) AS refreshed_at
FROM mip.gold.borrower_360
WHERE in_the_money = TRUE
  AND state = :state
""".strip()

_CANONICAL_ITM_COUNT_BY_CITY_SQL = """
SELECT COUNT(*) AS in_the_money_borrowers
     , MAX(refreshed_at) AS refreshed_at
FROM mip.gold.borrower_360
WHERE in_the_money = TRUE
  AND LOWER(city) = LOWER(:city)
""".strip()

_CANONICAL_ITM_TOP_ZIPS_SQL = """
SELECT zip
     , state
     , COUNT(*) AS in_the_money_borrowers
     , CAST(ROUND(AVG(opportunity_score), 1) AS DOUBLE) AS avg_score
     , MAX(refreshed_at) AS refreshed_at
FROM mip.gold.borrower_360
WHERE in_the_money = TRUE
  AND zip IS NOT NULL
  AND TRIM(zip) <> ''
GROUP BY zip, state
ORDER BY in_the_money_borrowers DESC, avg_score DESC, zip ASC
LIMIT 10
""".strip()

_CANONICAL_CURRENT_CUSTOMER_RETENTION_RISK_SQL = """
SELECT COUNT(*) AS retention_risk_borrowers
     , MAX(refreshed_at) AS refreshed_at
FROM mip.gold.borrower_360
WHERE is_current_customer = TRUE
  AND (
    array_contains(segment_codes, 'retention')
    OR recommended_offer_code = 'retention'
  )
""".strip()

_CANONICAL_RETENTION_COMPETITOR_LIEN_LIST_SQL = """
WITH matches AS (
  SELECT b.borrower_id
       , b.city
       , b.state
       , b.recommended_offer_code
       , b.opportunity_score
       , MAX(to_timestamp(e.`timestamp`)) AS latest_competitor_lien_at
  FROM mip.gold.borrower_360 AS b
  JOIN mip.gold.evidence_events AS e
    ON e.clip = b.clip
  WHERE array_contains(b.segment_codes, 'retention')
    AND e.signal_type = 'competitor_lien'
    AND to_timestamp(e.`timestamp`) >= current_timestamp() - interval 30 days
  GROUP BY b.borrower_id
         , b.city
         , b.state
         , b.recommended_offer_code
         , b.opportunity_score
),
ranked AS (
  SELECT borrower_id
       , city
       , state
       , recommended_offer_code
       , opportunity_score
       , latest_competitor_lien_at
       , COUNT(*) OVER () AS total_matching_borrowers
  FROM matches
)
SELECT borrower_id
     , city
     , state
     , recommended_offer_code
     , opportunity_score
     , latest_competitor_lien_at
     , total_matching_borrowers
FROM ranked
ORDER BY latest_competitor_lien_at DESC
       , opportunity_score DESC
       , borrower_id ASC
LIMIT 50
""".strip()

_CANONICAL_MSA_SCORE_SQL = """
WITH borrower_markets AS (
  SELECT situs_cbsa_code
       , COALESCE(NULLIF(city, ''), 'Unknown') AS city
       , state
       , opportunity_score
       , refreshed_at
  FROM mip.gold.borrower_360
  WHERE situs_cbsa_code IS NOT NULL
    AND TRIM(situs_cbsa_code) <> ''
),
market_scores AS (
  SELECT situs_cbsa_code AS msa_cbsa_code
       , CAST(COUNT(*) AS BIGINT) AS borrowers
       , CAST(ROUND(AVG(opportunity_score), 1) AS DOUBLE) AS avg_score
       , MAX(refreshed_at) AS refreshed_at
  FROM borrower_markets
  GROUP BY situs_cbsa_code
),
city_counts AS (
  SELECT situs_cbsa_code
       , city
       , state
       , COUNT(*) AS city_borrowers
  FROM borrower_markets
  GROUP BY situs_cbsa_code, city, state
),
city_ranked AS (
  SELECT situs_cbsa_code
       , city
       , state
       , city_borrowers
       , ROW_NUMBER() OVER (
           PARTITION BY situs_cbsa_code
           ORDER BY city_borrowers DESC, city ASC, state ASC
         ) AS rn
  FROM city_counts
)
SELECT CONCAT(cr.city, ', ', cr.state, ' (CBSA ', ms.msa_cbsa_code, ')') AS market
     , ms.msa_cbsa_code
     , ms.borrowers
     , ms.avg_score
     , ms.refreshed_at
FROM market_scores AS ms
LEFT JOIN city_ranked AS cr
  ON cr.situs_cbsa_code = ms.msa_cbsa_code
 AND cr.rn = 1
ORDER BY ms.borrowers DESC, ms.avg_score DESC, ms.msa_cbsa_code ASC
LIMIT 5
""".strip()

_US_STATE_FILTERS: tuple[tuple[str, str], ...] = (
    ("alabama", "AL"), ("alaska", "AK"), ("arizona", "AZ"), ("arkansas", "AR"),
    ("california", "CA"), ("colorado", "CO"), ("connecticut", "CT"), ("delaware", "DE"),
    ("florida", "FL"), ("georgia", "GA"), ("hawaii", "HI"), ("idaho", "ID"),
    ("illinois", "IL"), ("indiana", "IN"), ("iowa", "IA"), ("kansas", "KS"),
    ("kentucky", "KY"), ("louisiana", "LA"), ("maine", "ME"), ("maryland", "MD"),
    ("massachusetts", "MA"), ("michigan", "MI"), ("minnesota", "MN"),
    ("mississippi", "MS"), ("missouri", "MO"), ("montana", "MT"), ("nebraska", "NE"),
    ("nevada", "NV"), ("new hampshire", "NH"), ("new jersey", "NJ"),
    ("new mexico", "NM"), ("new york", "NY"), ("north carolina", "NC"),
    ("north dakota", "ND"), ("ohio", "OH"), ("oklahoma", "OK"), ("oregon", "OR"),
    ("pennsylvania", "PA"), ("rhode island", "RI"), ("south carolina", "SC"),
    ("south dakota", "SD"), ("tennessee", "TN"), ("texas", "TX"), ("utah", "UT"),
    ("vermont", "VT"), ("virginia", "VA"), ("washington", "WA"),
    ("west virginia", "WV"), ("wisconsin", "WI"), ("wyoming", "WY"),
)
_AMBIGUOUS_STATE_CODES: frozenset[str] = frozenset({"HI", "ID", "IN", "ME", "OH", "OK", "OR"})


def _ambiguous_state_code_match_is_contextual(
    question: str, match: re.Match[str]
) -> bool:
    before = question[: match.start()]
    after = question[match.end() :]
    has_geo_preface = bool(
        re.search(
            r"(?:^|[\s(,/;:-])(?:in|for|from|state|states|market|coverage|geography|geo)[:\s]+$",
            before,
            flags=re.IGNORECASE,
        )
    )
    if not has_geo_preface and not before.rstrip().endswith(("(", "[")):
        return False
    next_word = re.match(r"[\s,;:.-]+([A-Za-z]+)", after)
    if next_word is None:
        return True
    return next_word.group(1).lower() in {"is", "are", "has", "have", "with", "and"}


def _current_footprint_label() -> str:
    from backend.services.state_footprint import get_state_footprint_resolver

    codes = get_state_footprint_resolver().state_codes()
    return " / ".join(codes) if codes else "configured"


def _canonical_itm_state_scope(question: str) -> tuple[str, str] | None:
    q = question.lower()
    for name, code in _US_STATE_FILTERS:
        name_pattern = r"(?<![a-z0-9])" + re.escape(name) + r"(?![a-z0-9])"
        code_pattern = r"(?<![A-Za-z0-9])" + re.escape(code) + r"(?![A-Za-z0-9])"
        code_match = False
        exact_code_matches = tuple(re.finditer(code_pattern, question, flags=re.IGNORECASE))
        if exact_code_matches:
            code_match = code not in _AMBIGUOUS_STATE_CODES or any(
                _ambiguous_state_code_match_is_contextual(question, match)
                for match in exact_code_matches
            )
        if re.search(name_pattern, q) or code_match:
            return name.title(), code
    return None


def _canonical_in_the_money_count_scope(question: str) -> tuple[str, str] | None | bool:
    q = re.sub(r"[^a-z0-9\s-]+", " ", question.lower())
    q = re.sub(r"\s+", " ", q).strip()
    if not any(phrase in q for phrase in ("in-the-money", "in the money")):
        return False
    if "borrower" not in q:
        return False
    if not any(term in q for term in ("how many", "count", "total number", "number of")):
        return False
    breakdown_terms = (
        " by ",
        "break down",
        "broken down",
        " by state",
        "by-state",
        "state by state",
        "top ",
        "rank",
        "list",
        "zip",
        "county",
        "msa",
        "market",
        "average",
        "avg",
        "mean",
    )
    if any(term in q for term in breakdown_terms):
        return None
    state_scope = _canonical_itm_state_scope(question)
    if state_scope is not None:
        return state_scope
    if re.search(
        r"\bborrowers?\b(?:\s+[a-z0-9-]+){0,6}\s+"
        r"(?:in|for|near|around|within)\s+(?!the\b|the-money\b)[a-z]",
        q,
    ):
        return None
    if re.search(r"\bin[- ]the[- ]money\s+in\s+[a-z]", q):
        return None
    return True


def _canonical_itm_city_scope(question: str) -> str | None:
    q = re.sub(r"[^a-z0-9\s-]+", " ", question.lower())
    q = re.sub(r"[-]+", " ", q)
    q = re.sub(r"\s+", " ", q).strip()
    if "in the money" not in q or "borrower" not in q:
        return None
    if not any(term in q for term in ("how many", "count", "total number", "number of")):
        return None
    city_start = q.rfind(" in ")
    if city_start <= q.find("in the money"):
        return None
    city = q[city_start + 4 :].strip()
    city = re.sub(r"\b(?:right now|currently|today|this week|this month)\b.*$", "", city)
    city = city.strip()
    if not city:
        return None
    blocked_geo_terms = {"state", "states", "zip", "zips", "msa", "market", "markets", "county"}
    if any(term in city.split() for term in blocked_geo_terms):
        return None
    state_names = {name for name, _code in _US_STATE_FILTERS}
    state_codes = {code.lower() for _name, code in _US_STATE_FILTERS}
    if city in state_names or city.lower() in state_codes:
        return None
    return " ".join(part.capitalize() for part in city.split())


def _canonical_msa_score_scope(question: str) -> bool:
    q = re.sub(r"[^a-z0-9\s]+", " ", question.lower())
    q = re.sub(r"\s+", " ", q).strip()
    score_terms = (
        "lead score",
        "opportunity score",
        "avg score",
        "average score",
        "mean score",
        "mean lead score",
    )
    geo_terms = ("msa", "cbsa", "market", "markets")
    top_terms = ("top five", "top 5", "five markets", "5 markets")
    return (
        "compare" in q
        and any(term in q for term in score_terms)
        and any(term in q for term in geo_terms)
        and any(term in q for term in top_terms)
    )


def _canonical_itm_zip_scope(question: str) -> bool:
    q = re.sub(r"[^a-z0-9\s-]+", " ", question.lower())
    q = re.sub(r"\s+", " ", q).strip()
    zip_terms = ("zip", "zips", "zipcode", "zipcodes", "zip code", "zip codes", "postal")
    rank_terms = (
        "top",
        "most",
        "highest",
        "rank",
        "ranked",
        "which",
        "show",
        "list",
        "break down",
        "by zip",
    )
    refi_terms = ("in-the-money", "in the money", "itm", "refi", "refinance")
    return (
        any(term in q for term in zip_terms)
        and any(term in q for term in rank_terms)
        and any(term in q for term in refi_terms)
        and any(term in q for term in ("borrower", "lead", "candidate"))
    )


def _canonical_genie_answer(
    *,
    question: str,
    result: GenieResponse,
    sql_client: DatabricksSqlClient | None,
) -> GenieMessageResponse | None:
    """Return hard-gated trusted answers for known grain-sensitive metrics.

    The Genie space is allowed to read metric views, but some executive
    questions have canonical gold-grain SQL that we use as a governed repair
    when the live Genie turn returns text without SQL proof. Unsafe live SQL
    or PII-bearing answers are blocked before this path so canonical repair
    cannot mask a policy failure.
    """
    if sql_client is None:
        return None
    if _retention_competitor_lien_list_question(question):
        try:
            rows = _redact_genie_rows(
                sql_client.execute(_CANONICAL_RETENTION_COMPETITOR_LIEN_LIST_SQL)
            ) or []
        except DatabricksSqlError as exc:
            log.warning("canonical_genie_retention_competitor_lien_failed: %s", exc, exc_info=True)
            return None
        trusted_assets = [
            qualify("gold", "borrower_360", catalog="mip"),
            qualify("gold", "evidence_events", catalog="mip"),
        ]
        question_hash = _genie_question_hash(question)
        proof = _build_genie_proof(
            sql_query=_CANONICAL_RETENTION_COMPETITOR_LIEN_LIST_SQL,
            trusted_assets=trusted_assets,
            rows=rows,
            question=question,
            conversation_id=result.conversation_id,
            message_id=result.message_id,
            elapsed_ms=result.elapsed_ms,
        )
        visualization = _plan_genie_visualization(question, rows)
        actions = _suggest_genie_actions(
            question=question,
            rows=rows,
            trusted_assets=trusted_assets,
            visualization=visualization,
            conversation_id=result.conversation_id,
            message_id=result.message_id,
            question_hash=question_hash,
            sql_query=_CANONICAL_RETENTION_COMPETITOR_LIEN_LIST_SQL,
            source="trusted_sql",
        )
        total_matching = _total_matching_from_rows(rows)
        shown_count = len(rows)
        if rows:
            if total_matching > shown_count:
                answer = (
                    f"There are {total_matching:,} retention-list borrowers with "
                    f"competitor-lien evidence in the last 30 days; showing the first "
                    f"{shown_count:,} by latest evidence timestamp and opportunity score. "
                    "The result uses the governed `competitor_lien` signal_type from "
                    "mip.gold.evidence_events."
                )
            else:
                answer = (
                    f"I found {shown_count:,} retention-list borrowers with competitor-lien "
                    "evidence in the last 30 days. The result uses the governed "
                    "`competitor_lien` signal_type from mip.gold.evidence_events."
                )
        else:
            answer = (
                "No retention-list borrowers have governed competitor-lien evidence "
                "in the last 30 days. This is a live result from the modeled "
                "`competitor_lien` signal_type, not a stale `lien-change` alias."
            )
        return GenieMessageResponse(
            conversation_id=result.conversation_id,
            message_id=result.message_id,
            elapsed_ms=result.elapsed_ms,
            question_hash=question_hash,
            question=question,
            answer=answer,
            source="trusted_sql",
            trusted_assets=trusted_assets,
            sql_query=_CANONICAL_RETENTION_COMPETITOR_LIEN_LIST_SQL,
            row_count=len(rows),
            proof=proof,
            visualization=visualization,
            actions=actions,
            metric_value=f"{total_matching:,}",
            table_rows=rows,
        )
    if _retention_risk_question(question):
        try:
            row = sql_client.execute_one(_CANONICAL_CURRENT_CUSTOMER_RETENTION_RISK_SQL) or {}
        except DatabricksSqlError as exc:
            log.warning("canonical_genie_retention_risk_failed: %s", exc, exc_info=True)
            return None
        count = row.get("retention_risk_borrowers")
        try:
            count_int = int(count)
        except (TypeError, ValueError):
            log.warning("canonical_genie_retention_risk_bad_count: %r", count)
            return None
        rows = [
            {
                "retention_risk_borrowers": count_int,
                "refreshed_at": row.get("refreshed_at"),
            }
        ]
        trusted_assets = [qualify("gold", "borrower_360", catalog="mip")]
        question_hash = _genie_question_hash(question)
        proof = _build_genie_proof(
            sql_query=_CANONICAL_CURRENT_CUSTOMER_RETENTION_RISK_SQL,
            trusted_assets=trusted_assets,
            rows=rows,
            question=question,
            conversation_id=result.conversation_id,
            message_id=result.message_id,
            elapsed_ms=result.elapsed_ms,
        )
        visualization = _plan_genie_visualization(question, rows)
        actions = _suggest_genie_actions(
            question=question,
            rows=rows,
            trusted_assets=trusted_assets,
            visualization=visualization,
            conversation_id=result.conversation_id,
            message_id=result.message_id,
            question_hash=question_hash,
            sql_query=_CANONICAL_CURRENT_CUSTOMER_RETENTION_RISK_SQL,
            source="trusted_sql",
        )
        answer = (
            f"There are {count_int:,} current Summit customers in the retention-risk "
            "cohort. This uses the modeled retention signal in mip.gold.borrower_360 "
            "rather than the mutually exclusive current-customer and competitor-lien "
            "flags."
        )
        return GenieMessageResponse(
            conversation_id=result.conversation_id,
            message_id=result.message_id,
            elapsed_ms=result.elapsed_ms,
            question_hash=question_hash,
            question=question,
            answer=answer,
            source="trusted_sql",
            trusted_assets=trusted_assets,
            sql_query=_CANONICAL_CURRENT_CUSTOMER_RETENTION_RISK_SQL,
            row_count=len(rows),
            proof=proof,
            visualization=visualization,
            actions=actions,
            metric_value=f"{count_int:,}",
            table_rows=rows,
        )
    if _canonical_itm_zip_scope(question):
        try:
            rows = sql_client.execute(_CANONICAL_ITM_TOP_ZIPS_SQL)
        except DatabricksSqlError as exc:
            log.warning("canonical_genie_itm_zips_failed: %s", exc, exc_info=True)
            return None
        rows = _redact_genie_rows(rows) or []
        trusted_assets = [qualify("gold", "borrower_360", catalog="mip")]
        question_hash = _genie_question_hash(question)
        proof = _build_genie_proof(
            sql_query=_CANONICAL_ITM_TOP_ZIPS_SQL,
            trusted_assets=trusted_assets,
            rows=rows,
            question=question,
            conversation_id=result.conversation_id,
            message_id=result.message_id,
            elapsed_ms=result.elapsed_ms,
        )
        visualization = _plan_genie_visualization(question, rows)
        actions = _suggest_genie_actions(
            question=question,
            rows=rows,
            trusted_assets=trusted_assets,
            visualization=visualization,
            conversation_id=result.conversation_id,
            message_id=result.message_id,
            question_hash=question_hash,
            sql_query=_CANONICAL_ITM_TOP_ZIPS_SQL,
            source="trusted_sql",
        )
        if rows:
            top = rows[0]
            answer = (
                "I ranked ZIP codes by unique borrowers currently in-the-money "
                "for refinance from mip.gold.borrower_360. "
                f"The current leader is ZIP {top.get('zip')} ({top.get('state')}) "
                f"with {int(top.get('in_the_money_borrowers') or 0):,} borrowers; "
                "the cohort action below carries these ZIP filters into Lead Queue."
            )
        else:
            answer = (
                "The trusted borrower table returned no in-the-money ZIP rows for "
                "the current refreshed data coverage."
            )
        return GenieMessageResponse(
            conversation_id=result.conversation_id,
            message_id=result.message_id,
            elapsed_ms=result.elapsed_ms,
            question_hash=question_hash,
            question=question,
            answer=answer,
            source="trusted_sql",
            trusted_assets=trusted_assets,
            sql_query=_CANONICAL_ITM_TOP_ZIPS_SQL,
            row_count=len(rows),
            proof=proof,
            visualization=visualization,
            actions=actions,
            table_rows=rows,
        )
    if _canonical_msa_score_scope(question):
        try:
            rows = sql_client.execute(_CANONICAL_MSA_SCORE_SQL)
        except DatabricksSqlError as exc:
            log.warning("canonical_genie_msa_score_failed: %s", exc, exc_info=True)
            return None
        rows = _redact_genie_rows(rows) or []
        trusted_assets = [qualify("gold", "borrower_360", catalog="mip")]
        question_hash = _genie_question_hash(question)
        proof = _build_genie_proof(
            sql_query=_CANONICAL_MSA_SCORE_SQL,
            trusted_assets=trusted_assets,
            rows=rows,
            question=question,
            conversation_id=result.conversation_id,
            message_id=result.message_id,
            elapsed_ms=result.elapsed_ms,
        )
        visualization = _plan_genie_visualization(question, rows)
        actions = _suggest_genie_actions(
            question=question,
            rows=rows,
            trusted_assets=trusted_assets,
            visualization=visualization,
            conversation_id=result.conversation_id,
            message_id=result.message_id,
            question_hash=question_hash,
            sql_query=_CANONICAL_MSA_SCORE_SQL,
            source="trusted_sql",
        )
        if rows:
            answer = (
                "I used Cotality's `situs_cbsa_code` as the MSA identifier and "
                "ranked the top five markets by borrower volume, then calculated "
                "mean lead score at the unique borrower grain from mip.gold.borrower_360."
            )
        else:
            answer = (
                "The current gold borrower table did not return CBSA-coded market rows. "
                "Module 0 has `situs_cbsa_code` for MSA-style grouping, but no separate "
                "MSA-name lookup is loaded."
            )
        return GenieMessageResponse(
            conversation_id=result.conversation_id,
            message_id=result.message_id,
            elapsed_ms=result.elapsed_ms,
            question_hash=question_hash,
            question=question,
            answer=answer,
            source="trusted_sql",
            trusted_assets=trusted_assets,
            sql_query=_CANONICAL_MSA_SCORE_SQL,
            row_count=len(rows),
            proof=proof,
            visualization=visualization,
            actions=actions,
            table_rows=rows,
        )

    scope = _canonical_in_the_money_count_scope(question)
    if not scope:
        city_scope = _canonical_itm_city_scope(question)
        if not city_scope:
            return None
        try:
            row = sql_client.execute_one(
                _CANONICAL_ITM_COUNT_BY_CITY_SQL,
                {"city": city_scope},
            ) or {}
        except DatabricksSqlError as exc:
            log.warning("canonical_genie_metric_failed: %s", exc, exc_info=True)
            return None
        count = row.get("in_the_money_borrowers")
        try:
            count_int = int(count)
        except (TypeError, ValueError):
            log.warning("canonical_genie_metric_bad_count: %r", count)
            return None
        rows = [
            {
                "city": city_scope,
                "in_the_money_borrowers": count_int,
                "refreshed_at": row.get("refreshed_at"),
            }
        ]
        trusted_assets = [qualify("gold", "borrower_360", catalog="mip")]
        question_hash = _genie_question_hash(question)
        proof = _build_genie_proof(
            sql_query=_CANONICAL_ITM_COUNT_BY_CITY_SQL,
            trusted_assets=trusted_assets,
            rows=rows,
            question=question,
            conversation_id=result.conversation_id,
            message_id=result.message_id,
            elapsed_ms=result.elapsed_ms,
        )
        visualization = _plan_genie_visualization(question, rows)
        actions = _suggest_genie_actions(
            question=question,
            rows=rows,
            trusted_assets=trusted_assets,
            visualization=visualization,
            conversation_id=result.conversation_id,
            message_id=result.message_id,
            question_hash=question_hash,
            sql_query=_CANONICAL_ITM_COUNT_BY_CITY_SQL,
            source="trusted_sql",
        )
        answer = (
            f"There are {count_int:,} borrowers currently in-the-money in {city_scope} "
            f"within the current {_current_footprint_label()} evaluation-share scope. "
            "This is a city-scoped unique borrower count from mip.gold.borrower_360; "
            "it is not the overall share total."
        )
        return GenieMessageResponse(
            conversation_id=result.conversation_id,
            message_id=result.message_id,
            elapsed_ms=result.elapsed_ms,
            question_hash=question_hash,
            question=question,
            answer=answer,
            source="trusted_sql",
            trusted_assets=trusted_assets,
            sql_query=_CANONICAL_ITM_COUNT_BY_CITY_SQL,
            row_count=len(rows),
            proof=proof,
            visualization=visualization,
            actions=actions,
            metric_value=f"{count_int:,}",
            table_rows=rows,
        )
    state_scope = scope if isinstance(scope, tuple) else None
    sql_query = _CANONICAL_ITM_COUNT_BY_STATE_SQL if state_scope else _CANONICAL_ITM_COUNT_SQL
    params = {"state": state_scope[1]} if state_scope else None
    try:
        row = sql_client.execute_one(sql_query, params) or {}
    except DatabricksSqlError as exc:
        log.warning("canonical_genie_metric_failed: %s", exc, exc_info=True)
        return None
    count = row.get("in_the_money_borrowers")
    try:
        count_int = int(count)
    except (TypeError, ValueError):
        log.warning("canonical_genie_metric_bad_count: %r", count)
        return None

    rows = [{"in_the_money_borrowers": count_int, "refreshed_at": row.get("refreshed_at")}]
    if state_scope:
        rows[0]["state"] = state_scope[1]
    trusted_assets = [qualify("gold", "borrower_360", catalog="mip")]
    question_hash = _genie_question_hash(question)
    proof = _build_genie_proof(
        sql_query=sql_query,
        trusted_assets=trusted_assets,
        rows=rows,
        question=question,
        conversation_id=result.conversation_id,
        message_id=result.message_id,
        elapsed_ms=result.elapsed_ms,
    )
    visualization = _plan_genie_visualization(question, rows)
    actions = _suggest_genie_actions(
        question=question,
        rows=rows,
        trusted_assets=trusted_assets,
        visualization=visualization,
        conversation_id=result.conversation_id,
        message_id=result.message_id,
        question_hash=question_hash,
        sql_query=sql_query,
        source="trusted_sql",
    )
    geo_text = f" in {state_scope[0]} ({state_scope[1]})" if state_scope else ""
    answer = (
        f"There are {count_int:,} borrowers currently in-the-money{geo_text}. "
        "This is a unique borrower count from mip.gold.borrower_360 at the "
        "gold borrower grain, so multi-segment borrowers are counted once."
    )
    return GenieMessageResponse(
        conversation_id=result.conversation_id,
        message_id=result.message_id,
        elapsed_ms=result.elapsed_ms,
        question_hash=question_hash,
        question=question,
        answer=answer,
        source="trusted_sql",
        trusted_assets=trusted_assets,
        sql_query=sql_query,
        row_count=len(rows),
        proof=proof,
        visualization=visualization,
        actions=actions,
        metric_value=f"{count_int:,}",
        table_rows=rows,
    )


# Governance denylist applied to every Genie table-row before the response
# leaves the repository boundary. Matches the ``_FORBIDDEN_OUTPUT_KEYS`` set
# in ``backend/services/pii_redaction`` (keep in sync). These keys are PII
# per the governance contract and must never ship to the frontend.
_GENIE_PII_KEYS: frozenset[str] = frozenset({
    *_FORBIDDEN_OUTPUT_KEYS,
    "owner_name",
    "owner_names",
    "owner_full_name",
    "primary_owner",
    "owner_name_hash",      # hashed, but still a stable identifier — not exported
    "owner_link_id",        # raw Cotality identifier — replaced with a display surrogate elsewhere
    "clip",                 # raw CLIP — evidence drawer surfaces a short form only
    "raw_clip",
    "street_address",
    "site_address",
    "mailing_address",
    "tax_mailing_address",
    "subject_property",     # carries synthesized city + ZIP; synthesized upstream, but redacted here too
    "owner_email",
    "borrower_email",
    "email",
    "phone",
    "phone_number",
    "ssn",
})


def _redact_genie_rows(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """Strip PII keys from Genie's result set before returning to the UI.

    Never raises; if ``rows`` is falsy we pass it through. Applied to every
    response path that sets ``table_rows`` on ``GenieMessageResponse``.
    """
    if not rows:
        return rows
    redacted: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        redacted.append(
            {
                k: v
                for k, v in row.items()
                if _normalise_genie_key(str(k)) not in _GENIE_PII_KEYS
            }
        )
    return redacted


def _normalise_genie_key(key: str) -> str:
    split_camel = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
    return re.sub(r"[^a-z0-9]+", "_", split_camel.lower()).strip("_")


_PII_TEXT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    re.compile(r"\b(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}\b"),
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    re.compile(
        r"\b(?:clip|raw\s+clip|owner[_\s]*link(?:[_\s]*id)?|ownerlink(?:\s+id)?|"
        r"owner_link_id|owner[_\s]*1[_\s]*identifier|owner[_\s]*id|ownerid|"
        r"owner_identifier|owner\s+identifier|borrower_identifier|borrower\s+identifier)\b"
        r"\s*[:#=]?\s*[A-Za-z0-9_-]*\d[A-Za-z0-9_-]{7,}\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b\d{1,6}\s+[A-Za-z0-9 .'-]{2,40}\s+"
        r"(?:st|street|ave|avenue|rd|road|dr|drive|ln|lane|ct|court|"
        r"blvd|boulevard|way|pl|place|pkwy|parkway)\b",
        re.IGNORECASE,
    ),
)


def _answer_text_contains_pii(text: str | None) -> bool:
    if not text:
        return False
    return any(pattern.search(text) for pattern in _PII_TEXT_PATTERNS)


def _trusted_genie_asset_names() -> frozenset[str]:
    """Return explicit trusted assets for the configured and demo catalogs."""

    pairs = (
        ("gold", "lead_population"),
        ("gold", "segment_population"),
        ("gold", "lead_scores"),
        ("gold", "borrower_360"),
        ("gold", "borrower_dossier"),
        ("gold", "evidence_events"),
        ("gold", "source_readiness"),
        ("gold", "lockin_cohort"),
        ("gold", "county_rollup"),
        ("gold", "zip_rollup"),
        ("semantics", "lead_generation_metric_view"),
        ("semantics", "segment_performance_metric_view"),
        ("semantics", "borrower_opportunity_metric_view"),
    )
    assets = {qualify(schema, table) for schema, table in pairs}
    assets.update(qualify(schema, table, catalog="mip") for schema, table in pairs)
    return frozenset(assets)


_TRUSTED_GENIE_ASSETS: frozenset[str] = _trusted_genie_asset_names()


def _genie_question_hash(question: str) -> str:
    return hashlib.sha256(question.encode("utf-8")).hexdigest()[:16]


def _is_select_only(sql: str | None) -> bool:
    if not sql:
        return False
    policy_sql = _scrub_sql_for_policy(sql)
    if policy_sql is None:
        return False
    stripped = policy_sql.strip().lower()
    if not (stripped.startswith("select") or stripped.startswith("with")):
        return False
    blocked = "alter|create|delete|drop|grant|insert|merge|revoke|set|truncate|update|use"
    return re.search(rf"\b(?:{blocked})\b", stripped) is None


def _trusted_sql_policy(sql: str | None, trusted_assets: list[str]) -> bool:
    return _trusted_sql_policy_core(
        sql,
        trusted_assets,
        allow_stale_evidence_enum=False,
    )


def _trusted_sql_policy_allowing_stale_evidence_enum(
    sql: str | None,
    trusted_assets: list[str],
) -> bool:
    return _trusted_sql_policy_core(
        sql,
        trusted_assets,
        allow_stale_evidence_enum=True,
    )


def _trusted_sql_policy_core(
    sql: str | None,
    trusted_assets: list[str],
    *,
    allow_stale_evidence_enum: bool,
) -> bool:
    refs = _extract_asset_refs(sql)
    return (
        bool(refs)
        and all(asset in _TRUSTED_GENIE_ASSETS for asset in refs)
        and all(asset in _TRUSTED_GENIE_ASSETS for asset in trusted_assets)
        and _is_select_only(sql)
        and not _sql_mentions_pii_columns(sql)
        and not _sql_has_unqualified_relations(_scrub_sql_for_policy(sql) or "")
        and (allow_stale_evidence_enum or not _sql_uses_stale_evidence_signal_enum(sql))
    )


def _source_readiness_only_assets(assets: list[str]) -> bool:
    return bool(assets) and all(asset.endswith(".gold.source_readiness") for asset in assets)


def _bounded_genie_sql(sql: str) -> str:
    stripped = sql.strip().rstrip(";")
    return f"SELECT * FROM ({stripped}) AS genie_result LIMIT 500"


def _execute_trusted_genie_sql(
    sql_client: DatabricksSqlClient,
    sql: str,
) -> list[dict[str, Any]] | None:
    try:
        return sql_client.execute(_bounded_genie_sql(sql))
    except DatabricksSqlError as exc:
        log.warning("trusted_genie_sql_replay_failed: %s", exc, exc_info=True)
        return None


def _extract_filters(sql: str | None) -> list[str]:
    if not sql:
        return []
    import re

    match = re.search(
        r"\bwhere\b(?P<where>.*?)(?:\bgroup\s+by\b|\border\s+by\b|\blimit\b|$)",
        sql,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return []
    where = re.sub(r"\s+", " ", match.group("where")).strip()
    if not where:
        return []
    return [where[:500]]


def _row_columns(rows: list[dict[str, Any]] | None) -> list[str]:
    if not rows:
        return []
    cols: list[str] = []
    for row in rows[:5]:
        for key in row:
            if key not in cols:
                cols.append(key)
    return cols


_GENIE_IDENTIFIER_COLUMNS = {
    "zip",
    "zip_code",
    "zipcode",
    "postal_code",
    "fips",
    "fips_5",
    "county_fips",
    "county_fips_5",
    "msa_cbsa_code",
    "cbsa_code",
    "census_tract",
    "tract",
    "borrower_id",
    "id",
}


def _is_genie_identifier_column(column: str) -> bool:
    lower = column.lower()
    return lower in _GENIE_IDENTIFIER_COLUMNS or lower.endswith("_id")


def _numeric_columns(rows: list[dict[str, Any]] | None) -> list[str]:
    out: list[str] = []
    for col in _row_columns(rows):
        if _is_genie_identifier_column(col):
            continue
        values = [row.get(col) for row in rows or [] if row.get(col) is not None]
        if values and all(isinstance(v, int | float) for v in values):
            out.append(col)
    return out


def _text_columns(rows: list[dict[str, Any]] | None) -> list[str]:
    out: list[str] = []
    for col in _row_columns(rows):
        values = [row.get(col) for row in rows or [] if row.get(col) is not None]
        if values and all(isinstance(v, str) for v in values):
            out.append(col)
    return out


def _dateish_columns(rows: list[dict[str, Any]] | None) -> list[str]:
    names = []
    for col in _row_columns(rows):
        lower = col.lower()
        if lower.endswith("_date") or lower.endswith("_at") or "snapshot" in lower:
            names.append(col)
    return names


def _freshness_from_rows(
    assets: list[str],
    rows: list[dict[str, Any]] | None,
) -> list[GenieDataFreshness]:
    freshness_cols = [
        col
        for col in _row_columns(rows)
        if col.lower() in {"refreshed_at", "snapshot_at", "data_refreshed_at"}
        or col.lower().endswith("_refreshed_at")
    ]
    values: list[str] = []
    for col in freshness_cols:
        for row in rows or []:
            value = row.get(col)
            if value is not None:
                values.append(str(value))
    refreshed_at = max(values) if values else None
    if refreshed_at:
        return [
            GenieDataFreshness(
                asset=asset,
                refreshed_at=refreshed_at,
                status="live",
                note="freshness returned by the generated SQL result",
            )
            for asset in assets
        ]
    return [
        GenieDataFreshness(
            asset=asset,
            status="source-cited",
            note="generated SQL did not return refreshed_at/snapshot_at; inspect SQL or query MAX(refreshed_at) for this asset",
        )
        for asset in assets
    ]


def _known_data_gaps(question: str, assets: list[str]) -> list[str]:
    material = " ".join([question, *assets]).lower()
    return _pending_feed_gaps_from_material(material)


def _pending_feed_gaps_from_material(material: str) -> list[str]:
    material = material.lower()
    gaps: list[str] = []
    if any(token in material for token in ("permit", "building permit", "has_permit")):
        gaps.append(
            "Cotality Building Permits feed is pending; permit flags are blocked false today."
        )
    if any(token in material for token in ("listing", "listed", "mls", "listed_for_sale")):
        gaps.append(
            "Cotality MLS/listing feed is pending; listed-for-sale flags are blocked false today."
        )
    return gaps


def _pending_feed_gaps_from_rows(rows: list[dict[str, Any]] | None) -> list[str]:
    keys = " ".join(_row_columns(rows))
    return _pending_feed_gaps_from_material(keys)


def _known_data_gaps_for_result(
    *,
    question: str,
    assets: list[str],
    sql_query: str | None,
    rows: list[dict[str, Any]] | None,
) -> list[str]:
    gaps: list[str] = []
    for gap in [
        *_known_data_gaps(question, assets),
        *_pending_feed_gaps_from_material(sql_query or ""),
        *_pending_feed_gaps_from_rows(rows),
    ]:
        if gap not in gaps:
            gaps.append(gap)
    return gaps


def _build_genie_proof(
    *,
    sql_query: str | None,
    trusted_assets: list[str],
    rows: list[dict[str, Any]] | None,
    question: str,
    conversation_id: str,
    message_id: str,
    elapsed_ms: int,
    reasoning_trace: list[dict[str, str]] | None = None,
) -> GenieProof:
    trusted = _trusted_sql_policy(sql_query, trusted_assets)
    return GenieProof(
        sql_query=sql_query,
        source_assets=trusted_assets,
        data_freshness=_freshness_from_rows(trusted_assets, rows),
        row_count=len(rows) if rows else 0,
        filters=_extract_filters(sql_query),
        trusted=trusted,
        reasoning_trace=reasoning_trace or [],
        known_data_gaps=_known_data_gaps_for_result(
            question=question,
            assets=trusted_assets,
            sql_query=sql_query,
            rows=rows,
        ),
        conversation_id=conversation_id,
        message_id=message_id,
        elapsed_ms=elapsed_ms,
        generated_at=datetime.now(UTC).isoformat(),
    )


def _sql_hash(sql_query: str | None) -> str | None:
    if not sql_query:
        return None
    normalized = re.sub(r"\s+", " ", sql_query.strip())
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _label_column(rows: list[dict[str, Any]] | None, question: str) -> str | None:
    cols = _row_columns(rows)
    preferred = [
        "state",
        "zip",
        "zip_code",
        "zipcode",
        "postal_code",
        "fips",
        "fips_5",
        "county_fips",
        "county_fips_5",
        "msa_cbsa_code",
        "cbsa_code",
        "census_tract",
        "tract",
        "borrower_id",
        "clip",
        "id",
        "county",
        "county_name",
        "msa",
        "market",
        "segment",
        "segment_code",
        "recommended_offer",
        "offer_code",
        "product_label",
    ]
    q = question.lower()
    if "zip" in q or "postal" in q:
        preferred = ["zip", "zip_code", "zipcode", "postal_code", *preferred]
    if "county" in q or "counties" in q:
        preferred = [
            "county_fips_5",
            "county_fips",
            "fips_5",
            "fips",
            "county",
            "county_name",
            *preferred,
        ]
    if "fips" in q:
        preferred = ["fips", "fips_5", "county_fips", "county_fips_5", *preferred]
    if "cbsa" in q or "msa" in q:
        preferred = ["msa_cbsa_code", "cbsa_code", "msa", "market", *preferred]
    if "state" in q or "map" in q:
        preferred = ["state", *preferred]
    for col in preferred:
        if col in cols:
            return col
    texts = _text_columns(rows)
    return texts[0] if texts else None


def _value_column(rows: list[dict[str, Any]] | None, question: str) -> str | None:
    nums = _numeric_columns(rows)
    if not nums:
        return None
    q = question.lower()
    preferred = [
        "borrowers",
        "borrower_count",
        "count",
        "marketable_borrowers",
        "addressable_borrowers",
        "in_the_money_borrowers",
        "high_opportunity_borrowers",
        "opportunity_score",
        "avg_score",
        "approval_rate",
        "conversion_rate",
        "rate_spread_bps",
        "equity_pct",
    ]
    if "score" in q:
        preferred = ["opportunity_score", "avg_score", *preferred]
    if "rate" in q:
        preferred = ["approval_rate", "conversion_rate", "rate_spread_bps", *preferred]
    for col in preferred:
        if col in nums:
            return col
    return nums[0]


def _plan_genie_visualization(
    question: str,
    rows: list[dict[str, Any]] | None,
) -> GenieVisualizationSpec | None:
    q = question.lower()
    row_count = len(rows) if rows else 0
    label = _label_column(rows, question)
    value = _value_column(rows, question)
    date_col = (_dateish_columns(rows) or [None])[0]
    cols = set(_row_columns(rows))

    if "borrower_id" in cols and row_count > 0:
        return GenieVisualizationSpec(
            kind="borrower_list",
            title="Borrower drill-down",
            x="borrower_id",
            y=value,
            reason="result includes borrower_id rows",
        )
    if ("strategy" in q or "10,000" in q or "outreach touches" in q) and row_count > 0:
        return GenieVisualizationSpec(
            kind="strategy_board",
            title="Strategy board",
            x=label,
            y=value,
            reason="strategy-oriented prompt with returned rows",
        )
    if ("map" in q or "geo" in q or "where" in q) and label == "state" and value:
        return GenieVisualizationSpec(
            kind="map",
            title=f"{value} by {label}",
            x=label,
            y=value,
            reason="geography prompt with state column",
        )
    if ("trend" in q or "over time" in q or "by week" in q or "daily" in q) and date_col and value:
        return GenieVisualizationSpec(
            kind="line",
            title=f"{value} trend",
            x=date_col,
            y=value,
            reason="time-oriented prompt with date/snapshot column",
        )
    if row_count == 1 and value:
        return GenieVisualizationSpec(
            kind="metric",
            title=value,
            y=value,
            reason="single-row numeric result",
        )
    if label and value and row_count >= 2:
        return GenieVisualizationSpec(
            kind="bar",
            title=f"{value} by {label}",
            x=label,
            y=value,
            reason="categorical result with numeric measure",
        )
    if row_count > 0:
        return GenieVisualizationSpec(kind="table", title="Query result")
    return None


def _borrower_ids_from_rows(rows: list[dict[str, Any]] | None) -> list[str]:
    ids: list[str] = []
    for row in rows or []:
        value = row.get("borrower_id")
        if not isinstance(value, str):
            continue
        try:
            borrower_id = validate_public_borrower_id(value)
        except ValueError:
            continue
        if borrower_id not in ids:
            ids.append(borrower_id)
    return ids


def _total_matching_from_rows(rows: list[dict[str, Any]] | None) -> int:
    for row in rows or []:
        raw = row.get("total_matching_borrowers")
        if raw is None:
            continue
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            continue
    return len(rows or [])


def _row_values(
    rows: list[dict[str, Any]] | None,
    *columns: str,
    digits: int | None = None,
    upper: bool = False,
) -> list[str]:
    values: list[str] = []
    for row in rows or []:
        for column in columns:
            raw = row.get(column)
            if raw is None:
                continue
            value = str(raw).strip()
            if upper:
                value = value.upper()
            if digits is not None:
                if re.fullmatch(r"\d+\.0", value):
                    value = value.split(".", 1)[0]
                if re.fullmatch(r"\d+", value) and len(value) < digits:
                    value = value.zfill(digits)
                match = re.fullmatch(rf"\d{{{digits}}}", value)
                if not match:
                    continue
            elif upper and not re.fullmatch(r"[A-Z]{2}", value):
                continue
            if value not in values:
                values.append(value)
    return values


def _segment_codes_from_question(question: str) -> list[str]:
    q = question.lower()
    codes: list[str] = []
    if re.search(r"\b(in[-\s]?the[-\s]?money|itm|refi|refinance)\b", q):
        codes.append("itm")
    if "home equity" in q or "heloc" in q:
        codes.append("equity")
    if "investor" in q or "multi-property" in q or "multi property" in q:
        codes.append("investor")
    if (
        "retention" in q
        or "competitor lien" in q
        or "current customer" in q
        or "recapture" in q
        or "at risk" in q
        or "going to a competitor" in q
    ):
        codes.append("retention")
    if "listed" in q or "for sale" in q or "purchase" in q:
        codes.append("listed")
    if "permit" in q:
        codes.append("permit")
    return list(dict.fromkeys(codes))


def _portfolio_criteria_from_question(question: str) -> dict[str, Any]:
    q = question.lower()
    criteria: dict[str, Any] = {}
    if re.search(r"\b(owner[-\s]?occupied|primary residence)\b", q):
        criteria["occupancy"] = "Owner-occupied"
    elif re.search(r"\b(non[-\s]?owner[-\s]?occupied|investment property)\b", q):
        criteria["occupancy"] = "Non-owner-occupied"

    if re.search(r"\b(open|active)\s+(heloc|second lien|2nd lien)\b", q):
        criteria["lien_status"] = "Open HELOC"
    elif re.search(r"\b(open|active)\s+(first lien|1st lien)\b", q):
        criteria["lien_status"] = "Open 1st lien"
    elif re.search(r"\b(free\s*(?:&|and)\s*clear)\b", q):
        criteria["lien_status"] = "Free & clear"

    if re.search(r"\bsingle[-\s]?property owner\b", q):
        criteria["owner_link"] = "Single-property owner"
    elif re.search(r"\b(multi[-\s]?property|2[-\s]*4 properties)\b", q):
        criteria["owner_link"] = "Multi-property (2-4)"
    elif re.search(r"\b(portfolio investor|5\+ properties)\b", q):
        criteria["owner_link"] = "Portfolio investor (5+)"

    if "listed for sale" in q:
        criteria["purchase_intent"] = "Listed for sale"
    elif "permit" in q:
        criteria["purchase_intent"] = "Recent permit activity"

    if "cash-out" in q or "cash out" in q:
        criteria["product"] = "Cash-out"
    elif re.search(r"\bheloc\b", q):
        criteria["product"] = "HELOC"
    elif re.search(r"\b(retention|recapture|at risk|going to a competitor)\b", q):
        criteria["product"] = "Retention"

    if "current customer" in q:
        criteria["lender_relationship"] = "Current customer"
    elif "former customer" in q:
        criteria["lender_relationship"] = "Former customer"
    elif "competitor" in q and not _retention_competitor_lien_list_question(question):
        criteria["lender_relationship"] = "Competitor customer"

    equity_match = re.search(
        r"(?:equity|ltv|loan[-\s]?to[-\s]?value)[^\d]{0,24}(15|25|40)\s*%",
        q,
    )
    if equity_match:
        criteria["min_equity_pct_label"] = f"≥ {equity_match.group(1)}%"
    return criteria


def _portfolio_criteria_from_sql(sql_query: str | None) -> dict[str, Any]:
    if not sql_query:
        return {}
    sql = re.sub(r"\s+", " ", sql_query.strip().lower())
    if not sql:
        return {}

    criteria: dict[str, Any] = {}
    if re.search(r"\bis_owner_occupied\s*=\s*(true|1)\b", sql) or re.search(
        r"\bwhere\s+is_owner_occupied\b", sql
    ):
        criteria["occupancy"] = "Owner-occupied"
    elif re.search(r"\bis_owner_occupied\s*=\s*(false|0)\b", sql):
        criteria["occupancy"] = "Non-owner-occupied"

    second_pos_positive = bool(
        re.search(r"\bcoalesce\(\s*second_pos_amount\s*,\s*0\s*\)\s*>\s*0\b", sql)
        or re.search(r"\bsecond_pos_amount\s*>\s*0\b", sql)
    )
    second_pos_zero = bool(
        re.search(r"\bcoalesce\(\s*second_pos_amount\s*,\s*0\s*\)\s*=\s*0\b", sql)
        or re.search(r"\bsecond_pos_amount\s*=\s*0\b", sql)
        or re.search(r"\bsecond_pos_amount\s+is\s+null\b", sql)
    )
    first_pos_positive = bool(
        re.search(r"\bcoalesce\(\s*current_lien_balance\s*,\s*0\s*\)\s*>\s*0\b", sql)
        or re.search(r"\bcurrent_lien_balance\s*>\s*0\b", sql)
    )
    first_pos_zero = bool(
        re.search(r"\bcoalesce\(\s*current_lien_balance\s*,\s*0\s*\)\s*<=\s*0\b", sql)
        or re.search(r"\bcurrent_lien_balance\s*<=\s*0\b", sql)
        or re.search(r"\bcurrent_lien_balance\s+is\s+null\b", sql)
    )
    if second_pos_positive:
        criteria["lien_status"] = "Open HELOC"
    elif first_pos_positive and second_pos_zero:
        criteria["lien_status"] = "Open 1st lien"
    elif first_pos_zero and second_pos_zero:
        criteria["lien_status"] = "Free & clear"

    if re.search(r"\bcoalesce\(\s*related_property_count\s*,\s*1\s*\)\s*<=\s*1\b", sql):
        criteria["owner_link"] = "Single-property owner"
    elif re.search(
        r"\bcoalesce\(\s*related_property_count\s*,\s*1\s*\)\s+between\s+2\s+and\s+4\b",
        sql,
    ):
        criteria["owner_link"] = "Multi-property (2-4)"
    elif re.search(r"\bcoalesce\(\s*related_property_count\s*,\s*1\s*\)\s*>=\s*5\b", sql):
        criteria["owner_link"] = "Portfolio investor (5+)"

    has_listing = bool(re.search(r"\blisted_for_sale\s*=\s*true\b", sql))
    has_permit = bool(re.search(r"\bhas_permit\s*=\s*true\b", sql))
    if has_listing and has_permit:
        criteria["purchase_intent"] = "Both"
    elif has_listing:
        criteria["purchase_intent"] = "Listed for sale"
    elif has_permit:
        criteria["purchase_intent"] = "Recent permit activity"

    if re.search(r"\bis_current_customer\s*=\s*true\b", sql):
        criteria["lender_relationship"] = "Current customer"
    elif re.search(r"\bis_former_customer\s*=\s*true\b", sql):
        criteria["lender_relationship"] = "Former customer"
    elif re.search(r"\bis_competitor_lien\s*=\s*true\b", sql):
        criteria["lender_relationship"] = "Competitor customer"

    if re.search(r"\brecommended_offer_code\s*=\s*'cash_out'\b", sql) or re.search(
        r'\brecommended_offer_code\s*=\s*"cash_out"\b',
        sql,
    ) or re.search(r"\brecommended_offer_code\s+in\s*\([^)]*'cash_out'[^)]*\)", sql):
        criteria["product"] = "Cash-out"
    elif re.search(r"\brecommended_offer_code\s*=\s*'retention'\b", sql):
        criteria["product"] = "Retention"
    elif re.search(r"\brecommended_offer_code\s*=\s*'heloc'\b", sql):
        criteria["product"] = "HELOC"

    equity_match = re.search(r"\bequity_pct\s*>=\s*(15|25|40)\b", sql)
    if equity_match:
        criteria["min_equity_pct_label"] = f"≥ {equity_match.group(1)}%"
    return criteria


def _route_from_answer_rows(
    *,
    question: str,
    rows: list[dict[str, Any]] | None,
    borrower_ids: list[str],
    sql_query: str | None = None,
) -> tuple[str, dict[str, Any]]:
    params: dict[str, str] = {}
    filter_criteria: dict[str, Any] = {}
    zips = _row_values(rows, "zip", "zip_code", "zipcode", "postal_code", digits=5)
    counties = _row_values(rows, "county_fips_5", "county_fips", "fips_5", "fips", digits=5)
    states = _row_values(rows, "state", "state_code", upper=True)
    if states and re.search(r"\bwhich\s+state\b|\bwhat\s+state\b", question.lower()):
        states = states[:1]
    segment_codes = _segment_codes_from_question(question)
    portfolio_criteria = {
        **_portfolio_criteria_from_question(question),
        **_portfolio_criteria_from_sql(sql_query),
    }

    if borrower_ids:
        filter_criteria["borrower_ids"] = borrower_ids
        params["borrower_ids"] = ",".join(borrower_ids)
    elif zips:
        params["zips"] = ",".join(zips)
        filter_criteria["zips"] = zips
    elif counties:
        if len(counties) == 1:
            params["county"] = counties[0]
            filter_criteria["county"] = counties[0]
        else:
            params["counties"] = ",".join(counties)
            filter_criteria["counties"] = counties
    elif states:
        params["states"] = ",".join(states)
        filter_criteria["states"] = states

    if segment_codes:
        if len(segment_codes) == 1:
            params["segment"] = segment_codes[0]
        else:
            params["segment_codes"] = ",".join(segment_codes)
            params["segment_mode"] = "all"
        filter_criteria["segment_codes"] = segment_codes
        filter_criteria["segment_mode"] = "all" if len(segment_codes) > 1 else "any"

    if portfolio_criteria:
        filter_criteria["portfolio_criteria"] = portfolio_criteria
        for key in (
            "occupancy",
            "lien_status",
            "lender_relationship",
            "product",
            "target_lender_ref",
            "min_equity_pct_label",
            "owner_link",
            "purchase_intent",
        ):
            value = portfolio_criteria.get(key)
            if isinstance(value, str) and value.strip():
                params[key] = value

    if params:
        return f"/lead-queue?{urlencode(params)}", filter_criteria
    return "/lead-queue", filter_criteria


def _suggest_genie_actions(
    *,
    question: str,
    rows: list[dict[str, Any]] | None,
    trusted_assets: list[str],
    visualization: GenieVisualizationSpec | None,
    conversation_id: str | None,
    message_id: str | None,
    question_hash: str | None,
    sql_query: str | None,
    source: str = "genie",
) -> list[GenieActionSuggestion]:
    actions: list[GenieActionSuggestion] = []
    borrower_ids = _borrower_ids_from_rows(rows)
    row_count = len(rows) if rows else 0
    q = question.lower()
    base_criteria: dict[str, Any] = {
        "source": source,
        "source_assets": trusted_assets,
        "visualization_kind": visualization.kind if visualization else None,
        "row_count": row_count,
    }
    sql_digest = _sql_hash(sql_query)
    if sql_digest:
        base_criteria["sql_hash"] = sql_digest
    lead_queue_route, result_filters = _route_from_answer_rows(
        question=question,
        rows=rows,
        borrower_ids=borrower_ids,
        sql_query=sql_query,
    )
    if result_filters:
        base_criteria["result_filters"] = result_filters
    if borrower_ids:
        criteria = dict(base_criteria)
        actions.append(
            GenieActionSuggestion(
                id="save-borrowers",
                label=f"Save {len(borrower_ids)} borrower{'' if len(borrower_ids) == 1 else 's'}",
                action_type="save_borrowers",
                description="Add returned borrowers to the governed saved workspace.",
                borrower_ids=borrower_ids,
                criteria=criteria,
            )
        )
        criteria = dict(base_criteria)
        route = f"/borrower-360/{borrower_ids[0]}"
        actions.append(
            GenieActionSuggestion(
                id="show-first-rationale",
                label="Show why first borrower is ranked",
                action_type="show_rationale",
                description="Open Borrower 360 for the top returned borrower.",
                route=route,
                borrower_ids=[borrower_ids[0]],
                criteria=criteria,
            )
        )
    if row_count > 0 and result_filters:
        criteria = dict(base_criteria)
        actions.append(
            GenieActionSuggestion(
                id="open-cohort",
                label="Open this cohort in Lead Queue",
                action_type="open_cohort",
                description="Navigate into the lead queue with this Genie result audited.",
                route=lead_queue_route,
                borrower_ids=borrower_ids,
                criteria=criteria,
            )
        )
        criteria = dict(base_criteria)
        actions.append(
            GenieActionSuggestion(
                id="create-campaign-draft",
                label="Create draft campaign",
                action_type="create_draft_campaign",
                description="Create a Lakebase draft campaign from this governed Genie result.",
                route=lead_queue_route,
                borrower_ids=borrower_ids,
                criteria=criteria,
            )
        )
    if borrower_ids and ("offer" in q or "strategy" in q or "10,000" in q):
        criteria = dict(base_criteria)
        route = f"/offer-orchestrator/{borrower_ids[0]}"
        actions.append(
            GenieActionSuggestion(
                id="compare-offers",
                label="Compare offer strategies",
                action_type="compare_offer_strategies",
                description="Audit this strategy comparison and open the offer surface.",
                route=route,
                borrower_ids=borrower_ids[:1],
                criteria=criteria,
            )
        )
    return actions[:5]


_SQL_IDENT_RE = r"(?:`[^`]+`|[a-zA-Z_][a-zA-Z0-9_]*)"


def _scrub_sql_for_policy(sql: str | None) -> str | None:
    """Remove literals and reject SQL shapes the proof gate cannot trust.

    Genie is allowed to generate one read-only statement over curated UC
    assets. Comments, semicolons, and double-quoted identifiers are rejected
    fail-closed: they are unnecessary for Module 0 questions and make a
    regex-backed verifier easy to spoof.
    """
    if not sql:
        return None
    out: list[str] = []
    i = 0
    while i < len(sql):
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < len(sql) else ""
        if ch == ";":
            return None
        if ch == "-" and nxt == "-":
            return None
        if ch == "/" and nxt == "*":
            return None
        if ch == '"':
            return None
        if ch == "'":
            out.append(" '' ")
            i += 1
            while i < len(sql):
                if sql[i] == "'" and i + 1 < len(sql) and sql[i + 1] == "'":
                    i += 2
                    continue
                if sql[i] == "'":
                    i += 1
                    break
                i += 1
            continue
        if ch == "`":
            start = i
            i += 1
            while i < len(sql) and sql[i] != "`":
                i += 1
            if i >= len(sql):
                return None
            out.append(sql[start : i + 1])
            i += 1
            continue
        out.append(ch)
        i += 1
    scrubbed = "".join(out).strip()
    return scrubbed or None


def _normalise_sql_ref(ref: str) -> str:
    parts = [part.strip().strip("`").lower() for part in ref.split(".")]
    return ".".join(part for part in parts if part)


_GENIE_PII_SQL_COLUMNS: frozenset[str] = frozenset({
    *_FORBIDDEN_OUTPUT_KEYS,
    "owner_link_id",
    "owner_name",
    "owner_names",
    "owner_full_name",
    "owner_name_hash",
    "primary_owner",
    "raw_clip",
    "street_address",
    "site_address",
    "mailing_address",
    "tax_mailing_address",
    "subject_property",
    "owner_email",
    "borrower_email",
    "email",
    "phone",
    "phone_number",
    "ssn",
})


_RAW_IDENTIFIER_SQL_LITERAL_RE = re.compile(
    rf"(?<![A-Za-z0-9_])(?:{_SQL_IDENT_RE}\s*\.\s*)?"
    r"`?(?:clip|raw_clip|owner_link_id|owner_1_identifier|borrower_identifier)`?"
    r"\s*(?:"
    r"=\s*(?:CAST\s*\(\s*)?(?:'[^']*[0-9][^']{7,}'|[0-9]{8,})"
    r"|IN\s*\([^)]*(?:'[^']*[0-9][^']{7,}'|[0-9]{8,})"
    r")",
    re.IGNORECASE,
)
_LONG_RAW_IDENTIFIER_LITERAL_RE = re.compile(
    r"(?:'[^']*[0-9][^']{7,}'|[0-9]{8,})",
    re.IGNORECASE,
)
_SENSITIVE_SQL_IDENTIFIER_RE = re.compile(
    rf"(?<![A-Za-z0-9_])(?:{_SQL_IDENT_RE}\s*\.\s*)?"
    r"`?(?:clip|raw_clip|owner_link_id|owner_1_identifier|borrower_identifier)`?"
    r"(?![A-Za-z0-9_])",
    re.IGNORECASE,
)


def _sql_has_raw_identifier_literal(sql: str) -> bool:
    if _RAW_IDENTIFIER_SQL_LITERAL_RE.search(sql):
        return True
    return bool(
        _LONG_RAW_IDENTIFIER_LITERAL_RE.search(sql)
        and _SENSITIVE_SQL_IDENTIFIER_RE.search(sql)
    )


def _projection_has_wildcard_expansion(term: str) -> bool:
    compact = re.sub(
        r"\s*\.\s*",
        ".",
        re.sub(r"\s+", " ", term.replace("`", "")).strip(),
    ).lower()
    if re.fullmatch(
        r"(?:count|approx_count_distinct)\s*\(\s*\*\s*\)(?:\s+as\s+[a-z_][a-z0-9_]*)?",
        compact,
    ):
        return False
    if compact.startswith("*") or re.match(r"^(?:[a-z_][a-z0-9_]*\.)+\*", compact):
        return True
    for match in re.finditer(r"\*", compact):
        before = compact[: match.start()].rstrip()
        after = compact[match.end() :].lstrip()
        previous = before[-1:] if before else ""
        next_char = after[:1]
        if previous in {"(", "."} or next_char in {"", ")", ","} or after.startswith("except"):
            return True
    return False


def _sql_has_wildcard_expansion(sql: str) -> bool:
    compact = re.sub(
        r"\s*\.\s*",
        ".",
        re.sub(r"\s+", " ", sql.replace("`", "")).strip(),
    ).lower()
    compact = re.sub(
        r"\b(?:count|approx_count_distinct)\s*\(\s*\*\s*\)",
        "safe_count_star()",
        compact,
        flags=re.IGNORECASE,
    )
    if re.search(r"\bselect\s+\*", compact) or re.search(r",\s*\*", compact):
        return True
    for match in re.finditer(r"\*", compact):
        before = compact[: match.start()].rstrip()
        after = compact[match.end() :].lstrip()
        previous = before[-1:] if before else ""
        next_char = after[:1]
        if previous in {"(", ".", ","} or next_char in {"", ")", ","} or after.startswith("except"):
            return True
    return False


def _cte_names(sql: str) -> set[str]:
    names: set[str] = set()
    for match in re.finditer(
        rf"(?:\bwith\b|,)\s+({_SQL_IDENT_RE})\s+as\s*\(",
        sql,
        flags=re.IGNORECASE,
    ):
        names.add(match.group(1).strip("`").lower())
    return names


def _split_top_level_commas(text: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    in_quote: str | None = None
    index = 0
    while index < len(text):
        char = text[index]
        if in_quote:
            if char == in_quote:
                in_quote = None
            index += 1
            continue
        if char in {"'", '"', "`"}:
            in_quote = char
        elif char == "(":
            depth += 1
        elif char == ")" and depth > 0:
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(text[start:index])
            start = index + 1
        index += 1
    parts.append(text[start:])
    return parts


def _iter_from_clause_segments(sql: str) -> list[str]:
    clause_boundary = re.compile(
        r"\b(?:where|group\s+by|order\s+by|having|limit|qualify|union|intersect|except)\b",
        flags=re.IGNORECASE,
    )
    segments: list[str] = []
    for match in re.finditer(r"\bfrom\b", sql, flags=re.IGNORECASE):
        start = match.end()
        depth = 0
        end = len(sql)
        index = start
        while index < len(sql):
            char = sql[index]
            if char == "(":
                depth += 1
            elif char == ")":
                if depth == 0:
                    end = index
                    break
                depth -= 1
            if depth == 0:
                boundary = clause_boundary.match(sql, index)
                if boundary is not None:
                    end = index
                    break
            index += 1
        segments.append(sql[start:end])
    return segments


def _relation_refs(sql: str) -> list[str]:
    seen: list[str] = []
    relation_pattern = re.compile(
        rf"\b(?:from|join)\s+({_SQL_IDENT_RE}(?:\s*\.\s*{_SQL_IDENT_RE}){{0,2}})(?!\s*\.)",
        flags=re.IGNORECASE,
    )
    parenthesized_relation_pattern = re.compile(
        rf"\b(?:from|join)\s*\(\s*({_SQL_IDENT_RE}(?:\s*\.\s*{_SQL_IDENT_RE}){{0,2}})\s*\)",
        flags=re.IGNORECASE,
    )
    for match in relation_pattern.finditer(sql):
        ref = _normalise_sql_ref(match.group(1))
        if ref and ref not in seen:
            seen.append(ref)
    for match in parenthesized_relation_pattern.finditer(sql):
        ref = _normalise_sql_ref(match.group(1))
        if ref and ref not in seen:
            seen.append(ref)

    leading_relation = re.compile(
        rf"^\s*({_SQL_IDENT_RE}(?:\s*\.\s*{_SQL_IDENT_RE}){{0,2}})(?!\s*\.)",
        flags=re.IGNORECASE,
    )
    parenthesized_leading_relation = re.compile(
        rf"^\s*\(\s*({_SQL_IDENT_RE}(?:\s*\.\s*{_SQL_IDENT_RE}){{0,2}})\s*\)",
        flags=re.IGNORECASE,
    )
    for segment in _iter_from_clause_segments(sql):
        for part in _split_top_level_commas(segment):
            stripped = part.lstrip()
            if re.match(r"^\(\s*(?:select|with)\b", stripped, flags=re.IGNORECASE):
                continue
            match = leading_relation.match(part) or parenthesized_leading_relation.match(part)
            if match is None:
                continue
            ref = _normalise_sql_ref(match.group(1))
            if ref and ref not in seen:
                seen.append(ref)
    return seen


def _sql_has_unqualified_relations(sql: str) -> bool:
    ctes = _cte_names(sql)
    for ref in _relation_refs(sql):
        parts = ref.split(".")
        if len(parts) == 1 and parts[0] not in ctes:
            return True
    return False


def _sql_mentions_pii_columns(sql: str | None) -> bool:
    if sql and _sql_has_raw_identifier_literal(sql):
        return True
    scrubbed = _scrub_sql_for_policy(sql)
    if not scrubbed:
        return False
    if _sql_has_wildcard_expansion(scrubbed):
        return True
    select_match = re.search(r"\bselect\b(?P<select>.*?)\bfrom\b", scrubbed, re.IGNORECASE | re.DOTALL)
    if select_match is not None:
        select_list = select_match.group("select")
        projected_terms = [term.strip() for term in select_list.split(",")]
        for term in projected_terms:
            if _projection_has_wildcard_expansion(term):
                return True
            if (
                re.search(r"(?<![A-Za-z0-9_])(?:[A-Za-z_][A-Za-z0-9_]*\.)?clip(?![A-Za-z0-9_])", term)
                and not re.search(r"\b(count|approx_count_distinct)\s*\(", term, flags=re.IGNORECASE)
            ):
                return True
    normalised = re.sub(r"[^a-z0-9_]+", " ", scrubbed.lower())
    tokens = set(normalised.split())
    return bool(tokens & _GENIE_PII_SQL_COLUMNS)


def _extract_asset_refs(sql: str | None) -> list[str]:
    """Pull three-part UC references out of a SQL string.

    Best-effort, but intentionally returns every unique reference it
    sees. UI callers can choose how many chips to render; trust
    enforcement must evaluate the whole query.
    """
    if not sql:
        return []
    policy_sql = _scrub_sql_for_policy(sql)
    if not policy_sql:
        return []

    seen: list[str] = []
    for ref in _relation_refs(policy_sql):
        if ref and len(ref.split(".")) >= 2 and ref not in seen:
            seen.append(ref)
    return seen


__all__ = [
    "DatabricksBorrowerRepository",
    "DatabricksGenieRepository",
    "DatabricksGeoRepository",
    "DatabricksLeadRepository",
    "DatabricksOfferRepository",
    "DatabricksOutreachRepository",
    "DatabricksPortfolioRepository",
    "DatabricksSegmentRepository",
]
