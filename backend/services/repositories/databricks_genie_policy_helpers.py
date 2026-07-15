"""Genie response policy helpers shared by the repository adapter."""

from __future__ import annotations

import logging
import re
from typing import Any

from backend.config.settings import settings
from backend.schemas._validators import contains_unsafe_ai_text
from backend.services.databricks_sql_helpers import qualify
from backend.services.genie_answers import GenieNativeVisualization, GenieReasoningStep
from backend.services.genie_client import GenieResponse
from backend.services.observability import emit
from backend.services.pii_redaction import _FORBIDDEN_OUTPUT_KEYS, scrub_free_text
from backend.services.repositories.databricks_genie_canonical import (
    _retention_risk_question,
)
from backend.services.repositories.databricks_genie_policy import _extract_asset_refs
from backend.services.repositories.databricks_genie_trust import (
    _sql_uses_stale_evidence_signal_enum,
)

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
        "owner_name_hash",  # hashed, but still a stable identifier; not exported
        "owner_link_id",  # raw Cotality identifier; replaced with a display surrogate elsewhere
        "clip",  # raw CLIP; evidence drawer surfaces a short form only
        "raw_clip",
        "street_address",
        "site_address",
        "mailing_address",
        "tax_mailing_address",
        "subject_property",
        "owner_email",
        "borrower_email",
        "email",
        "phone",
        "phone_number",
        "ssn",
    }
)


def _redact_genie_rows(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """Strip PII keys from Genie's result set before returning to the UI."""
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
    """Return true for any text unsafe to render, retaining the legacy name."""

    if not text:
        return False
    return any(pattern.search(text) for pattern in _PII_TEXT_PATTERNS) or contains_unsafe_ai_text(
        text
    )


def _safe_rendered_summary(text: object, *, max_length: int | None = None) -> str | None:
    """Return scrubbed public text, or drop the field when either pass is unsafe."""

    raw = str(text or "").strip()
    if not raw or _answer_text_contains_pii(raw):
        return None
    cleaned = scrub_free_text(raw).strip()
    if max_length is not None:
        cleaned = cleaned[:max_length].strip()
    if not cleaned or _answer_text_contains_pii(cleaned):
        return None
    return cleaned


# ---------------------------------------------------------------------------
# Genie enhancement fields (2026-07). The live Genie turn can carry a native
# visualization reference, model-suggested follow-up questions, and a public
# process summary. Raw model thoughts never cross the API boundary. Each
# model-authored string that ships in the response body is routed through
# the same fail-closed output guard as the answer text, before optional
# scrubbing, so a drifting Space cannot leak or visibly redact unsafe text
# through a suggestion, a viz title, or a reasoning step. Deterministic
# trusted_sql / refused paths do NOT
# call these builders; they emit empty list / None (no fabrication).
# ---------------------------------------------------------------------------

# Cap the public process summary so a runaway thoughts array cannot bloat the
# response body.
_GENIE_MAX_REASONING_STEPS = 12

_GENIE_PUBLIC_PROCESS_STEPS: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (
        ("FILTER", "CONTEXT", "CLASSIFY"),
        "context",
        "Selected the governed mortgage context for this question.",
    ),
    (
        ("QUERY", "SQL", "EXECUT"),
        "query",
        "Prepared a governed query plan over approved data assets.",
    ),
    (
        ("SYNTH", "ANSWER", "SUMMAR"),
        "answer",
        "Summarized the verified result for review.",
    ),
)


def _public_genie_process_step(raw_kind: object) -> tuple[str, str]:
    normalized = re.sub(r"[^A-Z0-9]+", "_", str(raw_kind or "").upper()).strip("_")
    normalized = normalized.removeprefix("THOUGHT_TYPE_")
    for tokens, kind, content in _GENIE_PUBLIC_PROCESS_STEPS:
        if any(token in normalized for token in tokens):
            return kind, content
    return "analysis", "Analyzed the request within the governed Genie workflow."


def genie_reasoning_trace_from_thoughts(
    thoughts: list[dict[str, str]] | None,
) -> list[GenieReasoningStep]:
    """Translate private model thoughts into bounded server-owned process steps."""
    steps: list[GenieReasoningStep] = []
    for raw in thoughts or []:
        if not isinstance(raw, dict):
            continue
        raw_content = str(raw.get("content") or "").strip()
        if not raw_content:
            continue
        kind, content = _public_genie_process_step(raw.get("kind"))
        steps.append(GenieReasoningStep(kind=kind, content=content))
        if len(steps) >= _GENIE_MAX_REASONING_STEPS:
            break
    return steps


def genie_follow_up_questions(suggested: list[str] | None) -> list[str]:
    """Scrub + dedupe Genie-suggested follow-up questions for the response.

    Defensive cap: the client already limits its field to five entries, but a
    future caller must not be able to widen the surfaced list past that
    contract, so the cap is re-applied here.
    """
    out: list[str] = []
    for raw in suggested or []:
        text = _safe_rendered_summary(raw)
        if text is not None and text not in out:
            out.append(text)
        if len(out) >= 5:
            break
    return out


def genie_native_visualization(
    native: dict[str, Any] | None,
) -> GenieNativeVisualization | None:
    """Adapt the client's native-visualization dict into the wire model."""
    if not isinstance(native, dict):
        return None
    attachment_id = native.get("attachment_id")
    if not attachment_id:
        return None
    title = native.get("title")
    query_attachment_id = native.get("query_attachment_id")
    safe_title = _safe_rendered_summary(title) if title else None
    return GenieNativeVisualization(
        attachment_id=str(attachment_id),
        query_attachment_id=str(query_attachment_id) if query_attachment_id else None,
        title=safe_title or None,
    )
