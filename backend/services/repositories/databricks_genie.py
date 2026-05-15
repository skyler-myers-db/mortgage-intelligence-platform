"""Databricks-backed Genie repository and SQL trust-policy helpers."""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import UTC, datetime
from typing import Any

from backend.services.databricks_sql import DatabricksSqlClient, DatabricksSqlError
from backend.services.databricks_sql_helpers import qualify
from backend.services.genie_answers import (
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
    _CANONICAL_CURRENT_CUSTOMER_RETENTION_RISK_SQL,
    _CANONICAL_ITM_COUNT_BY_CITY_SQL,
    _CANONICAL_ITM_COUNT_BY_STATE_SQL,
    _CANONICAL_ITM_COUNT_SQL,
    _CANONICAL_ITM_TOP_ZIPS_SQL,
    _CANONICAL_MSA_SCORE_SQL,
    _CANONICAL_RETENTION_COMPETITOR_LIEN_LIST_SQL,
    _canonical_in_the_money_count_scope,
    _canonical_itm_city_scope,
    _canonical_itm_zip_scope,
    _canonical_msa_score_scope,
    _current_footprint_label,
    _retention_competitor_lien_list_question,
    _retention_risk_question,
)
from backend.services.repositories.databricks_genie_policy import (
    _extract_asset_refs,
    _scrub_sql_for_policy,
    _sql_has_unqualified_relations,
    _sql_mentions_pii_columns,
)
from backend.services.resilience import DependencyDownError

log = logging.getLogger("backend.services.repositories.databricks_repo")


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
    if _retention_competitor_lien_list_question(question):
        try:
            rows = (
                _redact_genie_rows(
                    sql_client.execute(_CANONICAL_RETENTION_COMPETITOR_LIEN_LIST_SQL)
                )
                or []
            )
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
            row = (
                sql_client.execute_one(
                    _CANONICAL_ITM_COUNT_BY_CITY_SQL,
                    {"city": city_scope},
                )
                or {}
            )
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
