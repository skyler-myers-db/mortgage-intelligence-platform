"""Analyst-brief composers that shape canonical ranking rows into prose."""

from __future__ import annotations

from typing import Any

from backend.services.scoring import offer_display_label


def _brief_money(value: Any) -> str | None:
    """Format a dollar amount the way an analyst would say it aloud."""

    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    if amount <= 0:
        return None
    if amount >= 1_000_000:
        return f"${amount / 1_000_000:.2f}M"
    if amount >= 1_000:
        return f"${amount / 1_000:.0f}k"
    return f"${amount:,.0f}"


def _brief_city_label(row: dict[str, Any]) -> str:
    city = str(row.get("city") or "").strip()
    state = str(row.get("state") or "").strip()
    if city and state:
        return f"{city.title()}, {state}"
    return city.title() or state or "coverage area"


def _brief_int(value: Any) -> int | None:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


# Overlay segment codes translated into the sentence an analyst would say.
# Raw codes like ``heloc_draw_to_payback`` must never reach the narrative.
_BRIEF_OVERLAY_SIGNALS: tuple[tuple[str, str], ...] = (
    (
        "heloc_draw_to_payback",
        "an existing home-equity line is nearing the end of its draw period, "
        "which usually brings a payment jump — a natural moment to restructure",
    ),
    (
        "home_equity_history",
        "they have borrowed against home equity before, so the product is familiar territory",
    ),
    (
        "second_lien_itm",
        "they carry an expensive second lien that is worth consolidating",
    ),
    (
        "refi_propensity",
        "Cotality's refinance-propensity model independently flags them as likely to act",
    ),
    (
        "itm_on_related_property",
        "another property they own also passes the refinance screen",
    ),
    (
        "payoff_loss_leads",
        "they recently paid off a loan, which opens a recapture window",
    ),
)


def _brief_position_sentences(row: dict[str, Any]) -> list[str]:
    """The borrower's mortgage position, in plain dollars-and-rates English."""

    sentences: list[str] = []
    balance = _brief_int(row.get("current_lien_balance")) or 0
    avm = _brief_money(row.get("avm_value"))
    equity_money = _brief_money(row.get("equity_estimate"))
    equity_pct = _brief_int(row.get("equity_pct"))
    rate = row.get("current_rate")
    rate_f = None
    try:
        rate_f = float(rate) if rate is not None and float(rate) > 0 else None
    except (TypeError, ValueError):
        rate_f = None
    if balance > 0 and rate_f is not None and avm:
        base = (
            f"They are paying {rate_f:.2f}% on roughly "
            f"{_brief_money(balance)} of remaining mortgage against a home "
            f"valued around {avm}"
        )
        if equity_money and equity_pct is not None:
            base += (
                f", which leaves about {equity_money} of equity — they own "
                f"{equity_pct}% of the home's value outright"
            )
        sentences.append(base + ".")
    elif balance == 0 and avm:
        owned = (
            f"They own the home outright — no mortgage balance remains against "
            f"a value of about {avm}"
        )
        if equity_money:
            owned += f", so all {equity_money} of it is equity"
        sentences.append(owned + ".")
    spread = _brief_int(row.get("rate_spread_bps"))
    min_spread = _brief_int(row.get("min_spread_bps_applied"))
    if row.get("in_the_money") and spread and rate_f is not None:
        market = rate_f - spread / 100.0
        points = spread / 100.0
        threshold_txt = (
            f" — well past the {min_spread}-bps mark where a refinance typically "
            "starts paying for itself"
            if min_spread and spread >= 2 * min_spread
            else (
                f" — clearing the {min_spread}-bps refinance threshold"
                if min_spread
                else ""
            )
        )
        sentences.append(
            f"Today's market rate is roughly {market:.2f}%, so they are "
            f"overpaying by about {points:.2f} percentage points "
            f"({spread} basis points){threshold_txt}."
        )
    return sentences


def _brief_signal_sentences(row: dict[str, Any]) -> list[str]:
    """Behavioral and ownership signals, translated from codes into stories."""

    sentences: list[str] = []
    segments = {code.strip() for code in str(row.get("segments") or "").split(",")}
    if row.get("listed_for_sale"):
        status = str(row.get("listing_status_category") or "").strip().lower()
        # Only annotate with a readable status word — single-letter source
        # codes ("a") add noise, not meaning, for a general reader.
        status_txt = (
            f" ({status})" if len(status) >= 4 and status not in {"none", "unknown"} else ""
        )
        sentences.append(
            f"The home is actively listed for sale{status_txt} — the strongest "
            "intent signal in the funnel, because when it sells this borrower "
            "needs financing for the next one."
        )
    related = _brief_int(row.get("related_property_count")) or 0
    if related >= 2:
        sentences.append(
            f"Cotality's mastered ownership records tie them to {related} "
            "properties in total, marking an experienced multi-property "
            "investor rather than a first-time borrower."
        )
    propensity = _brief_int(row.get("heloc_propensity_score"))
    if row.get("has_heloc_propensity_trigger") and propensity:
        sentences.append(
            f"Cotality's HELOC-propensity model scores them {propensity} out "
            "of 999 — top-tier likelihood of opening a home-equity line, "
            "based on behavioral and property patterns, not marketing guesses."
        )
    overlays = [text for code, text in _BRIEF_OVERLAY_SIGNALS if code in segments]
    if overlays:
        joined = "; ".join(overlays[:3])
        sentences.append(f"On top of that, {joined}.")
    if row.get("is_current_customer"):
        sentences.append(
            "They are also an existing customer, so this is a retention "
            "conversation, not a cold introduction."
        )
    return sentences


