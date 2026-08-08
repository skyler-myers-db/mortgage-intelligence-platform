"""Reviewed read-only analytics question shapes.

Closed-vocabulary grammars for analytics questions the product itself asks
(offer mix by segment, ranked product-intent cohorts, permit source-gap
probes). Split from the criterion machine when it crossed the size gate
(2026-08-08); the vocabulary and shapes are unchanged. Every fragment stays
closed so an unknown criterion can never ride an approved shape.
"""

from __future__ import annotations

import re

_REVIEWED_ANALYTIC_POPULATION = (
    r"(?:(?:(?:in[- ]the[- ]money|listed|refinance[- ]ready|retention[- ]risk|"
    r"marketing[- ]eligible|investor|equity[- ]rich|highest[- ]scoring)\s+)?"
    r"(?:borrowers?|leads?|applicants?|homeowners?|customers?|cohorts?|populations?))"
)
_REVIEWED_ANALYTIC_DIMENSION = (
    r"(?:(?:current\s+coverage|covered)\s+)?(?:states?|count(?:y|ies)|"
    r"zip(?:\s+codes?)?|postal\s+codes?|markets?|metros?|"
    r"segments?|lead\s+scores?|opportunity\s+scores?|equity|ltv|loan[- ]to[- ]value|"
    r"rate[- ]spreads?|listing\s+time(?:\s+on\s+market)?)"
)
_REVIEWED_ANALYTIC_MEASURE = (
    r"(?:listing\s+time\s+on\s+market|lead\s+score|opportunity\s+score|"
    r"borrower\s+count|equity(?:\s+percentage)?|ltv|loan[- ]to[- ]value|rate[- ]spread)"
)
_REVIEWED_ANALYTIC_LOCATION = (
    r"(?:\s+(?:in|across)\s+(?:the\s+current\s+(?:coverage|portfolio)|"
    r"[A-Za-z]{2}|[A-Z][A-Za-z' -]{2,40}))?"
)
# Governed Module 0 product-intent cohorts. Closed list: these are offer
# codes/segment names the product models, never free-text criteria.
_REVIEWED_PRODUCT_INTENT = (
    r"(?:cash[- ]out|heloc|home[- ]equity|refi(?:nance)?|rate[- ]and[- ]term|"
    r"purchase|listed(?:[- ]for[- ]sale)?|investor|multi[- ]property|"
    r"retention|recapture|in[- ]the[- ]money|high[- ]equity)"
)
_REVIEWED_READ_ONLY_ANALYTIC_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        # Offer strategy by segment ("which offer should we lead with for each
        # segment, and why?"). The audience grammar read the affirmative
        # "lead with ... for each segment" as an unreviewed audience decision;
        # it is the product's core read-only offer-mix question. Live persona
        # audit 2026-08-07 (marketing-leader).
        r"^(?:which|what)\s+(?:next[- ]best\s+)?offers?\s+"
        r"(?:should|do|would)\s+(?:we|i|the\s+team)\s+"
        r"(?:lead\s+with|use|present|recommend|make|pitch|prioriti[sz]e)\s+"
        r"(?:for|to|with)\s+(?:each|every|the|our)?\s*"
        rf"(?:{_REVIEWED_ANALYTIC_DIMENSION}|{_REVIEWED_ANALYTIC_POPULATION})"
        r"(?:\s*,?\s*and\s+why)?$",
        re.IGNORECASE,
    ),
    re.compile(
        # Ranked product-intent cohort with a per-row rationale ("rank the top
        # cash-out candidates in Texas and explain why each one qualifies").
        # The intent vocabulary is closed, so an unknown criterion cannot ride
        # this shape. Live persona audit 2026-08-07 (sales-manager).
        r"^(?:rank|show|list|give\s+me|surface|prioriti[sz]e)\s+(?:me\s+)?(?:the\s+)?"
        r"(?:top\s+(?:[0-9]{1,3}\s+)?)?"
        # One or two stacked intent tokens ("in-the-money refi", "purchase
        # mortgage") — still drawn from the same closed vocabulary, so an
        # unknown criterion cannot ride the second slot. Live persona audit
        # 2026-08-07 (co-pilot flagship objective).
        rf"{_REVIEWED_PRODUCT_INTENT}(?:[\s-]+(?:{_REVIEWED_PRODUCT_INTENT}|mortgage|loan))?\s+"
        r"(?:candidates?|borrowers?|leads?|opportunities)"
        rf"{_REVIEWED_ANALYTIC_LOCATION}"
        r"(?:\s*,?\s*and\s+(?:explain|tell\s+me|describe|show)\s+"
        r"(?:me\s+)?why\s+(?:each|every)(?:\s+one)?\s+"
        r"(?:qualifies|ranks?|scores?|is\s+(?:a\s+)?(?:strong|good)(?:\s+candidate)?))?$",
        re.IGNORECASE,
    ),
    re.compile(
        # Reviewed-population cohort carrying a closed product-intent
        # with-clause ("show customers with an in-the-money refi", "show
        # investor borrowers with multiple properties" — the Owner Link
        # domain rule). The with-clause alternatives are a closed set; a
        # free-text criterion ("with zyrplax", "with eczema") does not match
        # and falls through to the strict criterion machine. Live persona
        # audit 2026-08-07 (co-pilot).
        r"^(?:show|find|list|rank|surface|give\s+me)\s+(?:me\s+)?(?:the\s+)?"
        rf"(?:{_REVIEWED_PRODUCT_INTENT}\s+)?"
        r"(?:customers?|borrowers?|leads?|candidates?|prospects?|homeowners?|owners?)\s+"
        r"with\s+(?:"
        rf"(?:(?:a|an|the)\s+)?{_REVIEWED_PRODUCT_INTENT}"
        rf"(?:[\s-]+(?:{_REVIEWED_PRODUCT_INTENT}|mortgage|loan|refi|refinance|position|offer|opportunity))?"
        r"|multiple\s+properties"
        r")"
        rf"{_REVIEWED_ANALYTIC_LOCATION}$",
        re.IGNORECASE,
    ),
    re.compile(
        # Building-permit data is an explicit product source gap. This closed
        # read-only query must reach the Genie source-gap policy rather than
        # being mislabeled as protected-class targeting merely because it
        # uses the product noun ``candidates``.
        r"^(?:show|list|find|identify)\s+(?:me\s+)?(?:the\s+)?"
        r"(?:heloc|home[- ]equity)\s+candidates?\s+with\s+"
        r"(?:recent|filed)\s+(?:building\s+)?permits?\s+and\s+"
        r"(?:strong|high)\s+equity$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:show|list|count)\s+(?:me\s+)?(?:the\s+)?"
        r"(?:approved|assigned|queued)\s+leads?\s+"
        r"(?:that|who)\s+(?:have|has)\s+not\s+been\s+"
        r"(?:touched|contacted|called)\s+in\s+[0-9]{1,3}\s+days?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:chart|plot|graph|visualize|display|show|list|count|rank|order|group|compare|"
        r"break\s+down)\s+(?:me\s+)?(?:the\s+)?"
        r"(?:(?:top|bottom)\s+(?:[0-9]{1,3}|ten|twenty(?:[- ]five)?)\s+)?"
        rf"{_REVIEWED_ANALYTIC_POPULATION}\s+"
        r"(?:(?:by|grouped\s+by|ordered\s+by|ranked\s+by)\s+)"
        rf"{_REVIEWED_ANALYTIC_DIMENSION}{_REVIEWED_ANALYTIC_LOCATION}$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:what\s+is|calculate|show|display|report|compare)\s+(?:me\s+)?(?:the\s+)?"
        r"(?:average|median|mean|total|minimum|maximum|distribution\s+of)\s+"
        rf"{_REVIEWED_ANALYTIC_MEASURE}\s+(?:for|across)\s+(?:the\s+)?"
        rf"{_REVIEWED_ANALYTIC_POPULATION}\s+by\s+"
        rf"{_REVIEWED_ANALYTIC_DIMENSION}{_REVIEWED_ANALYTIC_LOCATION}$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^(?:which|what)\s+{_REVIEWED_ANALYTIC_DIMENSION}\s+"
        r"(?:leads?|ranks?\s+(?:highest|lowest)|has\s+(?:the\s+)?"
        r"(?:highest|lowest|most|fewest)\s+(?:count|score|borrowers?|leads?))$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^how\s+(?:has|have)\s+(?:the\s+)?{_REVIEWED_ANALYTIC_POPULATION}\s+"
        r"(?:moved|changed|shifted|trended)(?:\s+(?:recently|over\s+time|this\s+week))?$",
        re.IGNORECASE,
    ),
)
