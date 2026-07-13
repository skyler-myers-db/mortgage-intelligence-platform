"""Input and model-output safety policy for the Genie message route."""

import re

from pydantic import BaseModel, Field, field_validator

from backend.schemas._validators import (
    contains_human_name_shape,
    contains_protected_class_marketing_text,
    contains_unsafe_ai_text,
)
from backend.services.genie_answers import GenieMessageResponse


class GenieMessageRequest(BaseModel):
    question: str = Field(min_length=1)
    conversation_id: str | None = None

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
    re.compile(
        r"(?<![a-z0-9])(?:white|black)\s+"
        r"(?:plains|settlement|salmon|center|creek|river|falls|rock|oaks?|"
        r"haven|bluffs?|stone|mountain|hills?|city|county|lake|earth|water|"
        r"sands?|house|hall|bear|fish|hawk|diamond)(?![a-z0-9])",
        re.IGNORECASE,
    ),
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
    if contains_protected_class_marketing_text(scannable):
        return "protected_class_language"
    return None


def identity_prompt_match(question: str) -> bool:
    """Reject person-name-shaped prompts before they enter session state."""

    return contains_human_name_shape(_mask_safe_phrases(question))


def genie_response_has_unsafe_visible_text(response: GenieMessageResponse) -> bool:
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
    return any(contains_unsafe_ai_text(value) for value in values)
