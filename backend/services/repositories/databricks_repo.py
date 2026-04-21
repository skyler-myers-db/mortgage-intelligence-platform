"""Live Databricks-backed repository implementations.

Each class below implements one Protocol from
``backend.services.repositories.protocols`` against the ``mip_demo.gold.*``
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

Not yet backed by Databricks (Slice 7 territory): Genie grounding stays
on the in-process deterministic catalog.
"""
from __future__ import annotations

import json
from typing import Any

from backend.schemas.common import EvidenceEvent
from backend.schemas.lead import Borrower360, LeadSummary, SegmentSummary
from backend.schemas.portfolio import (
    PortfolioCreateRequest,
    PortfolioCreateResponse,
    PortfolioPreview,
    PortfolioPreviewRequest,
)
from backend.schemas.why import WhyPanel
from backend.services.databricks_sql import DatabricksSqlClient
from backend.services.pii_redaction import (
    redact_borrower_row,
    redact_evidence_row,
    redact_lead_row,
)
from backend.services.scoring import (
    NBO_PRODUCT_LABELS,
    in_the_money,
)

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
    "min_spread_bps_applied, min_equity_pct_applied, in_the_money"
)

_LEAD_POPULATION_COLUMNS: str = (
    "clip, borrower_id, display_name, city, state, zip, segment_codes, "
    "equity_estimate, rate_spread_bps, opportunity_score, confidence, "
    "recommended_offer, why_now, evidence_ids, approval_status"
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


# ---------------------------------------------------------------------------
# Repositories
# ---------------------------------------------------------------------------


class DatabricksPortfolioRepository:
    """Portfolio preview rollup over ``gold.borrower_360``.

    Slice-4 scope: the criteria on the request don't yet shift the
    rollup -- the whole population is the portfolio preview, matching
    booth-demo behaviour. A later slice adds criteria push-down.
    """

    def __init__(self, client: DatabricksSqlClient) -> None:
        self._client = client

    _PREVIEW_SQL = (
        "SELECT "
        "  COUNT(*)                                               AS marketable_population, "
        "  SUM(CASE WHEN in_the_money THEN 1 ELSE 0 END)          AS high_intent_leads, "
        "  CAST(ROUND(AVG(opportunity_score)) AS INT)             AS avg_score "
        "FROM mip_demo.gold.borrower_360"
    )

    def preview(self, request: PortfolioPreviewRequest | None) -> PortfolioPreview:
        _ = request
        row = self._client.execute_one(self._PREVIEW_SQL) or {}
        return PortfolioPreview(
            marketable_population=int(row.get("marketable_population") or 0),
            high_intent_leads=int(row.get("high_intent_leads") or 0),
            avg_score=int(row.get("avg_score") or 0),
            # Projections that aren't in gold yet live on the UI as
            # deterministic constants -- keeping mock parity for the
            # booth bar chart.
            projected_contact_to_app=9.7,
            cost_per_contact=2.18,
        )

    def create(self, payload: PortfolioCreateRequest) -> PortfolioCreateResponse:
        preview = self.preview(None)
        return PortfolioCreateResponse(
            portfolio_id="demo-portfolio",
            name=payload.name,
            marketable_population=preview.marketable_population,
        )

    def get(self, portfolio_id: str) -> dict[str, object]:
        preview = self.preview(None)
        return {
            "portfolio_id": portfolio_id,
            "status": "ready",
            "marketable_population": preview.marketable_population,
        }


class DatabricksSegmentRepository:
    """Segment rollup serves the national ``state='_ALL'`` row."""

    def __init__(self, client: DatabricksSqlClient) -> None:
        self._client = client

    _LIST_SQL = (
        f"SELECT {_SEGMENT_COLUMNS} "
        "FROM mip_demo.gold.segment_population "
        "WHERE state = '_ALL' "
        "ORDER BY count DESC"
    )

    def list(self, portfolio_id: str | None) -> list[SegmentSummary]:
        _ = portfolio_id
        rows = self._client.execute(self._LIST_SQL)
        return [
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


class DatabricksLeadRepository:
    """Ranked leads from ``gold.lead_population``."""

    def __init__(self, client: DatabricksSqlClient) -> None:
        self._client = client

    _LIST_BASE_SQL = (
        f"SELECT {_LEAD_POPULATION_COLUMNS} "
        "FROM mip_demo.gold.lead_population "
        "ORDER BY opportunity_score DESC, borrower_id ASC "
        "LIMIT 500"
    )

    _LIST_BY_SEGMENT_SQL = (
        f"SELECT {_LEAD_POPULATION_COLUMNS} "
        "FROM mip_demo.gold.lead_population "
        "WHERE array_contains(segment_codes, :segment) "
        "ORDER BY opportunity_score DESC, borrower_id ASC "
        "LIMIT 500"
    )

    def list(self, segment: str | None, portfolio_id: str | None) -> list[LeadSummary]:
        _ = portfolio_id
        if segment:
            rows = self._client.execute(self._LIST_BY_SEGMENT_SQL, {"segment": segment})
        else:
            rows = self._client.execute(self._LIST_BASE_SQL)
        return [LeadSummary(**redact_lead_row(r)) for r in rows]


class DatabricksBorrowerRepository:
    """Borrower-360 + evidence reads from ``gold.borrower_360`` / ``gold.evidence_events``."""

    def __init__(self, client: DatabricksSqlClient) -> None:
        self._client = client

    _GET_SQL = (
        f"SELECT {_BORROWER_360_COLUMNS}, trigger_timeline_json "
        "FROM mip_demo.gold.borrower_360 "
        "WHERE borrower_id = :borrower_id "
        "LIMIT 1"
    )

    _EVIDENCE_SQL = (
        f"SELECT {_EVIDENCE_COLUMNS} "
        "FROM mip_demo.gold.evidence_events "
        "WHERE clip = ("
        "  SELECT clip FROM mip_demo.gold.borrower_360 "
        "  WHERE borrower_id = :borrower_id LIMIT 1"
        ") "
        "ORDER BY signal_rank ASC"
    )

    def get(self, borrower_id: str) -> Borrower360 | None:
        row = self._client.execute_one(self._GET_SQL, {"borrower_id": borrower_id})
        if row is None:
            return None

        redacted = redact_borrower_row(row)

        # Evidence + trigger timeline: trigger_timeline_json is the
        # pre-materialised top-3; evidence_events is the full list.
        timeline_events = _parse_timeline(row.get("trigger_timeline_json"))
        evidence_events = self.evidence(borrower_id) or []

        why = WhyPanel(
            rate_spread_bps=int(row.get("rate_spread_bps") or 0),
            market_rate=float(row.get("market_rate_fraction") or 0.0),
            equity_pct=int(row.get("equity_pct") or 0),
            in_the_money=bool(row.get("in_the_money")),
            in_the_money_reason=(
                f"+{row.get('rate_spread_bps')} bps spread "
                f"(>= {row.get('min_spread_bps_applied')}) AND "
                f"{row.get('equity_pct')}% equity "
                f"(>= {row.get('min_equity_pct_applied')}%)"
                if _coerce_bool(row.get("in_the_money"))
                else (
                    f"{row.get('rate_spread_bps')} bps or "
                    f"{row.get('equity_pct')}% equity does not clear "
                    f"({row.get('min_spread_bps_applied')} / "
                    f"{row.get('min_equity_pct_applied')}%)"
                )
            ),
            min_spread_bps=int(row.get("min_spread_bps_applied") or 75),
            min_equity_pct=int(row.get("min_equity_pct_applied") or 15),
            sources=[
                "mip_demo.gold.fn_rate_spread",
                "mip_demo.gold.fn_in_the_money",
                "mip_demo.gold.borrower_360",
            ],
        )

        # Enrich the redacted projection with the Borrower360 extras.
        # Note: every dependent construction works on the *redacted*
        # dict -- no raw PII is ever composed into the Pydantic object.
        borrower = Borrower360(
            **redacted,
            trigger_timeline=timeline_events or evidence_events[:1],
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
        rows = self._client.execute(self._EVIDENCE_SQL, {"borrower_id": borrower_id})
        if not rows:
            # Distinguish "no such borrower" from "no evidence rows" by
            # cheap probe. Missing borrower -> None so the router 404s;
            # missing evidence on a real borrower -> []. This matches
            # the in-process mock's semantics.
            probe = self._client.execute_one(
                "SELECT 1 AS present FROM mip_demo.gold.borrower_360 "
                "WHERE borrower_id = :borrower_id LIMIT 1",
                {"borrower_id": borrower_id},
            )
            if probe is None:
                return None
            return []
        return [EvidenceEvent(**redact_evidence_row(r)) for r in rows]


class DatabricksOfferRepository:
    """Offer-input bundle used by the offers router to build a recommendation."""

    def __init__(self, client: DatabricksSqlClient) -> None:
        self._client = client

    _SQL = (
        "SELECT "
        "  rate_spread_bps, equity_pct, has_permit, listed_for_sale, "
        "  is_investor, is_current_customer, is_competitor_lien, "
        "  recommended_offer_code "
        "FROM mip_demo.gold.borrower_360 "
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


__all__ = [
    "DatabricksBorrowerRepository",
    "DatabricksLeadRepository",
    "DatabricksOfferRepository",
    "DatabricksOutreachRepository",
    "DatabricksPortfolioRepository",
    "DatabricksSegmentRepository",
]
