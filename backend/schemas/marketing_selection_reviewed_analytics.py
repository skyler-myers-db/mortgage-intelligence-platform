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
# Signal columns a planned sub-analysis may name alongside a ranked cohort.
# Closed: every entry is a governed gold column or Module 0 domain signal
# (CLAUDE.md domain rules), never free text.
_REVIEWED_ANALYTIC_SIGNAL = (
    r"(?:opportunity\s+scores?|lead\s+scores?|rate\s+spreads?|equity(?:\s+percentage)?|"
    r"ltv|loan[- ]to[- ]value|key\s+triggers?|triggers?|recommended\s+offers?|"
    r"next[- ]best\s+offers?|segment\s+memberships?|segments?|listing\s+status|"
    r"competitor\s+liens?|listed\s+for\s+sale|investor\s+status|"
    r"retention\s+risk|heloc\s+propensity)"
)
_REVIEWED_ANALYTIC_SIGNAL_LIST = (
    rf"{_REVIEWED_ANALYTIC_SIGNAL}(?:\s*,?\s*(?:and\s+)?{_REVIEWED_ANALYTIC_SIGNAL})*"
)
# "the top 20 (eligible) borrowers" — the cohort noun a planned deep analysis
# names in nearly every sub-question.
_REVIEWED_TOP_COHORT = (
    r"(?:the\s+)?top\s+(?:[0-9]{1,3}\s+)?"
    r"(?:eligible\s+|marketing[- ]eligible\s+|highest[- ]scoring\s+)?"
    r"(?:borrowers?|leads?|candidates?|opportunities)"
)
_REVIEWED_WHOLE_POPULATION = (
    r"(?:the\s+)?(?:full|entire|whole|overall|broader)\s+"
    r"(?:eligible\s+)?(?:borrower\s+)?(?:population|pool|universe|portfolio|group)"
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
        r"(?:\s+ranked\s+by\s+" + _REVIEWED_ANALYTIC_SIGNAL_LIST + r")?"
        r"(?:\s*,?\s*and\s+what\s+(?:are|is)\s+(?:their|its)\s+"
        + _REVIEWED_ANALYTIC_SIGNAL_LIST
        + r")?\s*\??$",
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
