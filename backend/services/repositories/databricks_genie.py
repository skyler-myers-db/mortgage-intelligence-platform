"""Databricks-backed Genie repository and SQL trust-policy helpers."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

from backend.config.settings import settings
from backend.services.databricks_sql import DatabricksSqlClient, DatabricksSqlError
from backend.services.databricks_sql_helpers import qualify
from backend.services.genie_answers import (
    GenieMessageResponse,
    GenieProof,
    default_follow_up_questions,
)
from backend.services.genie_client import (
    GenieClientError,
    GenieResponse,
    ResilientGenieClient,
)
from backend.services.observability import emit
from backend.services.pii_redaction import _FORBIDDEN_OUTPUT_KEYS
from backend.services.repositories.databricks_genie_actions import (
    _borrower_ids_from_rows,  # noqa: F401 - compatibility re-export
    _portfolio_criteria_from_question,  # noqa: F401 - compatibility re-export
    _portfolio_criteria_from_sql,  # noqa: F401 - compatibility re-export
    _route_from_answer_rows,  # noqa: F401 - compatibility re-export
    _row_values,  # noqa: F401 - compatibility re-export
    _segment_codes_from_question,  # noqa: F401 - compatibility re-export
    _sql_hash,  # noqa: F401 - compatibility re-export
    _suggest_genie_actions,
    _total_matching_from_rows,
)
from backend.services.repositories.databricks_genie_canonical import (
    _CANONICAL_CASH_OUT_TOP_STATE_SQL,
    _CANONICAL_CURRENT_CUSTOMER_RETENTION_RISK_SQL,
    _CANONICAL_HELOC_TOP_ZIPS_SQL,
    _CANONICAL_ITM_COUNT_BY_CITY_SQL,
    _CANONICAL_ITM_COUNT_BY_STATE_SQL,
    _CANONICAL_ITM_COUNT_SQL,
    _CANONICAL_ITM_TOP_ZIPS_SQL,
    _CANONICAL_MSA_SCORE_SQL,
    _CANONICAL_RETENTION_COMPETITOR_LIEN_LIST_SQL,
    _canonical_cash_out_state_scope,
    _canonical_heloc_zip_scope,
    _canonical_in_the_money_count_scope,
    _canonical_itm_city_scope,
    _canonical_itm_zip_scope,
    _canonical_msa_score_scope,
    _current_footprint_label,
    _retention_competitor_lien_list_question,
    _retention_risk_question,
)
from backend.services.repositories.databricks_genie_numeric import (
    _numeric_claim_blocked_response,
    _unsupported_answer_numeric_claims,
)
from backend.services.repositories.databricks_genie_policy import (
    _extract_asset_refs,
)
from backend.services.repositories.databricks_genie_trust import (
    _TRUSTED_GENIE_ASSETS,  # noqa: F401 - compatibility re-export
    _bounded_genie_sql,  # noqa: F401 - compatibility re-export
    _build_genie_proof,
    _execute_trusted_genie_sql,
    _extract_filters,  # noqa: F401 - compatibility re-export
    _freshness_from_rows,  # noqa: F401 - compatibility re-export
    _genie_question_hash,
    _is_select_only,  # noqa: F401 - compatibility re-export
    _known_data_gaps,
    _known_data_gaps_for_result,
    _pending_feed_gaps_from_material,  # noqa: F401 - compatibility re-export
    _pending_feed_gaps_from_rows,  # noqa: F401 - compatibility re-export
    _source_readiness_only_assets,
    _sql_uses_stale_evidence_signal_enum,
    _trusted_genie_asset_names,  # noqa: F401 - compatibility re-export
    _trusted_sql_policy,
    _trusted_sql_policy_allowing_stale_evidence_enum,
    _trusted_sql_policy_core,  # noqa: F401 - compatibility re-export
)
from backend.services.repositories.databricks_genie_visualization import (
    _GENIE_IDENTIFIER_COLUMNS,  # noqa: F401 - compatibility re-export
    _dateish_columns,  # noqa: F401 - compatibility re-export
    _is_genie_identifier_column,  # noqa: F401 - compatibility re-export
    _label_column,  # noqa: F401 - compatibility re-export
    _numeric_columns,  # noqa: F401 - compatibility re-export
    _plan_genie_visualization,
    _row_columns,  # noqa: F401 - compatibility re-export
    _text_columns,  # noqa: F401 - compatibility re-export
    _value_column,  # noqa: F401 - compatibility re-export
)
from backend.services.resilience import DependencyDownError

log = logging.getLogger("backend.services.repositories.databricks_repo")


def _emit_genie_warning(event: str, *, exc: BaseException | None = None, **fields: Any) -> None:
    emit(
        log,
        event,
        level=logging.WARNING,
        dependency="warehouse",
        outcome="degraded",
        exc_type=type(exc).__name__ if exc is not None else None,
        exc_msg=str(exc)[:500] if exc is not None else None,
        **fields,
    )


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


def _sql_uses_impossible_retention_conjunction(question: str, sql_query: str | None) -> bool:
    if not sql_query or not _retention_risk_question(question):
        return False
    sql = re.sub(r"\s+", " ", sql_query.strip().lower())
    return bool(
        re.search(r"\bis_current_customer\s*=\s*(true|1)\b", sql)
        and re.search(r"\bis_competitor_lien\s*=\s*(true|1)\b", sql)
    )


def _trusted_sql_repair_prompt(question: str) -> str:
    catalog = settings.mip_default_catalog
    borrower_asset = qualify("gold", "borrower_360")
    return (
        "Regenerate the following Mortgage Intelligence Platform data question "
        "as a governed analytics answer. Produce a read-only SQL SELECT query "
        f"attachment over the trusted {catalog}.gold or {catalog}.semantics assets, execute "
        "it, return the result rows, and cite the source asset. Do not answer "
        "from narrative alone, do not use PII or protected-class criteria, and "
        f"do not use catalogs outside {catalog}. For current-customer retention or "
        "recapture-risk questions, use the retention risk signal already modeled "
        f"in {borrower_asset} (segment_codes contains 'retention' or "
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
    source_readiness_gap_disclosure = trusted_sql and _source_readiness_only_assets(trusted_assets)
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
        result.sql_query
        and _trusted_sql_policy_allowing_stale_evidence_enum(result.sql_query, trusted_assets)
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
    if text_contains_pii or lacks_trusted_proof or unsafe_live_sql or depends_on_pending_feeds:
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
    unsupported_numeric_claims = _unsupported_answer_numeric_claims(
        result.answer_text,
        rows,
        question,
    )
    if unsupported_numeric_claims:
        return _numeric_claim_blocked_response(
            conversation_id=result.conversation_id,
            message_id=result.message_id,
            elapsed_ms=result.elapsed_ms,
            question_hash=question_hash,
            question=question,
            trusted_assets=trusted_assets,
            reasoning_trace=result.thoughts,
        )
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
    borrower_asset = qualify("gold", "borrower_360")
    evidence_asset = qualify("gold", "evidence_events")
    lender_name = (settings.mip_lender_name or "configured lender").strip() or "configured lender"
    if _retention_competitor_lien_list_question(question):
        try:
            rows = (
                _redact_genie_rows(
                    sql_client.execute(_CANONICAL_RETENTION_COMPETITOR_LIEN_LIST_SQL)
                )
                or []
            )
        except DatabricksSqlError as exc:
            _emit_genie_warning("canonical_genie_retention_competitor_lien_failed", exc=exc)
            return None
        trusted_assets = [
            borrower_asset,
            evidence_asset,
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
                    f"{evidence_asset}."
                )
            else:
                answer = (
                    f"I found {shown_count:,} retention-list borrowers with competitor-lien "
                    "evidence in the last 30 days. The result uses the governed "
                    f"`competitor_lien` signal_type from {evidence_asset}."
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
            _emit_genie_warning("canonical_genie_retention_risk_failed", exc=exc)
            return None
        count = row.get("retention_risk_borrowers")
        try:
            count_int = int(count)
        except (TypeError, ValueError):
            _emit_genie_warning("canonical_genie_retention_risk_bad_count", value_type=type(count).__name__)
            return None
        rows = [
            {
                "retention_risk_borrowers": count_int,
                "refreshed_at": row.get("refreshed_at"),
            }
        ]
        trusted_assets = [borrower_asset]
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
            f"There are {count_int:,} current {lender_name} customers in the retention-risk "
            f"cohort. This uses the modeled retention signal in {borrower_asset} "
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
            _emit_genie_warning("canonical_genie_itm_zips_failed", exc=exc)
            return None
        rows = _redact_genie_rows(rows) or []
        trusted_assets = [borrower_asset]
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
                f"for refinance from {borrower_asset}. "
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
    if _canonical_heloc_zip_scope(question):
        try:
            rows = sql_client.execute(_CANONICAL_HELOC_TOP_ZIPS_SQL)
        except DatabricksSqlError as exc:
            _emit_genie_warning("canonical_genie_heloc_zips_failed", exc=exc)
            return None
        rows = _redact_genie_rows(rows) or []
        trusted_assets = [borrower_asset]
        question_hash = _genie_question_hash(question)
        proof = _build_genie_proof(
            sql_query=_CANONICAL_HELOC_TOP_ZIPS_SQL,
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
            sql_query=_CANONICAL_HELOC_TOP_ZIPS_SQL,
            source="trusted_sql",
        )
        if rows:
            top = rows[0]
            answer = (
                "I ranked ZIP codes by HELOC-eligible borrowers with equity_pct "
                f"at or above 35% from {borrower_asset}. "
                f"The current leader is ZIP {top.get('zip')} ({top.get('state')}) "
                f"with {int(top.get('heloc_eligible_borrowers') or 0):,} borrowers. "
                "This is an equity-only HELOC eligibility view; Building Permits "
                "signals remain pending and are not used as triggers here."
            )
        else:
            answer = (
                "The trusted borrower table returned no equity-only HELOC ZIP rows "
                "for the current refreshed data coverage. Building Permits signals "
                "remain pending and are not treated as zero demand."
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
            sql_query=_CANONICAL_HELOC_TOP_ZIPS_SQL,
            row_count=len(rows),
            proof=proof,
            visualization=visualization,
            actions=actions,
            table_rows=rows,
        )
    if _canonical_cash_out_state_scope(question):
        try:
            rows = sql_client.execute(_CANONICAL_CASH_OUT_TOP_STATE_SQL)
        except DatabricksSqlError as exc:
            _emit_genie_warning("canonical_genie_cash_out_state_failed", exc=exc)
            return None
        rows = _redact_genie_rows(rows) or []
        trusted_assets = [borrower_asset]
        question_hash = _genie_question_hash(question)
        proof = _build_genie_proof(
            sql_query=_CANONICAL_CASH_OUT_TOP_STATE_SQL,
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
            sql_query=_CANONICAL_CASH_OUT_TOP_STATE_SQL,
            source="trusted_sql",
        )
        if rows:
            top = rows[0]
            count_int = int(top.get("cash_out_borrowers") or 0)
            answer = (
                f"{top.get('state')} has the most cash-out opportunity right now "
                f"with {count_int:,} borrowers. This uses recommended_offer_code = "
                f"'cash_out' at the unique borrower grain from {borrower_asset}."
            )
            metric_value = f"{count_int:,}"
        else:
            answer = (
                "The trusted borrower table returned no cash-out state rows for "
                "the current refreshed data coverage."
            )
            metric_value = None
        return GenieMessageResponse(
            conversation_id=result.conversation_id,
            message_id=result.message_id,
            elapsed_ms=result.elapsed_ms,
            question_hash=question_hash,
            question=question,
            answer=answer,
            source="trusted_sql",
            trusted_assets=trusted_assets,
            sql_query=_CANONICAL_CASH_OUT_TOP_STATE_SQL,
            row_count=len(rows),
            proof=proof,
            visualization=visualization,
            actions=actions,
            metric_value=metric_value,
            table_rows=rows,
        )
    if _canonical_msa_score_scope(question):
        try:
            rows = sql_client.execute(_CANONICAL_MSA_SCORE_SQL)
        except DatabricksSqlError as exc:
            _emit_genie_warning("canonical_genie_msa_score_failed", exc=exc)
            return None
        rows = _redact_genie_rows(rows) or []
        trusted_assets = [borrower_asset]
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
                f"mean lead score at the unique borrower grain from {borrower_asset}."
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
            row = (
                sql_client.execute_one(
                    _CANONICAL_ITM_COUNT_BY_CITY_SQL,
                    {"city": city_scope},
                )
                or {}
            )
        except DatabricksSqlError as exc:
            _emit_genie_warning("canonical_genie_metric_failed", exc=exc)
            return None
        count = row.get("in_the_money_borrowers")
        try:
            count_int = int(count)
        except (TypeError, ValueError):
            _emit_genie_warning("canonical_genie_metric_bad_count", value_type=type(count).__name__)
            return None
        rows = [
            {
                "city": city_scope,
                "in_the_money_borrowers": count_int,
                "refreshed_at": row.get("refreshed_at"),
            }
        ]
        trusted_assets = [borrower_asset]
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
            f"This is a city-scoped unique borrower count from {borrower_asset}; "
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
        _emit_genie_warning("canonical_genie_metric_failed", exc=exc)
        return None
    count = row.get("in_the_money_borrowers")
    try:
        count_int = int(count)
    except (TypeError, ValueError):
        _emit_genie_warning("canonical_genie_metric_bad_count", value_type=type(count).__name__)
        return None

    rows = [{"in_the_money_borrowers": count_int, "refreshed_at": row.get("refreshed_at")}]
    if state_scope:
        rows[0]["state"] = state_scope[1]
    trusted_assets = [borrower_asset]
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
        f"This is a unique borrower count from {borrower_asset} at the "
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
_GENIE_PII_KEYS: frozenset[str] = frozenset(
    {
        *_FORBIDDEN_OUTPUT_KEYS,
        "owner_name",
        "owner_names",
        "owner_full_name",
        "primary_owner",
        "owner_name_hash",  # hashed, but still a stable identifier — not exported
        "owner_link_id",  # raw Cotality identifier — replaced with a display surrogate elsewhere
        "clip",  # raw CLIP — evidence drawer surfaces a short form only
        "raw_clip",
        "street_address",
        "site_address",
        "mailing_address",
        "tax_mailing_address",
        "subject_property",  # carries synthesized city + ZIP; synthesized upstream, but redacted here too
        "owner_email",
        "borrower_email",
        "email",
        "phone",
        "phone_number",
        "ssn",
    }
)


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
            {k: v for k, v in row.items() if _normalise_genie_key(str(k)) not in _GENIE_PII_KEYS}
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
