"""Campaign intelligence and portfolio economics response contracts."""

import re
import sys
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.schemas._validators import (
    assert_no_protected_class_marketing_text,
    configured_public_lender_name,
    contains_contextual_human_name,
    contains_human_name_shape,
    contains_mechanical_pii_or_raw_identifier,
    contains_prompt_injection_text,
)

if TYPE_CHECKING:
    from backend.schemas.portfolio import PortfolioCriteria


_PUBLIC_TEXT_DENYLIST: tuple[str, ...] = (
    r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}",
    r"\b(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}\b",
    r"\b\d{3}-\d{2}-\d{4}\b",
    r"\b\d{9,}\b",
    r"\b\d{1,6}\s+[A-Za-z0-9.'-]+\s+(?:st|street|ave|avenue|rd|road|dr|drive|ln|lane|blvd|boulevard|ct|court|way)\b",
    r"\b(?:raw[_\s-]?clip|owner[_\s-]?name|borrower[_\s-]?name|customer[_\s-]?name|prospect[_\s-]?name|street[_\s-]?address|mailing[_\s-]?address)\b",
    r"\[(?:first|last|full)[_\s-]?name\]",
    r"\{(?:first|last|full)[_\s-]?name\}",
    r"\binsert governed\b",
)
_HUMAN_NAME_SHAPE_RE = re.compile(r"\b[A-Z][a-z]{1,30}\s+(?:[A-Z]\s+)?[A-Z][a-z]{1,30}\b")
_PUBLIC_TITLECASE_PHRASE_ALLOWLIST: tuple[str, ...] = (
    "Summit Mortgage",
    "Equal Housing",
    "Building Permits",
    "New York",
    "New Jersey",
    "New Mexico",
    "North Carolina",
    "North Dakota",
    "South Carolina",
    "South Dakota",
    "Rhode Island",
    "West Virginia",
    "United States",
)
_BORROWER_COPY_UNSUPPORTED_CLAIM_RE = re.compile(
    r"(?:\$|\b\d+(?:\.\d+)?\s*(?:%|percent|bps|basis points?|dollars?)\b|"
    r"\b(?:guarantee(?:d)?|pre[- ]?approved|lowest rate|best rate|save money|"
    r"qualif(?:y|ies|ied)(?:\s+for)?|lower (?:your )?(?:monthly )?payment|"
    r"you(?:'re| are| may be| can be) (?:eligible|approved|qualified)|"
    r"your (?:monthly )?payment (?:will|would|can|could) (?:be )?lower|"
    r"instant approval|act now|urgent|limited time|expires? today|final notice)\b|"
    r"\b(?:score|scoring|ranked|ranking|algorithm|model(?:ed)?|propensity|"
    r"segment|signal|trigger|target(?:ed|ing)?|eligible cohort|public record)\b)",
    re.IGNORECASE,
)
_SUMMARY_NUMERIC_CLAIM_RE = re.compile(
    r"(?:[$€£]|\b\d+(?:[,.]\d+)*(?:\.\d+)?\b|\b(?:percent|percentage|bps|basis points?|dollars?)\b)",
    re.IGNORECASE,
)
_BORROWER_COPY_CTA_RE = re.compile(
    r"\b(?:review|schedule|talk|speak|reply|learn|compare|explore|contact|call|discuss)\b",
    re.IGNORECASE,
)


def assert_public_campaign_text(value: object, *, field_name: str, max_length: int) -> str:
    """Normalize campaign text and reject PII-shaped or unresolved content."""

    if value is None:
        return ""
    text = re.sub(r"\s+", " ", str(value).strip())
    if len(text) > max_length:
        raise ValueError(f"{field_name} must be {max_length} characters or fewer")
    if contains_mechanical_pii_or_raw_identifier(text) or any(
        re.search(pattern, text, re.IGNORECASE) for pattern in _PUBLIC_TEXT_DENYLIST
    ):
        raise ValueError(
            f"{field_name} cannot contain PII, raw identifiers, or unresolved placeholders"
        )
    name_scan_text = remove_allowed_public_titlecase_phrases(text)
    if contains_prompt_injection_text(text):
        raise ValueError(f"{field_name} cannot contain instruction-override language")
    assert_no_protected_class_marketing_text(text, field_name=field_name)
    if (
        _HUMAN_NAME_SHAPE_RE.search(name_scan_text)
        or contains_contextual_human_name(name_scan_text)
        or contains_human_name_shape(name_scan_text)
    ):
        raise ValueError(f"{field_name} cannot contain human-name-shaped text")
    return text


