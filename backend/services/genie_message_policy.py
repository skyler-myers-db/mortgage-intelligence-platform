"""Input and model-output safety policy for the Genie message route."""

import re
from collections.abc import Mapping, Sequence

from pydantic import BaseModel, Field, field_validator

from backend.schemas._validators_person_names import contains_human_name_shape
from backend.schemas._validators_protected_class import (
    contains_protected_class_marketing_text,
    contains_protected_class_proxy_marketing_text,
)
from backend.schemas._validators_unsafe_text import contains_unsafe_ai_text
from backend.services.genie_answers import GenieMessageResponse


class GenieMessageRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4_000)
    conversation_id: str | None = Field(default=None, max_length=256)

    @field_validator("question")
    @classmethod
    def _question_must_contain_text(cls, value: str) -> str:
        normalized = re.sub(r"\s+", " ", value).strip()
        if not normalized:
            raise ValueError("question is required")
        return normalized


class GenieProgressRequest(BaseModel):
    """Poll one in-flight Genie message (async lifecycle).

    POST body (not GET query params) so the signed token never lands in
    access logs or proxy URL captures.
    """

    conversation_id: str = Field(min_length=1, max_length=256)
    message_id: str = Field(min_length=1, max_length=256)
    progress_token: str = Field(min_length=1, max_length=4_096)


class GenieCompleteRequest(BaseModel):
    """Finish an in-flight Genie message into a governed answer.

    ``question`` must hash-match the token minted at submit, which makes the
    guarded prompt and the completed answer cryptographically the same turn.
    """

    conversation_id: str = Field(min_length=1, max_length=256)
    message_id: str = Field(min_length=1, max_length=256)
    progress_token: str = Field(min_length=1, max_length=4_096)
    question: str = Field(min_length=1, max_length=4_000)

    @field_validator("question")
    @classmethod
    def _question_must_contain_text(cls, value: str) -> str:
        normalized = re.sub(r"\s+", " ", value).strip()
        if not normalized:
            raise ValueError("question is required")
        return normalized


_PROTECTED_PROMPT_TERMS = (
    "age",
    "asian",
    "black",
    "disability",
    "disabled",
    "ethnic",
    "ethnicity",
    "familial status",
    "female",
    "gender",
    "hispanic",
    "latino",
    "latina",
    "male",
    "marital status",
    "national origin",
    "native american",
    "pacific islander",
    "pregnant",
    "race",
    "religion",
    "religious",
    "sex",
    "sexual orientation",
    "white",
    "woman",
    "women",
)

# Narrow exemptions for mortgage vocabulary and geographic proper nouns. The
# rest of each question remains subject to the protected-class scan.
_SAFE_PHRASE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?<![a-z0-9])loan ages?(?![a-z0-9])", re.IGNORECASE),
    re.compile(r"(?<![a-z0-9])ages? of (?:the )?loans?(?![a-z0-9])", re.IGNORECASE),
    re.compile(r"(?<![a-z0-9])loan aging(?![a-z0-9])", re.IGNORECASE),
    re.compile(r"(?<![a-z0-9])lien ages?(?![a-z0-9])", re.IGNORECASE),
    # The geography router treats this reviewed wording as country scope,
    # not borrower national origin. Campaign/outreach validators do not carry
    # this exemption.
    re.compile(
        r"(?<![a-z0-9])canadian borrowers by (?:zip|postal code)(?![a-z0-9])",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<![a-z0-9])(?:white|black)\s+"
        r"(?:plains|settlement|salmon|center|creek|river|falls|rock|oaks?|"
        r"haven|bluffs?|stone|mountain|hills?|city|county|lake|earth|water|"
        r"sands?|house|hall|bear|fish|hawk|diamond)(?![a-z0-9])",
        re.IGNORECASE,
    ),
    # Governed offer connectors (live probe 2026-08-06): "candidates with
    # offers" / "with what offer" is core product phrasing, but the trailing
    # "with" reads as an audience-criterion connector to the campaign clause
    # machine and refused a plain HELOC ranking ask. Masking only these
    # literal offer connectors keeps unknown-term laundering ("carry zyrplax")
    # fully scannable.
    re.compile(r"(?<![a-z0-9])with (?:what )?offers?(?![a-z0-9])", re.IGNORECASE),
    re.compile(r"(?<![a-z0-9])heloc[- ]eligible(?![a-z0-9])", re.IGNORECASE),
)


def _mask_safe_phrases(question: str) -> str:
    masked = question
    for pattern in _SAFE_PHRASE_PATTERNS:
        masked = pattern.sub(lambda match: " " * len(match.group(0)), masked)
    return masked


