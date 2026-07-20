"""Context-bound lowercase person-name detection for borrower-facing copy."""

import re

from backend.schemas import borrower_copy_relationship_evidence as relationship_evidence
from backend.schemas._validators import configured_public_lender_name
from backend.schemas.marketing_audience_admission import (
    remove_audience_admission_clauses_for_identity_scan,
)

_PUBLIC_TITLECASE_PHRASE_ALLOWLIST: tuple[str, ...] = (
    "Summit Mortgage",
    "Call consent",
    "Equal Housing",
    "Building Permits",
    "New York",
    "New Jersey",
    "New Mexico",
    "North Carolina",
    "North Dakota",
    "South Carolina",
    "South Dakota",
    "Rhode Island",
    "West Virginia",
    "United States",
)
_LOWERCASE_NAME_TOKEN_RE_FRAGMENT = r"[a-z][a-z'’-]{1,29}"
_LOWERCASE_NAME_MIDDLE_TOKEN_RE_FRAGMENT = rf"(?:[a-z](?:\.)?|{_LOWERCASE_NAME_TOKEN_RE_FRAGMENT})"
_LOWERCASE_MULTI_TOKEN_IDENTITY_RE_FRAGMENT = (
    rf"(?P<identity>(?P<first>{_LOWERCASE_NAME_TOKEN_RE_FRAGMENT})"
    rf"(?:\s+{_LOWERCASE_NAME_MIDDLE_TOKEN_RE_FRAGMENT}){{0,2}}\s+"
    rf"(?P<last>{_LOWERCASE_NAME_TOKEN_RE_FRAGMENT}))"
)
_BORROWER_IDENTITY_ROLE_RE_FRAGMENT = (
    r"(?:recipient|addressee|beneficiary|applicant|account\s+holder|borrower|"
    r"customer|homeowner|lead|prospect)"
)
_BORROWER_FACING_ARTIFACT_RE_FRAGMENT = (
    r"(?:offer|message|email|sms|notice|communication|correspondence|copy|campaign|"
    r"mortgage\s+review)"
)
_NON_IDENTITY_SLOT_STATE_RE_FRAGMENT = (
    r"(?:active|available|configured|disabled|enabled|encrypted|important|needed|"
    r"optional|required|requested|supported|unavailable)"
)
# Explicit correspondence slots deserve a narrower, relationship-first rule
# than the more general borrower-copy prose below.  The prefix is a closed
# grammar of header/role, copy, addressing, and delivery relations; the value
# remains case-sensitive so ordinary title-cased lender and place names keep
# flowing through the existing public-name policy.
_IDENTITY_SLOT_NAME_TOKEN_RE_FRAGMENT = r"[A-Za-z][A-Za-z'’-]{1,29}"
_IDENTITY_SLOT_MIDDLE_TOKEN_RE_FRAGMENT = (
    rf"(?:[A-Za-z](?:\.)?|{_IDENTITY_SLOT_NAME_TOKEN_RE_FRAGMENT})"
)
_LOWERCASE_IDENTITY_SLOT_RE_FRAGMENT = (
    rf"(?P<identity>(?P<first>{_IDENTITY_SLOT_NAME_TOKEN_RE_FRAGMENT})"
    rf"(?:\s+{_IDENTITY_SLOT_MIDDLE_TOKEN_RE_FRAGMENT}){{0,2}}\s+"
    rf"(?P<last>{_IDENTITY_SLOT_NAME_TOKEN_RE_FRAGMENT}))"
)
_LOWERCASE_RELUCTANT_IDENTITY_SLOT_RE_FRAGMENT = (
    rf"(?P<identity>(?P<first>{_IDENTITY_SLOT_NAME_TOKEN_RE_FRAGMENT})"
    rf"(?:\s+{_IDENTITY_SLOT_MIDDLE_TOKEN_RE_FRAGMENT}){{0,2}}?\s+"
    rf"(?P<last>{_IDENTITY_SLOT_NAME_TOKEN_RE_FRAGMENT}))"
)
_LOWERCASE_STRUCTURAL_IDENTITY_SLOT_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Correspondence headers and accountable-party roles are explicit
    # identity slots when joined to a value by a delimiter or copula. Keep the
    # relationship grammar closed: a bare use of ``sender`` or ``contact`` in
    # ordinary prose is not enough to activate person-name detection.
    re.compile(
        rf"(?=(?:^|[.!?;:\n]\s*)(?i:(?:attn|attention|to|(?:the\s+)?"
        rf"(?:sender|author|signer|contact|correspondent|"
        rf"authorized\s+representative|point\s+of\s+contact))\s*"
        rf"(?:[:：\-–—]\s*|(?:is|was|will\s+be|shall\s+be)\s+))"
        rf"(?!(?i:{_NON_IDENTITY_SLOT_STATE_RE_FRAGMENT})\b)"
        rf"{_LOWERCASE_IDENTITY_SLOT_RE_FRAGMENT}\b)"
    ),
    # Header families accept a delimiter or an explicit copula. Regarding and
    # concerning are already relational and therefore also support plain
    # whitespace. This covers generated headers such as ``Subject is ...``
    # without treating arbitrary uses of ``subject`` as an identity slot.
    re.compile(
        rf"(?=(?:^|[.!?;:\n]\s*)(?i:(?:(?:subject|topic|reference|ref|re)"
        rf"(?:\s+line)?\s*(?:[:：\-–—]\s*|(?:is|for)\s+)|"
        rf"(?:regarding|concerning)"
        rf"(?:\s*[:：\-–—]\s*|\s+)))"
        rf"{_LOWERCASE_IDENTITY_SLOT_RE_FRAGMENT}\b)"
    ),
    # A correspondence role is an identity slot whether rendered as a label,
    # a copular phrase, or a compact generated header.
    re.compile(
        rf"(?=(?:^|[.!?;:\n]\s*)(?i:(?:(?:intended|designated|primary|named|"
        rf"notification|message|email|mail|correspondence|delivery|distribution|"
        rf"dispatch)\s+)?(?:recipient|addressee|destination)\s*"
        rf"(?:[:：\-–—]\s*|(?:is\s+)?))"
        rf"{_LOWERCASE_IDENTITY_SLOT_RE_FRAGMENT}\b)"
    ),
    # Copy morphology is bounded by the delivery connector or header
    # delimiter, covering copy/copies/copied and courtesy/carbon variants.
    re.compile(
        rf"(?=(?:^|[.!?;:\n]\s*)(?i:(?:(?:courtesy|carbon|blind|notification)"
        rf"\s+)?(?:copy|copies|copied)\s*(?:(?:to|for)\s+|[:：\-–—]\s*))"
        rf"{_LOWERCASE_IDENTITY_SLOT_RE_FRAGMENT}\b)"
    ),
    # Explicit carbon-copy roles can be rendered as copular slots instead of
    # labels (``CC is ...`` or ``the courtesy copy is for ...``). A bare
    # ``copy is`` is deliberately excluded because it commonly describes a
    # document or system state rather than a correspondence identity.
    re.compile(
        rf"(?=(?:^|[.!?;:\n]\s*)(?i:(?:the\s+)?(?:cc|bcc|"
        rf"(?:courtesy|carbon|blind|notification)\s+(?:copy|copies))\s+"
        rf"(?:is|was|will\s+be|shall\s+be)\s+(?:(?:to|for)\s+)?)"
        rf"(?!(?i:{_NON_IDENTITY_SLOT_STATE_RE_FRAGMENT})\b)"
        rf"{_LOWERCASE_IDENTITY_SLOT_RE_FRAGMENT}\b"
        rf"(?=\s*(?:[.!?;,\n]|$)))"
    ),
    # A direct-object copy command has no connector, so require its imperative
    # marker to distinguish ``Please copy <identity>`` from operational prose
    # such as ``copy is enabled``.
    re.compile(
        rf"(?=(?:^|[.!?;:\n]\s*)(?i:please\s+(?:(?:courtesy|carbon|blind)\s+)?"
        rf"copy\s+){_LOWERCASE_IDENTITY_SLOT_RE_FRAGMENT}\b)"
    ),
    # Addressing/intention and nominal delivery relations.  These are useful
    # for generated prose such as 'addressed to' and 'for delivery to' without
    # accepting a bare preposition as an identity signal.
    re.compile(
        rf"(?=(?:^|[.!?;:\n]\s*)(?i:(?:(?:addressed?|directed|intended|"
        rf"designated|marked|earmarked)\s+(?:to|for)|(?:for\s+)?"
        rf"(?:delivery|distribution|dispatch)\s+(?:to|for))"
        rf"\s*(?:[:：\-–—]\s*)?)"
        rf"{_LOWERCASE_IDENTITY_SLOT_RE_FRAGMENT}\b)"
    ),
    # Imperative/direct transport relations are identity-bearing only when
    # they include the to/for connector.  Artifact-first double-object forms
    # remain covered by the established rules below.
    re.compile(
        rf"(?=(?:^|[.!?;:\n]\s*)(?i:(?:please\s+)?(?:deliver|send|forward|"
        rf"route|dispatch|transmit|mail|email|message|notify)\s+"
        rf"(?:(?:this|the|an?)\s+{_BORROWER_FACING_ARTIFACT_RE_FRAGMENT}\s+)?"
        rf"(?:to|for)\s*(?:[:：\-–—]\s*)?(?:the\s+)?)"
        rf"{_LOWERCASE_IDENTITY_SLOT_RE_FRAGMENT}\b)"
    ),
    # Issuance is another explicit destination relation. Keep the governed
    # connector mandatory so ordinary uses of ``issued`` remain untouched.
    re.compile(
        rf"(?=(?:^|[.!?;:\n]\s*)(?i:(?:issued|made\s+out)\s+(?:to|for)"
        rf"\s*(?:[:：\-–—]\s*)?(?:the\s+)?)"
        rf"{_LOWERCASE_IDENTITY_SLOT_RE_FRAGMENT}\b)"
    ),
    # Agency and destination relations remain identity-bearing even when
    # embedded in a sentence. These markers are complete relationships, so
    # they do not depend on a growing list of preparatory verbs or artifacts.
    re.compile(
        rf"(?=(?i:\b(?:on\s+behalf\s+of|in\s+the\s+name\s+of)\s+)"
        rf"{_LOWERCASE_IDENTITY_SLOT_RE_FRAGMENT}\b)"
    ),
    re.compile(
        rf"(?=(?i:\b(?:payable|deliverable)\s+(?:to|for)\s*"
        rf"(?:[:：\-–—]\s*)?)"
        rf"{_LOWERCASE_IDENTITY_SLOT_RE_FRAGMENT}\b)"
    ),
    # Authorship, signature, and artifact-origin relations bind the following
    # value to a responsible identity. Optional voice/tense morphology keeps
    # the grammar structural without admitting an unbounded list of prose
    # verbs.
    re.compile(
        rf"(?=(?:^|[.!?;:\n]\s*)(?i:(?:(?:this|the|an?)\s+"
        rf"{_BORROWER_FACING_ARTIFACT_RE_FRAGMENT}\s+)?"
        rf"(?:(?:is|are|was|were|has\s+been|have\s+been|will\s+be|would\s+be|"
        rf"shall\s+be|should\s+be|(?:is|are|was|were)\s+going\s+to\s+be)\s+)?"
        rf"(?:signed|authored|prepared|drafted|reviewed|verified|checked|coauthored|composed|written|"
        rf"compiled|produced|created|approved|edited|assembled|presented|proofread|"
        rf"finalized|issued|authorized)\s+by\s+)"
        rf"{_LOWERCASE_IDENTITY_SLOT_RE_FRAGMENT}\b)"
    ),
    re.compile(
        rf"(?=(?i:\b(?:(?:this|the|an?)\s+)?"
        rf"{_BORROWER_FACING_ARTIFACT_RE_FRAGMENT}\s+"
        rf"(?:comes?|came)\s+from\s+)"
        rf"{_LOWERCASE_IDENTITY_SLOT_RE_FRAGMENT}\b)"
    ),
    # Two-way correspondence verbs are identity-bearing when the named party
    # is related to a governed borrower-facing artifact. The full relation is
    # strong enough to detect inside prose, not only at a sentence boundary.
    re.compile(
        rf"(?=(?i:\b(?:correspond|communicate|coordinate|engage)\s+with\s+)"
        rf"{_LOWERCASE_IDENTITY_SLOT_RE_FRAGMENT}\s+"
        rf"(?i:(?:about|regarding|concerning)\s+(?:this|the|an?)\s+"
        rf"{_BORROWER_FACING_ARTIFACT_RE_FRAGMENT})\b)"
    ),
    # Direct correspondence commands bind a named party only when both the
    # governed connector and borrower-facing artifact are present. This
    # excludes generic instructions such as ``write clearly`` while covering
    # equivalent write/reply/reach-out wording as one relation family.
    re.compile(
        rf"(?=(?i:\b(?:write|reply|reach\s+out|get\s+in\s+touch)\s+"
        rf"(?:to|with)\s+)"
        rf"{_LOWERCASE_IDENTITY_SLOT_RE_FRAGMENT}\s+"
        rf"(?i:(?:about|regarding|concerning)\s+(?:this|the|an?)\s+"
        rf"{_BORROWER_FACING_ARTIFACT_RE_FRAGMENT})\b)"
    ),
    # Artifact-first discussion is the inverse of the preceding interaction
    # grammar: the governed content and ``with`` connector establish the
    # identity slot before its value is examined.
    re.compile(
        rf"(?=(?i:\bdiscuss\s+(?:this|the|an?)\s+"
        rf"{_BORROWER_FACING_ARTIFACT_RE_FRAGMENT}\s+with\s+)"
        rf"{_LOWERCASE_IDENTITY_SLOT_RE_FRAGMENT}\b)"
    ),
    # Notification-state and benefit relations remain identity-bearing even
    # without an artifact noun. Their complete predicate is required so bare
    # uses of ``keep`` and ``benefit`` do not become name heuristics.
    re.compile(
        rf"(?=(?i:\b(?:please\s+)?keep\s+)"
        rf"{_LOWERCASE_RELUCTANT_IDENTITY_SLOT_RE_FRAGMENT}\s+"
        rf"(?i:(?:fully\s+)?informed)\b)"
    ),
    re.compile(
        rf"(?=(?i:\bfor\s+(?:the\s+)?benefit\s+of\s+)" rf"{_LOWERCASE_IDENTITY_SLOT_RE_FRAGMENT}\b)"
    ),
    # Artifact-placement commands can put an identity before the governed
    # correspondence noun (``Add <name> to correspondence``). The artifact is
    # mandatory, avoiding a generic ``add``-plus-two-words name heuristic.
    re.compile(
        rf"(?=(?:^|[.!?;:\n]\s*)(?i:(?:please\s+)?(?:add|copy|include)\s+)"
        rf"{_LOWERCASE_IDENTITY_SLOT_RE_FRAGMENT}\s+"
        rf"(?i:(?:to|on|in)\s+(?:the\s+|this\s+|that\s+)?"
        rf"{_BORROWER_FACING_ARTIFACT_RE_FRAGMENT})\b)"
    ),
    re.compile(
        rf"(?=(?i:\b(?:(?:your|the|this)\s+)?"
        rf"(?:reply|response|message|email|sms|notice|communication|correspondence)\s+"
        rf"(?:arrives?|arrived|lands?|landed)\s+(?:with|at)\s+)"
        rf"{_LOWERCASE_IDENTITY_SLOT_RE_FRAGMENT}\b)"
    ),
    *relationship_evidence.LOWERCASE_RESPONSE_IDENTITY_PATTERNS,
)
_SINGLE_TOKEN_STRUCTURAL_IDENTITY_SLOT_PATTERNS: tuple[re.Pattern[str], ...] = (
    # A reply/message transport ending at exactly one destination token is an
    # explicit identity slot. Unknown values fail closed; a short reviewed set
    # of organizational destinations remains valid below.
    re.compile(
        rf"(?=(?i:\b(?:(?:your|the|this)\s+)?"
        rf"(?:reply|response|message|email|sms|notice|communication|correspondence)\s+"
        rf"(?:(?:goes?|went|will\s+go|is\s+(?:sent|routed|forwarded|delivered|addressed)|"
        rf"will\s+be\s+(?:sent|routed|forwarded|delivered|addressed))\s+"
        rf"(?:directly\s+)?to|(?:(?:will|would|can|could)\s+)?"
        rf"(?:reach|reaches|reached)|(?:arrive|arrives|arrived|land|lands|landed)\s+"
        rf"(?:with|at))\s+)"
        rf"(?P<identity>{_IDENTITY_SLOT_NAME_TOKEN_RE_FRAGMENT})\b"
        rf"(?=\s*(?:[.!?;,\n]|$)))"
    ),
    # A direct interaction names one destination token before a governed
    # artifact, so it is as explicit as the transport relation above.
    re.compile(
        rf"(?=(?i:\b(?:reply|write|respond|reach\s+out)\s+(?:to|with)\s+)"
        rf"(?P<identity>{_IDENTITY_SLOT_NAME_TOKEN_RE_FRAGMENT})\s+"
        rf"(?i:(?:about|regarding|concerning)\s+(?:this|the|an?)\s+"
        rf"{_BORROWER_FACING_ARTIFACT_RE_FRAGMENT})\b)"
    ),
    # Inverse delivery syntax still establishes the same identity slot.
    re.compile(
        rf"(?=(?:^|[.!?;:\n]\s*)(?P<identity>"
        rf"{_IDENTITY_SLOT_NAME_TOKEN_RE_FRAGMENT})\s+"
        rf"(?i:(?:(?:will|would|can|could|shall|should)\s+)?"
        rf"(?:receives?|gets?|got)\s+(?:your|the|this)\s+"
        rf"(?:reply|response|message|email|sms|notice|communication|correspondence))\b)"
    ),
    # Single-token attention headers are explicit identity fields even without
    # a multi-token human-name shape.
    re.compile(
        rf"(?=(?:^|[.!?;:\n]\s*)(?i:(?:attn|attention|fao|cc|bcc)\s*"
        rf"(?:[:：\-–—]\s*|\s+))"
        rf"(?P<identity>{_IDENTITY_SLOT_NAME_TOKEN_RE_FRAGMENT})\b"
        rf"(?=\s*(?:[.!?;,\n]|$)))"
    ),
    # Imperative artifact delivery is the active-voice inverse of the first
    # transport relation.
    re.compile(
        rf"(?=(?:^|[.!?;:\n]\s*)(?i:(?:please\s+)?"
        rf"(?:send|route|forward|deliver|address|email|message)\s+"
        rf"(?:this|the|an?)\s+"
        rf"(?:reply|response|message|email|sms|notice|communication|correspondence)\s+"
        rf"(?:directly\s+)?to\s+)"
        rf"(?P<identity>{_IDENTITY_SLOT_NAME_TOKEN_RE_FRAGMENT})\b"
        rf"(?=\s*(?:[.!?;,\n]|$)))"
    ),
    # Double-object delivery binds the one-token destination before the
    # borrower-facing artifact (``Send Jordan this reply``).
    re.compile(
        rf"(?=(?:^|[.!?;:\n]\s*)(?i:(?:please\s+)?"
        rf"(?:send|route|forward|deliver|address|email|message|give)\s+)"
        rf"(?P<identity>{_IDENTITY_SLOT_NAME_TOKEN_RE_FRAGMENT})\s+"
        rf"(?i:(?:this|the|an?)\s+"
        rf"(?:reply|response|message|email|sms|notice|communication|correspondence))\b)"
    ),
    # Authorship/accountability and named preparation destinations are explicit
    # identity relations even when the rendered value has one token.
    re.compile(
        rf"(?=(?:^|[.!?;:\n]\s*)(?i:(?:signed|authored|prepared|drafted|"
        rf"reviewed|verified|checked|coauthored|composed|written|compiled|produced|created|approved|edited|"
        rf"assembled|presented|proofread|finalized|issued|authorized)\s+by\s+)"
        rf"(?P<identity>{_IDENTITY_SLOT_NAME_TOKEN_RE_FRAGMENT})\b"
        rf"(?=\s*(?:[.!?;,\n]|$)))"
    ),
    re.compile(
        rf"(?=(?:^|[.!?;:\n]\s*)(?i:(?:prepared|drafted|composed|written|"
        rf"created|produced)\s+for\s+)"
        rf"(?P<identity>{_IDENTITY_SLOT_NAME_TOKEN_RE_FRAGMENT})\b"
        rf"(?=\s*(?:[.!?;,\n]|$)))"
    ),
    # A one-token ``For`` header is identity-bearing only when punctuation
    # binds it to a governed borrower-facing artifact.
    re.compile(
        rf"(?=(?:^|[.!?;:\n]\s*)(?i:for\s+)"
        rf"(?P<identity>{_IDENTITY_SLOT_NAME_TOKEN_RE_FRAGMENT})\s*"
        rf"(?i:[:：\-–—]\s*(?:this|the|an?)?\s*"
        rf"{_BORROWER_FACING_ARTIFACT_RE_FRAGMENT})\b)"
    ),
    re.compile(
        rf"(?=(?:^|[.!?;:\n]\s*)(?i:(?:to\b\s*[:：\-–—]\s*|"
        rf"(?:the\s+)?(?:(?:message|email|sms|notice|communication|correspondence)\s+)?"
        rf"(?:recipient|addressee|destination)\b"
        rf"(?:\s*[:：\-–—]\s*|\s+is\s+|\s+)))"
        rf"(?P<identity>{_IDENTITY_SLOT_NAME_TOKEN_RE_FRAGMENT})\b"
        rf"(?=\s*(?:[.!?;,\n]|$)))"
    ),
    re.compile(
        rf"(?=(?:^|[.!?;:\n]\s*)(?P<identity>"
        rf"{_IDENTITY_SLOT_NAME_TOKEN_RE_FRAGMENT})\s+"
        rf"(?i:(?:is|was|will\s+be)\s+(?:the\s+)?"
        rf"(?:recipient|addressee|destination))\b)"
    ),
    *relationship_evidence.SINGLE_TOKEN_RESPONSE_IDENTITY_PATTERNS,
)
_SAFE_SINGLE_TOKEN_IDENTITY_SLOT_VALUES: frozenset[str] = frozenset(
    {
        "compliance",
        "me",
        "operations",
        "servicing",
        "staff",
        "support",
        "team",
        "them",
        "underwriting",
        "us",
        "you",
    }
)
_LOWERCASE_CONTEXTUAL_NAME_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Correspondence headers are explicit identity slots even without a
    # borrower-role noun (for example, ``Attn: jane doe`` or ``Cc: jane
    # doe``). Attention-copy headers may use either whitespace or punctuation;
    # generic ``To`` and ``RE`` retain the explicit delimiter requirement.
    re.compile(
        rf"(?=(?:^|[.!?;:\n]\s*)(?i:(?:(?:attn|attention|fao|cc|bcc)"
        rf"(?:\s*[:：\-–—]\s*|\s+)|(?:to|re)\s*[:：\-–—]\s*))"
        rf"{_LOWERCASE_MULTI_TOKEN_IDENTITY_RE_FRAGMENT}\b)"
    ),
    # Addressing phrases serve the same identity-slot purpose without header
    # punctuation. Anchor them at a clause boundary so ordinary uses of
    # prepositions elsewhere in business prose remain outside this rule.
    re.compile(
        rf"(?=(?:^|[.!?;:\n]\s*)(?i:(?:for\s+the\s+attention\s+of|"
        rf"care\s+of|c\s*/\s*o)\s*(?::\s*|\s+))"
        rf"{_LOWERCASE_MULTI_TOKEN_IDENTITY_RE_FRAGMENT}\b)"
    ),
    # Artifact + recipient roles and explicit naming relations are identity
    # slots even when prose omits punctuation (for example, 'offer recipient
    # jane doe' or 'borrower goes by jane doe'). A bare role is intentionally
    # insufficient: ordinary prose such as 'the borrower asked us' must not be
    # shifted into a two-token name candidate.
    re.compile(
        rf"(?=(?i:\b(?:(?:offer|message|email|sms|notice|communication|"
        rf"correspondence|copy|campaign)\s+"
        rf"(?:recipient|addressee|beneficiary)\s+|"
        rf"(?:the\s+)?{_BORROWER_IDENTITY_ROLE_RE_FRAGMENT}\s+(?:"
        rf"(?:name|identity)\s+(?:(?:is|named|called)\s+)?|"
        rf"is\s+(?!(?:opposed|unwilling)\s+to\b)|named\s+|known\s+as\s+|"
        rf"goes\s+by\s+|called\s+)))"
        rf"{_LOWERCASE_MULTI_TOKEN_IDENTITY_RE_FRAGMENT}\b)"
    ),
    # Direct salutations are borrower identity positions, not general prose.
    re.compile(
        rf"(?=(?:^|[.!?;:]\s*)(?i:(?:dear|hello|hi)\s+)"
        rf"{_LOWERCASE_MULTI_TOKEN_IDENTITY_RE_FRAGMENT}\b)"
    ),
    # A role label followed by a colon/dash or explicit naming copula is a
    # borrower identity slot, not ordinary business prose. Keep the name
    # tokens case-sensitive so this complements the general title-case name
    # guard without widening it.
    re.compile(
        rf"(?=(?i:\b(?:the\s+)?{_BORROWER_IDENTITY_ROLE_RE_FRAGMENT}"
        rf"(?:\s+(?:name|identity))?\s*(?::|[-–—]|"
        rf"is\b(?!\s+(?:opposed|unwilling)\s+to\b)|named\b)\s*)"
        rf"{_LOWERCASE_MULTI_TOKEN_IDENTITY_RE_FRAGMENT}\b)"
    ),
    # Double-object delivery syntax places the identity between a delivery
    # action and the governed borrower-facing artifact (for example, 'send
    # <person> this offer'). This is distinct from safe 'send this offer to
    # product review' business-copy syntax.
    re.compile(
        rf"(?=(?:^|[.!?;:]\s*)(?i:(?:please\s+)?(?:send|email|mail|text|message|"
        rf"give|deliver|forward|route|address|show|present|transmit|dispatch|share))\s+"
        rf"{_LOWERCASE_MULTI_TOKEN_IDENTITY_RE_FRAGMENT}\s+"
        rf"(?i:(?:this|the|an?)\s+{_BORROWER_FACING_ARTIFACT_RE_FRAGMENT})\b)"
    ),
    # Contact verbs place the identity before an artifact-bearing connector.
    re.compile(
        rf"(?=(?:^|[.!?;:]\s*)(?i:(?:please\s+)?(?:contact|call|"
        rf"reach(?!\s+out\b)|notify))\s+"
        rf"{_LOWERCASE_MULTI_TOKEN_IDENTITY_RE_FRAGMENT}\s+"
        rf"(?i:(?:with|about|regarding|concerning)\s+(?:this|the|an?)\s+"
        rf"{_BORROWER_FACING_ARTIFACT_RE_FRAGMENT})\b)"
    ),
    # A naming relation that explicitly assigns a borrower/recipient role is
    # identity-bearing. Anchoring the candidate after the relation prevents a
    # safe phrase such as 'names servicing team as the recipient' from being
    # shifted left and interpreted as the candidate 'names servicing team'.
    re.compile(
        rf"(?=(?i:\b(?:names|identifies|lists|labels|marks|designates|describes|"
        rf"recognizes|records|specifies|references|mentions))\s+"
        rf"{_LOWERCASE_MULTI_TOKEN_IDENTITY_RE_FRAGMENT}\s+"
        rf"(?i:as\s+(?:the\s+)?{_BORROWER_IDENTITY_ROLE_RE_FRAGMENT})\b)"
    ),
    # The primary rules are relationship-shaped rather than verb-shaped: a
    # governed borrower-facing artifact plus a to/for connector, or a
    # candidate identity plus its recipient/list/priority role. This catches
    # unseen lead verbs without treating arbitrary two-word prose as a name.
    re.compile(
        r"(?=\b(?i:(?:(?:this|the|an?)\s+)?"
        r"(?:offer|message|email|sms|notice|communication|correspondence|"
        r"mortgage\s+review))\b"
        r"(?:\s+[a-z-]{2,30}){0,3}\s+(?:to|for)\s+"
        r"(?!(?:discuss|review|explore|learn|talk|ask|request)\b)"
        r"(?P<first>[a-z]{2,30})\s+(?:[a-z]\s+)?(?P<last>[a-z]{2,30})\b)"
    ),
    # Relative placement is identity-bearing only when a placement action
    # governs the artifact. A bare transport fact such as ``SMS before staff
    # reads it`` is operational prose, not an addressee relation.
    re.compile(
        r"(?=\b(?i:(?:place|position|put|present|show)\s+"
        r"(?:(?:this|the|an?)\s+)?(?:offer|message|email|sms|notice|communication|"
        r"correspondence|mortgage\s+review)\s+(?:before|in\s+front\s+of))\s+"
        r"(?P<first>[a-z]{2,30})\s+(?:[a-z]\s+)?(?P<last>[a-z]{2,30})\b)"
    ),
    re.compile(
        r"(?=\b(?i:(?:the\s+)?(?:priority|recipient|addressee|beneficiary)"
        r"(?:\s+status)?)\s+"
        r"(?i:(?:is\s+)?(?:to|for|on|as))\s+"
        r"(?P<first>[a-z]{2,30})\s+(?:[a-z]\s+)?(?P<last>[a-z]{2,30})\b)"
    ),
    re.compile(
        r"(?=\b(?P<first>[a-z]{2,30})\s+(?:[a-z]\s+)?(?P<last>[a-z]{2,30})\s+"
        r"(?i:(?:(?:is|was|becomes?|serves?)\s+|as\s+)(?:the\s+)?"
        r"(?:recipient|addressee|beneficiary)|"
        r"(?:the\s+)?(?:recipient|addressee|beneficiary)|"
        r"(?:(?:is|was|has\s+been)\s+)?(?:on|in|into)\s+(?:the\s+)?"
        r"(?:list|queue|cohort|campaign)|"
        r"(?:(?:has|gets?|receives?|is\s+given)\s+)?(?:the\s+)?priority)\b)"
    ),
    re.compile(
        r"(?=(?i:\b(?:prepared\s+for|(?:this\s+|the\s+)?offer\s+belongs\s+to|"
        r"belongs\s+to|assign\s+(?:this|the)\s+offer\s+to|"
        r"send\s+(?:this|the)(?:\s+(?:offer|message|email|notice))?\s+to|"
        r"(?:route|deliver|forward|address)\s+(?:this|the)\s+"
        r"(?:offer|message|email|notice)\s+to|"
        r"(?:this\s+|the\s+)?(?:offer\s+)?(?:is\s+)?earmarked\s+for|"
        r"allocate\s+(?:this|the)\s+offer\s+to|"
        r"(?:personalize|customize|tailor)\s+(?:this|the)\s+"
        r"(?:offer|message|email|notice)\s+(?:for|to)|"
        r"(?:this\s+|the\s+)?(?:offer\s+)?(?:is\s+)?intended\s+for|"
        r"this(?:\s+(?:offer|message|email|notice))?\s+is\s+for|"
        r"(?:focus|center)\s+(?:the\s+(?:offer|campaign)\s+)?on|"
        r"(?:this\s+|the\s+)?(?:offer\s+)?concerns|"
        r"(?:the\s+)?home\s+(?:is\s+)?owned\s+by|"
        r"(?:the\s+)?mortgage\s+(?:is\s+)?belonging\s+to|"
        r"(?:the\s+)?applicant(?:\s+is|:)|"
        r"(?:the\s+)?(?:addressee|beneficiary)(?:\s+is|:)|"
        r"(?:the\s+)?account\s+holder(?:\s+is|:)?))\s+"
        r"(?:(?i:an?|the)\s+)?"
        r"(?P<first>[a-z]{2,30})\s+(?:[a-z]\s+)?(?P<last>[a-z]{2,30})\b)"
    ),
    re.compile(
        r"(?=\b(?i:make|keep)\s+"
        r"(?P<first>[a-z]{2,30})\s+(?:[a-z]\s+)?(?P<last>[a-z]{2,30})\s+"
        r"(?i:(?:as\s+)?the\s+(?:focus|priority|target|recipient)|"
        r"(?:in|on)\s+(?:the\s+)?(?:cohort|campaign|list|queue))\b)"
    ),
    re.compile(
        r"(?=\b(?i:designate|treat)\s+"
        r"(?P<first>[a-z]{2,30})\s+(?:[a-z]\s+)?(?P<last>[a-z]{2,30})\s+"
        r"(?i:(?:as\s+)?(?:the\s+)?(?:recipient|addressee|beneficiary))\b)"
    ),
    re.compile(
        r"(?=\b(?i:put|place)\s+"
        r"(?P<first>[a-z]{2,30})\s+(?:[a-z]\s+)?(?P<last>[a-z]{2,30})\s+"
        r"(?i:on|in)\s+(?:the\s+)?(?:list|queue|cohort|campaign)\b)"
    ),
    re.compile(
        r"(?=\b(?i:give)\s+"
        r"(?P<first>[a-z]{2,30})\s+(?:[a-z]\s+)?(?P<last>[a-z]{2,30})\s+"
        r"(?i:(?:the\s+)?priority|priority\s+status)\b)"
    ),
    re.compile(
        r"(?=\b(?i:prioritize|favor)\s+"
        r"(?P<first>[a-z]{2,30})\s+(?:[a-z]\s+)?(?P<last>[a-z]{2,30})\b)"
    ),
    re.compile(
        r"(?=\b(?P<first>[a-z]{2,30})\s+(?:[a-z]\s+)?(?P<last>[a-z]{2,30})"
        r"['’]s\s+(?i:home|house|property|mortgage|loan|account|offer)\b)"
    ),
    re.compile(
        r"(?=\b(?P<first>[a-z]{2,30})\s+(?:[a-z]\s+)?(?P<last>[a-z]{2,30})\s+"
        r"(?i:is\s+(?:the\s+)?(?:recipient|addressee|beneficiary|applicant|"
        r"account\s+holder|borrower))\b)"
    ),
    re.compile(
        r"(?=\b(?i:review)\s+(?P<first>[a-z]{2,30})\s+(?:[a-z]\s+)?"
        r"(?P<last>[a-z]{2,30})(?:['’]s)?\s+"
        r"(?i:mortgage|loan|account|application|property)\b)"
    ),
    re.compile(
        r"(?=(?i:\bmortgage\s+review\s+for)\s+"
        r"(?P<first>[a-z]{2,30})\s+(?:[a-z]\s+)?(?P<last>[a-z]{2,30})\b)"
    ),
    re.compile(
        r"(?=(?:^|[.!?;:]\s*)(?i:for)\s+"
        r"(?P<first>[a-z]{2,30})\s+(?:[a-z]\s+)?(?P<last>[a-z]{2,30})"
        r"(?=\s*[,：:–—]))"
    ),
)
_SAFE_CONTEXT_PAIRS: frozenset[tuple[str, str]] = frozenset(
    {
        ("approved", "campaign"),
        ("borrower", "review"),
        ("borrower", "segment"),
        ("branch", "manager"),
        ("branch", "review"),
        ("campaign", "queue"),
        ("compliance", "review"),
        ("compliance", "team"),
        ("current", "customer"),
        ("customer", "review"),
        ("customer", "segment"),
        ("daily", "monitoring"),
        ("eligible", "borrower"),
        ("final", "review"),
        ("friendly", "lending"),
        ("governed", "campaign"),
        ("human", "review"),
        ("human", "reviewer"),
        ("loan", "officer"),
        ("loan", "portfolio"),
        ("manual", "review"),
        ("mortgage", "portfolio"),
        ("mortgage", "options"),
        ("product", "catalog"),
        ("product", "review"),
        ("quality", "review"),
        ("refinance", "portfolio"),
        ("review", "queue"),
        ("servicing", "team"),
        ("staff", "review"),
        ("support", "team"),
        ("weekly", "monitoring"),
    }
)
_SAFE_CONTEXT_FIRST_TOKENS: frozenset[str] = frozenset(
    {
        "already",
        "additional",
        "aggregate",
        "all",
        "any",
        "a",
        "an",
        "are",
        "available",
        "be",
        "been",
        "being",
        "branch",
        "borrower",
        "borrowers",
        "campaign",
        "compliance",
        "current",
        "customer",
        "customers",
        "daily",
        "data",
        "eligible",
        "everyone",
        "final",
        "fully",
        "further",
        "get",
        "gets",
        "got",
        "governed",
        "highly",
        "human",
        "high",
        "help",
        "home",
        "homeowners",
        "in",
        "investment",
        "information",
        "is",
        "lender",
        "lending",
        "loan",
        "manual",
        "marketing",
        "message",
        "mortgage",
        "more",
        "not",
        "of",
        "operations",
        "options",
        "people",
        "product",
        "prime",
        "portfolio",
        "quality",
        "qualified",
        "questions",
        "rate",
        "rates",
        "refi",
        "refinance",
        "review",
        "sales",
        "servicing",
        "staffed",
        "support",
        "source",
        "these",
        "that",
        "the",
        "their",
        "this",
        "those",
        "was",
        "were",
        "will",
        "would",
        "our",
        "your",
        "weekly",
    }
)
_RELATION_SYNTAX_TOKENS: frozenset[str] = frozenset({"as", "is", "the", "to"})
_RELATION_SYNTAX_PAIRS: frozenset[tuple[str, str]] = frozenset(
    {
        # These verb continuations can sit immediately before a governed
        # placement relation (``end up in the campaign``) and therefore have
        # the same two-token shape as a lowercase identity. They are syntax,
        # not plausible person-name values.
        ("end", "up"),
        ("find", "themselves"),
        ("make", "it"),
        ("to", "rest"),
        ("wind", "up"),
    }
)