def assert_borrower_campaign_copy(value: str, *, field_name: str) -> str:
    """Reject unsupported or internally framed borrower-facing campaign copy."""

    if _BORROWER_COPY_UNSUPPORTED_CLAIM_RE.search(value):
        raise ValueError(f"{field_name} contains an unsupported borrower-facing claim")
    if field_name.endswith("body") and not _BORROWER_COPY_CTA_RE.search(value):
        raise ValueError(f"{field_name} must include a clear review or contact call to action")
    return value


def remove_allowed_public_titlecase_phrases(value: str) -> str:
    """Remove reviewed business/place names before human-name-shape checks."""

    cleaned = value
    for allowed in (*_PUBLIC_TITLECASE_PHRASE_ALLOWLIST, configured_public_lender_name()):
        cleaned = cleaned.replace(allowed, "")
    return cleaned


def _default_portfolio_criteria() -> "PortfolioCriteria":
    # Delayed import keeps this focused schema module independent while
    # preserving the request contract's empty-body default.
    from backend.schemas.portfolio import PortfolioCriteria

    return PortfolioCriteria()


class PortfolioOfferMixRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    offer_code: Literal[
        "purchase",
        "refi_plus_heloc",
        "heloc",
        "refi",
        "cash_out",
        "investor",
        "retention",
        "nurture",
    ]
    borrower_count: int = Field(ge=0)


class CampaignRecommendationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criteria: "PortfolioCriteria" = Field(default_factory=_default_portfolio_criteria)


class CampaignRecommendationVariant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    variant_name: Literal["Benefit-led", "Guidance-led"]
    subject: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=1000)
    hypothesis: str = Field(min_length=1, max_length=280)
    provenance_token: str | None = Field(default=None, min_length=32, max_length=4096)

    @field_validator("subject", "body", "hypothesis")
    @classmethod
    def _validate_public_copy(cls, value: str, info) -> str:
        text = assert_public_campaign_text(
            value,
            field_name=f"campaign recommendation {info.field_name}",
            max_length={"subject": 120, "body": 1000, "hypothesis": 280}[info.field_name],
        )
        if info.field_name in {"subject", "body"}:
            assert_borrower_campaign_copy(
                text,
                field_name=f"campaign recommendation {info.field_name}",
            )
        return text


class CampaignRecommendationEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=120)
    source_asset: Literal[
        "mip.semantics.portfolio_headline_metric_view",
        "mip.gold.borrower_360",
        "mip_app.call_dispositions",
        "mip_app.lead_outcomes",
    ]

    @field_validator("label", "value")
    @classmethod
    def _validate_public_evidence(cls, value: str, info) -> str:
        return assert_public_campaign_text(
            value,
            field_name=f"campaign evidence {info.field_name}",
            max_length={"label": 80, "value": 120}[info.field_name],
        )


class CampaignRecommendationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generation_mode: Literal["supervisor", "reviewed_fallback"]
    generator_label: str = Field(min_length=1, max_length=80)
    performance_status: Literal["qualified", "insufficient_sample", "unavailable"]
    audience_summary: str = Field(min_length=1, max_length=280)
    strategy: str = Field(min_length=1, max_length=500)
    variants: list[CampaignRecommendationVariant] = Field(min_length=2, max_length=2)
    holdout_pct: float = Field(ge=5, le=30)
    evidence: list[CampaignRecommendationEvidence] = Field(min_length=1, max_length=8)
    warnings: list[str] = Field(default_factory=list, max_length=4)

    @field_validator("generator_label", "audience_summary", "strategy")
    @classmethod
    def _validate_public_summary(cls, value: str, info) -> str:
        text = assert_public_campaign_text(
            value,
            field_name=f"campaign recommendation {info.field_name}",
            max_length={"generator_label": 80, "audience_summary": 280, "strategy": 500}[
                info.field_name
            ],
        )
        if info.field_name in {"audience_summary", "strategy"} and _SUMMARY_NUMERIC_CLAIM_RE.search(
            text
        ):
            raise ValueError(
                f"campaign recommendation {info.field_name} must keep numeric facts in evidence"
            )
        return text

    @field_validator("warnings")
    @classmethod
    def _validate_warnings(cls, value: list[str]) -> list[str]:
        return [
            assert_public_campaign_text(item, field_name="campaign warning", max_length=240)
            for item in value
        ]


def bind_portfolio_criteria(criteria_type: type[BaseModel]) -> None:
    """Resolve the request model after the core portfolio criteria exists."""

    CampaignRecommendationRequest.model_rebuild(
        _types_namespace={"PortfolioCriteria": criteria_type},
    )


# Support direct imports of this focused module as well as the legacy
# backend.schemas.portfolio re-export. During a portfolio-first import, the
# parent module performs the bind after defining PortfolioCriteria.
if "backend.schemas.portfolio" not in sys.modules:
    from backend.schemas.portfolio import PortfolioCriteria as _PortfolioCriteria

    bind_portfolio_criteria(_PortfolioCriteria)
