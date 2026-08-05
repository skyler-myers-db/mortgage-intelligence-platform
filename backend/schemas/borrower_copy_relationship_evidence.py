"""Closed borrower-copy identity relationships shared by name scanning."""

from __future__ import annotations

import re

_NAME_TOKEN = r"[A-Za-z][A-Za-z'’-]{1,29}"
_MIDDLE_TOKEN = rf"(?:[A-Za-z](?:\.)?|{_NAME_TOKEN})"
_MULTI_IDENTITY = (
    rf"(?P<identity>(?P<first>{_NAME_TOKEN})"
    rf"(?:\s+{_MIDDLE_TOKEN}){{0,6}}\s+(?P<last>{_NAME_TOKEN}))"
)
_LOWERCASE_MULTI_IDENTITY = (
    r"(?P<identity>(?P<first>[a-z][a-z'’-]{1,29})"
    r"(?:\s+(?:[a-z](?:\.)?|[a-z][a-z'’-]{1,29})){0,6}\s+"
    r"(?P<last>[a-z][a-z'’-]{1,29}))"
)
_ARTIFACT = (
    r"(?:(?:inbound|incoming)\s+)?"
    r"(?:repl(?:y|ies)|responses?|messages?|emails?|sms|notices?|"
    r"communications?|correspondence)"
)
_HANDLING = (
    r"(?:handled|read|reviewed|checked|processed|monitored|opened|received|answered|owned|"
    r"routed|relayed|overseen|supervised|prepared)"
)
_ACTIVE_HANDLING = (
    r"(?:handles|reads|reviews|checks|processes|monitors|opens|receives|answers|owns|"
    r"routes|relays|oversees|supervises|prepares)"
)
_MODAL_HANDLING = (
    r"(?:(?:(?:will|would|can|could|may|might|shall|should)\s+|"
    r"(?:is|are|was|were)\s+going\s+to\s+)"
    r"(?:(?:be\s+)?(?:handling|reading|reviewing|checking|processing|monitoring|opening|"
    r"receiving|answering|owning|routing|relaying|overseeing|supervising)|"
    r"(?:handle|read|review|check|process|monitor|open|receive|answer|own|route|relay|"
    r"oversee|supervise)))"
)
_ACTIVE_RELATION = rf"(?:{_ACTIVE_HANDLING}|{_MODAL_HANDLING})"
_PASSIVE_AUXILIARY = (
    r"(?:(?:is|are|was|were|has\s+been|have\s+been)|"
    r"(?:is|are|was|were)\s+(?:being|to\s+be)|"
    r"(?:will|would|shall|should|must|can|could|may|might)\s+be|"
    r"(?:is|are|was|were)\s+going\s+to\s+be)"
)

