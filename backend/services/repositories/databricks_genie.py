"""Databricks-backed Genie repository and SQL trust-policy helpers."""

from __future__ import annotations

import re
from datetime import UTC, datetime

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
    _CANONICAL_LISTED_PURCHASE_TOP_SQL,
    _CANONICAL_MSA_SCORE_SQL,
    _CANONICAL_RETENTION_COMPETITOR_LIEN_LIST_BY_STATE_SQL,
    _CANONICAL_RETENTION_COMPETITOR_LIEN_LIST_SQL,
    _CANONICAL_RETENTION_ELIGIBILITY_SUMMARY_BY_STATE_SQL,
    _CANONICAL_RETENTION_ELIGIBILITY_SUMMARY_GLOBAL_SQL,
    _CANONICAL_TOP_BORROWERS_BY_STATE_INTENT_SQL,
    _CANONICAL_TOP_BORROWERS_BY_STATE_SQL,
    _CANONICAL_TOP_BORROWERS_GLOBAL_INTENT_SQL,
    _canonical_cash_out_state_scope,
    _canonical_heloc_zip_scope,
    _canonical_in_the_money_count_scope,
    _canonical_itm_city_scope,
    _canonical_itm_state_scope,
    _canonical_itm_zip_scope,
    _canonical_listed_purchase_scope,
    _canonical_msa_score_scope,
    _canonical_specific_top_borrowers_global_scope,
    _canonical_specific_top_borrowers_state_scope,
    _canonical_top_borrowers_state_scope,
    _current_footprint_label,
    _retention_competitor_lien_list_question,
    _retention_risk_question,
    _specific_top_borrower_intent_label,
    _specific_top_borrower_intent_note,
    _specific_top_borrower_sort_label,
)
from backend.services.repositories.databricks_genie_direct import (
    direct_canonical_response,
)
from backend.services.repositories.databricks_genie_numeric import (
    _numeric_claim_blocked_response,
    _unsupported_answer_numeric_claims,
)
from backend.services.repositories.databricks_genie_policy import (
    _extract_asset_refs,
)
from backend.services.repositories.databricks_genie_policy_helpers import (
    _GENIE_PII_KEYS,  # noqa: F401 - compatibility re-export
    _PII_TEXT_PATTERNS,  # noqa: F401 - compatibility re-export
    _answer_text_contains_pii,
    _emit_genie_warning,
    _genie_response_has_query_proof,
    _likely_data_question,  # noqa: F401 - compatibility re-export
    _merge_trusted_assets,
    _needs_genie_sql_repair,
    _normalise_genie_key,  # noqa: F401 - compatibility re-export
    _redact_genie_rows,
    _sql_uses_impossible_retention_conjunction,  # noqa: F401 - compatibility re-export
    _trusted_sql_repair_prompt,
)
from backend.services.repositories.databricks_genie_strategy import (
    _canonical_strategy_board_answer,
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
    _sql_uses_stale_evidence_signal_enum,  # noqa: F401 - compatibility re-export
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
from backend.services.scoring import offer_display_label

_SOURCE_LINE_RE = re.compile(
    r"(?im)^\s*source\s*:\s*`?[A-Za-z_][\w-]*\.[A-Za-z_]\w*\.[A-Za-z_]\w*`?\.?\s*$"
)


class DatabricksGenieRepository:
    """Real Genie, then an honest warming-up message when the breaker opens.

    No path fabricates data. Unavailable Genie returns a degraded message only;
    data-bearing answers require live Genie output or executed trusted SQL proof.
    Unexpected Genie failures re-raise so the router returns the dependency-down
    response rather than silently replaying analytic content.
    """

    _CONNECTING_MESSAGE = (
        "Genie is connecting to the live Mortgage Lead Intelligence space. "
        "Try again in a few seconds once the connection is ready. No data was "
        "generated for this question."
    )
    _BREAKER_OPEN_MESSAGE = (
        "Genie is temporarily unavailable because the answer-path circuit "
        "breaker is open after recent failures. Wait for the cooldown or check "
        "health before retrying. No data was generated for this question."
    )
    _RETRIES_EXHAUSTED_MESSAGE = (
        "Genie could not return a governed answer after exhausting the retry "
        "budget. Check the Databricks Genie connection and retry after health "
        "recovers. No data was generated for this question."
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
        direct_canonical = direct_canonical_response(question, self._sql_client)
        if direct_canonical is not None:
            return direct_canonical
        breaker_state = self._genie.resilient.breaker.state
        if breaker_state == "open":
            return self._degraded(
                question,
                kind=DependencyDownError.KIND_BREAKER_OPEN,
            )
        try:
            result = self._genie.ask(question, conversation_id=conversation_id)
            if _needs_genie_sql_repair(question, result):
                result = self._repair_text_only_genie_answer(
                    question=question,
                    original=result,
                    conversation_id=conversation_id,
                )
        except DependencyDownError as exc:
            return self._degraded(question, kind=exc.kind)
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

    def _degraded(
        self,
        question: str,
        *,
        kind: str = DependencyDownError.KIND_WARMING_UP,
    ) -> GenieMessageResponse:
        """Honest dependency-down message with no fabricated content.

        Prompt suggestions are questions only. The degraded path does not
        inspect local analytics and cannot return rows, counts, or
        borrower examples.
        """
        if kind == DependencyDownError.KIND_BREAKER_OPEN:
            answer = self._BREAKER_OPEN_MESSAGE
        elif kind == DependencyDownError.KIND_RETRIES_EXHAUSTED:
            answer = self._RETRIES_EXHAUSTED_MESSAGE
        else:
            answer = self._CONNECTING_MESSAGE
        return GenieMessageResponse(
            conversation_id="",
            question=question,
            answer=answer,
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
            trusted_assets=[],
            rows=[],
            question=question,
            conversation_id=result.conversation_id,
            message_id=result.message_id,
            elapsed_ms=result.elapsed_ms,
            reasoning_trace=[],
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
            trusted_assets=[],
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
            reasoning_trace=[],
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
        answer=_ensure_answer_cites_source(result.answer_text, trusted_assets),
        source="genie",
        trusted_assets=trusted_assets,
        sql_query=result.sql_query,
        row_count=len(rows) if rows else 0,
        proof=proof,
        visualization=visualization,
        actions=actions,
        table_rows=rows,
    )


def _ensure_answer_cites_source(answer: str | None, trusted_assets: list[str]) -> str:
    """Append a source line when trusted live Genie text omits one."""

    text = (answer or "").strip()
    if not trusted_assets or _SOURCE_LINE_RE.search(text):
        return text
    source = trusted_assets[0]
    if not text:
        return f"Source: {source}"
    return f"{text}\n\nSource: {source}"


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
    lead_population_asset = qualify("gold", "lead_population")
    evidence_asset = qualify("gold", "evidence_events")
    lender_name = (settings.mip_lender_name or "configured lender").strip() or "configured lender"
    strategy_answer = _canonical_strategy_board_answer(
        question=question,
        result=result,
        sql_client=sql_client,
        borrower_asset=borrower_asset,
    )
    if strategy_answer is not None:
        return strategy_answer
    specific_top_borrowers_state_scope = _canonical_specific_top_borrowers_state_scope(question)
    if specific_top_borrowers_state_scope is not None:
        intent, state_name, state_code = specific_top_borrowers_state_scope
        sql_query = _CANONICAL_TOP_BORROWERS_BY_STATE_INTENT_SQL[intent]
        intent_label = _specific_top_borrower_intent_label(intent)
        sort_label = _specific_top_borrower_sort_label(intent)
        try:
            rows = sql_client.execute(sql_query, {"state": state_code})
        except DatabricksSqlError as exc:
            _emit_genie_warning(
                "canonical_genie_specific_top_borrowers_state_failed",
                intent=intent,
                exc=exc,
            )
            return None
        rows = _redact_genie_rows(rows) or []
        response_sql_query = sql_query
        response_rows = rows
        suppress_actions = False
        metric_value = None
        if not rows and intent == "retention":
            try:
                summary_rows = (
                    _redact_genie_rows(
                        sql_client.execute(
                            _CANONICAL_RETENTION_ELIGIBILITY_SUMMARY_BY_STATE_SQL,
                            {"state": state_code},
                        )
                    )
                    or []
                )
            except DatabricksSqlError as exc:
                _emit_genie_warning(
                    "canonical_genie_retention_eligibility_summary_state_failed",
                    exc=exc,
                )
                summary_rows = []
            if summary_rows:
                response_sql_query = _CANONICAL_RETENTION_ELIGIBILITY_SUMMARY_BY_STATE_SQL
                response_rows = summary_rows
                suppress_actions = True
                metric_value = f"{int(summary_rows[0].get('action_ready_retention_borrowers') or 0):,}"
        trusted_assets = [borrower_asset]
        question_hash = _genie_question_hash(question)
        proof = _build_genie_proof(
            sql_query=response_sql_query,
            trusted_assets=trusted_assets,
            rows=response_rows,
            question=question,
            conversation_id=result.conversation_id,
            message_id=result.message_id,
            elapsed_ms=result.elapsed_ms,
        )
        visualization = _plan_genie_visualization(question, response_rows)
        actions = [] if suppress_actions else _suggest_genie_actions(
            question=question,
            rows=response_rows,
            trusted_assets=trusted_assets,
            visualization=visualization,
            conversation_id=result.conversation_id,
            message_id=result.message_id,
            question_hash=question_hash,
            sql_query=response_sql_query,
            source="trusted_sql",
        )
        if rows:
            top = rows[0]
            intent_note = _specific_top_borrower_intent_note(question, intent)
            answer = (
                f"I ranked the top {len(rows)} {state_name} ({state_code}) "
                f"{intent_label} borrowers from {borrower_asset}, ordered by "
                f"{sort_label}. The current first borrower is masked "
                f"{top.get('borrower_id')} with opportunity score "
                f"{int(top.get('opportunity_score') or 0):,}.{intent_note}"
            )
        elif response_rows and intent == "retention":
            summary = response_rows[0]
            retention_count = int(summary.get("retention_segment_borrowers") or 0)
            marketing_count = int(summary.get("marketing_eligible_retention_borrowers") or 0)
            action_ready_count = int(summary.get("action_ready_retention_borrowers") or 0)
            answer = (
                f"{state_name} ({state_code}) has {retention_count:,} borrowers in the "
                "Retention Risk segment, but none qualify for the action-ready best-retention "
                f"queue after marketing-eligibility and opt-in consent filters "
                f"({marketing_count:,} marketing-eligible; {action_ready_count:,} opt-in). "
                "Competitor-lien evidence questions use a separate evidence workflow and may "
                "return borrowers that are not action-ready for outreach."
            )
        else:
            answer = (
                f"The trusted borrower table returned no marketing-eligible "
                f"{intent_label} borrowers in {state_name} ({state_code}) for "
                "the current refreshed coverage."
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
            sql_query=response_sql_query,
            row_count=len(response_rows),
            proof=proof,
            visualization=visualization,
            actions=actions,
            metric_value=metric_value,
            table_rows=response_rows,
        )
    specific_top_borrowers_global_scope = _canonical_specific_top_borrowers_global_scope(question)
    if specific_top_borrowers_global_scope is not None:
        intent = specific_top_borrowers_global_scope
        sql_query = _CANONICAL_TOP_BORROWERS_GLOBAL_INTENT_SQL[intent]
        intent_label = _specific_top_borrower_intent_label(intent)
        sort_label = _specific_top_borrower_sort_label(intent)
        try:
            rows = sql_client.execute(sql_query)
        except DatabricksSqlError as exc:
            _emit_genie_warning(
                "canonical_genie_specific_top_borrowers_global_failed",
                intent=intent,
                exc=exc,
            )
            return None
        rows = _redact_genie_rows(rows) or []
        response_sql_query = sql_query
        response_rows = rows
        suppress_actions = False
        metric_value = None
        if not rows and intent == "retention":
            try:
                summary_rows = (
                    _redact_genie_rows(
                        sql_client.execute(_CANONICAL_RETENTION_ELIGIBILITY_SUMMARY_GLOBAL_SQL)
                    )
                    or []
                )
            except DatabricksSqlError as exc:
                _emit_genie_warning(
                    "canonical_genie_retention_eligibility_summary_global_failed",
                    exc=exc,
                )
                summary_rows = []
            if summary_rows:
                response_sql_query = _CANONICAL_RETENTION_ELIGIBILITY_SUMMARY_GLOBAL_SQL
                response_rows = summary_rows
                suppress_actions = True
                metric_value = f"{int(summary_rows[0].get('action_ready_retention_borrowers') or 0):,}"
        trusted_assets = [borrower_asset]
        question_hash = _genie_question_hash(question)
        proof = _build_genie_proof(
            sql_query=response_sql_query,
            trusted_assets=trusted_assets,
            rows=response_rows,
            question=question,
            conversation_id=result.conversation_id,
            message_id=result.message_id,
            elapsed_ms=result.elapsed_ms,
        )
        visualization = _plan_genie_visualization(question, response_rows)
        actions = [] if suppress_actions else _suggest_genie_actions(
            question=question,
            rows=response_rows,
            trusted_assets=trusted_assets,
            visualization=visualization,
            conversation_id=result.conversation_id,
            message_id=result.message_id,
            question_hash=question_hash,
            sql_query=response_sql_query,
            source="trusted_sql",
        )
        if rows:
            top = rows[0]
            intent_note = _specific_top_borrower_intent_note(question, intent)
            answer = (
                f"I ranked the top {len(rows)} {intent_label} borrowers across the "
                f"current refreshed coverage from {borrower_asset}, ordered by "
                f"{sort_label}. The current first borrower is masked "
                f"{top.get('borrower_id')} with opportunity score "
                f"{int(top.get('opportunity_score') or 0):,}.{intent_note}"
            )
        elif response_rows and intent == "retention":
            summary = response_rows[0]
            retention_count = int(summary.get("retention_segment_borrowers") or 0)
            marketing_count = int(summary.get("marketing_eligible_retention_borrowers") or 0)
            action_ready_count = int(summary.get("action_ready_retention_borrowers") or 0)
            answer = (
                f"The current coverage has {retention_count:,} borrowers in the Retention "
                "Risk segment, but none qualify for the action-ready best-retention queue "
                f"after marketing-eligibility and opt-in consent filters "
                f"({marketing_count:,} marketing-eligible; {action_ready_count:,} opt-in). "
                "Competitor-lien evidence questions use a separate evidence workflow and may "
                "return borrowers that are not action-ready for outreach."
            )
        else:
            answer = (
                f"The trusted borrower table returned no marketing-eligible "
                f"{intent_label} borrowers for the current refreshed coverage."
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
            sql_query=response_sql_query,
            row_count=len(response_rows),
            proof=proof,
            visualization=visualization,
            actions=actions,
            metric_value=metric_value,
            table_rows=response_rows,
        )
    top_borrower_state_scope = _canonical_top_borrowers_state_scope(question)
    if top_borrower_state_scope is not None:
        state_name, state_code = top_borrower_state_scope
        try:
            rows = sql_client.execute(
                _CANONICAL_TOP_BORROWERS_BY_STATE_SQL,
                {"state": state_code},
            )
        except DatabricksSqlError as exc:
            _emit_genie_warning("canonical_genie_top_borrowers_state_failed", exc=exc)
            return None
        rows = _redact_genie_rows(rows) or []
        trusted_assets = [lead_population_asset]
        question_hash = _genie_question_hash(question)
        proof = _build_genie_proof(
            sql_query=_CANONICAL_TOP_BORROWERS_BY_STATE_SQL,
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
            sql_query=_CANONICAL_TOP_BORROWERS_BY_STATE_SQL,
            source="trusted_sql",
        )
        if rows:
            top = rows[0]
            answer = (
                f"I ranked the top {len(rows)} {state_name} ({state_code}) borrowers "
                f"by lead score from {lead_population_asset}. "
                f"The current leader is masked borrower {top.get('borrower_id')} "
                f"with lead score {int(top.get('lead_score') or 0):,}."
            )
        else:
            answer = (
                f"The trusted lead population returned no {state_name} ({state_code}) "
                "borrowers for the current refreshed data coverage."
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
            sql_query=_CANONICAL_TOP_BORROWERS_BY_STATE_SQL,
            row_count=len(rows),
            proof=proof,
            visualization=visualization,
            actions=actions,
            table_rows=rows,
        )
    if _retention_competitor_lien_list_question(question):
        state_scope = _canonical_itm_state_scope(question)
        sql_query = (
            _CANONICAL_RETENTION_COMPETITOR_LIEN_LIST_BY_STATE_SQL
            if state_scope is not None
            else _CANONICAL_RETENTION_COMPETITOR_LIEN_LIST_SQL
        )
        parameters = {"state": state_scope[1]} if state_scope is not None else None
        scope_phrase = f" in {state_scope[0]}" if state_scope is not None else ""
        try:
            rows = (
                _redact_genie_rows(
                    sql_client.execute(sql_query, parameters)
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
        total_matching = _total_matching_from_rows(rows)
        shown_count = len(rows)
        if rows:
            if total_matching > shown_count:
                answer = (
                    f"There are {total_matching:,} retention-list borrowers{scope_phrase} with "
                    f"competitor-lien evidence in the last 30 days; showing the first "
                    f"{shown_count:,} by latest evidence timestamp and opportunity score. "
                    "The result uses the governed `competitor_lien` signal_type from "
                    f"{evidence_asset}."
                )
            else:
                answer = (
                    f"I found {shown_count:,} retention-list borrowers{scope_phrase} with competitor-lien "
                    "evidence in the last 30 days. The result uses the governed "
                    f"`competitor_lien` signal_type from {evidence_asset}."
                )
        else:
            answer = (
                f"No retention-list borrowers{scope_phrase} have governed competitor-lien evidence "
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
            sql_query=sql_query,
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
                "I ranked ZIP codes by unique borrowers passing the refinance-economics screen "
                f"from {borrower_asset}. "
                f"The current leader is ZIP {top.get('zip')} ({top.get('state')}) "
                f"with {int(top.get('in_the_money_borrowers') or 0):,} borrowers; "
                "the cohort action below carries these ZIP filters into Lead Queue."
            )
        else:
            answer = (
                "The ranked lead population returned no refinance-economics ZIP rows for "
                "the current refreshed, marketing-eligible coverage."
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
                "I ranked ZIP codes by borrowers with modeled equity at or above "
                f"35% from {borrower_asset}. "
                f"The current leader is ZIP {top.get('zip')} ({top.get('state')}) "
                f"with {int(top.get('equity_capacity_borrowers') or 0):,} borrowers. "
                "This is an equity-capacity view, not a filed-permit or HELOC-intent "
                "count; Building Permits are only used when that source is live."
            )
        else:
            answer = (
                "The trusted borrower table returned no ZIP rows with modeled "
                "equity at or above 35% for the current refreshed data coverage. "
                "Building Permits signals remain pending and are not treated as "
                "zero demand."
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
                f"with {count_int:,} borrowers. This counts borrowers whose "
                f"primary offer is a cash-out refinance review at the unique "
                f"borrower grain from {borrower_asset}."
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
    if _canonical_listed_purchase_scope(question):
        try:
            rows = sql_client.execute(_CANONICAL_LISTED_PURCHASE_TOP_SQL)
        except DatabricksSqlError as exc:
            _emit_genie_warning("canonical_genie_listed_purchase_failed", exc=exc)
            return None
        rows = _redact_genie_rows(rows) or []
        trusted_assets = [borrower_asset]
        question_hash = _genie_question_hash(question)
        proof = _build_genie_proof(
            sql_query=_CANONICAL_LISTED_PURCHASE_TOP_SQL,
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
            sql_query=_CANONICAL_LISTED_PURCHASE_TOP_SQL,
            source="trusted_sql",
        )
        if rows:
            top = rows[0]
            top_offer = offer_display_label(
                str(top.get("recommended_offer_code") or ""),
                str(top.get("recommended_offer") or ""),
            )
            answer = (
                f"I ranked the top {len(rows)} marketing-eligible listed-for-sale borrowers "
                f"from {borrower_asset}. The current first borrower is masked "
                f"{top.get('borrower_id')} in {top.get('city')}, {top.get('state')} "
                f"with opportunity score {int(top.get('opportunity_score') or 0):,}. "
                f"Lead with {top_offer} only after review in the governed outreach workflow."
            )
        else:
            answer = (
                "The trusted borrower table returned no marketing-eligible listed-for-sale "
                "borrowers for the current refreshed coverage."
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
            sql_query=_CANONICAL_LISTED_PURCHASE_TOP_SQL,
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
            f"There are {count_int:,} borrowers passing the refinance-economics screen in {city_scope} "
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
        f"There are {count_int:,} borrowers passing the refinance-economics screen{geo_text}. "
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
