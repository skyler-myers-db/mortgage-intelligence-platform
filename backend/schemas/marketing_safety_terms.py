"""Auditable vocabulary used by protected-class marketing safety checks."""

from __future__ import annotations

import re

from backend.schemas.marketing_selection_criteria import (
    REVIEWED_ATTRIBUTE_PURPOSE_FRAGMENT,
    REVIEWED_MORTGAGE_ATTRIBUTE_LIST_FRAGMENT,
)

_PROTECTED_NATIONAL_ORIGIN_TERMS = frozenset(
    {
        "afghan",
        "albanian",
        "algerian",
        "american",
        "argentine",
        "argentinian",
        "armenian",
        "australian",
        "austrian",
        "bahamian",
        "bangladeshi",
        "barbadian",
        "belgian",
        "belizean",
        "bhutanese",
        "bolivian",
        "bosnian",
        "brazilian",
        "british",
        "bulgarian",
        "burmese",
        "cambodian",
        "cameroonian",
        "canadian",
        "chilean",
        "chinese",
        "colombian",
        "congolese",
        "costa rican",
        "croatian",
        "cuban",
        "czech",
        "danish",
        "dominican",
        "dutch",
        "ecuadorian",
        "egyptian",
        "english",
        "eritrean",
        "ethiopian",
        "filipino",
        "finnish",
        "french",
        "german",
        "ghanaian",
        "greek",
        "guatemalan",
        "guyanese",
        "haitian",
        "hmong",
        "honduran",
        "hungarian",
        "icelandic",
        "indian",
        "indonesian",
        "iranian",
        "iraqi",
        "irish",
        "italian",
        "jamaican",
        "japanese",
        "jordanian",
        "kazakh",
        "kenyan",
        "korean",
        "kyrgyz",
        "lao",
        "laotian",
        "latvian",
        "lebanese",
        "liberian",
        "lithuanian",
        "macedonian",
        "malaysian",
        "mexican",
        "mongolian",
        "moroccan",
        "nepalese",
        "nepali",
        "new zealander",
        "nicaraguan",
        "nigerian",
        "norwegian",
        "pakistani",
        "palestinian",
        "panamanian",
        "paraguayan",
        "peruvian",
        "polish",
        "portuguese",
        "puerto rican",
        "romanian",
        "russian",
        "salvadoran",
        "samoan",
        "saudi",
        "senegalese",
        "serbian",
        "singaporean",
        "slovak",
        "slovenian",
        "somali",
        "south african",
        "spanish",
        "sri lankan",
        "sudanese",
        "swedish",
        "swiss",
        "syrian",
        "taiwanese",
        "thai",
        "tongan",
        "trinidadian",
        "turkish",
        "ugandan",
        "ukrainian",
        "uruguayan",
        "uzbek",
        "venezuelan",
        "vietnamese",
        "yemeni",
        "zimbabwean",
    }
)

PROTECTED_NATIONAL_ORIGIN_RE = re.compile(
    r"\b(?:"
    + "|".join(
        re.escape(term).replace(r"\ ", r"[ -]")
        for term in sorted(_PROTECTED_NATIONAL_ORIGIN_TERMS, key=len, reverse=True)
    )
    + r")s?\b",
    re.IGNORECASE,
)


def _dotted_spelling(term: str) -> str:
    """Return an auditable pattern for a fully separator-obfuscated term."""

    return r"(?:" + r"[.\s_-]+".join(re.escape(letter) for letter in term) + r"\.?)"


# Health is sensitive data, but a bare condition word is not automatically a
# marketing violation (for example, a compliance note may document that it was
# excluded).  The detector below therefore composes these reviewed vocabularies
# with a person/status relationship and, where needed, governed selection
# intent.  Keeping the concepts grouped here makes additions reviewable without
# turning the policy into an unbounded list of diseases.
_PROTECTED_HEALTH_NAMED_CONDITIONS = (
    "addiction",
    "alzheimer's",
    "anxiety",
    "asthma",
    "bipolar disorder",
    "cancer",
    "cerebral palsy",
    "dementia",
    "depression",
    "diabetes",
    # ``psoriasis`` has been in this bank from the start and ``eczema`` was
    # not, so the repo's own canonical health carrier refused as an UNKNOWN
    # criterion rather than as a health term -- the fail-closed net catching it
    # by shape, not the bank recognizing it. Both are common chronic skin
    # conditions and the asymmetry was an omission, not a decision. Adding it
    # moves the reason from ``unreviewed_criterion`` to ``protected_class`` for
    # every eczema carrier; the sibling expectations in
    # ``test_genie_prompt_guard_geography`` were updated with it.
    "eczema",
    "epilepsy",
    "heart disease",
    "high blood pressure",
    "hypertension",
    "kidney disease",
    "kidney failure",
    "leukemia",
    "lupus",
    "mental illness",
    "migraine",
    "multiple sclerosis",
    "parkinson's",
    "psoriasis",
    "renal failure",
    "schizophrenia",
    "stroke",
    "substance use disorder",
)

