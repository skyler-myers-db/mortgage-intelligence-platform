"""Clause-aware negative consent and response-channel evidence."""

import re

from backend.schemas import borrower_cta_relationship_evidence as relationship_evidence
from backend.schemas.borrower_cta_actions import (
    BORROWER_CTA_CHANNEL_STATE_RE_FRAGMENT,
    BORROWER_CTA_CONSENT_STATE_RE_FRAGMENT,
    cta_channel_actions,
    explicit_borrower_contact_actions,
    negative_actions_for_positive,
)

_CORE_CONSENT_EVIDENCE_RE = re.compile(
    BORROWER_CTA_CONSENT_STATE_RE_FRAGMENT,
    re.IGNORECASE,
)
_CORE_DEAD_CHANNEL_EVIDENCE_RE = re.compile(
    BORROWER_CTA_CHANNEL_STATE_RE_FRAGMENT,
    re.IGNORECASE,
)
_CONSENT_SUBJECT_RE_FRAGMENT = (
    r"(?:(?:the\s+)?(?:borrowers?|customers?|recipients?|clients?|applicants?|"
    r"homeowners?|prospects?)|"
    r"i|we|you|they|he|she)"
)
_CONSENT_CHANNEL_RE_FRAGMENT = (
    r"(?:telephone\s+calls?|phone\s+calls?|calls?|emails?|mail|sms|texts?|"
    r"messages?|messaging|replies|responses?|contact|communications?|"
    r"correspondence|outreach|telephoning)"
)
_ACTIVE_OPT_OUT_EVIDENCE_RE = re.compile(
    rf"\b(?:standing\s+|current\s+|existing\s+|documented\s+|recorded\s+)?"
    rf"(?:{_CONSENT_CHANNEL_RE_FRAGMENT}\s+)?"
    r"(?:opt[- ]out|unsubscribe)(?:\s+(?:request|record|preference))?"
    r"(?:\s*,\s*(?:which|that)\s+|\s+)"
    r"(?:(?:is|was|remains?|remained|continues?\s+to\s+be)\s+)?"
    r"(?:active|valid|standing|on\s+file|in\s+effect|effective)\b",
    re.IGNORECASE,
)
_QUALIFIED_OPT_OUT_EVIDENCE_RE = re.compile(
    rf"\b(?:standing|current|existing|active|documented|recorded)\s+"
    rf"(?:{_CONSENT_CHANNEL_RE_FRAGMENT}\s+)?"
    r"(?:opt[- ]out|unsubscribe)(?:\s+(?:request|record|preference))?\b",
    re.IGNORECASE,
)
_RELATIVE_OPT_OUT_EVIDENCE_RE = re.compile(
    rf"\b{_CONSENT_SUBJECT_RE_FRAGMENT}\s*,?\s*(?:who|that)\s+"
    r"(?:(?:has|have|had)\s+)?(?:opted\s+out(?:\s+(?:of|from))?|"
    r"unsubscribed(?:\s+from)?)\s+"
    rf"(?:(?:all|any)\s+)?(?:marketing\s+)?{_CONSENT_CHANNEL_RE_FRAGMENT}\b",
    re.IGNORECASE,
)
_SUBJECT_OPT_OUT_EVIDENCE_RE = re.compile(
    rf"\b{_CONSENT_SUBJECT_RE_FRAGMENT}\s+"
    r"(?:(?:has|have|had)\s+)?(?:opted\s+out|unsubscribed)\b"
    rf"(?:\s+(?:of|from))?(?:\s+{_CONSENT_CHANNEL_RE_FRAGMENT})?",
    re.IGNORECASE,
)
_WITHDRAWN_PREFERENCE_EVIDENCE_RE = re.compile(
    rf"\b{_CONSENT_SUBJECT_RE_FRAGMENT}\s+"
    r"(?:(?:does|do|did)\s+not|doesn['’]t|don['’]t|didn['’]t|no\s+longer)\s+"
    rf"(?:want|wants|wish|wishes|accept|accepts|receive)\s+{_CONSENT_CHANNEL_RE_FRAGMENT}\b|"
    rf"\b{_CONSENT_SUBJECT_RE_FRAGMENT}\s+"
    rf"(?:said|stated|declared|requested|asked\s+for)\s+no\s+(?:more|further)\s+"
    rf"{_CONSENT_CHANNEL_RE_FRAGMENT}\b",
    re.IGNORECASE,
)
_STOP_EVIDENCE_RE = re.compile(
    rf"\b{_CONSENT_SUBJECT_RE_FRAGMENT}\s*,?\s*(?:(?:who|that)\s+)?"
    r"(?:said|stated|sent|texted|wrote|replied|responded)\s+"
    r"(?:us\s+|with\s+)?STOP\b",
    re.IGNORECASE,
)
_PASSIVE_STOP_EVIDENCE_RE = re.compile(
    rf"\b(?:an?\s+|the\s+)?STOP\s+(?:reply|response|message|text)\s+"
    r"(?:(?:was|is|has\s+been|had\s+been)\s+)?"
    rf"(?:received|sent|submitted)\s+(?:by|from)\s+{_CONSENT_SUBJECT_RE_FRAGMENT}\b",
    re.IGNORECASE,
)
_BARE_PASSIVE_STOP_EVIDENCE_RE = re.compile(
    rf"\b(?:an?\s+|the\s+)?STOP\s+"
    r"(?:(?:was|is|has\s+been|had\s+been)\s+)?"
    rf"(?:received|sent|submitted)\s+(?:by|from)\s+{_CONSENT_SUBJECT_RE_FRAGMENT}\b",
    re.IGNORECASE,
)
_DIRECT_STOP_INSTRUCTION_EVIDENCE_RE = re.compile(
    rf"\b{_CONSENT_SUBJECT_RE_FRAGMENT}\s+"
    r"(?:told|asked|instructed|directed|ordered)\s+"
    r"(?:us|our\s+team|the\s+team)\s+to\s+stop\b",
    re.IGNORECASE,
)
_REFRAIN_INSTRUCTION_EVIDENCE_RE = re.compile(
    rf"\b{_CONSENT_SUBJECT_RE_FRAGMENT}\s+"
    r"(?:told|asked|instructed|directed|ordered)\s+"
    r"(?:(?:us|our\s+team|the\s+team)\s+to\s+|"
    r"(?:(?:us|our\s+team|the\s+team)\s+)?that\s+"
    r"(?:we|our\s+team|the\s+team)\s+(?:must|should)?\s*)"
    r"(?:refrain|abstain)\s+from\s+"
    r"(?:contacting|calling|emailing|texting|messaging|replying|responding|"
    r"communicating|sending\s+(?:emails?|texts?|messages?))\b",
    re.IGNORECASE,
)
_WITHDRAWAL_SYNONYM_EVIDENCE_RE = re.compile(
    rf"\b{_CONSENT_SUBJECT_RE_FRAGMENT}\s+"
    r"(?:asked|requested|declared|demanded|imposed|called\s+for)\s+"
    r"(?:(?:us|our\s+team|the\s+team)\s+)?"
    r"(?:(?:an?|the)\s+)?(?:moratorium|freeze|embargo|ban|hiatus|blackout|"
    r"suspension)\s+(?:on|against|from)\s+"
    rf"(?:(?:all|any|further|future|additional)\s+)?{_CONSENT_CHANNEL_RE_FRAGMENT}\b|"
    rf"\b{_CONSENT_SUBJECT_RE_FRAGMENT}\s+"
    r"(?:vetoed|forbade|barred|disallowed|prohibited|rejected|renounced|revoked|"
    r"rescinded|withdrew|declined|refused)\s+"
    rf"(?:(?:all|any|further|future|additional)\s+){{1,2}}"
    rf"{_CONSENT_CHANNEL_RE_FRAGMENT}\b|"
    rf"\b(?:(?:all|any|further|future|additional)\s+)?{_CONSENT_CHANNEL_RE_FRAGMENT}\s+"
    r"(?:(?:is|are|was|were|has\s+been|have\s+been)\s+)?"
    r"(?:unwelcome|unwanted|not\s+welcome|objectionable|forbidden|prohibited|"
    r"barred|off[- ]limits)\b|"
    rf"\b{_CONSENT_SUBJECT_RE_FRAGMENT}\s+"
    r"(?:said|stated|declared|reported|asked|requested)\s+"
    r"(?:that\s+)?(?:(?:all|any|further|future|additional)\s+)?"
    rf"{_CONSENT_CHANNEL_RE_FRAGMENT}\s+"
    r"(?:(?:is|are|was|were|be|remain)\s+)?"
    r"(?:forbidden|prohibited|barred|unwelcome|unwanted|off[- ]limits|"
    r"stopped|halted|paused|suspended)\b|"
    rf"\b{_CONSENT_SUBJECT_RE_FRAGMENT}\s+"
    r"(?:asked|requested|said|stated|directed|ordered)\s+(?:us\s+)?that\s+"
    rf"(?:(?:all|any|further|future|additional)\s+)?{_CONSENT_CHANNEL_RE_FRAGMENT}\s+"
    r"(?:stop|cease|halt|end|pause|remain\s+stopped)\b",
    re.IGNORECASE,
)
_PASSIVE_WITHDRAWAL_EVIDENCE_RE = re.compile(
    rf"\b{_CONSENT_SUBJECT_RE_FRAGMENT}\s+"
    r"(?:asked|requested|demanded|directed|insisted|said|stated)\s+"
    r"(?:(?:that\s+)?(?:they|he|she)\s+(?:should\s+)?)?"
    r"(?:to\s+)?be\s+left\s+alone\b|"
    rf"\b{_CONSENT_SUBJECT_RE_FRAGMENT}\s+"
    r"(?:want|wants|wanted|wish|wishes|wished|prefer|prefers|preferred)\s+"
    r"(?:to\s+)?be\s+left\s+alone\b|"
    rf"\b{_CONSENT_SUBJECT_RE_FRAGMENT}\s+"
    r"(?:nullif(?:y|ies|ied)|void(?:s|ed)?|invalidat(?:e|es|ed)|annul(?:s|led)?|"
    r"terminat(?:e|es|ed)|cancel(?:s|led|ed))\s+"
    rf"(?:(?:the|their|this)\s+)?(?:{_CONSENT_CHANNEL_RE_FRAGMENT}\s+)?"
    r"(?:permission|authorization|auth|consent)\b|"
    # "Radio silence" is an unqualified no-contact directive, not benign
    # operational copy.
    rf"\b{_CONSENT_SUBJECT_RE_FRAGMENT}\s+"
    r"(?:asked\s+for|requested|demanded|directed|imposed|insisted\s+on)\s+"
    r"(?:(?:complete|continued|future|ongoing|total)\s+)?radio\s+silence\b|"
    # Bind colloquial DNC wording to the number/call transport so a later call
    # CTA cannot contradict it.
    rf"\b{_CONSENT_SUBJECT_RE_FRAGMENT}\s+"
    r"(?:said|stated|requested|demanded|directed|insisted)\s+"
    r"(?:that\s+)?(?:this|the|their)\s+(?:phone\s+)?number\s+"
    r"(?:(?:is|was|must\s+be|should\s+be)\s+)?not\s+to\s+be\s+"
    r"called\s+again\b|"
    rf"\b(?:(?:their|the|this)\s+)?(?:{_CONSENT_CHANNEL_RE_FRAGMENT}\s+)?"
    r"(?:permission|authorization|auth|consent)"
    rf"(?:\s+(?:for|to)\s+(?:(?:all|any|further|future|additional)\s+)?"
    rf"{_CONSENT_CHANNEL_RE_FRAGMENT})?\s+"
    r"(?:is|are|was|were|has\s+been|have\s+been|had\s+been)\s+"
    r"(?:relinquished|disclaimed|abandoned|discontinued|given\s+up)\s+"
    rf"(?:by|from)\s+{_CONSENT_SUBJECT_RE_FRAGMENT}\b",
    re.IGNORECASE,
)
_RECORDED_PAUSE_REQUEST_EVIDENCE_RE = re.compile(
    r"\b(?:an?|the|their|your)\s+request\s+"
    r"(?:"
    r"to\s+(?:pause|halt|suspend|stop|cease|discontinue|refrain\s+from|avoid)\s+"
    rf"(?:(?:all|any|further)\s+)?{_CONSENT_CHANNEL_RE_FRAGMENT}\s+"
    r"(?:(?:is|was|has\s+been|had\s+been|remains?)\s+)?"
    r"(?:recorded|logged|documented|on\s+file)|"
    r"(?:(?:is|was|has\s+been|had\s+been|remains?)\s+)?"
    r"(?:recorded|logged|documented|on\s+file)\s+to\s+"
    r"(?:pause|halt|suspend|stop|cease|discontinue|refrain\s+from|avoid)\s+"
    rf"(?:(?:all|any|further)\s+)?{_CONSENT_CHANNEL_RE_FRAGMENT}|"
    rf"that\s+(?:(?:all|any|further)\s+)?{_CONSENT_CHANNEL_RE_FRAGMENT}\s+"
    r"(?:be|are|remain)\s+(?:paused|halted|suspended|stopped|discontinued)\s+"
    r"(?:(?:is|was|has\s+been|had\s+been|remains?)\s+)?"
    r"(?:recorded|logged|documented|on\s+file)"
    r")\b",
    re.IGNORECASE,
)
_CHANNEL_WITHDRAWAL_REQUEST_EVIDENCE_RE = re.compile(
    r"\b(?:(?:an?|the|their|your)\s+)?"
    r"(?:request|instruction|directive|preference)\s+"
    r"(?:"
    rf"for\s+no\s+(?:more|further)?\s*{_CONSENT_CHANNEL_RE_FRAGMENT}|"
    r"to\s+(?:pause|halt|suspend|stop|cease|discontinue|hold|"
    r"refrain\s+from|desist\s+from)\s+"
    rf"(?:(?:all|any|further)\s+)?{_CONSENT_CHANNEL_RE_FRAGMENT}|"
    rf"(?:that\s+)?(?:(?:all|any|further)\s+)?{_CONSENT_CHANNEL_RE_FRAGMENT}\s+"
    r"(?:be|are|remain)\s+(?:(?:put|placed)\s+on\s+hold|held|paused|suspended)"
    r")\b",
    re.IGNORECASE,
)
_SUBJECT_CHANNEL_WITHDRAWAL_EVIDENCE_RE = re.compile(
    rf"\b{_CONSENT_SUBJECT_RE_FRAGMENT}\s+"
    r"(?:asked|requested|told|instructed|directed|ordered)\s+"
    r"(?:"
    r"(?:(?:us|our\s+team|the\s+team)\s+)?to\s+"
    r"(?:pause|halt|suspend|stop|cease|discontinue|hold|"
    r"refrain\s+from|desist\s+from)\s+"
    rf"(?:(?:all|any|further)\s+)?{_CONSENT_CHANNEL_RE_FRAGMENT}|"
    rf"(?:for\s+)?(?:(?:all|any|further)\s+)?{_CONSENT_CHANNEL_RE_FRAGMENT}\s+"
    r"(?:to\s+)?(?:be|are|remain)\s+"
    r"(?:(?:put|placed)\s+on\s+hold|held|paused|suspended|"
    r"stopped|discontinued)|"
    rf"for\s+no\s+(?:more|further)?\s*{_CONSENT_CHANNEL_RE_FRAGMENT}"
    r")\b",
    re.IGNORECASE,
)
_STRUCTURAL_CONTACT_WITHDRAWAL_EVIDENCE_RE = re.compile(
    # Bind a borrower role to withdrawal and contact scope structurally.
    rf"\b{_CONSENT_SUBJECT_RE_FRAGMENT}\s+"
    r"(?:"
    r"(?:insist(?:s|ed)?|ask(?:s|ed)?|request(?:s|ed)?|direct(?:s|ed)?|"
    r"order(?:s|ed)?)\s+"
    r"(?:(?:that\s+)?(?:we|our\s+team|the\s+team)\s+|"
    r"(?:us|our\s+team|the\s+team)\s+to\s+)?"
    r"(?:cease|stop|halt|suspend|discontinue|desist\s+from|refrain\s+from)\s+"
    rf"(?:(?:all|any|further|future|additional)\s+)?{_CONSENT_CHANNEL_RE_FRAGMENT}|"
    r"(?:place(?:s|d)?|file(?:s|d)?|record(?:s|ed)?|submit(?:s|ted)?)\s+"
    r"(?:(?:a|the|their)\s+)?(?:do[- ]not[- ]disturb|no[- ]contact)\s+"
    r"(?:request|instruction|directive|preference)|"
    r"(?:has|have|had|express(?:es|ed)?|state(?:s|d)?|record(?:s|ed)?)\s+"
    r"(?:(?:a|the|their)\s+)?(?:do[- ]not[- ]disturb|no[- ]contact)\s+"
    r"(?:request|instruction|directive|preference)"
    r")\b",
    re.IGNORECASE,
)
_WITHDRAWAL_CHANNEL_RE_FRAGMENT = (
    rf"(?:{_CONSENT_CHANNEL_RE_FRAGMENT}|calling|emailing|texting|contacting|" r"communicating)"
)
_SUBJECT_WITHDRAWAL_PREDICATE_EVIDENCE_RE = re.compile(
    # Bind the borrower, withdrawal predicate, and affected channel without
    # treating unrelated demands or permissions as consent evidence.
    rf"\b{_CONSENT_SUBJECT_RE_FRAGMENT}\s+"
    r"(?:"
    r"(?:expressly\s+|explicitly\s+|affirmatively\s+)?"
    r"(?:demand(?:s|ed|ing)?|request(?:s|ed|ing)?|ask(?:s|ed|ing)?)\s+"
    r"(?:"
    rf"no\s+(?:(?:more|further|future|additional)\s+)?{_WITHDRAWAL_CHANNEL_RE_FRAGMENT}|"
    r"(?:(?:the|an?)\s+)?(?:cessation|termination|end|halt|suspension|"
    r"discontinuation)\s+(?:of|to)\s+"
    rf"(?:(?:all|any|further|future|additional)\s+)?{_WITHDRAWAL_CHANNEL_RE_FRAGMENT}|"
    rf"(?:(?:all|any|further|future|additional)\s+)?{_WITHDRAWAL_CHANNEL_RE_FRAGMENT}\s+"
    r"(?:stop|cease|end|halt|terminate|remain\s+stopped)"
    r")|"
    r"insist(?:s|ed|ing)?\s+(?:that\s+)?"
    r"(?:"
    rf"(?:(?:all|any|further|future|additional)\s+)?{_WITHDRAWAL_CHANNEL_RE_FRAGMENT}\s+"
    r"(?:stop|cease|end|halt|terminate|remain\s+stopped)|"
    r"(?:on\s+)?(?:(?:the|an?)\s+)?(?:cessation|termination|end|halt|"
    r"suspension|discontinuation)\s+of\s+"
    rf"(?:(?:all|any|further|future|additional)\s+)?{_WITHDRAWAL_CHANNEL_RE_FRAGMENT}"
    r")|"
    r"(?:expressly\s+|explicitly\s+|affirmatively\s+)?"
    r"(?:declin(?:e|es|ed|ing)|refus(?:e|es|ed|ing)|reject(?:s|ed|ing)?)\s+"
    rf"(?:(?:all|any|further|future|additional)\s+)?{_WITHDRAWAL_CHANNEL_RE_FRAGMENT}|"
    r"(?:want|wants|wanted|wish|wishes|wished|prefer|prefers|preferred)\s+"
    rf"(?:(?:all|any|further|future|additional)\s+)?{_WITHDRAWAL_CHANNEL_RE_FRAGMENT}\s+"
    r"(?:ended|stopped|ceased|halted|terminated|discontinued|suspended)|"
    r"object(?:s|ed|ing)?\s+to\s+"
    rf"(?:(?:all|any|further|future|additional)\s+)?{_WITHDRAWAL_CHANNEL_RE_FRAGMENT}|"
    r"(?:(?:does|do|did)\s+not|doesn['’]t|don['’]t|didn['’]t|no\s+longer)\s+"
    r"(?:permits?|allows?|authorizes?)\s+"
    rf"(?:(?:all|any|further|future|additional)\s+)?{_WITHDRAWAL_CHANNEL_RE_FRAGMENT}|"
    r"(?:(?:has|have|had)\s+)?stopp(?:ed|ing)\s+"
    r"(?:permitting|allowing|authorizing)\s+"
    rf"(?:(?:all|any|further|future|additional)\s+)?{_WITHDRAWAL_CHANNEL_RE_FRAGMENT}|"
    r"(?:permit|permits|allow|allows|authorize|authorizes)\s+no\s+"
    rf"(?:(?:more|further|future|additional)\s+)?{_WITHDRAWAL_CHANNEL_RE_FRAGMENT}|"
    r"(?:said|stated|declared)\s+no(?:\s+to)?\s+"
    rf"(?:(?:more|further|future|additional)\s+)?{_WITHDRAWAL_CHANNEL_RE_FRAGMENT}|"
    r"(?:declin(?:e|es|ed|ing)|refus(?:e|es|ed|ing)|den(?:y|ies|ied|ying)|"
    r"(?:withhold(?:s|ing)?|withheld))\s+"
    r"(?:(?:their|the)\s+)?(?:permission|authorization|auth|consent)\s+"
    rf"(?:for|to)\s+{_WITHDRAWAL_CHANNEL_RE_FRAGMENT}|"
    r"(?:(?:does|do|did)\s+not|doesn['’]t|don['’]t|didn['’]t|never)\s+"
    r"(?:grant|give|provide)\s+"
    r"(?:(?:their|the|any)\s+)?(?:permission|authorization|auth|consent)\s+"
    rf"(?:for|to)\s+{_WITHDRAWAL_CHANNEL_RE_FRAGMENT}|"
    r"(?:ask(?:s|ed|ing)?|request(?:s|ed|ing)?)\s+(?:not|never)\s+to\s+"
    r"hear\s+from\s+(?:us|our\s+team|the\s+team)(?:\s+again)?"
    r")\b",
    re.IGNORECASE,
)
_STRUCTURAL_CONSENT_PROHIBITION_EVIDENCE_RE = re.compile(
    # Consent withdrawal is governed by grammar, not a finite sentence list:
    # bind the borrower-role subject to a prohibition/revocation predicate and
    # keep either its affected channel or the generic consent object in-span.
    rf"\b{_CONSENT_SUBJECT_RE_FRAGMENT}\s+"
    r"(?:(?:has|have|had|is|are|was|were)\s+)?"
    r"(?:(?:expressly|explicitly|affirmatively)\s+)?"
    r"(?:"
    r"(?:prohibit(?:s|ed|ing)?|forbid(?:s|ding)?|forbade|forbidden|"
    r"bar(?:s|red|ring)?|disallow(?:s|ed|ing)?)\s+"
    r"(?:"
    r"(?:(?:us|our\s+team|the\s+team)\s+(?:from\s+)?)?"
    rf"(?:(?:all|any|further|future|additional)\s+)?{_WITHDRAWAL_CHANNEL_RE_FRAGMENT}|"
    r"(?:(?:us|our\s+team|the\s+team)\s+)?(?:from\s+)?"
    r"(?:calling|emailing|texting|messaging|contacting|communicating)|"
    r"(?:(?:their|the|this)\s+)?"
    rf"(?:{_CONSENT_CHANNEL_RE_FRAGMENT}\s+)?"
    r"(?:permission|authorization|auth|consent)"
    rf"(?:\s+(?:for|to)\s+{_WITHDRAWAL_CHANNEL_RE_FRAGMENT})?"
    r")|"
    r"(?:retract(?:s|ed|ing)?|repudiat(?:e|es|ed|ing)|"
    r"disavow(?:s|ed|ing)?|revok(?:e|es|ed|ing)|rescind(?:s|ed|ing)?|"
    r"withdraw(?:s|n|ing)?|withdrew|relinquish(?:es|ed|ing)?|"
    r"disclaim(?:s|ed|ing)?|abandon(?:s|ed|ing)?|"
    r"discontinu(?:e|es|ed|ing)|(?:give|gives|gave|giving)\s+up)\s+"
    r"(?:"
    r"(?:(?:their|the|this)\s+)?"
    rf"(?:{_WITHDRAWAL_CHANNEL_RE_FRAGMENT}\s+)?"
    r"(?:permission|authorization|auth|consent)"
    rf"(?:\s+(?:for|to)\s+{_WITHDRAWAL_CHANNEL_RE_FRAGMENT})?|"
    rf"(?:(?:all|any|further|future|additional)\s+)?{_WITHDRAWAL_CHANNEL_RE_FRAGMENT}"
    r")|"
    r"(?:declin(?:e|es|ed|ing)|refus(?:e|es|ed|ing)|reject(?:s|ed|ing)?)\s+"
    r"(?:to\s+be\s+(?:called|emailed|texted|messaged|contacted)|"
    rf"(?:(?:all|any|further|future|additional)\s+)+{_WITHDRAWAL_CHANNEL_RE_FRAGMENT})|"
    r"insist(?:s|ed|ing)?\s+on\s+no\s+"
    rf"(?:(?:more|further|future|additional)\s+)?{_WITHDRAWAL_CHANNEL_RE_FRAGMENT}"
    r")\b",
    re.IGNORECASE,
)
_EXPLICIT_CHANNEL_WITHDRAWAL_EVIDENCE_RE = re.compile(
    # Bind an identifiable borrower role, an explicit withdrawal predicate,
    # and its contact object. Non-contact rejection (for example, a fee) is
    # deliberately outside this grammar.
    rf"\b{_CONSENT_SUBJECT_RE_FRAGMENT}\s+"
    r"(?:(?:has|have|had|is|are|was|were)\s+)?"
    r"(?:"
    r"(?:cancel(?:s|led|ling|ed|ing)?|terminat(?:e|es|ed|ing)|"
    r"discontinu(?:e|es|ed|ing)|abandon(?:s|ed|ing)?|"
    r"withdraw(?:s|n|ing)?|withdrew|refus(?:e|es|ed|ing)|"
    r"veto(?:s|es|ed|ing)?|renounc(?:e|es|ed|ing)|reject(?:s|ed|ing)?|"
    r"opt(?:s|ed|ing)?\s+against)\s+(?:from\s+)?"
    r"(?:(?:(?:all|any|more|further|future|additional|ongoing)\s+){0,2}"
    rf"{_WITHDRAWAL_CHANNEL_RE_FRAGMENT}|"
    r"(?:being|to\s+be)\s+"
    r"(?:called|telephoned|emailed|texted|messaged|contacted)|"
    r"(?:receiving|accepting)\s+"
    rf"(?:(?:all|any|more|further|future|additional)\s+)?{_WITHDRAWAL_CHANNEL_RE_FRAGMENT})"
    r")\b|"
    r"\b(?:(?:(?:all|any|more|further|future|additional|ongoing)\s+){0,2}"
    rf"{_WITHDRAWAL_CHANNEL_RE_FRAGMENT}|"
    r"(?:being|to\s+be)\s+"
    r"(?:called|telephoned|emailed|texted|messaged|contacted))\s+"
    r"(?:is|are|was|were|has\s+been|have\s+been|had\s+been)\s+"
    r"(?:cancelled|canceled|terminated|withdrawn|refused|vetoed|renounced|"
    r"rejected|abandoned|discontinued|opted\s+against)\s+(?:by|from)\s+"
    rf"{_CONSENT_SUBJECT_RE_FRAGMENT}\b",
    re.IGNORECASE,
)
_UNREAD_CHANNEL_EVIDENCE_RE = re.compile(
    r"(?:\b(?:nobody|no\s+one|no\s+employee|none\s+of\s+"
    r"(?:(?:our|the)\s+)?employees?)\s+(?:ever\s+)?"
    r"(?:checks?|opens?|reads?|reviews?|monitors?|watches?)\s+"
    r"(?:it|them|this\s+|the\s+|our\s+|incoming\s+)?"
    r"(?:inbox|mailbox|emails?|sms|texts?|messages?|repl(?:y|ies)|responses?)\b|"
    r"\b(?:this\s+|the\s+|our\s+)?(?:inbox|mailbox|reply\s+channel)\s+"
    r"[^.!?;:–—]{0,80}\b(?:but|yet|while)\b[^.!?;:–—]{0,60}\b"
    r"(?:nobody|no\s+one|no\s+employee|none\s+of\s+"
    r"(?:(?:our|the)\s+)?employees?)\s+(?:ever\s+)?"
    r"(?:checks?|opens?|reads?|reviews?|monitors?|watches?)\s+"
    r"(?:it|them|the\s+(?:inbox|mailbox|replies|responses?))\b)",
    re.IGNORECASE,
)
_PASSIVE_UNREAD_CHANNEL_EVIDENCE_RE = re.compile(
    r"\b(?:no|not\s+a|not\s+one)\s+(?:human|employee|staff\s+member|team\s+member)\s+"
    r"(?:will|would|can|could|does|did)\s+(?:ever\s+)?"
    r"(?:read|review|see|receive|monitor|check)\s+"
    r"(?:the\s+|these\s+|those\s+|any\s+)?(?:replies|responses|messages|emails)\b",
    re.IGNORECASE,
)
_RESPONSE_SINK_EVIDENCE_RE = re.compile(
    r"\b(?:(?:(?:all|every|any)\s+)?(?:inbound|incoming)\s+|"
    r"(?:all|every|any)\s+)?(?:repl(?:y|ies)|responses?|messages?|emails?)\b"
    r"[^.!?;:–—]{0,28}\b"
    r"(?:vanish(?:es|ed)?|disappear(?:s|ed)?|"
    r"(?:are\s+|is\s+|was\s+|were\s+)?"
    r"(?:lost|discarded|dropped|dumped|swallowed|terminated|expunged)|"
    r"(?:disposed\s+of|swept\s+away)|(?:face|faces|faced|facing)\s+"
    r"(?:disposal|deletion|destruction|discarding)|"
    r"(?:(?:are|is|was|were|will\s+be|would\s+be|has\s+been|have\s+been)\s+)?"
    r"(?:doomed|destined|fated|condemned)\s+to\s+(?:remain\s+)?"
    r"(?:unread|unseen|unreviewed|unanswered)|"
    r"(?:terminate|terminates|terminated|end|ends)\b|"
    r"(?:(?:are|is|was|were|will\s+be|would\s+be|have\s+been|has\s+been)\s+)?"
    r"held\s+indefinitely|"
    r"(?:(?:are|is|was|were|will\s+be|would\s+be)\s+)?"
    r"(?:quarantined|consigned)\s+(?:indefinitely|(?:in)?to\s+nowhere)|"
    r"(?:go|goes|remain|remains)\s+unseen\s+by\s+"
    r"(?:staff|our\s+team|the\s+team|a\s+human|an\s+employee)|"
    r"(?:are\s+|is\s+)?routed\s+(?:in)?to\s+"
    r"(?:a\s+|the\s+)?(?:void|nowhere|dead\s+end|unmonitored\s+queue))\b"
    r"(?:[^.!?;:–—]{0,50}\b(?:before|without)\b[^.!?;:–—]{0,35}\b"
    r"(?:anyone|a\s+human|an\s+employee|staff)\s+"
    r"(?:reads?|reviews?|sees?|receives?)(?:\s+it|\s+them)?)?",
    re.IGNORECASE,
)
_EXPIRING_RESPONSE_EVIDENCE_RE = re.compile(
    r"\b(?:(?:all|every|any)\s+)?(?:(?:inbound|incoming)\s+)?"
    r"(?:repl(?:y|ies)|responses?|messages?|emails?|sms|texts?)\s+"
    r"(?:expire(?:s|d)?|lapse(?:s|d)?|evaporate(?:s|d)?|time(?:s|d)?\s+out|"
    r"self[- ]destruct(?:s|ed)?|"
    r"self[- ]delet(?:e|es|ed))\s+before\s+"
    r"(?:(?:a|an|any|the)\s+)?(?:human|employee|staff(?:\s+member)?|team(?:\s+member)?)\s+"
    r"(?:(?:can|could|will|would)\s+)?"
    r"(?:read|review|see|receive|process|open)(?:s|ed)?(?:\s+(?:it|them))?\b",
    re.IGNORECASE,
)
_PRONOUN_RESPONSE_SINK_EVIDENCE_RE = re.compile(
    r"\b(?:it|this|that|the\s+system|the\s+platform|the\s+service)\s+"
    r"(?:silently\s+)?"
    r"(?:suppresses?|filters?|blocks?|blackholes?|drops?|rejects?|discards?|dumps?|"
    r"swallows?|intercepts?|destroys?|quarantines?|erases?|deletes?|purges?|shreds?|"
    r"wipes?)\s+(?:(?:all|every|any)\s+)?(?:inbound|incoming)\s+"
    r"(?:emails?|sms|texts?|messages?|repl(?:y|ies)|responses?)\b",
    re.IGNORECASE,
)
_PROVIDER_RESPONSE_SINK_EVIDENCE_RE = re.compile(
    r"\b(?:(?:our|the)\s+)?(?:email\s+|mail\s+|sms\s+|telecom\s+)?"
    r"(?:provider|carrier|gateway)\s*,?\s*(?:(?:which|that)\s+)?"
    r"(?:silently\s+)?(?:suppresses?|filters?|blocks?|blackholes?|drops?|rejects?|"
    r"intercepts?|destroys?|quarantines?|erases?|deletes?|purges?|discards?|shreds?|"
    r"wipes?|incinerates?|obliterates?)\s+"
    r"(?:(?:(?:all|every|any)\s+)?(?:inbound|incoming)\s+|"
    r"(?:all|every|any)\s+)"
    r"(?:emails?|sms|texts?|messages?|repl(?:y|ies)|responses?)\b",
    re.IGNORECASE,
)
_PASSIVE_PROVIDER_SINK_EVIDENCE_RE = re.compile(
    r"\b(?:(?:all|every|any)\s+)?(?:inbound|incoming)\s+"
    r"(?:emails?|sms|texts?|messages?|repl(?:y|ies)|responses?)\s+"
    r"(?:(?:is|are|was|were|has\s+been|have\s+been)\s+)?"
    r"(?:suppressed|filtered|blocked|blackholed|dropped|rejected|intercepted|destroyed|"
    r"quarantined|erased|deleted|purged|discarded|shredded|wiped|incinerated|"
    r"obliterated|vaporized|annihilated|overwritten)\s+"
    r"(?:by|through)\s+(?:(?:our|the)\s+)?"
    r"(?:email\s+|mail\s+|sms\s+|telecom\s+)?"
    r"(?:provider|carrier|gateway|platform|system|service)\b",
    re.IGNORECASE,
)
_OVERWRITTEN_RESPONSE_EVIDENCE_RE = re.compile(
    r"\b(?:(?:all|every|any)\s+)?(?:inbound|incoming)\s+"
    r"(?:emails?|sms|texts?|messages?|repl(?:y|ies)|responses?)\s+"
    r"(?:(?:is|are|was|were|has\s+been|have\s+been)\s+)?"
    r"overwrit(?:e|es|ten)\s+(?:by|through)\s+"
    r"(?:(?:our|the)\s+)?(?:email\s+|mail\s+|sms\s+|telecom\s+)?"
    r"(?:provider|carrier|gateway|platform|system|service)\b",
    re.IGNORECASE,
)
_ACTORLESS_PASSIVE_DESTRUCTIVE_RESPONSE_EVIDENCE_RE = re.compile(
    r"\b(?:(?:all|every|any)\s+)?(?:inbound|incoming)\s+"
    r"(?:emails?|sms|texts?|messages?|repl(?:y|ies)|responses?)\s+"
    r"(?:(?:is|are|was|were|has\s+been|have\s+been)\s+)?(?:"
    r"overwrit(?:e|es|ten)|"
    r"replaced\b[^.!?;:–—]{0,90}\bbefore\b[^.!?;:–—]{0,50}(?:"
    r"\b(?:staff|a\s+human|an\s+employee|a\s+team\s+member|anyone)\b"
    r"[^.!?;:–—]{0,24}\b(?:can\s+|could\s+|will\s+|would\s+)?"
    r"(?:read|review|see|receive|process|open)(?:s|ed)?\b|"
    r"\b(?:(?:human|staff)\s+)?review\b))",
    re.IGNORECASE,
)
_REPLACED_BEFORE_REVIEW_EVIDENCE_RE = re.compile(
    r"\b(?:"
    r"(?:(?:our|the)\s+)?(?:email\s+|mail\s+|sms\s+|telecom\s+)?"
    r"(?:provider|carrier|gateway|platform|system|service)\s*,?\s*"
    r"replac(?:e|es|ed|ing)\s+"
    r"(?:(?:all|every|any)\s+)?(?:inbound|incoming)\s+"
    r"(?:emails?|sms|texts?|messages?|repl(?:y|ies)|responses?)|"
    r"(?:(?:all|every|any)\s+)?(?:inbound|incoming)\s+"
    r"(?:emails?|sms|texts?|messages?|repl(?:y|ies)|responses?)\s+"
    r"(?:(?:is|are|was|were|has\s+been|have\s+been)\s+)?replaced\s+"
    r"(?:by|through)\s+(?:(?:our|the)\s+)?"
    r"(?:email\s+|mail\s+|sms\s+|telecom\s+)?"
    r"(?:provider|carrier|gateway|platform|system|service)"
    r")[^.!?;:–—]{0,90}\bbefore\b[^.!?;:–—]{0,50}(?:"
    r"\b(?:staff|a\s+human|an\s+employee|a\s+team\s+member|anyone)\b"
    r"[^.!?;:–—]{0,24}\b(?:can\s+|could\s+|will\s+|would\s+)?"
    r"(?:read|review|see|receive|process|open)(?:s|ed)?\b|"
    r"\b(?:(?:human|staff)\s+)?review\b)",
    re.IGNORECASE,
)
_UNREVIEWED_PROVIDER_CHANNEL_ACTION_RE = re.compile(
    r"\b(?:(?:our|the)\s+)?(?:email\s+|mail\s+|sms\s+|telecom\s+)?"
    r"(?:provider|carrier|gateway|platform|system|service)\s*,?\s*"
    r"(?:(?:which|that)\s+)?(?:silently\s+)?"
    r"(?!(?:routes?|delivers?|relays?|forwards?|sends?|releases?|receives?|accepts?|"
    r"queues?|stores?|archives?)\b)"
    r"[a-z][a-z-]{2,30}\s+"
    r"(?:(?:(?:all|every|any)\s+)?(?:inbound|incoming)\s+|"
    r"(?:all|every|any)\s+)"
    r"(?:emails?|sms|texts?|messages?|repl(?:y|ies)|responses?)\b",
    re.IGNORECASE,
)
_INDEFINITE_PROVIDER_RESPONSE_SINK_RE = re.compile(
    r"\b(?:(?:our|the)\s+)?(?:email\s+|mail\s+|sms\s+|telecom\s+)?"
    r"(?:provider|carrier|gateway|platform|system|service)\s*,?\s*"
    r"(?:silently\s+)?(?:quarantines?|holds?|detains?|sequesters?)\s+"
    r"(?:(?:all|every|any)\s+)?(?:(?:inbound|incoming)\s+)?"
    r"(?:emails?|sms|texts?|messages?|repl(?:y|ies)|responses?)\s+"
    r"(?:indefinitely|permanently|without\s+(?:human|staff)\s+review)\b",
    re.IGNORECASE,
)
_UNSURFACED_RESPONSE_EVIDENCE_RE = re.compile(
    r"\b(?:(?:all|every|any)\s+)?(?:(?:inbound|incoming)\s+)?"
    r"(?:emails?|sms|texts?|messages?|repl(?:y|ies)|responses?)\s+"
    r"(?:(?:is|are|was|were|will\s+be|would\s+be)\s+)?never\s+"
    r"(?:surfaced|shown|presented|delivered|routed|forwarded|released)\s+to\s+"
    r"(?:staff|our\s+team|the\s+team|a\s+human|an\s+employee)\b",
    re.IGNORECASE,
)
_UNREVIEWED_STORED_RESPONSE_EVIDENCE_RE = re.compile(
    r"\b(?:(?:our|the)\s+)?(?:email\s+|mail\s+|sms\s+|telecom\s+)?"
    r"(?:provider|carrier|gateway|platform|system|service)\s*,?\s*"
    r"(?:archives?|stores?|queues?|files?|retains?)\s+"
    r"(?:(?:all|every|any)\s+)?(?:inbound|incoming)\s+"
    r"(?:emails?|sms|texts?|messages?|repl(?:y|ies)|responses?)\s+"
    r"(?:where|while|but|yet|and)\s+"
    r"(?:nobody|no\s+one|no\s+employee|not\s+a\s+human|staff\s+never|"
    r"our\s+team\s+never|the\s+team\s+never)\s+"
    r"(?:ever\s+)?(?:reads?|reviews?|sees?|receives?|monitors?|checks?|opens?)\s+"
    r"(?:it|them|the\s+(?:archive|queue|messages?|replies|responses?))\b",
    re.IGNORECASE,
)
_UNREVIEWED_ARCHIVED_RESPONSE_EVIDENCE_RE = re.compile(
    r"\b(?:(?:all|every|any)\s+)?(?:(?:inbound|incoming)\s+)?"
    r"(?:emails?|sms|texts?|messages?|repl(?:y|ies)|responses?)\s+"
    r"(?:(?:is|are|was|were|has\s+been|have\s+been|will\s+be)\s+)?"
    r"(?:archived|stored|queued|filed|retained)\s+"
    r"(?:and|but|where|while|yet)\s+"
    r"(?:"
    r"(?:is|are|was|were|will\s+be|would\s+be)?\s*"
    r"(?:not|never)\s+(?:ever\s+)?"
    r"(?:looked\s+at|opened|read|reviewed|processed|monitored|checked)\s+by\s+"
    r"(?:anyone|a\s+human|an?\s+employees?|staff|support|compliance|an?\s+agents?|"
    r"our\s+team|the\s+team)|"
    r"(?:nobody|no\s+one|no\s+employee|staff\s+never|support\s+never|"
    r"compliance\s+never|our\s+team\s+never|the\s+team\s+never)\s+"
    r"(?:ever\s+)?(?:looks?\s+at|opens?|reads?|reviews?|processes?|monitors?|checks?)\s+"
    r"(?:it|them|the\s+(?:archive|queue|emails?|messages?|replies|responses?))"
    r")\b",
    re.IGNORECASE,
)
_STRUCTURAL_UNREVIEWED_STORED_RESPONSE_EVIDENCE_RE = re.compile(
    # A response transport is dead when storage is explicitly permanent or
    # unstaffed. Keep the storage predicate and proof of non-review in the same
    # clause so ordinary retention followed by a real staff review stays safe.
    r"\b(?:(?:all|every|any)\s+)?(?:(?:inbound|incoming)\s+)?"
    r"(?:emails?|sms|texts?|messages?|repl(?:y|ies)|responses?)\s+"
    r"(?:(?:is|are|was|were|has\s+been|have\s+been|will\s+be|would\s+be|"
    r"remain|remains|remained)\s+)?"
    r"(?:"
    r"fil(?:e|es|ed|ing)\s+away|"
    r"(?:archiv(?:e|es|ed|ing)|stor(?:e|es|ed|ing)|queu(?:e|es|ed|ing)|"
    r"retain(?:s|ed|ing)?|fil(?:e|es|ed|ing)|sit(?:s|ting)?|sat|"
    r"languish(?:es|ed|ing)?)"
    r"[^.!?;:–—]{0,80}\b(?:"
    r"(?:forever|permanently|indefinitely)|"
    r"(?:unseen|unread|unreviewed|unchecked)"
    r"(?:\s+by\s+(?:anyone|humans?|employees?|(?:a|any)\s+humans?|"
    r"an?\s+employees?|staff|"
    r"our\s+team|the\s+team|support|compliance|an?\s+agents?))?|"
    r"without\s+(?:"
    r"(?:anyone|humans?|employees?|(?:a|any)\s+humans?|an?\s+employees?|"
    r"staff|our\s+team|"
    r"the\s+team|support|compliance|an?\s+agents?)\s+(?:ever\s+)?"
    r"(?:looking\s+at|opening|reading|reviewing|processing|monitoring|checking|seeing)"
    r"(?:\s+(?:it|them))?|"
    r"(?:(?:a|any)\s+)?(?:human|staff|employee|team)\s+review"
    r")|"
    r"with\s+no\s+(?:(?:human|staff|employee|team)\s+)?review|"
    r"(?:where|while|but|yet|and)\s+"
    r"(?:nobody|no\s+one|no\s+employee|no\s+staff|staff\s+never|"
    r"support\s+never|compliance\s+never|our\s+team\s+never|"
    r"the\s+team\s+never)\s+(?:ever\s+)?"
    r"(?:looks?\s+at|opens?|reads?|reviews?|processes?|monitors?|checks?)\s+"
    r"(?:it|them|the\s+(?:archive|queue|emails?|messages?|replies|responses?))"
    r")"
    r")\b",
    re.IGNORECASE,
)
_STRUCTURAL_UNSEEN_RESPONSE_EVIDENCE_RE = re.compile(
    # Bind an inbound response object directly to an unread/unseen state. This
    # covers ordinary active, passive, and copula-elided generated language;
    # the shared staffed-delivery reconciliation below still permits a real
    # same-clause staff read (for example, ``left unread at first, then staff
    # reviews them``).
    r"\b(?:(?:all|every|any)\s+)?(?:(?:inbound|incoming)\s+)?"
    r"(?:emails?|sms|texts?|messages?|repl(?:y|ies)|responses?)\s+"
    r"(?:"
    r"(?:(?:is|are|was|were|has\s+been|have\s+been|will\s+be|would\s+be)\s+)?"
    r"(?:left|kept|remain(?:s|ed)?|stayed)\s+(?:unread|unseen|unreviewed|unchecked)|"
    r"(?:go|goes|went|have\s+gone|has\s+gone)\s+(?:unread|unseen|unreviewed|unchecked)|"
    r"(?:(?:is|are|was|were|has\s+been|have\s+been|will\s+be|would\s+be)\s+)?"
    r"never\s+(?:seen|read|reviewed|opened|checked|monitored)|"
    r"never\s+(?:get|gets|got)\s+(?:seen|read|reviewed|opened|checked|monitored)"
    r")\b",
    re.IGNORECASE,
)
_STRUCTURAL_UNATTENDED_RESPONSE_EVIDENCE_RE = re.compile(
    r"\b(?:(?:all|every|any)\s+)?(?:(?:inbound|incoming)\s+)?"
    r"(?:emails?|sms|texts?|messages?|repl(?:y|ies)|responses?|calls?)\s+"
    r"(?:"
    r"(?:(?:is|are|was|were|has\s+been|have\s+been|will\s+be|would\s+be)\s+)?"
    r"(?:being\s+)?(?:neglect(?:s|ed|ing)?|ignored|unattended|untouched)|"
    r"(?:get|gets|got)\s+(?:neglected|ignored|unattended|untouched)|"
    r"(?:receive(?:s|d)?|get(?:s|ting)?|got|have|has|had)\s+(?:no|zero)\s+"
    r"(?:(?:human|staff|employee|team)\s+)?(?:attention|review|handling|reply|response|answer)|"
    r"(?:(?:is|are|was|were|has\s+been|have\s+been|will\s+be|would\s+be)\s+)?"
    r"not\s+(?:acted\s+(?:on|upon)|attended\s+to|handled|processed|reviewed|read|monitored)|"
    r"(?:fall|falls|fell|fallen|falling)\s+through\s+(?:the\s+)?cracks|"
    r"(?:(?:is|are|was|were|has\s+been|have\s+been|will\s+be|would\s+be)\s+)?"
    r"(?:disregarded|overlooked|buried)|"
    r"(?:(?:is|are|was|were)\s+)?(?:await|awaits|awaited|awaiting)\s+"
    r"(?:(?:human|staff)\s+)?review\s+indefinitely|"
    r"never\s+(?:reach|reaches|reached)\s+(?:staff|our\s+team|the\s+team|a\s+human)|"
    r"(?!(?:do|does|did)\s+not\s+)(?:go|goes|went|gone|going)\s+unanswered|"
    r"(?:(?:is|are|was|were|has\s+been|have\s+been|will\s+be|would\s+be)\s+)?"
    r"(?:pil(?:e|es|ed|ing)|stack(?:s|ed|ing)?|gather(?:s|ed|ing)?|"
    r"accumulat(?:e|es|ed|ing)|build(?:s|ing)?|built)(?:\s+up)?\s+"
    r"(?:unread|unseen|untouched|unreviewed|unhandled)|"
    r"(?:(?:is|are|was|were|has\s+been|have\s+been|will\s+be|would\s+be)\s+)?"
    r"(?!(?:remain(?:s|ed|ing)?)\s+(?:answered|read|reviewed|monitored|handled|"
    r"processed|staffed|attended)\b)"
    r"(?:lie|lies|lay|lain|lying|sit|sits|sat|sitting|languish(?:es|ed|ing)?|"
    r"remain(?:s|ed|ing)?)"
    r"(?:\s+(?:unread|unseen|untouched|unreviewed|unhandled)|"
    r"\s+without\s+(?:(?:human|staff|employee|team)\s+)?review)?|"
    r"collect(?:s|ed|ing)?\s+dust"
    r"(?:\s+without\s+(?:(?:human|staff|employee|team)\s+)?review)?"
    r")\b|"
    r"\b(?:nobody|no\s+one|no\s+employee|no\s+staff(?:\s+member)?|"
    r"no\s+team\s+member)\s+"
    r"(?:(?:is|are|was|were|has|have|had|will|would|will\s+be|would\s+be)\s+)?"
    r"(?:ever\s+)?(?:read(?:s|ing)?|review(?:s|ed|ing)?|handle(?:s|d|ing)?|"
    r"process(?:es|ed|ing)?|monitors?|respond(?:s|ed|ing)?\s+to|"
    r"act(?:s|ed|ing)?\s+(?:on|upon)|"
    r"(?:pay(?:s|ing)?|paid)\s+attention\s+to)\s+"
    r"(?:(?:the|these|those|any|incoming|inbound)\s+)?"
    r"(?:emails?|sms|texts?|messages?|repl(?:y|ies)|responses?)\b",
    re.IGNORECASE,
)
_SAFE_PROVIDER_OPERATION_RE = re.compile(
    r"\b(?:(?:our|the)\s+)?(?:email\s+|mail\s+|sms\s+|telecom\s+)?"
    r"(?:provider|carrier|gateway|platform|system|service)\s*,?\s*"
    r"(?:(?:which|that)\s+)?(?:silently\s+)?"
    r"(?:encrypt(?:s|ed|ing)?|authenticat(?:e|es|ed|ing)|"
    r"virus[- ]scan(?:s|ned|ning)?|scan(?:s|ned|ning)?|compress(?:es|ed|ing)?|"
    r"validat(?:e|es|ed|ing)|normaliz(?:e|es|ed|ing)|pars(?:e|es|ed|ing)|"
    r"log(?:s|ged|ging)?|filter(?:s|ed|ing)?|intercept(?:s|ed|ing)?|"
    r"quarantin(?:e|es|ed|ing))\s+"
    r"(?:(?:(?:all|every|any)\s+)?(?:inbound|incoming)\s+|"
    r"(?:all|every|any)\s+)"
    r"(?:emails?|sms|texts?|messages?|repl(?:y|ies)|responses?)\b",
    re.IGNORECASE,
)
_SIMPLE_SAFE_PROVIDER_OPERATION_RE = re.compile(
    r"\b(?:(?:our|the)\s+)?(?:provider|carrier|gateway|platform|system|service|engine|automation)\s+(?:encrypt(?:s|ed|ing)?|authenticat(?:e|es|ed|ing)|virus[- ]scan(?:s|ned|ning)?|scan(?:s|ned|ning)?|compress(?:es|ed|ing)?|validat(?:e|es|ed|ing)|normaliz(?:e|es|ed|ing)|pars(?:e|es|ed|ing)|log(?:s|ged|ging)?)\s+(?:emails?|sms|texts?|messages?|repl(?:y|ies)|responses?)\b", re.IGNORECASE)
