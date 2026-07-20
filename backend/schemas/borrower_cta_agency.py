"""Borrower-agency grammar for governed marketing calls to action."""

import re

from backend.schemas._validators import configured_public_lender_name

_STRONG_CLAUSE_BOUNDARY_RE = re.compile(r"[.!?;]+")
_BORROWER_SUBJECT_RE_FRAGMENT = (
    r"(?:you|borrowers?|mortgage\s+holders?|homeowners?|applicants?|clients?|"
    r"recipients?|customers?)"
)
_CTA_ADVERB_RE_FRAGMENT = (
    r"(?:also|then|quickly|warmly|always|promptly|first|securely|directly|today|now|still|freely|optionally|"
    r"personally|confidentially|online|instead|easily|privately|immediately|later|anytime|"
    r"any\s+time|safely|simply)"
)
_BORROWER_PARENTHETICAL_RE_FRAGMENT = (
    r"(?:at\s+any\s+time|at\s+your\s+convenience|when\s+ready|if\s+desired|"
    r"if\s+interested|if\s+you\s+wish|when\s+convenient|today|now)"
)
_CTA_ADVERB_SEQUENCE_RE_FRAGMENT = (
    rf"{_CTA_ADVERB_RE_FRAGMENT}(?:\s+(?:and\s+)?{_CTA_ADVERB_RE_FRAGMENT})?"
)
_INVITATION_ADVERB_RE_FRAGMENT = rf"(?:{_CTA_ADVERB_RE_FRAGMENT}|[A-Za-z]+ly)"
_DIRECT_VOCATIVE_RE = re.compile(
    r"\s*(?P<vocative>(?:the\s+)?[A-Za-z][A-Za-z'-]*"
    r"(?:\s+[A-Za-z][A-Za-z'-]*){0,10})\s*,\s*"
    r"(?:(?:please|kindly|alternatively)\s*)?\Z",
    re.IGNORECASE,
)
_BORROWER_VOCATIVE_RE = re.compile(
    rf"(?:(?:dear|interested|mortgage|current|valued|prospective|"
    rf"existing(?:\s+mortgage)?)\s+)?"
    rf"{_BORROWER_SUBJECT_RE_FRAGMENT}"
    rf"(?:\s+with\s+questions)?\Z",
    re.IGNORECASE,
)
_THIRD_PARTY_ROLE_RE_FRAGMENT = (
    r"(?:loan\s+officers?|brokers?|servicing\s+(?:teams?|staff|representatives?)|"
    r"mortgage\s+consultants?|lenders?|advisors?|staff|agents?|our\s+team|"
    r"the\s+system|automated\s+notices?)"
)
_THIRD_PARTY_AUDIENCE_RE = re.compile(
    rf"(?:\A|[.!?;])\s*(?:for\s+)?{_THIRD_PARTY_ROLE_RE_FRAGMENT}\s*[,：:;.!?–—]",
    re.IGNORECASE,
)
_THIRD_PARTY_REPORTING_RE = re.compile(
    rf"\b{_THIRD_PARTY_ROLE_RE_FRAGMENT}\b[^.!?;:–—]{{0,100}}\b"
    r"(?:say|says|said|report|reports|reported|tell|tells|told|encourage|encourages|"
    r"recommend|recommends|advise|advises|claim|claims|believe|believes|think|thinks|"
    r"mention|mentions|mentioned|note|notes|noted|display|displays|displayed|"
    r"confirm|confirms|confirmed|state|states|stated|indicate|indicates|indicated|"
    r"announce|announces|announced|declare|declares|declared|explain|explains|explained)\b",
    re.IGNORECASE,
)
_THIRD_PARTY_SUFFIX_ATTRIBUTION_RE = re.compile(
    rf"(?:\b(?:according\s+to|per|as\s+(?:recommended|advised|explained)\s+by)\s+"
    rf"(?:an?\s+|the\s+)?{_THIRD_PARTY_ROLE_RE_FRAGMENT}\b|"
    rf"\b(?:an?\s+|the\s+)?{_THIRD_PARTY_ROLE_RE_FRAGMENT}\s+"
    r"(?:say|says|said|recommend|recommends|recommended|advise|advises|advised|"
    r"explain|explains|explained|report|reports|reported|state|states|stated)\b)",
    re.IGNORECASE,
)
_LENDER_NARRATIVE_DIRECTIVE_RE = re.compile(
    r"\s*(?:we|our\s+team)\s+(?:will|may|might|can|could|have|has|are|is|send|sent|"
    r"provide|provided|share|shared)\b[^.!?;:–—]{0,120},\s*"
    r"(?:(?:please|kindly)\s*)?\Z",
    re.IGNORECASE,
)
_LOCAL_IMPERATIVE_PREFIX_RE = re.compile(
    r"\s*(?:(?:(?:and|but)\s+)?(?:instead|alternatively|however)\s*,?\s*"
    r"(?:please\s+)?|(?:and|but)\s+(?:please\s+)?|(?:please|kindly)\s+)\Z",
    re.IGNORECASE,
)
_NEGATED_REPLACEMENT_PREFIX_RE = re.compile(
    rf"\s*(?:(?:{_BORROWER_SUBJECT_RE_FRAGMENT})\s+)?"
    r"(?:do\s+not|don['’]t|never|cannot|can['’]t|not\s+required)\b"
    r"[^.!?;]{0,100}\b(?:(?:and|but)\s+)?(?:instead|alternatively)\s*,?\s*"
    r"(?:please\s+)?\Z",
    re.IGNORECASE,
)
_DIRECT_BORROWER_PREFIX_RE = re.compile(
    rf"\b{_BORROWER_SUBJECT_RE_FRAGMENT}\s+(?:may|can|could|should|must|might)"
    rf"(?:\s*,\s*{_BORROWER_PARENTHETICAL_RE_FRAGMENT}\s*,\s*|"
    rf"\s+(?:{_CTA_ADVERB_SEQUENCE_RE_FRAGMENT}\s+)?)(?:please\s+)?\Z|"
    rf"\b{_BORROWER_SUBJECT_RE_FRAGMENT}\s+(?:may\s+wish|can\s+choose|choose|want|"
    rf"would\s+like)\s+to\s+(?:{_CTA_ADVERB_RE_FRAGMENT}\s+)?\Z|"
    rf"\b(?:{_BORROWER_SUBJECT_RE_FRAGMENT}\s+are|you['’]re)\s+"
    rf"(?:{_CTA_ADVERB_RE_FRAGMENT}\s+)?(?:able|free|welcome|invited|encouraged)"
    rf"(?:,\s*{_BORROWER_PARENTHETICAL_RE_FRAGMENT}\s*,\s*|\s+)to\s+\Z|"
    rf"\b{_BORROWER_SUBJECT_RE_FRAGMENT}\s+have\s+the\s+option\s+"
    rf"(?:{_CTA_ADVERB_RE_FRAGMENT}\s+)?to\s+\Z|"
    r"\b(?:please\s+)?feel\s+free\s+to\s+\Z",
    re.IGNORECASE,
)
_LENDER_INVITATION_PREFIX_RE = re.compile(
    rf"\s*(?:(?:may|can|could|would)\s+)?"
    rf"(?P<inviter>[A-Za-z][A-Za-z&.'-]*(?:\s+[A-Za-z][A-Za-z&.'-]*){{0,5}}?)\s+"
    rf"(?:{_INVITATION_ADVERB_RE_FRAGMENT}\s+)?"
    rf"(?:(?:invite|invites|encourage|encourages|ask|asks|urge|urges|recommend|"
    rf"recommends|advise|advises|request|requests|welcome|welcomes)\s+"
    rf"(?:that\s+)?{_BORROWER_SUBJECT_RE_FRAGMENT}|"
    rf"are\s+(?:inviting|encouraging|asking|urging|recommending|advising|requesting)\s+"
    rf"(?:that\s+)?{_BORROWER_SUBJECT_RE_FRAGMENT}|"
    rf"(?:would|['’]d)\s+like\s+{_BORROWER_SUBJECT_RE_FRAGMENT})"
    rf"(?:\s+{_CTA_ADVERB_RE_FRAGMENT})?"
    rf"(?:,\s*{_BORROWER_PARENTHETICAL_RE_FRAGMENT}\s*,\s*|\s+)"
    rf"(?:to\s+)?\Z",
    re.IGNORECASE,
)
_LENDER_HOSPITABLE_INVITATION_PREFIX_RE = re.compile(
    rf"\s*(?P<inviter>[A-Za-z][A-Za-z&.'-]*(?:\s+[A-Za-z][A-Za-z&.'-]*){{0,5}}?)\s+"
    rf"(?:would\s+be|are)\s+(?:happy|glad|pleased)\s+for\s+"
    rf"{_BORROWER_SUBJECT_RE_FRAGMENT}\s+to\s+\Z",
    re.IGNORECASE,
)
_LENDER_CONTRACTED_INVITATION_PREFIX_RE = re.compile(
    rf"\s*(?P<inviter>[A-Za-z][A-Za-z&.'-]*(?:\s+[A-Za-z][A-Za-z&.'-]*){{0,5}}?)"
    rf"['’]d\s+like\s+{_BORROWER_SUBJECT_RE_FRAGMENT}\s+"
    rf"(?:{_CTA_ADVERB_RE_FRAGMENT}\s+)?to\s+\Z",
    re.IGNORECASE,
)
_DIRECT_BORROWER_QUESTION_PREFIX_RE = re.compile(
    rf"\s*(?:(?:(?:if\s+(?:so|interested|ready))|when\s+ready)\s*,\s*)?"
    rf"(?:would\s+you\s+be\s+willing\s+to\s+|"
    rf"(?:can|could|will|would)\s+you(?:\s+|(?=,))"
    rf"(?:please(?:\s+|(?=,)))?(?:,\s*{_BORROWER_PARENTHETICAL_RE_FRAGMENT}\s*,\s*|"
    rf"(?:{_CTA_ADVERB_SEQUENCE_RE_FRAGMENT}\s+)?)|"
    rf"are\s+you\s+(?:{_CTA_ADVERB_RE_FRAGMENT}\s+)?ready\s+to\s+)\Z",
    re.IGNORECASE,
)
_DIRECT_BORROWER_MATCH_RE = re.compile(
    rf"\b{_BORROWER_SUBJECT_RE_FRAGMENT}\s+(?:may|can|could|should|must|might)\s+|"
    r"\b(?:would|do)\s+you\b|\bwould\b[^.!?;:–—]{0,60}\breview\b",
    re.IGNORECASE,
)
_SAFE_GOVERNING_AUTONOMY_RE = re.compile(
    rf"\s*{_BORROWER_SUBJECT_RE_FRAGMENT}\b[^.!?;:–—]{{0,100}}\b"
    r"(?:not\s+required|do\s+not\s+have\s+to|don['’]t\s+have\s+to|"
    r"no\s+obligation)\b[^.!?;:–—]{0,100},?\s*(?:but|and|although)\s*\Z",
    re.IGNORECASE,
)
_LEADING_BORROWER_ADJUNCT_RE = re.compile(
    rf"\s*(?:(?:if|when|whenever|once|although|unless)\b[^.!?;:–—]{{0,100}},|"
    rf"(?:today|please|at\s+your\s+convenience|"
    rf"at\s+a\s+time\s+(?:that\s+)?(?:works|is\s+convenient)\s+for\s+you|"
    rf"for\s+(?:help|assistance|(?:more\s+)?information|"
    rf"questions\s+about\s+(?:your|the)\s+(?:mortgage|loan))|"
    rf"to\s+(?:learn\s+more|get\s+started))\s*,|"
    rf"to\s+(?:discuss|review|compare|explore)\s+"
    rf"(?:(?:your|the)\s+)?(?:(?:available|current)\s+)?"
    rf"(?:mortgage\s+)?options\s*,|"
    rf"{_BORROWER_SUBJECT_RE_FRAGMENT}\s*,|"
    rf"for\s+{_BORROWER_SUBJECT_RE_FRAGMENT}\s*[,：:–—]|"
    rf"(?:no\s+(?:response|reply|action)\s+(?:is\s+)?(?:required|needed)|"
    rf"no\s+(?:need|obligation|requirement)\s+to\s+[A-Za-z ]{{1,40}})\s*[,：:–—])"
    r"\s*(?:(?:but|however)\s+)?(?:(?:please|kindly)\s+)?\Z",
    re.IGNORECASE,
)
_LEADING_BORROWER_DIRECTIVE_RE = re.compile(
    r"\s*(?:please\s+)?(?:contact|call|email|text|send|message|write|reply|respond|reach\s+out|schedule|request|start|"
    r"book|arrange|review|compare|explore|discuss|talk|speak)\b"
    r"[^.!?;]{0,140}[：:–—]\s*(?:(?:please|kindly)\s+)?\Z",
    re.IGNORECASE,
)


