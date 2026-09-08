"""Reviewed read-only analytics question shapes.

Closed-vocabulary grammars for analytics questions the product itself asks
(offer mix by segment, ranked product-intent cohorts, permit source-gap
probes). Split from the criterion machine when it crossed the size gate
(2026-08-08); the vocabulary and shapes are unchanged. Every fragment stays
closed so an unknown criterion can never ride an approved shape.
"""

from __future__ import annotations

import re

from backend.schemas.marketing_selection_vocabulary import REVIEWED_MORTGAGE_ATTRIBUTE_FRAGMENT

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
#
# ``[- ]?`` on every compound and one spelled fold image (``lnvestor``): the
# protected-class scanner also runs the criterion machine over a de-hyphenated
# variant ("in-the-money" -> "inthemoney") and an ASCII-confusable variant that
# reads a capital ``I`` as ``l`` ("Investor" -> "lnvestor"), and ONE tripping
# variant refuses the whole prompt (#228; the same rule spells
# ``(?:insert|lnsert)`` in the formation grammar). Both are closed images of
# already reviewed words, never a fold relaxation: an unreviewed intent still
# de-obfuscates and fails closed.
_REVIEWED_PRODUCT_INTENT = (
    r"(?:cash[- ]?out|heloc|home[- ]?equity|refi(?:nance)?|rate[- ]?and[- ]?term|"
    r"purchase|listed(?:[- ]?for[- ]?sale)?|investor|lnvestor|multi[- ]?property|"
    r"retention|recapture|in[- ]?the[- ]?money|high[- ]?equity)"
)
# Signal columns a planned sub-analysis may name alongside a ranked cohort.
# Closed: every entry is a governed gold column or Module 0 domain signal
# (CLAUDE.md domain rules), never free text.
_REVIEWED_ANALYTIC_SIGNAL = (
    # An aggregate qualifier describes how a governed signal is summarized
    # ("average rate spread"); it names no new signal -- the rule
    # ``REVIEWED_MORTGAGE_ATTRIBUTE_FRAGMENT`` already applies. The unit and
    # ratio suffixes are how the planner spells the governed columns
    # (``rate_spread_bps``, ``ltv``): "rate spread bps", "loan-to-value ratio".
    # Live planner capture 2026-09-08.
    r"(?:(?:average|avg|mean|median)\s+)?"
    r"(?:opportunity\s+scores?|lead\s+scores?|rate\s+spreads?(?:\s+bps)?|"
    r"equity(?:\s+percentage)?|ltv|loan[- ]?to[- ]?value(?:\s+ratios?)?|"
    r"key\s+triggers?|triggers?|recommended\s+offers?|"
    r"next[- ]?best\s+offers?|segment\s+memberships?|segments?|listing\s+status|"
    r"competitor\s+liens?|listed\s+for\s+sale|investor\s+status|"
    r"retention\s+risk|heloc\s+propensity)"
)
_REVIEWED_ANALYTIC_SIGNAL_LIST = (
    rf"{_REVIEWED_ANALYTIC_SIGNAL}(?:\s*,?\s*(?:and\s+)?{_REVIEWED_ANALYTIC_SIGNAL})*"
)
# Premodifiers a planner puts between "top" and the cohort noun. Every entry
# is a governed Module 0 label: the eligibility flags, the segment display
# names (gold_segment_population.sql meta -- "Investor / Multi-Property" is two
# intents joined by the slash the label itself carries; "Retention Risk"), the
# competitor-lien signal, and the retention population "current customers"
# (CLAUDE.md domain rules: Retention/Recapture = current/former customers).
#
# Captured live 2026-09-08 from the governed space's own deep-analysis plan:
# "the top current-customer borrowers in the retention-risk cohort", "the top
# Investor / Multi-Property borrowers" and "the top current customers with
# retention or recapture risk" each fell to the population-directive tail of
# the criterion machine (a lead-in phrase, a population noun, a ``by``/``with``
# connector), and every drop cost the sweep one section.
_REVIEWED_COHORT_PREMODIFIER = (
    r"(?:eligible|marketing[- ]?eligible|outreach[- ]?ready|highest[- ]?scoring|"
    r"current(?:[- ]?customer)?|retention[- ]?risk|competitor[- ]?lien|"
    rf"{_REVIEWED_PRODUCT_INTENT}(?:\s*/\s*{_REVIEWED_PRODUCT_INTENT})?)"
)
# "the top 20 (eligible) borrowers" — the cohort noun a planned deep analysis
# names in nearly every sub-question. "top-tier" is the same ranked cohort
# named by its band instead of its count.
_REVIEWED_TOP_COHORT = (
    r"(?:the\s+)?top(?:[- ]?tier)?\s+(?:[0-9]{1,3}\s+)?"
    rf"(?:{_REVIEWED_COHORT_PREMODIFIER}\s+)?"
    r"(?:borrowers?|leads?|candidates?|opportunities|customers?)"
)
# A ranked cohort scoped to a governed segment ("in the retention-risk
# cohort", "within the retention-risk or competitor-lien cohorts").
_REVIEWED_COHORT_SCOPE = (
    rf"(?:\s+(?:in|within)\s+the\s+{_REVIEWED_COHORT_PREMODIFIER}"
    rf"(?:\s+or\s+{_REVIEWED_COHORT_PREMODIFIER})?\s+(?:cohorts?|segments?))"
)
# The Retention Risk segment's own description bound to its population with
# ``with`` ("current customers with retention or recapture risk"; the gold
# label reads "Current-customer or recapture signals"). Measured 2026-09-08:
# this phrase alone refused eight of nine planned lines and aborted the sweep.
_REVIEWED_COHORT_SIGNAL_BINDING = (
    r"(?:\s+with\s+(?:retention|recapture)(?:\s+or\s+(?:retention|recapture))?"
    r"\s+(?:risk|signals?))"
)
# "ranked by opportunity score" / "by opportunity score": the participle is
# how the planner sometimes says it, not what makes the ranking governed.
_REVIEWED_RANKING_TAIL = rf"(?:\s+(?:ranked\s+)?by\s+{_REVIEWED_ANALYTIC_SIGNAL_LIST})"
_REVIEWED_RANKING_ORDER_NOTE = (
    r"(?:\s*,?\s*using\s+the\s+default\s+(?:ranking|sort)\s+order)"
)
# "..., and what are their governed signal columns: <list>" / "..., and for
# each borrower what are the governed drivers visible in the data — <list>".
# A colon splits the clause, so the list may be absent here and is scanned as
# its own clause; an em-dash does not split, so the list is consumed inline.
# Every noun is closed, and the list itself is the governed signal vocabulary.
_REVIEWED_SIGNAL_COLUMNS_TAIL = (
    r"(?:\s*,?\s*and\s+(?:for\s+each\s+(?:borrower|lead|candidate|customer|one)\s+)?"
    r"what\s+(?:are|is)\s+(?:their|its|the)\s+"
    r"(?:(?:governed\s+)?(?:signal\s+columns?|drivers?(?:\s+visible\s+in\s+the\s+data)?|"
    r"signals?)"
    rf"(?:\s*[\u2014\u2013-]\s*{_REVIEWED_ANALYTIC_SIGNAL_LIST})?"
    rf"|{_REVIEWED_ANALYTIC_SIGNAL_LIST}))"
)
# Closed predicates a planner attaches to a counted population ("borrowers who
# are both in-the-money and have at least 35% equity"). The equity floor is
# the product's own HELOC-eligibility threshold, and its number slot is digits
# only, like every other count in these grammars.
_REVIEWED_POPULATION_PREDICATE = (
    rf"(?:{_REVIEWED_PRODUCT_INTENT}|listed\s+for\s+sale|current\s+customers|"
    r"have\s+(?:a\s+)?competitor\s+liens?|"
    r"have\s+at\s+least\s+[0-9]{1,3}\s*(?:%|percent)\s+(?:home\s+)?equity)"
)
_REVIEWED_WHOLE_POPULATION = (
    r"(?:the\s+)?(?:full|entire|whole|overall|broader)\s+"
    r"(?:(?:eligible|marketing[- ]?eligible|outreach[- ]?ready)\s+)?(?:borrower\s+)?"
    r"(?:population|pool|universe|portfolio|group)"
)
# A closed product-intent with-clause on a reviewed population ("customers
# with an in-the-money refi", "borrowers with retention signals", "investor
# borrowers with multiple properties" -- the Owner Link domain rule). Shared
# by the cohort-listing shape and the cohort-versus-book comparison below.
# The alternatives are a closed set; a free-text criterion ("with zyrplax",
# "with eczema") does not match and falls through to the strict criterion
# machine.
#
# ``signals?`` is in the second slot because a segment SIGNAL is the product's
# own noun for its closed segment vocabulary
# (``is_closed_reviewed_segment_signal_criterion``), yet "customers with
# retention signals" refused as an unreviewed criterion while "customers with
# retention" answered -- one governed noun apart (captured 2026-09-08).
_REVIEWED_PRODUCT_INTENT_WITH_CLAUSE = (
    r"with\s+(?:"
    rf"(?:(?:a|an|the)\s+)?{_REVIEWED_PRODUCT_INTENT}"
    rf"(?:[\s-]+(?:{_REVIEWED_PRODUCT_INTENT}|mortgage|loan|refi|refinance|position|"
    r"offer|opportunity|signals?))?"
    r"|multiple\s+properties"
    r")"
)
# The Module 0 funnel stages a movement question can break down by: the lead
# population the product builds, the approvals it gates, and the outreach it
# hands off. Closed, like every list in this module.
_REVIEWED_FUNNEL_STAGE = (
    r"(?:lead\s+population|leads?|lead\s+queue|approvals?|outreach|campaigns?|"
    r"contacts?|responses?|conversions?|handoffs?|(?:the\s+)?queue|segments?|"
    r"states?|offers?)"
)
_REVIEWED_FUNNEL_STAGE_LIST = (
    rf"{_REVIEWED_FUNNEL_STAGE}(?:\s*,?\s*(?:and\s+)?{_REVIEWED_FUNNEL_STAGE})*"
)
# A trailing time window ("over the last 30 days", "this month", "recently").
# The count is digits only, the unit a closed list.
_REVIEWED_TRAILING_WINDOW = (
    r"(?:\s+(?:over|in|during|across)\s+the\s+(?:last|past|previous|trailing)\s+"
    r"[0-9]{1,3}\s+(?:days?|weeks?|months?|quarters?)|"
    r"\s+recently|\s+over\s+time|\s+this\s+(?:week|month|quarter))"
)

