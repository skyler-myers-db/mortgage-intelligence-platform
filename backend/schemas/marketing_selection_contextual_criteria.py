"""Contextual and self-contained criterion patterns for the selection policy.

Split out of ``marketing_selection_criteria`` when that module crossed the
900-line gate again (#227 carved the reviewed workflow grammars for the same
reason; #228 and the population-quantifier fix pushed it back over). Pure
pattern definitions and the two tuples the clause machine iterates: no
decisions, no state. The coreference fragments live here because every
pattern in this family is built from them; the criteria module imports the
few it embeds in its own grammars.
"""

from __future__ import annotations

import re

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