# Each of these is unambiguous in ordinary mortgage prose, so each matches
# standalone. ``ms`` is deliberately NOT here: it is the one entry that is also
# a USPS state code, and it is handled by ``_TWO_LETTER_MS_RE_FRAGMENT`` below.
_PROTECTED_HEALTH_ABBREVIATIONS = ("adhd", "aids", "als", "copd", "hiv", "ocd")

# ``MS`` is both multiple sclerosis and the USPS code for Mississippi. Matching
# it standalone made every governed per-state rollup that named Mississippi a
# fair-lending finding -- "Borrowers in MS.", "State rollup: 70 MS borrowers
# and 20 LA borrowers." -- on all of the co-pilot, plan, Genie prompt/planner,
# visible-prose and visible-cell surfaces at once, and it kept ``MS`` out of the
# governed place dimension that geography drill-down reads. Mississippi was the
# only state code affected; the other 50 values were already clean.
#
# So the two-letter form is admitted to the bank only beside a health carrier:
# a relationship phrase immediately before it, or a health noun immediately
# after it. That is the same shape several other banks already use, and it
# narrows the TERM rather than exempting the PLACE -- recognizing a place is
# not authority to strip it, and a place exemption here would have had to hold
# on every surface separately.
#
# Place prepositions are deliberately absent from the carrier list: "borrowers
# in MS" is Mississippi. Where a relationship ends in a preposition that is
# ambiguous on its own (``from``, ``by``, ``for``) the whole phrase is listed,
# so that preposition can never admit the term by itself.
#
# Nothing else about multiple sclerosis is relaxed: the spelled-out
# ``multiple sclerosis`` stays in ``_PROTECTED_HEALTH_NAMED_CONDITIONS`` and
# still matches unconditioned, and the dotted evasion (``M.S.``, ``m s``) is
# kept unconditioned below because it has no legitimate geography reading.
_TWO_LETTER_MS_LEADING_CARRIERS: tuple[str, ...] = (
    "affected by",
    "battles",
    "battling",
    "experiencing",
    "facing",
    "had",
    "has",
    "have",
    "manages",
    "managing",
    "prescribed",
    "recovering from",
    "remission from",
    "suffering from",
    "treated for",
    "undergoing",
    # Covers ``diagnosed with``, ``living with``, ``dealing with``,
    # ``coping with``, ``afflicted with`` and ``treated with`` as well.
    "with",
)

_TWO_LETTER_MS_TRAILING_NOUNS: str = (
    r"(?:affected|care|clinics?|diagnos(?:is|es|ed)|drugs?|flares?|flare[- ]ups?|"
    r"history|lesions?|meds?|medications?|nurses?|patients?|progression|related|"
    r"relapses?|remission|specialists?|status|sufferers?|survivors?|symptoms?|"
    r"therap(?:y|ies)|treatments?)"
)


def _two_letter_ms_fragment() -> str:
    """Two-letter ``MS`` -- only next to a health carrier, plus dotted forms.

    Python requires each look-behind to be fixed width, so the carrier list is
    compiled as an alternation OF look-behinds rather than one look-behind of
    an alternation.
    """

    leading = "|".join(
        rf"(?<=\b{re.escape(phrase)}\s)" for phrase in _TWO_LETTER_MS_LEADING_CARRIERS
    )
    return (
        rf"(?:{_dotted_spelling('ms')}|"
        rf"(?:{leading})\bms\b|"
        rf"\bms(?=[-\s]+{_TWO_LETTER_MS_TRAILING_NOUNS}\b))"
    )


_TWO_LETTER_MS_RE_FRAGMENT: str = _two_letter_ms_fragment()

_PROTECTED_HEALTH_TREATMENTS = (
    "cancer treatment",
    "chemotherapy",
    "dialysis",
    "immunotherapy",
    "medical treatment",
    "radiation therapy",
    "rehabilitation",
    "surgery",
)

_PROTECTED_HEALTH_MEDICATIONS = (
    "antidepressant",
    "antipsychotic",
    "beta blocker",
    "blood thinner",
    "insulin",
    "metformin",
    "opioid",
    "statin",
)

_PROTECTED_HEALTH_CLINICAL_MEASUREMENTS = (
    "a1c",
    "blood glucose",
    "blood sugar",
    "cholesterol",
    "glycated hemoglobin",
    "hemoglobin a1c",
    "tumor marker",
)