_REVIEWED_READ_ONLY_ANALYTIC_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        # Compound sales ask: a ranked cohort followed by any of the closed
        # follow-on clauses — per-item rationale, the offer call, and the
        # risk-of-loss question — in any order ("Rank the top opportunities,
        # explain why each one is especially strong compared to the rest of
        # the book, what the best curated offer is for each and why, and what
        # would make me lose them"). Live persona probe 2026-08-10
        # (sales-manager): refused as an unreviewed criterion because no
        # ^…$-anchored shape covered a four-part sentence.
        #
        # This stays anchored and fully closed ON PURPOSE. Splitting the
        # sentence and screening the parts was measured and REJECTED: comma
        # fragments lose the population context the detectors need, so
        # "Rank borrowers with high equity, eczema, and good scores" is caught
        # whole but every fragment passes. Requiring the WHOLE string to match
        # reviewed vocabulary means an unknown criterion anywhere breaks the
        # match and the clause fails closed.
        r"^(?:rank|show|list|give\s+me|surface|prioriti[sz]e)\s+(?:me\s+)?(?:the\s+)?"
        r"(?:top\s+(?:[0-9]{1,3}\s+)?)?"
        rf"(?:{_REVIEWED_PRODUCT_INTENT}(?:[\s-]+(?:{_REVIEWED_PRODUCT_INTENT}|mortgage|loan))?\s+)?"
        r"(?:candidates?|borrowers?|leads?|opportunities)"
        rf"{_REVIEWED_ANALYTIC_LOCATION}"
        r"(?:"
        r"\s*,?\s*(?:and\s+)?"
        r"(?:"
        # Per-item rationale, optionally against the wider book.
        r"(?:explain|tell\s+me|describe|show)\s+(?:me\s+)?why\s+"
        r"(?:each|every)(?:\s+(?:one|borrower|candidate|lead|prospect))?\s+"
        r"(?:is|ranks?|scores?|qualifies|stands?\s+out)"
        r"(?:\s+(?:especially|particularly|very)?\s*"
        r"(?:strong|good|great|promising|compelling))?"
        rf"(?:\s+compared\s+to\s+{_REVIEWED_WHOLE_POPULATION}|"
        r"\s+compared\s+to\s+(?:the\s+)?rest\s+of\s+the\s+(?:book|list|portfolio))?"
        r"|"
        # The offer call.
        r"what\s+the\s+(?:best|right|recommended)\s+(?:curated\s+)?offers?\s+"
        r"(?:is|are)\s+for\s+(?:each|every)(?:\s+one)?(?:\s*,?\s*and\s+why)?"
        r"|"
        # Risk of losing the opportunity — a governed retention question.
        r"what\s+(?:would|could|might)\s+(?:make\s+)?(?:me\s+|us\s+)?"
        r"(?:lose|losing)\s+(?:them|these|the\s+(?:deal|opportunity))"
        r"|"
        r"what\s+(?:is|are)\s+the\s+(?:retention\s+)?risks?"
        r"(?:\s+of\s+losing\s+(?:them|these))?"
        r")"
        r"){1,4}\s*\??$",
        re.IGNORECASE,
    ),
    re.compile(
        # Ranked shortlist plus its governed signal columns ("who are the top
        # 20 eligible borrowers ranked by opportunity score, and what are
        # their rate spread, equity percentage, key triggers, and recommended
        # offer?"). Captured live 2026-08-10 from the governed space's OWN
        # deep-analysis plan: the criterion machine read it as an unreviewed
        # criterion, three of seven planned sub-questions were dropped, the
        # plan fell under the deep floor, and the sweep aborted silently — the
        # user saw a single-screen answer instead of the deep decomposition.
        r"^(?:who|what)\s+(?:are|is)\s+"
        rf"{_REVIEWED_TOP_COHORT}"
        # Second live capture, 2026-09-08: the cohort may be scoped to a
        # governed segment (before or after the ranking), bound to the
        # retention signals, ranked with a bare ``by``, annotated with the
        # default ranking order, and followed by the governed-signal-columns
        # tail (colon or em-dash list). Each slot is a closed alternation; an
        # unknown word in any of them breaks the match and the clause falls
        # through to the strict criterion machine.
        rf"{_REVIEWED_COHORT_SCOPE}?"
        rf"{_REVIEWED_COHORT_SIGNAL_BINDING}?"
        rf"{_REVIEWED_RANKING_TAIL}?"
        rf"{_REVIEWED_COHORT_SCOPE}?"
        rf"{_REVIEWED_RANKING_ORDER_NOTE}?"
        rf"{_REVIEWED_SIGNAL_COLUMNS_TAIL}?"
        r"\s*\??$",
        re.IGNORECASE,
    ),
    re.compile(
        # Percentile placement of a ranked cohort within the population
        # ("what is the percentile rank of the top 20 borrowers for
        # opportunity score, rate spread, and equity percentage within the
        # entire eligible borrower pool?"). Same live capture.
        r"^what\s+(?:is|are)\s+the\s+"
        r"(?:percentile\s+ranks?|percentiles?|rank(?:ings?)?)\s+of\s+"
        rf"{_REVIEWED_TOP_COHORT}\s+"
        r"(?:for|on|by|across)\s+"
        + _REVIEWED_ANALYTIC_SIGNAL_LIST
        + rf"(?:\s+(?:within|in|against|compared\s+to)\s+{_REVIEWED_WHOLE_POPULATION})?"
        r"\s*\??$",
        re.IGNORECASE,
    ),
    re.compile(
        # Geography ranked by the count of a closed-predicate population, with
        # that cohort's governed averages ("which states rank highest by count
        # of borrowers who are both in-the-money and have at least 35% equity,
        # and what are their average opportunity score, ..."). Live planner
        # capture 2026-09-08: the bound-population capture read "rank highest
        # by count of" as the criterion forming a "borrowers" audience.
        rf"^(?:which|what)\s+{_REVIEWED_ANALYTIC_DIMENSION}\s+"
        r"ranks?\s+(?:highest|lowest)\s+by\s+(?:count|number)\s+of\s+"
        r"(?:borrowers?|leads?|candidates?|customers?|homeowners?)"
        rf"(?:\s+who\s+(?:are\s+)?(?:both\s+)?{_REVIEWED_POPULATION_PREDICATE}"
        rf"(?:\s+and\s+{_REVIEWED_POPULATION_PREDICATE})?)?"
        rf"(?:\s*,?\s*and\s+what\s+(?:are|is)\s+(?:their|its)\s+"
        rf"{_REVIEWED_ANALYTIC_SIGNAL_LIST})?"
        r"\s*\??$",
        re.IGNORECASE,
    ),
    re.compile(
        # Top band against the whole population ("in the highest-volume
        # states, how do top-tier opportunities (opportunity_score at or above the high-opportunity floor)
        # compare with the full outreach-ready population on average
        # opportunity score, ..."). Same live capture: the bound-population
        # capture read the adjective "top-tier" as the formation verb ``tier``
        # binding everything up to "population". The parenthetical is the
        # governed score column with a digits-only bound; ``ln`` is the
        # capital-I fold image of a sentence-initial ``In`` (see
        # ``_REVIEWED_PRODUCT_INTENT``).
        r"^(?:(?:in|ln)\s+the\s+(?:highest|top|largest)[- ]?volume\s+"
        rf"{_REVIEWED_ANALYTIC_DIMENSION}\s*,\s*)?"
        rf"how\s+do(?:es)?\s+{_REVIEWED_TOP_COHORT}"
        # The same cohort scope and retention binding the ranked-shortlist
        # shape takes: the planner reuses one cohort description across its
        # sub-questions, so the phrase that refused there refuses here too.
        rf"{_REVIEWED_COHORT_SCOPE}?"
        rf"{_REVIEWED_COHORT_SIGNAL_BINDING}?"
        r"(?:\s*\(\s*(?:opportunity|lead)[_ ]scores?\s*(?:>=|<=|>|<|=)\s*[0-9]{1,3}\s*\))?"
        r"\s+compare\s+(?:with|to|against)\s+"
        rf"{_REVIEWED_WHOLE_POPULATION}"
        rf"(?:\s+(?:on|in\s+terms\s+of|across|by)\s+{_REVIEWED_ANALYTIC_SIGNAL_LIST})?"
        r"\s*\??$",
        re.IGNORECASE,
    ),
    re.compile(
        # Offer strategy by segment ("which offer should we lead with for each
        # segment, and why?"). The audience grammar read the affirmative
        # "lead with ... for each segment" as an unreviewed audience decision;
        # it is the product's core read-only offer-mix question. Live persona
        # audit 2026-08-07 (marketing-leader).
        #
        # The scope tail is OPTIONAL. "what offer should we lead with?" is the
        # same question over the whole book, and without the tail it fell to
        # the audience-decision net, which reads the VERB in "lead with" as the
        # population noun ``lead`` bound to a ``with`` criterion connector,
        # with ``offer`` supplying the governed outcome (captured 2026-09-08;
        # "which offer should we recommend first and why?" answered). Every
        # slot is still a closed alternation, so no criterion can ride in.
        r"^(?:which|what)\s+(?:next[- ]?best\s+)?offers?\s+"
        r"(?:should|do|would)\s+(?:we|i|the\s+team)\s+"
        r"(?:lead\s+with|use|present|recommend|make|pitch|prioriti[sz]e)"
        r"(?:\s+(?:for|to|with)\s+(?:each|every|the|our)?\s*"
        rf"(?:{_REVIEWED_ANALYTIC_DIMENSION}|{_REVIEWED_ANALYTIC_POPULATION}))?"
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
        # The intent token is OPTIONAL: "rank the top opportunities" names no
        # criterion at all, so there is no unreviewed criterion to smuggle —
        # it ranks by the governed score. An unknown word in this slot still
        # fails to match (it is neither an intent token nor a cohort noun),
        # so "rank the top zyrplax borrowers" keeps falling through to the
        # strict criterion machine. Live persona probe 2026-08-10
        # (sales-manager): the whole question was refused because of it.
        rf"(?:{_REVIEWED_PRODUCT_INTENT}(?:[\s-]+(?:{_REVIEWED_PRODUCT_INTENT}|mortgage|loan))?\s+)?"
        r"(?:candidates?|borrowers?|leads?|opportunities)"
        # The ranking signal ("the top 15 borrowers BY OPPORTUNITY SCORE and
        # explain why each one is a strong candidate"). Without it the whole
        # sentence fell past this shape to the audience-decision net, which
        # reads "borrowers by <signal>" as a population bound to a criterion
        # connector behind an open lead-in (captured 2026-09-08; the
        # signal-free twin and the bare ranked shape both answered). The
        # signal list is the same closed governed-column vocabulary the
        # ranked-shortlist shape reads, so an unknown signal ("by zyrplax")
        # still breaks the shape and still fails closed.
        rf"(?:\s+(?:ranked\s+by|ordered\s+by|sorted\s+by|by)\s+{_REVIEWED_ANALYTIC_SIGNAL_LIST})?"
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
        rf"{_REVIEWED_PRODUCT_INTENT_WITH_CLAUSE}"
        rf"{_REVIEWED_ANALYTIC_LOCATION}$",
        re.IGNORECASE,
    ),
    re.compile(
        # Cohort-versus-book comparison on governed measures ("how do our
        # current customers with retention signals compare with the rest of
        # our customers on rate spread and equity?"). The audience-decision
        # net read "customers with retention signals compare ..." as a
        # population bound to an open ``with`` criterion, because that net
        # captures the criterion to the END of the clause and "retention
        # signals compare with the rest ..." is not a reviewed attribute
        # (captured 2026-09-08; "customers in the retention segment" answered,
        # one connector apart). Every slot is closed: the cohort, its optional
        # intent with-clause, the comparison baseline and the measure list.
        r"^how\s+do(?:es)?\s+(?:(?:our|the|these)\s+)?(?:(?:current|existing|former)\s+)?"
        rf"{_REVIEWED_ANALYTIC_POPULATION}"
        rf"(?:\s+{_REVIEWED_PRODUCT_INTENT_WITH_CLAUSE})?"
        r"\s+compare\s+(?:with|to|against|versus|vs)\s+"
        r"(?:the\s+)?rest\s+of\s+(?:our|the)\s+"
        r"(?:book|portfolio|population|pool|base|customers?|borrowers?|leads?|homeowners?)"
        rf"(?:\s+on\s+{_REVIEWED_ANALYTIC_SIGNAL_LIST})?$",
        re.IGNORECASE,
    ),
    re.compile(
        # Lender strategy given a cohort's closed product-intent outcome ("what
        # is the best move for a lender given these borrowers will not
        # rate-and-term refi"). ``move`` is a NOUN here, but the
        # bound-population capture reads any formation verb followed within 80
        # characters by a population noun, so "move for a lender given these
        # borrowers" captured "for a lender given these" as the criterion and
        # refused while both halves answered alone (captured 2026-09-08). The
        # strategy noun, the subordinator, the population and the negated
        # intent are all closed alternations, so nothing rides the shape:
        # "... will not take zyrplax" still falls through and still fails
        # closed.
        r"^what(?:'s|\s+is|\s+are|\s+would\s+be)\s+(?:the|our|a)\s+"
        r"(?:best|right|next|smart(?:est)?|optimal|recommended)\s+"
        r"(?:move|play|step|action|approach|strategy|offer|option|response)\s+"
        r"for\s+(?:a|the|our)\s+lender\s+"
        r"(?:given|since|if|when|because|now\s+that)\s+(?:that\s+)?"
        r"(?:these|those|the|our|such)\s+"
        r"(?:borrowers?|customers?|homeowners?|leads?|candidates?|prospects?)\s+"
        r"(?:will\s+not|won't|cannot|can't|do\s+not|don't|are\s+not\s+going\s+to|"
        r"are\s+unlikely\s+to)\s+"
        rf"{_REVIEWED_PRODUCT_INTENT}"
        rf"(?:[\s-]+(?:{_REVIEWED_PRODUCT_INTENT}|refi|refinance|mortgage|loan))?$",
        re.IGNORECASE,
    ),
    re.compile(
        # Funnel movement over a trailing window, by stage and by driver ("do
        # a comprehensive review of how the funnel moved over the last 30 days
        # across lead population, approvals, and outreach", "... and which
        # segments and states drove the change"). ``moved`` is INTRANSITIVE
        # here -- the funnel is its subject -- but the bound-population
        # capture reads any formation verb followed within 80 characters by a
        # population noun, so "moved over the last 30 days across lead
        # population" captured "over the last 30 days across" as the criterion
        # of ``lead`` and refused, while the ``changed`` twin answered
        # (captured 2026-09-08). The preamble, the subject, the window, the
        # stage list and the driver clause are all closed alternations.
        r"^(?:(?:do|run|perform|conduct|complete)\s+(?:a|an)\s+"
        r"(?:(?:deep|full|comprehensive|complete|thorough)\s+)?"
        r"(?:analysis|review|assessment|study|deep[- ]?dive)\s+of\s+|"
        r"(?:analy[sz]e|review|examine|assess|explain|summari[sz]e|describe)\s+)?"
        # Both auxiliary orders: "how the funnel has moved" and the inverted
        # "how has the funnel moved" the population-movement shape above
        # already admits.
        r"how\s+(?:(?:has|have|did|does|do)\s+)?(?:(?:the|our)\s+)?"
        r"(?:(?:lead|conversion|sales|marketing)\s+)?"
        r"(?:funnel|pipeline)\s+(?:(?:has|have)\s+)?"
        r"(?:moved|changed|shifted|trended|performed|progressed)"
        rf"{_REVIEWED_TRAILING_WINDOW}?"
        rf"(?:\s+across\s+{_REVIEWED_FUNNEL_STAGE_LIST})?"
        r"(?:\s*,?\s*and\s+which\s+"
        rf"{_REVIEWED_ANALYTIC_DIMENSION}(?:\s*,?\s*(?:and\s+)?{_REVIEWED_ANALYTIC_DIMENSION})*"
        r"\s+(?:drove|drives|is\s+driving|explains?|explained|accounts?\s+for|"
        r"accounted\s+for|caused)\s+"
        r"(?:the\s+|that\s+|this\s+)?(?:change|movement|shift|difference|delta|result)s?)?$",
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
        # The COUNT is optional, the rank word is not. "Show the top 25
        # borrowers by state" matched and "Show the top borrowers by state"
        # did not -- the same numeric asymmetry ``_POPULATION_QUANTIFIER``
        # fixes one module over, pointing the other way. A count quantifies a
        # ranked cohort; it does not decide whether the shape is governed, and
        # every other slot here (population, dimension, location) is closed, so
        # dropping it cannot admit an unknown criterion.
        r"(?:(?:top|bottom)\s+(?:(?:[0-9]{1,3}|ten|twenty(?:[- ]five)?)\s+)?)?"
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
    re.compile(
        # Superlative ranked-shortlist directive ("rank the strongest
        # candidates for outreach", "pick the very best refinance
        # candidates", "give me a curated list of the highest-potential
        # borrowers"). Every slot is a closed alternation — superlatives,
        # product intents, population nouns, and purposes — so an unknown
        # criterion cannot ride the shape. Live capture 2026-08-08: the
        # audience-formation grammar failed this closed as an unreviewed
        # audience decision, refusing a fundamental deep-analysis ask.
        r"^(?:and\s+)?(?:rank|pick|surface|identify|find|curate|list|show|give\s+me|"
        r"determine)\s+(?:me\s+)?"
        r"(?:a\s+(?:curated\s+)?list\s+of\s+)?(?:the\s+)?"
        r"(?:very\s+|absolute\s+|single\s+)?"
        r"(?:top|best|strongest|highest[- ]potential|most\s+promising)\s+"
        r"(?:[0-9]{1,3}\s+)?(?:potential\s+)?"
        rf"(?:{_REVIEWED_PRODUCT_INTENT}\s+)?"
        r"(?:candidates?|borrowers?|leads?|opportunities|prospects?)"
        r"(?:\s+(?:for\s+(?:outreach|contact|review|follow[- ]up)|to\s+contact))?$",
        re.IGNORECASE,
    ),
    re.compile(
        # Best-offer-per-borrower directive ("recommend the best offer for
        # each with reasoning", "what the absolute best curated offer for
        # each would be and why"). The population tokens and the
        # answer-format tail are closed; free-text criteria fall through to
        # the strict criterion machine. Same 2026-08-08 live capture.
        r"^(?:and\s+)?(?:recommend|determine|identify|pick|choose|select|suggest|"
        r"tell\s+me|what)\s+"
        r"(?:what\s+)?(?:the\s+)?(?:absolute\s+)?(?:best|ideal|right|optimal)\s+"
        r"(?:curated\s+|fit\s+)?offers?\s+(?:for|to)\s+(?:each|every)"
        r"(?:\s+(?:one|borrower|candidate|lead|prospect|customer))?"
        r"(?:\s+would\s+be)?"
        r"(?:\s+with\s+(?:full\s+|clear\s+|detailed\s+)?"
        r"(?:reasoning|rationale|justification|explanations?))?"
        r"(?:\s*,?\s*and\s+why)?$",
        re.IGNORECASE,
    ),
)