_CLAUSE_BOUNDARY_RE = re.compile(r"[.!?;:–—]+")
_EXPLICIT_REPLACEMENT_CONNECTOR_RE = re.compile(
    r"\b(?:instead|alternatively|rather\s+than|in\s+place\s+of)\b",
    re.IGNORECASE,
)
_REPLACEMENT_CHANNEL_RE = re.compile(
    rf"\b(?:replacement|alternate|alternative)\s+channel\s+(?:is|will\s+be|:)\s+"
    rf"(?P<channel>phone|telephone|email|sms|text|message|"
    rf"{_CONSENT_CHANNEL_RE_FRAGMENT})\b",
    re.IGNORECASE,
)
_CONSENT_EVIDENCE_PATTERNS = (
    _CORE_CONSENT_EVIDENCE_RE,
    _ACTIVE_OPT_OUT_EVIDENCE_RE,
    _QUALIFIED_OPT_OUT_EVIDENCE_RE,
    _RELATIVE_OPT_OUT_EVIDENCE_RE,
    _SUBJECT_OPT_OUT_EVIDENCE_RE,
    _WITHDRAWN_PREFERENCE_EVIDENCE_RE,
    _STOP_EVIDENCE_RE,
    _PASSIVE_STOP_EVIDENCE_RE,
    _BARE_PASSIVE_STOP_EVIDENCE_RE,
    _DIRECT_STOP_INSTRUCTION_EVIDENCE_RE,
    _REFRAIN_INSTRUCTION_EVIDENCE_RE,
    _RECORDED_PAUSE_REQUEST_EVIDENCE_RE,
    _CHANNEL_WITHDRAWAL_REQUEST_EVIDENCE_RE,
    _SUBJECT_CHANNEL_WITHDRAWAL_EVIDENCE_RE,
    _WITHDRAWAL_SYNONYM_EVIDENCE_RE,
    _PASSIVE_WITHDRAWAL_EVIDENCE_RE,
    _STRUCTURAL_CONTACT_WITHDRAWAL_EVIDENCE_RE,
    _SUBJECT_WITHDRAWAL_PREDICATE_EVIDENCE_RE,
    _STRUCTURAL_CONSENT_PROHIBITION_EVIDENCE_RE,
    _EXPLICIT_CHANNEL_WITHDRAWAL_EVIDENCE_RE,
    relationship_evidence.PERFECT_CONSENT_RELATION_EVIDENCE_RE,
    relationship_evidence.ADDITIONAL_CONSENT_STATE_EVIDENCE_RE,
    relationship_evidence.STRUCTURAL_NEGATIVE_CONSENT_STATE_EVIDENCE_RE,
    relationship_evidence.ADDITIONAL_CONSENT_RELATION_EVIDENCE_RE,
    relationship_evidence.FAIL_CLOSED_CONSENT_STATE_EVIDENCE_RE,
    relationship_evidence.CHANNEL_PROHIBITION_STATE_EVIDENCE_RE,
)
_DEAD_CHANNEL_EVIDENCE_PATTERNS = (
    _CORE_DEAD_CHANNEL_EVIDENCE_RE,
    _UNREAD_CHANNEL_EVIDENCE_RE,
    _PASSIVE_UNREAD_CHANNEL_EVIDENCE_RE,
    _RESPONSE_SINK_EVIDENCE_RE,
    _EXPIRING_RESPONSE_EVIDENCE_RE,
    _PRONOUN_RESPONSE_SINK_EVIDENCE_RE,
    _PROVIDER_RESPONSE_SINK_EVIDENCE_RE,
    _PASSIVE_PROVIDER_SINK_EVIDENCE_RE,
    _OVERWRITTEN_RESPONSE_EVIDENCE_RE,
    _ACTORLESS_PASSIVE_DESTRUCTIVE_RESPONSE_EVIDENCE_RE,
    _REPLACED_BEFORE_REVIEW_EVIDENCE_RE,
    _UNREVIEWED_PROVIDER_CHANNEL_ACTION_RE,
    _INDEFINITE_PROVIDER_RESPONSE_SINK_RE,
    _UNSURFACED_RESPONSE_EVIDENCE_RE,
    _UNREVIEWED_STORED_RESPONSE_EVIDENCE_RE,
    _UNREVIEWED_ARCHIVED_RESPONSE_EVIDENCE_RE,
    _STRUCTURAL_UNREVIEWED_STORED_RESPONSE_EVIDENCE_RE,
    _STRUCTURAL_UNSEEN_RESPONSE_EVIDENCE_RE,
    _STRUCTURAL_UNATTENDED_RESPONSE_EVIDENCE_RE,
    relationship_evidence.ADDITIONAL_DEAD_RESPONSE_EVIDENCE_RE,
    relationship_evidence.ADDITIONAL_PRONOUN_DEAD_RESPONSE_EVIDENCE_RE,
    relationship_evidence.DESTRUCTIVE_OR_IGNORED_RESPONSE_EVIDENCE_RE,
    relationship_evidence.FAIL_CLOSED_RESPONSE_FATE_EVIDENCE_RE,
    relationship_evidence.FAIL_CLOSED_RESPONSE_DESTINATION_EVIDENCE_RE,
)