LOWERCASE_RESPONSE_IDENTITY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        rf"(?=(?i:\b(?:(?:your|the|this)\s+)?{_ARTIFACT}\s+"
        rf"(?:{_PASSIVE_AUXILIARY}\s+)?"
        rf"{_HANDLING}\s+(?:by|via|through)\s+)"
        rf"(?!(?i:(?:a|an|the|our|your|this)\s+)){_MULTI_IDENTITY}\b)"
    ),
    re.compile(
        rf"(?=(?:^|[.!?;:\n]\s*)"
        rf"(?!(?i:(?:a|an|no|nobody|no\s+one|our|the|this|your)\s+))"
        rf"{_MULTI_IDENTITY}\s+"
        rf"(?i:{_ACTIVE_RELATION}\s+"
        rf"(?:(?:your|the|this)\s+)?{_ARTIFACT})\b)"
    ),
    re.compile(
        rf"(?=(?i:\b(?:(?:your|the|this|a|an)\s+)?{_ARTIFACT}\s+"
        rf"(?:gets?\s+)?(?:{_ACTIVE_HANDLING}|{_HANDLING})\s+"
        rf"(?:by|via|through)\s+)"
        rf"(?!(?i:(?:a|an|the|our|your|this)\s+)){_MULTI_IDENTITY}\b)"
    ),
    re.compile(
        rf"(?=(?i:\b(?:(?:your|the|this|a|an)\s+)?{_ARTIFACT}\s+"
        r"(?:comes?|falls?|is|are|was|were)\s+under\s+(?:the\s+)?"
        r"(?:supervision|oversight|authority|control|responsibility)\s+of\s+)"
        rf"(?!(?i:(?:a|an|the|our|your|this)\s+)){_MULTI_IDENTITY}\b)"
    ),
    re.compile(
        rf"(?=(?i:\b(?:(?:your|the|this|a|an)\s+)?{_ARTIFACT}\s+"
        rf"(?:{_PASSIVE_AUXILIARY}\s+)?{_HANDLING}\s+under\s+)"
        rf"(?!(?i:(?:a|an|the|our|your|this)\s+)){_MULTI_IDENTITY}"
        r"(?i:['’]s\s+(?:supervision|oversight|authority|control|responsibility))\b)"
    ),
    re.compile(
        rf"(?=(?:^|[.!?;:\n]\s*)"
        rf"(?!(?i:(?:a|an|no|nobody|no\s+one|our|the|this|your)\s+))"
        rf"{_LOWERCASE_MULTI_IDENTITY}\s+"
        r"(?i:(?:is|are|was|were)\s+(?:due|scheduled|expected|assigned|set)\s+to\s+"
        rf"(?:{_ACTIVE_HANDLING}|handle|read|review|check|process|monitor|open|receive|"
        rf"answer|own|route|relay|oversee|supervise|prepare)\s+"
        rf"(?:(?:your|the|this)\s+)?{_ARTIFACT})\b)"
    ),
    re.compile(
        rf"(?=(?i:\b(?:(?:your|the|this|a|an)\s+)?{_ARTIFACT}\s+"
        r"(?:(?:is|are|was|were|has\s+been|have\s+been)\s+"
        r"(?:entrusted|assigned|committed)\s+to|"
        r"(?:is|are|was|were)\s+(?:in|under)|belong(?:s|ed)?\s+to|"
        r"(?:rest|rests|rested|sit|sits|sat|stay|stays|stayed)\s+with|"
        r"(?:wind|winds|wound|end|ends|ended)\s+up\s+with)\s+)"
        rf"(?!(?i:(?:a|an|the|our|your|this)\s+)){_MULTI_IDENTITY}\b"
        r"(?i:(?:['’]s\s+(?:care|custody|charge|hands))?))"
    ),
    re.compile(
        rf"(?=(?:^|[.!?;:\n]\s*)"
        rf"(?!(?i:(?:a|an|compliance|customer|he|i|it|no|nobody|no\s+one|operations|our|"
        rf"servicing|she|staff|support|team|the|they|this|underwriting|we|you|your)\s+)){_MULTI_IDENTITY}\s+"
        r"(?i:(?:takes?|has\s+taken)\s+care\s+of\s+"
        rf"(?:(?:your|the|this)\s+)?{_ARTIFACT}|"
        r"(?:has|holds|bears)\s+(?:the\s+)?(?:responsibility|care|charge|custody|ownership)\s+"
        rf"(?:for|of)\s+(?:(?:your|the|this)\s+)?{_ARTIFACT}|"
        r"(?:is|was|remains?)\s+(?:responsible|accountable)\s+for\s+"
        rf"(?:(?:your|the|this)\s+)?{_ARTIFACT}|"
        rf"(?:looks?\s+after|manages?)\s+(?:(?:your|the|this)\s+)?{_ARTIFACT}|"
        r"(?:is|was)\s+on\s+(?:reply|response|message|email)\s+duty|"
        r"serves?\s+as\s+(?:the\s+|your\s+)?(?:reply|response|message|email)\s+"
        r"(?:handler|owner|reviewer|reader)|"
        rf"is\s+the\s+person\s+who\s+{_ACTIVE_RELATION}\s+"
        rf"(?:(?:your|the|this)\s+)?{_ARTIFACT}|"
        rf"(?:gets?|accepts?|obtains?)\s+(?:(?:your|the|this)\s+)?{_ARTIFACT}|"
        r"(?:has|have)\s+been\s+(?:tasked|charged)\s+with\s+"
        rf"(?:handling|reading|reviewing|checking|processing|monitoring|opening|receiving|answering)\s+"
        rf"(?:(?:your|the|this)\s+)?{_ARTIFACT})\b)"
    ),
    # Fail closed on an unreviewed identity-shaped subject linked to governed
    # correspondence, regardless of the author's chosen relationship verb.
    # The caller still admits complete organizational noun phrases through
    # its reviewed business-identity grammar.
    re.compile(
        rf"(?=(?:^|[.!?;:\n]\s*)"
        rf"(?!(?i:(?:a|an|after|because|before|borrower|but|compliance|customer|deliver|do|gateway|he|however|i|if|instead|it|no|nobody|no\s+one|operations|our|platform|please|provider|"
        rf"servicing|she|staff|support|team|the|they|this|underwriting|unless|we|when|while|would|you|your)\s+))"
        rf"{_LOWERCASE_MULTI_IDENTITY}\s+"
        rf"(?i:(?:[a-z][a-z'’-]*\s+){{0,6}}(?:(?:your|the|this)\s+)?{_ARTIFACT})\b)"
    ),
    # The inverse relation puts the identity after a bounded preposition slot,
    # for example ``responses are kept by <identity>``. The bridge is closed
    # by clause punctuation and cannot consume a later unrelated sentence.
    re.compile(
        rf"(?=(?i:\b(?:(?:your|the|this|a|an)\s+{_ARTIFACT}|"
        r"(?:(?:inbound|incoming)\s+)?(?:replies|responses|messages|emails|notices|"
        r"communications|correspondence))\s+"
        rf"(?:[a-z][a-z'’-]*\s+){{0,6}}by\s+)"
        rf"(?!(?i:(?:a|an|the|our|your|this)\s+)){_LOWERCASE_MULTI_IDENTITY}\b)"
    ),
    re.compile(
        rf"(?=(?:^|[.!?;:\n]\s*)(?i:(?:a|an|the|our|this)\s+"
        rf"(?:[a-z][a-z'’-]*\s+){{0,4}}(?:(?:for|of)\s+(?:the\s+)?|"
        r"(?:handling|monitoring|overseeing)\s+)"
        rf"{_ARTIFACT}\s+"
        r"(?:is|are|was|were|remains?)\s+)"
        rf"(?!(?i:(?:a|an|the|our|your|this)\s+)){_LOWERCASE_MULTI_IDENTITY}\b)"
    ),
    re.compile(
        rf"(?=(?:^|[.!?;:\n]\s*)(?i:(?:please\s+)?"
        r"(?:ask|tell|have|instruct|appoint|assign|designate|entrust)\s+)"
        rf"{_LOWERCASE_MULTI_IDENTITY}"
        rf"(?i:(?:\s+[a-z][a-z'’-]*){{0,6}}\s+{_ARTIFACT})\b)"
    ),
    re.compile(
        rf"(?=(?:^|[.!?;:\n]\s*)(?i:(?:(?:we|they)\s+"
        rf"(?:[a-z][a-z'’-]*\s+){{0,3}})?(?:(?:our|the|this)\s+)?{_ARTIFACT}\s+"
        r"(?:reports?(?:\s+upward)?|pass(?:es|ed|ing)?|flows?|moves?|travels?|"
        r"go(?:es|ne|ing)?|routes?|relays?)\s+"
        r"(?:to|through|with|under)\s+)"
        rf"(?!(?i:(?:a|an|the|our|your|this)\s+)){_LOWERCASE_MULTI_IDENTITY}\b)"
    ),
    re.compile(
        rf"(?=(?:^|[.!?;:\n]\s*)(?i:(?:we|they|our\s+team|the\s+team)\s+"
        rf"(?:entrust|route|send|forward|assign)\s+(?:(?:our|the|this)\s+)?{_ARTIFACT}\s+"
        r"(?:to|through|with|under)\s+)"
        rf"(?!(?i:(?:a|an|the|our|your|this)\s+)){_LOWERCASE_MULTI_IDENTITY}\b)"
    ),
    re.compile(
        r"(?=(?:^|[.!?;:\n]\s*)(?i:(?:we(?:['’]ll|\s+will)?|they(?:['’]ll|\s+will)?|"
        r"our\s+team|the\s+team)\s+(?:send|route|forward|relay|deliver)\s+"
        r"(?:what|whatever|anything|everything)\s+you\s+(?:write|send|submit)\s+"
        r"(?:to|through|with|under)\s+)"
        rf"(?!(?i:(?:a|an|the|our|your|this)\s+)){_LOWERCASE_MULTI_IDENTITY}\b)"
    ),
    re.compile(
        rf"(?=(?:^|[.!?;:\n]\s*)(?i:(?:a|an|the|our|this)\s+"
        r"(?:reply|response|message|email)\s+"
        r"(?:steward|custodian|owner|handler|reviewer|monitor|liaison|coordinator)\s+"
        r"(?:is|was|remains?)\s+)"
        rf"(?!(?i:(?:a|an|the|our|your|this)\s+)){_LOWERCASE_MULTI_IDENTITY}\b)"
    ),
    re.compile(
        rf"(?=(?:^|[.!?;:\n]\s*)(?i:{_ARTIFACT}\s+"
        r"(?:accountability|ownership|stewardship|custody|oversight|responsibility|escalation)\s+"
        r"(?:(?:(?:is|was|has\s+been|had\s+been)\s+)?"
        r"(?:assigned|delegated|entrusted|allocated)\s+|"
        r"(?:points?|belongs?|flows?|passes?|routes?|lies?|rests?|sits?|remains?|goes?)\s+)"
        r"(?:to|through|with)\s+)"
        rf"(?!(?i:(?:a|an|the|our|your|this)\s+)){_LOWERCASE_MULTI_IDENTITY}\b)"
    ),
    re.compile(
        rf"(?=(?:^|[.!?;:\n]\s*)(?i:(?:a|an|the|our|this)\s+"
        r"(?:inbox|mailbox|queue|channel|portal|address)\s+"
        r"(?:point\s+person|contact\s+person|coordinator|liaison)\s+(?:is|was|remains?)\s+)"
        rf"(?!(?i:(?:a|an|the|our|your|this)\s+)){_LOWERCASE_MULTI_IDENTITY}\b)"
    ),
    re.compile(
        rf"(?=(?:^|[.!?;:\n]\s*)(?i:(?:a|an|the|our|this)\s+"
        rf"(?:reply|response|message|email)\s+(?:steward|custodian|owner|handler|reviewer|"
        r"monitor|liaison|coordinator)\s*,\s*)"
        rf"{_LOWERCASE_MULTI_IDENTITY}"
        rf"(?i:\s*,\s*(?:{_ACTIVE_RELATION})\s+(?:(?:your|the|this)\s+)?{_ARTIFACT})\b)"
    ),
    re.compile(
        rf"(?=(?:^|[.!?;:\n]\s*){_LOWERCASE_MULTI_IDENTITY}\s+"
        r"(?i:(?:is|was|remains?)\s+(?:our|the|this|your)\s+"
        r"(?:inbox|mailbox|queue|channel|portal|address|reply|response|message|email)\s+"
        r"(?:point\s+person|contact\s+person|coordinator|liaison|steward|custodian|owner|"
        r"handler|reviewer|monitor))\b)"
    ),
)

