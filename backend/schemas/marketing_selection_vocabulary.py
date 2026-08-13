"""The closed reviewed-attribute vocabulary the selection policy is built on.

Split out of ``marketing_selection_criteria`` when that module crossed the
900-line gate. Pure vocabulary and the one compiled matcher over it: no
clause machinery, no decisions. ``marketing_safety_terms`` already imported
two of these fragments, so the split also stops a policy module importing a
policy module for a constant.
"""

from __future__ import annotations

import re

# An article does not change WHICH attribute is being named: "the highest
# opportunity scores" selects on ``opportunity_score`` exactly as "highest
# opportunity scores" does. It belongs to the fragment, not to any one
# alternative and not to any one embedding site.
#
# It was first added inline on the potential/upside alternative (2026-08-08)
# and then again as a prefix on ``_REVIEWED_MORTGAGE_ATTRIBUTE_FULL_RE``
# (2026-08-12). Neither reaches the clause patterns in
# ``marketing_selection_criteria``, which embed the LIST fragment directly and
# never consult the full matcher -- so every reviewed attribute except
# potential/upside stayed unreviewed behind an article.
#
# Live on paychex 2026-08-12, one word apart:
#   "Show me the top borrowers with the highest opportunity scores." refused in
#   ~2s with "it is outside the reviewed Module 0 vocabulary", no SQL;
#   "Show me the top borrowers with highest opportunity scores." answered in
#   ~37s, source `genie`, governed SQL against `mip.gold.lead_population`, 10
#   real rows. `opportunity_score` is the product's own ranking column.
# Measured matrix: `the` refused across `opportunity score`, `lead score`,
# `home equity`, `rate spread`, `LTV`, `loan balance`, `property value`,
# `competitor lien` and `recommended offer`; every one of them passed bare.
#
# Hoisted here so the article is admitted once, for every alternative and at
# every embedding site, instead of per-alternative -- that asymmetry is the
# bug, twice over. This does NOT weaken the fail-closed default: the
# alternation below is untouched, so "the highest credit scores" and "the
# highest FICO" stay unreviewed (control: both still refused live), and a
# reviewed conjunct cannot carry an unreviewed one ("the home equity and
# eczema" still fails closed). Measured over 4,102 prompt probes: 803 newly
# answerable, every one an article-prefixed form of a question that already
# passed, and zero unreviewed attributes among them. The tests
# ``test_an_article_never_admits_an_unreviewed_attribute`` and
# ``test_an_article_cannot_launder_an_unreviewed_attribute_through_a_reviewed_one``
# pin both halves.
_REVIEWED_ATTRIBUTE_ARTICLE = r"(?:(?:an?|the)\s+)?"

# A count QUANTIFIES the population it precedes -- "the top 50 borrowers", "the
# next 1,000 leads". It selects nobody on its own, and it lives here because two
# other modules need the identical definition: ``marketing_selection_criteria``
# (both lead-ins) and ``marketing_audience_admission`` (the bounded modifier
# run).
#
# DIGITS ONLY, and the SPELLED-OUT half is deliberately absent.
#
# It was built ("the top twenty-five borrowers", a closed 81-form cardinal list,
# fold-stable across the de-hyphenated variant) and then pulled, because an
# independent audit found what it is coupled to. Every admission pattern embeds
# ``_MODIFIERS``, and ``remove_audience_admission_clauses_for_identity_scan``
# DELETES from the identity scan every clause that parses as an admission. Word
# cardinals made 550 more clauses parse, so 550 more were deleted before
# ``contains_borrower_copy_contextual_name`` -- the sole person-name detector in
# the deep audit-metadata value scan (``audit_store._metadata_pii_value_paths``)
# -- ever read them.
#
# Measured on a 1,320-prompt battery (12 admission shapes x 10 name payloads x
# 11 count spellings), branch vs origin/main: 106 name detections lost, every
# one carrying a spelled-out count, ZERO carrying a digit. The digit half is
# unchanged from main on that surface, which is why it stays.
#
# Landing the word half needs the coupling fixed first -- the cheapest correct
# form is to gate the deletion on ``shares_token_with_person_lexicon``, the same
# "recognition is not exemption" rule the place-name masks already apply -- plus
# the control battery that surface has never had. Filed separately.
POPULATION_QUANTIFIER_DIGITS = r"(?:[0-9]{1,3}(?:,[0-9]{3})*|[0-9]+)"
POPULATION_QUANTIFIER_FRAGMENT = POPULATION_QUANTIFIER_DIGITS

