"""Canonical action and response-channel semantics for borrower CTAs."""

import re

BORROWER_CTA_ACTION_RE_FRAGMENT = (
    r"(?:contact|call|telephone|email|text|reply|respond|communicate|reach\s+out|"
    r"reach\s+us\s+by\s+email|get\s+in\s+touch(?:\s+with\s+us)?|"
    r"drop\s+us\s+a\s+line|connect\s+with\s+us|"
    r"send\s+us\s+(?:an?\s+)?(?:message|email|text)|message\s+us|write\s+to\s+us|"
    r"schedule|request|start|review|book|arrange|"
    r"compare|explore|discuss|talk|speak)"
)
BORROWER_CTA_NEGATIVE_ACTION_RE_FRAGMENT = (
    r"(?:contact(?:s|ed|ing)?|communicat(?:e|ed|ing|ion|ions)|reach(?:ed|ing)?\s+out|"
    r"call(?:s|ed|ing)?|email(?:s|ed|ing)?|text(?:s|ed|ing)?|"
    r"send(?:s|ing|sent)?\s+us\s+(?:an?\s+)?(?:messages?|emails?|texts?)|"
    r"messag(?:e|es|ed|ing)(?:\s+us)?|writ(?:e|es|ing|ten)\s+to\s+us|"
    r"repl(?:y|ies|ied|ying)|responses?|respond(?:ed|ing)?|"
    r"schedul(?:e|ed|ing)|book(?:ed|ing)?|arrang(?:e|ed|ing)|"
    r"request(?:ed|ing)?|start(?:ed|ing)?|review(?:ed|ing)?|compar(?:e|ed|ing)|"
    r"explor(?:e|ed|ing)|discuss(?:ed|ing|ions?)?|conversations?|bookings?|appointments?|"
    r"talk(?:ed|ing)?|speak(?:ing)?|telephone(?:d|s|ing)?(?:\s+us)?|"
    r"reach(?:ed|ing)?\s+us\s+by\s+email|"
    r"(?:get|gets|got|getting)\s+in\s+touch(?:\s+with\s+us)?|"
    r"drop(?:s|ped|ping)?\s+us\s+a\s+line|connect(?:s|ed|ing)?\s+with\s+us|"
    r"telephone\s+service|phone\s+(?:number|service|line)s?)"
)
BORROWER_CTA_TERM_RE_FRAGMENT = (
    rf"(?:{BORROWER_CTA_NEGATIVE_ACTION_RE_FRAGMENT}|communication|channels?|"
    r"inbox(?:es)?|mailbox(?:es)?|this\s+number|options?|action|"
    r"permission|authorization|consent)"
)
BORROWER_CTA_POSITIVE_STATUS_RE_FRAGMENT = (
    r"(?:available|open|offered|permitted|required|allowed|needed|possible|recommended|"
    r"advisable|encouraged|scheduled|necessary|requested|expected|feasible|active|accessible|"
    r"accepted|valid|authorized|processed|supported|monitored|read|answered|received|staffed|"
    r"delivered|going\s+through|in\s+service|online|operational|"
    r"work(?:ed|ing|s)?|proceed|an?\s+option)"
)
BORROWER_CTA_NEGATIVE_STATUS_RE_FRAGMENT = (
    r"(?:unavailable|closed|prohibited|forbidden|disallowed|discontinued|disabled|cancelled|"
    r"canceled|impossible|discouraged|prevented|barred|blocked|suspended|unnecessary|"
    r"unauthorized|revoked|rescinded|retracted|voided|rejected|invalid|denied|withdrawn|"
    r"ineligible|expired|infeasible|inactive|inaccessible|failed|lapsed|offline|paused|"
    r"unsupported|unmonitored|unattended|unusable|disconnected|unanswered|ignored|read[- ]only|"
    r"dead|defunct|retired|abandoned|not\s+in\s+use|outbound[- ]only|undeliverable|purged|"
    r"out\s+of\s+service)"
)
BORROWER_CTA_CONSENT_STATE_RE_FRAGMENT = (
    r"(?:\b(?:(?:the\s+)?(?:borrower|customer|recipient|client|applicant)|you|we)\s+"
    r"(?:(?:who|that)\s+)?"
    r"(?:(?:has|have|had)\s+)?(?:opted\s+out(?:\s+(?:of|from))?|"
    r"unsubscribed(?:\s+from)?)\s+(?:(?:all|any)\s+)?(?:marketing\s+)?"
    r"(?:telephone\s+calls?|phone\s+calls?|calls?|emails?|sms|texts?|messages?|"
    r"replies|responses?|contact|communications?)\b|"
    r"\b(?:(?:the\s+)?(?:borrower|customer|recipient|client|applicant)|you|we)\s+"
    r"(?:revoked|rescinded|withdrew|withdrawn|removed|terminated|cancelled|canceled)\s+"
    r"(?:(?:the|your|our|their)\s+)?(?:telephone|phone|call|email|sms|text|message|"
    r"reply|response|contact|communication)?\s*"
    r"(?:permission|authorization|auth|consent)\b"
    r"(?:\s+(?:for|to)\s+(?:telephone\s+calls?|phone\s+calls?|calls?|emails?|sms|"
    r"texts?|messages?|replies|responses?|contact|communications?))?|"
    r"\b(?:(?:your|our|their|the\s+borrower['’]s|the\s+customer['’]s)\s+)?"
    r"(?:(?:telephone|phone|call|email|sms|text|message|reply|response|contact|"
    r"communication)\s+)?(?:permission|authorization|auth|consent)\b"
    r"(?:\s+(?:for|to)\s+(?:telephone\s+calls?|phone\s+calls?|calls?|emails?|sms|"
    r"texts?|messages?|replies|responses?|contact|communications?))?\s+"
    r"(?:(?:is|was|has\s+been|had\s+been)\s+)?"
    r"(?:revoked|rescinded|withdrawn|removed|terminated|cancelled|canceled|expired)|"
    r"\b(?:(?:the\s+)?(?:borrower|customer|recipient|client|applicant)|you|we)\s+"
    r"(?:(?:does|do|did)\s+not|no\s+longer)\s+(?:have|hold)\s+"
    r"(?:(?:telephone|phone|call|email|sms|text|message|reply|response|contact|"
    r"communication)\s+)?(?:permission|authorization|auth|consent)\b"
    r"(?:\s+(?:for|to)\s+(?:telephone\s+calls?|phone\s+calls?|calls?|emails?|sms|"
    r"texts?|messages?|replies|responses?|contact|communications?))?|"
    r"\b(?:(?:the\s+)?(?:borrower|customer|recipient|client|applicant)|you)\s+"
    r"declined\s+(?:any\s+)?further\s+"
    r"(?:telephone\s+calls?|phone\s+calls?|calls?|emails?|sms|texts?|messages?|"
    r"replies|responses?|contact|communications?)\b|"
    r"\b(?:(?:the\s+)?(?:borrower|customer|recipient|client|applicant)|you)\s+"
    r"(?:(?:has|have|had)\s+)?(?:asked|requested|told|instructed|directed|demanded|"
    r"ordered|insisted)\s+"
    r"(?:(?:us|our\s+team|the\s+team)\s+|(?:that\s+)?(?:we|our\s+team|the\s+team)\s+)"
    r"(?:(?:not|never)\s+to|(?:not|never|no\s+longer)|to\s+(?:stop|cease|quit|discontinue)|"
    r"(?:stop|cease|quit|discontinue))\s+"
    r"(?:(?:all|any|further)\s+)?(?:marketing\s+)?"
    r"(?:contact(?:ing)?|call(?:s|ing)?|email(?:s|ing)?|text(?:s|ing)?|messag(?:e|es|ing)|"
    r"repl(?:y|ying)|respond(?:ing)?|communicat(?:e|ing|ions?)|reach(?:ing)?\s+out|"
    r"send(?:ing)?\s+(?:(?:you|them)\s+)?(?:any\s+(?:more\s+)?|further\s+)?"
    r"(?:emails?|texts?|messages?))\b|"
    # Audited indirect withdrawal language. These forms still bind the
    # speaker, the withdrawn channel, and an explicit stop/no-more directive.
    r"\b(?:(?:the\s+)?(?:borrower|customer|recipient|client|applicant)|you)\s+"
    r"(?:said|stated|confirmed|explained|made\s+clear)\s+(?:that\s+)?"
    r"(?:(?:(?:we|our\s+team|the\s+team)\s+)?(?:must|should|need\s+to|have\s+to)\s+"
    r"(?:stop|cease|quit|discontinue)\s+"
    r"(?:contacting|calling|emailing|texting|messaging|replying|responding|communicating)"
    r"(?:\s+(?:you|them))?|(?:calls?|emails?|texts?|messages?|replies|responses?|"
    r"communications?)\s+(?:must|should|need\s+to|have\s+to)\s+"
    r"(?:stop|cease|end))\b|"
    r"\b(?:(?:the\s+)?(?:borrower|customer|recipient|client|applicant)|you)\s+"
    r"(?:asked|requested|demanded|instructed)\s+(?:no|not\s+any)\s+"
    r"(?:more|further)\s+(?:telephone\s+calls?|phone\s+calls?|calls?|emails?|sms|"
    r"texts?|messages?|replies|responses?|contact|communications?)\b|"
    r"\b(?:(?:the\s+)?(?:borrower|customer|recipient|client|applicant)|you)\s+"
    r"expressed\s+(?:(?:a|their|your)\s+)?preference\s+"
    r"(?:not\s+to\s+(?:receive|accept)\s+(?:telephone\s+calls?|calls?|emails?|mail|"
    r"sms|texts?|messages?|replies|responses?|contact|communications?)|"
    r"(?:that\s+)?(?:we|our\s+team|the\s+team)\s+(?:not\s+to|not|never)\s+"
    r"(?:contact|call|email|text|message|reply|respond|communicate)(?:ing)?\s*(?:you|them)?)\b|"
    r"\b(?:we|our\s+team|the\s+team)\s+honored\s+"
    r"(?:(?:your|their|the\s+(?:borrower|customer|recipient|client|applicant)['’]s)\s+)?"
    r"(?:request|preference|withdrawal|opt[- ]out)\s+"
    r"(?:to\s+(?:stop|cease|quit|discontinue)\s+(?:contact(?:ing)?|call(?:ing)?|"
    r"email(?:ing)?|text(?:ing)?|messag(?:e|ing)|repl(?:y|ying)|respond(?:ing)?)|"
    r"(?:from|of)\s+(?:telephone\s+calls?|calls?|emails?|mail|sms|texts?|messages?|"
    r"replies|responses?|contact|communications?))\b|"
    r"\b(?:(?:the\s+)?(?:borrower|customer|recipient|client|applicant)|you)\s+"
    r"forbade\s+(?:us|our\s+team|the\s+team)\s+from\s+"
    r"(?:contacting|calling|emailing|texting|messaging|replying|responding|communicating)\b|"
    r"\b(?:(?:the\s+)?(?:borrower|customer|recipient|client|applicant)|you)\s+"
    r"(?:refused|declined|denied)\s+(?:(?:their|your)\s+)?"
    r"(?:(?:telephone|phone|call|email|sms|text|message|contact|communication)\s+)?"
    r"(?:permission|authorization|auth|consent)\s+(?:for|to)\s+"
    r"(?:telephone\s+calls?|phone\s+calls?|calls?|emails?|sms|texts?|messages?|"
    r"contact|communications?)\b|"
    r"\b(?:(?:the\s+)?(?:borrower|customer|recipient|client|applicant)|you)\s+"
    r"(?:asked|requested|demanded|instructed)\s+(?:(?:us|our\s+team|the\s+team)\s+)?"
    r"(?:(?:for\s+)?(?:(?:their|your)\s+)?(?:removal|deletion|suppression)\s+"
    r"(?:from|of)|to\s+be\s+(?:removed|deleted|suppressed|excluded)\s+from)\s+"
    r"(?:(?:our|the|your)\s+)?(?:email|mailing|contact)\s+lists?\b|"
    r"\b(?:(?:the\s+)?(?:borrower|customer|recipient|client|applicant)|you)\s+"
    r"(?:asked|requested|demanded|instructed)\s+(?:(?:us|our\s+team|the\s+team)\s+)?"
    r"(?:(?:for\s+)?(?:(?:their|your)\s+)?(?:removal|deletion|suppression)\s+"
    r"(?:from|of)|to\s+be\s+(?:removed|deleted|suppressed|excluded)\s+from)\s+"
    r"(?:(?:our|the|your)\s+)?(?:marketing\s+emails?|email\s+(?:outreach|communications?))\b|"
    r"\b(?:remove|take)\s+(?:me|us|the\s+(?:borrower|customer|recipient|client|applicant))"
    r"\s+(?:off|from)\s+(?:(?:our|the|your)\s+)?(?:email|mailing|contact)\s+list\b|"
    r"\b(?:(?:the\s+)?(?:borrower|customer|recipient|client|applicant)|you)\s+"
    r"(?:(?:is|are|was|were|has\s+been|have\s+been)\s+)?"
    r"(?:(?:added|placed)\s+to|on)\s+(?:(?:our|the|your)\s+)?"
    r"(?:do[- ]not[- ]call|dnc)\s+list\b|"
    # Action-bound withdrawal directives that use verbs beyond the older
    # stop/cease vocabulary. The withdrawn channel stays inside the match so
    # ``negative_actions_for_positive`` can compare it with the advertised CTA.
    r"\b(?:(?:the\s+)?(?:borrower|customer|recipient|client|applicant)|you)\s+"
    r"(?:asked|requested|told|instructed|directed|demanded|ordered|insisted)\s+"
    r"(?:(?:us|our\s+team|the\s+team)\s+|(?:that\s+)?(?:we|our\s+team|the\s+team)\s+)"
    r"(?:to\s+)?(?:halt|end|suspend|bar|forbid|prohibit|block|disallow)\s+"
    r"(?:(?:all|any|further)\s+)?(?:telephone\s+calls?|phone\s+calls?|calls?|emails?|"
    r"sms|texts?|messages?|contact|communications?)\b|"
    # Borrower-facing generators can emit sentence fragments. Keep the same
    # action/channel semantics when the already-established subject is elided.
    r"\b(?:asked|requested|told|instructed|directed|demanded|ordered|insisted)\s+"
    r"(?:(?:(?:us|our\s+team|the\s+team)\s+|(?:that\s+)?"
    r"(?:we|our\s+team|the\s+team)\s+))?"
    r"(?:to\s+)?(?:halt|end|suspend|bar|forbid|prohibit|block|disallow)\s+"
    r"(?:(?:all|any|further)\s+)?(?:telephone\s+calls?|phone\s+calls?|calls?|emails?|"
    r"sms|texts?|messages?|contact|communications?)\b|"
    r"\b(?:(?:the\s+)?(?:borrower|customer|recipient|client|applicant)|you)\s+"
    r"(?:halt(?:ed|s)|end(?:ed|s)|suspend(?:ed|s)|bar(?:red|s)|forbade|forbids?|"
    r"prohibit(?:ed|s)|block(?:ed|s)|disallow(?:ed|s))\s+"
    r"(?:(?:all|any|further)\s+)?(?:telephone\s+calls?|phone\s+calls?|calls?|emails?|"
    r"sms|texts?|messages?|contact|communications?)\b|"
    r"\b(?:halt(?:ed|s)|end(?:ed|s)|suspend(?:ed|s)|bar(?:red|s)|forbade|forbids?|"
    r"prohibit(?:ed|s)|block(?:ed|s)|disallow(?:ed|s))\s+"
    r"(?:(?:all|any|further)\s+)?(?:telephone\s+calls?|phone\s+calls?|calls?|emails?|"
    r"sms|texts?|messages?|contact|communications?)\b|"
    # "Leave me alone" is an unqualified withdrawal from contact rather than
    # an email/SMS/call-specific preference.
    r"\b(?:(?:the\s+)?(?:borrower|customer|recipient|client|applicant)|you)\s+"
    r"(?:asked|requested|told|instructed|directed|demanded|ordered)\s+"
    r"(?:(?:us|our\s+team|the\s+team)\s+|(?:that\s+)?(?:we|our\s+team|the\s+team)\s+)"
    r"(?:to\s+)?leave\s+(?:you|them|me|the\s+(?:borrower|customer|recipient|client|applicant))\s+alone\b|"
    r"\b(?:asked|requested|told|instructed|directed|demanded|ordered)\s+"
    r"(?:(?:us|our\s+team|the\s+team)\s+|(?:that\s+)?(?:we|our\s+team|the\s+team)\s+)"
    r"(?:to\s+)?leave\s+(?:you|them|me|the\s+(?:borrower|customer|recipient|client|applicant))\s+alone\b|"
    # A DNC registration is call-specific even when the sentence uses a legal
    # prohibition verb instead of saying the borrower is "on the DNC list".
    r"\b(?:(?:your|their|the\s+(?:borrower|customer|recipient|client|applicant)['’]s)\s+)?"
    r"(?:do[- ]not[- ]call|dnc)\s+registration\s+"
    r"(?:prohibits?|forbids?|bars?|blocks?|disallows?)\s+(?:further\s+)?"
    r"(?:telephone\s+calls?|phone\s+calls?|calls?)\b|"
    # Recorded/logged/on-file STOP and opt-out facts are channel withdrawals,
    # not harmless operational narration. A generic opt-out withdraws contact
    # unless its email/phone/SMS channel is stated.
    r"\b(?:(?:an?|the|your|their)\s+)?"
    r"(?:(?:email|sms|text|call|phone|marketing|communication)\s+)?"
    r"(?:stop|opt[- ]out|unsubscribe)\s*(?:request)?\s+"
    r"(?:(?:is|was|has\s+been|had\s+been)\s+)?"
    r"(?:recorded|logged|documented|on\s+file)\b|"
    r"\b(?:(?:your|their|the\s+(?:borrower|customer|recipient|client|applicant)['’]s)\s+)?"
    r"documented\s+(?:(?:email|sms|text|call|phone|marketing|communication)\s+)?"
    r"(?:stop|opt[- ]out|unsubscribe)\s*(?:request)?\b|"
    r"\b(?:we|our\s+team|the\s+team)\s+recorded\s+"
    r"(?:(?:your|their|the\s+(?:borrower|customer|recipient|client|applicant)['’]s)\s+)?"
    r"(?:(?:email|sms|text|call|phone|marketing|communication)\s+)?"
    r"(?:stop|opt[- ]out|unsubscribe)\s*(?:request)?\b|"
    r"\b(?:(?:the\s+)?(?:borrower|customer|recipient|client|applicant)|you)\s+"
    r"(?:(?:who|that)\s+)?"
    r"(?:said|stated|texted|replied|responded)\s+stop\b|"
    r"\bzero\s+further\s+(?:contact|communications?|calls?|emails?|texts?|messages?)\b)"
)
BORROWER_CTA_CHANNEL_STATE_RE_FRAGMENT = (
    r"(?:\b(?:incoming\s+)?(?:emails?|mail|sms|texts?|messages?|replies|responses?|"
    r"calls?|telephone\s+service|phone\s+lines?|inbox(?:es)?|mailbox(?:es)?|"
    r"email\s+address(?:es)?|address(?:es)?|short\s+codes?|this\s+number|the\s+number|"
    r"communication\s+channels?)\b[^.!?;:–—]{0,60}\b"
    r"(?:(?:is|are|was|were|has\s+been|have\s+been|will|would)\s+)?"
    r"(?:auto(?:matically)?[- ]?delet(?:e|ed|es)|delet(?:e|ed|es)|"
    r"discard(?:ed|s)?|purg(?:e|ed|es)|"
    r"(?:silently\s+)?drop(?:ped|s)?|(?:silently\s+)?suppress(?:ed|es)?|"
    r"(?:silently\s+)?filter(?:ed|s)?|"
    r"automatically\s+quarantin(?:e|ed|es)|archived\s+unread|"
    r"reject(?:ed|s)?(?:\s+all)?|bounce(?:d|s)?|(?:be\s+)?returned\s+"
    r"(?:as\s+)?undeliverable|undeliverable|decommissioned|deactivated|reassigned|closed|"
    r"unavailable|offline|dead|defunct|retired|abandoned|not\s+in\s+use|outbound[- ]only|"
    r"write[- ]only|unstaffed|never\s+opened|unopened|no\s+longer\s+exists?|shut\s+down|"
    r"out\s+of\s+service|"
    r"unmonitored|unattended|not\s+deliverable|(?:no\s+longer|doesn['’]t|does\s+not)\s+accepts?|"
    r"not\s+(?:(?:actively|regularly|routinely)\s+)?(?:watched|checked|read|reviewed)|"
    r"(?:isn['’]t|aren['’]t|wasn['’]t|weren['’]t)\s+"
    r"(?:(?:actively|regularly|routinely)\s+)?(?:watched|checked|read|reviewed)|"
    r"unwatched|unchecked|"
    r"unable\s+to\s+(?:receive|accept)|"
    r"(?:cannot|can\s+not|can['’]t)\s+(?:be\s+)?(?:received|accepted|delivered))\b|"
    r"\b(?:this|the|our)\s+(?:(?:reply|email)\s+)?(?:inbox|mailbox|email\s+address|address|phone\s+line|"
    r"telephone\s+line|short\s+code|communication\s+channel)\s+"
    r"(?:(?:is|are|was|were|has\s+been|have\s+been)\s+)?"
    r"(?:auto(?:matically)?[- ]?delet(?:e|ed|es)|delet(?:e|ed|es)|discard(?:ed|s)?|purg(?:e|ed|es)|"
    r"(?:silently\s+)?drop(?:ped|s)?|(?:silently\s+)?suppress(?:ed|es)?|"
    r"(?:silently\s+)?filter(?:ed|s)?|"
    r"reject(?:ed|s)?(?:\s+all)?|closed|dead|defunct|retired|abandoned|not\s+in\s+use|"
    r"outbound[- ]only|write[- ]only|unstaffed|no\s+longer\s+exists?|shut\s+down)"
    r"\b[^.!?;:–—]{0,60}|"
    r"\bthis\s+(?:is|was)\s+(?:an?\s+)?(?:dead|defunct|retired|abandoned|not\s+in\s+use|"
    r"outbound[- ]only)\s+"
    r"(?:inbox|mailbox|email\s+address|address|phone\s+line|communication\s+channel)\b|"
    r"\b(?:we|our\s+team|the\s+team|this\s+inbox|the\s+inbox|this\s+mailbox|"
    r"the\s+mailbox)\s+(?:cannot|can\s+not|can['’]t|no\s+longer)\s+"
    r"(?:receive|accept|process)\s+(?:incoming\s+)?"
    r"(?:emails?|mail|sms|texts?|messages?|replies|responses?|calls?|contact)\b|"
    r"\b(?:we|our\s+team|the\s+team)\s+"
    r"(?:auto(?:matically)?[- ]?delet(?:e|ed|es)|discard(?:ed|s)?|"
    r"reject(?:ed|s)?|decommissioned|deactivated|reassigned|closed)\b"
    r"[^.!?;:–—]{0,60}\b(?:emails?|mail|sms|texts?|messages?|replies|responses?|"
    r"calls?|inbox|mailbox|email\s+address|phone\s+line|telephone\s+line|"
    r"short\s+code|this\s+number|communication\s+channel)\b|"
    r"\b(?:emails?|messages?|replies|responses?)\s+(?:go|goes|route|routes)\s+"
    r"(?:to\s+)?nowhere\b|"
    r"\brepl(?:y|ies)\s+routing\s+"
    r"(?:(?:is|are|was|were|has\s+been|have\s+been)\s+)?disabled\b|"
    r"\b(?:emails?|messages?|replies|responses?)\s+"
    r"(?:(?:never|(?:will|would|can|could)\s+never|"
    r"(?:do|does|will|would|can|could)\s+not|don['’]t|doesn['’]t|"
    r"won['’]t|can['’]t|couldn['’]t)\s+(?:ever\s+)?(?:reach|arrive\s+at)|"
    r"(?:never|(?:will|would|can|could)\s+never|"
    r"(?:will|would|can|could)\s+not|won['’]t|can['’]t|couldn['’]t)\s+"
    r"be\s+(?:seen|read|received|reviewed)\s+by)\s+"
    r"(?:us|our\s+team|the\s+team|anyone|a\s+person)\b|"
    r"\b(?:nobody|no\s+one)\s+(?:(?:will|would|can|could)\s+)?"
    r"(?:see|read|receive|review|monitor)\s+"
    r"(?:(?:your|the|a|any)\s+)?(?:emails?|messages?|repl(?:y|ies)|responses?)\b|"
    r"\b(?:replies|responses?)\s+(?:cannot|can\s+not|can['’]t|do\s+not|don['’]t)\s+"
    r"(?:ever\s+)?reach\s+(?:a\s+)?human\b|"
    r"\bno\s+(?:employee|staff\s+member|team\s+member|human)\s+"
    r"(?:reads?|reviews?|monitors?|sees?)\s+(?:this\s+|the\s+|our\s+)?"
    r"(?:inbox|mailbox|emails?|messages?|replies|responses?)\b|"
    r"\b(?:this\s+|the\s+|our\s+)?(?:inbox|mailbox)\s+"
    r"(?:accepts?|receives?)\s+(?:incoming\s+)?(?:emails?|messages?|replies|responses?)"
    r"[^.!?;:–—]{0,40}\b(?:but|yet|while)\b[^.!?;:–—]{0,40}\b"
    r"(?:nobody|no\s+one)\s+(?:ever\s+)?(?:looks?\s+at|opens?|reads?|reviews?)\s+"
    r"(?:it|them|the\s+(?:inbox|mailbox|emails?|messages?|replies|responses?))\b|"
    # Provider/carrier controls are dead channels when they suppress the
    # inbound response itself; the reply noun keeps the rule CTA-specific.
    r"\b(?:our\s+|the\s+)?(?:email\s+|mail\s+|sms\s+|telecom\s+)?"
    r"(?:provider|carrier)\s+(?:silently\s+)?"
    r"(?:suppress(?:es|ed|ing)?|filter(?:s|ed|ing)?|block(?:s|ed|ing)?|"
    r"blackhol(?:e|es|ed|ing)|"
    r"drop(?:s|ped|ping)?|reject(?:s|ed|ing)?)\s+"
    r"(?:(?:all|every|any)\s+)?(?:inbound|incoming)\s+"
    r"(?:emails?|messages?|repl(?:y|ies)|responses?)\b)"
)
_ACTION_TOKEN_RE = re.compile(
    rf"\b(?P<action>{BORROWER_CTA_NEGATIVE_ACTION_RE_FRAGMENT})\b",
    re.IGNORECASE,
)
_CLAUSE_BOUNDARY_RE = re.compile(r"[.!?;:–—]+")
_NEGATIVE_ACTION_CONTINUATION_RE = re.compile(
    rf"^(?P<continuation>(?:\s*(?:,\s*(?:(?:or|and)\s+)?|(?:or|and)\s+)"
    rf"(?:{BORROWER_CTA_NEGATIVE_ACTION_RE_FRAGMENT})(?:\s+us)?){{1,4}})",
    re.IGNORECASE,
)
_CONTACT_RECIPIENT_RE_FRAGMENT = (
    r"(?:us|you|me|him|her|them|"
    r"(?:(?:the|this|that|these|those)\s+)?(?:borrower|recipient|customer|applicant|"
    r"homeowner|prospect|client)s?)"
)
_EXPLICIT_BORROWER_CONTACT_ACTION_RE = re.compile(
    rf"\b(?:(?P<direct>contact|call|email|text|message)\s+"
    rf"{_CONTACT_RECIPIENT_RE_FRAGMENT}|"
    rf"(?P<reply>reply)\s+(?:yes|to\s+{_CONTACT_RECIPIENT_RE_FRAGMENT}|"
    r"to\s+(?:review|discuss|compare|explore)\b)|"
    rf"send\s+{_CONTACT_RECIPIENT_RE_FRAGMENT}\s+"
    rf"(?:(?:an?|the)\s+)?(?P<sent>text|email|message)|"
    rf"{_CONTACT_RECIPIENT_RE_FRAGMENT}\s+"
    rf"(?:should|must|will|would|can|could|may|might)\s+be\s+"
    rf"(?P<passive>contacted|called|emailed|texted|messaged)|"
    rf"{_CONTACT_RECIPIENT_RE_FRAGMENT}\s+"
    rf"(?:should|must|will|would|can|could|may|might)\s+"
    rf"(?:receive|get)\s+(?:(?:an?|the)\s+)?"
    rf"(?P<received>call|email|text|message))\b",
    re.IGNORECASE,
)
_NEGATED_CONTACT_DIRECTIVE_PREFIX_RE = re.compile(
    r"\b(?:do\s+not|don['’]t|never|without|avoid|refrain\s+from)\s+$",
    re.IGNORECASE,
)
_GLOBAL_WITHDRAWAL_ACTION_RE = re.compile(
    r"\b(?:(?:the\s+)?(?:borrowers?|customers?|recipients?|clients?|applicants?|"
    r"homeowners?|prospects?)|i|we|you|they|he|she)\s+"
    r"(?:"
    r"(?:asked|requested|demanded|directed|insisted|said|stated)\s+"
    r"(?:(?:that\s+)?(?:they|he|she)\s+(?:should\s+)?)?"
    r"(?:to\s+)?be\s+left\s+alone|"
    r"(?:want|wants|wanted|wish|wishes|wished|prefer|prefers|preferred)\s+"
    r"(?:to\s+)?be\s+left\s+alone|"
    r"(?:asked\s+for|requested|demanded|directed|imposed|insisted\s+on)\s+"
    r"(?:(?:complete|continued|future|ongoing|total)\s+)?radio\s+silence"
    r")\b",
    re.IGNORECASE,
)