# Explicit correspondence slots are ambiguous when their values are all
# lowercase: ``payment update`` has the same broad token shape as a person
# name. Keep the exception compositional: every modifier and head must belong
# to a reviewed business grammar.
# fmt: off
_SAFE_ORGANIZATIONAL_MODIFIERS: frozenset[str] = frozenset(
    {
        "accounting", "borrower", "branch", "business", "care", "closing", "compliance", "corporate",
        "courteous", "credit", "customer", "service", "dedicated", "experienced", "friendly", "home", "knowledgeable",
        "executive", "finance", "fraud", "governance", "human", "legal", "lending",
        "loan", "local", "marketing", "mortgage", "operations", "origination", "privacy",
        "product", "professional", "qualified", "quality", "regional", "responsive", "review", "risk", "sales", "security", "secure",
        "servicing", "staff", "support", "trained",
        "underwriting",
    }
)
_SAFE_ORGANIZATIONAL_HEADS: frozenset[str] = frozenset(
    {
        "administration", "center", "customer", "committee", "counsel", "department",
        "desk", "group", "manager", "mortgage", "office", "officer", "operator",
        "approver", "associate", "associates", "operations", "queue", "review",
        "professional", "professionals", "representative", "representatives", "reviewer", "servicing", "staff",
        "specialist", "specialists", "support", "team", "unit", "workflow",
        "workflows",
    }
)
_SAFE_MORTGAGE_CONTENT_MODIFIERS: frozenset[str] = frozenset(
    {
        "amortization", "annual", "available", "borrower", "campaign", "closing", "credit",
        "current", "customer", "equity", "escrow", "home", "interest", "lending", "loan", "lock",
        "monthly", "mortgage", "new", "offer", "payment", "prepayment", "product",
        "property", "rate", "rates", "refi", "refinance",
        "servicing",
    }
)
_SAFE_MORTGAGE_CONTENT_HEADS: frozenset[str] = frozenset(
    {
        "analysis", "benefits", "confirmation", "decision", "details", "disclosure",
        "criteria", "estimate", "guidance", "information", "notice", "opportunities", "options",
        "program", "reminder", "request", "requirements", "review", "savings", "schedule", "solutions", "standards",
        "statement", "status", "summary", "terms", "timeline",
        "update",
    }
)
_SAFE_POSSESSIVE_CONTENT_HEADS: frozenset[str] = frozenset(
    {"account", "application", "home", "loan", "mortgage", "offer", "payment", "property"}
)
# fmt: on


