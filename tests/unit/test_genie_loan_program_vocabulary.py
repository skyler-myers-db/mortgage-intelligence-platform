"""Loan PROGRAM is a reviewed Module 0 attribute, like loan rate structure.

``(?:fixed|adjustable)-rate loans`` was reviewed and the loan program was not,
which is an asymmetry rather than a decision: ``loan_product_type`` is a
governed ``mip.gold.borrower_360`` column with its own canonical UC function
(``sql/uc_functions/fn_loan_product_type.sql``), and it is populated. Measured
live on paychex 2026-08-12:

    conventional 2,306,974 | fha 303,977 | jumbo 278,900 | va 68,071
    other 96,187           | NULL 2,102,075 (no first lien / unknown)

So "Break down borrowers by loan type." failed closed as an unknown criterion
against a column the product models, Genie can query, and the CNV work in
#179/#185 was specifically about.

The alternation is anchored on a loan/mortgage noun. That is load-bearing: a
bare ``va`` is the USPS abbreviation for Virginia and a bare ``conventional``
is an ordinary adjective, so requiring the noun keeps this to the product's own
program vocabulary.
"""

from __future__ import annotations

import pytest

from backend.services.genie_deterministic import _protected_prompt_match

# Live-shaped asks against the governed column.
_ANSWERABLE = (
    "Break down borrowers by loan type.",
    "Break down borrowers by loan product.",
    "Rank borrowers by loan program.",
    "Show borrowers with conventional loans.",
    "Show me borrowers with a VA loan.",
    "How many borrowers have jumbo loans?",
    "Show borrowers with FHA mortgages.",
    "Which borrowers have conforming loans?",
    "Show me the average opportunity score by loan type.",
)

# The fail-closed default has to be exactly as strong as before. None of these
# names a governed column; gold has 101 columns and none is fico/credit/income.
_STILL_UNREVIEWED = (
    "Show borrowers with a credit score above 740.",
    "Break down borrowers by FICO.",
    "Rank borrowers by household income.",
    "Show borrowers by citizenship.",
    "Break down borrowers by employment length.",
    "Show borrowers with a net worth above 1 million.",
)

# The bare tokens the anchor exists to keep out. A US state abbreviation and an
# ordinary adjective must not become selection vocabulary.
_ANCHOR_HOLDS = (
    "Show borrowers in VA.",
    "How many borrowers are in Virginia?",
    "Compare VA and MD on cash-out candidates.",
    "Which states have conventional wisdom about refinancing?",
)


@pytest.mark.parametrize("question", _ANSWERABLE)
def test_loan_program_questions_are_answerable(question: str) -> None:
    assert _protected_prompt_match(question) is None, question


@pytest.mark.parametrize("question", _STILL_UNREVIEWED)
def test_unreviewed_attributes_stay_unreviewed(question: str) -> None:
    assert _protected_prompt_match(question) == "unreviewed_criterion", question


@pytest.mark.parametrize("question", _ANCHOR_HOLDS)
def test_the_loan_noun_anchor_admits_no_bare_token(question: str) -> None:
    """These must not be refused, and must not be refused *for this reason*.

    ``Show borrowers in VA.`` is a geography question and passes either way;
    what this pins is that adding ``va`` to the vocabulary did not do it by
    making a bare ``VA`` a reviewed criterion — the fragment requires a
    loan/mortgage noun after it.
    """

    import re

    from backend.schemas.marketing_selection_vocabulary import (
        REVIEWED_MORTGAGE_ATTRIBUTE_FRAGMENT,
    )

    fragment = re.compile(REVIEWED_MORTGAGE_ATTRIBUTE_FRAGMENT, re.IGNORECASE)
    assert fragment.fullmatch("va") is None
    assert fragment.fullmatch("conventional") is None
    assert fragment.fullmatch("fha") is None
    assert fragment.fullmatch("va loans") is not None
    assert _protected_prompt_match(question) is None, question
