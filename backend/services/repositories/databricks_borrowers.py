"""Databricks-backed borrower, offer, and outreach repositories.

These classes are split from ``databricks_repo`` to keep the public repository
facade stable while making borrower-specific SQL and redaction behavior easier
to review in isolation.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from threading import Lock

from backend.config.settings import settings
from backend.schemas.common import EvidenceEvent
from backend.schemas.lead import Borrower360, LeadSummary
from backend.schemas.proof import (
    BorrowerProof,
)
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
from backend.services.repositories.databricks_borrower_proof import (
    # Re-exported: the repository below calls it, and tests/fixtures import
    # it from this module's original path.
    _build_borrower_proof,
)
from backend.services.repositories.databricks_genie_canonical import _US_STATE_FILTERS
from backend.services.repositories.databricks_lead_cohort_support import (
    LeadCohortQuerySupport,
)
from backend.services.repositories.databricks_shared import (
    _BORROWER_DOSSIER_COLUMNS,
    _EVIDENCE_COLUMNS,
    _coerce_bool,
    _parse_timeline,
    _redact_evidence_list,
)
from backend.services.resilience import TTLCache
from backend.services.scoring import (
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
    p95 from ~4600 ms to ~3300 ms; folding them into one indexed row read
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
        self._cache_guard = Lock()
        self._cache_generation = 0

    _GET_SQL = (
        f"SELECT {_BORROWER_DOSSIER_COLUMNS} "
        f"FROM {qualify('gold', 'borrower_dossier')} "
        "WHERE borrower_id = :borrower_id "
        "LIMIT 1"
    )

    _PROOF_SQL = (
        "SELECT "
        "  b.clip, b.borrower_id, b.city, b.state, b.zip, b.evidence_ids, "
        "  b.equity_estimate, b.equity_pct, b.rate_spread_bps, b.market_rate_fraction, "
        "  b.opportunity_score, b.confidence, b.recommended_offer_code, b.recommended_offer, "
        "  b.avm_value, b.current_lien_balance, b.current_rate, b.ltv, "
        "  b.related_property_count, b.is_owner_occupied, b.is_absentee, "
        "  b.is_corporate_owner, b.is_investor, b.is_current_customer, b.is_former_customer, "
        "  b.is_competitor_lien, b.has_permit, b.listed_for_sale, "
        "  b.heloc_propensity_score, b.has_heloc_propensity_trigger, "
        "  b.refi_propensity_score, b.has_refi_propensity_trigger, b.second_pos_amount, "
        "  b.has_first_party_relationship, b.first_party_relationship_depth, "
        "  b.first_party_recent_interactions, b.first_party_recent_application, "
        "  b.min_spread_bps_applied, b.min_equity_pct_applied, "
        "  b.heloc_equity_min_applied, b.cashout_equity_min_applied, "
        "  b.retention_min_spread_applied, b.in_the_money, b.first_pos_loan_type, "
        "  b.evidence_events, b.refreshed_at AS dossier_refreshed_at, "
        "  ls.economic_incentive, ls.intent_trigger, ls.fit, ls.relationship, ls.evidence, "
        "  ls.opportunity_score AS score_opportunity_score, "
        "  ls.confidence AS score_signal_strength, "
        "  ls.refreshed_at AS score_refreshed_at "
        f"FROM {qualify('gold', 'borrower_dossier')} AS b "
        f"LEFT JOIN {qualify('gold', 'lead_scores')} AS ls "
        "  ON ls.clip = b.clip "
        "WHERE b.borrower_id = :borrower_id "
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
        "  b.owner_count, b.has_unresolved_owner, b.primary_owner_entity_type, "
        "  b.current_lien_balance, b.second_pos_amount, b.has_permit, b.listed_for_sale, "
        "  b.listing_status_category, b.listing_status_description, b.listing_date, "
        "  b.listing_status_date, b.listing_price, b.listing_days_on_market, b.listing_service, "
        "  b.heloc_propensity_score, b.heloc_propensity_run_date, b.has_heloc_propensity_trigger, "
        "  b.refi_propensity_score, b.refi_propensity_run_date, b.has_refi_propensity_trigger, "
        "  b.marketing_eligible, b.consent_status, b.suppression_reason, b.last_touch_at, "
        "  b.eligible_recontact_at "
        f"FROM {qualify('gold', 'borrower_360')} AS b "
        "LEFT JOIN latest_counties AS cr "
        "  ON cr.fips_5 = b.county_fips_5 "
        "WHERE UPPER(b.borrower_id) LIKE :borrower_prefix "
        "   OR b.zip = :zip_exact "
        "   OR b.zip LIKE :zip_prefix "
        "   OR UPPER(b.city) LIKE :term_contains "
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
        with self._cache_guard:
            generation = self._cache_generation
            cached = self._cache.get(cache_key) if self._cache_ttl_s > 0 else None
        if self._cache_ttl_s > 0 and isinstance(cached, Borrower360):
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
            with self._cache_guard:
                if self._cache_generation == generation:
                    self._cache.set(cache_key, borrower, self._cache_ttl_s)
        return borrower.model_copy(deep=True)

    def get_fresh(self, borrower_id: str) -> Borrower360 | None:
        """Bypass a cached dossier generation during a coordinated refresh."""

        cache_key = f"borrower_dossier:{borrower_id}"
        with self._cache_guard:
            self._cache_generation += 1
            self._cache.invalidate(cache_key)
        return self.get(borrower_id)

    #: Bind-parameter ceiling per existence statement. 250 ids is one
    #: round-trip for every caller we have today (``/sales/aging`` reads at
    #: most 500 candidates) while keeping the statement well inside the
    #: connector's parameter limits on any future larger batch.
    _EXISTS_CHUNK = 250

    _EXISTS_SQL_TEMPLATE = (
        "SELECT borrower_id "
        f"FROM {qualify('gold', 'borrower_dossier')} "
        "WHERE 1 = 1 {borrower_clause}"
    )

    def existing_borrower_ids(self, borrower_ids: Sequence[str]) -> set[str]:
        """Return the subset of ``borrower_ids`` present in the dossier table.

        One statement per :attr:`_EXISTS_CHUNK` ids instead of the per-id
        ``get()`` loop the sales-aging route used to run (2026-08-07 platform
        audit F1: 250 sequential round-trips, 25 s warm). Ids are normalised
        through the same public-id validator the routers use, so a malformed
        value is dropped rather than bound into SQL.
        """
        normalised = LeadCohortQuerySupport.normalise_borrower_ids(list(borrower_ids))
        if not normalised:
            return set()
        found: set[str] = set()
        for start in range(0, len(normalised), self._EXISTS_CHUNK):
            chunk = normalised[start : start + self._EXISTS_CHUNK]
            params: dict[str, object] = {}
            clause = LeadCohortQuerySupport.in_clause(
                column="borrower_id",
                prefix="exists_borrower_id",
                values=chunk,
                params=params,
            )
            rows = self._client.execute(
                self._EXISTS_SQL_TEMPLATE.format(borrower_clause=clause),
                params,
            )
            for row in rows or []:
                value = str(row.get("borrower_id") or "").strip()
                if value:
                    found.add(value)
        return found

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

    def proof(self, borrower_id: str) -> BorrowerProof | None:
        row = self._client.execute_one(self._PROOF_SQL, {"borrower_id": borrower_id})
        if row is None:
            return None
        return _build_borrower_proof(row)

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
