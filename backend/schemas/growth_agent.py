"""Contracts for the Mortgage Growth Agent workspace."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.schemas.common import (
    contains_pii_marker,
    validate_public_campaign_label,
    validate_public_opaque_id,
)
from backend.schemas.usps import is_usps_state_code

GrowthAgentWorkflowId = Literal[
    "daily_refi_brief",
    "borrower_dossier_review",
    "listing_watch",
    "competitor_recapture_monitor",
    "high_equity_heloc_watch",
    "branch_capacity_review",
    "source_freshness_sentinel",
    "custom_segment_watch",
]
GrowthAgentCadence = Literal["daily", "weekly"]
GrowthAgentSegmentCode = Literal["itm", "listed", "permit", "investor", "equity", "retention"]
GrowthAgentSegmentMode = Literal["any", "all"]
GrowthAgentNotificationChannel = Literal["slack", "teams"]
GrowthAgentSpecialist = Literal[
    "structured_data_agent",
    "borrower_dossier_agent",
    "offer_agent",
    "compliance_agent",
    "campaign_agent",
    "data_ops_agent",
]
GrowthAgentExecutionMode = Literal["deterministic", "genie_conversation", "agent_framework"]
GrowthAgentTraceKind = Literal["local_hash", "genie_conversation", "agent_framework", "mlflow_trace"]

_STATE_RE = re.compile(r"^[A-Z]{2}$")
_MONITOR_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 &.,:+/-]{0,79}$")
_RAW_IDENTIFIER_RE = re.compile(
    r"\b(?:clip_ref_[0-9a-f]{6,}|clip\s*[:#-]?\s*[A-Za-z0-9_-]{6,}|"
    r"owner[_\s-]?link(?:\s*[A-Za-z0-9_-]{3,})?|raw[_\s-]?clip|B-[A-Za-z0-9_-]{3,})\b",
    re.IGNORECASE,
)
_PROMPT_STREET_ADDRESS_RE = re.compile(
    r"\b\d{1,6}\s+[A-Za-z0-9.'-]+\s+"
    r"(?:st|street|ave|avenue|rd|road|dr|drive|ln|lane|blvd|boulevard|ct|court|"
    r"ter|terrace|way)\b",
    re.IGNORECASE,
)
_PROMPT_STREET_ADDRESS_NO_SUFFIX_RE = re.compile(
    r"\b\d{1,6}\s+[A-Z][A-Za-z0-9.'-]{2,}\s+[A-Z][A-Za-z0-9.'-]{2,}"
    r"(?:\s+[A-Z][A-Za-z0-9.'-]{2,}){0,2}\b"
)
_PROMPT_ADDRESS_CONTEXT_RE = re.compile(
    r"\b(?:at|near|around|address|property|home|house|located|location)\s+$",
    re.IGNORECASE,
)
_PROMPT_HUMAN_NAME_RE = re.compile(
    r"\b(?:for|named|borrowers?|customers?|prospects?|contacts?|person|show|find|review)\s+"
    r"(?:(?:[A-Z][a-z]{1,30}|[A-Z]{2,30})\s+"
    r"(?:[A-Z]\s+)?(?:[A-Z][a-z]{1,30}|[A-Z]{2,30}))\b"
)
_PROMPT_COMMON_NAME_RE = re.compile(
    r"\b(?:for|named|borrowers?|customers?|prospects?|contacts?|person|show|find|review)\s+"
    r"(?:alice|john|jane|maria|robert|james|mary|michael|david|linda|william|"
    r"richard|joseph|thomas|patricia|jennifer|barbara)\s+"
    r"(?:[a-z]\s+)?"
    r"(?:smith|johnson|williams|brown|jones|garcia|miller|davis|rodriguez|"
    r"martinez|hernandez|lopez|gonzalez|wilson|anderson|thomas|taylor|moore)\b",
    re.IGNORECASE,
)
_PROMPT_LOWERCASE_NAME_AFTER_GROUP_RE = re.compile(
    r"\b(?:borrowers?|customers?|prospects?|contacts?|people|person)\s+"
    r"([a-z]{2,30})\s+(?:[a-z]\s+)?([a-z]{2,30})\b"
)
_PROMPT_LOWERCASE_NAME_AFTER_ACTION_RE = re.compile(
    # Deliberately lowercase-only and verb-scoped: widening the verb list or
    # making it case-insensitive false-refuses ordinary analytics phrasings
    # ("Find listed purchase opportunities" reads ("listed","purchase") as a
    # name). Capitalized proper nouns are _PROMPT_HUMAN_NAME_RE territory;
    # lowercase names after verbs outside this list (e.g. "prioritize maria
    # garcia") are a known gap awaiting a real name-detection pass, not more
    # regex.
    r"\b(?:for|find|show|review|run(?:\s+this)?\s+for|build\b.{0,80}\bfor)\s+"
    r"([a-z]{2,30})\s+(?:[a-z]\s+)?([a-z]{2,30})\b"
)
_PROMPT_LOWERCASE_NAME_SKIP_FIRST: frozenset[str] = frozenset(
    {
        "across",
        "after",
        "at",
        "borrowers",
        "both",
        "by",
        "customers",
        "for",
        "from",
        "in",
        "into",
        "near",
        "of",
        "on",
        "over",
        "prospects",
        "refi",
        "that",
        "the",
        "under",
        "weekly",
        "where",
        "who",
        "whose",
        "with",
        "without",
        # Closed-class English words that can never begin a personal name.
        # "for those signals" was flagged as a human name and 422'd a
        # legitimate compose objective (live false refusal, 2026-07-07).
        "these",
        "those",
        "this",
        "them",
        "they",
        "their",
        "our",
        "your",
        "all",
        "any",
        "some",
        "most",
        "more",
        "fewer",
        "each",
        "every",
        "few",
        "many",
        "much",
        "several",
        "such",
        "other",
        "another",
        "which",
        "what",
        "its",
        "new",
        "top",
        "high",
        "low",
        "strong",
        "strongest",
        "recent",
        "active",
    }
)
_PROMPT_REVIEWED_LOWERCASE_PAIRS: frozenset[tuple[str, str]] = frozenset(
    {
        ("branch", "review"),
        ("cash", "out"),
        ("current", "coverage"),
        ("current", "customer"),
        ("economic", "incentive"),
        ("for", "sale"),
        ("home", "equity"),
        ("prime", "refi"),
        ("prime", "refinance"),
        ("rate", "spread"),
        ("retention", "risk"),
        ("story", "dossiers"),
        ("story", "queue"),
    }
)
_PROMPT_LEADING_HUMAN_NAME_RE = re.compile(
    r"^(?!(?:find|show|list|run|build|open|review|count|check|create|save|how|what|which|"
    r"source|data|mortgage|growth|prime|home|high|daily|branch|heloc|HELOC|"
    r"genie|Genie|cotality|Cotality|databricks|Databricks)\b)"
    r"(?:[A-Z][a-z]{1,30}|[A-Z]{2,30})\s+"
    r"(?:[A-Z]\s+)?(?:[A-Z][a-z]{1,30}|[A-Z]{2,30})\b"
)
_PROMPT_PROTECTED_CLASS_RE = re.compile(
    r"\b(?:race|racial|ethnicity|ethnic|religion|religious|gender|sex|sexual|"
    r"national\s+origin|familial\s+status|marital\s+status|disability|disabled|"
    r"protected\s+class|age|color|public\s+assistance)\b",
    re.IGNORECASE,
)
_PROMPT_PROTECTED_AGE_PROXY_RE = re.compile(
    r"\b(?:elderly|older\s+(?:adults?|borrowers?|homeowners?|customers?|prospects?|people)|"
    r"retirement[-\s]?age|senior\s+citizens?)\b|"
    r"\b(?:borrowers?|homeowners?|customers?|prospects?|people|adults?)\s+"
    r"(?:over|under)\s+(?:age\s+)?(?:6[0-9]|7[0-9]|8[0-9]|9[0-9])"
    r"(?!\s*(?:bps?|basis\s+points?|percent|%|days?|weeks?|months?|score|points?))\b|"
    r"\b(?:over|under)[-\s](?:age[-\s])?(?:6[0-9]|7[0-9]|8[0-9]|9[0-9])"
    r"\s*(?:\+)?\s*(?:borrowers?|homeowners?|customers?|prospects?|people|adults?)\b|"
    r"\b(?:6[0-9]|7[0-9]|8[0-9]|9[0-9])\s*(?:\+|plus)\s*"
    r"(?:borrowers?|homeowners?|customers?|prospects?|people|adults?)\b|"
    r"\b(?:aged?|ages)\s+(?:6[0-9]|7[0-9]|8[0-9]|9[0-9])\b",
    re.IGNORECASE,
)
_PROMPT_JAILBREAK_RE = re.compile(
    r"(?:\bignore\s+(?:all\s+)?(?:previous|prior|system)\s+instructions\b|"
    r"\bignore\s+(?:the\s+)?safety\s+policy\b|"
    r"\bsystem\s+prompt\b|\bdeveloper\s+message\b|\bjailbreak\b|\bbypass\s+policy\b|"
    r"\bshow\s+all\s+tables\b|\blist\s+all\s+tables\b|\braw\s+(?:tables?|sources?|rows?)\b|"
    r"\b(?:use|query|read)\s+(?:the\s+)?silver\b|"
    r"\bcotality_mortgage_data\b|"
    r"\bselect\s+\*|\bdrop\s+table\b|\binsert\s+into\b|\bdelete\s+from\b|\bupdate\s+\w+\s+set\b)",
    re.IGNORECASE,
)
_PROMPT_UNAVAILABLE_SOURCE_RE = re.compile(
    r"\b(?:fico|credit\s+score|filed\s+permits?|building\s+permits?|permit\s+activity)\b",
    re.IGNORECASE,
)
_PROMPT_UNREVIEWED_LENDER_TARGET_RE = re.compile(
    r"\b(?:wells\s+fargo|rocket\s+mortgage|fairway|loan\s*depot|movement\s+mortgage|"
    r"chase|bank\s+of\s+america|td\s+bank)\s+(?:customers?|borrowers?|prospects?)\b",
    re.IGNORECASE,
)
_WORKFLOW_MONITOR_TITLE_RE = re.compile(
	r"^(?:Daily Refi Opportunity Brief|Listed-for-Sale Purchase Watch|"
	r"Competitor Recapture Monitor|High-Equity / HELOC Watch|"
	r"Borrower Dossier Review|Branch Manager Capacity Review|Source/Freshness Sentinel|"
	r"Custom Segment Workflow|Mortgage Growth Agent)"
	r"(?: - [A-Z]{2}(?:, [A-Z]{2}){0,19})?$"
)
_CUSTOM_WORKFLOW_MONITOR_TITLE_RE = re.compile(
    r"^Custom Segment Workflow - (?:(?:ANY|ALL) - )?"
    r"(?:ITM|LISTED|PERMIT|INVESTOR|EQUITY|RETENTION)"
    r"(?:\+(?:ITM|LISTED|PERMIT|INVESTOR|EQUITY|RETENTION)){0,5}"
    r"(?: - [A-Z]{2}(?:, [A-Z]{2}){0,19})?$"
)


def _default_notification_channels() -> list[GrowthAgentNotificationChannel]:
    return ["slack", "teams"]


def _contains_lowercase_name_after_group(clean: str) -> bool:
    """Catch uncommon lower-case names after borrower/person group nouns."""
    for match in _PROMPT_LOWERCASE_NAME_AFTER_GROUP_RE.finditer(clean):
        first, second = match.group(1).lower(), match.group(2).lower()
        if _looks_like_unreviewed_lowercase_name_pair(first, second):
            return True
    for match in _PROMPT_LOWERCASE_NAME_AFTER_ACTION_RE.finditer(clean):
        first, second = match.group(1).lower(), match.group(2).lower()
        if _looks_like_unreviewed_lowercase_name_pair(first, second):
            return True
    return False


def _contains_street_address_without_suffix(clean: str) -> bool:
    """Catch address-like text without blocking ordinary numeric rank prompts."""

    match = _PROMPT_STREET_ADDRESS_NO_SUFFIX_RE.search(clean)
    if not match:
        return False
    prefix = clean[max(0, match.start() - 24) : match.start()]
    return bool(_PROMPT_ADDRESS_CONTEXT_RE.search(prefix))


def _looks_like_unreviewed_lowercase_name_pair(first: str, second: str) -> bool:
    if first in _PROMPT_LOWERCASE_NAME_SKIP_FIRST:
        return False
    if (first, second) in _PROMPT_REVIEWED_LOWERCASE_PAIRS:
        return False
    return second not in {
        "and",
        "candidates",
        "cohort",
        "coverage",
        "customers",
        "leads",
        "opportunities",
        "or",
        "prospects",
        "queue",
        "workflow",
    }


def default_growth_agent_cadences() -> list[GrowthAgentCadence]:
    return ["daily", "weekly"]


def assert_reviewed_growth_objective(value: str) -> str:
    """Normalize + reject a natural-language mortgage-growth objective.

    Shared by the selection path (``GrowthAgentPromptRunRequest.prompt``) and the
    composition path (``ComposePlanRequest.objective``) so both surfaces enforce
    the identical PII, protected-class, raw-identifier, and prompt-injection
    guards before any model planner sees the text. Returns the whitespace-
    normalized objective; raises ``ValueError`` when the text is not reviewed.
    """

    clean = re.sub(r"\s+", " ", value.strip())
    if (
        contains_pii_marker(clean)
        or _RAW_IDENTIFIER_RE.search(clean)
        or _PROMPT_STREET_ADDRESS_RE.search(clean)
        or _contains_street_address_without_suffix(clean)
        or _PROMPT_HUMAN_NAME_RE.search(clean)
        or _PROMPT_COMMON_NAME_RE.search(clean)
        or _PROMPT_LEADING_HUMAN_NAME_RE.search(clean)
        or _contains_lowercase_name_after_group(clean)
        or _PROMPT_PROTECTED_CLASS_RE.search(clean)
        or _PROMPT_PROTECTED_AGE_PROXY_RE.search(clean)
        or _PROMPT_JAILBREAK_RE.search(clean)
        or _PROMPT_UNAVAILABLE_SOURCE_RE.search(clean)
        or _PROMPT_UNREVIEWED_LENDER_TARGET_RE.search(clean)
    ):
        raise ValueError("prompt must use reviewed, non-PII mortgage-growth criteria")
    return clean


class GrowthAgentWorkflow(BaseModel):
    id: GrowthAgentWorkflowId
    title: str
    objective: str
    trigger_label: str
    action_label: str
    source_assets: list[str]
    default_route: str
    proof_points: list[str]
    cadence_options: list[GrowthAgentCadence] = Field(default_factory=default_growth_agent_cadences)


class GrowthAgentToolStep(BaseModel):
    label: str
    status: Literal["completed", "blocked", "review_required"]
    detail: str
    source_asset: str | None = None
    tool_name: str | None = None
    result_hash: str | None = None


class GrowthAgentPolicyCheck(BaseModel):
    label: str
    status: Literal["passed", "review_required", "blocked"]
    detail: str


class GrowthAgentGovernanceChip(BaseModel):
    label: str
    status: Literal["passed", "review_required", "roadmap", "not_provisioned", "not_attached"]
    detail: str
    evidence_ref: str | None = None


class GrowthAgentMonitor(BaseModel):
    monitor_id: str
    workflow_id: GrowthAgentWorkflowId
    name: str
    cadence: GrowthAgentCadence
    status: Literal["active", "paused", "disabled"] = "active"
    criteria: dict[str, object]
    route: str
    actionable_total: int = Field(ge=0)
    source_assets: list[str]
    last_run_id: str | None = None
    created_at: datetime | str | None = None
    updated_at: datetime | str | None = None


class GrowthAgentNotificationDraft(BaseModel):
    draft_id: str
    monitor_id: str
    run_id: str
    channel: GrowthAgentNotificationChannel
    title: str
    body: str
    status: Literal["draft", "reviewed", "cancelled"] = "draft"
    created_at: datetime | str | None = None
    updated_at: datetime | str | None = None


class GrowthAgentRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    states: list[str] = Field(default_factory=list, max_length=20)
    save_monitor: bool = False
    cadence: GrowthAgentCadence = "daily"
    monitor_name: str | None = Field(default=None, max_length=80)
    request_id: str | None = None

    @field_validator("states")
    @classmethod
    def _states(cls, values: list[str]) -> list[str]:
        out: list[str] = []
        for value in values:
            state = str(value).strip().upper()
            if not state:
                continue
            if not _STATE_RE.fullmatch(state) or not is_usps_state_code(state):
                raise ValueError("states must contain valid USPS state codes")
            if state not in out:
                out.append(state)
        return out

    @field_validator("monitor_name")
    @classmethod
    def _monitor_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = re.sub(r"\s+", " ", value.strip())
        if not clean:
            return None
        if contains_pii_marker(clean) or _RAW_IDENTIFIER_RE.search(clean):
            raise ValueError("monitor_name must be a public-safe workflow label")
        if _WORKFLOW_MONITOR_TITLE_RE.fullmatch(clean) or _CUSTOM_WORKFLOW_MONITOR_TITLE_RE.fullmatch(clean):
            return clean
        if not _MONITOR_NAME_RE.fullmatch(clean):
            raise ValueError("monitor_name must be a public-safe workflow label")
        try:
            validate_public_campaign_label(clean, field_name="monitor_name")
        except ValueError as exc:
            raise ValueError("monitor_name must be a public-safe workflow label") from exc
        return clean

    @field_validator("request_id")
    @classmethod
    def _request_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_public_opaque_id(value)


class GrowthAgentMonitorDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channels: list[GrowthAgentNotificationChannel] = Field(default_factory=_default_notification_channels)
    request_id: str | None = None

    @field_validator("channels")
    @classmethod
    def _channels(cls, values: list[GrowthAgentNotificationChannel]) -> list[GrowthAgentNotificationChannel]:
        deduped: list[GrowthAgentNotificationChannel] = []
        for value in values:
            if value not in deduped:
                deduped.append(value)
        if not deduped:
            raise ValueError("channels must include slack or teams")
        return deduped[:2]

    @field_validator("request_id")
    @classmethod
    def _draft_request_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_public_opaque_id(value)


class GrowthAgentDueMonitorRunRequest(GrowthAgentMonitorDraftRequest):
    limit: int = Field(default=5, ge=1, le=20)


class GrowthAgentCustomRunRequest(GrowthAgentRunRequest):
    segment_codes: list[GrowthAgentSegmentCode] = Field(min_length=1, max_length=6)
    segment_mode: GrowthAgentSegmentMode = "any"

    @field_validator("segment_codes")
    @classmethod
    def _segment_codes(cls, values: list[GrowthAgentSegmentCode]) -> list[GrowthAgentSegmentCode]:
        out: list[GrowthAgentSegmentCode] = []
        for value in values:
            if value not in out:
                out.append(value)
        if not out:
            raise ValueError("segment_codes must include at least one reviewed segment")
        return out


class GrowthAgentPromptRunRequest(GrowthAgentRunRequest):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    prompt: str = Field(min_length=3, max_length=500)
    segment_codes: list[GrowthAgentSegmentCode] = Field(default_factory=list, max_length=6)
    segment_mode: GrowthAgentSegmentMode = "any"

    @field_validator("prompt")
    @classmethod
    def _prompt(cls, value: str) -> str:
        return assert_reviewed_growth_objective(value)

    @field_validator("segment_codes")
    @classmethod
    def _prompt_segment_codes(cls, values: list[GrowthAgentSegmentCode]) -> list[GrowthAgentSegmentCode]:
        out: list[GrowthAgentSegmentCode] = []
        for value in values:
            if value not in out:
                out.append(value)
        return out


class GrowthAgentRunResponse(BaseModel):
    workflow: GrowthAgentWorkflow
    run_id: str
    monitor: GrowthAgentMonitor | None = None
    specialist_agent: GrowthAgentSpecialist
    execution_mode: GrowthAgentExecutionMode = "deterministic"
    trace_kind: GrowthAgentTraceKind = "local_hash"
    planner_label: str = "Reviewed deterministic planner"
    trace_id: str
    tool_result_hash: str
    broad_label: str = "Broad opportunity"
    actionable_label: str = "Eligible subset"
    broad_total: int = Field(ge=0)
    actionable_total: int = Field(ge=0)
    broad_avg_score: float | None = None
    actionable_avg_score: float | None = None
    avg_rate_spread_bps: float | None = None
    avg_equity_pct: float | None = None
    route: str
    criteria: dict[str, object]
    source_assets: list[str]
    tool_steps: list[GrowthAgentToolStep]
    policy_checks: list[GrowthAgentPolicyCheck]
    governance_chips: list[GrowthAgentGovernanceChip] = Field(default_factory=list)
    interpreted_intent: str | None = None
    agent_reasoning: str | None = None
    genie_conversation_id: str | None = None
    genie_message_id: str | None = None
    genie_question_hash: str | None = None
    genie_sql_hash: str | None = None
    genie_row_count: int | None = Field(default=None, ge=0)
    genie_trusted_assets: list[str] = Field(default_factory=list)
    audit_event_id: str | None = None
    created_at: datetime | str | None = None


class GrowthAgentHomeResponse(BaseModel):
    workflows: list[GrowthAgentWorkflow]
    monitors: list[GrowthAgentMonitor]
    capabilities: list[dict[str, object]] = Field(default_factory=list)


class GrowthAgentDueMonitorRunResponse(BaseModel):
    runs: list[GrowthAgentRunResponse]
    drafts: list[GrowthAgentNotificationDraft]
    due_count: int = Field(ge=0)
    actor_count: int = Field(default=1, ge=0)
