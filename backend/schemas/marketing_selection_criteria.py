"""Closed policy for criteria coordinated with a selected marketing audience."""

from __future__ import annotations

import re

from backend.schemas.growth_agent_segment_intent import (
    is_closed_reviewed_segment_signal_criterion,
)
from backend.schemas.marketing_audience_admission import audience_admission_criterion

REVIEWED_MORTGAGE_ATTRIBUTE_FRAGMENT = (
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
    r"(?:fixed|adjustable)[- ]?rate\s+(?:mortgages?|loans?))"
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
_CRITERION_TAIL = r"(?P<criterion>[^.!?;:]{1,120})"
_ELIGIBILITY_SUBJECT = (
    r"(?:(?:their|its)\s+|the\s+(?:group|cohort|audience|segment|population|selection)'?s\s+)?"
    r"(?:eligibility|qualification)"
)
_COREFERENCE_POPULATION = (
    r"(?:people|persons?|individuals?|residents?|households?|borrowers?|homeowners?|"
    r"applicants?|recipients?|customers?|prospects?|clients?|owners?|members?|patients?|"
    r"candidates?|leads?)"
)
_COREFERENCE_SUBJECT = (
    rf"(?:(?:these|those|such|the\s+selected)\s+{_COREFERENCE_POPULATION}|"
    rf"(?:each|every|all)\s+(?:selected\s+)?{_COREFERENCE_POPULATION}|"
    r"members?\s+of\s+(?:this|that|the)\s+(?:group|cohort|audience|segment|population)|"
    r"(?:everyone|everybody|each\s+person|every\s+person)\s+in\s+"
    r"(?:(?:this|that|the)\s+)?(?:(?:resulting|selected|ranked|ordered)\s+)?"
    r"(?:list|group|cohort|audience|segment|population)|"
    r"they(?:\s+all)?|these|those|"
    r"each(?:\s+one)?(?:\s+of\s+(?:them|these|those))?|"
    r"every\s+one\s+of\s+(?:them|these|those)|"
    r"all(?:\s+of\s+(?:them|these|those))?|"
    r"(?:this|that|the)\s+(?:group|cohort|audience|segment|population|selection)|"
    rf"(?:the\s+(?:selected\s+)?|){_COREFERENCE_POPULATION})"
)
_CONTEXTUAL_SUBJECT_CLAUSE_RE = re.compile(
    rf"^(?:{_COREFERENCE_SUBJECT}|{_ELIGIBILITY_SUBJECT})\b(?P<predicate>.+)$",
    re.IGNORECASE,
)

_ONLY_INCLUDE_CRITERION_RE = re.compile(
    r"\bonly\s+include\s+(?:those|people|persons?|individuals?|residents?|households?|"
    r"borrowers?|homeowners?|applicants?)\s+(?:diagnosed\s+with|with|having|"
    r"whose\s+(?:diagnosis|medical\s+condition|health\s+condition)\s+(?:is|was))\s+"
    rf"{_CRITERION_TAIL}",
    re.IGNORECASE,
)
_COREFERENCE_REQUIREMENT_RE = re.compile(
    rf"\b{_COREFERENCE_SUBJECT}\s+(?:"
    r"must\s+(?:also\s+)?(?:have|meet|show|carry|possess|satisfy)|"
    r"(?:also\s+)?(?:needs?|requires?))\s+"
    rf"{_CRITERION_TAIL}",
    re.IGNORECASE,
)
_COREFERENCE_DECLARATIVE_CRITERION_RE = re.compile(
    rf"\b{_COREFERENCE_SUBJECT}\s+(?:also\s+)?(?:"
    r"have|has|show|shows|carry|carries|possess|possesses|exhibit|exhibits|"
    r"present\s+with|presents\s+with)\s+"
    rf"{_CRITERION_TAIL}",
    re.IGNORECASE,
)
_OBJECT_REQUIREMENT_RE = re.compile(
    r"(?:^|\b(?:and|but|while)\s+|,\s*)"
    r"(?P<criterion>[a-z][a-z0-9' -]{0,120}?)\s+"
    r"(?:(?:is|are|was|were)\s+)?(?<!not\s)(?<!never\s)(?<!no longer\s)"
    r"(?<!not currently\s)(?<!not presently\s)(?:mandatory|required(?:\s+too)?|"
    r"an?\s+additional\s+(?:criterion|requirement)|necessary)\b",
    re.IGNORECASE,
)
_PROVIDED_CRITERION_RE = re.compile(
    rf"\bprovided\s+(?:that\s+)?(?:{_COREFERENCE_SUBJECT}\s+)?"
    r"(?:have|has|meet|meets|show|shows|carry|carries|possess|possesses|satisfy|satisfies)\s+"
    rf"{_CRITERION_TAIL}",
    re.IGNORECASE,
)
_DOCUMENTED_FOR_WHOM_RE = re.compile(
    r"\bfor\s+whom\s+(?P<criterion>[a-z][a-z0-9' -]{0,120}?)\s+"
    r"(?:(?:is|was)\s+)?(?:documented|verified|confirmed|recorded)\b",
    re.IGNORECASE,
)
_COORDINATE_REQUIRE_RE = re.compile(
    r"(?:^|\b(?:and|then|but)\s+|,\s*)(?:also\s+)?require\s+" rf"{_CRITERION_TAIL}",
    re.IGNORECASE,
)
_ELIGIBILITY_DEPENDS_RE = re.compile(
    rf"\b{_ELIGIBILITY_SUBJECT}\s+(?:also\s+)?(?:"
    r"(?:depends?|hinges?|rests?|relies?)\s+(?:on|upon)|turns?\s+on|"
    r"is\s+(?:based|conditioned|contingent|dependent)\s+(?:on|upon)|"
    r"is\s+(?:tied|subject)\s+to)\s+"
    rf"{_CRITERION_TAIL}",
    re.IGNORECASE,
)
_DETERMINES_ELIGIBILITY_RE = re.compile(
    r"(?:^|\b(?:and|but|while)\s+|,\s*)"
    r"(?P<criterion>[a-z][a-z0-9' -]{0,120}?)\s+"
    r"(?<!never\s)(?<!no longer\s)determines\s+"
    r"(?:the\s+)?(?:final\s+)?eligibility\b",
    re.IGNORECASE,
)
_ONLY_SELECT_OR_FILTER_BY_RE = re.compile(
    r"\bonly\s+(?:select|filter)(?:\s+(?:them|those|these|people|persons?|individuals?|"
    r"households?|borrowers?|homeowners?|applicants?|recipients?|"
    r"the\s+(?:group|cohort|audience|segment|selection)))?\s+by\s+"
    rf"{_CRITERION_TAIL}",
    re.IGNORECASE,
)
_FILTER_CRITERION_RE = re.compile(
    r"\bfilter\s+(?:them|those|these|this\s+(?:group|cohort|audience|segment)|"
    r"the\s+(?:group|cohort|audience|segment|selection|recipients?))\s+by\s+"
    rf"{_CRITERION_TAIL}",
    re.IGNORECASE,
)
_CONTEXTUAL_CRITERION_PATTERNS = (
    _COREFERENCE_REQUIREMENT_RE,
    _COREFERENCE_DECLARATIVE_CRITERION_RE,
    _OBJECT_REQUIREMENT_RE,
    _PROVIDED_CRITERION_RE,
    _DOCUMENTED_FOR_WHOM_RE,
    _COORDINATE_REQUIRE_RE,
    _FILTER_CRITERION_RE,
)
_SELF_CONTAINED_CRITERION_PATTERNS = (
    _ONLY_INCLUDE_CRITERION_RE,
    _ONLY_SELECT_OR_FILTER_BY_RE,
    _ELIGIBILITY_DEPENDS_RE,
    _DETERMINES_ELIGIBILITY_RE,
)

_AUDIENCE_DECISION_REFERENCE = (
    rf"(?:{_COREFERENCE_POPULATION}|groups?|cohorts?|audiences?|segments?|populations?|"
    rf"{_COREFERENCE_SUBJECT})"
)
# Keep the scanner's leading-capital-I confusable form of ``Insert`` aligned
# with ``marketing_audience_admission`` across the selection-state grammar.
_AUDIENCE_FORMATION_COMMAND_FRAGMENT = (
    r"(?:select|choose|pick|target|include|prioritize|favor|rank|order|sort|reserve|"
    r"sequence|group|elevate|tier|screen|queue|advance|shortlist|nominate|enroll|"
    r"move|transfer|admit|place|add|put|assign|route|direct|(?:insert|lnsert)|"
    r"allocate|dispatch)"
)
_AUDIENCE_FORMATION_ACTION_FRAGMENT = (
    r"(?:select(?:s|ed|ing)?|choos(?:e|es|ing)|chose|chosen|"
    r"pick(?:s|ed|ing)?|target(?:s|ed|ing)?|includ(?:e|es|ed|ing)|"
    r"prioritiz(?:e|es|ed|ing)|favor(?:s|ed|ing)?|rank(?:s|ed|ing)?|"
    r"order(?:s|ed|ing)?|sort(?:s|ed|ing)?|reserv(?:e|es|ed|ing)|"
    r"sequenc(?:e|es|ed|ing)|group(?:s|ed|ing)?|elevat(?:e|es|ed|ing)|"
    r"tier(?:s|ed|ing)?|screen(?:s|ed|ing)?|queue(?:s|d|ing)?|queuing|"
    r"advanc(?:e|es|ed|ing)|shortlist(?:s|ed|ing)?|nominat(?:e|es|ed|ing)|"
    r"enroll(?:s|ed|ing)?|mov(?:e|es|ed|ing)|transfer(?:s|red|ring)?|"
    r"admit(?:s|ted|ting)?|plac(?:e|es|ed|ing)|add(?:s|ed|ing)?|put(?:s|ting)?|"
    r"assign(?:s|ed|ing)?|rout(?:e|es|ed|ing)|direct(?:s|ed|ing)?|"
    r"(?:insert|lnsert)(?:s|ed|ing)?|allocat(?:e|es|ed|ing)|dispatch(?:es|ed|ing)?)"
)
_AUDIENCE_FORMATION_NAMED_POPULATION = (
    rf"(?:{_COREFERENCE_POPULATION}|groups?|cohorts?|audiences?|segments?|populations?)"
)
_AUDIENCE_FORMATION_ACTION_RE = re.compile(
    rf"\b{_AUDIENCE_FORMATION_ACTION_FRAGMENT}\s+"
    r"(?:(?:the|these|those|all|reviewed|eligible|qualified|marketing[- ]eligible|"
    r"highest[- ]scoring|prospective|current)\s+){0,3}"
    rf"{_AUDIENCE_FORMATION_NAMED_POPULATION}\b",
    re.IGNORECASE,
)
_AUDIENCE_FORMATION_BOUND_POPULATION_RE = re.compile(
    rf"\b{_AUDIENCE_FORMATION_ACTION_FRAGMENT}\s+"
    rf"(?:(?P<criterion>[^.!?;:]{{1,80}}?)\s+)?"
    rf"{_AUDIENCE_FORMATION_NAMED_POPULATION}\b",
    re.IGNORECASE,
)
_AUDIENCE_MAKE_CUT_OUTCOME_RE = re.compile(
    r"\b(?:make|makes|made|making|will\s+make|would\s+make)\s+(?:the\s+)?cut\b",
    re.IGNORECASE,
)
_AUDIENCE_DECISION_OUTCOME_RE = re.compile(
    r"\b(?:campaign|offer|benefit|priority|preference|consideration|access|treatment|"
    r"selected|chosen|picked|targeted|eligible|qualified|reserved|prioritized|favored|"
    r"ranked|ordered|sorted|screened|queued|advanced|shortlisted|nominated|enrolled|"
    r"moved|transferred|admitted|placed|added)\b",
    re.IGNORECASE,
)
_AUDIENCE_DECISION_REFERENCE_RE = re.compile(
    rf"\b{_AUDIENCE_DECISION_REFERENCE}\b",
    re.IGNORECASE,
)
_AUDIENCE_CRITERION_SUFFIX_RE = re.compile(
    r"^\s+(?:with(?!\s+(?:(?:this|the|a|an|reviewed)\s+)*"
    r"(?:campaign|offer|review)\b)|having|carrying|showing|displaying|dealing\s+with|"
    r"whose\s+|"
    r"who\s+(?:have|show|carry|meet)|"
    r"that\s+(?:have|has|show|shows|carry|carries|meet|meets)|"
    r"if\s+|where\s+|(?:conditional|conditioned|contingent|dependent)\s+on\s+|"
    r"provided\s+(?:that\s+)?|"
    r"(?:by|according\s+to)\s+(?:whether\s+)?"
    r"(?:(?:they|those|these)\s+)?(?:have|show|carry|meet)|"
    r"by|according\s+to|based\s+on)\b",
    re.IGNORECASE,
)
_REVIEWED_SEGMENT_SIGNAL_BINDING_RE = re.compile(
    r"^\s+with\s+(?P<criterion>.+)$",
    re.IGNORECASE,
)
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
_REVIEWED_READ_ONLY_ANALYTIC_PATTERNS: tuple[re.Pattern[str], ...] = (
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
_REVIEWED_NON_POPULATION_STRATEGY_RE = re.compile(
    # This grammar allocates aggregate outreach capacity; it does not select
    # people or define an audience predicate. Keep both the allocatable object
    # and every grouping dimension closed so a health criterion cannot hide in
    # a canonical strategy-board sentence.
    r"^(?:(?:prioritize|allocate|sequence|rank|order)\s+(?:the\s+)?(?:next\s+)?"
    r"(?:[0-9]{1,9}\s+)?(?:outreach\s+)?(?:touches|capacity|budget)\s+"
    r"(?:by|across)\s+"
    r"(?:states?|count(?:y|ies)|markets?|metros?|segments?|offer\s+lanes?)"
    r"(?:\s*,\s*(?:states?|count(?:y|ies)|markets?|metros?|segments?|"
    r"offer\s+lanes?))*"
    r"(?:\s*,?\s+and\s+(?:states?|count(?:y|ies)|markets?|metros?|segments?|"
    r"offer\s+lanes?))?|"
    # Internal queue ownership is an operating instruction, not an audience
    # criterion. Keep the reviewed object and terminal role closed.
    r"(?:review|inspect|confirm)\s+(?:the\s+)?"
    r"(?:priority|queue|workload|capacity|ownership)\s+distribution\s+and\s+"
    r"(?:assign|confirm)\s+(?:the\s+)?next\s+"
    r"(?:owner|operator|reviewer))$",
    re.IGNORECASE,
)
_SAFE_CHANNEL_CONSENT_REROUTE_RE = re.compile(
    r"^(?:create|build|prepare)\s+(?:a|the)\s+"
    r"(?:(?:refi|refinance|heloc|home[- ]equity|retention|mortgage)\s+)?campaign\s+"
    rf"for\s+(?:a|the)\s+{_COREFERENCE_POPULATION}\s+whose\s+"
    r"(?:documented\s+)?(?:email|phone|sms|text(?:\s+message)?)\s+opt[ -]?out\s+"
    r"is\s+on\s+file\s+and\s+(?:instead\s+)?"
    r"(?:call|email|text|message)\s+(?:them|the\s+(?:borrower|recipient|customer))"
    r"(?:\s+instead)?$",
    re.IGNORECASE,
)
_REVIEWED_SCREEN_GATE_WORKFLOW_RE = re.compile(
    r"^(?:compose\s+(?:a\s+)?(?:reviewed\s+)?(?:growth\s+)?plan\s+to\s+)?"
    r"screen\s+(?:prime\s+)?(?:refi|refinance|mortgage)\s+economics"
    r"(?:\s*,\s*|\s+)(?:then\s+)?gate\s+to\s+eligible(?:\s+leads?)?"
    r"(?:\s*,?\s*(?:and|then)\s+(?:"
    r"prepare\s+(?:a\s+)?lead\s+queue\s+handoff\s+for\s+review|"
    r"hand\s+off\s+for\s+review))?$",
    re.IGNORECASE,
)
_REVIEWED_CAMPAIGN_AUDIENCE_DESCRIPTION = (
    r"(?:borrowers\s+whose\s+current\s+property\s+and\s+listing\s+signals\s+support\s+"
    r"a\s+next[ -]?home\s+conversation|"
    r"borrowers\s+with\s+refinance\s+economics\s+and\s+usable\s+home\s+equity|"
    r"borrowers\s+whose\s+equity\s+and\s+heloc\s+propensity\s+support\s+an\s+"
    r"equity[ -]?access\s+review|"
    r"borrowers\s+whose\s+current\s+lien\s+economics\s+support\s+a\s+refinance\s+review|"
    r"borrowers\s+with\s+substantial\s+modeled\s+equity\s+for\s+a\s+cash[ -]?out\s+review|"
    r"multi[ -]?property\s+borrowers\s+whose\s+financing\s+needs\s+may\s+span\s+more\s+"
    r"than\s+one\s+property|"
    r"existing\s+or\s+former\s+customers\s+with\s+a\s+timely\s+retention\s+review\s+signal|"
    r"borrowers\s+who\s+should\s+receive\s+education\s+rather\s+than\s+a\s+"
    r"product[ -]?specific\s+claim)"
)
_REVIEWED_CAMPAIGN_AUDIENCE_DESCRIPTION_RE = re.compile(
    rf"^{_REVIEWED_CAMPAIGN_AUDIENCE_DESCRIPTION}$",
    re.IGNORECASE,
)
_REVIEWED_CAMPAIGN_AUDIENCE_SUMMARY_RE = re.compile(
    rf"^the\s+selected\s+audience\s+is\s+led\s+by\s+"
    rf"{_REVIEWED_CAMPAIGN_AUDIENCE_DESCRIPTION}\s+and\s+is\s+ready\s+for\s+a\s+"
    r"controlled\s+message\s+test\.?$",
    re.IGNORECASE,
)
_REVIEWED_NAMED_LENDER_RATE_QUERY_RE = re.compile(
    r"^(?:[Ll]ist|[Ss]how|[Cc]ount)\s+(?:[A-Z][A-Za-z0-9&.'-]*\s+){1,3}"
    r"(?:borrowers?|customers?|homeowners?|applicants?)\s+whose\s+"
    r"(?:mortgage\s+)?(?:rate|payment|ltv|loan[- ]to[- ]value|equity)\s+"
    r"(?:is\s+)?(?:above|below|over|under|at\s+least|no\s+more\s+than)\s+"
    r"[0-9olieast]{1,3}(?:\.[0-9olieast]{1,3})?"
    r"(?:\s*(?:%|percent|bps?|basis\s+points?))?$",
)
_AUDIENCE_DIRECTIVE_PREFIX_FRAGMENT = (
    r"(?:(?:could|would|can|will)\s+you\s+)?"
    r"(?:(?:please|kindly)\s*,?\s+)?"
    r"(?:(?:(?:go|move)\s+ahead\s+and|proceed\s+(?:to|and))\s+)?"
)
_APPLY_CRITERION_WHEN_SELECTING_RE = re.compile(
    rf"^{_AUDIENCE_DIRECTIVE_PREFIX_FRAGMENT}"
    r"(?:use|apply|consider)\s+(?P<criterion>[^.!?;:]{1,120}?)\s+"
    r"(?:(?:when|while|in)\s+(?:selecting|choosing|picking|targeting|including|"
    r"prioritizing|favoring|ranking|ordering|sorting|reserving|grouping|screening|"
    r"queuing|queueing|advancing|shortlisting|nominating|enrolling)|"
    rf"to\s+{_AUDIENCE_FORMATION_COMMAND_FRAGMENT})\s+"
    rf"(?:the\s+)?(?:{_AUDIENCE_DECISION_REFERENCE})$",
    re.IGNORECASE,
)
_AUDIENCE_PASSIVE_AUX_FRAGMENT = (
    r"(?:(?:is|are|was|were|gets?|got)|(?:has|have|had)\s+(?:been|gotten)|"
    r"(?:will|would|should|can|could|may|might)\s+(?:be|get))"
)
_AUDIENCE_FORMATION_PARTICIPLE_FRAGMENT = (
    r"(?:selected|chosen|picked|targeted|included|prioritized|favored|ranked|ordered|"
    r"sorted|reserved|sequenced|grouped|elevated|tiered|screened|queued|advanced|"
    r"shortlisted|nominated|enrolled|moved|transferred|admitted|placed|added|put|"
    r"assigned|routed|directed|(?:inserted|lnserted)|allocated|dispatched)"
)
_PASSIVE_AUDIENCE_SELECTION_OUTCOME_RE = re.compile(
    rf"\b{_AUDIENCE_PASSIVE_AUX_FRAGMENT}\s+"
    rf"(?:eligible|qualified|{_AUDIENCE_FORMATION_PARTICIPLE_FRAGMENT})\b",
    re.IGNORECASE,
)
_OUTCOME_THEN_CRITERION_RE = re.compile(
    rf"^\s+{_AUDIENCE_PASSIVE_AUX_FRAGMENT}\s+"
    rf"(?:eligible|qualified|{_AUDIENCE_FORMATION_PARTICIPLE_FRAGMENT})\s+"
    r"(?:by|according\s+to|based\s+on|using)\s+"
    rf"{_CRITERION_TAIL}$",
    re.IGNORECASE,
)
_IMMEDIATE_AUDIENCE_OUTCOME_RE = re.compile(
    rf"^\s+(?:{_AUDIENCE_PASSIVE_AUX_FRAGMENT}\s+)?"
    rf"(?:eligible|qualified|{_AUDIENCE_FORMATION_PARTICIPLE_FRAGMENT})\b",
    re.IGNORECASE,
)
_REVIEWED_DECISION_CRITERION = (
    rf"(?:{_AUDIENCE_DECISION_REFERENCE})\s+"
    r"(?:with|having|carrying|showing|displaying|dealing\s+with|"
    r"by|based\s+on|according\s+to|"
    r"who\s+(?:(?:have|show|carry|meet)|suffer\s+from))\s+"
    rf"(?:{REVIEWED_MORTGAGE_ATTRIBUTE_LIST_FRAGMENT})"
    rf"{REVIEWED_ATTRIBUTE_PURPOSE_FRAGMENT}"
)
_REVIEWED_DIRECTIVE_CRITERION = (
    rf"(?:{_AUDIENCE_DECISION_REFERENCE})\s+(?:"
    r"with|having|carrying|showing|dealing\s+with|"
    r"who\s+(?:have|show|carry|meet)|"
    r"by|based\s+on|according\s+to|"
    r"(?:by|according\s+to)\s+(?:whether\s+)?"
    r"(?:(?:they|those|these)\s+)?(?:have|show|carry|meet))\s+"
    rf"(?:{REVIEWED_MORTGAGE_ATTRIBUTE_LIST_FRAGMENT})"
    rf"{REVIEWED_ATTRIBUTE_PURPOSE_FRAGMENT}"
    rf"(?:,\s*(?:provided\s+(?:that\s+)?(?:they|those|these)\s+"
    rf"(?:have|show|carry|meet)\s+(?:{REVIEWED_MORTGAGE_ATTRIBUTE_LIST_FRAGMENT})"
    rf"{REVIEWED_ATTRIBUTE_PURPOSE_FRAGMENT}|for\s+whom\s+"
    rf"(?:{REVIEWED_MORTGAGE_ATTRIBUTE_LIST_FRAGMENT})\s+"
    r"(?:(?:is|was)\s+)?(?:documented|verified|confirmed|recorded)))?"
    r"(?:\s+and\s+(?:report|summarize|compare)\s+(?:only\s+)?aggregate\s+"
    r"(?:trends?|results?|counts?|metrics?))?"
)
_REVIEWED_WHOSE_DIRECTIVE_CRITERION = (
    rf"(?:{_AUDIENCE_DECISION_REFERENCE})\s+whose\s+"
    rf"(?:{REVIEWED_MORTGAGE_ATTRIBUTE_LIST_FRAGMENT})\s+"
    r"(?:is|are|was|were)\s+(?:active|current|documented|verified|confirmed|recorded)"
    rf"{REVIEWED_ATTRIBUTE_PURPOSE_FRAGMENT}"
)
_REVIEWED_CONDITIONAL_DIRECTIVE_CRITERION = (
    rf"(?:{_AUDIENCE_DECISION_REFERENCE})\s+(?:"
    r"that\s+(?:have|has|show|shows|carry|carries|meet|meets)\s+|"
    r"(?:conditional|conditioned|contingent|dependent)\s+on\s+|"
    r"provided\s+(?:that\s+)?"
    r"(?:(?:they|those|these)\s+)?(?:have|show|carry|meet)\s+|"
    r"(?:if|where)\s+"
    r"(?:(?:they|those|these)\s+)?(?:have|show|carry|meet)\s+)"
    rf"(?:{REVIEWED_MORTGAGE_ATTRIBUTE_LIST_FRAGMENT})"
    rf"{REVIEWED_ATTRIBUTE_PURPOSE_FRAGMENT}"
)
_REVIEWED_AUDIENCE_DECISION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        rf"^(?:[a-z][a-z'-]*\s+){{1,10}}{_REVIEWED_DIRECTIVE_CRITERION}$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^(?:[a-z][a-z'-]*\s+){{1,10}}{_REVIEWED_CONDITIONAL_DIRECTIVE_CRITERION}$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^(?:[a-z][a-z'-]*\s+){{1,10}}{_REVIEWED_WHOSE_DIRECTIVE_CRITERION}$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^{_REVIEWED_DECISION_CRITERION}\s+"
        r"(?:receive|receives|get|gets|obtain|obtains|are\s+given|is\s+given)\s+"
        r"(?:(?:this|the|an?)\s+)?(?:campaign|offer|benefit|priority|preference|"
        r"preferential\s+(?:consideration|treatment|access)|consideration|access)$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^{_REVIEWED_DECISION_CRITERION}\s+"
        rf"(?:{_AUDIENCE_PASSIVE_AUX_FRAGMENT}\s+)?"
        rf"(?:eligible|qualified|{_AUDIENCE_FORMATION_PARTICIPLE_FRAGMENT})"
        r"(?:\s+for\s+(?:(?:this|the|a|an)\s+)?(?:reviewed\s+)?"
        r"(?:(?:refi|refinance|heloc|home[- ]equity|retention|portfolio|purchase|"
        r"mortgage|loan|servicing)\s+)?(?:campaign|offer|options?|review))?$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^{_REVIEWED_WHOSE_DIRECTIVE_CRITERION}\s+"
        rf"(?:{_AUDIENCE_PASSIVE_AUX_FRAGMENT}\s+)?"
        rf"(?:eligible|qualified|{_AUDIENCE_FORMATION_PARTICIPLE_FRAGMENT})"
        r"(?:\s+for\s+(?:(?:this|the|a|an)\s+)?(?:reviewed\s+)?"
        r"(?:(?:refi|refinance|heloc|home[- ]equity|retention|portfolio|purchase|"
        r"mortgage|loan|servicing)\s+)?(?:campaign|offer|options?|review))?$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^(?:{_REVIEWED_DECISION_CRITERION}|{_REVIEWED_WHOSE_DIRECTIVE_CRITERION})\s+"
        r"(?:make|makes|made|will\s+make|would\s+make)\s+(?:the\s+)?cut$",
        re.IGNORECASE,
    ),
)
_REVIEWED_BARE_AUDIENCE_DIRECTIVE_RE = re.compile(
    rf"^{_AUDIENCE_DIRECTIVE_PREFIX_FRAGMENT}"
    rf"{_AUDIENCE_FORMATION_COMMAND_FRAGMENT}\s+(?:the\s+)?"
    rf"(?:(?:reviewed|eligible|qualified|marketing[- ]eligible|highest[- ]scoring)\s+){{0,2}}"
    rf"{_AUDIENCE_DECISION_REFERENCE}"
    r"(?:\s+for\s+(?:(?:this|the|a|an)\s+)?(?:reviewed\s+)?"
    r"(?:campaign|offer|review))?$",
    re.IGNORECASE,
)
_REVIEWED_PRENOMINAL_AUDIENCE_DIRECTIVE_RE = re.compile(
    rf"^{_AUDIENCE_DIRECTIVE_PREFIX_FRAGMENT}"
    rf"{_AUDIENCE_FORMATION_COMMAND_FRAGMENT}\s+(?:the\s+)?"
    rf"(?:{REVIEWED_MORTGAGE_ATTRIBUTE_FRAGMENT})\s+"
    rf"{_AUDIENCE_DECISION_REFERENCE}"
    r"(?:\s+(?:in|into|for)\s+(?:(?:this|the|a|an)\s+)?(?:reviewed\s+)?"
    r"(?:cohort|campaign|audience|segment|list|queue|offer|review))?$",
    re.IGNORECASE,
)
_AFFIRMATIVE_AUDIENCE_DIRECTIVE_RE = re.compile(
    rf"^{_AUDIENCE_DIRECTIVE_PREFIX_FRAGMENT}(?P<directive>"
    rf"{_AUDIENCE_FORMATION_COMMAND_FRAGMENT}|give\s+priority\s+to)\b"
    rf"(?P<before_population>[^.!?;:]{{0,80}}?)\b"
    rf"(?P<population>{_AUDIENCE_DECISION_REFERENCE})\b"
    r"(?P<after_population>[^.!?;:]*)$",
    re.IGNORECASE,
)
_REVIEWED_DIRECTIVE_POPULATION_PREFIX_RE = re.compile(
    r"^(?:(?:the|reviewed|eligible|qualified|marketing[- ]eligible|highest[- ]scoring)\s*){0,3}$",
    re.IGNORECASE,
)
_SAFE_CONTEXTUAL_CTA_RE = re.compile(
    r"^(?:may|can|should)\s+(?:contact|call|email|text|message|reply|respond|reach\s+out)"
    r"(?:\s+(?:to|with)\s+us|\s+us)?"
    r"(?:\s+to\s+(?:review|discuss|consider|learn\s+about)\s+"
    r"(?:(?:their|the|available)\s+)?(?:mortgage|loan|refi|refinance|heloc|home[- ]equity)?"
    r"\s*(?:options?|offer|review))?$",
    re.IGNORECASE,
)
_SAFE_CONTEXTUAL_REVIEW_RE = re.compile(
    r"^(?:is|are)\s+(?:prepared|ready|queued|scheduled)\s+for\s+"
    r"(?:(?:a|the)\s+)?(?:human|manual|operator|lender)\s+review$",
    re.IGNORECASE,
)
_SAFE_CONTEXTUAL_EXCLUSION_RE = re.compile(
    r"^(?:does|do|is|are)\s+(?:not|never)\s+"
    r"(?:(?:depend|rely|hinge)\s+(?:on|upon)|(?:based|conditioned|contingent|dependent)\s+on|"
    r"selected\s+(?:by|based\s+on)|targeted\s+(?:by|based\s+on)|use|require)\s+"
    r"[^.!?;:]{1,120}$",
    re.IGNORECASE,
)