def is_borrower_directed_cta(value: str, match: re.Match[str]) -> bool:
    """Require imperative or explicit borrower-subject syntax for a CTA."""

    prefix = value[: match.start()]
    suffix = value[match.end() :]
    suffix_boundary = _STRONG_CLAUSE_BOUNDARY_RE.search(suffix)
    suffix_clause = suffix[: suffix_boundary.start()] if suffix_boundary else suffix
    if _THIRD_PARTY_SUFFIX_ATTRIBUTION_RE.search(suffix_clause):
        return False
    if _THIRD_PARTY_AUDIENCE_RE.search(prefix):
        return False
    boundaries = list(_STRONG_CLAUSE_BOUNDARY_RE.finditer(prefix))
    clause_prefix = prefix[boundaries[-1].end() :] if boundaries else prefix
    if re.match(r"[.!?]\s+", match.group(0)):
        return True
    if not clause_prefix.strip():
        return True
    if _LEADING_BORROWER_ADJUNCT_RE.fullmatch(clause_prefix):
        return True
    if _LOCAL_IMPERATIVE_PREFIX_RE.fullmatch(clause_prefix):
        return True
    vocative = _DIRECT_VOCATIVE_RE.fullmatch(clause_prefix)
    if vocative is not None:
        if _BORROWER_VOCATIVE_RE.fullmatch(vocative.group("vocative")) is not None:
            return True
        if not _LENDER_NARRATIVE_DIRECTIVE_RE.fullmatch(clause_prefix):
            return False
    invitation = _LENDER_INVITATION_PREFIX_RE.fullmatch(clause_prefix)
    if invitation is None:
        invitation = _LENDER_CONTRACTED_INVITATION_PREFIX_RE.fullmatch(clause_prefix)
    if invitation is None:
        invitation = _LENDER_HOSPITABLE_INVITATION_PREFIX_RE.fullmatch(clause_prefix)
    if invitation is not None:
        inviter = invitation.group("inviter").casefold()
        return inviter in {"we", "our team", configured_public_lender_name().casefold()}
    if _THIRD_PARTY_REPORTING_RE.search(clause_prefix):
        return False
    if re.search(
        r"\b(?:deny|denies|denied|dispute|disputes|reject|rejects|withhold)\b",
        clause_prefix,
        re.IGNORECASE,
    ):
        return False
    if _DIRECT_BORROWER_MATCH_RE.match(match.group(0)):
        return _SAFE_GOVERNING_AUTONOMY_RE.fullmatch(clause_prefix) is not None
    local_prefix = clause_prefix.rsplit(",", maxsplit=1)[-1]
    return bool(
        _LOCAL_IMPERATIVE_PREFIX_RE.fullmatch(local_prefix)
        or _LOCAL_IMPERATIVE_PREFIX_RE.fullmatch(clause_prefix)
        or _NEGATED_REPLACEMENT_PREFIX_RE.fullmatch(clause_prefix)
        or _DIRECT_BORROWER_PREFIX_RE.search(clause_prefix)
        or _DIRECT_BORROWER_QUESTION_PREFIX_RE.fullmatch(clause_prefix)
        or _LEADING_BORROWER_DIRECTIVE_RE.fullmatch(clause_prefix)
    )
