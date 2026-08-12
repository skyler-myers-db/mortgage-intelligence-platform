"""Reviewed Module 0 mortgage attribute vocabulary.

The closed set of borrower/loan attributes a governed directive may select on
(rate spread, equity, LTV, opportunity and lead scores, listings, liens,
modeled potential), plus the aggregate qualifiers and purpose suffix that
describe one without becoming a new criterion. Split from the criterion
machine when it crossed the size gate (2026-08-12), exactly as the reviewed
analytics shapes were on 2026-08-08; the vocabulary is unchanged, byte for
byte. The grammar that consumes it stays in
``marketing_selection_criteria``.

Every alternative here is closed. Widening one widens what a reviewed
directive may select on, so a change needs the same captured failing case any
other vocabulary relaxation does.
"""

from __future__ import annotations

import re

REVIEWED_MORTGAGE_ATTRIBUTE_FRAGMENT = (
    # An aggregate qualifier is a DESCRIPTOR of a reviewed attribute, not a new
    # selection criterion: "average rate spread" selects on rate spread exactly
    # as "rate spread" does. These were admitted on the opportunity/lead-score
    # alternative below and nowhere else, so the same aggregate flipped every
    # OTHER reviewed attribute to unreviewed and the prompt failed closed.
    #
    # Live on paychex 2026-08-11: "Rank our segments by average rate spread."
    # was refused with "it is outside the reviewed Module 0 vocabulary" in
    # ~1s, before any repository call -- while
    # `_canonical_mean_rate_spread_by_segment_scope` already matched that exact
    # string and its canonical SQL was sitting there unreachable. Measured
    # matrix: `rate spread` passed bare and with "high", and refused with
    # "average", "mean" and "highest"; `home equity`, `LTV`, `loan balance` and
    # `property value` behaved the same way.
    #
    # This does NOT weaken the fail-closed default. The attribute alternation
    # below is unchanged, so an UNREVIEWED attribute stays unreviewed with or
    # without a qualifier -- "average credit score" still refuses, and
    # ``test_aggregate_qualifiers_never_admit_an_unreviewed_attribute`` pins it.
    r"(?:(?:average|avg|mean|median|typical|high(?:est)?|low(?:est)?|top|bottom)\s+)?"
    r"(?:(?:high|strong|substantial|sufficient|available|usable)\s+(?:home[- ]?)?equity|"
    r"substantial\s+modeled\s+(?:home[- ]?)?equity|"
    r"(?:high|low|rising|falling|current|elevated)\s+"
    r"(?:mortgage\s+)?(?:rates?|payments?)|"
    r"(?:high|low|current)?\s*(?:ltv|loan[- ]?to[- ]?value(?:\s+ratio)?)|"
    r"(?:current|high|low|remaining|outstanding)?\s*(?:loan|mortgage)\s+balances?|"
    r"(?:listed|active[- ]?listing)\s+(?:homes?|properties)|"
    r"(?:active\s+)?(?:property|home)\s+listings?|"
    r"(?:strong|high|positive|current)?\s*rate[- ]?spreads?|"
    r"(?:refinance|refi|mortgage)\s+economics|"
    r"(?:high|low|current)?\s*(?:property|home)\s+values?|"
    r"(?:competitor|second|existing)\s+liens?|"
    r"(?:heloc|home[- ]?equity)\s+(?:intent|propensity)|"
    # Governed Module 0 scoring/eligibility columns (2026-08-07): these are
    # reviewed product attributes (opportunity_score, marketing_eligible,
    # the >=35% HELOC-eligibility floor), not unknown-criterion surface.
    r"(?:high(?:est)?|top|low(?:est)?|average|mean)?\s*(?:opportunity|lead)\s+scores?|"
    r"marketing[- ]?eligib(?:le|ility)|"
    r"(?:heloc|home[- ]?equity)[- ]?eligib(?:le|ility)|"
    r"(?:an?\s+)?helocs?|(?:an?\s+)?home[- ]?equity\s+lines?(?:\s+of\s+credit)?|"
    r"eligib(?:le|ility)\s+for\s+(?:an?\s+)?(?:heloc|refi(?:nance)?|home[- ]?equity(?:\s+line)?)|"
    r"timely\s+retention\s+review\s+signal|"
    r"(?:reviewed|eligible)\s+segment\s+membership|"
    # Modeled potential/upside is the product's own scoring concept
    # (opportunity score), phrased the way growth leaders ask for it. "the
    # top 10 borrowers with the highest potential" failed closed as an
    # unknown criterion (live capture, 2026-08-08).
    r"(?:the\s+)?(?:absolute\s+)?"
    r"(?:high(?:est)?|top|strong(?:est)?|great(?:est)?|most|best)?\s*"
    r"(?:refi(?:nance)?|heloc|growth|opportunity|conversion)?\s*"
    r"(?:potential|upside)|"
    # Answer-format adverbials: "recommend the best offer for each with
    # reasoning" asks for an explained answer, not a selection criterion.
    # The ambiguous-relationship grammar reads "with <object>" as a
    # criterion, so the closed adverbial vocabulary lives here (same
    # capture).
    r"(?:(?:full|clear|detailed|complete|supporting)\s+)?"
    r"(?:reasoning|rationale|justifications?|explanations?)|"
    # Assessment nouns: "evaluate why each borrower is an especially good
    # candidate" is a why-question about the product's own ranking, but the
    # declarative co-reference grammar reads "is <X>" as assigning criterion
    # X. The assessment vocabulary is the product's own ("strong candidate"
    # already appears in the reviewed analytics shapes). Same capture.
    r"(?:an?\s+)?(?:especially\s+|particularly\s+|very\s+)?"
    r"(?:strong|good|great|excellent|prime|ideal|top|promising)\s+"
    r"(?:candidates?|prospects?|fits?|matches?|opportunit(?:y|ies))|"
    r"(?:fixed|adjustable)[- ]?rate\s+(?:mortgages?|loans?)|"
    # Bare "home equity" / "equity percentage". Every other equity alternative
    # above REQUIRES a qualifier ("strong equity", "substantial equity"), so
    # "Rank our segments by home equity" -- a plain analytics question about
    # the signal this product is built on (CLAUDE.md: HELOC/Cash-out
    # candidates = strong equity) -- failed closed as an unknown criterion,
    # with or without an aggregate. Deliberately anchored on "home" or on an
    # explicit percentage noun: bare "equity" on its own is left out because
    # it is also the DEI sense of the word, and the protected-class terms are
    # matched before this fragment is ever consulted. Placed last so it cannot
    # shadow "home equity line of credit" or "home equity intent" above.
    r"(?:home[- ]?equity|equity\s+(?:pct|percent(?:age)?|share)))"
)
REVIEWED_MORTGAGE_ATTRIBUTE_LIST_FRAGMENT = (
    rf"(?:{REVIEWED_MORTGAGE_ATTRIBUTE_FRAGMENT})"
    rf"(?:\s+(?:and|or)\s+(?:{REVIEWED_MORTGAGE_ATTRIBUTE_FRAGMENT})){{0,3}}"
)
REVIEWED_ATTRIBUTE_PURPOSE_FRAGMENT = (
    r"(?:\s+for\s+(?:(?:this|the|a)\s+)?"
    r"(?:(?:refi|refinance|heloc|home[- ]equity|retention|portfolio|purchase|"
    r"mortgage|loan|servicing)\s+)?(?:campaign|offer|options?|review))?"
)

_REVIEWED_MORTGAGE_ATTRIBUTE_FULL_RE = re.compile(
    rf"^(?:{REVIEWED_MORTGAGE_ATTRIBUTE_LIST_FRAGMENT})$",
    re.IGNORECASE,
)