def _is_explicitly_safe_contextual_clause(clause: str) -> bool:
    """Allow only bounded non-criterion clauses after a selected audience."""

    match = _CONTEXTUAL_SUBJECT_CLAUSE_RE.fullmatch(clause)
    if match is None:
        return False
    predicate = re.sub(r"\s+", " ", match.group("predicate").strip(" ,.-"))
    return any(
        pattern.fullmatch(predicate) is not None
        for pattern in (
            _SAFE_CONTEXTUAL_CTA_RE,
            _SAFE_CONTEXTUAL_REVIEW_RE,
            _SAFE_CONTEXTUAL_EXCLUSION_RE,
        )
    )


def _contains_unreviewed_contextual_clause(clause: str) -> bool:
    """Fail closed on later audience co-reference not parsed as reviewed criteria."""

    if _CONTEXTUAL_SUBJECT_CLAUSE_RE.fullmatch(clause) is None:
        return False
    matches: list[re.Match[str]] = []
    for pattern in (*_CONTEXTUAL_CRITERION_PATTERNS, *_SELF_CONTAINED_CRITERION_PATTERNS):
        matches.extend(pattern.finditer(clause))
    if matches:
        return _contains_unreviewed_match(matches)
    return not _is_explicitly_safe_contextual_clause(clause)