def _same_clause_bounds(value: str, match: re.Match[str]) -> tuple[int, int]:
    prefix = value[: match.start()]
    previous = list(_CLAUSE_BOUNDARY_RE.finditer(prefix))
    start = previous[-1].end() if previous else 0
    following = _CLAUSE_BOUNDARY_RE.search(value, match.end())
    end = following.start() if following else len(value)
    return start, end


def explicit_replacement_channel_actions(value: str) -> set[str]:
    """Return actions named by a governed replacement-channel declaration."""

    actions: set[str] = set()
    for match in _REPLACEMENT_CHANNEL_RE.finditer(value):
        actions.update(cta_channel_actions(match.group("channel")))
    return actions


def _staffed_delivery_matches_negative(value: str, negative: re.Match[str]) -> bool:
    """Return true only when staffed delivery covers the failed response channel."""
    if negative.re is relationship_evidence.FAIL_CLOSED_RESPONSE_FATE_EVIDENCE_RE and re.search(
        r"\bexpir(?:e|es|ed|ing)\b[^.!?;:–—]{0,50}\bafter\b"
        r"[^.!?;:–—]{0,40}\bstaff\s+archiv(?:e|es|ed|ing)\b",
        negative.group(0),
        re.IGNORECASE,
    ):
        return True
    if re.search(
        r"\b(?:destroy(?:s|ed|ing)?|eras(?:e|es|ed|ing)|delet(?:e|es|ed|ing)|"
        r"purg(?:e|es|ed|ing)|shred(?:s|ded|ding)?|wip(?:e|es|ed|ing)|"
        r"incinerat(?:e|es|ed|ing)|obliterat(?:e|es|ed|ing)|"
        r"vaporiz(?:e|es|ed|ing)|annihilat(?:e|es|ed|ing)|"
        r"expir(?:e|es|ed|ing)|laps(?:e|es|ed|ing)|evaporat(?:e|es|ed|ing)|"
        r"overwrit(?:e|es|ten|ing)|drop(?:s|ped|ping)|discard(?:s|ed|ing)|"
        r"expung(?:e|es|ed|ing)|dispos(?:e|es|ed|ing)\s+of|"
        r"sweep(?:s|ing)?\s+away|swept\s+away|(?:face|faces|faced|facing)\s+"
        r"(?:disposal|deletion|destruction|discarding)|"
        r"(?:doom(?:s|ed|ing)?|destin(?:e|es|ed|ing)|fat(?:e|es|ed|ing)|"
        r"condemn(?:s|ed|ing)?)\s+to\s+"
        r"(?:remain\s+)?(?:unread|unseen|unreviewed|unanswered)|"
        r"trash(?:es|ed|ing)?|bin(?:s|ned|ning)?|blackhol(?:e|es|ed|ing)|"
        r"swallow(?:s|ed|ing)|reject(?:s|ed|ing)|devour(?:s|ed|ing)|consum(?:e|es|ed|ing)|"
        r"abandon(?:s|ed|ing)|buri(?:es|ed|ying)\s+(?:forever|without\s+(?:a\s+)?trace)|"
        r"(?:sink|sinks|sank|sunk)\s+without\s+(?:a\s+)?trace|"
        r"los(?:e|es|t|ing)|vanish(?:es|ed|ing)?|disappear(?:s|ed|ing)?|"
        r"self[- ]destruct(?:s|ed|ing)?|time(?:s|d)?\s+out)\b",
        negative.group(0),
        re.IGNORECASE,
    ):
        return False
    if (
        negative.re is _UNREVIEWED_PROVIDER_CHANNEL_ACTION_RE
        and _SAFE_PROVIDER_OPERATION_RE.search(negative.group(0)) is None
    ):
        return False
    clause_start, clause_end = _same_clause_bounds(value, negative)
    clause = value[clause_start:clause_end]
    if negative.re is relationship_evidence.FAIL_CLOSED_RESPONSE_DESTINATION_EVIDENCE_RE and (
        _SIMPLE_SAFE_PROVIDER_OPERATION_RE.search(clause)
        or _SAFE_PROVIDER_OPERATION_RE.search(clause)
        or _REPLACEMENT_CHANNEL_RE.fullmatch(negative.group(0).strip(" .!?;:–—\t\r\n"))
    ):
        return True
    if relationship_evidence.SAFE_RESPONSE_OPERATION_RE.search(clause):
        return True
    if negative.re in {
        relationship_evidence.FAIL_CLOSED_RESPONSE_FATE_EVIDENCE_RE,
        relationship_evidence.FAIL_CLOSED_RESPONSE_DESTINATION_EVIDENCE_RE,
    } and relationship_evidence.STAFFED_DELIVERY_RE.search(clause):
        return True
    if re.search(
        r"\breplac(?:e|es|ed|ing)\b[^.!?;:–—]{0,90}\bbefore\b"
        r"[^.!?;:–—]{0,50}(?:"
        r"\b(?:staff|a\s+human|an\s+employee|a\s+team\s+member|anyone)\b"
        r"[^.!?;:–—]{0,24}\b(?:can\s+|could\s+|will\s+|would\s+)?"
        r"(?:read|review|see|receive|process|open)(?:s|ed)?\b|"
        r"\b(?:(?:human|staff)\s+)?review\b)",
        clause,
        re.IGNORECASE,
    ):
        return False
    local_negative_end = negative.end() - clause_start
    negative_transports = relationship_evidence.response_transports(negative.group(0))
    for staffed in relationship_evidence.STAFFED_DELIVERY_RE.finditer(clause):
        if (
            staffed.start() >= negative.start() - clause_start
            and staffed.end() <= local_negative_end
        ):
            continue
        if staffed.end() <= local_negative_end:
            delivery_scope = staffed.group(0)
        else:
            delivery_scope = clause[local_negative_end : staffed.end()]
        delivery_transports = relationship_evidence.response_transports(delivery_scope)
        if delivery_transports:
            if negative_transports & delivery_transports:
                return True
            continue
        if staffed.start() >= local_negative_end:
            return True
    return False