_REVIEWED_ANALYSIS_PREAMBLE_RE = re.compile(
    # Closed analysis preamble ("Analyze the full dataset of eligible
    # borrowers and <directive>"). Compound clauses defeated the
    # ``^…$``-anchored reviewed shapes below, so a reviewed directive
    # prefixed by an analysis framing failed closed (live capture,
    # 2026-08-08). Every slot is a closed alternation — an unknown
    # population or criterion does not match, is not stripped, and the
    # clause still fails closed.
    r"^(?:"
    # Scope framing with no verb ("Across the entire portfolio, …").
    r"across\s+(?:the\s+)?(?:entire|whole|full|current)?\s*"
    r"(?:portfolio|book|footprint|coverage|datasets?|pipelines?)\s*,\s*"
    r"|"
    r"(?:(?:comprehensively|deeply|thoroughly|fully)\s+)?"
    # Direct verb ("Comprehensively analyze …") or light-verb form
    # ("Do a deep analysis of …").
    r"(?:analyze|analyse|review|examine|study|assess|explore|deep[- ]dive(?:\s+into)?|"
    r"(?:do|run|perform|conduct|complete)\s+(?:a|an)\s+"
    r"(?:(?:deep|full|comprehensive|complete|thorough)\s+)?"
    r"(?:analysis|review|assessment|study|deep[- ]dive)\s+of)\s+"
    r"(?:(?:the|our|this|my|every|all)\s+)?(?:(?:full|entire|complete|whole)\s+)?"
    r"(?:(?:highest[- ]priority|top[- ]priority|priority|top)\s+)?"
    r"(?:(?:eligible|marketing[- ]eligible|reviewed|contact[- ]eligible)\s+)?"
    r"(?:datasets?|data\s*sets?|data|portfolios?|books?|pipelines?|populations?|"
    r"outreach\s+lists?|call\s+lists?|work\s+lists?|lists?|"
    r"borrowers?|leads?|customers?|prospects?|homeowners?)?"
    r"(?:\s+of\s+(?:(?:all|our|the)\s+)?(?:(?:eligible|marketing[- ]eligible|reviewed|"
    r"contact[- ]eligible)\s+)?(?:borrowers?|leads?|customers?|prospects?|homeowners?))?"
    r"(?:\s*,?\s*and\s+|\s*[.;]\s+)"
    r")",
    re.IGNORECASE,
)
_REVIEWED_WHY_ASSESSMENT_PREAMBLE_RE = re.compile(
    # Closed why-assessment preamble ("Evaluate why each borrower is an
    # especially good candidate, and <directive>"). Same compound-clause
    # problem and the same posture as the analysis preamble above: the
    # assessment vocabulary is closed, so an unknown criterion inside the
    # segment does not match, is not stripped, and the clause fails closed.
    r"^(?:evaluate|explain|justify|assess|describe|tell\s+me|"
    r"walk\s+(?:me\s+)?through)\s+(?:me\s+)?"
    r"(?:why\s+(?:each|every)(?:\s+(?:one|borrower|candidate|lead|prospect|customer))?\s+"
    r"(?:is|would\s+be|makes)\s+(?:an?\s+)?"
    r"(?:especially\s+|particularly\s+|very\s+)?"
    r"(?:strong|good|great|excellent|prime|ideal|top|promising)\s+"
    r"(?:candidate|prospect|fit|match|choice|opportunity)|"
    r"each\s+selection|"
    r"the\s+rationale\s+for\s+each(?:\s+(?:one|borrower|candidate|selection))?)"
    r"\s*,?\s*and\s+",
    re.IGNORECASE,
)


