"""Structural protected religion and familial-status marketing relations."""

from __future__ import annotations

import re

_POPULATION = (
    r"(?:people|persons?|individuals?|adults?|residents?|households?|homeowners?|"
    r"borrowers?|applicants?|customers?|prospects?|clients?|mortgage\s+holders?|"
    r"loan\s+holders?|mortgagors?|account\s+holders?|members?|leads?|candidates?|"
    r"recipients?|consumers?|participants?)"
)
_RELIGIOUS_OBSERVANCE = (
    r"(?:ramadan|passover|eid(?:\s+al[- ](?:fitr|adha))?|easter|christmas|lent|"
    r"yom\s+kippur|rosh\s+hashanah|hanukkah|purim|sukkot|diwali|holi|vesak|"
    r"the\s+sabbath)"
)
_RELIGIOUS_DESTINATION = (
    r"(?:(?:sunday\s+)?mass|worship|religious\s+services?|church|mosque|"
    r"synagogue|temple|congregation|parish)"
)

PROTECTED_RELIGION_FAMILIAL_RELATION_RE = re.compile(
    rf"\b(?:{_POPULATION}\s+(?:"
    r"(?:await(?:s|ed|ing)?|anticipat(?:e|es|ed|ing))\s+"
    r"(?:(?:their|a)\s+)?(?:childbirth|newborn|child|baby|twins?|triplets?)|"
    r"(?:(?:is|are|was|were)\s+)?expect(?:s|ed|ing)?\s+"
    r"(?:(?:their|a)\s+)?(?:newborn|child|baby|twins?|triplets?)|"
    r"prepar(?:e|es|ed|ing)\s+for\s+(?:(?:a|the)\s+)?(?:new\s+arrival|child|baby)|"
    rf"(?:who\s+)?(?:go|goes|went|going)\s+to\s+{_RELIGIOUS_DESTINATION}|"
    rf"(?:who\s+)?(?:head|heads|headed|heading)\s+to\s+{_RELIGIOUS_DESTINATION}|"
    rf"(?:who\s+)?(?:attend|attends|attended|attending)\s+{_RELIGIOUS_DESTINATION}|"
    rf"(?:who\s+)?(?:are\s+|were\s+)?at\s+{_RELIGIOUS_DESTINATION}|"
    rf"(?:who\s+)?(?:observ(?:e|es|ed|ing)|celebrat(?:e|es|ed|ing)|keep(?:s|ing)?|kept)\s+{_RELIGIOUS_OBSERVANCE})|"
    rf"{_RELIGIOUS_OBSERVANCE}\s+{_POPULATION})\b",
    re.IGNORECASE,
)
