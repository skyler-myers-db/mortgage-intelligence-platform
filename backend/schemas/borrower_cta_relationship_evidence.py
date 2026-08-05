"""Additional structural consent and response relationship evidence."""

import re

_SUBJECT = (
    r"(?:(?:the\s+)?(?:borrowers?|customers?|recipients?|clients?|applicants?|"
    r"homeowners?|prospects?)|i|we|you|they|he|she)"
)
_CHANNEL = (
    r"(?:(?:telephone|phone|email|mail|sms|text|message)\s+(?:contact|outreach)|"
    r"telephone\s+calls?|phone\s+calls?|calls?|phone|telephone|emails?|email|mail|"
    r"sms|texts?|texting|calling|emailing|"
    r"messages?|messaging|replies|responses?|contact|communications?|"
    r"correspondence|outreach|telephoning|phoning)"
)
_CHANNEL_ACTION = (
    r"(?:call|phone|telephone|email|text|message|contact|communicate|make\s+(?:phone\s+)?calls?|"
    r"send\s+(?:emails?|texts?|messages?))"
)
_RESPONSE = (
    r"(?:emails?|sms|texts?|messages?|repl(?:y|ies)|responses?|calls?|"
    r"correspondence|communications?|submissions?)"
)
_NEGATIVE_RESPONSE_ACTOR = (
    r"(?:nobody|no\s+one|no\s+employee|no\s+staff(?:\s+member)?|"
    r"no\s+team\s+member|no\s+(?:[a-z][a-z-]{2,24}(?:\s+[a-z][a-z-]{2,24}){0,2}))"
)
_RESPONSE_HANDLING_ACTION = (
    r"(?:answer(?:s|ing)?|see(?:s|ing)?|open(?:s|ing)?|check(?:s|ing)?|"
    r"read(?:s|ing)?|review(?:s|ing)?|handle(?:s|d|ing)?|monitor(?:s|ing)?|"
    r"process(?:es|ing)?|own(?:s|ing)?)"
)