_PROTECTED_HEALTH_CARE_STATUSES = (
    "hospitalization",
    "hospice",
    "inpatient",
    "organ transplant",
    "palliative care",
)


# The ENUMERATED health vocabulary above, flattened for exact lookup. It
# deliberately excludes the condition-MORPHOLOGY heuristic the detectors
# compose with it (``-algia``, ``-emia``, ``-itis``, ``-oma``, ``-osis``):
# morphology is a spelling family that collides with ordinary proper nouns
# (the gold city ``TACOMA``), while these are reviewed words that name a
# condition. The place-dimension admission gate needs that distinction — a
# gold city named ``CANCER`` must be refused, one named ``TACOMA`` need not be.
_REVIEWED_NAMED_HEALTH_TERMS: frozenset[str] = frozenset(
    term.casefold()
    for group in (
        _PROTECTED_HEALTH_NAMED_CONDITIONS,
        _PROTECTED_HEALTH_ABBREVIATIONS,
        _PROTECTED_HEALTH_TREATMENTS,
        _PROTECTED_HEALTH_MEDICATIONS,
        _PROTECTED_HEALTH_CLINICAL_MEASUREMENTS,
        _PROTECTED_HEALTH_CARE_STATUSES,
    )
    for term in group
)


def reviewed_named_health_terms() -> frozenset[str]:
    """Reviewed, enumerated health vocabulary — no morphology heuristics."""

    return _REVIEWED_NAMED_HEALTH_TERMS


def _phrase_fragment(terms: tuple[str, ...]) -> str:
    """Compile reviewed phrases while accepting ordinary space/dash variants."""

    return "|".join(
        re.escape(term).replace(r"\ ", r"[- ]") for term in sorted(terms, key=len, reverse=True)
    )


def _build_health_trait_fragments(
    *,
    ptsd_re_fragment: str,
    mobility_re_fragment: str,
) -> tuple[str, str, str, str, str]:
    """Build shared condition, treatment, medication, Medicare, and trait fragments."""

    named_conditions = _phrase_fragment(_PROTECTED_HEALTH_NAMED_CONDITIONS)
    condition_abbreviations = "|".join(
        (
            *(
                fragment
                for term in _PROTECTED_HEALTH_ABBREVIATIONS
                for fragment in (re.escape(term), _dotted_spelling(term))
            ),
            # Carries its own carrier requirement, so every composite that
            # embeds a medical condition inherits it without restating it.
            _TWO_LETTER_MS_RE_FRAGMENT,
        )
    )
    dotted_medical_conditions = "|".join(
        _dotted_spelling(term) for term in ("asthma", "asthmatic", "cancer", "diabetes", "diabetic")
    )
    # Morphology and clinical category nouns cover condition families without
    # pretending a static disease-name list can ever be complete. These forms
    # are only protected when bound to a person/population relationship by the
    # caller.
    condition_morphology = (
        r"(?:(?:[a-z][a-z'-]{1,30}\s+){0,2}[a-z][a-z'-]{2,30}"
        r"(?:algia|emia|itis|oma|opathy|osis|philia)|"
        r"(?:[a-z][a-z'-]{1,30}\s+){0,3}(?:disease|disorders?|syndromes?)|"
        r"(?:chronic|persistent|long[- ]term)\s+pain|long[- ]covid|"
        r"(?:sleep\s+)?apnea|(?:hearing|vision|sensory)\s+loss|"
        r"blindness|deafness|infertility|obesity)"
    )
    medical_condition = (
        rf"(?:{dotted_medical_conditions}|{condition_abbreviations}|{named_conditions}|"
        rf"{condition_morphology}|"
        r"heart[- ]attack|diabetic|asthmatic|hiv(?:[- ]positive)?|epileptic|"
        r"terminal[- ]illness(?:es)?|terminally[- ]ill|"
        r"(?:chronic|serious|long[- ]term|terminal|acute)\s+(?:illness(?:es)?|disease|"
        r"conditions?|diagnos(?:is|es)|disorders?|syndromes?)|"
        r"(?:mental|behavioral|psychiatric|neurological|neurodevelopmental|cognitive|"
        r"respiratory|pulmonary|physical|medical|health)\s+"
        r"(?:conditions?|diagnos(?:is|es)|disorders?|illness(?:es)?|issues?|status)|"
        r"(?:addiction|substance[- ]use)\s+recovery|recovering\s+from\s+"
        r"(?:addiction|substance[- ]use))"
    )
    reviewed_treatments = _phrase_fragment(_PROTECTED_HEALTH_TREATMENTS)
    medical_treatment = (
        rf"(?:{reviewed_treatments}|chemo|radiation|health[- ]treatment|"
        r"(?:clinical|psychiatric|neurological|respiratory|addiction|physical|occupational)\s+"
        r"(?:care|therapy|treatment|rehab(?:ilitation)?)|treatments?|therap(?:y|ies)|"
        r"medical\s+procedures?)"
    )
    reviewed_medications = _phrase_fragment(_PROTECTED_HEALTH_MEDICATIONS)
    medication = (
        rf"(?:(?:{reviewed_medications})s?|medications?|prescriptions?|"
        r"prescription\s+(?:drugs?|medications?)|"
        r"prescribed\s+(?:drugs?|medications?)|"
        r"(?:prescription|medication)(?:\s+(?:called|named))?\s+[a-z][a-z-]{2,30}|"
        # Audited prescription-family suffixes cover named medications without
        # pretending to maintain a complete commercial drug-name dictionary.
        r"[a-z][a-z-]{1,24}(?:cillin|cycline|formin|glutide|mab|nib|olol|oxetine|"
        r"prazole|pril|sartan|statin))"
    )
    reviewed_clinical_measurements = _phrase_fragment(_PROTECTED_HEALTH_CLINICAL_MEASUREMENTS)
    clinical_measurement = (
        rf"(?:(?:elevated|high|low|abnormal|current|documented)\s+)?"
        rf"(?:(?:{reviewed_clinical_measurements})"
        r"(?:\s+(?:levels?|readings?|results?|measurements?|status))?|"
        r"(?:laboratory|lab)\s+(?:results?|values?|findings?))"
    )
    reviewed_care_statuses = _phrase_fragment(_PROTECTED_HEALTH_CARE_STATUSES)
    care_status = (
        rf"(?:(?:{reviewed_care_statuses})"
        r"(?:\s+(?:care|enrollment|history|status))?|"
        r"(?:heart|kidney|liver|lung|bone[- ]marrow)\s+transplant"
        r"(?:\s+(?:history|status))?)"
    )
    medicare = rf"(?:medicare|{_dotted_spelling('medicare')})"
    dotted_ptsd = _dotted_spelling("ptsd")
    health_trait = (
        rf"(?:{medical_condition}|{dotted_ptsd}|{ptsd_re_fragment}|{mobility_re_fragment}|"
        rf"disabilit(?:y|ies)|(?:(?:common|chronic|serious|long[- ]term)\s+)?"
        rf"(?:medical|health)\s+(?:conditions?|issues?)|{medical_treatment}|{medication}|"
        rf"{clinical_measurement}|{care_status}|"
        rf"{medicare}(?:\s+(?:coverage|benefits?))?)"
    )
    return medical_condition, medical_treatment, medication, medicare, health_trait


