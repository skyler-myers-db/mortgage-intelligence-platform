"""Genie wire models and static prompt suggestions.

This module deliberately contains only schema models and prompt text.
Runtime answers must come
from Databricks Genie or from explicitly executed trusted SQL in the repository
layer. The only static content here is prompt text used to help users ask a
question when Genie is idle or degraded.
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field


class GenieDataFreshness(BaseModel):
    asset: str
    refreshed_at: str | None = None
    status: str = "unknown"
    note: str | None = None


class GenieReasoningStep(BaseModel):
    kind: str
    content: str


class GenieNativeVisualization(BaseModel):
    """Reference to a Genie-authored native visualization attachment.

    Carries only the attachment ids and optional title. MIP does not fetch
    the Beta download endpoint (not rolled out on this workspace, returns
    404); the frontend uses these ids to advertise/label the native chart.
    """

    attachment_id: str
    query_attachment_id: str | None = None
    title: str | None = None


class GenieProof(BaseModel):
    sql_query: str | None = None
    source_assets: list[str] = Field(default_factory=list)
    data_freshness: list[GenieDataFreshness] = Field(default_factory=list)
    row_count: int | None = None
    filters: list[str] = Field(default_factory=list)
    trusted: bool = False
    reasoning_trace: list[GenieReasoningStep] = Field(default_factory=list)
    known_data_gaps: list[str] = Field(default_factory=list)
    conversation_id: str | None = None
    message_id: str | None = None
    elapsed_ms: int | None = None
    generated_at: str | None = None


class GenieVisualizationSpec(BaseModel):
    kind: str
    title: str | None = None
    x: str | None = None
    y: str | None = None
    series: str | None = None
    reason: str | None = None


class GenieActionSuggestion(BaseModel):
    id: str
    label: str
    action_type: str
    description: str
    requires_confirmation: bool = True
    route: str | None = None
    borrower_ids: list[str] = Field(default_factory=list)
    criteria: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = Field(default=None, max_length=128)
    confirmation_token: str | None = None


class GenieActionRequest(BaseModel):
    action_type: str = Field(min_length=1, max_length=64)
    conversation_id: str | None = Field(default=None, max_length=128)
    message_id: str | None = Field(default=None, max_length=128)
    question_hash: str | None = Field(default=None, max_length=64)
    borrower_ids: list[str] = Field(default_factory=list, max_length=500)
    criteria: dict[str, Any] = Field(default_factory=dict)
    route: str | None = Field(default=None, max_length=16384)
    request_id: str | None = Field(default=None, max_length=128)
    confirmed: bool = False
    confirmation_token: str | None = Field(default=None, max_length=65536)


class GenieActionResponse(BaseModel):
    ok: bool
    action_type: str
    audit_event_id: str | None = None
    route: str | None = None
    saved_count: int = 0
    campaign_id: str | None = None
    message: str


class GenieStartResponse(BaseModel):
    conversation_id: str | None = None
    trusted_assets: list[str] = Field(default_factory=list)
    sample_questions: list[str] = Field(default_factory=list)


class GenieMessageResponse(BaseModel):
    """Wire contract returned by `/api/genie/message`."""

    conversation_id: str
    question: str
    answer: str
    source: str
    trusted_assets: list[str]
    message_id: str | None = None
    elapsed_ms: int | None = None
    question_hash: str | None = None
    sql_query: str | None = None
    row_count: int | None = None
    proof: GenieProof | None = None
    visualization: GenieVisualizationSpec | None = None
    actions: list[GenieActionSuggestion] = Field(default_factory=list)
    metric_value: str | None = None
    table_rows: list[dict[str, Any]] | None = None
    follow_up_questions: list[str] = Field(default_factory=list)
    #: Genie-authored native visualization reference for this turn (from the
    #: ``enable_visualization`` attachment). ``None`` on deterministic
    #: trusted_sql / refused / degraded paths and older API shapes.
    native_visualization: GenieNativeVisualization | None = None
    #: Server-owned public process summary derived from the presence/type of
    #: Genie query steps. Raw model thoughts never leave the backend. Empty on
    #: deterministic trusted_sql / refused paths (no fabrication). Mirrors
    #: ``proof.reasoning_trace`` for turns that carry one.
    reasoning_trace: list[GenieReasoningStep] = Field(default_factory=list)
    #: Terminal Genie message status for the polled turn (e.g. ``COMPLETED``).
    #: The ask is a single blocking backend call, so only the terminal status
    #: is surfaced -- intermediate stages (FILTERING_CONTEXT, EXECUTING_QUERY)
    #: are not faked. ``None`` on deterministic / degraded paths.
    genie_status: str | None = None


class GenieSubmitResponse(BaseModel):
    """Wire contract for `/api/genie/message/submit` (async lifecycle).

    ``completed=True`` means the turn resolved deterministically before any
    live Genie call (guardrail refusal, sales-ops, footprint, degraded
    fallback) and ``response`` carries the full governed answer — identical
    body to the synchronous endpoint. ``completed=False`` means a live Genie
    message was created; the browser polls `/message/progress` with the
    signed ``progress_token`` and finishes via `/message/complete`.
    """

    completed: bool
    conversation_id: str | None = None
    message_id: str | None = None
    #: HMAC-signed claims binding (actor, conversation, message, question
    #: hash, expiry). Progress/complete calls are refused without it, which
    #: is what authorizes the in-flight window before the completed turn is
    #: recorded to Lakebase session ownership.
    progress_token: str | None = None
    question_hash: str | None = None
    response: GenieMessageResponse | None = None


class GenieSessionSummary(BaseModel):
    """One row of the actor's Ask Genie history list."""

    conversation_id: str
    #: First question of the conversation, truncated for the list UI. Empty
    #: for conversations recorded before turn payloads were persisted.
    title: str = ""
    last_activity_at: str | None = None
    turn_count: int = 0


