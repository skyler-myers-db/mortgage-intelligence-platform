"""Slice 3 -- personalized, TILA/ECOA-safe outreach DRAFT copy.

These tests exercise ``_compose_outreach_body`` and ``_personalization_hook``
directly (the copy generators), not the route, so they pin the regulated-copy
contract without needing the Lakebase/disclosure resolver stack:

* personalization is grounded in SAFE public-record signals only,
* no numeric TILA trigger terms (rate %, $ amount, payment, APR) appear in the
  pitch we author (the tenant disclosure block is exempt -- it is governed copy),
* SMS stays <=160 chars,
* current customers never receive cold-acquisition language,
* no unprovable / firm-offer terms ("guaranteed", "pre-approved", etc.).
"""
from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from backend.services.outreach_copy import _compose_outreach_body, _personalization_hook
from tests.fixtures import mock_population

# A digit-bearing financial trigger: "6.5%", "$300", "APR".
_TRIGGER_TERM_RE = re.compile(r"\d+(\.\d+)?\s?%|\$\s?\d|\bAPR\b", re.IGNORECASE)

_FORBIDDEN_TERMS = ("guaranteed", "pre-approved", "preapproved", "you qualify", "lowest rate")

_DISCLOSURE = SimpleNamespace(
    body="Summit Mortgage, NMLS #123456. Equal Housing Lender. Reply unsubscribe to opt out."
)


def _borrower(**overrides):
    return mock_population.BORROWERS[0].model_copy(update=overrides)


def _pitch_without_disclosure(body: str) -> str:
    """Strip the governed disclosure block so we only scan copy we authored."""
    return body.replace(_DISCLOSURE.body, "")


# ---------------------------------------------------------------------------
# _personalization_hook priority + safety
# ---------------------------------------------------------------------------
def test_hook_priority_competitor_lien_wins() -> None:
    borrower = _borrower(
        is_competitor_lien=True,
        rate_spread_bps=120,
        equity_estimate=90_000,
        is_investor=True,
        related_property_count=4,
    )
    assert _personalization_hook(borrower) == "your current mortgage with another servicer"


def test_hook_rate_spread_then_equity_then_portfolio() -> None:
    assert (
        _personalization_hook(_borrower(is_competitor_lien=False, rate_spread_bps=80))
        == "your current rate relative to today's market"
    )
    assert (
        _personalization_hook(
            _borrower(
                is_competitor_lien=False, rate_spread_bps=0, ltv=90, equity_estimate=120_000
            )
        )
        == "your estimated home-equity position"
    )
    assert (
        _personalization_hook(
            _borrower(
                is_competitor_lien=False,
                rate_spread_bps=0,
                ltv=95,
                equity_estimate=0,
                is_investor=True,
                related_property_count=3,
            )
        )
        == "your property portfolio"
    )


def test_hook_returns_empty_when_no_signal() -> None:
    borrower = _borrower(
        is_competitor_lien=False,
        rate_spread_bps=0,
        ltv=95,
        equity_estimate=0,
        is_investor=False,
        related_property_count=1,
    )
    assert _personalization_hook(borrower) == ""


def test_hook_never_emits_a_financial_figure() -> None:
    for spread in (10, 75, 350):
        hook = _personalization_hook(_borrower(is_competitor_lien=False, rate_spread_bps=spread))
        assert not _TRIGGER_TERM_RE.search(hook), hook


# ---------------------------------------------------------------------------
# (a) competitor-lien email: hook + why_now + CTA + disclosure
# ---------------------------------------------------------------------------
def test_competitor_lien_email_has_hook_reason_cta_disclosure() -> None:
    borrower = _borrower(
        is_current_customer=False,
        is_competitor_lien=True,
        why_now="Permit filed and equity is building -- a timely refinance window.",
    )
    subject, body = _compose_outreach_body(
        borrower=borrower, channel="email", disclosure=_DISCLOSURE
    )
    assert subject
    assert "your current mortgage with another servicer" in body
    assert "Permit filed and equity is building" in body
    assert "Reply YES and a licensed loan officer will follow up -- no obligation." in body
    assert _DISCLOSURE.body in body