def _is_reviewed_pre_population_binding(value: str) -> bool:
    """Return true for empty audience modifiers or a closed mortgage criterion."""

    criterion = re.sub(r"\s+", " ", value.strip(" ,.-"))
    criterion = re.sub(
        r"^(?:(?:the|these|those|all|any|our|your|only|reviewed|eligible|qualified|"
        r"marketing[- ]eligible|highest[- ]scoring|prospective|current)(?:\s+|$))+",
        "",
        criterion,
        flags=re.IGNORECASE,
    )
    criterion = re.sub(r"^for\s+", "", criterion, flags=re.IGNORECASE)
    criterion = re.sub(r"\s+(?:among|from)$", "", criterion, flags=re.IGNORECASE)
    criterion = _normalize_criterion(criterion)
    return bool(
        not criterion
        or _REVIEWED_MORTGAGE_ATTRIBUTE_FULL_RE.fullmatch(criterion) is not None
        or is_closed_reviewed_segment_signal_criterion(criterion)
    )


def _is_reviewed_admission_criterion(value: str) -> bool:
    """Recognize only closed mortgage signals inside an admission relation."""

    criterion = re.sub(r"\s+", " ", value.strip(" ,.-"))
    criterion = re.sub(
        r"^(?:(?:they|those|these|borrowers?|homeowners?|applicants?|recipients?)\s+)"
        r"(?:have|has|show|shows|carry|carries|meet|meets)\s+",
        "",
        criterion,
        flags=re.IGNORECASE,
    )
    criterion = re.sub(r"^(?:their|the)\s+", "", criterion, flags=re.IGNORECASE)
    criterion = re.sub(
        r"\s+(?:is|are|was|were)\s+"
        r"(?:present|current|active|documented|verified|confirmed|recorded)$",
        "",
        criterion,
        flags=re.IGNORECASE,
    )
    criterion = _normalize_criterion(criterion)
    return bool(
        _REVIEWED_MORTGAGE_ATTRIBUTE_FULL_RE.fullmatch(criterion) is not None
        or is_closed_reviewed_segment_signal_criterion(criterion)
    )


