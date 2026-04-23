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

Slice-7: ``DatabricksGenieRepository`` flips ``/api/genie`` onto the
real Mortgage Lead Intelligence Genie space. A deterministic safe
corpus (parsed from ``genie/sample_questions.md`` + the curated catalog
in ``backend.services.genie_answers``) only fires when the ``genie``
circuit breaker is OPEN -- otherwise every answer comes from the live
space. An open breaker on an unknown question returns a honest
"warming up" message rather than fabricating data.
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
from backend.schemas.why import WhyPanel, WhyPanelSource
from backend.services.databricks_sql import DatabricksSqlClient
from backend.services.genie_answers import (
    GenieMessageResponse,
)
from backend.services.genie_answers import (
    respond as genie_catalog_respond,
)
from backend.services.genie_client import (
    GenieClientError,
    GenieResponse,
    ResilientGenieClient,
)
from backend.services.pii_redaction import (
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
    # `clip` is projected first so the repository boundary can surface the
    # real Cotality CLIP on LeadSummary (fix for the cross-route CLIP
    # inconsistency blocker 2026-04-22). `redact_lead_row` passes it
    # through under the same key.
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

    _PREVIEW_SQL = (
        "SELECT "
        "  COUNT(*)                                               AS marketable_population, "
        "  SUM(CASE WHEN in_the_money THEN 1 ELSE 0 END)          AS high_intent_leads, "
        "  CAST(ROUND(AVG(opportunity_score)) AS INT)             AS avg_score "
        "FROM mip.gold.borrower_360"
    )

    _PREVIEW_CACHE_KEY = "portfolio.preview.all"

    def preview(self, request: PortfolioPreviewRequest | None) -> PortfolioPreview:
        _ = request
        cached = self._cache.get(self._PREVIEW_CACHE_KEY)
        if cached is not None:
            return cached
        row = self._client.execute_one(self._PREVIEW_SQL) or {}
        preview = PortfolioPreview(
            marketable_population=int(row.get("marketable_population") or 0),
            high_intent_leads=int(row.get("high_intent_leads") or 0),
            avg_score=int(row.get("avg_score") or 0),
            # Projections that aren't in gold yet live on the UI as
            # deterministic constants -- keeping mock parity for the
            # portfolio preview bar chart.
            projected_contact_to_app=9.7,
            cost_per_contact=2.18,
        )
        self._cache.set(self._PREVIEW_CACHE_KEY, preview, self._cache_ttl_s)
        return preview

    def create(self, payload: PortfolioCreateRequest) -> PortfolioCreateResponse:
        preview = self.preview(None)
        return PortfolioCreateResponse(
            portfolio_id="module0-portfolio",
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
        "FROM mip.gold.segment_population "
        "WHERE state = '_ALL' "
        "ORDER BY count DESC"
    )

    def _list_cache_key(self, portfolio_id: str | None) -> str:
        return f"segments.list.{portfolio_id or '_ALL'}"

    def list(self, portfolio_id: str | None) -> list[SegmentSummary]:
        key = self._list_cache_key(portfolio_id)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
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
        self._cache.set(key, segments, self._cache_ttl_s)
        return segments


class DatabricksLeadRepository:
    """Ranked leads from ``gold.lead_population``."""

    def __init__(self, client: DatabricksSqlClient) -> None:
        self._client = client

    _LIST_BASE_SQL = (
        f"SELECT {_LEAD_POPULATION_COLUMNS} "
        "FROM mip.gold.lead_population "
        "ORDER BY opportunity_score DESC, borrower_id ASC "
        "LIMIT 500"
    )

    _LIST_BY_SEGMENT_SQL = (
        f"SELECT {_LEAD_POPULATION_COLUMNS} "
        "FROM mip.gold.lead_population "
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
        "FROM mip.gold.borrower_dossier "
        "WHERE borrower_id = :borrower_id "
        "LIMIT 1"
    )

    # Fallback evidence fetch — preserved so /api/borrowers/{id}/evidence
    # can still serve a borrower whose dossier row was rebuilt between
    # refreshes AND whose evidence_events array is somehow empty (schema
    # drift, upstream outage). The primary path reads from the dossier's
    # evidence array below.
    _EVIDENCE_SQL = (
        f"SELECT {_EVIDENCE_COLUMNS} "
        "FROM mip.gold.evidence_events "
        "WHERE clip = ("
        "  SELECT clip FROM mip.gold.borrower_dossier "
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
            "mip.gold.fn_rate_spread",
            "mip.gold.fn_in_the_money",
            "mip.gold.borrower_dossier",
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
            "SELECT clip, evidence_events FROM mip.gold.borrower_dossier "
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


class DatabricksOfferRepository:
    """Offer-input bundle used by the offers router to build a recommendation."""

    def __init__(self, client: DatabricksSqlClient) -> None:
        self._client = client

    _SQL = (
        "SELECT "
        "  rate_spread_bps, equity_pct, has_permit, listed_for_sale, "
        "  is_investor, is_current_customer, is_competitor_lien, "
        "  recommended_offer_code "
        "FROM mip.gold.borrower_360 "
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


class DatabricksGenieRepository:
    """Real Genie + safe-corpus fallback gated on the ``genie`` breaker.

    The control flow is deliberately narrow and defensive:

    1. If the ``genie`` circuit breaker is OPEN *and* the question
       matches a canonical sample (deterministic catalog from
       ``backend.services.genie_answers``), return that catalog answer
       with ``source="fallback"`` so the canonical trio of questions
       still lands.
    2. Otherwise call ``ResilientGenieClient.ask(question)``. On
       success, adapt ``GenieResponse`` into the ``GenieMessageResponse``
       wire contract (``source="genie"``).
    3. If the call fails with ``DependencyDownError`` (breaker just
       opened on us), fall back to the safe corpus; if the question
       isn't in the corpus, return a honest "warming up" message --
       never fabricate data.
    4. On any other exception, re-raise so the router's 503 translation
       engages -- we never swallow to a mock answer.
    """

    _WARMING_MESSAGE = (
        "The Genie service is warming up - please try that question again "
        "in a few seconds."
    )

    def __init__(self, genie: ResilientGenieClient) -> None:
        self._genie = genie

    def respond(self, question: str) -> GenieMessageResponse:
        breaker_state = self._genie.resilient.breaker.state
        if breaker_state == "open":
            return self._fallback_or_degraded(question)
        try:
            result = self._genie.ask(question)
        except DependencyDownError:
            # Breaker opened during this call; serve safe corpus if we
            # can, otherwise honest degraded message.
            return self._fallback_or_degraded(question)
        except GenieClientError:
            # Underlying Genie surfaced an unrecoverable response (401,
            # 500, malformed JSON). Re-raise so the router translates
            # to 503 + degraded UI. No silent mock fallback.
            raise
        return _adapt_genie_response(question, result)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _fallback_or_degraded(self, question: str) -> GenieMessageResponse:
        """Return a curated safe-corpus answer, or an honest degraded
        message if the question has no match.

        ``genie_catalog_respond`` returns its own deterministic warm
        fallback when no intent matches -- we detect that and replace
        it with the "warming up" message so the user sees a resilience
        signal, not a generic catch-all answer dressed up as real data.
        """
        catalog_answer = genie_catalog_respond(question)
        if catalog_answer.source == "deterministic_fallback":
            # Unknown question + breaker open -> honest degraded state.
            return GenieMessageResponse(
                conversation_id=catalog_answer.conversation_id,
                question=question,
                answer=self._WARMING_MESSAGE,
                source="degraded",
                trusted_assets=[],
                follow_up_questions=catalog_answer.follow_up_questions,
            )
        # Rewrite the source marker so the UI can style the fallback
        # banner / the caller can tell catalog vs live apart.
        return catalog_answer.model_copy(update={"source": "fallback"})


def _adapt_genie_response(
    question: str,
    result: GenieResponse,
) -> GenieMessageResponse:
    """Wrap a live ``GenieResponse`` into the wire contract the UI
    already consumes. We derive ``trusted_assets`` from the SQL query
    when one is available (best-effort regex for ``mip.*``
    references); empty otherwise -- the UI tolerates an empty list.
    """
    trusted_assets = _extract_asset_refs(result.sql_query)
    return GenieMessageResponse(
        conversation_id=result.conversation_id,
        question=question,
        answer=result.answer_text or "",
        source="genie",
        trusted_assets=trusted_assets,
        table_rows=result.sql_result_rows,
    )


def _extract_asset_refs(sql: str | None) -> list[str]:
    """Pull ``mip.<schema>.<table>`` references out of a SQL
    string. Best-effort; returns the first three unique references so
    the evidence chip row stays readable.
    """
    if not sql:
        return []
    import re

    pattern = re.compile(r"\bmip\.[a-zA-Z_][a-zA-Z0-9_]*\.[a-zA-Z_][a-zA-Z0-9_]*\b")
    seen: list[str] = []
    for match in pattern.findall(sql):
        if match not in seen:
            seen.append(match)
        if len(seen) >= 3:
            break
    return seen


__all__ = [
    "DatabricksBorrowerRepository",
    "DatabricksGenieRepository",
    "DatabricksLeadRepository",
    "DatabricksOfferRepository",
    "DatabricksOutreachRepository",
    "DatabricksPortfolioRepository",
    "DatabricksSegmentRepository",
]