def _normalized_identity_tokens(value: str) -> tuple[str, ...]:
    return tuple(token.casefold().removesuffix(".") for token in value.split())


def is_reviewed_business_identity_slot_value(
    value: str,
    *,
    public_values: frozenset[str],
) -> bool:
    """Return whether a lowercase identity-shaped slot is reviewed business copy."""

    normalized = " ".join(_normalized_identity_tokens(value))
    if normalized in public_values:
        return True
    tokens = tuple(normalized.split())
    if len(tokens) < 2:
        return False
    modifiers, head = tokens[:-1], tokens[-1]
    if (
        tokens[0] in {"our", "the", "this", "your"}
        and all(token in _SAFE_MORTGAGE_CONTENT_MODIFIERS for token in tokens[1:-1])
        and head in _SAFE_POSSESSIVE_CONTENT_HEADS
    ):
        return True
    # Strip only a grammatical determiner before the reviewed business phrase.
    business_tokens = tokens[1:] if tokens[0] in {"our", "the"} else tokens
    modifiers, head = business_tokens[:-1], business_tokens[-1]
    if (
        all(token in _SAFE_ORGANIZATIONAL_MODIFIERS for token in modifiers)
        and head in _SAFE_ORGANIZATIONAL_HEADS
    ):
        return True
    return (
        all(token in _SAFE_MORTGAGE_CONTENT_MODIFIERS for token in modifiers)
        and head in _SAFE_MORTGAGE_CONTENT_HEADS
    )