def canonical_cta_action_token(token: str) -> str | None:
    """Collapse action morphology into governed action families."""

    folded = token.casefold()
    if folded.startswith("send"):
        if "email" in folded:
            return "email"
        if "text" in folded:
            return "text"
        return "message"
    for stem, canonical in (
        ("contact", "contact"),
        ("communicat", "contact"),
        ("reach", "contact"),
        ("call", "call"),
        ("telephone", "call"),
        ("phone", "call"),
        ("email", "email"),
        ("text", "text"),
        ("writ", "message"),
        ("repl", "reply"),
        ("response", "reply"),
        ("respond", "reply"),
        ("messag", "message"),
        ("schedul", "initiate"),
        ("book", "initiate"),
        ("arrang", "initiate"),
        ("booking", "initiate"),
        ("appointment", "initiate"),
        ("request", "initiate"),
        ("start", "initiate"),
        ("review", "review"),
        ("compar", "compare"),
        ("explor", "explore"),
        ("discuss", "conversation"),
        ("conversation", "conversation"),
        ("talk", "conversation"),
        ("speak", "conversation"),
        ("get", "contact"),
        ("connect", "contact"),
        ("drop", "message"),
    ):
        if folded.startswith(stem):
            return canonical
    return None


def canonical_cta_actions(value: str) -> set[str]:
    """Return every governed action family present in text."""

    return {
        canonical
        for match in _ACTION_TOKEN_RE.finditer(value)
        if (canonical := canonical_cta_action_token(match.group("action"))) is not None
    }


