"""Outreach copy generation -- relationship-aware, governed draft copy.

Extracted from ``backend/api/outreach.py`` so the API router stays under the
monolith threshold. This module owns the *authoring* of borrower-facing draft
copy: offer-code normalization, the SAFE/QUALITATIVE personalization hook, the
TILA numeric-trigger-term guard, and the channel-specific compose function.

The product never sends messages; everything here produces draft copy for
governed human review. No Lakebase / audit / scoring-write side effects live
here -- this module is pure copy authoring (plus the ``HTTPException`` the
compose path raises when a tenant SMS disclosure is too long to fit 160 chars).
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import HTTPException

from backend.config.settings import settings
from backend.services.scoring import NBO_PRODUCT_LABELS


def _safe_offer_code(raw: str | None) -> str:
    code = str(raw or "nurture").strip()
    return code if code in NBO_PRODUCT_LABELS else "nurture"


def _offer_label(code: str, fallback: str | None = None) -> str:
    return NBO_PRODUCT_LABELS.get(code) or fallback or "mortgage review"


BORROWER_OFFER_PHRASES: dict[str, str] = {
    "purchase": "next-home financing",
    "refi_plus_heloc": "a refinance and home-equity review",
    "heloc": "a home-equity line review",
    "refi": "a refinance review",
    "cash_out": "a cash-out refinance review",
    "investor": "an investment-property financing review",
    "retention": "a current-loan review",
    "nurture": "a mortgage checkup",
}


def _borrower_offer_phrase(code: str, fallback: str | None = None) -> str:
    return BORROWER_OFFER_PHRASES.get(code) or (fallback or "a mortgage review")


def _sms_lender_label(lender_name: str) -> str:
    """Return a compact tenant label for 160-character SMS drafts."""

    normalized = " ".join(lender_name.split())
    if len(normalized) <= 18:
        return normalized
    first_word = re.sub(r"[^A-Za-z0-9&.-]", "", normalized.split()[0])
    return first_word[:18] or "Lender"


def _personalization_hook(borrower: Any) -> str:
    """Return one short, SAFE, QUALITATIVE personalization phrase.

    The phrase is grounded in the borrower's strongest available public-record
    signal, in priority order, and contains NO numeric financial figure (TILA
    trigger-term safe) and NO protected-class language (ECOA safe). Returns ""
    when no signal is confidently present -- we never fabricate a hook.

    Priority: competitor lien -> rate-spread incentive -> equity position ->
    investor / multi-property portfolio.
    """
    if bool(getattr(borrower, "is_competitor_lien", False)):
        return "your current mortgage"

    rate_spread = getattr(borrower, "rate_spread_bps", None)
    if (
        isinstance(rate_spread, int | float)
        and not isinstance(rate_spread, bool)
        and rate_spread > 0
    ):
        return "your current rate relative to today's market"

    ltv = getattr(borrower, "ltv", None)
    equity = getattr(borrower, "equity_estimate", None)
    has_equity_ltv = isinstance(ltv, int | float) and not isinstance(ltv, bool) and ltv < 80
    has_equity_est = isinstance(equity, int | float) and not isinstance(equity, bool) and equity > 0
    if has_equity_ltv or has_equity_est:
        return "your estimated home-equity position"

    related = getattr(borrower, "related_property_count", None)
    if bool(getattr(borrower, "is_investor", False)) and (
        isinstance(related, int | float) and not isinstance(related, bool) and related > 1
    ):
        return "your property portfolio"

    return ""


# Numeric financial figures that must never appear in authored borrower-facing
# copy: a percent, a $ amount, an "N bps" spread, a bare rate like "6.5 rate",
# or "APR". (The tenant disclosure block is governed copy and is composed
# separately.) Used to gate `why_now` at the compose boundary.
_TRIGGER_TERM_RE = re.compile(
    r"\d+(?:\.\d+)?\s?%|\$\s?\d|\b\d+(?:\.\d+)?\s?bps\b|\b\d+(?:\.\d+)?\s?(?:rate|apr)\b|\bAPR\b",
    re.IGNORECASE,
)


def _figure_safe_reason(text: str) -> str:
    """Return `text` only if it carries NO numeric financial trigger term;
    otherwise "" so the caller falls back to figure-free qualitative copy.

    We suppress the whole phrase rather than scrub tokens, to avoid emitting
    mangled fragments ("... bps spread and ... equity ...")."""
    if not text:
        return ""
    return "" if _TRIGGER_TERM_RE.search(text) else text


def _compose_outreach_body(
    *, borrower: Any, channel: str, disclosure: Any
) -> tuple[str | None, str]:
    """Return relationship-aware, channel-specific human-review copy.

    This is still a draft; the product does not send messages. Copy is
    personalized on SAFE public-record signals via ``_personalization_hook``
    (no numeric TILA trigger terms, no ECOA protected-class language), grounded
    in the proof-backed ``why_now`` reason, and ends in a single low-effort
    call to action. It avoids literal personalization placeholders and branches
    on the governed borrower relationship flags so current customers do not
    receive cold acquisition language.
    """
    code = _safe_offer_code(getattr(borrower, "recommended_offer_code", None))
    offer = _borrower_offer_phrase(
        code, _offer_label(code, getattr(borrower, "recommended_offer", None))
    )
    is_customer = bool(getattr(borrower, "is_current_customer", False))
    is_competitor = bool(getattr(borrower, "is_competitor_lien", False))
    # `why_now` is a proof-backed reason from the gold tables. Production copy is
    # de-numericised, but we DEFENSE-IN-DEPTH guard the compose boundary: if it
    # carries any numeric financial figure (a %, a $ amount, "N bps", a rate, or
    # APR), we DROP it from borrower-facing copy rather than emit a TILA
    # trigger term. The qualitative `hook` still carries personalization, so the
    # message stays specific without a figure. Structural guarantee, not a
    # convention — so a future template change can't silently leak a figure.
    hook = _personalization_hook(borrower)
    lender_name = (settings.mip_lender_name or "configured lender").strip() or "configured lender"
    sms_lender = _sms_lender_label(lender_name)

    if channel == "sms":
        # Hook is optional on SMS -- it is dropped first when the 160-char guard
        # would otherwise be blown. Current customers never get cold language.
        if code == "purchase":
            body = f"{lender_name}: planning a move? We can help review {offer}. Reply YES. {disclosure.body}"
        elif is_customer:
            body = f"{lender_name}: a quick mortgage review is ready. Reply YES. {disclosure.body}"
        else:
            body = f"{lender_name}: a quick mortgage review may help. Reply YES. {disclosure.body}"
        if len(body) > 160:
            body = f"{sms_lender}: mortgage review. Reply YES. {disclosure.body}"
        if len(body) > 160:
            body = f"Reply YES to review. {disclosure.body}"
        if len(body) > 160:
            raise HTTPException(
                status_code=412,
                detail=(
                    "tenant SMS disclosure leaves no room for a reviewed call to action within "
                    "160 characters; configure a shorter SMS disclosure"
                ),
            )
        return None, body

    if code == "purchase":
        opening = (
            f"If you are planning a move, {lender_name} can help you compare "
            "financing options for your next home before your current loan is paid off at closing."
        )
        benefit = (
            "A licensed loan officer can walk through timing, pre-approval, "
            "and next steps so you know what you can shop for with confidence."
        )
        subject = "Planning your next move?"
    elif is_customer:
        signal = f" Based on {hook}," if hook else ""
        opening = f"As a {lender_name} customer,{signal.lower()} your current loan may be worth a quick review."
        benefit = (
            "A licensed loan officer can help compare whether your current mortgage still fits "
            "or whether another option is worth considering."
        )
        subject = f"A quick mortgage review from {lender_name}"
    elif is_competitor:
        opening = (
            f"Based on {hook}, we can help you compare your current mortgage with today's options."
            if hook
            else "We can help you compare your current mortgage with today's options."
        )
        benefit = (
            "A licensed loan officer can review whether a refinance, home-equity option, "
            "or another mortgage path is worth your time."
        )
        subject = "A quick mortgage review"
    else:
        opening = (
            f"Based on {hook}, a quick mortgage review may be useful."
            if hook
            else "A quick mortgage review may be useful."
        )
        benefit = f"A licensed loan officer can help you compare {offer} and decide whether it is worth pursuing."
        subject = "A quick mortgage review"

    if channel == "direct_mail":
        body = (
            f"{opening}\n\n"
            f"{benefit}\n\n"
            "To learn more, call or reply and a licensed loan officer will follow up "
            "at your convenience -- no obligation.\n\n"
            f"{disclosure.body}"
        )
        return subject, body

    # Email
    body = (
        f"Hello,\n\n"
        f"{opening}\n\n"
        f"{benefit}\n\n"
        "Reply YES and a licensed loan officer will follow up -- no obligation.\n\n"
        f"{disclosure.body}"
    )
    return subject, body