class GenieSessionListResponse(BaseModel):
    sessions: list[GenieSessionSummary] = Field(default_factory=list)


class GenieSessionTurn(BaseModel):
    """One replayable question/answer pair from a stored conversation.

    ``response`` is the exact governed ``GenieMessageResponse`` the chat
    already renders. It was scanned by the output policy and PII-redacted
    before it was ever persisted.
    """

    question: str
    response: GenieMessageResponse


class GenieSessionDetailResponse(BaseModel):
    conversation_id: str
    turns: list[GenieSessionTurn] = Field(default_factory=list)


class GenieProgressResponse(BaseModel):
    """Live progress for one in-flight Genie message.

    Server-owned vocabulary only: ``status`` is the raw Genie lifecycle enum
    (platform-authored, not model text), ``stage``/``stage_label`` are our
    bounded mapping of it, and ``reasoning_trace`` reuses the same
    private-thought → public-process translation as the completed answer.
    ``sql_preview`` is the same generated SQL the completed proof already
    exposes — surfaced earlier, capped, never model prose.
    """

    status: str
    stage: str
    stage_label: str
    terminal: bool = False
    failed: bool = False
    reasoning_trace: list[GenieReasoningStep] = Field(default_factory=list)
    sql_preview: str | None = None
    error_hint: str | None = None


_SAMPLE_QUESTIONS_CACHE: tuple[list[str], ...] | None = None


def _sample_questions_path() -> Any:  # pragma: no cover -- trivial
    from pathlib import Path

    return Path(__file__).resolve().parents[2] / "genie" / "sample_questions.md"


def _normalise_sample(q: str) -> str:
    """Lowercase, collapse whitespace, strip trailing punctuation."""

    q = q.lower().strip()
    q = re.sub(r"[-‐-―_]", " ", q)
    q = re.sub(r"\s+", " ", q)
    return q.rstrip(" .?!")


def load_sample_questions() -> list[str]:
    """Parse ``genie/sample_questions.md`` into prompt suggestions.

    Returning an empty list on read failure keeps degraded Genie honest: the
    app can still say Genie is unavailable without inventing an answer or
    source.
    """

    global _SAMPLE_QUESTIONS_CACHE
    if _SAMPLE_QUESTIONS_CACHE is not None:
        return list(_SAMPLE_QUESTIONS_CACHE[0])
    path = _sample_questions_path()
    questions: list[str] = []
    if not path.exists():
        _SAMPLE_QUESTIONS_CACHE = (questions,)
        return questions
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        _SAMPLE_QUESTIONS_CACHE = (questions,)
        return questions
    pattern = re.compile(r"^\s*\d+\.\s+\*\*(.+?)\*\*\s*$", re.MULTILINE)
    for match in pattern.finditer(text):
        q = match.group(1).strip()
        if q:
            questions.append(q)
    _SAMPLE_QUESTIONS_CACHE = (questions,)
    return questions


def default_follow_up_questions(limit: int = 3) -> list[str]:
    """Return static prompt suggestions only.

    These strings are intentionally questions, never answers, metrics, rows, or
    recommendations. The default list is sourced from the same markdown used to
    provision the Genie space.
    """

    if limit <= 0:
        return []
    return load_sample_questions()[:limit]


def match_sample_question(question: str) -> str | None:
    """Return a canonical sample question matching the caller input."""

    normalised = _normalise_sample(question)
    if not normalised:
        return None
    for sample in load_sample_questions():
        norm_sample = _normalise_sample(sample)
        if not norm_sample:
            continue
        if normalised == norm_sample:
            return sample
        if normalised in norm_sample or norm_sample in normalised:
            return sample
    return None


def _reset_sample_questions_cache_for_tests() -> None:
    """Test helper -- drop the memoised markdown parse."""

    global _SAMPLE_QUESTIONS_CACHE
    _SAMPLE_QUESTIONS_CACHE = None