ADDITIONAL_CONSENT_RELATION_EVIDENCE_RE = re.compile(
    rf"\b{_SUBJECT}\s+(?:"
    r"(?:(?:does|do|did)\s+not|doesn['’]t|don['’]t|didn['’]t|no\s+longer)\s+"
    rf"(?:consents?|agrees?)\s+to\s+(?:(?:all|any|further|future|additional)\s+)?{_CHANNEL}|"
    r"(?:(?:has|have|had)\s+)?stopp(?:ed|ing)\s+(?:giving|granting)\s+"
    rf"(?:(?:their|the|our)\s+)?(?:permission|authorization|consent)\s+(?:for|to)\s+{_CHANNEL}|"
    r"(?:(?:has|have|had)\s+)?(?:revok(?:e|es|ed|ing)|rescind(?:s|ed|ing)?|"
    r"withdraw(?:s|n|ing)?|withdrew|relinquish(?:es|ed|ing)?)\s+"
    rf"(?:(?:their|the|our)\s+)?ability\s+to\s+{_CHANNEL_ACTION}|"
    r"(?:(?:will|would|shall|should|can|could)\s+)?"
    rf"(?:permits?|allows?|authorizes?)\s+no\s+(?:(?:more|further|future|additional)\s+)?{_CHANNEL}|"
    r"(?:(?:has|have|had)\s+)?(?:give|gives|gave|given|giving)\s+up\s+"
    rf"(?:(?:their|the)\s+)?(?:permission|authorization|consent)(?:\s+(?:for|to)\s+{_CHANNEL})?|"
    r"(?:asked|told|instructed|directed|ordered)\s+"
    r"(?:(?:us|our\s+team|the\s+team)\s+to\s+|"
    r"(?:(?:that\s+)?(?:we|our\s+team|the\s+team)\s+"
    r"(?:(?:must|should|need\s+to|have\s+to)\s+)?)|to\s+)"
    rf"(?:stop|cease|end|halt|discontinue|quit)\s+(?:(?:all|any|further)\s+)?{_CHANNEL}|"
    r"(?:said|stated|replied|responded)\s+no\s+to\s+"
    rf"(?:being\s+)?(?:contacted|called|emailed|texted|messaged|{_CHANNEL})|"
    r"(?:declin(?:e|es|ed|ing)|refus(?:e|es|ed|ing)|reject(?:s|ed|ing)?)\s+"
    r"(?:(?:being|to\s+be)\s+"
    rf"(?:contacted|called|emailed|texted|messaged)|{_CHANNEL})|"
    r"(?:would\s+)?prefer(?:s|red|ring)?\s+(?:not|never)\s+to\s+be\s+"
    r"(?:contacted|called|emailed|texted|messaged)|"
    r"(?:opted|chose)\s+not\s+to\s+be\s+"
    r"(?:contacted|called|emailed|texted|messaged)|"
    r"turned\s+down\s+(?:being|to\s+be)\s+"
    r"(?:contacted|called|emailed|texted|messaged)|"
    r"(?:asked|told|instructed|directed|ordered)\s+"
    r"(?:(?:us|our\s+team|the\s+team)\s+)?(?:not|never)\s+to\s+"
    r"(?:be\s+)?(?:contacted|called|emailed|texted|messaged)|"
    r"(?:objected|objects?)\s+to\s+(?:being\s+)?"
    rf"(?:contacted|called|emailed|texted|messaged|(?:receiving\s+)?{_CHANNEL})|"
    r"(?:forbid(?:s|ding)?|forbade|forbidden)\s+"
    r"(?:(?:us|our\s+team|the\s+team)\s+to\s+)?"
    rf"(?:(?:all|any|further|future|additional)\s+)?(?:{_CHANNEL}|{_CHANNEL_ACTION}(?:\s+them)?)|"
    r"(?:instructed|directed|ordered)\s+(?:us|our\s+team|the\s+team)\s+against\s+"
    rf"{_CHANNEL}|"
    r"(?:(?:has|have|had)\s+)?(?:prohibit(?:s|ed|ing)?|ban(?:s|ned|ning)?|"
    r"bar(?:s|red|ring)?|disallow(?:s|ed|ing)?|block(?:s|ed|ing)?)\s+"
    rf"(?:(?:all|any|further|future|additional|our|their|your|the)\s+)?{_CHANNEL}|"
    r"(?:(?:has|have|had)\s+not|hasn['’]t|haven['’]t|hadn['’]t)\s+"
    r"(?:permit(?:ted)?|allow(?:ed)?|authoriz(?:e|ed)|approv(?:e|ed))\s+"
    rf"(?:(?:all|any|further|future|additional|our|their|your|the)\s+)?{_CHANNEL}|"
    r"(?:veto(?:es|ed|ing)?|nix(?:es|ed|ing)?)\s+"
    rf"(?:(?:all|any|further|future|additional)\s+)?{_CHANNEL}|"
    r"(?:withheld|denied|refused)\s+(?:(?:their|the|our)\s+)?"
    rf"(?:blessing|right|leave|authority)\s+(?:for|to)\s+{_CHANNEL}|"
    r"(?:(?:has|have|had)\s+)?no\s+(?:blessing|right|leave|authority)\s+to\s+"
    rf"{_CHANNEL_ACTION}(?:\s+(?:us|them|the\s+(?:borrower|customer|homeowner|recipient)))?|"
    r"(?:is|are|was|were)\s+(?:opposed|unwilling)\s+to\s+"
    rf"(?:(?:receiv(?:e|es|ed|ing)\s+)?{_CHANNEL}|{_CHANNEL_ACTION})|"
    r"(?:do|does|did)\s+not\s+have\s+"
    r"(?:(?:the\s+)?(?:borrower|customer|homeowner|recipient)['’]s\s+|"
    r"(?:their|your|the)\s+)?(?:blessing|right|leave)\s+to\s+"
    rf"{_CHANNEL_ACTION}|"
    r"(?:disavowed|renounced)\s+(?:(?:our|their|the)\s+)?"
    rf"(?:blessing|right|leave)\s+to\s+{_CHANNEL_ACTION}|"
    r"(?:asked|told|instructed|directed|ordered)\s+"
    r"(?:(?:us|our\s+team|the\s+team)\s+)?(?:for\s+)?"
    r"no\s+(?:more|further|additional)\s+"
    rf"{_CHANNEL}"
    r")\b",
    re.IGNORECASE,
)

PERFECT_CONSENT_RELATION_EVIDENCE_RE = re.compile(
    rf"\b(?:{_SUBJECT}\s+(?:has|have|had)|you['’]ve)\s+(?:"
    r"(?:withheld|denied|refused)\s+"
    r"(?:(?:their|the|our)\s+)?(?:permission|authorization|consent)\s+"
    rf"(?:for|to)\s+{_CHANNEL}|"
    r"not\s+(?:granted|given|provided)\s+"
    r"(?:(?:their|the|our|any)\s+)?(?:permission|authorization|consent)\s+"
    rf"(?:for|to)\s+{_CHANNEL}|"
    r"(?:asked|requested)\s+(?:not|never)\s+to\s+hear\s+from\s+"
    r"(?:us|our\s+team|the\s+team)(?:\s+again)?)\b",
    re.IGNORECASE,
)