def explicit_borrower_contact_actions(
    value: str,
) -> list[tuple[re.Match[str], set[str]]]:
    """Return bounded borrower/recipient-directed contact actions in prose."""

    actions: list[tuple[re.Match[str], set[str]]] = []
    for match in _EXPLICIT_BORROWER_CONTACT_ACTION_RE.finditer(value):
        prefix = value[max(0, match.start() - 32) : match.start()]
        if _NEGATED_CONTACT_DIRECTIVE_PREFIX_RE.search(prefix):
            continue
        token = (
            match.group("direct")
            or match.group("reply")
            or match.group("sent")
            or match.group("passive")
            or match.group("received")
        )
        canonical = canonical_cta_action_token(token)
        if canonical is not None:
            actions.append((match, {canonical}))
    return actions


def _channel_actions(clause: str) -> set[str]:
    actions: set[str] = set()
    if _GLOBAL_WITHDRAWAL_ACTION_RE.search(clause):
        actions.add("contact")
    if re.search(
        r"\b(?:channels?|communications?|correspondence|"
        r"no[- ]contact|do[- ]not[- ]disturb)\b",
        clause,
        re.IGNORECASE,
    ):
        actions.add("contact")
    if re.search(r"\boutreach\b", clause, re.IGNORECASE) and not re.search(
        r"\b(?:email|sms|text|call|phone|telephone)\s+outreach\b",
        clause,
        re.IGNORECASE,
    ):
        actions.add("contact")
    if re.search(
        r"\b(?:permission|authorization|consent)\b", clause, re.IGNORECASE
    ) and not re.search(
        r"\b(?:calls?|phones?|telephone|emails?|texts?|messages?|replies|responses?)\b",
        clause,
        re.IGNORECASE,
    ):
        actions.add("contact")
    if re.search(r"\boptions?\b", clause, re.IGNORECASE):
        actions.add("review")
    if re.search(
        r"\b(?:inbox(?:es)?|mailbox(?:es)?|email\s+address(?:es)?|"
        r"address(?:es)?|incoming\s+mail)\b",
        clause,
        re.IGNORECASE,
    ):
        actions.update({"email", "reply"})
    if re.search(
        r"\b(?:messages?|replies|responses?|messaging|incoming\s+messages?)\b",
        clause,
        re.IGNORECASE,
    ):
        actions.update({"message", "reply", "text"})
    if re.search(r"\btexts?\b", clause, re.IGNORECASE):
        actions.update({"reply", "text"})
    if re.search(r"\b(?:sms|short\s+codes?)\b", clause, re.IGNORECASE):
        actions.update({"reply", "text"})
    if re.search(r"\bemails?\b", clause, re.IGNORECASE):
        actions.add("email")
        if re.search(
            r"\b(?:inbound|incoming)\s+emails?\b|"
            r"\bemails?\b[^.!?;:–—]{0,48}\b(?:indefinitely|never\s+"
            r"(?:surfaced|delivered|routed|forwarded|released))\b",
            clause,
            re.IGNORECASE,
        ):
            actions.add("reply")
    if re.search(
        r"\bmessages?\s+to\s+(?:this|the)\s+(?:address|inbox|mailbox)\b",
        clause,
        re.IGNORECASE,
    ):
        actions.update({"email", "message", "reply"})
    if re.search(
        r"\b(?:calls?|phones?|telephoning|this\s+number|telephone\s+service|"
        r"phone\s+(?:numbers?|services?|lines?)|dnc(?:\s+list)?)\b",
        clause,
        re.IGNORECASE,
    ):
        actions.add("call")
    if re.search(
        r"\bleave\s+(?:you|them|me|the\s+(?:borrower|customer|recipient|client|applicant))\s+alone\b",
        clause,
        re.IGNORECASE,
    ):
        actions.add("contact")
    if re.search(r"\bopt[- ]out\b", clause, re.IGNORECASE) and not actions:
        actions.add("contact")
    if re.search(r"\bstop\s+request\b", clause, re.IGNORECASE):
        actions.update({"message", "reply", "text"})
    if re.search(
        r"\bstop\s+(?:(?:was|is|has\s+been|had\s+been)\s+)?"
        r"(?:received|sent|submitted)\s+(?:by|from)\b",
        clause,
        re.IGNORECASE,
    ):
        actions.update({"message", "reply", "text"})
    if re.search(
        r"\b(?:they|he|she|the\s+(?:borrower|customer|recipient|client|applicant))\s+"
        r"(?:told|asked|instructed|directed|ordered)\s+"
        r"(?:us|our\s+team|the\s+team)\s+to\s+stop\b",
        clause,
        re.IGNORECASE,
    ):
        actions.add("contact")
    if re.search(
        r"\b(?:said|stated|sent|texted|wrote|replied|responded)\s+"
        r"(?:us\s+|with\s+)?stop\b",
        clause,
        re.IGNORECASE,
    ):
        actions.update({"message", "reply", "text"})
    if re.search(r"\bunsubscrib(?:e|ed|es|ing)\b", clause, re.IGNORECASE):
        actions.add("email")
    return actions


