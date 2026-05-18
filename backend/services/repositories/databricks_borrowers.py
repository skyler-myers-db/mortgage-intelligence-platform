"""Databricks-backed borrower, offer, and outreach repositories.

These classes are split from ``databricks_repo`` to keep the public repository
facade stable while making borrower-specific SQL and redaction behavior easier
to review in isolation.
"""

from __future__ import annotations

import logging
import re

from backend.config.settings import settings
from backend.schemas.common import EvidenceEvent
from backend.schemas.lead import Borrower360, LeadSummary
from backend.schemas.why import WhyPanel, WhyPanelSource
from backend.services.county_names import county_fips_for_name
from backend.services.databricks_sql import DatabricksSqlClient
from backend.services.databricks_sql_helpers import qualify
from backend.services.observability import emit
from backend.services.pii_redaction import (
    redact_borrower_row,
    redact_evidence_row,
    redact_lead_row,
)
from backend.services.repositories.databricks_genie_canonical import _US_STATE_FILTERS
from backend.services.repositories.databricks_shared import (
    _BORROWER_DOSSIER_COLUMNS,
    _EVIDENCE_COLUMNS,
    _coerce_bool,
    _parse_timeline,
    _redact_evidence_list,
)
from backend.services.resilience import TTLCache
from backend.services.scoring import (
    NBO_PRODUCT_LABELS,
    in_the_money,
    source_display_label,
)

log = logging.getLogger(__name__)


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

    def __init__(
        self,
        client: DatabricksSqlClient,
        *,
        cache: TTLCache | None = None,
        cache_ttl_s: float | None = None,
    ) -> None:
        self._client = client
        self._cache = cache if cache is not None else TTLCache()
        self._cache_ttl_s = settings.mip_cache_ttl_s if cache_ttl_s is None else cache_ttl_s

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

    # Fallback evidence fetch -- preserved so /api/borrowers/{id}/evidence
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
        cache_key = f"borrower_dossier:{borrower_id}"
        if self._cache_ttl_s > 0:
            cached = self._cache.get(cache_key)
            if isinstance(cached, Borrower360):
                return cached.model_copy(deep=True)

        # Single-statement indexed lookup on the dossier cluster key
        # (borrower_id). Evidence + trigger timeline are both pre-joined
        # as ARRAY<STRUCT> columns, so no fan-out is needed.
        row = self._client.execute_one(self._GET_SQL, {"borrower_id": borrower_id})
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
        # (raised by Copilot 2026-04-22 - [:1] under-populated the UI).
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
                WhyPanelSource(name=s, display_label=source_display_label(s)) for s in why_sources
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
        # Defence in depth: compare gold's materialized ITM flag against the
        # canonical Python primitive using the same applied thresholds. Do not
        # break a borrower read in production; emit a structured warning so
        # operators can triage scoring drift without losing the dossier.
        expected_itm = in_the_money(
            borrower.rate_spread_bps,
            int(row.get("equity_pct") or 0),
            why.min_spread_bps,
            why.min_equity_pct,
        )
        if expected_itm != borrower.why_panel.in_the_money:
            emit(
                log,
                "borrower_itm_parity_drift",
                level=logging.WARNING,
                dependency="warehouse",
                outcome="error",
                expected_itm=expected_itm,
                actual_itm=borrower.why_panel.in_the_money,
                rate_spread_bps=borrower.rate_spread_bps,
                equity_pct=int(row.get("equity_pct") or 0),
                min_spread_bps=why.min_spread_bps,
                min_equity_pct=why.min_equity_pct,
            )
        if self._cache_ttl_s > 0:
            self._cache.set(cache_key, borrower, self._cache_ttl_s)
        return borrower.model_copy(deep=True)

    def evidence(self, borrower_id: str) -> list[EvidenceEvent] | None:
        # Prefer reading from the dossier's pre-joined evidence array --
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
        # Dossier row present but evidence empty -- fall back to direct
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
        county_contains = (
            f"%{county_term.upper()}%" if len(county_term) >= 2 else "__NO_COUNTY_MATCH__"
        )
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
        "  recommended_offer_code, min_spread_bps_applied, min_equity_pct_applied, "
        "  heloc_equity_min_applied, cashout_equity_min_applied, "
        "  retention_min_spread_applied "
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
            "min_spread_bps": int(row.get("min_spread_bps_applied") or 75),
            "min_equity_pct": int(row.get("min_equity_pct_applied") or 15),
            "heloc_equity_min_pct": int(row.get("heloc_equity_min_applied") or 35),
            "cashout_equity_min_pct": int(row.get("cashout_equity_min_applied") or 25),
            "retention_min_spread_bps": int(row.get("retention_min_spread_applied") or 50),
        }


class DatabricksOutreachRepository:
    """Borrower lookup for outreach draft composition -- same projection
    as ``BorrowerRepository.get`` but carved separately so outreach can
    grow draft-specific columns (opt-out, channel-preference) without
    widening the borrower surface.
    """

    def __init__(
        self, client: DatabricksSqlClient, borrower_repo: DatabricksBorrowerRepository
    ) -> None:
        self._client = client
        self._borrower_repo = borrower_repo

    def find_borrower(self, borrower_id: str) -> Borrower360 | None:
        return self._borrower_repo.get(borrower_id)