ADDITIONAL_CONSENT_STATE_EVIDENCE_RE = re.compile(
    rf"\b(?:{_SUBJECT}\s+(?:had\s+)?consented\s+to\s+{_CHANNEL}"
    r"[^.!?;:–—]{0,40}\b(?:then\s+|later\s+)?"
    r"(?:cancel(?:s|led|ed)|end(?:s|ed)|withdraw(?:s|n|ing)?|withdrew|revok(?:e|es|ed))\s+"
    r"(?:it|that|the\s+(?:permission|authorization|consent))|"
    rf"{_CHANNEL}\s+(?:lack|lacks|lacked|is\s+without|are\s+without)\s+"
    r"(?:(?:your|their|the|our)\s+)?(?:permission|authorization|consent)|"
    rf"{_SUBJECT}\s+(?:(?:has|have|had)\s+)?(?:refus(?:e|es|ed)|den(?:y|ies|ied))\s+"
    rf"{_CHANNEL}\s+(?:permission|authorization|consent)|"
    rf"(?:(?:your|their|our)\s+){_CHANNEL}\s+(?:permission|authorization|consent)\s+"
    r"(?:ended|expired|lapsed|ceased|was\s+canceled|was\s+cancelled|was\s+annulled)|"
    r"(?:(?:your|their|the|our)\s+)?(?:permission|authorization|consent)\s+"
    rf"(?:for|to)\s+{_CHANNEL}\s+(?:was\s+)?"
    r"(?:ended|expired|lapsed|ceased|canceled|cancelled|annulled)|"
    rf"{_SUBJECT}\s+(?:(?:does|do|did)\s+not|doesn['’]t|don['’]t|didn['’]t)\s+"
    rf"(?:assent|agree|consent)\s+to\s+{_CHANNEL}|"
    rf"{_SUBJECT}\s+(?:has|have|had)\s+yet\s+to\s+"
    rf"(?:assent|agree|consent)\s+to\s+{_CHANNEL}|"
    rf"{_CHANNEL}\s+(?:permission|authorization|consent)\s+"
    r"(?:is|are|was|were|remains?)\s+(?:absent|missing|unavailable|not\s+present)|"
    rf"(?:we|our\s+team|the\s+team)\s+(?:lack|lacks|lacked)\s+"
    r"(?:(?:your|their|the)\s+)?(?:permission|authorization|consent)\s+"
    rf"(?:for|to)\s+{_CHANNEL}|"
    rf"(?:we|our\s+team|the\s+team)\s+(?:(?:has|have|had)\s+not\s+|"
    r"fail(?:s|ed)?\s+to\s+|never\s+)"
    r"(?:obtained|obtain|secured|secure|received|receive)\s+(?:(?:your|their|the)\s+)?"
    rf"(?:permission|authorization|consent)\s+(?:for|to)\s+{_CHANNEL})\b",
    re.IGNORECASE,
)

# Consent state is a relationship among a response channel, a consent noun,
# and an explicit negative state inside one clause. Word order and tense do not
# change that state, so detect the three concepts structurally rather than
# attempting to enumerate every English sentence form.
STRUCTURAL_NEGATIVE_CONSENT_STATE_EVIDENCE_RE = re.compile(
    r"\b(?:"
    r"(?:no|zero|without|lack(?:s|ed|ing)?|withheld|denied|refused|revoked|rescinded|"
    r"withdrawn|yet\s+to|(?:fail(?:s|ed)?\s+to|never\s+)(?:obtain|secure|receive|grant|"
    r"obtained|secured|received|granted|provide|provided)|(?:does|do|did)\s+not\s+"
    r"(?:possess|have|obtain|secure|receive))\b[^.!?;:–—]{0,48}\b"
    rf"(?:permission|authorization|consent)\b[^.!?;:–—]{{0,48}}\b{_CHANNEL}|"
    r"(?:permission|authorization|consent)\b[^.!?;:–—]{0,48}\b"
    r"(?:absent|missing|unavailable|withheld|denied|refused|revoked|rescinded|"
    r"withdrawn|expired|lapsed|ceased|canceled|cancelled|annulled|invalid|void)\b"
    rf"[^.!?;:–—]{{0,48}}\b{_CHANNEL}|"
    rf"{_CHANNEL}\b[^.!?;:–—]{{0,48}}\b(?:permission|authorization|consent)\b"
    r"[^.!?;:–—]{0,24}\b(?:absent|missing|unavailable|withheld|denied|refused|"
    r"revoked|rescinded|withdrawn|expired|lapsed|ceased|canceled|cancelled|annulled|"
    r"invalid|void)|"
    rf"(?:(?:{_SUBJECT}\s+(?:(?:has|have|had)\s+)?|you['’]ve\s+)"
    r"(?:not|never)\s+opted\s+in\s+to|"
    rf"{_SUBJECT}\s+(?:hasn['’]t|haven['’]t|hadn['’]t)\s+opted\s+in\s+to|"
    rf"{_SUBJECT}\s+(?:(?:has|have|had)\s+)?declin(?:e|es|ed|ing)\s+to\s+opt\s+in\s+to)\s+"
    rf"{_CHANNEL}|"
    r"(?:permission|authorization|consent)\s+(?:for|to)\s+"
    rf"{_CHANNEL}\s+(?:is|are|was|were|remains?)\s+(?:invalid|void)"
    r")\b",
    re.IGNORECASE,
)

