"""Borrower-facing draft copy never carries an internal term or a TILA figure.

critic-11 / D-platform-process-g step 9. "In the Money" (and its short form
ITM, and the retired "Prime" label) is staff vocabulary: it must never reach
a borrower. The guarantee is structural -- ``_compose_outreach_body`` reads
only the safe offer code, the two relationship flags and the qualitative
personalization hook, never ``why_now``, the offer display text or the
segment codes -- and this suite enforces it adversarially: every channel x
every offer code x every relationship x every hook branch is composed with
those fields stuffed with internal labels and figures, and no subject or body
outside the governed disclosure may match either the internal-term pattern or
the TILA trigger-term pattern.
"""

from __future__ import annotations

import itertools
import re
from types import SimpleNamespace
from typing import Any

import pytest

from backend.config.settings import settings
from backend.services.outreach_copy import _compose_outreach_body, _personalization_hook
from backend.services.scoring import NBO_PRODUCT_LABELS
from tests.fixtures import mock_population

_INTERNAL_TERM_RE = re.compile(r"in[- ]the[- ]money|\bITM\b|\bprime\b", re.IGNORECASE)
# A percent, a $ amount, an "N bps" spread, a bare "6.5 rate" or an APR.
_TILA_TRIGGER_RE = re.compile(
    r"\d+(?:\.\d+)?\s?%|\$\s?\d|\b\d+(?:\.\d+)?\s?bps\b|\b\d+(?:\.\d+)?\s?(?:rate|apr)\b|\bAPR\b",
    re.IGNORECASE,
)

_DISCLOSURE = SimpleNamespace(
    body="Summit Mortgage, NMLS #123456. Equal Housing Lender. Reply unsubscribe to opt out."
)

# Every field the composer could be tempted to quote, loaded with the words
# and figures that must never reach a borrower.
_ADVERSARIAL: dict[str, Any] = {
    "why_now": "ITM: in the money by 75 bps -- Prime refi window at 6.5% (in-the-money).",
    "recommended_offer": "Prime Refi Candidates: in-the-money refinance at 6.5%",
    "segment_codes": ["itm", "itm_on_related_property", "second_lien_itm"],
}

_CHANNELS = ("email", "sms", "direct_mail")
# Every governed offer code plus an unknown one (it normalizes to nurture).
_OFFER_CODES = (*NBO_PRODUCT_LABELS, "prime_refi")
_RELATIONSHIPS: dict[str, dict[str, bool]] = {
    "customer": {"is_current_customer": True, "is_competitor_lien": False},
    "competitor": {"is_current_customer": False, "is_competitor_lien": True},
    "neither": {"is_current_customer": False, "is_competitor_lien": False},
}
# One override set per _personalization_hook branch (the relationship flags
# are applied after, so the competitor branch also arises from "competitor").
_HOOK_BRANCHES: dict[str, dict[str, Any]] = {
    "rate_spread": {"rate_spread_bps": 120, "ltv": 95, "equity_estimate": 0},
    "equity": {"rate_spread_bps": 0, "ltv": 50, "equity_estimate": 90_000},
    "portfolio": {
        "rate_spread_bps": 0,
        "ltv": 95,
        "equity_estimate": 0,
        "is_investor": True,
        "related_property_count": 4,
    },
    "none": {
        "rate_spread_bps": 0,
        "ltv": 95,
        "equity_estimate": 0,
        "is_investor": False,
        "related_property_count": 1,
    },
}

_CASES = list(itertools.product(_CHANNELS, _OFFER_CODES, _RELATIONSHIPS, _HOOK_BRANCHES))


@pytest.fixture(autouse=True)
def _summit_lender(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mip_lender_name", "Summit Mortgage")


def _borrower(offer_code: str, relationship: str, hook_branch: str) -> Any:
    update: dict[str, Any] = {
        **_ADVERSARIAL,
        **_HOOK_BRANCHES[hook_branch],
        **_RELATIONSHIPS[relationship],
        "recommended_offer_code": offer_code,
    }
    return mock_population.BORROWERS[0].model_copy(update=update)


def _authored(text: str | None) -> str:
    """The copy this product authored: the governed disclosure is exempt."""

    return (text or "").replace(_DISCLOSURE.body, "")


def test_the_case_matrix_reaches_every_hook_branch() -> None:
    """Non-vacuity: the overrides really drive each personalization branch."""

    hooks = {
        (relationship, branch): _personalization_hook(_borrower("refi", relationship, branch))
        for relationship in _RELATIONSHIPS
        for branch in _HOOK_BRANCHES
    }
    assert hooks[("neither", "rate_spread")] == "your current rate relative to today's market"
    assert hooks[("neither", "equity")] == "your estimated home-equity position"
    assert hooks[("neither", "portfolio")] == "your property portfolio"
    assert hooks[("neither", "none")] == ""
    assert hooks[("competitor", "none")] == "your current mortgage"
    assert len(_CASES) == len(_CHANNELS) * len(_OFFER_CODES) * 3 * 4


def test_the_patterns_bite_on_the_adversarial_inputs() -> None:
    """Non-vacuity: every adversarial field would fail the scan if it leaked."""

    for value in (_ADVERSARIAL["why_now"], _ADVERSARIAL["recommended_offer"]):
        assert _INTERNAL_TERM_RE.search(value), value
        assert _TILA_TRIGGER_RE.search(value), value
    assert _INTERNAL_TERM_RE.search(" ".join(_ADVERSARIAL["segment_codes"]))


@pytest.mark.parametrize(("channel", "offer_code", "relationship", "hook_branch"), _CASES)
def test_no_internal_term_or_trigger_figure_reaches_borrower_copy(
    channel: str, offer_code: str, relationship: str, hook_branch: str
) -> None:
    borrower = _borrower(offer_code, relationship, hook_branch)
    subject, body = _compose_outreach_body(borrower=borrower, channel=channel, disclosure=_DISCLOSURE)
    assert _DISCLOSURE.body in body
    for part in (_authored(subject), _authored(body)):
        assert not _INTERNAL_TERM_RE.search(part), part
        assert not _TILA_TRIGGER_RE.search(part), part