# Remove only the bounded assertion that explicitly says health data is
# excluded from decisioning or retained for compliance. The matcher
# intentionally stops at the reviewed purpose noun instead of swallowing the
# rest of a clause: coordinated text such as ``... excluded from targeting and
# patients were selected`` must leave the unsafe selection available to the
# health detector. This mask runs only before the health-specific patterns;
# other PII, protected-class, and copy validators still inspect the original.
# A safe assertion may follow its data noun only through a closed auxiliary
# link. Never mask arbitrary interior prose: doing so lets an unsafe selection
# relationship hide before a later exclusion claim in the same clause.
_PROTECTED_HEALTH_SAFE_LINK = (
    r"\s+(?:(?:is|are|was|were|remains?|has\s+been|have\s+been|had\s+been|"
    r"must\s+be|should\s+be)\s+)?"
)
_PROTECTED_HEALTH_SAFE_ASSERTION = (
    r"(?:excluded\s+from\s+(?:(?:campaign|audience|marketing|mortgage)\s+)?"
    r"(?:selection|eligibility|targeting|decisioning|criteria)|"
    r"(?:not|never)\s+used\s+(?:to\s+determine|for|in)\s+"
    r"(?:(?:campaign|audience|marketing|mortgage)\s+)?"
    r"(?:selection|eligibility|targeting|decisioning|criteria)|"
    r"documented\s+for\s+(?:compliance|governance|fair[- ]lending)\s+(?:review\s+)?and\s+"
    r"(?:not|never)\s+used\s+(?:for|in)\s+(?:(?:campaign|audience|marketing|mortgage)\s+)?"
    r"(?:selection|eligibility|targeting|decisioning|criteria)|"
    r"retained\s+only\s+for\s+(?:compliance|governance|fair[- ]lending)\s+"
    r"(?:review|audit|documentation))"
)
_PROTECTED_HEALTH_SAFE_PTSD = (
    r"(?:p(?:[.\s_-]*t)(?:[.\s_-]*s)(?:[.\s_-]*d)|post[- ]?traumatic[- ]stress(?:[- ]disorders?)?)"
)
_PROTECTED_HEALTH_SAFE_MOBILITY = (
    r"(?:mobility\s+(?:challenges?|limitations?|impairments?|issues?|needs?|restrictions?)|"
    r"(?:limited|impaired|reduced|restricted)\s+mobility)"
)
*_, _PROTECTED_HEALTH_SAFE_TRAIT = _build_health_trait_fragments(
    ptsd_re_fragment=_PROTECTED_HEALTH_SAFE_PTSD,
    mobility_re_fragment=_PROTECTED_HEALTH_SAFE_MOBILITY,
)
PROTECTED_HEALTH_SAFE_CONTEXT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        rf"\b(?:documentation|information|records?|data)\b"
        rf"{_PROTECTED_HEALTH_SAFE_LINK}"
        rf"{_PROTECTED_HEALTH_SAFE_ASSERTION}\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:health|medical|clinical)\s+"
        rf"(?:status|history|conditions?|diagnoses?|treatment|medication|data|information|records?)\b"
        rf"{_PROTECTED_HEALTH_SAFE_LINK}"
        rf"{_PROTECTED_HEALTH_SAFE_ASSERTION}\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:medicare|p[.\s_-]*t[.\s_-]*s[.\s_-]*d[.\s_-]*|"
        r"post[- ]?traumatic[- ]stress(?:[- ]disorders?)?|"
        r"mobility[- ]challenges?)\s+"
        r"(?:documentation|information|records?|data)\b"
        rf"{_PROTECTED_HEALTH_SAFE_LINK}{_PROTECTED_HEALTH_SAFE_ASSERTION}\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:medicare[- ]covered\s+services?\s+are\s+outside\s+the\s+mortgage\s+review|"
        r"the\s+guide\s+discusses\s+mobility\s+challenges?\s+in\s+home\s+design)\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\bdocument\s+aggregate\s+{_PROTECTED_HEALTH_SAFE_TRAIT}\s+"
        r"(?:counts?|readings?|measurements?|statistics?)\s+for\s+"
        r"(?:compliance|governance|fair[- ]lending)\s+review\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b{_PROTECTED_HEALTH_SAFE_TRAIT}\s+(?:and|or)\s+"
        rf"{_PROTECTED_HEALTH_SAFE_TRAIT}\s+"
        r"(?:documentation|information|records?|data)\b"
        rf"{_PROTECTED_HEALTH_SAFE_LINK}{_PROTECTED_HEALTH_SAFE_ASSERTION}\b",
        re.IGNORECASE,
    ),
    # Named conditions can precede their documentation noun. Bind the health
    # trait directly to that noun and stop at the reviewed assertion; never
    # consume an arbitrary sentence prefix or suffix.
    re.compile(
        rf"\b(?:{_PROTECTED_HEALTH_SAFE_TRAIT}|laboratory|lab)\s+"
        r"(?:(?:clinical|medical|resource)\s+)?(?:documentation|history|information|levels?|readings?|records?|results?|measurements?|data)\b"
        r"(?:\s+for\s+(?:account\s+holders?|members?|people|persons?))?"
        rf"{_PROTECTED_HEALTH_SAFE_LINK}"
        rf"{_PROTECTED_HEALTH_SAFE_ASSERTION}\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bdocumentation\s+for\s+(?:account\s+holders?|members?|people|persons?)\s+"
        rf"(?:with|living\s+with)\s+{_PROTECTED_HEALTH_SAFE_TRAIT}"
        rf"{_PROTECTED_HEALTH_SAFE_LINK}{_PROTECTED_HEALTH_SAFE_ASSERTION}\b",
        re.IGNORECASE,
    ),
    # Negative-use statements are safe only for their exact subject/predicate
    # span. A coordinated continuation therefore remains available to the
    # direct health-term detector.
    re.compile(
        rf"\b(?:(?:{_PROTECTED_HEALTH_SAFE_TRAIT})\s+)?"
        r"(?:eligibility|selection|targeting|decisioning|criteria)\s+"
        r"(?:(?:does|do|did)\s+not\s+(?:depend|hinge|rest)\s+on|"
        r"(?:is|are|was|were)\s+not\s+(?:based|conditioned)\s+on)\s+"
        rf"(?:{_PROTECTED_HEALTH_SAFE_TRAIT}|health(?:\s+(?:status|data|information))?|medical\s+(?:data|information))\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:{_PROTECTED_HEALTH_SAFE_TRAIT})\s+"
        r"(?:(?:is|are|was|were)\s+(?:(?:not|never)\s+|no\s+longer\s+)"
        r"(?:mandatory|required|used)(?:\s+(?:for|in))?|"
        r"(?:does|do|did)\s+not\s+(?:determine|control|affect)|"
        r"(?:no\s+longer|never)\s+(?:determines?|controls?|affects?))\s+"
        r"(?:(?:campaign|audience|marketing|mortgage)\s+)?"
        r"(?:selection|eligibility|targeting|decisioning|criteria)\b",
        re.IGNORECASE,
    ),
)