# Consent claims in free-form borrower copy fail closed unless the whole
# bounded clause is an affirmative form reviewed below. This makes unknown
# negative paraphrases evidence by default instead of depending on a complete
# synonym list.
FAIL_CLOSED_CONSENT_STATE_EVIDENCE_RE = re.compile(
    rf"(?:^|[.!?;:–—]\s*)(?=[^.!?;:–—]{{0,180}}\b{_CHANNEL}\b)"
    r"(?=[^.!?;:–—]{0,180}\b(?:permission|authorization|consent|"
    r"authoriz(?:e|es|ed|ation)|opt(?:ed|ing)?[- ]in|unauthoriz(?:ed|ation)|unpermitted|"
    r"go[- ]ahead|green[- ]light|clearance|approv(?:e|es|ed|al)|ok(?:ay)?|"
    r"agree(?:s|d|ment)?|assent|allow(?:s|ed|ance)?|acquiescen(?:ce|t)|leave|"
    r"(?:say|said)\s+yes)\b)"
    r"[^.!?;:–—]{1,220}",
    re.IGNORECASE,
)
_REVIEWED_POSITIVE_CONSENT_CLAUSE_RE = re.compile(
    rf"(?:(?:the\s+)?{_SUBJECT}|our\s+team)\s+"
    r"(?:(?:has|have|had)\s+)?(?:"
    r"(?:grant(?:s|ed)?|giv(?:e|es|en)|provid(?:e|es|ed)|obtain(?:s|ed)?|"
    r"secur(?:e|es|ed)|receiv(?:e|es|ed))\s+"
    r"(?:(?:your|their|the|our)\s+)?(?:permission|authorization|consent)\s+"
    rf"(?:for|to)\s+(?:(?:all|any|future|further)\s+)?{_CHANNEL}|"
    rf"(?:consent(?:s|ed)?|agree(?:s|d)?|assent(?:s|ed)?|opt(?:s|ed)?[- ]in)\s+to\s+"
    rf"(?:(?:all|any|future|further)\s+)?{_CHANNEL}|"
    r"(?:asked|requested)\s+(?:us|our\s+team|the\s+team)\s+to\s+"
    rf"(?:allow|permit|authorize)\s+{_CHANNEL})\s*",
    re.IGNORECASE,
)
_REVIEWED_SAFE_CONSENT_OPERATION_RE = re.compile(
    rf"(?:(?:we|our\s+team|the\s+team|(?:our|the)\s+"
    r"(?:compliance|privacy|legal|servicing)\s+(?:team|department|staff|office))\s+"
    r"(?:verif(?:y|ies|ied|ying)|check(?:s|ed|ing)?|review(?:s|ed|ing)?|"
    r"document(?:s|ed|ing)?|record(?:s|ed|ing)?|audit(?:s|ed|ing)?|"
    r"confirm(?:s|ed|ing)?|validat(?:e|es|ed|ing))\s+"
    rf"(?:(?:{_CHANNEL})\s+)?(?:(?:the\s+)?(?:borrower|customer|homeowner|recipient)['’]s\s+)?"
    r"(?:permission|authorization|consent)(?:\s+records?)?\s+"
    rf"(?:(?:before|prior\s+to|for|to)\s+{_CHANNEL}|(?:monthly|periodically|regularly))|"
    rf"(?:(?:{_CHANNEL})\s+)?consent(?:\s+records?)?"
    rf"(?:\s+(?:for|to)\s+{_CHANNEL})?\s+(?:is|are)?\s*"
    r"(?:reviewed\s+(?:monthly|periodically)|documented\s+in\s+"
    r"(?:our|the)\s+compliance\s+system|follows\s+(?:our|the)\s+privacy\s+policy))",
    re.IGNORECASE,
)