def cta_channel_actions(clause: str) -> set[str]:
    """Return governed action families implied by explicit channel wording."""

    return _channel_actions(clause) - {"review"}


def negative_actions_for_positive(
    value: str,
    *,
    negative_match: re.Match[str],
    positive_match: re.Match[str],
) -> set[str]:
    """Bind negative copy to its action families and response channel."""

    clause = negative_match.group(0)
    if re.search(r"\b(?:do\s+not|don['’]t|never)\b", clause, re.IGNORECASE):
        continuation = _NEGATIVE_ACTION_CONTINUATION_RE.match(
            value[negative_match.end() :]
        )
        if continuation is not None:
            clause = f"{clause}{continuation.group('continuation')}"
    clause = re.sub(
        r"\b(?:replacement|alternate|alternative)\s+channel\s+"
        r"(?:is|will\s+be|:)\s+(?:phone|calls?|emails?|sms|texts?|messages?)\b",
        " ",
        clause,
        flags=re.IGNORECASE,
    )
    negative_actions = canonical_cta_actions(clause) | _channel_actions(clause)
    if negative_actions:
        return negative_actions
    tail = value[negative_match.start() :]
    boundary = _CLAUSE_BOUNDARY_RE.search(tail)
    clause_end = negative_match.start() + (
        boundary.start() if boundary else len(tail)
    )
    if negative_match.start() < positive_match.start() < clause_end:
        clause_end = positive_match.start()
    clause = value[negative_match.start() : clause_end]
    return canonical_cta_actions(clause) | _channel_actions(clause)