_PROTECTED_HEALTH_COORDINATED_CONTINUATION_RE = re.compile(
    r"^\s*(?:[,.!?;:–—]\s*)?"
    r"(?:(?:and|but|yet|while|although|though|whereas|however|nevertheless|nonetheless|still|"
    r"even\s+(?:so|then)|despite\s+(?:that|this)|in\s+spite\s+of\s+(?:that|this))\b[,:]?\s*)?"
    r"(?:(?:(?:it|they|this|that)|"
    r"(?:(?:those|these|the|such)\s+)?(?:(?:same|identical|equivalent)\s+)?"
    r"(?:(?:clinical|medical|health|case)\s+)?"
    r"(?:records?|data|information|documents?|details?|files?|charts?|notes?|history|evidence))\s+)?"
    r"(?:(?:(?:is|are|was|were)\s+(?:still\s+)?(?:used|applied)\s+to\s+)|"
    r"(?:(?:still|nevertheless|nonetheless|however|yet)\s+)?)"
    r"(?:selects?|chooses?|picks?|assigns?|lands?|determines?|funnels?|slots?|moves?|makes?|decides?|"
    r"prioritizes?|advances?|promotes?|elevates?|ranks?|brings?|gets?|routes?|places?|admits?|includes?|"
    r"enters?|puts?|dictates?|drives?|influences?|steers?|controls?)\b",
    re.IGNORECASE,
)