def negative_borrower_cta_evidence(value: str) -> list[re.Match[str]]:
    """Return independent consent/dead-channel evidence spans in source order."""

    candidates = [
        match
        for pattern in _CONSENT_EVIDENCE_PATTERNS
        for match in pattern.finditer(value)
        if pattern is not relationship_evidence.FAIL_CLOSED_CONSENT_STATE_EVIDENCE_RE
        or relationship_evidence.is_fail_closed_consent_evidence(match)
    ]
    candidates.extend(
        match
        for pattern in _DEAD_CHANNEL_EVIDENCE_PATTERNS
        for match in pattern.finditer(value)
        if not _staffed_delivery_matches_negative(value, match)
    )
    candidates.sort(
        key=lambda match: (match.start(), -(match.end() - match.start())),
    )
    evidence: list[re.Match[str]] = []
    for match in candidates:
        if any(
            existing.start() <= match.start() and existing.end() >= match.end()
            for existing in evidence
        ):
            continue
        evidence.append(match)
    return evidence

staffed_delivery_reconciles_negative = _staffed_delivery_matches_negative


def contains_borrower_cta_contradiction(value: str) -> bool:
    """Return whether a requested contact action conflicts with channel evidence."""
    negative_matches = negative_borrower_cta_evidence(value)
    positive_matches = explicit_borrower_contact_actions(value)
    for positive, positive_actions in positive_matches:
        for negative in negative_matches:
            negative_actions = negative_actions_for_positive(
                value,
                negative_match=negative,
                positive_match=positive,
            )
            if "contact" in negative_actions and positive_actions:
                return True
            if "message" in negative_actions and positive_actions & {"message", "reply", "text"}:
                return True
            if (negative_actions - {"contact"}) & positive_actions:
                return True
            if not negative_actions:
                return True
            if negative.start() < positive.start():
                bridge = value[negative.end() : positive.start()]
                tail = value[positive.end() :]
                boundary = _CLAUSE_BOUNDARY_RE.search(tail)
                clause_tail = tail[: boundary.start()] if boundary else tail
                replacement_scope = f"{bridge} {clause_tail}"
                replacement_actions = explicit_replacement_channel_actions(replacement_scope)
                if (
                    _EXPLICIT_REPLACEMENT_CONNECTOR_RE.search(replacement_scope) is None
                    and not positive_actions & replacement_actions
                ):
                    return True
    return False