def _contains_unreviewed_audience_decision(
    clause: str,
) -> bool:
    """Require affirmative audience decisions to prove a complete reviewed criterion."""

    applied_criterion = _APPLY_CRITERION_WHEN_SELECTING_RE.fullmatch(clause)
    if applied_criterion is not None:
        criterion = _normalize_criterion(applied_criterion.group("criterion"))
        return not (
            _REVIEWED_MORTGAGE_ATTRIBUTE_FULL_RE.fullmatch(criterion) is not None
            or is_closed_reviewed_segment_signal_criterion(criterion)
        )
    admission = audience_admission_criterion(clause)
    if admission is not None:
        return not _is_reviewed_admission_criterion(admission)
    if any(
        pattern.fullmatch(clause) is not None for pattern in _REVIEWED_READ_ONLY_ANALYTIC_PATTERNS
    ):
        return False
    if _REVIEWED_NON_POPULATION_STRATEGY_RE.fullmatch(clause) is not None:
        return False
    if _SAFE_CHANNEL_CONSENT_REROUTE_RE.fullmatch(clause) is not None:
        # Channel-consent validation owns whether the destination channel is
        # actually distinct. This boundary only avoids reclassifying a bounded
        # consent-aware reroute as an unknown audience-selection criterion.
        return False
    if _REVIEWED_SCREEN_GATE_WORKFLOW_RE.fullmatch(clause) is not None:
        return False
    if _REVIEWED_CAMPAIGN_AUDIENCE_SUMMARY_RE.fullmatch(clause) is not None:
        return False
    if _REVIEWED_NAMED_LENDER_RATE_QUERY_RE.fullmatch(clause) is not None:
        # A proper-name lender comparison over a closed mortgage measure is
        # outside the product scope, but it is not protected-class targeting.
        # The Genie scope guard retains ownership of the refusal reason.
        return False
    if _is_explicitly_safe_contextual_clause(clause):
        return False
    if _REVIEWED_BARE_AUDIENCE_DIRECTIVE_RE.fullmatch(clause) is not None:
        return False
    if _REVIEWED_PRENOMINAL_AUDIENCE_DIRECTIVE_RE.fullmatch(clause) is not None:
        return False
    if any(
        pattern.fullmatch(clause) is not None for pattern in _REVIEWED_AUDIENCE_DECISION_PATTERNS
    ):
        return False
    for match in _AUDIENCE_FORMATION_BOUND_POPULATION_RE.finditer(clause):
        if not _is_reviewed_pre_population_binding(match.group("criterion") or ""):
            return True
    directive = _AFFIRMATIVE_AUDIENCE_DIRECTIVE_RE.fullmatch(clause)
    if directive is not None:
        before_population = re.sub(
            r"\s+",
            " ",
            directive.group("before_population").strip(" ,-"),
        )
        suffix = directive.group("after_population")
        signal_binding = _REVIEWED_SEGMENT_SIGNAL_BINDING_RE.fullmatch(suffix)
        has_reviewed_segment_binding = (
            _REVIEWED_DIRECTIVE_POPULATION_PREFIX_RE.fullmatch(before_population) is not None
            and signal_binding is not None
            and is_closed_reviewed_segment_signal_criterion(signal_binding.group("criterion"))
        )
        # Affirmative commands that form, order, or prioritize a marketing
        # audience are a closed grammar. The complete clause must have matched
        # a reviewed Module 0 mortgage/segment criterion above; recognizing a
        # new health term is intentionally irrelevant to this boundary.
        return not has_reviewed_segment_binding
    for candidate in _AUDIENCE_DECISION_REFERENCE_RE.finditer(clause):
        suffix = clause[candidate.end() :]
        if candidate.group(0).lower() == "all" and re.match(
            rf"^\s+(?:selected|reviewed|eligible|qualified)\s+"
            rf"{_AUDIENCE_FORMATION_NAMED_POPULATION}\b",
            suffix,
            re.IGNORECASE,
        ):
            # ``all selected segments`` is one qualified population noun
            # phrase; do not reinterpret ``all`` as an earlier co-reference.
            continue
        formation_prefix = clause[: candidate.end()]
        immediate_outcome = _IMMEDIATE_AUDIENCE_OUTCOME_RE.match(suffix)
        if (
            immediate_outcome is not None
            and _AUDIENCE_FORMATION_ACTION_RE.search(formation_prefix) is None
            and re.search(
                r"\b(?:may|might|could|can|should|would|will|make|makes|made|help|helps|"
                r"allow|allows)\b",
                clause[: candidate.start()],
                re.IGNORECASE,
            )
            is None
            and not _is_reviewed_pre_population_binding(clause[: candidate.start()])
        ):
            return True
        post_outcome_criterion = _OUTCOME_THEN_CRITERION_RE.fullmatch(suffix)
        if post_outcome_criterion is not None:
            criterion = _normalize_criterion(post_outcome_criterion.group("criterion"))
            if not (
                _REVIEWED_MORTGAGE_ATTRIBUTE_FULL_RE.fullmatch(criterion) is not None
                or is_closed_reviewed_segment_signal_criterion(criterion)
            ):
                return True
        passive_selection = _PASSIVE_AUDIENCE_SELECTION_OUTCOME_RE.search(suffix)
        make_cut = _AUDIENCE_MAKE_CUT_OUTCOME_RE.search(suffix)
        decision_outcome = passive_selection or make_cut
        if decision_outcome is not None and suffix[: decision_outcome.start()].strip():
            # Any predicate bound between a population subject and a passive
            # or idiomatic selection outcome is itself a selection criterion.
            # Reviewed mortgage forms returned above; all other predicate
            # vocabulary is rejected without maintaining a health dictionary.
            return True
        has_criterion_connector = _AUDIENCE_CRITERION_SUFFIX_RE.match(suffix) is not None
        signal_binding = _REVIEWED_SEGMENT_SIGNAL_BINDING_RE.fullmatch(suffix)
        if signal_binding is not None and is_closed_reviewed_segment_signal_criterion(
            signal_binding.group("criterion")
        ):
            continue
        has_governed_outcome = _AUDIENCE_DECISION_OUTCOME_RE.search(clause) is not None
        # Once governed marketing text names a borrower population and binds a
        # predicate or outcome, the complete clause must match a reviewed
        # grammar. Connector words are deliberately constrained at the allow
        # boundary rather than treated as evidence that an unknown criterion is
        # safe.
        prefix = clause[: candidate.start()].strip()
        is_population_directive = suffix.strip() != "" and (
            _AUDIENCE_FORMATION_ACTION_RE.search(formation_prefix) is not None
            or bool(prefix and re.fullmatch(r"[A-Za-z][A-Za-z' -]{0,100}", prefix))
        )
        if (has_governed_outcome or is_population_directive) and has_criterion_connector:
            return True
    return False