def mask_protected_health_safe_contexts(candidate: str) -> str:
    """Remove complete reviewed health-exclusion assertions from a scan copy."""

    for _ in range(16):
        matches = [
            match
            for pattern in PROTECTED_HEALTH_SAFE_CONTEXT_PATTERNS
            if (match := pattern.search(candidate)) is not None
        ]
        if not matches:
            break
        match = max(matches, key=lambda item: item.end() - item.start())
        if _PROTECTED_HEALTH_COORDINATED_CONTINUATION_RE.match(candidate[match.end() :]):
            # Scan-only sentinel: a reviewed exclusion followed by a
            # co-referential selection effect is itself protected targeting,
            # even when the exclusion subject is generic ``medical history``.
            candidate = f"{candidate[: match.start()]} cancer {candidate[match.end() :]}"
        else:
            candidate = f"{candidate[: match.start()]} {candidate[match.end() :]}"
    return candidate


def build_protected_health_marketing_patterns(
    *,
    population_re_fragment: str,
    ptsd_re_fragment: str,
    mobility_re_fragment: str,
) -> tuple[re.Pattern[str], re.Pattern[str]]:
    """Build auditable health-status and governed-selection detectors."""

    medical_condition, medical_treatment, medication, medicare, health_trait = (
        _build_health_trait_fragments(
            ptsd_re_fragment=ptsd_re_fragment,
            mobility_re_fragment=mobility_re_fragment,
        )
    )
    # These relationships are deliberately enumerated. They bind a health
    # condition, treatment, medication, or patient status to a person before
    # the selection-intent grammar below can reject it. Documentation about a
    # condition remains available when it is explicitly excluded from use.
    health_relationship = (
        r"(?:with|who\s+(?:have|experience|face|manage|battle|take|use|"
        r"suffer(?:s|ing)?\s+from|deal(?:s|ing)?\s+with)|"
        r"diagnosed\s+with|living\s+with|experiencing|facing|affected\s+by|"
        r"managing|battling|suffering\s+from|dealing\s+with|afflicted\s+with|"
        r"treated\s+(?:for|with)|"
        r"being\s+treated\s+(?:for|with)|recovering\s+from|undergoing|receiving|"
        r"taking|using|on|in|prescribed)"
    )
    health_population = rf"(?:{population_re_fragment}|patients?)"
    health_bound_population = (
        rf"(?:patients?|{health_population}\s+{health_relationship}\s+(?:a\s+)?{health_trait}|"
        rf"(?:{medical_condition}|{medical_treatment}|{medication})[- ]affected\s+"
        rf"{health_population}|(?:{medical_condition}|{medical_treatment}|{medication})\s+"
        rf"{health_population})"
    )
    # A population selected through an ambiguous relationship ("with X",
    # "dealing with X", and peers) fails closed unless the complete object is
    # a reviewed Module 0 mortgage/property attribute. This is the general
    # policy boundary for newly named health conditions: correctness does not
    # depend on recognizing every disease. The full-object lookahead prevents
    # an allowed prefix such as "high equity" from laundering a coordinated
    # unsafe object such as "high equity and eczema".
    reviewed_attribute_list = REVIEWED_MORTGAGE_ATTRIBUTE_LIST_FRAGMENT
    reviewed_attribute_purpose = REVIEWED_ATTRIBUTE_PURPOSE_FRAGMENT
    selection_decision = (
        r"(?:(?:(?:is|are|was|were)\s+)?(?:the\s+)?(?:intended\s+audience|eligible|"
        r"qualified|targeted|reserved|invited\s+to\s+respond)|qualif(?:y|ies|ied)\s+for|"
        r"(?:is|are|was|were)\s+(?:selected|chosen|picked)(?:\s+"
        r"(?:for|to\s+receive)\s+(?:(?:this|the|an?)\s+)?"
        r"(?:offer|message|invitation|outreach|mortgage\s+reviews?))?|"
        r"(?:can|may|should)\s+(?:get|receive|contact|call|email|text|reply|respond|"
        r"reach\s+out))"
    )
    ambiguous_relationship = (
        r"(?:with|who\s+(?:have|experience|face|manage|battle|take|use|"
        r"suffer(?:s|ing)?\s+from|deal(?:s|ing)?\s+with)|"
        r"living\s+with|experiencing|facing|affected\s+by|managing|battling|"
        r"suffering\s+from|dealing\s+with|afflicted\s+with|diagnosed\s+with|"
        r"whose\s+(?:diagnosis|medical\s+condition|health\s+condition)\s+(?:is|was)|"
        r"having|who\s+reports?|carrying|coping\s+with|"
        r"being\s+treated\s+(?:for|with)|recovering\s+from|undergoing|receiving|"
        r"taking|using|on|in|prescribed)"
    )
    generic_relationship_bound_population = (
        rf"(?:{health_population}\s+{ambiguous_relationship}\s+"
        rf"(?!(?:{reviewed_attribute_list}){reviewed_attribute_purpose}"
        rf"(?=\s*(?:(?:who\s+)?{selection_decision}|"
        rf"(?:contact|call|email|text|message|reply)\b|"
        rf",\s*(?:provided\b|for\s+whom\b)|[.!?;:]|$)))"
        rf"[^.!?;:]{{1,80}}?)"
    )
    governed_health_population = (
        rf"(?:{health_bound_population}|{generic_relationship_bound_population})"
    )
    health_status = re.compile(
        rf"\b(?:{health_bound_population}|"
        rf"{medical_condition}\s+(?:survivors?|patients?|sufferers?|homeowners?|"
        rf"borrowers?|applicants?|customers?|prospects?|clients?|people|persons?|adults?)|"
        rf"{medical_condition}[- ]affected\s+{population_re_fragment}|"
        rf"(?:survivors?|patients?|sufferers?)\s+(?:of|with)\s+{medical_condition}|"
        rf"{population_re_fragment}\s+(?:diagnosed\s+with|living\s+with|recovering\s+from|"
        rf"in\s+remission\s+from)\s+{medical_condition}|"
        rf"{population_re_fragment}\s+(?:undergoing|receiving)\s+{medical_treatment}|"
        rf"{medical_treatment}\s+"
        rf"(?:patients?|recipients?|homeowners?|borrowers?|applicants?|customers?)|"
        rf"{population_re_fragment}\s+(?:diagnosed\s+with|living\s+with|with|"
        rf"who\s+have|experiencing|affected\s+by|recovering\s+from|managing|"
        rf"suffering\s+from|treated\s+for)\s+(?:a\s+)?{ptsd_re_fragment}"
        rf"(?:\s+diagnosis)?|{population_re_fragment}\s+diagnosed\s+as\s+having\s+"
        rf"{ptsd_re_fragment}|{ptsd_re_fragment}(?:[- ](?:affected|diagnosed))?\s+"
        rf"{population_re_fragment}|{ptsd_re_fragment}\s+(?:patients?|survivors?|"
        rf"sufferers?)|(?:patients?|survivors?|sufferers?)\s+(?:of|with)\s+"
        rf"{ptsd_re_fragment}|{medicare}\s+(?:beneficiar(?:y|ies)|recipients?|enrollees?|"
        rf"members?|patients?)|(?:beneficiar(?:y|ies)|recipients?|enrollees?|members?|"
        rf"patients?)\s+(?:of|under|in)\s+{medicare}|{medicare}\s+"
        rf"{population_re_fragment}|{medicare}[- ](?:eligible|covered|"
        rf"enrolled|insured)\s+{population_re_fragment}|{population_re_fragment}\s+"
        rf"(?:receiving|using|on|covered\s+(?:by|under)|enrolled\s+in|eligible\s+for|"
        rf"insured\s+by|dependent\s+on)\s+{medicare}|{population_re_fragment}\s+who\s+"
        rf"(?:receive|use|have|rely\s+on)\s+{medicare}|{population_re_fragment}\s+with\s+"
        rf"{medicare}\s+(?:coverage|benefits?)|(?:\b(?:campaign|offer|promotion|message|"
        rf"invitation|outreach|notice|communication)\b(?:\s+(?:is|was|has\s+been))?\s+"
        rf"(?:for|to|(?:intended|directed|designed)\s+(?:(?:specifically|exclusively|"
        rf"primarily)\s+)?(?:for|to)|aimed\s+at)|(?:\A|[.!?;:])\s*(?:for|to|"
        rf"(?:intended|directed|designed)\s+(?:(?:specifically|exclusively|primarily)"
        rf"\s+)?(?:for|to)|aimed\s+at))[^.!?;:]{{0,80}}(?:{population_re_fragment}\s+"
        rf"(?:with|who\s+(?:have|experience|face|manage)|living\s+with|experiencing|"
        rf"facing|affected\s+by|managing)\s+(?:a\s+)?(?:{medical_condition}|"
        rf"{ptsd_re_fragment}|{mobility_re_fragment})|(?:{medical_condition}|"
        rf"{ptsd_re_fragment}|{mobility_re_fragment})(?:[- ](?:affected|diagnosed))?\s+"
        rf"{population_re_fragment}))\b",
        re.IGNORECASE,
    )
    governance_intent = re.compile(
        rf"\b(?:"
        # Selection or restriction stated before the protected population.
        rf"(?:(?:the\s+)?(?:intended\s+)?audience\s+(?:is|consists\s+of)|eligibility"
        rf"(?:\s+is\s+(?:limited|restricted)\s+to)?|(?:we\s+are\s+)?contacting|"
        rf"(?:we\s+)?(?:chose|choose|picked|pick|selected|select)|target(?:s|ed|ing)?|"
        rf"(?:access|campaign|message|offer|outreach|invitation|promotion|"
        rf"mortgage\s+reviews?)\s+(?:is\s+|are\s+|was\s+|were\s+)?"
        rf"(?:available\s+only\s+to|restricted\s+to|limited\s+to|goes?\s+to|for|directed\s+to|"
        rf"reserved\s+for|targets?))[^.!?;:]{{0,80}}?{governed_health_population}|"
        # Selection or response entitlement stated after the protected population.
        rf"{governed_health_population}[^.!?;:]{{0,50}}?{selection_decision})\b",
        re.IGNORECASE,
    )
    return health_status, governance_intent