def _brief_offer_paragraph(row: dict[str, Any]) -> str:
    """The offer, the reason it follows from the numbers, and the play."""

    code = str(row.get("recommended_offer_code") or "")
    offer = offer_display_label(code, str(row.get("recommended_offer") or ""))
    spread = _brief_int(row.get("rate_spread_bps"))
    equity_pct = _brief_int(row.get("equity_pct"))
    heloc_min = _brief_int(row.get("heloc_equity_min_applied"))
    cashout_min = _brief_int(row.get("cashout_equity_min_applied"))
    equity_money = _brief_money(row.get("equity_estimate"))
    balance = _brief_int(row.get("current_lien_balance")) or 0
    spread_clause = (
        f"the {spread}-bps rate gap makes the refinance case on its own"
        if spread
        else "the rate economics make the refinance case"
    )
    equity_clause = (
        f"{equity_pct}% equity"
        + (f" (about {equity_money})" if equity_money else "")
        + (f" clears the {heloc_min}% home-equity bar" if heloc_min else "")
    )
    plays = {
        "refi_plus_heloc": (
            f"{spread_clause}, and {equity_clause}, so the same conversation can "
            "add a home-equity line. The play: lead with the rate savings, then "
            "present the equity line as optional flexibility rather than a "
            "second pitch."
        ),
        "refi": (
            f"{spread_clause}; equity sits below the "
            f"{heloc_min or 'home-equity'}% bar, so keep it a clean "
            "rate-improvement conversation without upsell noise."
        ),
        "heloc": (
            f"{equity_clause}, and the propensity signals say the appetite is "
            "there. The play: an equity line that leaves their current "
            "first mortgage untouched."
        ),
        "cash_out": (
            (
                "There is no rate incentive to lead with, but "
                if not row.get("in_the_money")
                else ""
            )
            + f"{equity_clause}"
            + (f" (cash-out threshold: {cashout_min}%)" if cashout_min else "")
            + (
                ". With no remaining mortgage, this is unlocking cash against a "
                "debt-free home on their terms."
                if balance == 0
                else ". The play: convert locked-up equity into usable cash in one refinance."
            )
        ),
        "purchase": (
            "the active listing means a purchase-loan need on the next home. "
            "The play: get a pre-approval in place before the sale closes, so "
            "the financing is ready the day they buy."
        ),
        "investor": (
            "the multi-property profile routes to investor financing — "
            "portfolio-aware terms rather than a single-home product."
        ),
        "retention": (
            "the relationship plus rate-drift signals say a competitor could "
            "take this loan; the play is a proactive retention review before "
            "that call happens."
        ),
        "nurture": (
            "the signals are real but early — keep them in the funnel and "
            "let the triggers mature before spending an outreach touch."
        ),
    }
    reason = plays.get(code, "the governed next-best-offer rules select it from this row's signals.")
    return f"**The offer: {offer}.** {reason[0].upper() + reason[1:] if reason else reason}"


def _brief_screen_txt(rows: list[dict[str, Any]]) -> str:
    top = rows[0] if rows else {}
    min_spread = _brief_int(top.get("min_spread_bps_applied"))
    min_equity = _brief_int(top.get("min_equity_pct_applied"))
    return (
        f"at least {min_spread} bps of spread with at least {min_equity}% equity"
        if min_spread and min_equity
        else "enough spread and equity"
    )


def _brief_terms_txt(rows: list[dict[str, Any]]) -> str:
    """The shared term-teaching sentence, thresholds read from the rows."""

    return (
        "Two terms recur below: **rate spread** is how far a borrower's "
        "current mortgage rate sits above today's market rate, measured in "
        "basis points (100 bps = 1 percentage point), and a borrower is "
        "**in the money** when that gap is wide enough to make refinancing "
        f"pay for itself ({_brief_screen_txt(rows)} under the current policy)."
    )


