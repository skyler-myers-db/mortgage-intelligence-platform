"""Governed product labels the person-name heuristic read as identities.

Both narratives below are verbatim live captures from the
``mortgage_lead_intelligence`` space on paychex 2026-08-12, taken while auditing
the masked-ID parenthetical. Each is an ordinary top-N borrower answer over real
gold rows, and each had its governed narrative withheld -- not by a
fair-lending, PII, or injection detector, but by the title-case person-name
heuristic pairing two words of the product's own vocabulary.

* ``Opportunity Score`` -- the ranking label Genie writes into every top-N
  borrower list. Fixed as a title-case SUFFIX ("score"/"scores"/"confidence"),
  because the pair is generative: "Lead Score", "Confidence Score" all take the
  shape.
* ``Competitor Other`` -- the repo-authored redaction label for a non-tenant
  lien holder (``gold_borrower_360.sql``), the live ``current_lender_ref`` on
  most rows. Fixed as a reviewed literal, because it is one fixed string we
  author ourselves.

Neither is reachable through the masked-ID parenthetical blank: the second
capture carries the label both inside the parenthetical AND in the trailing
sentence, so a positional blank never unblocked the answer.
"""

from __future__ import annotations

import pytest

from backend.services.genie_message_policy import genie_visible_text_unsafe

_LIVE_REFI_HELOC_TOP_10 = (
    "You want to see the top 10 borrowers recommended for refinance plus HELOC, "
    "including their city, state, ZIP code, opportunity score, confidence level, "
    "recommended offer label, and the data refresh timestamp.\n\n"
    "The top 10 borrowers to review first for a refinance plus HELOC offer, based on "
    "opportunity score and confidence, are located in cities including WOODINVILLE, "
    "DES MOINES, NORTH LAUDERDALE, LINCOLNWOOD, and DAVIE. All have high opportunity "
    "scores (ranging from 84 to 91) and strong confidence levels (79 to 86). Notable "
    "data points include:\n"
    "- **WOODINVILLE, WA** (Opportunity Score: 91, Confidence: 86)\n"
    "- **DES MOINES, WA** (Opportunity Score: 86, Confidence: 83)\n"
    "- **NORTH LAUDERDALE, FL** (Opportunity Score: 86, Confidence: 83)\n"
    "- **LINCOLNWOOD, IL** (Opportunity Score: 85, Confidence: 81)\n"
    "- **DAVIE, FL** (Opportunity Score: 85, Confidence: 80)\n\n"
    "Source: mip.gold.lead_population."
)

_LIVE_HIGHEST_RATE_SPREAD_TOP_10 = (
    "You want to see the top 10 borrowers with the highest rate spread, including "
    "their borrower ID, city, and current lender.\n\n"
    "The 10 borrowers with the highest rate spread are all with a competitor lender, "
    "and their rate spreads range from **806** to **830** basis points. Notable data "
    "points include:\n"
    "- **B-165PEPSZOHHBT** (DUNCANVILLE, 830 bps, Competitor Other)\n"
    "- **B-1KNF2U88QWGSI** (STEGER, 830 bps, Competitor Other)\n"
    "- **B-0ITNDNF0JC7VA** (CICERO, 821 bps, Competitor Other)\n"
    "- **B-1WLTGGYHCETP6** (SAMMAMISH, 821 bps, Competitor Other)\n"
    "- **B-19BUASO7GMQFS** (DALLAS, 818 bps, Competitor Other)\n"
    'All borrowers in this sample are with "Competitor Other" as their current '
    "lender. Source: mip.gold.borrower_360."
)


@pytest.mark.parametrize(
    "narrative",
    [
        pytest.param(_LIVE_REFI_HELOC_TOP_10, id="opportunity-score"),
        pytest.param(_LIVE_HIGHEST_RATE_SPREAD_TOP_10, id="competitor-other"),
    ],
)
def test_withheld_live_borrower_row_narratives_now_render(narrative: str) -> None:
    assert genie_visible_text_unsafe(narrative) is False


@pytest.mark.parametrize(
    "label",
    [
        pytest.param("Opportunity Score", id="opportunity-score"),
        pytest.param("Lead Score", id="lead-score"),
        pytest.param("Confidence Score", id="confidence-score"),
        pytest.param("Competitor Other", id="competitor-other"),
    ],
)
def test_governed_label_alone_is_not_an_identity(label: str) -> None:
    assert genie_visible_text_unsafe(f"{label} drives the ranking for this cohort.") is False


@pytest.mark.parametrize(
    "narrative",
    [
        # The exempted words are suffix-only and literal-only. A real name in
        # the same sentence is untouched by either relaxation.
        pytest.param(
            "- **B-165PEPSZOHHBT** (DUNCANVILLE, 830 bps, Competitor Other), "
            "handled by John Smith.",
            id="name-beside-competitor-other",
        ),
        pytest.param(
            "Opportunity Score 91 for Mary Johnson.",
            id="name-beside-opportunity-score",
        ),
        pytest.param("Score Robert Wilson first.", id="score-as-a-leading-verb"),
    ],
)
def test_person_names_beside_the_governed_labels_still_fail_closed(narrative: str) -> None:
    assert genie_visible_text_unsafe(narrative) is True