def protected_prompt_match(question: str) -> str | None:
    scannable = _mask_safe_phrases(question)
    for term in _PROTECTED_PROMPT_TERMS:
        pattern = r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])"
        if re.search(pattern, scannable, flags=re.IGNORECASE):
            return term
    if contains_protected_class_proxy_marketing_text(scannable):
        return "protected_class_proxy"
    if contains_protected_class_marketing_text(scannable):
        return "protected_class_language"
    return None


def identity_prompt_match(question: str) -> bool:
    """Reject person-name-shaped prompts before they enter session state."""

    return contains_human_name_shape(_mask_safe_phrases(question))


def _visible_text_values(value: object) -> list[str]:
    """Flatten rendered response values, including dynamic table keys and cells."""

    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        values: list[str] = []
        for key, item in value.items():
            values.extend(_visible_text_values(key))
            values.extend(_visible_text_values(item))
        return values
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        values = []
        for item in value:
            values.extend(_visible_text_values(item))
        return values
    return []


def _without_allowed_literals(value: str, allowed_literals: Sequence[str]) -> str:
    scrubbed = value
    for literal in sorted(
        {item.strip() for item in allowed_literals if item.strip()},
        key=len,
        reverse=True,
    ):
        scrubbed = re.sub(re.escape(literal), " governed_staff_label ", scrubbed)
    return scrubbed


# "City Name, ST" / "(City Name, ST)" geography references in Genie prose.
# Borrower rows carry the same city/state strings, so citing them in a
# narrative is sanctioned analytics output — but title-case city names
# ("Lake Forest, CA") pattern-match the human-name-shape guard. Strip the
# geography shape before the name-shape scan only. No real-person identity
# can take this shape here: borrower names never render, and display
# identities are synthetic masked IDs.
GENIE_GEO_LOCATION_RE = re.compile(
    r"\(?\b[A-Z][A-Za-z.'-]*(?:\s+[A-Z][A-Za-z.'-]*){0,3},\s*[A-Z]{2}\b\)?"
)

# A parenthetical immediately after a masked borrower ID is always the
# borrower's CITY in this product ("**B-0YINYSXBPWZBF** (Miramar): ...") —
# borrower names never render and masked IDs are the only identity. City-only
# forms lack the ", ST" the geography pattern above needs, so strip them here
# before the name-shape scan.
_MASKED_ID_CITY_PARENS_RE = re.compile(
    r"(B-[0-9A-Z]{13}\*{0,2}[\s:,·—-]*)\(([A-Z][^)]{1,40})\)"
)


def genie_visible_text_unsafe(value: str, *, structured_value: bool = False) -> bool:
    """Fail-closed scan for one Genie-rendered string on the analytics surface.

    Ask Genie output is a read-only analytics narrative, not campaign copy:
    ranking vocabulary ("candidates are those with the highest opportunity
    scores") is the product's core language, so the campaign audience-formation
    criterion machine is bypassed. Every PII, injection, confidential,
    health-status, and direct protected-class detector stays on.

    ``structured_value`` marks governed table-cell values (already key-redacted
    gold columns such as city or offer labels). Those keep the mechanical-PII,
    injection, and protected-class scans but skip the title-case human-name
    heuristic, which can only false-positive on structured values ("El Paso",
    "San Antonio", "Purchase Mortgage") — gold rows carry no name columns after
    redaction.
    """

    scannable = GENIE_GEO_LOCATION_RE.sub(" ", value)
    scannable = _MASKED_ID_CITY_PARENS_RE.sub(r"\1 ", scannable)
    return contains_unsafe_ai_text(
        scannable,
        include_titlecase=not structured_value,
        assume_reviewed_read_only_analytics=True,
    )


def genie_response_has_unsafe_visible_text(
    response: GenieMessageResponse,
    *,
    allowed_literals: Sequence[str] = (),
) -> bool:
    """Check every model-authored text field rendered by the Genie UI."""

    values = [response.answer, *response.follow_up_questions]
    values.extend(step.kind for step in response.reasoning_trace)
    values.extend(step.content for step in response.reasoning_trace)
    if response.proof is not None:
        values.extend(step.kind for step in response.proof.reasoning_trace)
        values.extend(step.content for step in response.proof.reasoning_trace)
    if response.native_visualization is not None and response.native_visualization.title:
        values.append(response.native_visualization.title)
    if response.visualization is not None:
        values.extend(
            value
            for value in (
                response.visualization.title,
                response.visualization.reason,
                response.visualization.x,
                response.visualization.y,
                response.visualization.series,
            )
            if value
        )
    row_values = _visible_text_values(response.table_rows or [])
    return any(
        genie_visible_text_unsafe(_without_allowed_literals(value, allowed_literals))
        for value in values
    ) or any(
        genie_visible_text_unsafe(
            _without_allowed_literals(value, allowed_literals),
            structured_value=True,
        )
        for value in row_values
    )