def is_reviewed_read_only_analytics_text(value: str) -> bool:
    """Return true only when every clause is a closed, read-only analytics query."""

    clauses = [part.strip() for part in re.split(r"[.!?;:\n]+", value) if part.strip()]
    return bool(clauses) and all(
        any(
            pattern.fullmatch(clause) is not None
            for pattern in _REVIEWED_READ_ONLY_ANALYTIC_PATTERNS
        )
        for clause in clauses
    )


def is_reviewed_campaign_audience_summary_text(value: str) -> bool:
    """Return true only for the closed server-rendered offer-audience summary."""

    return _REVIEWED_CAMPAIGN_AUDIENCE_SUMMARY_RE.fullmatch(str(value).strip()) is not None


def is_reviewed_campaign_audience_description_text(value: str) -> bool:
    """Return true only for one closed server-owned offer-audience description."""

    return _REVIEWED_CAMPAIGN_AUDIENCE_DESCRIPTION_RE.fullmatch(str(value).strip()) is not None


def build_selection_context_pattern(*, population_re_fragment: str) -> re.Pattern[str]:
    """Build the bounded audience-selection marker for the criterion state machine."""

    audience = rf"(?:{population_re_fragment}|groups?|cohorts?|audiences?|segments?|populations?)"
    ordinary_action = (
        r"(?:select(?:s|ed|ing)?|choos(?:e|es|ing)|chose|chosen|pick(?:s|ed|ing)?|"
        r"target(?:s|ed|ing)?|includ(?:e|es|ed|ing)|prioritiz(?:e|es|ed|ing)|"
        r"favor(?:s|ed|ing)?|rank(?:s|ed|ing)?|order(?:s|ed|ing)?|sort(?:s|ed|ing)?|"
        r"reserv(?:e|es|ed|ing)|sequenc(?:e|es|ed|ing)|group(?:s|ed|ing)?|"
        r"elevat(?:e|es|ed|ing)|tier(?:s|ed|ing)?|screen(?:s|ed|ing)?|"
        r"queue(?:s|d|ing)?|queuing|advanc(?:e|es|ed|ing)|shortlist(?:s|ed|ing)?|"
        r"nominat(?:e|es|ed|ing)|enroll(?:s|ed|ing)?)"
    )
    # The complete population-to-destination relationship is the selection
    # signal. Keep its verb open so a newly phrased admission still carries
    # governance context into a following clause.
    admission_action = r"(?:[a-z][a-z-]{2,24})"
    destination = r"(?:into|in|to|onto)\s+(?:the\s+|this\s+|that\s+|an?\s+)?(?:campaign|cohort|audience|segment|population|list|queue|offer)"
    return re.compile(
        rf"\b{audience}\b[^.!?;:]{{0,100}}?\b"
        rf"(?:selected|chosen|picked|targeted|eligible|qualified|reserved|prioritized|"
        rf"favored|ranked|ordered|sorted|screened|queued|advanced|shortlisted|nominated|"
        rf"enrolled|(?:make|makes|made|making)\s+(?:the\s+)?cut)|"
        rf"\b{ordinary_action}\b"
        rf"[^.!?;:]{{0,100}}?\b{audience}\b|"
        rf"\b(?:selected|chosen|picked|targeted|eligible|qualified|prioritized|favored|"
        rf"ranked|ordered|sorted|reserved|screened|queued|advanced|shortlisted|nominated|"
        rf"enrolled)\s+{audience}\b|"
        rf"\b{admission_action}\b[^.!?;:]{{0,100}}?\b{audience}\b"
        rf"[^.!?;:]{{0,100}}?\b{destination}\b|"
        rf"\b{audience}\b[^.!?;:]{{0,100}}?\b{admission_action}\b"
        rf"[^.!?;:]{{0,100}}?\b{destination}\b",
        re.IGNORECASE,
    )