def compose_borrower_ranking_brief(
    rows: list[dict[str, Any]],
    *,
    intro: str,
    source_asset: str,
    empty_message: str,
) -> str:
    """Teaching-analyst brief shared by every borrower-level ranking shape.

    Written for a Head of Growth, not a mortgage quant: every term is
    explained inline, every number is interpreted in dollars and plain
    English, raw segment codes never leak into prose, and each candidate ends
    with the concrete play. Every figure reads from the governed rows, so the
    brief can never carry an unverifiable claim. Renders through the answer
    markdown (paragraph blocks, ``**bold**``).
    """

    if not rows:
        return empty_message
    blocks: list[str] = [intro]
    for rank, row in enumerate(rows, start=1):
        score = _brief_int(row.get("opportunity_score"))
        header = (
            f"**#{rank} · {row.get('borrower_id')} · {_brief_city_label(row)} · "
            f"opportunity score {score if score is not None else '—'}/100.**"
        )
        story = " ".join(
            [*_brief_position_sentences(row), *_brief_signal_sentences(row)]
        )
        offer_par = _brief_offer_paragraph(row)
        blocks.append(f"{header} {story}".strip())
        blocks.append(offer_par)
    itm_rows = [r for r in rows if r.get("in_the_money")]
    listed_rows = [r for r in rows if r.get("listed_for_sale")]
    investor_rows = [r for r in rows if (_brief_int(r.get("related_property_count")) or 0) >= 2]
    heloc_rows = [r for r in rows if r.get("has_heloc_propensity_trigger")]
    cohort_bits: list[str] = []
    if itm_rows:
        spreads = [
            float(r["rate_spread_bps"]) for r in itm_rows if r.get("rate_spread_bps") is not None
        ]
        if spreads:
            avg = int(round(sum(spreads) / len(spreads)))
            cohort_bits.append(
                f"{len(itm_rows)} of {len(rows)} are in the money with an "
                f"average gap of about {avg} bps (~{avg / 100:.1f} percentage "
                "points above market)"
            )
        else:
            cohort_bits.append(f"{len(itm_rows)} of {len(rows)} are in the money")
    if listed_rows:
        cohort_bits.append(f"{len(listed_rows)} have homes actively listed for sale")
    if investor_rows:
        cohort_bits.append(f"{len(investor_rows)} are multi-property investors")
    if heloc_rows:
        cohort_bits.append(
            f"{len(heloc_rows)} carry top-tier HELOC-propensity model scores"
        )
    cohort_txt = (
        f"**The cohort picture:** {'; '.join(cohort_bits)}. " if cohort_bits else ""
    )
    blocks.append(
        f"{cohort_txt}Offers come from the governed next-best-offer rules — "
        "the same decision tree the Lead Queue uses — and every "
        "recommendation still requires human approval there before any "
        "outreach happens."
    )
    blocks.append(f"Source: {source_asset}")
    return "\n\n".join(blocks)


def compose_all_segments_brief(rows: list[dict[str, Any]], borrower_asset: str) -> str:
    """The all-segments hero brief, built on the shared ranking composer."""

    refreshed = str(rows[0].get("refreshed_at") or "")[:10] if rows else ""
    refreshed_txt = f", refreshed {refreshed}," if refreshed else ""
    intro = (
        f"I ranked every marketing-eligible, opt-in borrower in "
        f"{borrower_asset}{refreshed_txt} by **opportunity score** — a "
        "0-100 blend of refinance economics, home equity, listing "
        "activity, portfolio ownership, retention risk, and Cotality's "
        "behavioral propensity models — with rate-spread economics "
        f"breaking ties. {_brief_terms_txt(rows)} Here are the top "
        f"{len(rows)} candidates in depth:"
    )
    return compose_borrower_ranking_brief(
        rows,
        intro=intro,
        source_asset=borrower_asset,
        empty_message=(
            "The trusted borrower table returned no marketing-eligible, opt-in "
            "borrowers across the segment portfolio for the current refreshed "
            "coverage."
        ),
    )


def compose_cohort_ranking_brief(
    rows: list[dict[str, Any]],
    *,
    cohort_label: str,
    ordering_label: str,
    source_asset: str,
    scope_note: str = "",
) -> str:
    """Teaching brief for a named ranking cohort (intent, state, Lead Queue)."""

    refreshed = str(rows[0].get("refreshed_at") or "")[:10] if rows else ""
    refreshed_txt = f", refreshed {refreshed}," if refreshed else ""
    intro = (
        f"I ranked the top {len(rows)} {cohort_label} from "
        f"{source_asset}{refreshed_txt} ordered by {ordering_label}."
        f"{' ' + scope_note if scope_note else ''} "
        f"{_brief_terms_txt(rows)} Here is each candidate in depth:"
    )
    return compose_borrower_ranking_brief(
        rows,
        intro=intro,
        source_asset=source_asset,
        empty_message=(
            f"The trusted borrower table returned no marketing-eligible "
            f"{cohort_label} for the current refreshed coverage."
        ),
    )