REVIEWED_MORTGAGE_ATTRIBUTE_FRAGMENT = (
    rf"{_REVIEWED_ATTRIBUTE_ARTICLE}"
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
    r"helocs?|home[- ]?equity\s+lines?(?:\s+of\s+credit)?|"
    r"eligib(?:le|ility)\s+for\s+(?:an?\s+)?(?:heloc|refi(?:nance)?|home[- ]?equity(?:\s+line)?)|"
    r"timely\s+retention\s+review\s+signal|"
    r"(?:reviewed|eligible)\s+segment\s+membership|"
    # Modeled potential/upside is the product's own scoring concept
    # (opportunity score), phrased the way growth leaders ask for it. "the
    # top 10 borrowers with the highest potential" failed closed as an
    # unknown criterion (live capture, 2026-08-08). The leading article that
    # capture needed is now hoisted onto the fragment, so this alternative no
    # longer carries its own -- carrying it here is what made potential/upside
    # the one reviewed attribute an article did not refuse.
    r"(?:absolute\s+)?"
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
    r"(?:especially\s+|particularly\s+|very\s+)?"
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
    r"(?:home[- ]?equity|equity\s+(?:pct|percent(?:age)?|share))|"
    # The governed next-best-offer column (recommended_offer). Qualifier
    # required: a bare "offer" is ordinary campaign vocabulary, this is the
    # product's own ranked recommendation.
    r"(?:next[- ]?best|recommended)\s+offers?)"
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

# A numeric threshold does not change WHICH attribute is being named. "a rate
# spread above 150 basis points" is the reviewed ``rate_spread_bps`` column
# with a bound on it, but the criterion is matched whole, so the article and
# the bound both made it unreviewed -- the whole ranking/threshold family
# refused (measured over a 9,677-question corpus, 2026-08-12: "Show me
# borrowers with a rate spread above 150 basis points", "Rank borrowers with
# an opportunity score above 80"). The article half of that fix now lives on
# ``REVIEWED_MORTGAGE_ATTRIBUTE_FRAGMENT`` above, where the clause patterns
# that embed the fragment directly can see it too; this constant keeps the
# bound, which is meaningful only against a whole criterion.
#
# This cannot admit an unreviewed attribute: the alternation is untouched, so
# "a FICO above 740" and "borrowers with eczema" stay unreviewed exactly as
# before. Deliberately NOT added: FICO, credit score, monthly savings and
# balance at risk. Measured against the live schema on 2026-08-12 --
# mip.gold.borrower_360 has 101 columns and NONE of them is fico, credit,
# savings or risk. Reviewing vocabulary for a measure the product does not
# have would be inventing a capability, not clearing a false positive.
#
# The number slot stays DIGITS ONLY, and that is load-bearing. Widening it to
# accept the de-obfuscator's fold images was tried on 2026-08-12 and reverted
# the same day: the reachable alphabet is exactly {o,l,e,a,s,t,i} plus digits,
# an unbounded run of which spells ``otitis``, ``stasis``, ``asia``,
# ``silesia``, ``tallit`` and the surnames ``salas`` and ``sotelo`` -- 584 of
# 596 probe tokens flipped from refused to allowed, on all five validators.
#
# The fold was the reason this slot could not be reached at all: the criterion
# was matched against variants that translate digits to lookalike letters
# (``str.maketrans("013457", "oleast")``), so "above 150 bps" arrived as "above
# lso bps" and the threshold family refused. That was the "unblock it where the
# variants are BUILT" fix this comment used to await, and it has landed --
# #217 scoped the fold away from numbers, and the criterion machine stopped
# being handed the unscoped variants (2026-08-12). "Rank borrowers with a rate
# spread above 150 basis points" is answered now.
#
# Still open, and unrelated to the fold: ``_REVIEWED_DIRECTIVE_CRITERION`` in
# ``marketing_selection_criteria`` embeds the LIST fragment without this
# threshold, so only the formation-verb branch reads a bound. "Show me
# borrowers with a rate spread above 150 basis points" therefore still refuses.
REVIEWED_ATTRIBUTE_THRESHOLD = (
    r"(?:\s+(?:above|over|below|under|at\s+least|at\s+most|greater\s+than|"
    r"less\s+than|more\s+than|fewer\s+than|of\s+at\s+least|of\s+at\s+most|"
    r">=?|<=?)\s*[0-9][0-9,.]*\s*"
    r"(?:bps|basis\s+points?|%|percent(?:age)?(?:\s+points?)?|points?)?)?"
)
_REVIEWED_ATTRIBUTE_THRESHOLD = REVIEWED_ATTRIBUTE_THRESHOLD

# The reviewed list WITH a bound on it, as its own constant.
#
# The bound deliberately does NOT go inside ``REVIEWED_MORTGAGE_ATTRIBUTE_FRAGMENT``
# even though that is where the article and the aggregate qualifier ended up.
# The fragment is also consumed PRENOMINALLY, by
# ``_REVIEWED_PRENOMINAL_AUDIENCE_DIRECTIVE_RE`` in
# ``marketing_selection_criteria``, where a trailing bound would parse "Select
# the rate spread above 150 borrowers" and lengthen the span between an
# attribute and a population noun that can be a bare pronoun. Silent inheritance
# by an embedding site nobody was thinking about is exactly how the previous two
# fixes over-reached; a separate constant means each site opts in.
REVIEWED_MORTGAGE_ATTRIBUTE_BOUND_LIST_FRAGMENT = (
    rf"(?:{REVIEWED_MORTGAGE_ATTRIBUTE_FRAGMENT}){REVIEWED_ATTRIBUTE_THRESHOLD}"
    rf"(?:\s+(?:and|or)\s+(?:{REVIEWED_MORTGAGE_ATTRIBUTE_FRAGMENT})"
    rf"{REVIEWED_ATTRIBUTE_THRESHOLD}){{0,3}}"
)

# The two tails ``_normalize_criterion`` used to DELETE, absorbed here instead.
# Deleting them was a fail-open: both strips ended in ``.*$``, so everything
# after the tail vanished before any vocabulary check and a reviewed head
# admitted an unscanned remainder --
#
#     "a rate spread for the campaign and eczema"
#       -> truncated to "a rate spread" -> reviewed -> allowed
#       -> "eczema" never scanned by anything
#
# Anchored here, the same sentence no longer matches (the remainder is still
# in the string), so it fails closed. What a reviewed purpose or call-to-action
# may contain is now a closed vocabulary rather than "anything at all".
#
# ``and then`` is spelled out as its own alternative because "and then contact
# them" is the commonest form of this tail and ``then`` otherwise lands in the
# modal slot, refusing 143 strings the truncation had been silently accepting
# ("Only select them by home equity for the campaign and then contact them").
_REVIEWED_ATTRIBUTE_CTA_TAIL = (
    r"(?:\s+(?:and\s+then|and|then)\s+(?:(?:may|can|should)\s+)?"
    r"(?:contact|call|email|text|message|reply)"
    r"(?:\s+(?:them|us|me|him|her|"
    r"(?:the\s+)?(?:borrowers?|homeowners?|customers?|leads?|candidates?|"
    r"prospects?|clients?|owners?)))?"
    r"(?:\s+(?:about|regarding|with|on)\s+(?:(?:the|their|an?)\s+)?"
    r"(?:offers?|options?|reviews?|campaigns?|refinance|refi|heloc|"
    r"home[- ]equity(?:\s+lines?)?|mortgages?|loans?|rates?|savings?))?)?"
)
_REVIEWED_ATTRIBUTE_CLOSING = r"(?:\s+(?:too|as\s+well))?"

_REVIEWED_MORTGAGE_ATTRIBUTE_FULL_RE = re.compile(
    rf"^(?:{REVIEWED_MORTGAGE_ATTRIBUTE_LIST_FRAGMENT})"
    rf"{_REVIEWED_ATTRIBUTE_THRESHOLD}"
    rf"{REVIEWED_ATTRIBUTE_PURPOSE_FRAGMENT}"
    rf"{_REVIEWED_ATTRIBUTE_CTA_TAIL}"
    rf"{_REVIEWED_ATTRIBUTE_CLOSING}$",
    re.IGNORECASE,
)