def is_fail_closed_consent_evidence(match: re.Match[str]) -> bool:
    """Fail closed on consent claims except complete reviewed-safe clauses."""

    clause = match.group(0).strip(" .!?;:–—\t\r\n")
    return (
        _REVIEWED_POSITIVE_CONSENT_CLAUSE_RE.fullmatch(clause) is None
        and _REVIEWED_SAFE_CONSENT_OPERATION_RE.fullmatch(clause) is None
    )


CHANNEL_PROHIBITION_STATE_EVIDENCE_RE = re.compile(
    rf"\b{_CHANNEL}\b[^.!?;:–—]{{0,32}}\b(?:off[- ]limits|prohibited|forbidden|"
    r"barred|disallowed|not\s+(?:allowed|permitted|authorized))\b",
    re.IGNORECASE,
)


ADDITIONAL_DEAD_RESPONSE_EVIDENCE_RE = re.compile(
    rf"\b(?:(?:(?:all|every|any)\s+)?(?:(?:inbound|incoming)\s+)?{_RESPONSE}\s+(?:"
    r"(?:(?:is|are|was|were)\s+)?(?:left|kept|remain(?:s|ed|ing)?)\s+unanswered|"
    r"(?:is|are|was|were|has\s+been|have\s+been)\s+left\s+without\s+"
    r"(?:(?:a|any|staff|human)\s+)?(?:reply|response|answer)|"
    r"(?:lack|lacks|lacked|lacking)\s+(?:(?:human|staff|employee|team)\s+)?"
    r"(?:monitoring|review|handling|(?:an?\s+|any\s+)?owner(?:ship)?)|"
    r"(?:has|have|had)\s+(?:nobody|no\s+one)\s+"
    r"(?:assigned|available|responsible|accountable)|"
    r"(?:is|are|was|were)\s+ownerless|"
    r"(?:is|are|was|were|will\s+be|would\s+be)\s+(?:thrown|cast)\s+away|"
    r"(?:has|have|had)\s+no\s+(?:assigned\s+)?"
    r"(?:staff|employee|team|owner|reviewer|handler)|"
    r"(?:receives?|received|gets?|got)\s+(?:no|zero)\s+"
    r"(?:(?:human|staff|employee|team)\s+)?"
    r"(?:follow[- ]?up|review|handling|attention|response)|"
    r"(?:disappear|disappears|disappeared|disappearing)\s+into\s+"
    r"(?:(?:a|the)\s+)?(?:void|black\s+hole))|"
    rf"{_NEGATIVE_RESPONSE_ACTOR}\s+"
    rf"(?:(?:will|would|can|could|does|did)\s+|"
    rf"is\s+(?:there|assigned|available|present)\s+to\s+)?"
    rf"(?:ever\s+)?{_RESPONSE_HANDLING_ACTION}\s+"
    rf"(?:(?:the|these|those|any|incoming|inbound)\s+)?{_RESPONSE}|"
    rf"{_RESPONSE}[^.!?;:–—]{{0,80}}\b(?:but|yet|while|although|even\s+though)\s+"
    rf"{_NEGATIVE_RESPONSE_ACTOR}\s+(?:(?:will|would|can|could)\s+)?"
    rf"(?:ever\s+)?{_RESPONSE_HANDLING_ACTION}\s+(?:it|them)|"
    rf"there\s+(?:is|was|will\s+be)\s+{_NEGATIVE_RESPONSE_ACTOR}\s+to\s+"
    rf"{_RESPONSE_HANDLING_ACTION}\s+(?:{_RESPONSE}|it|them))|"
    rf"\b(?:discard|delete|drop|dump|expunge|dispose\s+of|sweep\s+away|"
    rf"throw\s+away|cast\s+aside)\s+"
    rf"(?:(?:all|every|any)\s+)?{_RESPONSE}\b",
    re.IGNORECASE,
)

ADDITIONAL_PRONOUN_DEAD_RESPONSE_EVIDENCE_RE = re.compile(
    rf"\b(?:{_NEGATIVE_RESPONSE_ACTOR}\s+"
    rf"(?:(?:will|would|can|could)\s+|to\s+)?(?:ever\s+)?{_RESPONSE_HANDLING_ACTION}\s+"
    r"(?:it|them)|(?:it|they|them)\s+"
    r"(?:(?:won|wouldn|can|couldn)['’]t\s+|"
    r"(?:(?:will|would|can|could|is|are|was|were)\s+)?(?:not|never)\s+)"
    r"(?:(?:be|get)\s+)?(?:read|reviewed|opened|checked|monitored|handled|processed)|"
    rf"{_RESPONSE}\s+(?:(?:won|wouldn|can|couldn)['’]t\s+|"
    r"(?:(?:will|would|can|could|is|are|was|were)\s+)?(?:not|never)\s+)"
    r"(?:(?:be|get)\s+)?"
    r"(?:read|reviewed|opened|checked|monitored|handled|processed)|"
    r"there\s+(?:isn['’]t|is\s+not|wasn['’]t|was\s+not)\s+anyone\s+to\s+"
    rf"{_RESPONSE_HANDLING_ACTION}\s+(?:{_RESPONSE}|it|them))\b",
    re.IGNORECASE,
)

