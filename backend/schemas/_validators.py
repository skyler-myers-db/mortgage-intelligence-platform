"""Shared schema validators with no service-layer dependencies."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Sequence

from backend.schemas.lender_identity import validate_public_lender_name
from backend.schemas.marketing_safety_terms import (
    PROTECTED_NATIONAL_ORIGIN_RE,
    build_protected_health_marketing_patterns,
    build_protected_health_term_pattern,
    mask_protected_health_safe_contexts,
)
from backend.schemas.marketing_selection_criteria import (
    build_selection_context_pattern,
    contains_unreviewed_selection_criterion,
    is_reviewed_campaign_audience_description_text,
    is_reviewed_campaign_audience_summary_text,
    is_reviewed_read_only_analytics_text,
)
from backend.schemas.marketing_text_normalization import ascii_confusable_folds
from backend.schemas.protected_relationships import PROTECTED_RELIGION_FAMILIAL_RELATION_RE

_DEFAULT_PUBLIC_LENDER_NAME = "Summit Mortgage"
_PUBLIC_COMPETITOR_REF_RE = re.compile(r"^Competitor ([A-Z]|Other)$")
_PublicLenderNameProvider = Callable[[], str]
_StateFootprintProvider = Callable[[], tuple[Sequence[tuple[str, str]], bool]]
_public_lender_name_provider: _PublicLenderNameProvider | None = None
_state_footprint_provider: _StateFootprintProvider | None = None
_MARKETING_SYMBOL_CONFUSABLES: dict[int, str] = {
    ord("!"): "i",
    ord("$"): "s",
    ord("@"): "a",
    ord("|"): "l",
    ord("¢"): "c",
    ord("£"): "l",
    ord("€"): "e",
    ord("¥"): "y",
}
_PROTECTED_CLASS_MARKETING_RE = re.compile(
    r"\b(?:age|aged|african[\s\-\u2010-\u2015]+americans?|"
    r"alaska[\s\-\u2010-\u2015]+natives?|american[\s\-\u2010-\u2015]+indians?|"
    r"arabs?|asians?|autis(?:m|tic)|blacks?|blind|"
    r"agnostics?|atheists?|baptists?|buddhists?|catholics?|color|deaf|"
    r"disab(?:ility|ilities)|"
    r"disabled\s+(?:adults?|applicants?|borrowers?|customers?|homeowners?|people|persons?)|"
    r"(?:adults?|applicants?|borrowers?|customers?|homeowners?|people|persons?)\s+"
    r"(?:who\s+are\s+)?disabled|wheelchair(?:\s+users?)?|elderly|ethnic(?:ity|ities)?|"
    r"familial status(?:es)?|families? with children|family status(?:es)?|"
    r"parents?|dependents?|"
    r"(?:families?|households?|caregivers?)\s+(?:(?:raising|with|of)\s+"
    r"(?:bab(?:y|ies)|children|dependents?|minors?|newborns?|toddlers?)|(?:(?:awaiting|anticipating|expecting)\s+|preparing\s+for\s+)(?:a\s+)?(?:child|baby|newborn))|expecting (?:a )?baby|"
    r"females?|genders?|handicap(?:s|ped)?|"
    r"gay|lesbian|bisexual|transgender|lgbt(?:q(?:ia2s?)?)?\+?|"
    r"non[- ]?binary|queer|gender identity|"
    r"filipinos?|hispanics?|indigenous|koreans?|latin(?:a|o|x)s?|"
    r"males?|men|man|marital status|"
    r"married|mexicans?|military status(?:es)?|mothers?|fathers?|"
    r"middle eastern|mormons?|muslims?|islam(?:ic)?|christians?|hindus?|jewish|jews?|"
    r"evangelicals?|episcopalians?|jehovah(?:'s)? witnesses?|lutherans?|methodists?|"
    r"pentecostals?|presbyterians?|protestants?|scientologists?|"
    r"churchgoers?|churchgoing|congregants?|worshipp?ers?|believers?|"
    r"(?:people|persons?)\s+who\s+(?:attend\s+church|worship)|"
    r"orthodox|sikhs?|national origins?|native[\s\-\u2010-\u2015]+americans?|"
    r"native[\s\-\u2010-\u2015]+hawaiians?|pacific islanders?|"
    r"hawaiians?|chamorros?|guamanians?|biracial|multiracial|"
    r"pregnanc(?:y|ies)|pregnant|expectant(?:\s+(?:homeowners?|borrowers?|applicants?|"
    r"customers?|people|persons?|parents?|mothers?))?|"
    r"maternity[- ]leave(?:\s+(?:homeowners?|borrowers?|applicants?|customers?|"
    r"people|persons?))?|(?:civil|domestic)[- ]partner(?:ed)?(?:\s+(?:homeowners?|borrowers?|applicants?|customers?|"
    r"people|persons?|partners?))?|civil(?:[- ]union|ly[- ](?:joined|partnered|united|wed))(?:\s+(?:homeowners?|borrowers?|applicants?|customers?|people|persons?|partners?))?|races?|racial|impair(?:ment|ments|ed)|"
    r"mobility[- ]impaired|mobility[- ]aid users?|"
    r"(?:people|persons?)\s+using\s+mobility\s+aids?|"
    r"neurodivergent|special[- ]needs|"
    r"religions?|religious|people of faith|faith[- ]based|divorced|divorcees?|"
    r"marital status(?:es)?|unmarried|unwed|widowed|husbands?|wife|wives|spouses?|"
    r"senior citizens?|sex(?:es)?|sexual orientations?|single (?:mothers?|fathers?|parents?)|"
    r"(?:moms?|dads?|parents?|households?|families?)\s+with\s+(?:kids|children)|"
    r"consumer[- ]credit[- ]rights?|fair[- ]lending\s+complaints?|"
    r"source of income|welfare(?:\s+recipients?)?|"
    r"(?:ssi|supplemental security income)\s+recipients?|veterans?|reservists?|"
    r"national guard members?|armed forces members?|"
    r"military families?|active[- ]duty(?:\s+(?:homeowners?|borrowers?|people|persons?))?|"
    r"service[- ]?members?|whites?|woman|women|womxn|"
    r"(?:people|persons?|adults?|applicants?|borrowers?|customers?|homeowners?|recipients?)"
    r"\s+(?:aged?\s+)?(?:over|under|older than|younger than)\s+\d{1,3}"
    r"(?!\s*(?:bps?|basis\s+points?|%|percent|spread\s+points?))|"
    r"older\s+(?:adults?|applicants?|borrowers?|customers?|homeowners?|people|persons?)|"
    r"younger\s+(?:adults?|applicants?|borrowers?|customers?|homeowners?|people|persons?))\b",
    re.IGNORECASE,
)

_PROTECTED_AGE_CITIZENSHIP_MARKETING_RE = re.compile(
    r"\b(?:baby\s+boomers?|boomers?|gen(?:eration)?\s*[xyz]|millennials?|retirees?|"
    r"retirement[- ]age|young\s+families|foreign[- ]born|non[- ]?citizens?|"
    r"citizenship\s+status|citizens?|naturalized\s+(?:citizens?|homeowners?|"
    r"borrowers?|people|persons?)|"
    r"(?:people|persons?|borrowers?|homeowners?|applicants?)\s+(?:who\s+)?"
    r"(?:were\s+)?born\s+(?:abroad|overseas|outside(?:\s+the)?\s+"
    r"(?:u\.?s\.?|united states))|"
    r"green[ -]?card\s+holders?|lawful[ -]?permanent\s+residents?|"
    r"permanent\s+residents?|visa\s+holders?|immigrants?|refugees?|asylum\s+seekers?|"
    r"undocumented\s+(?:people|persons?|applicants?|borrowers?|homeowners?))\b",
    re.IGNORECASE,
)

# Bind ambiguous status adjectives to people/population nouns so product
# phrases such as ``single-family`` and ``senior lien`` remain available.
_PROTECTED_POPULATION_RE_FRAGMENT = (
    r"(?:people|persons?|individuals?|adults?|residents?|households?|"
    r"homeowners?|borrowers?|applicants?|customers?|"
    r"prospects?|clients?|mortgage\s+holders?|loan\s+holders?|mortgagors?|"
    r"account\s+holders?|members?|leads?|candidates?|recipients?|consumers?|participants?)"
)
_PROTECTED_PTSD_RE_FRAGMENT = (
    r"(?:p(?:[.\s_-]*t)(?:[.\s_-]*s)(?:[.\s_-]*d)|"
    r"post[- ]?traumatic[- ]stress(?:[- ]disorders?)?)"
)
_PROTECTED_MOBILITY_STATUS_RE_FRAGMENT = (
    r"(?:mobility\s+(?:challenges?|limitations?|impairments?|issues?|needs?|restrictions?)|"
    r"(?:limited|impaired|reduced|restricted)\s+mobility)"
)
_PROTECTED_CONTEXTUAL_TRAIT_MARKETING_RE = re.compile(
    r"\b(?:"
    r"(?:(?:sunday|sabbath|easter|lent)[- ]service|young|senior|middle[- ]aged|(?!(?:budget|data|deadline|market|mortgage|policy|price|product|rate|schedule|time|trend)[- ])"
    r"[a-z][a-z-]{2,24}[- ](?:observ(?:ing|ant)|practicing|keeping|worshipp?ing|celebrating|praying|attending)|church[- ]attending)\s+(?:professionals?|homeowners?|borrowers?|"
    r"applicants?|customers?|people|persons?|adults?)|"
    r"(?:under|over)[ -]?\d{1,3}\s+(?:homeowners?|borrowers?|applicants?|"
    r"customers?|people|persons?|adults?)|"
    r"(?:people|persons?|adults?)\s+in\s+their\s+(?:teens?|twenties|thirties|"
    r"forties|fifties|sixties|seventies|eighties|nineties)|empty[- ]nesters?|"
    r"newlyweds?|couples?|(?:single|separated|engaged|cohabiting)\s+"
    r"(?:homeowners?|borrowers?|applicants?|customers?|people|persons?|adults?)|"
    r"(?:foster|adoptive)\s+parents?|guardians?\s+of\s+minors?|"
    rf"{_PROTECTED_POPULATION_RE_FRAGMENT}\s+(?:joined\s+in\s+(?:a\s+)?civil\s+partnership|due\s+to\s+give\s+birth|expecting\s+(?:their\s+)?first\s+child|welcoming\s+(?:a\s+)?bab(?:y|ies)|active\s+in\s+(?:their\s+|a\s+)?(?:congregation|parish|church|mosque|synagogue|temple)|(?:who\s+)?(?:observ(?:e|es|ed|ing)|practic(?:e|es|ed|ing)|keep(?:s|ing)?|kept|celebrat(?:e|es|ed|ing))\s+(?:their\s+faith|ramadan|passover|easter|lent|the\s+sabbath)|(?:who\s+)?attend(?:s|ed|ing)?\s+(?:mass|worship|religious\s+services?|church|mosque|synagogue|temple)|(?:who\s+)?worship(?:s|ped|ping)?\s+(?:on\s+)?(?:sundays?|the\s+sabbath))|"
    r"faith\s+community\s+members?|parishioners?|(?:members?\s+of\s+the\s+)?clergy|"
    r"members?\s+of\s+(?:a\s+)?(?:congregation|church|mosque|synagogue|temple)|"
    r"religious\s+community\s+members?|"
    r"(?:expats?|expatriates?|foreign\s+nationals?)\s+(?:homeowners?|borrowers?|"
    r"applicants?|customers?|people|persons?)|"
    r"(?:overseas|non[- ]?u\.?s\.?)[- ]born\s+(?:homeowners?|borrowers?|"
    r"applicants?|customers?|people|persons?)|"
    r"(?:people|persons?|homeowners?|borrowers?|applicants?)\s+(?:who\s+)?"
    r"(?:were\s+)?born\s+outside(?:\s+the)?\s+(?:u\.?s\.?|united states|america)|"
    r"assistive[- ]device\s+users?|hearing[- ]aid\s+users?|visually[- ]challenged|"
    rf"mobility[- ](?:challenged|limited|impaired)\s+{_PROTECTED_POPULATION_RE_FRAGMENT}|"
    rf"mobility[- ](?:challenge|limitation|impairment)[- ]affected\s+{_PROTECTED_POPULATION_RE_FRAGMENT}|"
    rf"(?:limited|impaired|reduced|restricted)[- ]mobility\s+{_PROTECTED_POPULATION_RE_FRAGMENT}|"
    rf"{_PROTECTED_POPULATION_RE_FRAGMENT}\s+"
    rf"(?:with|who\s+have|facing|experiencing|living\s+with|affected\s+by|managing)\s+"
    rf"{_PROTECTED_MOBILITY_STATUS_RE_FRAGMENT}|"
    rf"{_PROTECTED_POPULATION_RE_FRAGMENT}\s+whose\s+mobility\s+is\s+(?:challenged|limited|impaired|reduced|restricted)|"
    rf"{_PROTECTED_POPULATION_RE_FRAGMENT}\s+(?:requiring|using|dependent\s+on)\s+mobility\s+aids?|"
    r"(?:people|persons?)\s+(?:with|who have)\s+(?:chronic\s+)?"
    r"(?:illness(?:es)?|conditions?|accessibility\s+needs)|"
    r"(?:serious|long[- ]term)\s+(?:medical|health)\s+conditions?|"
    r"accessibility\s+accommodations?|differently[- ]abled|"
    r"(?:social security|snap|wic|tanf|ssi|child[- ]support|alimony|pension|"
    r"unemployment|disability[- ]income|food[- ]stamps?|medicaid|housing[- ]assistance)"
    r"\s+recipients?|section\s*8\s+voucher\s+holders?|"
    r"(?:borrowers?|applicants?|customers?|people|persons?)\s+who\s+"
    r"(?:(?:filed|made|submitted)\s+(?:a\s+)?(?:discrimination|fair[- ]lending)\s+complaint|"
    r"reported\s+discrimination|exercised\s+fair[- ]lending\s+rights)|"
    r"unpartnered\s+(?:homeowners?|borrowers?|applicants?|customers?|people|persons?)|"
    r"domestic\s+partners?|retirement[- ]community\s+residents?|recent\s+graduates?|"
    r"elders?|older\s+generations?|former\s+service\s+personnel|"
    r"first[- ]generation\s+americans?|members?\s+of\s+the\s+diaspora|"
    r"observant\s+households?"
    r")\b",
    re.IGNORECASE,
)

(
    _PROTECTED_HEALTH_STATUS_MARKETING_RE,
    _PROTECTED_HEALTH_GOVERNANCE_INTENT_RE,
) = build_protected_health_marketing_patterns(
    population_re_fragment=_PROTECTED_POPULATION_RE_FRAGMENT,
    ptsd_re_fragment=_PROTECTED_PTSD_RE_FRAGMENT,
    mobility_re_fragment=_PROTECTED_MOBILITY_STATUS_RE_FRAGMENT,
)
_PROTECTED_HEALTH_TERM_MARKETING_RE = build_protected_health_term_pattern(
    ptsd_re_fragment=_PROTECTED_PTSD_RE_FRAGMENT,
    mobility_re_fragment=_PROTECTED_MOBILITY_STATUS_RE_FRAGMENT,
)
_PROTECTED_HEALTH_SELECTION_CONTEXT_RE = build_selection_context_pattern(
    population_re_fragment=_PROTECTED_POPULATION_RE_FRAGMENT
)
_AUDIENCE_OUTCOME_CLAIM_RE = re.compile(
    r"(?:^|[.!?;:\n])\s*(?P<audience>[^.!?;:\n]+?)\s+"
    r"(?:may|might|could|can|should)"
    r"(?:[^A-Za-z0-9.!?;]+[A-Za-z-]+ly){0,3}"
    r"(?:[^A-Za-z0-9.!?;]+be[^A-Za-z0-9.!?;]+able"
    r"[^A-Za-z0-9.!?;]+to)?"
    r"[^A-Za-z0-9.!?;]+(?:benefit|qualify|"
    r"be[^A-Za-z0-9.!?;]+eligible)"
    r"(?:[^A-Za-z0-9.!?;]+[A-Za-z-]+ly){0,3}"
    r"(?=\s*(?:from\b|for\b|today\b|[;,!?.]|$))",
    re.IGNORECASE,
)
_REVIEWED_GENERIC_AUDIENCE_RE = re.compile(
    r"(?:today[,]?\s+)?(?:you|your\s+(?:property\s+portfolio|portfolio|profile|loan|"
    r"mortgage\s+profile|household)|(?:a|an|this|that)\s+(?:borrower|homeowner|"
    r"applicant|customer|household|profile)|(?:(?:all|many|some|eligible|qualified|"
    r"reviewed|current|prospective|these|those|our|your)\s+)?(?:borrowers?|"
    r"homeowners?|applicants?|customers?|households?|people|persons?|recipients?|"
    r"property\s+portfolios?|loan\s+profiles?))",
    re.IGNORECASE,
)
_STRUCTURAL_AUDIENCE_KEYWORD_PATTERNS = tuple(
    (
        keyword,
        re.compile(
            r"(?<![A-Za-z])"
            + r"[^A-Za-z]*".join(re.escape(letter) for letter in keyword)
            + r"(?![A-Za-z])",
            re.IGNORECASE,
        ),
    )
    for keyword in (
        "eligible",
        "benefit",
        "qualify",
        "should",
        "might",
        "could",
        "may",
        "can",
    )
)


def _structural_audience_scan_variants(value: str) -> set[str]:
    """Canonicalize only governed audience-claim keywords, preserving sentences."""

    in_word_symbols: list[str] = []
    for index, char in enumerate(value):
        replacement = _MARKETING_SYMBOL_CONFUSABLES.get(ord(char))
        previous_is_ascii_letter = (
            index > 0 and value[index - 1].isascii() and value[index - 1].isalpha()
        )
        next_is_ascii_letter = (
            index + 1 < len(value) and value[index + 1].isascii() and value[index + 1].isalpha()
        )
        in_word_symbols.append(
            replacement
            if replacement is not None and previous_is_ascii_letter and next_is_ascii_letter
            else char
        )
    symbol_folded = "".join(in_word_symbols)
    variants = {
        value,
        symbol_folded,
        value.translate(str.maketrans("013457", "oleast")),
        value.translate(str.maketrans("013457", "oieast")),
        symbol_folded.translate(str.maketrans("013457", "oleast")),
        symbol_folded.translate(str.maketrans("013457", "oieast")),
    }
    canonical: set[str] = set()
    for variant in variants:
        folded = variant
        for keyword, pattern in _STRUCTURAL_AUDIENCE_KEYWORD_PATTERNS:
            folded = pattern.sub(keyword, folded)
        canonical.add(folded)
    return canonical


def _contains_unreviewed_audience_outcome_claim(value: str) -> bool:
    """Fail closed on unreviewed population descriptions in benefit claims."""

    for match in _AUDIENCE_OUTCOME_CLAIM_RE.finditer(value):
        audience = " ".join(match.group("audience").strip(" ,").split())
        if not _is_reviewed_generic_audience(audience):
            return True
    return False


def _is_reviewed_generic_audience(value: str) -> bool:
    return _REVIEWED_GENERIC_AUDIENCE_RE.fullmatch(value) is not None


# Governed national-origin vocabulary used only when the term occurs near a
# population or targeting verb. Keeping this explicit makes changes auditable
# and avoids treating arbitrary geography/product prose as protected targeting.
_PROMPT_INJECTION_RE = re.compile(
    r"\b(?:ignore|disregard|override|bypass|forget)\b.{0,80}\b"
    r"(?:previous|prior|system|developer|safety|guardrail|policy|rules?|instructions?|prompt)\b|"
    r"\b(?:system|developer)\s+(?:message|prompt)\b|"
    r"\b(?:reveal|show|print|return|expose)\b.{0,60}\b(?:hidden|system|developer)\s+prompt\b|"
    r"\b(?:jailbreak|prompt injection|do anything now|follow these new instructions)\b|"
    r"<\|(?:system|developer|assistant|user)\|>|"
    r"^\s*(?:system|developer)\s*:",
    re.IGNORECASE | re.DOTALL | re.MULTILINE,
)

_CONFIDENTIAL_OR_INTERNAL_TEXT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:api[-_ ]?keys?|client[-_ ]?secrets?|passwords?|passwds?|credentials?|"
        r"access[-_ ]?tokens?|refresh[-_ ]?tokens?|authorization\s*[:=]|"
        r"bearer\s+[A-Za-z0-9._~+/=-]{4,}|(?:databricks|lakebase|oauth|session|auth)"
        r"[-_ ]?tokens?)\b|\b[A-Z][A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|API_KEY)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:dapi[a-z0-9]{16,}|AKIA[0-9A-Z]{16}|"
        r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:system|developer|internal|hidden|private)\s+"
        r"(?:prompt|message|instructions?|policy|configuration)\b|"
        r"\b(?:chain[- ]of[- ]thought|hidden reasoning|model scratchpad)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:https?|wss?|ftp|jdbc|databricks|s3|dbfs)://\S+|"
        r"\bwww\.[A-Za-z0-9.-]+(?:/\S*)?|"
        r"\b(?:localhost|127\.0\.0\.1)(?::\d{2,5})?(?:/\S*)?|"
        r"\b[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?\."
        r"(?:com|net|org|io|ai|cloud|internal|local|dev)(?::\d{2,5})?(?:/\S*)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<![A-Za-z0-9])/(?:api|oauth2?|serving-endpoints?|secrets?|sql|warehouses?)"
        r"(?:/[A-Za-z0-9._~:@!$&'()*+,;=-]+)+|"
        r"\b(?:serving|model|workspace|internal|private|databricks|lakebase)[-_ ]endpoints?\b",
        re.IGNORECASE,
    ),
)

_TITLECASE_HUMAN_NAME_RE = re.compile(
    r"\b[A-Z][a-z]{1,30}(?:\s+|\s*\|\s*)(?:[A-Z](?:\s+|\s*\|\s*))?" r"[A-Z][a-z]{1,30}\b"
)
_LEADING_ANALYTICS_COMMAND_RE = re.compile(
    r"\b(?:Compare|Explain|Find|List|Open|Prioritize|Rank|Review|Show|Target)\s+(?=[A-Z])"
)
_NON_PERSON_TITLECASE_SUFFIXES = frozenset(
    {
        "borough",
        "city",
        "county",
        "metro",
        "msa",
        "parish",
        "region",
        "township",
        # US place-name geographic-feature suffixes (Lake Forest, Grand
        # Prairie, Coral Springs). Title-case city names are sanctioned
        # analytics output — borrower rows carry the same city strings — and
        # no real-person identity in this product ever renders with these
        # suffixes (borrower names never ship; display identities are
        # synthetic masked IDs).
        "beach",
        "bluffs",
        "canyon",
        "creek",
        "falls",
        "forest",
        "gardens",
        "grove",
        "harbor",
        "heights",
        "hills",
        "island",
        "junction",
        "lake",
        "lakes",
        "meadows",
        "mesa",
        "oaks",
        "park",
        "pines",
        "plains",
        "point",
        "prairie",
        "rapids",
        "ridge",
        "shores",
        "springs",
        "station",
        "valley",
        "village",
        "vista",
        "woods",
        # Mortgage-product phrase suffixes ("Purchase Mortgage",
        # "Cash-Out Refinance", "Home-Equity Review") — governed offer
        # vocabulary rendered in title case, never a person.
        "heloc",
        "loan",
        "mortgage",
        "offer",
        "refinance",
        "review",
    }
)
_COMMON_FIRST_NAMES = frozenset(
    {
        "alice",
        "barbara",
        "david",
        "elizabeth",
        "james",
        "jane",
        "jennifer",
        "john",
        "joseph",
        "linda",
        "maria",
        "mary",
        "michael",
        "patricia",
        "richard",
        "robert",
        "sarah",
        "thomas",
        "william",
    }
)
_COMMON_LAST_NAMES = frozenset(
    {
        "anderson",
        "brown",
        "davis",
        "doe",
        "garcia",
        "gonzalez",
        "hernandez",
        "johnson",
        "jones",
        "lee",
        "lopez",
        "martinez",
        "miller",
        "moore",
        "rodriguez",
        "smith",
        "taylor",
        "thomas",
        "williams",
        "wilson",
    }
)

_REVIEWED_NON_PERSON_PHRASES: tuple[str, ...] = (
    "Mortgage Intelligence Platform",
    "Mortgage Growth Agent",
    "Daily Refi Opportunity Brief",
    "Listed-for-Sale Purchase Watch",
    "Competitor Recapture Monitor",
    "High-Equity HELOC Watch",
    "Borrower Dossier Review",
    "Call consent",
    "Branch Manager Capacity Review",
    "Custom Segment Workflow",
    "Source Freshness Sentinel",
    "Building Permits",
    "Equal Housing Lender",
    "Equal Housing",
    "Offer Orchestrator",
    "Portfolio Builder",
    "Genie Conversation",
    "Databricks Genie",
    "Databricks Agent Responses",
    "Unity Catalog",
    "Supervisor Agent",
    "Growth Agent",
    "Lead Queue",
    "Borrower Dossier",
    "Mosaic AI",
    "Agent Bricks",
    "New Hampshire",
    "New Jersey",
    "New Mexico",
    "New York",
    "North Carolina",
    "North Dakota",
    "Rhode Island",
    "South Carolina",
    "South Dakota",
    "West Virginia",
    "United States",
)

_PROTECTED_CLASS_SAFE_CONTEXT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:loan|lien)\s+ages?\b", re.IGNORECASE),
    re.compile(r"\bages?\s+of\s+(?:the\s+)?(?:loan|lien)s?\b", re.IGNORECASE),
    re.compile(r"\b(?:loan|lien)\s+aging\b", re.IGNORECASE),
    re.compile(
        r"\b(?:white|black)\s+(?:plains|settlement|salmon|center|creek|river|"
        r"falls|rock|oaks?|haven|bluffs?|stone|mountain|hills?|city|county|lake|"
        r"earth|water|sands?|house|hall|bear|fish|hawk|diamond)\b",
        re.IGNORECASE,
    ),
)

_PROTECTED_CLASS_PROXY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bmajority[- ]minority\b", re.IGNORECASE),
    re.compile(
        r"\b(?:spanish[- ]speaking|limited[- ]english(?: proficiency)?|"
        r"limited english proficient)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:immigrants?|refugees?)\b", re.IGNORECASE),
    re.compile(r"\bmosques?\b", re.IGNORECASE),
    re.compile(
        r"\b(?:section\s*8(?:\s+housing)?|housing[- ]vouchers?|public[- ]assistance)\b",
        re.IGNORECASE,
    ),
)
_PROTECTED_CLASS_PROXY_SAFE_CONTEXT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\bspanish[- ]speaking\s+(?:loan officers?|staff|representatives?|"
        r"support|services?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\blimited[- ]english(?: proficiency)?\s+"
        r"(?:support|services?|materials?|disclosures?|translations?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bsection\s*8\s+of\s+(?:the\s+)?(?:document|report|review|policy|appendix)\b",
        re.IGNORECASE,
    ),
)
_PROTECTED_CLASS_PROXY_HARD_TARGETING_RE = re.compile(
    r"\b(?:target|targeting|prioritize|rank|score|segment|filter|exclude|select|"
    r"redline|steer|solicit|prospect|market to|advertise to|campaign to|outreach to|"
    r"contact)\b",
    re.IGNORECASE,
)
_PROTECTED_CLASS_PROXY_POPULATION_RE = re.compile(
    r"\b(?:applicants?|borrowers?|communities|community|customers?|households?|"
    r"homeowners?|leads?|neighbou?rhoods?|postal codes?|prospects?|recipients?|"
    r"residents?|tracts?|zip codes?|zips?)\b",
    re.IGNORECASE,
)

_MECHANICAL_PII_OR_RAW_IDENTIFIER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    re.compile(r"(?<!\d)(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}(?!\d)"),
    re.compile(
        r"\b\d{1,6}\s+[A-Za-z0-9.'-]+(?:\s+[A-Za-z0-9.'-]+){0,4}\s+"
        r"(?:st|street|ave|avenue|rd|road|dr|drive|ln|lane|blvd|boulevard|ct|court|"
        r"ter|terrace|way|pl|place|pkwy|parkway)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:clip_ref|raw[_\s-]?clip|owner[_\s-]?link(?:[_\s-]?id)?|"
        r"owner[_\s-]?name|borrower[_\s-]?name|customer[_\s-]?name|"
        r"prospect[_\s-]?name|street[_\s-]?address|mailing[_\s-]?address)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:clip|owner[_\s-]?(?:link|identifier|id)|borrower[_\s-]?identifier)\b"
        r"\s*[:#=]\s*[A-Za-z0-9_-]{6,}\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:account|loan)\s+(?:number|id)\s*[:#=]?\s*[A-Za-z0-9_-]{6,}\b", re.IGNORECASE),
    re.compile(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b\d{9,}\b"),
    re.compile(
        r"\[(?:first|last|full)[_\s-]?name\]|\{(?:first|last|full)[_\s-]?name\}|"
        r"\binsert governed\b",
        re.IGNORECASE,
    ),
)

_CONTEXTUAL_HUMAN_NAME_RE = re.compile(
    r"\b(?:call|contact|email|message|text|ask|target|prioritize|dear|hello|hi)\s+"
    r"(?!(?:to|the|a|an|and|or|at|about|before|if|when|this|that|these|those|your|our|us|me|you|"
    r"them|him|her|it|then|provider|carrier|system|platform|service|gateway|authorization|consent|permission|outreach|contact|records?|"
    # Domain population/ranking vocabulary: "prioritize overall", "contact
    # borrowers", "target top segments" are core product phrasings, not
    # person-name lookups. Real names never take these words.
    r"all|any|each|every|only|both|overall|first|next|now|today|top|"
    r"borrowers?|leads?|candidates?|prospects?|customers?|clients?|"
    r"segments?|cohorts?|homeowners?|investors?|people|everyone|anyone|someone|"
    r"is|are|was|were|will|would|can|could|may|might|has|have|had)\b)"
    r"[A-Za-z]{2,30}\s+[A-Za-z]{2,30}\b|"
    r"\b[A-Za-z]{2,30}\s+[A-Za-z]{2,30}\s+(?:qualifies?|is the top borrower)\b",
    re.IGNORECASE,
)


def set_public_lender_name_provider(provider: _PublicLenderNameProvider | None) -> None:
    """Register the configured tenant lender without importing runtime settings."""

    global _public_lender_name_provider
    _public_lender_name_provider = provider


def _configured_public_lender_name() -> str:
    if _public_lender_name_provider is None:
        return validate_public_lender_name(_DEFAULT_PUBLIC_LENDER_NAME)
    try:
        configured = _public_lender_name_provider()
    except Exception:
        configured = _DEFAULT_PUBLIC_LENDER_NAME
    return validate_public_lender_name(configured or _DEFAULT_PUBLIC_LENDER_NAME)


def configured_public_lender_name() -> str:
    """Return the public display name for the configured tenant lender."""

    return _configured_public_lender_name()


def set_state_footprint_provider(provider: _StateFootprintProvider | None) -> None:
    """Register the runtime geography resolver without importing services."""

    global _state_footprint_provider
    _state_footprint_provider = provider


def _state_footprint_snapshot() -> tuple[Sequence[tuple[str, str]], bool]:
    if _state_footprint_provider is None:
        return (), True
    return _state_footprint_provider()


def reviewed_geography_labels() -> set[str]:
    """Return lowercased Portfolio Builder geography labels."""

    states, using_fallback = _state_footprint_snapshot()
    if using_fallback:
        return {"all"}
    labels = {"all", *(name.lower() for _code, name in states)}
    labels.add(f"all {len(states)} states")
    return labels


def reviewed_state_codes() -> set[str]:
    """Return currently reviewed two-letter state codes, or empty on fallback."""

    states, using_fallback = _state_footprint_snapshot()
    if using_fallback:
        return set()
    return {code for code, _name in states}


def is_public_lender_ref(value: str | None, *, allow_all: bool = False) -> bool:
    """Return TRUE when ``value`` is from the public-safe lender vocabulary."""

    if value is None:
        return False
    stripped = value.strip()
    if allow_all and stripped == "All":
        return True
    if stripped == _configured_public_lender_name():
        return True
    return bool(_PUBLIC_COMPETITOR_REF_RE.fullmatch(stripped))


def normalize_public_lender_ref(
    value: str | None,
    *,
    allow_all: bool = False,
) -> str | None:
    """Validate a caller-provided lender filter without generalizing raw input."""

    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if is_public_lender_ref(stripped, allow_all=allow_all):
        return stripped
    raise ValueError("target_lender_ref must be a public-safe lender alias")


def assert_no_protected_class_marketing_text(value: str, *, field_name: str) -> str:
    """Reject protected-class language from targeting or outreach copy.

    This is intentionally narrower than a general prose validator. It is used
    only at campaign/outreach decision boundaries, where protected-class
    language must fail closed instead of being silently scrubbed or persisted.
    """

    if contains_protected_class_marketing_text(value):
        raise ValueError(f"{field_name} cannot contain protected-class targeting language")
    return value


def contains_protected_class_marketing_text(
    value: str,
    *,
    assume_reviewed_read_only_analytics: bool = False,
) -> bool:
    """Return true for protected-class or obvious proxy targeting language.

    ``assume_reviewed_read_only_analytics`` marks the caller's surface as a
    read-only analytics narrative (Ask Genie answers) whose ranking vocabulary
    ("candidates are those with the highest opportunity scores") is the
    product's core language. It bypasses ONLY the fail-closed unknown-criterion
    state machine that exists for campaign audience formation; every direct
    protected-class, health-status, national-origin, and proxy-targeting
    detector still runs.
    """

    normalized = unicodedata.normalize("NFKC", str(value))
    # Campaign copy is an English-language governed surface. Invisible format
    # controls and non-ASCII alphabetic confusables make exact safety matching
    # non-auditable, so reject rather than attempting a lossy transliteration.
    if any(unicodedata.category(char) == "Cf" for char in normalized):
        return True
    if any(
        unicodedata.category(char).startswith("L")
        and not ("A" <= char <= "Z" or "a" <= char <= "z")
        for char in normalized
    ):
        return True
    # NFKC intentionally preserves ordinary Latin diacritics. Strip combining
    # marks from an NFKD scan copy so accents cannot split a protected term
    # into unrelated ASCII fragments (for example ``Wómën`` or ``Müslïm``).
    # Keep ``normalized`` above for script/control validation and never persist
    # this lossy safety-only representation.
    mark_folded = "".join(
        char
        for char in unicodedata.normalize("NFKD", normalized)
        if not unicodedata.category(char).startswith("M")
    )
    if is_reviewed_campaign_audience_description_text(
        mark_folded
    ) or is_reviewed_campaign_audience_summary_text(mark_folded):
        # The server renders these fields from a closed offer-to-audience map.
        # Handle it before the generic unknown-health relationship detector,
        # whose fail-closed ``population with X`` grammar intentionally cannot
        # infer that each complete product description is governed. Full-match
        # semantics ensure appended or substituted criteria remain scannable.
        return False
    if any(
        _contains_unreviewed_audience_outcome_claim(candidate)
        for candidate in _structural_audience_scan_variants(mark_folded)
    ):
        return True
    # Scan ordinary prose plus bounded de-obfuscations. The marketing surface
    # has no valid need for leetspeak or split-word spelling; joining up to
    # eight adjacent ASCII tokens catches forms such as ``w0men``, ``wo.men``,
    # ``w o m e n``, and ``mus lim`` without stripping the whole sentence into
    # one unauditable string.
    symbol_variants = {
        mark_folded,
        # Explicit, reviewed in-word symbol substitutions. Keep the original
        # scan alongside this fold so ordinary punctuation remains a boundary
        # while evasions such as ``Wom€n`` and ``Musl!m`` cannot pass.
        mark_folded.translate(_MARKETING_SYMBOL_CONFUSABLES),
    }
    leet_variants = {
        translated
        for variant in symbol_variants
        for translated in (
            variant.translate(str.maketrans("013457", "oleast")),
            variant.translate(str.maketrans("013457", "oieast")),
        )
    }

    # Add only reviewed ASCII lookalike folds. These variants are safety-scan
    # inputs, never rewritten campaign copy: ``vv`` is commonly substituted
    # for ``w`` and a capital ``I`` for lowercase ``l`` in otherwise ordinary
    # words. Applying both orders catches combinations without opening a
    # general edit-distance matcher that would be difficult to audit.
    ascii_confusable_variants = {
        folded for variant in leet_variants for folded in ascii_confusable_folds(variant)
    }
    deobfuscated: set[str] = set()
    separator_folded: set[str] = set()
    joined_tokens: set[str] = set()
    for leet_folded in ascii_confusable_variants:
        separator_folded.add(
            # Safety-scan only: campaign prose has no legitimate reason to
            # make punctuation distinguish a protected multiword term. Fold
            # every non-alphanumeric separator run (including Unicode
            # bullets/dashes) to one space while preserving the submitted
            # copy unchanged for ordinary validation/persistence.
            re.sub(r"[^A-Za-z0-9]+", " ", leet_folded).strip()
        )
        deobfuscated.add(
            re.sub(
                r"(?<=[A-Za-z])[\-\u2010-\u2015](?=[A-Za-z])",
                "",
                leet_folded,
            )
        )
        tokenized = re.findall(r"[A-Za-z]+", leet_folded)
        joined_tokens.update(
            "".join(tokenized[start:stop])
            for start in range(len(tokenized))
            # Only join genuinely split terms. Re-emitting ordinary one-token
            # windows detaches words such as ``age`` and ``English`` from the
            # safe context already reviewed above, creating false positives.
            for stop in range(start + 2, min(len(tokenized), start + 8) + 1)
            if sum(len(token) for token in tokenized[start:stop]) <= 32
        )
    # Composition order must not reopen the boundary: apply the same bounded
    # folds after punctuation/split-token joining as well as before it.
    ascii_confusable_variants.update(
        folded
        for variant in deobfuscated | joined_tokens
        for folded in ascii_confusable_folds(variant)
    )
    scannable_parts = (
        mark_folded,
        *sorted(ascii_confusable_variants),
        *sorted(deobfuscated),
        *sorted(separator_folded),
        *sorted(joined_tokens),
    )
    scannable = " ".join(scannable_parts)
    for pattern in _PROTECTED_CLASS_SAFE_CONTEXT_PATTERNS:
        scannable = pattern.sub(" ", scannable)
    # Keep punctuation-preserving representations for criterion state.
    health_semantic_parts = (mark_folded, *sorted(leet_variants), *sorted(deobfuscated))
    health_scannable = " ; ".join(
        mask_protected_health_safe_contexts(part) for part in health_semantic_parts
    )
    # Direct status matching can use lossy separator/token normalization; selection state cannot.
    health_status_scannable = " ; ".join(
        mask_protected_health_safe_contexts(part) for part in scannable_parts
    )
    reviewed_analytics = is_reviewed_read_only_analytics_text(mark_folded)
    has_unreviewed_selection_criterion = (
        False
        if (reviewed_analytics or assume_reviewed_read_only_analytics)
        else any(
            contains_unreviewed_selection_criterion(
                mask_protected_health_safe_contexts(part),
                selection_context_re=_PROTECTED_HEALTH_SELECTION_CONTEXT_RE,
            )
            # Separator-folded text is invalid for the clause state machine.
            for part in health_semantic_parts
        )
    )
    return bool(
        _PROTECTED_CLASS_MARKETING_RE.search(scannable)
        or _PROTECTED_AGE_CITIZENSHIP_MARKETING_RE.search(scannable)
        or _PROTECTED_CONTEXTUAL_TRAIT_MARKETING_RE.search(scannable)
        or PROTECTED_RELIGION_FAMILIAL_RELATION_RE.search(scannable)
        or (not reviewed_analytics and _PROTECTED_HEALTH_TERM_MARKETING_RE.search(health_scannable))
        or _PROTECTED_HEALTH_STATUS_MARKETING_RE.search(health_status_scannable)
        or _PROTECTED_HEALTH_GOVERNANCE_INTENT_RE.search(health_scannable)
        or has_unreviewed_selection_criterion
        or _contains_national_origin_marketing_text(scannable)
        or contains_protected_class_proxy_marketing_text(scannable)
    )


def _contains_national_origin_marketing_text(value: str) -> bool:
    scannable = value
    for safe_pattern in _PROTECTED_CLASS_PROXY_SAFE_CONTEXT_PATTERNS:
        scannable = safe_pattern.sub(" ", scannable)
    for match in PROTECTED_NATIONAL_ORIGIN_RE.finditer(scannable):
        window = scannable[max(0, match.start() - 120) : match.end() + 120]
        if _PROTECTED_CLASS_PROXY_HARD_TARGETING_RE.search(window):
            return True
        if _PROTECTED_CLASS_PROXY_POPULATION_RE.search(window):
            return True
    return False


def contains_protected_class_proxy_marketing_text(value: str) -> bool:
    """Detect protected-class proxies only when used for people or targeting.

    The proxy terms are not intrinsically unsafe. Language-access support,
    branch-service coverage, and document section references remain usable;
    borrower/geography selection and explicit targeting fail closed.
    """

    text = str(value)
    for safe_pattern in _PROTECTED_CLASS_PROXY_SAFE_CONTEXT_PATTERNS:
        text = safe_pattern.sub(" ", text)
    for pattern in _PROTECTED_CLASS_PROXY_PATTERNS:
        for match in pattern.finditer(text):
            window = text[max(0, match.start() - 120) : match.end() + 120]
            if _PROTECTED_CLASS_PROXY_HARD_TARGETING_RE.search(window):
                return True
            if _PROTECTED_CLASS_PROXY_POPULATION_RE.search(window):
                return True
    return False


def contains_prompt_injection_text(value: str) -> bool:
    """Return true for instruction-override language at governed text boundaries."""

    return bool(_PROMPT_INJECTION_RE.search(str(value)))


def contains_confidential_or_internal_text(value: str) -> bool:
    """Detect secrets, credentials, internal instructions, URLs, and endpoints."""

    text = str(value)
    return any(pattern.search(text) for pattern in _CONFIDENTIAL_OR_INTERNAL_TEXT_PATTERNS)


def contains_human_name_shape(
    value: str,
    *,
    allowed_phrases: Sequence[str] = (),
    include_titlecase: bool = True,
) -> bool:
    """Detect title-case names and reviewed common lowercase first/last pairs.

    General two-word lowercase prose is not treated as an identity. The common
    pair vocabulary closes the audited ``john smith`` class without turning
    ordinary mortgage phrases into false positives.
    """

    text = _remove_reviewed_non_person_phrases(str(value), allowed_phrases=allowed_phrases)
    text = _LEADING_ANALYTICS_COMMAND_RE.sub(" ", text)
    if include_titlecase and any(
        re.split(r"\s+|\|", match.group(0))[-1].casefold() not in _NON_PERSON_TITLECASE_SUFFIXES
        for match in _TITLECASE_HUMAN_NAME_RE.finditer(text)
    ):
        return True
    if _CONTEXTUAL_HUMAN_NAME_RE.search(text):
        return True
    words = re.findall(r"[A-Za-z]{2,30}", text.casefold())
    return any(
        first in _COMMON_FIRST_NAMES and last in _COMMON_LAST_NAMES
        for first, last in zip(words, words[1:], strict=False)
    )


def contains_mechanical_pii_or_raw_identifier(value: str) -> bool:
    """Detect mechanical PII, unresolved placeholders, and raw source identifiers."""

    text = str(value)
    return any(pattern.search(text) for pattern in _MECHANICAL_PII_OR_RAW_IDENTIFIER_PATTERNS)


def contains_unsafe_ai_text(
    value: str,
    *,
    include_titlecase: bool = True,
    assume_reviewed_read_only_analytics: bool = False,
) -> bool:
    """Shared fail-closed guard for model-authored or model-directed prose.

    ``assume_reviewed_read_only_analytics`` relaxes only the campaign
    audience-formation criterion machine (see
    :func:`contains_protected_class_marketing_text`); it never disables the
    PII, injection, confidential, name-shape, or direct protected-class
    detectors.
    """

    text = str(value)
    return (
        contains_mechanical_pii_or_raw_identifier(text)
        or contains_protected_class_marketing_text(
            text,
            assume_reviewed_read_only_analytics=assume_reviewed_read_only_analytics,
        )
        or contains_prompt_injection_text(text)
        or contains_confidential_or_internal_text(text)
        or contains_human_name_shape(text, include_titlecase=include_titlecase)
    )


def _remove_reviewed_non_person_phrases(
    value: str,
    *,
    allowed_phrases: Sequence[str],
) -> str:
    phrases = {
        *_REVIEWED_NON_PERSON_PHRASES,
        _configured_public_lender_name(),
        *allowed_phrases,
    }
    cleaned = value
    for phrase in sorted((item.strip() for item in phrases if item.strip()), key=len, reverse=True):
        cleaned = re.sub(re.escape(phrase), " ", cleaned, flags=re.IGNORECASE)
    return cleaned


def contains_contextual_human_name(value: str) -> bool:
    """Detect name-shaped text in contexts where a person is being addressed.

    General lowercase two-word prose is intentionally not classified as a
    name. The governed free-text boundaries use this alongside mechanical PII
    checks and title-case detection to catch case-normalized names such as
    ``call john smith`` without treating ordinary sentences as identities.
    """

    return bool(_CONTEXTUAL_HUMAN_NAME_RE.search(str(value)))