# The bound-population capture in ``marketing_selection_criteria`` reads
# everything between a formation verb and the NEXT population noun as that
# population's criterion, so a criterion-free span that crosses a clause
# boundary is captured whole and fails closed. Two of the governed space's own
# planned deep-analysis lines bound that way (captured 2026-09-08 on the
# paychex space; ``test_planned_deep_analysis_cross_clause_bindings`` pins
# both):
#
#   "... when ranked BY AVERAGE OPPORTUNITY SCORE, AND HOW LARGE IS EACH segment"
#   "... prioritized FIRST WHEN BALANCING borrower volume, average ..."
#
# Each is admitted through one closed shape and nothing else. The ranking
# attribute is judged by the reviewers every other capture uses, so "ranked by
# rosacea, and how large is each segment" keeps refusing. The weighing list is
# admitted only when EVERY measure in it is reviewed vocabulary, on both sides
# of the population noun, so "balancing borrower volume, zyrplax, and equity"
# keeps refusing too -- that refusal was an accident of the capture (a weighing
# list with no population noun in it is read by nothing, before or after this
# change), and the closed list keeps the accident rather than trading it away.
_REVIEWED_RANKING_THEN_GROUP_SIZE_RE = re.compile(
    r"^(?:by|according\s+to|based\s+on|on)\s+(?P<criterion>[^,.!?;:]{1,80}?)"
    r"\s*,?\s+and\s+how\s+(?:(?:large|big|small)\s+(?:is|are)\s+(?:each|every)|many)$",
    re.IGNORECASE,
)
_REVIEWED_WEIGHING_MEASURE = (
    rf"(?:borrower\s+(?:volume|counts?)|(?:{REVIEWED_MORTGAGE_ATTRIBUTE_FRAGMENT})"
    r"(?:\s+(?:concentration|mix|share|distribution))?)"
)
_REVIEWED_WEIGHING_ADVERBIAL_RE = re.compile(
    r"^(?:(?:first|next|last)\s+)?(?:when|while)\s+"
    r"(?:balancing|weighing|considering|trading\s+off)"
    rf"(?:\s+{_REVIEWED_WEIGHING_MEASURE}(?:\s*,\s*{_REVIEWED_WEIGHING_MEASURE})*"
    r"\s*,?\s+(?:and|or))?$",
    re.IGNORECASE,
)
# The rest of the clause behind the captured population noun: the other half
# of the "borrower volume" measure, then the remaining reviewed measures.
_REVIEWED_WEIGHING_MEASURE_TAIL_RE = re.compile(
    rf"^\s+(?:volume|counts?)(?:\s*,\s*{_REVIEWED_WEIGHING_MEASURE})*"
    rf"(?:\s*,?\s+(?:and|or)\s+{_REVIEWED_WEIGHING_MEASURE})?\s*$",
    re.IGNORECASE,
)
# A grouping dimension coordinated with the population noun it precedes --
# "which STATES AND segments should be prioritized first" -- names no criterion.
# The slot is the strategy grammar's grouping list plus the analytics ZIP
# spellings. Measures are deliberately left out: the analytics dimension slot
# also lists equity, LTV and rate spread, and a measure in this run would strip
# the head off a reviewed attribute list ("equity and rate spread").
_GROUPING_DIMENSION = (
    r"(?:states?|count(?:y|ies)|zip(?:\s+codes?)?|postal\s+codes?|markets?|metros?|"
    r"segments?|offer\s+lanes?)"
)
_COORDINATED_GROUPING_DIMENSION = (
    rf"(?:{_GROUPING_DIMENSION}\s*,\s*)*{_GROUPING_DIMENSION}\s*,?\s+(?:and|or)"
)