# ---------------------------------------------------------------------------
# (b) no digit-bearing trigger term in the copy we author
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("channel", ["email", "sms", "direct_mail"])
def test_no_trigger_terms_in_authored_copy(channel: str) -> None:
    borrower = _borrower(
        is_competitor_lien=True,
        rate_spread_bps=140,
        equity_estimate=200_000,
        why_now="Your loan profile shows a refinance window worth a review.",
    )
    _subject, body = _compose_outreach_body(
        borrower=borrower, channel=channel, disclosure=_DISCLOSURE
    )
    pitch = _pitch_without_disclosure(body)
    assert not _TRIGGER_TERM_RE.search(pitch), pitch
    assert "%" not in pitch
    assert "$" not in pitch


# ---------------------------------------------------------------------------
# (c) SMS stays <=160 chars even with a hook
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("competitor", [True, False])
def test_sms_within_160_chars(competitor: bool) -> None:
    borrower = _borrower(
        is_current_customer=False,
        is_competitor_lien=competitor,
        rate_spread_bps=0 if competitor else 90,
    )
    subject, body = _compose_outreach_body(
        borrower=borrower, channel="sms", disclosure=_DISCLOSURE
    )
    assert subject is None
    assert len(body) <= 160, (len(body), body)
    assert _DISCLOSURE.body in body


# ---------------------------------------------------------------------------
# (d) current customer never gets cold-acquisition language
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("channel", ["email", "sms", "direct_mail"])
def test_current_customer_no_cold_language(channel: str) -> None:
    borrower = _borrower(
        is_current_customer=True,
        is_competitor_lien=False,
        rate_spread_bps=60,
    )
    _subject, body = _compose_outreach_body(
        borrower=borrower, channel=channel, disclosure=_DISCLOSURE
    )
    lowered = body.lower()
    # Cold-acquisition framing leans on "public-record signals" / "may qualify".
    assert "public-record" not in lowered
    assert "may qualify" not in lowered
    # Longer channels acknowledge the relationship explicitly; SMS has no room
    # for it under the 160-char cap, but must still avoid cold framing (above).
    if channel != "sms":
        assert "customer" in lowered


# ---------------------------------------------------------------------------
# (e) no unprovable / firm-offer terms
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("channel", ["email", "sms", "direct_mail"])
@pytest.mark.parametrize("is_customer", [True, False])
def test_no_overpromising_terms(channel: str, is_customer: bool) -> None:
    borrower = _borrower(
        is_current_customer=is_customer,
        is_competitor_lien=not is_customer,
        rate_spread_bps=110,
        why_now="A licensed officer can review whether the current profile fits.",
    )
    _subject, body = _compose_outreach_body(
        borrower=borrower, channel=channel, disclosure=_DISCLOSURE
    )
    lowered = body.lower()
    for term in _FORBIDDEN_TERMS:
        assert term not in lowered, term


# ---------------------------------------------------------------------------
# Defense-in-depth: a figure-bearing why_now must never leak a trigger term
# (QA signoff catch — the compose boundary drops it, keeping the qualitative
# hook). Uses the exact figure-laden shape present in real fixtures.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("channel", ["email", "direct_mail"])
def test_figure_bearing_why_now_is_suppressed_from_authored_copy(channel) -> None:
    borrower = _borrower(
        is_competitor_lien=True,
        why_now="138 bps spread and 49% equity make refi + HELOC a clean cross-sell.",
    )
    _subject, body = _compose_outreach_body(
        borrower=borrower, channel=channel, disclosure=_DISCLOSURE
    )
    authored = _pitch_without_disclosure(body)
    assert not _TRIGGER_TERM_RE.search(authored), authored
    # The specific leaked figures from the real fixture string are gone.
    assert "49%" not in authored
    assert "138" not in authored
    assert "bps" not in authored.lower()
    # Personalization still lands via the qualitative hook; disclosure retained.
    assert "your current mortgage with another servicer" in authored
    assert _DISCLOSURE.body in body