DESTRUCTIVE_OR_IGNORED_RESPONSE_EVIDENCE_RE = re.compile(
    rf"\b(?:(?:(?:your|the|this|our)\s+)?{_RESPONSE}\s+"
    r"(?:(?:is|are|was|were|will\s+be|would\s+be|has\s+been|have\s+been)\s+)?"
    r"(?:incinerated|trashed|binned|junked|removed|ignored|disregarded|erased|"
    r"obliterated|shredded|destroyed|deleted|pulverized|wiped|scrapped|burned|burnt|"
    r"tossed(?:\s+away)?|(?:reduced|ground)\s+to\s+(?:ash(?:es)?|dust|pulp|waste|nothing)|"
    r"sent\s+to\s+oblivion|"
    r"(?:die|dies|died)\s+in\s+(?:the\s+)?queue|"
    r"(?:has|have|had)\s+no\s+chance\s+of\s+being\s+(?:read|reviewed|opened))"
    r"(?:\s+(?:unread|unseen|unreviewed|without\s+(?:human\s+)?review))?|"
    rf"(?:(?:your|the|this|our)\s+)?{_RESPONSE}\s+"
    r"(?:is|are|was|were|will\s+be|would\s+be)\s+not\s+"
    r"(?:looked\s+at|read|reviewed|checked|monitored|opened)|"
    r"(?:(?:this|the|our)\s+)?(?:inbox|mailbox|team|staff)\s+"
    r"(?:bins?|trashes?|incinerates?|ignores?|disregards?|erases?|obliterates?|"
    r"shreds?|destroys?|deletes?)\s+"
    rf"(?:(?:all|every|any)\s+)?(?:inbound|incoming)?\s*{_RESPONSE}"
    r"(?:\s+(?:unread|unseen|unreviewed|without\s+(?:human\s+)?review))?|"
    rf"(?:(?:all|every|any)\s+)?(?:(?:inbound|incoming)\s+)?{_RESPONSE}\s+"
    r"(?:is|are|was|were|will\s+be|would\s+be)?\s*"
    r"(?:destined|fated|scheduled|marked)\s+for\s+"
    r"(?:deletion|destruction|disposal|shredding|erasure)|"
    rf"(?:(?:all|every|any)\s+)?(?:(?:inbound|incoming)\s+)?{_RESPONSE}\s+"
    r"(?:go|goes|went)\s+(?:straight|directly)?\s*(?:in|into|to)\s+"
    r"(?:the\s+)?(?:trash|bin|shredder|void)|"
    rf"(?:(?:all|every|any)\s+)?(?:(?:inbound|incoming)\s+)?{_RESPONSE}\s+"
    r"(?:is|are|was|were|will\s+be|would\s+be)?\s*"
    r"(?:erased|deleted|destroyed|obliterated|shredded)\s+before\s+"
    r"(?:review|reading|opening|inspection)|"
    rf"(?:(?:all|every|any)\s+)?(?:(?:inbound|incoming)\s+)?{_RESPONSE}\s+"
    r"(?:is|are|was|were|will\s+be|would\s+be)?\s*fed\s+to\s+"
    r"(?:the\s+)?(?:shredder|trash|bin)|"
    rf"(?:(?:all|every|any)\s+)?(?:(?:inbound|incoming)\s+)?{_RESPONSE}\s+"
    r"(?:get|gets|got)\s+(?:erased|deleted|destroyed|obliterated|shredded)|"
    rf"(?:(?:your|the|this|our)\s+)?{_RESPONSE}\s+"
    r"(?:sink|sinks|sank|sunk)\s+without\s+(?:a\s+)?trace)\b",
    re.IGNORECASE,
)

# A declarative claim about the fate of a response is governed evidence by
# default. Imperative CTAs (``Reply YES``) do not match the subject grammar;
# reviewed staffed recovery is reconciled by the shared caller.
FAIL_CLOSED_RESPONSE_FATE_EVIDENCE_RE = re.compile(
    rf"(?:^|[.!?;:–—]\s*)(?:(?:(?:(?:your|the|this|our|all|every|any)\s+)?"
    rf"(?:(?:incoming|inbound)\s+)?{_RESPONSE})\s+"
    rf"(?:is|are|was|were|will\s+be|would\s+be|shall\s+be|should\s+be|must\s+be|ought\s+to\s+be|has\s+been|"
    r"have\s+been|gets?|got|winds?\s+up|wound\s+up|ends?\s+up|ended\s+up)\s+"
    r"[^.!?;:–—]{1,160}|"
    r"(?:replies|responses|messages|emails|texts|calls)\s+[^.!?;:–—]{1,180}|"
    rf"{_NEGATIVE_RESPONSE_ACTOR}\s+[^.!?;:–—]{{0,100}}\b{_RESPONSE}\b"
    r"[^.!?;:–—]{0,80})",
    re.IGNORECASE,
)