def contains_borrower_copy_contextual_name(value: str) -> bool:
    """Detect lowercase multi-token identities only in borrower-copy relations."""

    value = remove_audience_admission_clauses_for_identity_scan(str(value))
    public_values = frozenset(
        " ".join(_normalized_identity_tokens(phrase))
        for phrase in (*_PUBLIC_TITLECASE_PHRASE_ALLOWLIST, configured_public_lender_name())
        if len(phrase.split()) >= 2
    )
    public_pairs = {
        tuple(public_value.split())
        for public_value in public_values
        if len(public_value.split()) == 2
    }
    for pattern in _SINGLE_TOKEN_STRUCTURAL_IDENTITY_SLOT_PATTERNS:
        for match in pattern.finditer(str(value)):
            identity = match.group("identity").casefold()
            if identity not in _SAFE_SINGLE_TOKEN_IDENTITY_SLOT_VALUES:
                return True
    for pattern in _LOWERCASE_STRUCTURAL_IDENTITY_SLOT_PATTERNS:
        for match in pattern.finditer(str(value)):
            pair = (match.group("first").casefold(), match.group("last").casefold())
            identity = match.groupdict().get("identity") or " ".join(pair)
            if (
                pair not in _SAFE_CONTEXT_PAIRS
                and pair not in public_pairs
                and not is_reviewed_business_identity_slot_value(
                    identity,
                    public_values=public_values,
                )
            ):
                return True

    for pattern in _LOWERCASE_CONTEXTUAL_NAME_PATTERNS:
        for match in pattern.finditer(str(value)):
            pair = (match.group("first").casefold(), match.group("last").casefold())
            identity = match.groupdict().get("identity") or " ".join(pair)
            if (
                pair[1] not in _RELATION_SYNTAX_TOKENS
                and pair not in _RELATION_SYNTAX_PAIRS
                and pair[0] not in _SAFE_CONTEXT_FIRST_TOKENS
                and pair not in _SAFE_CONTEXT_PAIRS
                and not is_reviewed_business_identity_slot_value(
                    identity,
                    public_values=public_values,
                )
            ):
                return True
    return False


def remove_configured_public_lender_phrase(value: str) -> str:
    """Remove only the exact reviewed tenant identity before trait scanning."""

    lender_name = configured_public_lender_name()
    return re.sub(
        rf"(?<!\w){re.escape(lender_name)}(?!\w)",
        " ",
        value,
        flags=re.IGNORECASE,
    )


def remove_allowed_public_titlecase_phrases(value: str) -> str:
    """Remove reviewed business/place names before human-name-shape checks."""

    cleaned = value
    for allowed in (*_PUBLIC_TITLECASE_PHRASE_ALLOWLIST, configured_public_lender_name()):
        cleaned = re.sub(re.escape(allowed), "", cleaned, flags=re.IGNORECASE)
    return cleaned
