"""Databricks-backed Genie repository and SQL trust-policy helpers."""

from __future__ import annotations

from datetime import UTC, datetime

from backend.config.settings import settings
from backend.services.databricks_sql import DatabricksSqlClient
from backend.services.genie_answers import (
    GenieMessageResponse,
    GenieProof,
    GenieReasoningStep,
    default_follow_up_questions,
)
from backend.services.genie_client import (
    GenieClientError,
    GenieResponse,
    ResilientGenieClient,
)
from backend.services.repositories.databricks_genie_actions import (
    _borrower_ids_from_rows,  # noqa: F401 - compatibility re-export
    _portfolio_criteria_from_sql,  # noqa: F401 - compatibility re-export
    _route_from_answer_rows,  # noqa: F401 - compatibility re-export
    _row_values,  # noqa: F401 - compatibility re-export
    _sql_hash,  # noqa: F401 - compatibility re-export
    _suggest_genie_actions,
    _total_matching_from_rows,  # noqa: F401 - compatibility re-export
)
from backend.services.repositories.databricks_genie_canonical import (
    _CANONICAL_SEGMENT_APPROVAL_RATE_SQL,  # noqa: F401 - compatibility re-export
)
from backend.services.repositories.databricks_genie_canonical_answer import (
    _canonical_genie_answer,
)
from backend.services.repositories.databricks_genie_cross_check import (
    _CROSS_CHECK_CLEAN,  # noqa: F401 - compatibility re-export
    _CrossCheckOutcome,  # noqa: F401 - compatibility re-export
    _governed_cross_check,
)
from backend.services.repositories.databricks_genie_direct import (
    direct_canonical_response,
)
from backend.services.repositories.databricks_genie_narrative import (
    _NARRATIVE_REWRITTEN_GAP,
    _SOURCE_LINE_RE,  # noqa: F401 - compatibility re-export
    _UNVERIFIED_CLAIMS_GAP_MARKER,
    _default_verification_note,  # noqa: F401 - compatibility re-export
    _ensure_answer_cites_source,
    _factual_row_summary,
    _format_cell,  # noqa: F401 - compatibility re-export
    _humanize_column,  # noqa: F401 - compatibility re-export
    _narrative_contradicts_metric,  # noqa: F401 - compatibility re-export
    _narrative_repair_prompt,
    _parse_metric_value,  # noqa: F401 - compatibility re-export
    _plain_label,  # noqa: F401 - compatibility re-export
    _plain_pairs,  # noqa: F401 - compatibility re-export
    _restore_live_voice,
    _sentence_join,  # noqa: F401 - compatibility re-export
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
from backend.services.repositories.databricks_genie_sweep import (
    is_deep_analysis_request,
    run_planned_sweep,
)
from backend.services.repositories.databricks_genie_trace import (
    WITHHELD_CONTRADICTED,
    WITHHELD_NO_NARRATIVE,
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
    augment_cohort_label,
)
from backend.services.resilience import DependencyDownError

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
        poll_timeout_s: int | None = None,
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
        if allow_sweep and is_deep_analysis_request(question):
            # A shortlist + per-item why + offer call is inherently
            # multi-part; a single governed SQL turn answers it at the depth
            # of one app screen (live capture, 2026-08-08). Deep asks go to
            # the live space's own planned decomposition first; an unusable
            # plan falls through to the normal single-turn path.
            sweep = run_planned_sweep(self, question, deep=True)
            if sweep is not None:
                return sweep
        repaired = False
        try:
            # Only the deep sweep sets a deadline; the interactive path
            # keeps the historical call shape so every client implementation
            # (and test double) of ask() stays valid.
            ask_kwargs: dict[str, object] = {"conversation_id": conversation_id}
            if poll_timeout_s is not None:
                ask_kwargs["poll_timeout_s"] = poll_timeout_s
            result = self._genie.ask(question, **ask_kwargs)
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
        adapted = _adapt_genie_response(
            question,
            result,
            sql_client=self._sql_client,
            repaired=repaired,
        )
        adapted = self._rewrite_unverified_narrative(question, result, adapted)
        if allow_sweep and adapted.source == "policy_blocked":
            # Outcome-triggered, no keywords: no guardrail-passing question is
            # allowed to end in a governed refusal until the live space has
            # been given the chance to PLAN its own decomposition and answer
            # each part itself. An unusable plan returns the honest refusal.
            sweep = run_planned_sweep(self, question)
            if sweep is not None:
                return sweep
        return adapted

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
        if is_deep_analysis_request(question):
            # Same deep-first routing as :meth:`respond`. The submitted live
            # message's cost is sunk either way; a usable planned
            # decomposition is the materially deeper answer, and an unusable
            # plan falls through to completing the submitted turn.
            sweep = run_planned_sweep(self, question, deep=True)
            if sweep is not None:
                return sweep
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
        adapted = _adapt_genie_response(
            question,
            result,
            sql_client=self._sql_client,
            repaired=repaired,
        )
        adapted = self._rewrite_unverified_narrative(question, result, adapted)
        if adapted.source == "policy_blocked":
            # Same outcome-triggered planner as :meth:`respond`.
            sweep = run_planned_sweep(self, question)
            if sweep is not None:
                return sweep
        return adapted

    def ask_raw(self, prompt: str) -> str | None:
        """Raw narrative text of one live turn (planner use only).

        No adaptation, no policy verdicts — the caller screens and never
        renders this text; it is parsed for planned sub-questions which then
        re-enter the full pipeline individually.
        """
        result = self._genie.ask(prompt, conversation_id=None)
        return result.answer_text

    def _rewrite_unverified_narrative(
        self,
        question: str,
        result: GenieResponse,
        adapted: GenieMessageResponse,
    ) -> GenieMessageResponse:
        """One live rewrite when the claims verifier withheld Genie's prose.

        Only for a data-bearing live turn whose narrative failed numeric
        verification. The rewrite re-enters the same verification (numeric
        claims against the rows, PII, output safety); a rewrite that fails
        leaves the plain row digest in place. Never authors text server-side.
        """

        from backend.services.genie_message_policy import genie_visible_text_unsafe

        proof = adapted.proof
        rows = adapted.table_rows or []
        if adapted.source != "genie" or proof is None or not rows:
            return adapted
        if not any(_UNVERIFIED_CLAIMS_GAP_MARKER in gap for gap in proof.known_data_gaps):
            return adapted
        try:
            turn = self._genie.ask(
                _narrative_repair_prompt(question, rows),
                conversation_id=result.conversation_id,
            )
        except (DependencyDownError, GenieClientError):
            return adapted
        draft = (turn.answer_text or "").strip()
        if not draft:
            return adapted
        if _unsupported_answer_numeric_claims(draft, rows, question):
            return adapted
        if _answer_text_contains_pii(draft) or genie_visible_text_unsafe(draft):
            return adapted
        gaps = [gap for gap in proof.known_data_gaps if _UNVERIFIED_CLAIMS_GAP_MARKER not in gap]
        gaps.append(_NARRATIVE_REWRITTEN_GAP)
        step = GenieReasoningStep(
            kind="verify",
            content=(
                "Genie's first draft cited a figure outside its returned rows; it "
                "rewrote the summary from the verified figures and the rewrite "
                "was verified against the same rows."
            ),
        )
        return adapted.model_copy(
            update={
                "answer": _ensure_answer_cites_source(draft, adapted.trusted_assets),
                "proof": proof.model_copy(
                    update={
                        "known_data_gaps": gaps,
                        "reasoning_trace": [*proof.reasoning_trace, step],
                    }
                ),
                "reasoning_trace": [*adapted.reasoning_trace, step],
            }
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
    # `depends_on_pending_feeds` is read off the LIVE turn's material -- the SQL
    # Genie wrote and the rows it returned. It must block that answer, and it
    # does, below. It must NOT veto the rescue, because the rescue serves a
    # DIFFERENT statement: whether Genie's discarded SQL happened to mention a
    # pending feed says nothing about whether the canonical one does.
    #
    # Measured live on paychex 2026-08-12: "Which segment converts best: HELOC,
    # cash-out, or retention?" returns SQL referencing a permit column --
    # unsurprising, since HELOC Intent IS the permit segment -- so the pending
    # Building Permits feed set this flag and the rescue never ran. The
    # canonical statement it would have served reads
    # `mip.semantics.segment_performance_metric_view` and touches no permit
    # column at all. The user got a refusal that cited a data gap irrelevant to
    # the answer they could have had.
    canonical_rescue_eligible = (
        lacks_trusted_proof
        or (unsafe_live_sql and stale_evidence_enum_only)
        or semantically_broken_sql
    )
    if canonical_rescue_eligible:
        canonical = _canonical_genie_answer(
            question=question,
            result=result,
            sql_client=sql_client,
        )
        # The guarantee is preserved where it belongs: on the statement actually
        # being served. A canonical statement that DOES depend on a pending feed
        # is still refused.
        if canonical is not None and _pending_feed_gaps_from_material(
            canonical.sql_query or ""
        ):
            canonical = None
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
    # Presentation only: a flag co-occurrence result gets a readable cohort
    # label so its chart and table can be told apart row by row. The claims
    # verification above already ran on the untouched rows.
    rows = augment_cohort_label(rows)
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
    elif cross_check_gaps and not prose_withheld_reason:
        # Doctrine: disclose divergence — IN THE ANSWER, not only in the
        # proof drawer. The persona audit confirmed a 0-of-10 ranking overlap
        # was computed and never shown to the user (the presenter's next click
        # into Lead Queue would not match the list on screen).
        divergence_note = " ".join(cross_check_gaps)
        answer_text = (
            f"{(result.answer_text or '').strip()}\n\n{divergence_note}"
            if (result.answer_text or "").strip()
            else divergence_note
        )
    elif prose_withheld_reason:
        # Withholding the model's prose must not leave the user reading
        # pipeline chatter. The verified rows are already in hand, so render
        # them factually — values straight from the result, no derivation and
        # no claims — and disclose why the draft was withheld. (Live persona
        # audit 2026-08-07: withheld turns rendered a status line and read as
        # content-free answers.)
        answer_text = _factual_row_summary(
            rows,
            trusted_assets,
            withheld_reason=prose_withheld_reason,
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