FAIL_CLOSED_RESPONSE_DESTINATION_EVIDENCE_RE = re.compile(
    rf"(?:^|[.!?;:–—]\s*)(?:(?:(?:the|our|this)\s+)?"
    rf"(?:(?:[a-z][a-z-]{{2,24}})\s+){{0,2}}(?:inbox|mailbox|queue|channel|"
    rf"system|platform|portal|address|engine|automation|repository|ledger|vault|archive)\b"
    rf"[^.!?;:–—]{{0,160}}\b{_RESPONSE}\b[^.!?;:–—]{{0,80}}|"
    r"(?:(?:anything|everything|nothing|whatever|what|all)|(?:any|every|no)\s+"
    rf"{_RESPONSE})\s+"
    r"(?:(?:that\s+)?you\s+)?(?:send|sent|submit|submitted|write|wrote)\b"
    r"[^.!?;:–—]{1,160}|(?:(?:this|the|our)\s+)?"
    r"(?:address|inbox|mailbox|queue|channel|portal)\s+(?:has|have|had)\s+no\s+"
    r"(?:(?:human|staff)\s+)?(?:monitor|owner|reviewer|handler)|"
    r"(?:we|they|the\s+team|our\s+team)\s+"
    r"(?:abandon|abandons|abandoned|devour|devours|consume|consumes)\s+"
    r"(?:anything|everything|nothing|whatever)(?:\s+that\s+arrives?\s+here)?|"
    r"(?:anything|everything|whatever|what)\s+(?:(?:that\s+)?arrives?|arriving)\s+here\s+"
    r"(?:(?:falls?|fell|fallen)\s+through\s+(?:the\s+)?cracks|"
    r"(?:is|are|was|were|gets?|got)?\s*(?:never\s+)?(?:read|reviewed|opened|checked)|"
    r"(?:disappear|disappears|disappeared|vanish|vanishes|vanished)\s+forever))",
    re.IGNORECASE,
)

SAFE_RESPONSE_OPERATION_RE = re.compile(
    rf"\b(?:(?:(?:your|the|this|our|all|every|incoming|inbound)\s+)?{_RESPONSE}\s+"
    r"(?:(?:is|are|was|were|will\s+be|would\s+be)\s+)?(?:stored|retained|archived)\s+"
    r"securely|(?:replies|responses|messages|emails|texts)\s+"
    r"(?:enter|enters|entered)\s+(?:(?:our|the|a)\s+)?(?:secure|monitored|staffed)\s+"
    r"(?:queue|inbox|mailbox)|replies\s+remain\s+available\s+for\s+follow[- ]?up|"
    r"responses\s+receive\s+confirmation|(?:(?:anything|everything|nothing|whatever|what|all)|"
    rf"(?:all|any|every|no)\s+{_RESPONSE})\s+"
    r"(?:(?:that\s+)?you\s+)?(?:send|sent|submit|submitted|write|wrote)\s+"
    r"(?:(?:is|are|was|were|will\s+be)\s+)?(?:stored|retained|archived)\s+securely|"
    rf"(?:(?:this|the|our)\s+)?(?:inbox|mailbox|queue)\s+is\s+staffed\s+and\s+"
    rf"accepts\s+(?:inbound|incoming)\s+{_RESPONSE}|"
    r"(?:(?:this|the|our)\s+)?(?:inbox|mailbox|queue)\s+accepts\s+"
    rf"(?:(?:inbound|incoming)\s+)?{_RESPONSE}\s+and\s+(?:an?\s+employee|staff|"
    r"our\s+team|the\s+team|someone)\s+(?:reads?|reviews?|monitors?|checks?|handles?)\s+"
    r"(?:it|them)(?:\s+daily)?|(?:(?:your|the|this|our)\s+)?"
    rf"{_RESPONSE}\s+(?:(?:is|are|was|were)\s+)?encrypted\s+at\s+rest|"
    rf"(?:(?:your|the|this|our)\s+)?{_RESPONSE}\s+"
    r"(?:remain|remains|remained)\s+in\s+(?:(?:a|the|our)\s+)?"
    r"(?:secure|monitored|staffed)\s+(?:queue|inbox|mailbox)\s+"
    r"(?:until|pending|before)\s+(?:human\s+|staff\s+)?review|"
    rf"(?:(?:the|our|this)\s+)?(?:intake\s+)?(?:ledger|vault|archive|repository)\s+"
    rf"(?:retains?|stores?|archives?)\s+(?:(?:all|every|each)\s+)?{_RESPONSE}\s+"
    r"for\s+(?:human|staff|team)\s+review)\b",
    re.IGNORECASE,
)

