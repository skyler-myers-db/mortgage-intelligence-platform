"""The closed Genie page-context model (audit 2026-09-21 genie-04, phase 2).

Phase 1 prefills the floating Genie panel from client-side templates that can
name one state and/or one segment. Everything else an empty Lead Queue can be
filtered to (a segment intersection, a county, a ZIP, a governed city, a
Competitor lender ref) needs vocabulary only the server holds: the URL carries
a county FIPS code, never a county name, and the governed city set is
registered at runtime from live coverage.

So the browser sends CODES, never text, and the server renders the reviewed
sentence (``backend/services/genie_context_scope.py``). Every field below is a
closed vocabulary: an enum, a fixed-width digit code, a ``CITY~ST`` key or the
public Competitor pattern. There is no free-text field, and ``extra='forbid'``
keeps anything else (a borrower id, an email, a cohort id, a criterion) off the
wire. The assignee slot exists only so the server can say it was left out.

This module imports schemas only (``test_schemas_do_not_import_runtime_services``).
"""

from __future__ import annotations

import re
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.schemas.usps import USPS_STATE_CODES

#: The frontend segment registry's codes (``frontend/src/lib/segmentMetadata.ts``),
#: pinned by ``tests/unit/test_genie_context_model.py``.
GenieContextSegmentCode = Literal[
    "itm",
    "listed",
    "permit",
    "investor",
    "equity",
    "retention",
    "second_lien_itm",
    "heloc_draw_to_payback",
    "home_equity_history",
    "refi_propensity",
    "itm_on_related_property",
    "payoff_loss_leads",
    "permit_activity",
]

#: The 50 states plus DC by USPS code, pinned to ``usps.USPS_STATE_CODES``.
GenieContextStateCode = Literal[
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO",
    "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA",
    "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
]  # fmt: skip

#: State FIPS prefix -> USPS code for the 50 states and DC. Territories (PR, GU,
#: VI, AS, MP) are absent on purpose: a county there is withheld by the schema.
STATE_FIPS_TO_USPS: Final[dict[str, str]] = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO",
    "09": "CT", "10": "DE", "11": "DC", "12": "FL", "13": "GA", "15": "HI",
    "16": "ID", "17": "IL", "18": "IN", "19": "IA", "20": "KS", "21": "KY",
    "22": "LA", "23": "ME", "24": "MD", "25": "MA", "26": "MI", "27": "MN",
    "28": "MS", "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH",
    "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND", "39": "OH",
    "40": "OK", "41": "OR", "42": "PA", "44": "RI", "45": "SC", "46": "SD",
    "47": "TN", "48": "TX", "49": "UT", "50": "VT", "51": "VA", "53": "WA",
    "54": "WV", "55": "WI", "56": "WY",
}  # fmt: skip

#: A ``CITY~ST`` key: one to four tokens of letters (plus ``.``, ``'`` and
#: ``-``), a tilde and a two-letter code. The code is checked against the USPS
#: list below; the city is checked against the governed set at render time.
GENIE_CONTEXT_CITY_PATTERN: Final[str] = r"^[A-Za-z][A-Za-z.'-]*(?: [A-Za-z][A-Za-z.'-]*){0,3}~[A-Z]{2}$"
GENIE_CONTEXT_LENDER_PATTERN: Final[str] = r"^Competitor ([A-Z]|Other)$"
_FIVE_DIGITS: Final[str] = r"^[0-9]{5}$"
_CITY_RE: Final[re.Pattern[str]] = re.compile(GENIE_CONTEXT_CITY_PATTERN)

GenieContextWithheld = Literal[
    "unsupported_combination", "excluded_value", "place_unavailable", "refused"
]


class GenieContext(BaseModel):
    """A Lead Queue filter selection as closed codes, for a server-rendered Genie question.

    Codes only: segment codes, a USPS state code, a county FIPS code, a ZIP, a
    governed city key and a public Competitor lender ref. No field carries free
    text, a borrower id or an email address.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    route: Literal["lead-queue"]
    segment_codes: list[GenieContextSegmentCode] = Field(default_factory=list, max_length=2)
    segment_mode: Literal["any", "all"] = "any"
    state: GenieContextStateCode | None = None
    county_fips: str | None = Field(default=None, pattern=_FIVE_DIGITS)
    zip: str | None = Field(default=None, pattern=_FIVE_DIGITS)
    city: str | None = Field(default=None, max_length=64, pattern=GENIE_CONTEXT_CITY_PATTERN)
    lender_ref: str | None = Field(default=None, pattern=GENIE_CONTEXT_LENDER_PATTERN)
    assignee: Literal["me"] | None = None

    @field_validator("segment_codes")
    @classmethod
    def _unique_segments(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("segment codes must be unique")
        return value

    @field_validator("city")
    @classmethod
    def _city_state_is_usps(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _CITY_RE.fullmatch(value) or value.rsplit("~", 1)[1] not in USPS_STATE_CODES:
            raise ValueError("city must be a governed CITY~ST key")
        return value

    @model_validator(mode="after")
    def _closed_combination(self) -> GenieContext:
        places = [slot for slot in (self.county_fips, self.zip, self.city) if slot is not None]
        if len(places) > 1:
            raise ValueError("at most one of county_fips, zip and city")
        if self.county_fips is not None:
            county_state = STATE_FIPS_TO_USPS.get(self.county_fips[:2])
            if county_state is None:
                raise ValueError("county_fips must be a county of a US state or DC")
            if self.state is not None and self.state != county_state:
                raise ValueError("county_fips must lie in state")
        if (
            self.city is not None
            and self.state is not None
            and self.city.rsplit("~", 1)[1] != self.state
        ):
            raise ValueError("city must lie in state")
        renderable = (
            self.segment_codes,
            self.state,
            self.county_fips,
            self.zip,
            self.city,
            self.lender_ref,
        )
        if not any(renderable):
            raise ValueError("at least one segment, place or lender slot is required")
        return self


class GenieContextScopeResponse(BaseModel):
    """The reviewed question for a context, or the closed reason none is offered.

    ``prompt`` is a server-rendered sentence that has already passed the
    deterministic prompt guards; the browser only prefills the composer with
    it. ``withheld`` names why no prompt is offered, and ``omitted`` names the
    slots the question deliberately leaves out.
    """

    prompt: str | None = None
    withheld: GenieContextWithheld | None = None
    omitted: list[Literal["assignee"]] = Field(default_factory=list, max_length=1)
