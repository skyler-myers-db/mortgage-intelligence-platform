"""Reviewed protected-class marketing pattern bank.

This module holds only the governed vocabulary regexes so auditors can review
the protected-class, health, and proxy term lists in one place. Detection
logic (scan-variant folding, windowed matching, criterion state) lives in
``backend.schemas._validators_protected_class``.
"""

from __future__ import annotations

import re

from backend.schemas.marketing_safety_terms import (
    build_protected_health_marketing_patterns,
    build_protected_health_term_pattern,
)
from backend.schemas.marketing_selection_criteria import build_selection_context_pattern

PROTECTED_CLASS_MARKETING_RE = re.compile(
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

PROTECTED_AGE_CITIZENSHIP_MARKETING_RE = re.compile(
    r"\b(?:baby\s+boomers?|boomers?|gen(?:eration)?\s*[xyz]|millennials?|retirees?|"
    r"retired\s+(?:homeowners?|borrowers?|households?|couples?|residents?|owners?|people|persons?)|"
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
PROTECTED_POPULATION_RE_FRAGMENT = (
    r"(?:people|persons?|individuals?|adults?|residents?|households?|"
    r"homeowners?|borrowers?|applicants?|customers?|"
    r"prospects?|clients?|mortgage\s+holders?|loan\s+holders?|mortgagors?|"
    r"account\s+holders?|members?|leads?|candidates?|recipients?|consumers?|participants?)"
)
PROTECTED_PTSD_RE_FRAGMENT = (
    r"(?:p(?:[.\s_-]*t)(?:[.\s_-]*s)(?:[.\s_-]*d)|"
    r"post[- ]?traumatic[- ]stress(?:[- ]disorders?)?)"
)
PROTECTED_MOBILITY_STATUS_RE_FRAGMENT = (
    r"(?:mobility\s+(?:challenges?|limitations?|impairments?|issues?|needs?|restrictions?)|"
    r"(?:limited|impaired|reduced|restricted)\s+mobility)"
)
PROTECTED_CONTEXTUAL_TRAIT_MARKETING_RE = re.compile(
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
    rf"{PROTECTED_POPULATION_RE_FRAGMENT}\s+(?:joined\s+in\s+(?:a\s+)?civil\s+partnership|due\s+to\s+give\s+birth|expecting\s+(?:their\s+)?first\s+child|welcoming\s+(?:a\s+)?bab(?:y|ies)|active\s+in\s+(?:their\s+|a\s+)?(?:congregation|parish|church|mosque|synagogue|temple)|(?:who\s+)?(?:observ(?:e|es|ed|ing)|practic(?:e|es|ed|ing)|keep(?:s|ing)?|kept|celebrat(?:e|es|ed|ing))\s+(?:their\s+faith|ramadan|passover|easter|lent|the\s+sabbath)|(?:who\s+)?attend(?:s|ed|ing)?\s+(?:mass|worship|religious\s+services?|church|mosque|synagogue|temple)|(?:who\s+)?worship(?:s|ped|ping)?\s+(?:on\s+)?(?:sundays?|the\s+sabbath))|"
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
    rf"mobility[- ](?:challenged|limited|impaired)\s+{PROTECTED_POPULATION_RE_FRAGMENT}|"
    rf"mobility[- ](?:challenge|limitation|impairment)[- ]affected\s+{PROTECTED_POPULATION_RE_FRAGMENT}|"
    rf"(?:limited|impaired|reduced|restricted)[- ]mobility\s+{PROTECTED_POPULATION_RE_FRAGMENT}|"
    rf"{PROTECTED_POPULATION_RE_FRAGMENT}\s+"
    rf"(?:with|who\s+have|facing|experiencing|living\s+with|affected\s+by|managing)\s+"
    rf"{PROTECTED_MOBILITY_STATUS_RE_FRAGMENT}|"
    rf"{PROTECTED_POPULATION_RE_FRAGMENT}\s+whose\s+mobility\s+is\s+(?:challenged|limited|impaired|reduced|restricted)|"
    rf"{PROTECTED_POPULATION_RE_FRAGMENT}\s+(?:requiring|using|dependent\s+on)\s+mobility\s+aids?|"
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
    PROTECTED_HEALTH_STATUS_MARKETING_RE,
    PROTECTED_HEALTH_GOVERNANCE_INTENT_RE,
) = build_protected_health_marketing_patterns(
    population_re_fragment=PROTECTED_POPULATION_RE_FRAGMENT,
    ptsd_re_fragment=PROTECTED_PTSD_RE_FRAGMENT,
    mobility_re_fragment=PROTECTED_MOBILITY_STATUS_RE_FRAGMENT,
)
PROTECTED_HEALTH_TERM_MARKETING_RE = build_protected_health_term_pattern(
    ptsd_re_fragment=PROTECTED_PTSD_RE_FRAGMENT,
    mobility_re_fragment=PROTECTED_MOBILITY_STATUS_RE_FRAGMENT,
)
PROTECTED_HEALTH_SELECTION_CONTEXT_RE = build_selection_context_pattern(
    population_re_fragment=PROTECTED_POPULATION_RE_FRAGMENT
)

PROTECTED_CLASS_SAFE_CONTEXT_PATTERNS: tuple[re.Pattern[str], ...] = (
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

PROTECTED_CLASS_PROXY_PATTERNS: tuple[re.Pattern[str], ...] = (
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
PROTECTED_CLASS_PROXY_SAFE_CONTEXT_PATTERNS: tuple[re.Pattern[str], ...] = (
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
PROTECTED_CLASS_PROXY_HARD_TARGETING_RE = re.compile(
    r"\b(?:target|targeting|prioritize|rank|score|segment|filter|exclude|select|"
    r"redline|steer|solicit|prospect|market to|advertise to|campaign to|outreach to|"
    r"contact)\b",
    re.IGNORECASE,
)
PROTECTED_CLASS_PROXY_POPULATION_RE = re.compile(
    r"\b(?:applicants?|borrowers?|communities|community|customers?|households?|"
    r"homeowners?|leads?|neighbou?rhoods?|postal codes?|prospects?|recipients?|"
    r"residents?|tracts?|zip codes?|zips?)\b",
    re.IGNORECASE,
)