SINGLE_TOKEN_RESPONSE_IDENTITY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        rf"(?=(?i:\b(?:(?:your|the|this)\s+)?{_ARTIFACT}\s+"
        rf"(?:{_PASSIVE_AUXILIARY}\s+)?"
        rf"{_HANDLING}\s+(?:by|via|through)\s+)(?P<identity>{_NAME_TOKEN})\b"
        rf"(?=\s*(?:[.!?;,\n]|$)))"
    ),
    re.compile(
        rf"(?=(?:^|[.!?;:\n]\s*)(?!(?i:(?:no|nobody)\b))"
        rf"(?P<identity>{_NAME_TOKEN})\s+"
        rf"(?i:{_ACTIVE_RELATION}\s+"
        rf"(?:(?:your|the|this)\s+)?{_ARTIFACT})\b)"
    ),
    re.compile(
        rf"(?=(?i:\b(?:(?:your|the|this|a|an)\s+)?{_ARTIFACT}\s+"
        rf"(?:gets?\s+)?(?:{_ACTIVE_HANDLING}|{_HANDLING})\s+"
        rf"(?:by|via|through)\s+)(?P<identity>{_NAME_TOKEN})\b"
        rf"(?=\s*(?:[.!?;,\n]|$)))"
    ),
    re.compile(
        rf"(?=(?i:\b(?:(?:your|the|this|a|an)\s+)?{_ARTIFACT}\s+"
        r"(?:comes?|falls?|is|are|was|were)\s+under\s+(?:the\s+)?"
        r"(?:supervision|oversight|authority|control|responsibility)\s+of\s+)"
        rf"(?P<identity>{_NAME_TOKEN})\b(?=\s*(?:[.!?;,\n]|$)))"
    ),
)
