"""Databricks-backed borrower, offer, and outreach repositories.

These classes are split from ``databricks_repo`` to keep the public repository
facade stable while making borrower-specific SQL and redaction behavior easier
to review in isolation.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from backend.config.settings import settings
from backend.schemas.common import EvidenceEvent
from backend.schemas.lead import Borrower360, LeadSummary
from backend.schemas.proof import (
    BorrowerProof,
    ProofEvidenceEvent,
    ProofFormulaLine,
    ProofOfferBranch,
    ProofReproduceQuery,
    ProofScoreComponent,
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
from backend.services.proof_policy import (
    borrower_proof_assets,
    hash_sql,
    validate_borrower_proof_sql,
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
    lead_score,
    next_best_offer,
    offer_display_label,
    source_display_label,
)

log = logging.getLogger(__name__)

_SCORE_WEIGHTS: dict[str, float] = {
    "economic_incentive": 0.35,
    "intent_trigger": 0.30,
    "fit": 0.15,
    "relationship": 0.10,
    "evidence": 0.10,
}

_SCORE_LABELS: dict[str, str] = {
    "economic_incentive": "Economic incentive",
    "intent_trigger": "Intent trigger",
    "fit": "Product fit",
    "relationship": "Relationship",
    "evidence": "Evidence coverage",
}

_SCORE_EXPLANATIONS: dict[str, str] = {
    "economic_incentive": (
        "Rate spread and home equity. Higher values mean the current lien sits "
        "far enough above market and the property has usable equity."
    ),
    "intent_trigger": (
        "Time-sensitive Cotality and first-party signals such as competitor "
        "lien activity, market movement, prior payoff/refi events, and product intent."
    ),
    "fit": (
        "Borrower/property fit for the offer lane, including owner-occupancy, "
        "first-lien product type, and investor/corporate-owner posture."
    ),
    "relationship": (
        "Current or former lender relationship, competitor lien status, owner-link "
        "history, and first-party engagement depth."
    ),
    "evidence": (
        "Count and strength of governed evidence rows attached to this borrower, "
        "plus bounded second-position lien context."
    ),
}

_SCORE_FIELDS: dict[str, list[str]] = {
    "economic_incentive": ["rate_spread_bps", "equity_pct"],
    "intent_trigger": [
        "is_competitor_lien",
        "is_investor",
        "is_current_customer",
        "has_permit",
        "listed_for_sale",
        "has_heloc_propensity_trigger",
        "heloc_propensity_score",
        "has_refi_propensity_trigger",
        "refi_propensity_score",
    ],
    "fit": ["is_owner_occupied", "first_pos_loan_type", "is_corporate_owner", "is_investor"],
    "relationship": [
        "is_current_customer",
        "is_former_customer",
        "is_competitor_lien",
        "related_property_count",
        "first_party_relationship_depth",
    ],
    "evidence": ["evidence_ids", "second_pos_amount"],
}

_FIT_FAIR_LENDING_NOTE = (
    "Audit note: this signal supports marketing prioritization, not credit eligibility."
)

_RELATIONSHIP_FAIR_LENDING_NOTE = (
    "Audit note: relationship status supports marketing prioritization, not credit eligibility."
)

_INVESTOR_FAIR_LENDING_NOTE = (
    "Audit note: ownership posture supports routing; it is not used for credit eligibility."
)


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


def _int_value(row: dict[str, Any], key: str, default: int = 0) -> int:
    value = row.get(key)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float_value(row: dict[str, Any], key: str, default: float = 0.0) -> float:
    value = row.get(key)
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _fair_lending_note(key: str) -> str | None:
    if key == "fit":
        return _FIT_FAIR_LENDING_NOTE
    if key == "relationship":
        return _RELATIONSHIP_FAIR_LENDING_NOTE
    if key == "intent_trigger":
        return _INVESTOR_FAIR_LENDING_NOTE
    return None


def _component_rows(row: dict[str, Any]) -> tuple[list[ProofScoreComponent], list[str]]:
    missing = [key for key in _SCORE_WEIGHTS if row.get(key) is None]
    if missing:
        return [], [
            "mip.gold.lead_scores did not return component sub-scores for this borrower; "
            "the proof is limited to materialized dossier outputs."
        ]
    out: list[ProofScoreComponent] = []
    for key, weight in _SCORE_WEIGHTS.items():
        value = _int_value(row, key)
        out.append(
            ProofScoreComponent(
                key=key,  # type: ignore[arg-type]
                label=_SCORE_LABELS[key],
                value=value,
                weight=weight,
                weighted_points=round(value * weight, 2),
                explanation=_SCORE_EXPLANATIONS[key],
                source_fields=_SCORE_FIELDS[key],
                fair_lending_note=_fair_lending_note(key),
            )
        )
    return out, []


def _offer_branches(row: dict[str, Any], selected_code: str) -> list[ProofOfferBranch]:
    spread = _int_value(row, "rate_spread_bps")
    equity = _int_value(row, "equity_pct")
    min_spread = _int_value(row, "min_spread_bps_applied", 75)
    min_equity = _int_value(row, "min_equity_pct_applied", 15)
    heloc_min = _int_value(row, "heloc_equity_min_applied", 35)
    cashout_min = _int_value(row, "cashout_equity_min_applied", 25)
    retention_min = _int_value(row, "retention_min_spread_applied", 50)
    has_permit = _coerce_bool(row.get("has_permit"))
    has_heloc_intent = has_permit or _coerce_bool(row.get("has_heloc_propensity_trigger"))
    listed = _coerce_bool(row.get("listed_for_sale"))
    investor = _coerce_bool(row.get("is_investor"))
    customer = _coerce_bool(row.get("is_current_customer"))
    competitor = _coerce_bool(row.get("is_competitor_lien"))

    specs: list[tuple[str, bool, str]] = [
        ("purchase", listed, "Listed-for-sale signal is true."),
        (
            "refi_plus_heloc",
            spread >= min_spread and equity >= heloc_min,
            f"Rate spread {spread} bps >= {min_spread} and equity {equity}% >= {heloc_min}%.",
        ),
        (
            "heloc",
            has_heloc_intent and equity >= heloc_min,
            f"HELOC-intent signal is {has_heloc_intent} and equity {equity}% >= {heloc_min}%.",
        ),
        (
            "refi",
            spread >= min_spread and equity >= min_equity,
            f"Rate spread {spread} bps >= {min_spread} and equity {equity}% >= {min_equity}%.",
        ),
        ("cash_out", equity >= cashout_min, f"Equity {equity}% >= cash-out threshold {cashout_min}%."),
        ("investor", investor, f"Investor or multi-property signal is {investor}."),
        (
            "retention",
            customer and (spread >= retention_min or competitor),
            f"Current-customer signal is {customer}; spread {spread} bps >= {retention_min} or competitor lien is {competitor}.",
        ),
    ]
    positive_branch_passed = any(passed for _, passed, _ in specs)
    branches = [
        ProofOfferBranch(
            code=code,
            label=offer_display_label(code, NBO_PRODUCT_LABELS[code]),
            passed=passed,
            selected=selected_code == code,
            reason=reason,
        )
        for code, passed, reason in specs
    ]
    branches.append(
        ProofOfferBranch(
            code="nurture",
            label=offer_display_label("nurture", NBO_PRODUCT_LABELS["nurture"]),
            passed=not positive_branch_passed,
            selected=selected_code == "nurture",
            reason="Fallback lane when no positive outreach branch is selected.",
        )
    )
    return branches


def _recomputed_offer_code(row: dict[str, Any]) -> str:
    return next_best_offer(
        _int_value(row, "rate_spread_bps"),
        _int_value(row, "equity_pct"),
        _coerce_bool(row.get("has_permit")) or _coerce_bool(row.get("has_heloc_propensity_trigger")),
        _coerce_bool(row.get("listed_for_sale")),
        _coerce_bool(row.get("is_investor")),
        _coerce_bool(row.get("is_current_customer")),
        _coerce_bool(row.get("is_competitor_lien")),
        _int_value(row, "min_spread_bps_applied", 75),
        _int_value(row, "min_equity_pct_applied", 15),
        _int_value(row, "heloc_equity_min_applied", 35),
        _int_value(row, "cashout_equity_min_applied", 25),
        _int_value(row, "retention_min_spread_applied", 50),
    )


def _proof_evidence_rows(raw: Any) -> list[ProofEvidenceEvent]:
    rows: list[ProofEvidenceEvent] = []
    for event in _redact_evidence_list(raw):
        rows.append(ProofEvidenceEvent(**event.model_dump(exclude={"source_table"})))
    return rows


def _proof_sql_templates() -> list[ProofReproduceQuery]:
    databricks_url = (
        f"{settings.databricks_host.rstrip('/')}/sql/editor"
        if settings.databricks_host
        else None
    )
    borrower_dossier = qualify("gold", "borrower_dossier")
    lead_scores = qualify("gold", "lead_scores")
    evidence_events = qualify("gold", "evidence_events")
    templates = [
        (
            "Score components",
            (
                "WITH borrower AS ("
                " SELECT borrower_id, clip, opportunity_score AS dossier_opportunity_score,"
                f" confidence AS dossier_signal_strength FROM {borrower_dossier}"
                " WHERE borrower_id = :borrower_id"
                "), score AS ("
                " SELECT ls.clip, ls.economic_incentive, ls.intent_trigger, ls.fit, ls.relationship,"
                " ls.evidence, ls.opportunity_score AS lead_scores_opportunity_score,"
                " ls.confidence AS lead_scores_signal_strength"
                f" FROM {lead_scores} AS ls"
                " JOIN borrower AS b ON b.clip = ls.clip"
                ") SELECT borrower.borrower_id, score.economic_incentive, score.intent_trigger,"
                " score.fit, score.relationship, score.evidence,"
                f" {qualify('gold', 'fn_lead_score')}(score.economic_incentive, score.intent_trigger,"
                " score.fit, score.relationship, score.evidence) AS recomputed_opportunity_score,"
                " CAST(ROUND((score.economic_incentive + score.intent_trigger + score.fit"
                " + score.relationship + score.evidence) / 5) AS INT) AS recomputed_signal_strength,"
                " borrower.dossier_opportunity_score, score.lead_scores_opportunity_score,"
                " borrower.dossier_signal_strength, score.lead_scores_signal_strength"
                " FROM borrower LEFT JOIN score ON score.clip = borrower.clip"
            ),
            "Recomputes the displayed score and signal strength, then shows both gold materializations.",
        ),
        (
            "Decision inputs",
            (
                "SELECT borrower_id, recommended_offer_code, recommended_offer, rate_spread_bps, equity_pct,"
                " has_permit, has_heloc_propensity_trigger, heloc_propensity_score, listed_for_sale,"
                " is_investor, is_current_customer, is_competitor_lien,"
                " min_spread_bps_applied, min_equity_pct_applied, heloc_equity_min_applied,"
                " cashout_equity_min_applied, retention_min_spread_applied,"
                f" {qualify('gold', 'fn_next_best_offer')}(rate_spread_bps, equity_pct,"
                " (has_permit OR has_heloc_propensity_trigger),"
                " listed_for_sale, is_investor, is_current_customer, is_competitor_lien,"
                " min_spread_bps_applied, min_equity_pct_applied, heloc_equity_min_applied,"
                " cashout_equity_min_applied, retention_min_spread_applied) AS recomputed_offer_code"
                f" FROM {borrower_dossier}"
                " WHERE borrower_id = :borrower_id"
            ),
            "Recomputes the primary offer from the exact public inputs and thresholds.",
        ),
        (
            "Evidence rows",
            (
                "SELECT evidence_id, source_product, signal_type, signal_value, display_text,"
                " confidence, `timestamp`"
                f" FROM {evidence_events} AS ev"
                f" WHERE EXISTS (SELECT 1 FROM {borrower_dossier} AS b"
                " WHERE b.borrower_id = :borrower_id AND b.clip = ev.clip)"
                " ORDER BY signal_rank ASC LIMIT 20"
            ),
            "Returns the same redacted evidence row shape used by the app drawer.",
        ),
    ]
    return [
        ProofReproduceQuery(
            title=title,
            sql=validate_borrower_proof_sql(sql),
            sql_hash=hash_sql(sql),
            note=note,
            databricks_sql_url=databricks_url,
        )
        for title, sql, note in templates
    ]


def _build_borrower_proof(row: dict[str, Any]) -> BorrowerProof:
    components, gaps = _component_rows(row)
    selected_code = str(row.get("recommended_offer_code") or "nurture")
    if selected_code not in NBO_PRODUCT_LABELS:
        selected_code = "nurture"
        gaps.append("recommended_offer_code was outside the governed primary-offer vocabulary.")
    recomputed_offer = _recomputed_offer_code(row)
    if recomputed_offer != selected_code:
        gaps.append(
            "Recomputed primary offer does not match the borrower dossier displayed offer; "
            f"recomputed {recomputed_offer}, dossier {selected_code}."
        )

    score = _int_value(row, "opportunity_score")
    signal_strength = _int_value(row, "confidence")
    score_row_score = row.get("score_opportunity_score")
    score_row_strength = row.get("score_signal_strength")
    dossier_refreshed_at = _str_or_none(row.get("dossier_refreshed_at"))
    score_refreshed_at = _str_or_none(row.get("score_refreshed_at"))
    if dossier_refreshed_at and score_refreshed_at and dossier_refreshed_at != score_refreshed_at:
        gaps.append(
            "Borrower dossier and lead_scores were refreshed at different times; "
            f"dossier {dossier_refreshed_at}, lead_scores {score_refreshed_at}."
        )
    if components:
        recomputed_score = lead_score(**{c.key: c.value for c in components})
        weighted_expr = " + ".join(f"{c.weight:.2f}*{c.value}" for c in components)
        score_result = f"{score} displayed opportunity score (recomputed {recomputed_score})"
        if score_row_score is not None and _int_value(row, "score_opportunity_score") != score:
            gaps.append(
                "Borrower dossier opportunity score does not match lead_scores materialized score; "
                f"dossier {score}, lead_scores {_int_value(row, 'score_opportunity_score')}."
            )
        if recomputed_score != score:
            gaps.append(
                "Recomputed opportunity score does not match the borrower dossier displayed score; "
                f"recomputed {recomputed_score}, dossier {score}."
            )
        if score_row_score is not None and recomputed_score != _int_value(row, "score_opportunity_score"):
            gaps.append(
                "Recomputed opportunity score does not match lead_scores materialized score; "
                f"recomputed {recomputed_score}, lead_scores {_int_value(row, 'score_opportunity_score')}."
            )
        strength_expr = "(" + " + ".join(str(c.value) for c in components) + ") / 5"
        strength_result = f"{signal_strength}% displayed signal strength"
        recomputed_strength = round(sum(c.value for c in components) / len(components))
        if score_row_strength is not None and _int_value(row, "score_signal_strength") != signal_strength:
            gaps.append(
                "Borrower dossier signal strength does not match lead_scores materialized value; "
                f"dossier {signal_strength}, lead_scores {_int_value(row, 'score_signal_strength')}."
            )
        if recomputed_strength != signal_strength:
            gaps.append(
                "Recomputed signal strength does not match the borrower dossier displayed value; "
                f"recomputed {recomputed_strength}, dossier {signal_strength}."
            )
        if score_row_strength is not None and recomputed_strength != _int_value(row, "score_signal_strength"):
            gaps.append(
                "Recomputed signal strength does not match lead_scores materialized value; "
                f"recomputed {recomputed_strength}, lead_scores {_int_value(row, 'score_signal_strength')}."
            )
    else:
        gaps.append("Governed lead_scores component row was unavailable, so the score cannot be recomputed.")
        weighted_expr = "component row unavailable"
        score_result = f"{score} materialized opportunity score"
        strength_expr = "component row unavailable"
        strength_result = f"{signal_strength}% materialized signal strength"

    current_rate_pct = _float_value(row, "current_rate")
    market_rate_pct = _float_value(row, "market_rate_fraction") * 100
    avm = _int_value(row, "avm_value")
    lien = _int_value(row, "current_lien_balance")
    equity = _int_value(row, "equity_estimate", max(0, avm - lien))
    equity_pct = _int_value(row, "equity_pct")
    ltv = _int_value(row, "ltv")

    return BorrowerProof(
        borrower_id=str(row.get("borrower_id") or ""),
        trusted=not gaps,
        known_data_gaps=gaps,
        generated_from=f"{qualify('gold', 'borrower_dossier')} + {qualify('gold', 'lead_scores')}",
        source_refresh_at=" / ".join(
            part
            for part in (
                f"dossier {dossier_refreshed_at}" if dossier_refreshed_at else None,
                f"lead_scores {score_refreshed_at}" if score_refreshed_at else None,
            )
            if part
        )
        or None,
        opportunity_score=score,
        signal_strength=signal_strength,
        signal_strength_note=(
            "Signal strength is a deterministic average of the five scoring sub-scores. "
            "It is not a statistical confidence interval and is not a credit decision probability."
        ),
        evidence_confidence_note=(
            "Evidence confidence is row-level source confidence from gold.evidence_events; "
            "AVM-backed rows use upstream AVM confidence, while count-based rows use governed constants."
        ),
        score_components=components,
        score_formula=ProofFormulaLine(
            label="Opportunity score",
            expression=weighted_expr,
            result=score_result,
            source=qualify("gold", "fn_lead_score"),
        ),
        signal_strength_formula=ProofFormulaLine(
            label="Signal strength",
            expression=strength_expr,
            result=strength_result,
            source=qualify("gold", "lead_scores"),
        ),
        rate_spread_formula=ProofFormulaLine(
            label="Rate spread",
            expression=f"({current_rate_pct:.3f}% current rate - {market_rate_pct:.3f}% market rate) * 100",
            result=f"{_int_value(row, 'rate_spread_bps')} bps",
            source=qualify("gold", "fn_rate_spread"),
        ),
        equity_formula=ProofFormulaLine(
            label="Equity",
            expression=f"{avm:,} AVM - {lien:,} current lien",
            result=f"{equity:,} equity ({equity_pct}%)",
            source=qualify("gold", "borrower_dossier"),
        ),
        ltv_formula=ProofFormulaLine(
            label="LTV",
            expression=f"{lien:,} current lien / {avm:,} AVM",
            result=f"{ltv}% LTV",
            source=qualify("gold", "borrower_dossier"),
        ),
        offer_code=selected_code,
        offer_label=offer_display_label(selected_code, NBO_PRODUCT_LABELS[selected_code]),
        offer_branches=_offer_branches(row, selected_code),
        evidence_rows=_proof_evidence_rows(row.get("evidence_events") or []),
        source_assets=borrower_proof_assets(),
        reproduce=_proof_sql_templates(),
    )


class DatabricksOfferRepository:
    """Offer-input bundle used by the offers router to build a recommendation."""

    def __init__(self, client: DatabricksSqlClient) -> None:
        self._client = client

    _SQL = (
        "SELECT "
        "  rate_spread_bps, equity_pct, has_permit, has_heloc_propensity_trigger, "
        "  heloc_propensity_score, has_refi_propensity_trigger, refi_propensity_score, "
        "  listed_for_sale, "
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
            "has_heloc_propensity_trigger": _coerce_bool(
                row.get("has_heloc_propensity_trigger")
            ),
            "heloc_propensity_score": int(row.get("heloc_propensity_score") or 0),
            "has_refi_propensity_trigger": _coerce_bool(
                row.get("has_refi_propensity_trigger")
            ),
            "refi_propensity_score": int(row.get("refi_propensity_score") or 0),
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