def build_protected_health_term_pattern(
    *,
    ptsd_re_fragment: str,
    mobility_re_fragment: str,
) -> re.Pattern[str]:
    """Build the direct sensitive-health term boundary for marketing copy.

    Campaign, approval, and audit text is not an analytical surface. After the
    explicit compliance-only assertions above are masked, a remaining health
    trait is itself enough to fail closed; no guessed causal-verb list is
    required to prove that the term should not reach borrower-facing copy.
    """

    *_, health_trait = _build_health_trait_fragments(
        ptsd_re_fragment=ptsd_re_fragment,
        mobility_re_fragment=mobility_re_fragment,
    )
    # ``treatment`` alone is a normal business word (best treatment/control
    # treatment). Named conditions and phrases such as ``cancer treatment``
    # still match from their unambiguous leading health term.
    return re.compile(rf"\b(?!(?:treatments?)\b)(?:{health_trait})\b", re.IGNORECASE)


# Competitor lender names the product refuses to target by. One vocabulary,
# shared by the Genie prompt guardrail and the growth-agent objective gate so
# the two surfaces refuse identically (2026-08-07 cross-surface audit: the
# lists had diverged — loanDepot refused on the co-pilot but passed on Genie).
UNREVIEWED_LENDER_NAME_RE_FRAGMENT: str = (
    r"(?:wells\s+fargo|chase|bank\s+of\s+america|td\s+bank|rocket\s+mortgage|"
    r"quicken\s+loans|lendingtree|fairway|loan\s*depot|movement\s+mortgage|"
    r"guaranteed\s+rate|united\s+wholesale(?:\s+mortgage)?|uwm)"
)