def _normalize_criterion(value: str) -> str:
    criterion = re.sub(r"\s+", " ", value.strip(" ,.-"))
    criterion = re.sub(r"\s+(?:too|as\s+well)$", "", criterion, flags=re.IGNORECASE)
    criterion = re.sub(
        r"\s+for\s+(?:(?:this|the|a)\s+)?"
        r"(?:(?:refi|refinance|heloc|home[- ]equity|retention|portfolio|purchase|"
        r"mortgage|loan|servicing)\s+)?(?:campaign|offer|options?|review)\b.*$",
        "",
        criterion,
        flags=re.IGNORECASE,
    )
    criterion = re.sub(
        r"\s+(?:and|then)\s+(?:(?:may|can|should)\s+)?"
        r"(?:contact|call|email|text|message|reply)\b.*$",
        "",
        criterion,
        flags=re.IGNORECASE,
    )
    return criterion.strip(" ,.-")


def _contains_unreviewed_match(matches: list[re.Match[str]]) -> bool:
    for match in matches:
        criterion = _normalize_criterion(match.group("criterion"))
        if not criterion or _REVIEWED_MORTGAGE_ATTRIBUTE_FULL_RE.fullmatch(criterion) is None:
            return True
    return False


def contains_unreviewed_selection_criterion(
    value: str,
    *,
    selection_context_re: re.Pattern[str],
) -> bool:
    """Reject unreviewed criteria structurally coordinated with audience selection."""

    clauses = [part.strip() for part in re.split(r"[.!?;:\n]+", value) if part.strip()]
    selection_seen = False
    for clause in clauses:
        clause = re.sub(
            r"^(?:and|but|then)\s+", "", clause.lstrip(" ,-"), flags=re.IGNORECASE
        )
        clause_selects_audience = selection_context_re.search(clause) is not None
        admission = audience_admission_criterion(clause)
        reviewed_admission = admission is not None and _is_reviewed_admission_criterion(admission)
        if _contains_unreviewed_audience_decision(clause):
            return True
        if selection_seen and _contains_unreviewed_contextual_clause(clause):
            return True
        matches: list[re.Match[str]] = []
        if not reviewed_admission:
            for pattern in _SELF_CONTAINED_CRITERION_PATTERNS:
                matches.extend(pattern.finditer(clause))
            if selection_seen or clause_selects_audience:
                for pattern in _CONTEXTUAL_CRITERION_PATTERNS:
                    matches.extend(pattern.finditer(clause))
        if _contains_unreviewed_match(matches):
            return True
        selection_seen = selection_seen or clause_selects_audience
    return False