_STAFFED_ACTOR = (
    r"(?:a\s+human|an?\s+employees?|personnel|someone|staff|our\s+team|the\s+team|"
    r"support|servicing|legal\s+counsel|(?:the\s+|our\s+)?support\s+(?:agent|team)|compliance|"
    r"(?:the\s+|our\s+)?(?:compliance|privacy|legal|servicing)\s+"
    r"(?:agent|team|department|staff|office)|an?\s+agents?)"
)
_STAFFED_BUSINESS_ACTOR = (
    r"(?:(?:[a-z][a-z-]{1,24})\s+){1,4}"
    r"(?:associates|professionals|representatives|specialists|staff|team|workflows)"
)
_STAFFED_REVIEW_ACTION = (
    r"(?:(?:(?:will|would|can|could)\s+)?(?:archives?|opens?|process(?:es)?|reads?|reviews?|"
    r"handles?|receives?|monitors?|checks?|answers?|owns?|sees?)|respond(?:s|ed|ing)?\s+to)"
)
STAFFED_DELIVERY_RE = re.compile(
    r"\b(?:deliver(?:s|ed|ing)?|route(?:s|d|ing)?|release(?:s|d|ing)?)\b"
    r"[^.!?;:–—]{0,50}\b(?:to|into)\s+(?:(?:a|the|our)\s+)?"
    r"(?:staffed|monitored|human[- ]reviewed)\s+"
    r"(?:(?:email|sms|text|reply|response)\s+)?(?:team|inbox|mailbox|queue|channel)\b|"
    rf"\b(?:{_STAFFED_ACTOR}|(?:the|our)\s+(?:inbox|mailbox|reply|response)\s+"
    r"(?:liaison|support|servicing)\s+(?:team|staff))\s+"
    rf"{_STAFFED_REVIEW_ACTION}\s+"
    rf"(?:(?:(?:the|these|those|all|every|each|any)\s+)?{_RESPONSE}|it|them)\b|"
    rf"\bthere\s+is\s+{_STAFFED_ACTOR}\s+{_STAFFED_REVIEW_ACTION}\s+"
    rf"{_RESPONSE}\b|"
    r"\b(?:(?:your|the|this|our)\s+(?:reply|response|message|email|sms|text)|"
    r"replies|responses|messages|emails|sms|texts|correspondence|communications|submissions)\s+"
    r"(?:is|are|was|were|remains?|will\s+be|would\s+be|shall\s+be|should\s+be|"
    r"must\s+be|is\s+going\s+to\s+be)\s+"
    r"(?:answered|authored|handled|opened|prepared|processed|read|reviewed|received|routed|seen|sent|"
    rf"monitored|checked)\s+(?:by|to)\s+(?:{_STAFFED_ACTOR}|{_STAFFED_BUSINESS_ACTOR})\b|"
    r"\b(?:replies|responses|messages|emails?|sms|texts?)\s+(?:"
    r"(?:has|have)\s+(?:(?:an?\s+)?owner|assigned\s+staff|staff\s+assigned)|"
    r"(?:will\s+be|are|remain)\s+(?:read|reviewed|answered)|"
    r"(?:receive|get|face)\s+(?:human|staff)\s+(?:follow[- ]?up|attention|review))\b",
    re.IGNORECASE,
)


def response_transports(value: str) -> set[str]:
    """Return the response-channel families named by a bounded evidence span."""

    transports: set[str] = set()
    if re.search(r"\breply\s+(?:inbox|mailbox|queue|channel)\b", value, re.IGNORECASE):
        transports.add("reply")
    if re.search(r"\b(?:emails?|mail|inbox|mailbox)\b", value, re.IGNORECASE):
        transports.add("email")
    if re.search(r"\b(?:sms|texts?|short\s+codes?)\b", value, re.IGNORECASE):
        transports.add("sms")
    if re.search(r"\b(?:calls?|phone|telephone)\b", value, re.IGNORECASE):
        transports.add("phone")
    if not transports and re.search(
        r"\b(?:messages?|repl(?:y|ies)|responses?)\b", value, re.IGNORECASE
    ):
        transports.add("reply")
    return transports
