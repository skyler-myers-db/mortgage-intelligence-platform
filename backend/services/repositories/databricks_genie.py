"""Databricks-backed Genie repository and SQL trust-policy helpers."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, NamedTuple

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
    _CANONICAL_TOP_BORROWERS_ALL_SEGMENTS_SQL,
    _CANONICAL_TOP_BORROWERS_BY_STATE_INTENT_SQL,
    _CANONICAL_TOP_BORROWERS_BY_STATE_SQL,
    _CANONICAL_TOP_BORROWERS_GLOBAL_INTENT_SQL,
    _CANONICAL_TOP_BORROWERS_GLOBAL_SQL,
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
    _canonical_top_borrowers_all_segments_scope,
    _canonical_top_borrowers_global_scope,
    _canonical_top_borrowers_state_scope,
    _current_footprint_label,
    _retention_competitor_lien_list_question,
    _retention_eligibility_fallback_from_summary,
    _retention_risk_question,
    _specific_top_borrower_intent_label,
    _specific_top_borrower_intent_note,
    _specific_top_borrower_sort_label,
    compose_all_segments_brief,
    compose_cohort_ranking_brief,
)
from backend.services.repositories.databricks_genie_direct import (
    direct_canonical_response,
)
from backend.services.repositories.databricks_genie_numeric import (
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
    genie_follow_up_questions,
    genie_native_visualization,
    genie_reasoning_trace_from_thoughts,
)
from backend.services.repositories.databricks_genie_strategy import (
    _canonical_strategy_board_answer,
)
from backend.services.repositories.databricks_genie_sweep import (
    is_full_analysis_question,
    run_full_analysis_sweep,
)
from backend.services.repositories.databricks_genie_trace import (
    WITHHELD_CONTRADICTED,
    WITHHELD_NO_NARRATIVE,
    WITHHELD_NO_NARRATIVE_DETERMINISTIC,
    WITHHELD_UNSAFE_TEXT,
    WITHHELD_UNVERIFIED_NUMBERS,
    GenieProcessTrace,
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

_SOURCE_LINE_RE = re.compile(
    r"(?im)^\s*source\s*:\s*`?[A-Za-z_][\w-]*\.[A-Za-z_]\w*\.[A-Za-z_]\w*`?\.?\s*$"
)

# Honest degraded-mode disclosure appended to a reviewed canonical fallback
# answer when live Genie is unavailable. It is added to the proof's
# known_data_gaps and prefixed to the answer text so the fallback is never
# presented as a live Genie turn. The wording follows the existing degraded
# conventions (name the dependency, name the substitute) without the
# "No data was generated" clause, because the fallback DID execute governed SQL.
_DEGRADED_FALLBACK_DISCLOSURE = (
    "Live Genie is temporarily unavailable, so this answer comes from the "
    "reviewed deterministic fallback (governed asset-scoped SQL) rather than a "
    "live Genie turn."
)


def _annotate_degraded_fallback(
    response: GenieMessageResponse,
    *,
    kind: str,
) -> GenieMessageResponse:
    """Stamp the honest degraded disclosure onto a canonical fallback answer.

    The reviewed canonical answers keep their ``trusted_sql`` source (reviewed,
    executed SQL over gold assets) but must disclose that they stood in for an
    unavailable live Genie turn. We prepend the disclosure to the answer text
    and add it to ``proof.known_data_gaps`` so no surface presents the fallback
    as live. The circuit-breaker ``kind`` is accepted for symmetry with the
    honest-message path but does not change the disclosure wording.

    Only data-bearing ``trusted_sql`` answers are stamped. Guide and source-gap
    fallbacks make no live-data claim (they carry no SQL, rows, or counts), so
    the SQL-specific disclosure would misdescribe them; they are returned as-is.
    """
    _ = kind
    if response.source != "trusted_sql":
        return response
    updates: dict[str, object] = {}
    answer = response.answer or ""
    if _DEGRADED_FALLBACK_DISCLOSURE not in answer:
        updates["answer"] = (
            f"{_DEGRADED_FALLBACK_DISCLOSURE}\n\n{answer}" if answer
            else _DEGRADED_FALLBACK_DISCLOSURE
        )
    proof = response.proof
    if proof is not None:
        gaps = list(proof.known_data_gaps)
        if _DEGRADED_FALLBACK_DISCLOSURE not in gaps:
            gaps = [_DEGRADED_FALLBACK_DISCLOSURE, *gaps]
            updates["proof"] = proof.model_copy(update={"known_data_gaps": gaps})
    if not updates:
        return response
    return response.model_copy(update=updates)


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
        *,
        allow_sweep: bool = True,
    ) -> GenieMessageResponse:
        # Product posture (mip_genie_live_first=True): LIVE Genie is the primary
        # answer path for every guardrail-passing question so the answer is
        # genuinely generated rather than replayed from a hand-authored catalog.
        # The reviewed deterministic canonical answers are demoted to an honest
        # degraded-mode fallback consulted only inside `_degraded` (breaker open
        # or a dependency-down live turn). Legacy/emergency posture
        # (mip_genie_live_first=False) restores interceptor-first ordering for
        # offline or rate-limited booth operation.
        if not settings.mip_genie_live_first:
            direct_canonical = direct_canonical_response(question, self._sql_client)
            if direct_canonical is not None:
                return direct_canonical
        breaker_state = self._genie.resilient.breaker.state
        if breaker_state == "open":
            return self._degraded(
                question,
                kind=DependencyDownError.KIND_BREAKER_OPEN,
            )
        if allow_sweep and is_full_analysis_question(question):
            # "Analyze everything" cannot be one SQL statement. Decompose into
            # themed LIVE Genie analyses (each through the full policy
            # pipeline) and assemble the verified results; fall through to the
            # normal single-turn pipeline when the sweep cannot stand one up.
            sweep = run_full_analysis_sweep(self, question)
            if sweep is not None:
                return sweep
        repaired = False
        try:
            result = self._genie.ask(question, conversation_id=conversation_id)
            if _needs_genie_sql_repair(question, result):
                regenerated = self._repair_text_only_genie_answer(
                    question=question,
                    original=result,
                    conversation_id=conversation_id,
                )
                # The repair helper returns the ORIGINAL object when the retry
                # did not recover governed SQL proof, so identity is the honest
                # signal for "the repair actually changed this turn".
                repaired = regenerated is not result
                result = regenerated
        except DependencyDownError as exc:
            return self._degraded(question, kind=exc.kind)
        except GenieClientError:
            # Underlying Genie surfaced an unrecoverable response (401,
            # 500, malformed JSON). Re-raise so the router translates
            # to 503 + degraded UI. No silent mock fallback.
            raise
        return _adapt_genie_response(
            question,
            result,
            sql_client=self._sql_client,
            repaired=repaired,
        )

    def respond_existing(
        self,
        question: str,
        *,
        conversation_id: str,
        message_id: str,
    ) -> GenieMessageResponse:
        """Complete an already-submitted live message (async lifecycle).

        Applies :meth:`respond`'s LIVE-path policy pipeline — breaker
        handling, text-only SQL repair, adaptation, degraded fallback — to a
        message the submit endpoint already created. The legacy
        interceptor-first posture (``mip_genie_live_first=False``) is
        deliberately absent here: under that posture the submit endpoint
        resolves the whole turn through :meth:`respond` and never creates a
        live message, so no turn reaches this method. ``question`` is the
        token-verified prompt of record for repair/adaptation/audit; it was
        prompt-guarded at submit before the message existed.
        """
        breaker_state = self._genie.resilient.breaker.state
        if breaker_state == "open":
            return self._degraded(question, kind=DependencyDownError.KIND_BREAKER_OPEN)
        repaired = False
        try:
            result = self._genie.resume_message(conversation_id, message_id)
            if _needs_genie_sql_repair(question, result):
                regenerated = self._repair_text_only_genie_answer(
                    question=question,
                    original=result,
                    conversation_id=conversation_id,
                )
                repaired = regenerated is not result
                result = regenerated
        except DependencyDownError as exc:
            return self._degraded(question, kind=exc.kind)
        except GenieClientError:
            raise
        return _adapt_genie_response(
            question,
            result,
            sql_client=self._sql_client,
            repaired=repaired,
        )

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
        """Degraded gateway: reviewed deterministic fallback, else honest message.

        In the product posture (``mip_genie_live_first``) live Genie is primary,
        so when it is unavailable this path tries the reviewed canonical
        trusted-SQL answers as an honest fallback. The fallback answer keeps its
        asset-scoped ``trusted_sql`` source (its SQL is reviewed and executed
        against gold tables) but is annotated with an explicit disclosure gap so
        it is never presented as a live Genie answer. When the canonical layer
        cannot answer -- or in the legacy interceptor-first posture, where it was
        already consulted before the live turn -- we fall back to the honest
        dependency-down message with no fabricated content. Prompt suggestions
        are questions only; the honest message returns no rows, counts, or
        borrower examples.
        """
        if settings.mip_genie_live_first:
            fallback = direct_canonical_response(question, self._sql_client)
            if fallback is not None:
                return _annotate_degraded_fallback(fallback, kind=kind)
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
    repaired: bool = False,
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

    ``repaired`` records that the narrative-only SQL repair retry actually
    changed this turn, so the deterministic process trace can state that step
    as fact instead of inferring it.
    """
    trace = GenieProcessTrace()
    trace.guardrails()
    if repaired:
        trace.repair()
    trusted_assets = _merge_trusted_assets(
        _extract_asset_refs(result.sql_query),
        result.trusted_assets,
    )
    rows = _redact_genie_rows(result.sql_result_rows)
    trusted_sql = _trusted_sql_policy(result.sql_query, trusted_assets)
    trace.live_turn(sql_query=result.sql_query, assets=trusted_assets)
    if trusted_sql:
        trace.trust()
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
    # Live-first posture (2026-08-07, product decision): when the live Genie
    # turn produced policy-trusted SQL, GENIE'S OWN work is the answer — its
    # query, its rows, its narrative (still subject to the claims guard and
    # the prose-withholding floor below). The reviewed canonical layer is a
    # RESCUE for turns without trusted SQL proof (text-only, or stale-enum
    # SQL the repair retry could not fix), never a replacement for a good
    # live turn. Accuracy comes from curated data and a curated space; the
    # deterministic layer verifies and rescues, it does not overwrite.
    unsafe_live_sql = bool(result.sql_query and not trusted_sql)
    stale_evidence_enum_only = bool(
        result.sql_query
        and _trusted_sql_policy_allowing_stale_evidence_enum(result.sql_query, trusted_assets)
    )
    semantically_broken_sql = bool(
        result.sql_query
        and _sql_uses_impossible_retention_conjunction(question, result.sql_query)
    )
    canonical_rescue_eligible = (
        lacks_trusted_proof
        or (unsafe_live_sql and stale_evidence_enum_only)
        or semantically_broken_sql
    ) and not depends_on_pending_feeds
    if canonical_rescue_eligible:
        canonical = _canonical_genie_answer(
            question=question,
            result=result,
            sql_client=sql_client,
        )
        if canonical is not None:
            # Governance (re-executed counts, trusted-asset policy, proof,
            # visualization, actions, scrubbing) is done. Restore Genie's own
            # narrative and live-intelligence fields so a rescued turn is not
            # flattened into canned phrasing with the live artifacts dropped.
            return _restore_live_voice(
                canonical,
                result,
                narrative_withheld=text_contains_pii,
                trace=trace,
            )
    if lacks_trusted_proof or unsafe_live_sql or depends_on_pending_feeds:
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
    trace.execute(row_count=len(rows) if rows else 0, assets=trusted_assets)
    # Governed cross-check (verification, never replacement): on recognized
    # ranking/metric shapes, compare Genie's OWN result against the reviewed
    # canonical framing and disclose material divergence. Genie's work stays
    # the answer either way; only a metric that contradicts the governed
    # unique-borrower definition additionally withholds the prose below.
    cross = _governed_cross_check(question, rows, sql_client, trace)
    cross_check_gaps = list(cross.gaps)
    # Trusted SQL is a floor, not a coin flip: from here the governed query,
    # rows, proof, and actions ALWAYS ship. Only the model PROSE is
    # conditionally withheld — when the output safety guard flags its wording,
    # or when it carries numbers the returned rows cannot verify — and the
    # withholding is disclosed in proof.known_data_gaps. Unverified model
    # numbers and guard-flagged prose still never render.
    prose_withheld_gap: str | None = None
    prose_withheld_reason: str | None = None
    answer_override: str | None = None
    if cross.count_reference is not None:
        # Audit teeth (2026-07-08 lineage): a live metric whose grain
        # contradicts the governed unique-borrower definition must not render
        # as fact. Genie's query and rows still ship; the answer teaches the
        # grain difference and cites the governed figure.
        prose_withheld_gap = (
            "Genie's draft narrative presented a count whose grain or scope differs "
            "from the governed unique-borrower definition; the prose was "
            "withheld and both governed framings are disclosed."
        )
        answer_override = (
            f"The governed definition for this metric counts "
            f"{cross.count_reference:,} — the canonical unique-borrower "
            "grain, scoped exactly to the question, where a multi-segment "
            "borrower counts once. Genie's live framing returned a different "
            "figure; its query and rows are shown, and the difference comes "
            "from counting grain or question scope, not data drift."
        )
        trace.narrative_withheld(reason=WITHHELD_CONTRADICTED)
    elif text_contains_pii:
        prose_withheld_gap = (
            "Genie's draft narrative was withheld by the output safety guard; "
            "the governed query results are shown instead."
        )
        prose_withheld_reason = "the output safety guard flagged its wording."
        trace.narrative_withheld(reason=WITHHELD_UNSAFE_TEXT)
    elif _unsupported_answer_numeric_claims(result.answer_text, rows, question):
        prose_withheld_gap = (
            "Genie's draft narrative included numeric or financial claims that "
            "could not be verified against the returned rows; the prose was "
            "withheld and the verified rows are shown."
        )
        prose_withheld_reason = (
            "it contained numbers the app could not verify against the returned rows."
        )
        trace.narrative_withheld(reason=WITHHELD_UNVERIFIED_NUMBERS)
    elif (result.answer_text or "").strip():
        trace.verified()
    else:
        trace.narrative_withheld(reason=WITHHELD_NO_NARRATIVE)
    # Genie enhancement fields (2026-07): the deterministic pipeline trace above
    # is the process summary; translated live thoughts are appended only when
    # they add something the pipeline steps do not already state. Every emitted
    # string clears the same output PII guard the answer text is held to, and
    # raw thoughts still never cross this boundary.
    reasoning_trace = trace.steps(genie_reasoning_trace_from_thoughts(result.thoughts))
    proof = _build_genie_proof(
        sql_query=result.sql_query,
        trusted_assets=trusted_assets,
        rows=rows,
        question=question,
        conversation_id=result.conversation_id,
        message_id=result.message_id,
        elapsed_ms=result.elapsed_ms,
        reasoning_trace=[step.model_dump() for step in reasoning_trace],
    )
    extra_gaps = [
        gap
        for gap in [prose_withheld_gap, *cross_check_gaps]
        if gap and gap not in proof.known_data_gaps
    ]
    if extra_gaps:
        proof = proof.model_copy(
            update={"known_data_gaps": [*proof.known_data_gaps, *extra_gaps]}
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
    if answer_override is not None:
        answer_text: str | None = answer_override
    elif prose_withheld_reason:
        row_count = len(rows) if rows else 0
        row_word = "row" if row_count == 1 else "rows"
        answer_text = (
            f"Genie ran a governed query against {trusted_assets[0]} and returned "
            f"{row_count:,} {row_word}, shown with the generated SQL. The draft "
            f"narrative was withheld: {prose_withheld_reason}"
        )
    else:
        answer_text = result.answer_text
    return GenieMessageResponse(
        conversation_id=result.conversation_id,
        message_id=result.message_id,
        elapsed_ms=result.elapsed_ms,
        question_hash=question_hash,
        question=question,
        answer=_ensure_answer_cites_source(answer_text, trusted_assets),
        source="genie",
        trusted_assets=trusted_assets,
        sql_query=result.sql_query,
        row_count=len(rows) if rows else 0,
        proof=proof,
        visualization=visualization,
        actions=actions,
        table_rows=rows,
        follow_up_questions=genie_follow_up_questions(result.suggested_questions),
        native_visualization=genie_native_visualization(result.native_visualization),
        reasoning_trace=reasoning_trace,
        genie_status=result.genie_status,
    )


class _CrossCheckOutcome(NamedTuple):
    gaps: list[str]
    # Canonical unique-borrower count when the live metric materially
    # diverges from the governed definition; None otherwise.
    count_reference: int | None


_CROSS_CHECK_CLEAN = _CrossCheckOutcome(gaps=[], count_reference=None)


def _governed_cross_check(
    question: str,
    rows: list[dict[str, Any]] | None,
    sql_client: DatabricksSqlClient | None,
    trace: GenieProcessTrace,
) -> _CrossCheckOutcome:
    """Verify a trusted LIVE result against the governed canonical framing.

    Verification, never replacement: Genie's own result remains the answer.
    Adds a verify step to the trace and returns disclosure notes only when the
    two governed framings materially diverge. Any warehouse error skips the
    check silently — a failed verification must never degrade a good answer.
    """

    if sql_client is None or not rows:
        return _CROSS_CHECK_CLEAN
    try:
        ranking_sql: str | None = None
        params: dict[str, object] | None = None
        if _canonical_top_borrowers_all_segments_scope(question):
            ranking_sql = _CANONICAL_TOP_BORROWERS_ALL_SEGMENTS_SQL
        else:
            spec_state = _canonical_specific_top_borrowers_state_scope(question)
            spec_global = _canonical_specific_top_borrowers_global_scope(question)
            state_scope = _canonical_top_borrowers_state_scope(question)
            if spec_state is not None:
                intent, _state_name, state_code = spec_state
                ranking_sql = _CANONICAL_TOP_BORROWERS_BY_STATE_INTENT_SQL[intent]
                params = {"state": state_code}
            elif spec_global is not None:
                ranking_sql = _CANONICAL_TOP_BORROWERS_GLOBAL_INTENT_SQL[spec_global]
            elif state_scope is not None:
                ranking_sql = _CANONICAL_TOP_BORROWERS_BY_STATE_SQL
                params = {"state": state_scope[1]}
            elif _canonical_top_borrowers_global_scope(question):
                ranking_sql = _CANONICAL_TOP_BORROWERS_GLOBAL_SQL
        if ranking_sql is not None:
            live_ids = [str(r.get("borrower_id") or "") for r in rows if r.get("borrower_id")]
            if not live_ids:
                return _CROSS_CHECK_CLEAN
            canonical_rows = sql_client.execute(ranking_sql, params) or []
            canonical_ids = [
                str(r.get("borrower_id") or "") for r in canonical_rows if r.get("borrower_id")
            ]
            if not canonical_ids:
                return _CROSS_CHECK_CLEAN
            top_n = min(len(live_ids), len(canonical_ids), 10)
            overlap = len(set(live_ids[:top_n]) & set(canonical_ids[:top_n]))
            trace.cross_check_ranking(overlap=overlap, total=top_n)
            if overlap * 2 < top_n:
                return _CrossCheckOutcome(
                    gaps=[
                        "Governed cross-check: this answer's framing overlaps "
                        f"{overlap} of {top_n} borrowers with the canonical "
                        "opportunity ranking; both are governed views — the "
                        "Lead Queue holds the operational list."
                    ],
                    count_reference=None,
                )
            return _CROSS_CHECK_CLEAN
        count_scope = _canonical_in_the_money_count_scope(question)
        city_scope = None if count_scope else _canonical_itm_city_scope(question)
        if count_scope or city_scope:
            if city_scope:
                count_sql = _CANONICAL_ITM_COUNT_BY_CITY_SQL
                count_params: dict[str, object] | None = {"city": city_scope}
            else:
                count_state = count_scope if isinstance(count_scope, tuple) else None
                count_sql = (
                    _CANONICAL_ITM_COUNT_BY_STATE_SQL if count_state else _CANONICAL_ITM_COUNT_SQL
                )
                count_params = {"state": count_state[1]} if count_state else None
            row = sql_client.execute_one(count_sql, count_params) or {}
            canonical_count = row.get("in_the_money_borrowers")
            if canonical_count is None:
                return _CROSS_CHECK_CLEAN
            canonical_f = float(canonical_count)
            live_values: list[float] = []
            for live_row in rows:
                for value in live_row.values():
                    try:
                        live_values.append(float(value))
                    except (TypeError, ValueError):
                        continue
            if not live_values:
                return _CROSS_CHECK_CLEAN
            tolerance = max(1.0, 0.01 * canonical_f)
            consistent = any(abs(value - canonical_f) <= tolerance for value in live_values)
            trace.cross_check_count(consistent=consistent)
            if not consistent:
                return _CrossCheckOutcome(
                    gaps=[
                        "Governed cross-check: the canonical unique-borrower "
                        f"recomputation returns {int(canonical_f):,} for this "
                        "metric; the live framing differs — compare both "
                        "governed queries in the proof."
                    ],
                    count_reference=int(canonical_f),
                )
    except DatabricksSqlError:
        return _CROSS_CHECK_CLEAN
    return _CROSS_CHECK_CLEAN


def _ensure_answer_cites_source(answer: str | None, trusted_assets: list[str]) -> str:
    """Append a source line when trusted live Genie text omits one."""

    text = (answer or "").strip()
    if not trusted_assets or _SOURCE_LINE_RE.search(text):
        return text
    source = trusted_assets[0]
    if not text:
        return f"Source: {source}"
    return f"{text}\n\nSource: {source}"


def _default_verification_note(
    trusted_assets: list[str],
    metric_value: str | None,
    row_count: int,
) -> str:
    """A short, honest note that the recognized-shape count was re-verified.

    This is appended to Genie's own narrative -- it sharpens/corroborates the
    live text with the re-executed gold-grain figure rather than replacing it.
    """
    asset = trusted_assets[0] if trusted_assets else "the trusted gold tables"
    if metric_value is not None:
        return f"Verified against {asset}: {metric_value} at the trusted gold grain."
    if row_count:
        return (
            f"Verified against {asset}: {row_count:,} rows recomputed at the "
            "trusted gold grain."
        )
    return f"Verified against {asset} at the trusted gold grain."


def _parse_metric_value(metric: object) -> float | None:
    """Normalize a canonical metric (often a pre-formatted string) to float."""
    if metric is None:
        return None
    try:
        return float(str(metric).replace(",", "").strip())
    except ValueError:
        return None


def _narrative_contradicts_metric(
    narrative: str, metric: object
) -> tuple[bool, str | None]:
    """Detect a numeric claim in model prose that the verified metric disproves.

    Conservative by design: only same-scale numbers count as claims (a "35%"
    threshold in prose must not contradict a 122,598 count), zero counts as a
    claim against large metrics (the classic wrong-zero turn), and a claim
    within 1% of the metric is treated as agreement (rounding/formatting).
    Returns (contradicted, first_conflicting_claim_formatted).
    """
    metric_parsed = _parse_metric_value(metric)
    if metric_parsed is None or not narrative:
        return False, None
    metric_f = metric_parsed
    # Identifier guard (external audit 2026-07-08): digits embedded in asset
    # or column names (mip.gold.borrower_360) are not numeric claims — a
    # number only counts when not attached to word characters or dots.
    raw = [
        t.replace(",", "")
        for t in re.findall(r"(?<![\w.])\d[\d,]*\.?\d*(?![\w])", narrative)
    ]
    try:
        nums = [float(t) for t in raw if t]
    except ValueError:  # pragma: no cover - regex only yields numeric tokens
        return False, None
    if metric_f >= 1000:
        claims = [n for n in nums if n == 0 or n >= 1000]
    else:
        # An asserted zero contradicts ANY positive verified metric (external
        # audit 2026-07-08: "0 matching borrowers" vs verified 304 slipped
        # through the magnitude band).
        claims = [n for n in nums if n == 0 or metric_f / 10 <= n <= metric_f * 10]
    if not claims:
        return False, None
    tolerance = max(1.0, 0.01 * abs(metric_f))
    if any(abs(n - metric_f) <= tolerance for n in claims):
        return False, None
    worst = claims[0]
    formatted = f"{worst:,.0f}" if worst == int(worst) else f"{worst:,}"
    return True, formatted


def _restore_live_voice(
    canonical: GenieMessageResponse,
    result: GenieResponse,
    *,
    narrative_withheld: bool = False,
    trace: GenieProcessTrace | None = None,
) -> GenieMessageResponse:
    """Keep a recognized-shape trusted answer's governance but restore voice.

    ``_canonical_genie_answer`` re-executes the count, builds proof, plans the
    visualization, and suggests actions -- all governance we keep. What it used
    to also do was overwrite Genie's narrative with hand-authored template
    phrasing and drop the live-intelligence fields. Here we:

    * Lead with Genie's own (already PII-gated) narrative and APPEND a short
      verification note instead of replacing it. When the live turn has no
      usable narrative even after the repair loop, we fall back to the precise
      deterministic template already on ``canonical.answer`` and disclose that
      honestly in ``proof.known_data_gaps``.
    * Carry through ``genie_status``, ``reasoning_trace``,
      ``native_visualization`` and ``follow_up_questions`` from the live result
      exactly like the generic adaptation path does.

    ``trace`` is the in-progress deterministic process trace from
    ``_adapt_genie_response``; this function appends the canonical /
    execution / verification / composition steps it alone can observe. A
    missing trace (direct callers, older tests) starts a fresh one so the
    canonical half of the story still ships.
    """
    trace = trace if trace is not None else GenieProcessTrace()
    proof = canonical.proof
    canonical_rows = canonical.table_rows or []
    if canonical.metric_value:
        canonical_shape = "metric"
    elif len(canonical_rows) > 1:
        canonical_shape = "ranking"
    else:
        canonical_shape = ""
    trace.canonical(shape=canonical_shape)
    trace.execute(row_count=len(canonical_rows), assets=canonical.trusted_assets)
    # A guard-flagged narrative is withheld wholesale: the deterministic
    # canonical answer ships instead, and the withholding is disclosed below.
    narrative = "" if narrative_withheld else (result.answer_text or "").strip()
    if narrative_withheld and proof is not None:
        gap = (
            "Genie's draft narrative was withheld by the output safety guard; "
            "presenting the verified deterministic summary instead."
        )
        if gap not in proof.known_data_gaps:
            proof = proof.model_copy(
                update={"known_data_gaps": [*proof.known_data_gaps, gap]}
            )
    updates: dict[str, Any] = {}
    contradicted, _ = _narrative_contradicts_metric(
        narrative,
        canonical.metric_value,
    )
    unsupported_claims = _unsupported_answer_numeric_claims(
        narrative,
        canonical.table_rows,
        canonical.question,
    )
    if narrative_withheld:
        trace.narrative_withheld(reason=WITHHELD_UNSAFE_TEXT)
    elif narrative and contradicted:
        trace.narrative_withheld(reason=WITHHELD_CONTRADICTED)
    elif narrative and unsupported_claims:
        trace.narrative_withheld(reason=WITHHELD_UNVERIFIED_NUMBERS)
    elif narrative:
        trace.verified()
    else:
        trace.narrative_withheld(reason=WITHHELD_NO_NARRATIVE_DETERMINISTIC)
    if "**#1" in (canonical.answer or ""):
        trace.composed_brief()
    if narrative and contradicted:
        updates["answer"] = canonical.answer
        if proof is not None:
            gap = (
                "Genie's draft narrative was superseded because it contradicted "
                "the governed recomputation; the unsupported prose was removed."
            )
            if gap not in proof.known_data_gaps:
                proof = proof.model_copy(
                    update={"known_data_gaps": [*proof.known_data_gaps, gap]}
                )
    elif narrative and unsupported_claims:
        # Recognized shapes must obey the same all-claims rule as generic
        # Genie turns. One matching count cannot launder another unsupported
        # rate, balance, percentage, or count in the same narrative.
        updates["answer"] = canonical.answer
        if proof is not None:
            gap = (
                "Genie's draft narrative included numeric or financial claims "
                "that were not supported by the governed recomputation; the "
                "unsupported prose was removed."
            )
            if gap not in proof.known_data_gaps:
                proof = proof.model_copy(
                    update={"known_data_gaps": [*proof.known_data_gaps, gap]}
                )
    elif narrative:
        if "**#1" in (canonical.answer or ""):
            # Any teaching-analyst ranking brief (marked by its per-candidate
            # headers) is the product's deep analysis; a surviving live
            # narrative leads and the verified brief follows, instead of
            # flattening to a one-line note.
            answer = f"{narrative}\n\n{canonical.answer}"
        else:
            note = _default_verification_note(
                canonical.trusted_assets,
                canonical.metric_value,
                len(canonical.table_rows or []),
            )
            answer = narrative
            if note and note not in answer:
                answer = f"{answer}\n\n{note}"
        updates["answer"] = _ensure_answer_cites_source(answer, canonical.trusted_assets)
    elif proof is not None and not narrative_withheld:
        gap = "Genie returned no narrative; presenting the verified deterministic summary."
        if gap not in proof.known_data_gaps:
            proof = proof.model_copy(
                update={"known_data_gaps": [*proof.known_data_gaps, gap]}
            )
    reasoning_trace = trace.steps(genie_reasoning_trace_from_thoughts(result.thoughts))
    if proof is not None:
        updates["proof"] = proof.model_copy(
            update={
                "reasoning_trace": reasoning_trace,
                "conversation_id": result.conversation_id,
                "message_id": result.message_id,
            }
        )
    # Canonical answers intentionally carry deterministic proof identifiers.
    # Feedback, however, belongs to the live Conversation API turn, so retain
    # its identity on the response without changing the recomputed metric.
    updates["conversation_id"] = result.conversation_id
    updates["message_id"] = result.message_id
    updates["elapsed_ms"] = result.elapsed_ms
    updates["reasoning_trace"] = reasoning_trace
    updates["follow_up_questions"] = genie_follow_up_questions(result.suggested_questions)
    updates["native_visualization"] = genie_native_visualization(result.native_visualization)
    updates["genie_status"] = result.genie_status
    return canonical.model_copy(update=updates)


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
        retention_fallback = None
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
            retention_fallback = _retention_eligibility_fallback_from_summary(
                summary_rows,
                state_name=state_name,
                state_code=state_code,
            )
            if retention_fallback is not None:
                response_sql_query = retention_fallback.sql_query
                response_rows = retention_fallback.rows
                suppress_actions = retention_fallback.suppress_actions
                metric_value = retention_fallback.metric_value
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
            intent_note = _specific_top_borrower_intent_note(question, intent)
            answer = compose_cohort_ranking_brief(
                rows,
                cohort_label=f"{state_name} ({state_code}) {intent_label} borrowers",
                ordering_label=sort_label,
                source_asset=borrower_asset,
                scope_note=intent_note.strip(),
            )
        elif retention_fallback is not None:
            answer = retention_fallback.answer
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
        retention_fallback = None
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
            retention_fallback = _retention_eligibility_fallback_from_summary(summary_rows)
            if retention_fallback is not None:
                response_sql_query = retention_fallback.sql_query
                response_rows = retention_fallback.rows
                suppress_actions = retention_fallback.suppress_actions
                metric_value = retention_fallback.metric_value
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
            intent_note = _specific_top_borrower_intent_note(question, intent)
            answer = compose_cohort_ranking_brief(
                rows,
                cohort_label=f"{intent_label} borrowers across the current refreshed coverage",
                ordering_label=sort_label,
                source_asset=borrower_asset,
                scope_note=intent_note.strip(),
            )
        elif retention_fallback is not None:
            answer = retention_fallback.answer
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
    if _canonical_top_borrowers_all_segments_scope(question):
        try:
            rows = sql_client.execute(_CANONICAL_TOP_BORROWERS_ALL_SEGMENTS_SQL)
        except DatabricksSqlError as exc:
            _emit_genie_warning("canonical_genie_top_borrowers_all_segments_failed", exc=exc)
            return None
        rows = _redact_genie_rows(rows) or []
        trusted_assets = [borrower_asset]
        question_hash = _genie_question_hash(question)
        proof = _build_genie_proof(
            sql_query=_CANONICAL_TOP_BORROWERS_ALL_SEGMENTS_SQL,
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
            sql_query=_CANONICAL_TOP_BORROWERS_ALL_SEGMENTS_SQL,
            source="trusted_sql",
        )
        return GenieMessageResponse(
            conversation_id=result.conversation_id,
            message_id=result.message_id,
            elapsed_ms=result.elapsed_ms,
            question_hash=question_hash,
            question=question,
            answer=compose_all_segments_brief(rows, borrower_asset),
            source="trusted_sql",
            trusted_assets=trusted_assets,
            sql_query=_CANONICAL_TOP_BORROWERS_ALL_SEGMENTS_SQL,
            row_count=len(rows),
            proof=proof,
            visualization=visualization,
            actions=actions,
            table_rows=rows,
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
        trusted_assets = [lead_population_asset, borrower_asset]
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
        answer = compose_cohort_ranking_brief(
            rows,
            cohort_label=f"{state_name} ({state_code}) borrowers in the ranked Lead Queue",
            ordering_label="lead score",
            source_asset=lead_population_asset,
        ) if rows else (
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
    if _canonical_top_borrowers_global_scope(question):
        try:
            rows = sql_client.execute(_CANONICAL_TOP_BORROWERS_GLOBAL_SQL)
        except DatabricksSqlError as exc:
            _emit_genie_warning("canonical_genie_top_borrowers_global_failed", exc=exc)
            return None
        rows = _redact_genie_rows(rows) or []
        trusted_assets = [lead_population_asset, borrower_asset]
        question_hash = _genie_question_hash(question)
        proof = _build_genie_proof(
            sql_query=_CANONICAL_TOP_BORROWERS_GLOBAL_SQL,
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
            sql_query=_CANONICAL_TOP_BORROWERS_GLOBAL_SQL,
            source="trusted_sql",
        )
        answer = compose_cohort_ranking_brief(
            rows,
            cohort_label="marketing-eligible borrowers in the ranked Lead Queue",
            ordering_label="lead score",
            source_asset=lead_population_asset,
        ) if rows else (
            "The ranked lead population returned no marketing-eligible borrower "
            "rows for the current refreshed coverage."
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
            sql_query=_CANONICAL_TOP_BORROWERS_GLOBAL_SQL,
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
        answer = compose_cohort_ranking_brief(
            rows,
            cohort_label="marketing-eligible listed-for-sale borrowers",
            ordering_label="opportunity score among active listing signals",
            source_asset=borrower_asset,
        ) if rows else (
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
            f"There are {count_int:,} borrowers passing the refinance-economics screen in {city_scope} — meaning their current mortgage rate sits far enough above today\'s market, with enough home equity, that refinancing typically pays for itself — "
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
        f"There are {count_int:,} borrowers passing the refinance-economics screen{geo_text} — meaning their current mortgage rate sits far enough above today\'s market, with enough home equity, that refinancing typically pays for itself. "
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
