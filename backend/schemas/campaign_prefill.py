"""Typed campaign-draft prefill contract for geo → campaigns handoff (S9).

The geography drill-down's "Start campaign" affordance seeds the
EXISTING campaigns surface (the Portfolio Builder, which owns campaign
creation today) with a typed draft context. S10 (the dedicated campaign
builder) is not built yet — this module IS the wire contract it will
consume, so the encoding is versioned by parameter names, validated on
both sides, and unit-pinned:

* ``states=XX`` reuses the Portfolio Builder's existing deep-link
  parameter, so the state predicate applies to the build immediately.
* ``prefill_*`` parameters carry the drill context (county / ZIP /
  segment carry-over) that today's surface can only display as draft
  context. The UI copy must stay honest about that boundary: county/ZIP
  narrowing becomes a build predicate when S10 lands, not before.

Python mirror of ``frontend/src/lib/campaignPrefill.ts`` — keep the two
in lockstep (same parameter names, same validation).
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.schemas.lead import SEGMENT_CODE_VALUES

PREFILL_SOURCE: Literal["geo-drilldown"] = "geo-drilldown"

# Wire parameter names — the versioned contract S10 will read.
PARAM_SOURCE = "prefill_source"
PARAM_LEVEL = "prefill_level"
PARAM_STATES = "states"  # existing Portfolio Builder deep-link param
PARAM_COUNTY_FIPS = "prefill_county_fips"
PARAM_COUNTY_NAME = "prefill_county_name"
PARAM_ZIP = "prefill_zip"
PARAM_SEGMENTS = "prefill_segments"
PARAM_SEGMENT_MODE = "prefill_segment_mode"
PARAM_LEAD_COUNT = "prefill_lead_count"
PARAM_UNATTENDED_COUNT = "prefill_unattended_count"

_STATE_RE = re.compile(r"^[A-Z]{2}$")
_FIPS_RE = re.compile(r"^\d{5}$")
_ZIP_RE = re.compile(r"^\d{5}$")
_ALLOWED_SEGMENTS = frozenset(SEGMENT_CODE_VALUES)


class CampaignGeoPrefill(BaseModel):
    """One drilled geography unit, carried into a campaign draft."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["geo-drilldown"] = PREFILL_SOURCE
    level: Literal["state", "county", "zip"]
    state: str = Field(description="2-char USPS state code of the drilled unit.")
    county_fips: str | None = Field(
        default=None, description="5-char county FIPS; set at county and zip levels."
    )
    county_name: str | None = Field(
        default=None, max_length=64, description="Display name for the county chip."
    )
    zip: str | None = Field(default=None, description="5-digit ZIP; set at zip level.")
    segment_codes: list[str] = Field(
        default_factory=list,
        description="Segment filter active on the map when the draft was started.",
    )
    segment_mode: Literal["any", "all"] = "any"
    lead_count: int | None = Field(
        default=None, ge=0, description="Unit lead count snapshot at draft time."
    )
    unattended_count: int | None = Field(
        default=None, ge=0, description="Unit unattended count snapshot at draft time."
    )

    @field_validator("state")
    @classmethod
    def _state_code(cls, value: str) -> str:
        code = str(value or "").strip().upper()
        if not _STATE_RE.fullmatch(code):
            raise ValueError("state must be a two-letter USPS code")
        return code

    @field_validator("county_fips")
    @classmethod
    def _fips(cls, value: str | None) -> str | None:
        if value is None:
            return None
        fips = str(value).strip()
        if not _FIPS_RE.fullmatch(fips):
            raise ValueError("county_fips must be a 5-digit county FIPS code")
        return fips

    @field_validator("zip")
    @classmethod
    def _zip5(cls, value: str | None) -> str | None:
        if value is None:
            return None
        zip5 = str(value).strip()
        if not _ZIP_RE.fullmatch(zip5):
            raise ValueError("zip must be a 5-digit ZIP code")
        return zip5

    @field_validator("segment_codes")
    @classmethod
    def _segments(cls, values: list[str]) -> list[str]:
        out: list[str] = []
        for raw in values:
            code = str(raw or "").strip().lower()
            if not code or code in out:
                continue
            if code not in _ALLOWED_SEGMENTS:
                raise ValueError(f"unknown segment code: {code}")
            out.append(code)
        return out

    @model_validator(mode="after")
    def _level_requires_parents(self) -> CampaignGeoPrefill:
        if self.level in {"county", "zip"} and not self.county_fips:
            raise ValueError("county_fips is required at county and zip levels")
        if self.level == "zip" and not self.zip:
            raise ValueError("zip is required at zip level")
        if self.level == "state" and (self.county_fips or self.zip):
            raise ValueError("state-level prefill must not carry county_fips or zip")
        return self

    def to_query_params(self) -> dict[str, str]:
        """Encode for a /portfolio-builder deep link (S10-stable)."""
        params: dict[str, str] = {
            PARAM_SOURCE: self.source,
            PARAM_LEVEL: self.level,
            PARAM_STATES: self.state,
        }
        if self.county_fips:
            params[PARAM_COUNTY_FIPS] = self.county_fips
        if self.county_name:
            params[PARAM_COUNTY_NAME] = self.county_name
        if self.zip:
            params[PARAM_ZIP] = self.zip
        if self.segment_codes:
            params[PARAM_SEGMENTS] = ",".join(self.segment_codes)
            params[PARAM_SEGMENT_MODE] = self.segment_mode
        if self.lead_count is not None:
            params[PARAM_LEAD_COUNT] = str(self.lead_count)
        if self.unattended_count is not None:
            params[PARAM_UNATTENDED_COUNT] = str(self.unattended_count)
        return params

    @classmethod
    def from_query_params(
        cls, params: Mapping[str, str]
    ) -> CampaignGeoPrefill | None:
        """Decode a query-parameter mapping.

        Returns ``None`` when the prefill marker is absent (an ordinary
        deep link, not a geo handoff). Raises ``ValueError`` on a marked
        but malformed payload — callers surface that instead of silently
        applying half a context.
        """
        if (params.get(PARAM_SOURCE) or "").strip() != PREFILL_SOURCE:
            return None
        segments_raw = (params.get(PARAM_SEGMENTS) or "").strip()

        def _int_or_none(key: str) -> int | None:
            raw = (params.get(key) or "").strip()
            if not raw:
                return None
            if not raw.isdigit():
                raise ValueError(f"{key} must be a non-negative integer")
            return int(raw)

        return cls(
            level=(params.get(PARAM_LEVEL) or "").strip(),  # type: ignore[arg-type]
            state=(params.get(PARAM_STATES) or "").split(",")[0],
            county_fips=(params.get(PARAM_COUNTY_FIPS) or "").strip() or None,
            county_name=(params.get(PARAM_COUNTY_NAME) or "").strip() or None,
            zip=(params.get(PARAM_ZIP) or "").strip() or None,
            segment_codes=[s for s in segments_raw.split(",") if s.strip()],
            segment_mode=(
                (params.get(PARAM_SEGMENT_MODE) or "any").strip().lower()  # type: ignore[arg-type]
            ),
            lead_count=_int_or_none(PARAM_LEAD_COUNT),
            unattended_count=_int_or_none(PARAM_UNATTENDED_COUNT),
        )
