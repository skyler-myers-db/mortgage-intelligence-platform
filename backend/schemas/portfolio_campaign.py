"""Campaign intelligence and portfolio economics response contracts."""

import re
import sys
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.schemas._validators import (
    assert_no_protected_class_marketing_text,
    contains_confidential_or_internal_text,
    contains_contextual_human_name,
    contains_human_name_shape,
    contains_mechanical_pii_or_raw_identifier,
    contains_prompt_injection_text,
)
from backend.schemas.borrower_copy_claims import (
    contains_unsupported_borrower_qualification_claim,
)
from backend.schemas.borrower_copy_names import (
    contains_borrower_copy_contextual_name,
    remove_allowed_public_titlecase_phrases,
    remove_configured_public_lender_phrase,
)
from backend.schemas.borrower_cta_actions import (
    BORROWER_CTA_ACTION_RE_FRAGMENT as _BORROWER_CTA_ACTION_RE_FRAGMENT,
)
from backend.schemas.borrower_cta_actions import (
    BORROWER_CTA_NEGATIVE_ACTION_RE_FRAGMENT as _BORROWER_CTA_NEGATIVE_ACTION_RE_FRAGMENT,
)
from backend.schemas.borrower_cta_actions import (
    BORROWER_CTA_NEGATIVE_STATUS_RE_FRAGMENT as _BORROWER_CTA_NEGATIVE_STATUS_RE_FRAGMENT,
)
from backend.schemas.borrower_cta_actions import (
    BORROWER_CTA_POSITIVE_STATUS_RE_FRAGMENT as _BORROWER_CTA_POSITIVE_STATUS_RE_FRAGMENT,
)
from backend.schemas.borrower_cta_actions import (
    BORROWER_CTA_TERM_RE_FRAGMENT as _BORROWER_CTA_TERM_RE_FRAGMENT,
)
from backend.schemas.borrower_cta_actions import (
    canonical_cta_action_token as _canonical_cta_action_token,
)
from backend.schemas.borrower_cta_actions import (
    canonical_cta_actions as _canonical_cta_actions,
)
from backend.schemas.borrower_cta_actions import (
    cta_channel_actions as _cta_channel_actions,
)
from backend.schemas.borrower_cta_actions import (
    negative_actions_for_positive as _negative_actions_for_positive,
)
from backend.schemas.borrower_cta_agency import is_borrower_directed_cta
from backend.schemas.borrower_cta_evidence import (
    explicit_replacement_channel_actions,
    negative_borrower_cta_evidence,
    staffed_delivery_reconciles_negative,
)
from backend.schemas.borrower_cta_normalization import (
    normalize_safe_affirmative_cta,
    normalize_safe_lender_invitation_for_name_scan,
)
from backend.schemas.marketing_safety_terms import mask_protected_health_safe_contexts

if TYPE_CHECKING:
    from backend.schemas.portfolio import PortfolioCriteria


_PUBLIC_TEXT_DENYLIST: tuple[str, ...] = (
    r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}",
    r"\b(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}\b",
    r"\b\d{3}-\d{2}-\d{4}\b",
    r"\b\d{9,}\b",
    r"\b\d{1,6}\s+[A-Za-z0-9.'-]+\s+(?:st|street|ave|avenue|rd|road|dr|drive|ln|lane|blvd|boulevard|ct|court|way)\b",
    r"\b(?:raw[_\s-]?clip|owner[_\s-]?name|borrower[_\s-]?name|customer[_\s-]?name|prospect[_\s-]?name|street[_\s-]?address|mailing[_\s-]?address)\b",
    r"\[(?:first|last|full)[_\s-]?name\]",
    r"\{(?:first|last|full)[_\s-]?name\}",
    r"\binsert governed\b",
)
_HUMAN_NAME_SHAPE_RE = re.compile(r"\b[A-Z][a-z]{1,30}\s+(?:[A-Z]\s+)?[A-Z][a-z]{1,30}\b")
_CANONICAL_PUBLIC_PLATFORM_LABELS = frozenset({"Databricks Agent Responses"})
_BORROWER_COPY_UNSUPPORTED_CLAIM_RE = re.compile(
    r"(?:\$|\b\d+(?:\.\d+)?\s*(?:%|percent|bps|basis points?|dollars?)\b|"
    r"\b(?:guarantee(?:d|s)?|pre[- ]?approved|lowest rate|best rate|save money|"
    r"qualif(?:y|ies)(?:\s+for)?|qualified(?!\s+(?:(?:home|lending|loan|mortgage|support)\s+){1,3}"
    r"(?:associates|professionals|representatives|specialists|staff|team)\b)|"
    r"lower (?:your )?(?:monthly )?payment|"
    r"you(?:'re| are| may be| can be) (?:eligible|approved|qualified)|"
    r"your (?:monthly )?payment (?:will|would|can|could) (?:be )?lower|"
    r"instant approval|act now|urgent|limited time|expires? today|final notice)\b|"
    r"\bsav(?:e|es|ed|ing)\s+(?:you\s+)?thousands\b|"
    r"\bthousands(?:\s+of\s+dollars)?\s+(?:in|on)\s+(?:savings?|interest|fees?)\b|"
    r"\b(?:no|zero|\$0)\s+(?:(?:lender|origination|application)\s+)?"
    r"(?:closing[- ]+costs?|fees?)\b|"
    r"\b(?:closing[- ]+costs?|fees?)[- ]free\b|"
    r"\bwithout\s+(?:any\s+)?(?:closing[- ]+costs?|fees?)\b|"
    r"\brate\s+guarantee\b|"
    r"\b(?:will|would|can|could)\s+(?:reduce|lower)\s+"
    r"(?:your\s+|the\s+)?total\s+interest\b|"
    r"\b(?:score|scoring|ranked|ranking|algorithm|model(?:ed)?|propensity|"
    r"segment|signal|trigger|target(?:ed|ing)?|eligible cohort|public record)\b)",
    re.IGNORECASE,
)
_SUMMARY_NUMERIC_CLAIM_RE = re.compile(
    r"(?:[$€£]|\b\d+(?:[,.]\d+)*(?:\.\d+)?\b|\b(?:percent|percentage|bps|basis points?|dollars?)\b)",
    re.IGNORECASE,
)
_BORROWER_COPY_CONDITIONAL_AUTONOMY_RE = re.compile(
    rf"\bno\s+(?:response|action|reply|contact)\s+(?:is\s+)?(?:required|needed)"
    rf"\s+unless\b[^.!?;:–—]{{0,100}}\b{_BORROWER_CTA_ACTION_RE_FRAGMENT}\b",
    re.IGNORECASE,
)
_BORROWER_COPY_SOFT_AUTONOMY_RE = re.compile(
    r"\b(?:no\s+(?:response|action|reply|contact)\b[^.!?;:–—]{0,60}\b"
    r"(?:required|needed)|no\s+(?:need|requirement|obligation)\b[^.!?;:–—]{0,60}\b"
    rf"{_BORROWER_CTA_ACTION_RE_FRAGMENT}|(?:not\s+required|isn['’]t\s+required|"
    r"aren['’]t\s+required|do\s+not\s+have\s+to|don['’]t\s+have\s+to|"
    rf"need\s+not|needn['’]t)[^.!?;:–—]{{0,60}}\b{_BORROWER_CTA_ACTION_RE_FRAGMENT}|"
    rf"(?:{_BORROWER_CTA_ACTION_RE_FRAGMENT}|response|action|options?)\b"
    r"[^.!?;:–—]{0,40}\b"
    r"(?:is|are)\s+not\s+(?:required|needed|necessary))\b",
    re.IGNORECASE,
)
_BORROWER_COPY_CLAUSE_BOUNDARY_RE = re.compile(r"[.!?;:–—]+")
_BORROWER_COPY_ACTION_SCOPE_BOUNDARY_RE = re.compile(
    r"[.!?;:–—]+|(?:,\s*)?\b(?:but|however|yet|although|though)\b|"
    r",\s*\b(?:and|while)\b|\b(?:and|then)\s+(?=(?:we|our|the|a|an|you|"
    r"borrowers?|applicants?|clients?)\b)",
    re.IGNORECASE,
)
_BORROWER_COPY_ALTERNATIVE_ACTION_RE = re.compile(
    rf"\b(?P<left>{_BORROWER_CTA_ACTION_RE_FRAGMENT})\s+or\s+"
    rf"(?P<right>{_BORROWER_CTA_ACTION_RE_FRAGMENT})\b",
    re.IGNORECASE,
)
_BORROWER_COPY_REPLACEMENT_PREFIX_RE = re.compile(
    r"\s*[,;:–—-]*\s*(?:(?:but|and)\s+)?"
    r"(?:(?:you\s+can\s+)?instead|alternatively|as\s+an?\s+alternative)"
    r"\s*,?\s*(?:please\s+)?",
    re.IGNORECASE,
)
_BORROWER_COPY_REPLACEMENT_POSTFIX_RE = re.compile(
    rf"\b(?:(?P<bare>instead(?!\s+of\b)|alternatively|as\s+an?\s+alternative)|"
    rf"(?P<targeted>rather\s+than|instead\s+of)\s+"
    rf"(?P<target>{_BORROWER_CTA_NEGATIVE_ACTION_RE_FRAGMENT}))\b",
    re.IGNORECASE,
)
_BORROWER_COPY_REPLACEMENT_TARGET_RE = re.compile(
    rf"\b(?:rather\s+than|instead\s+of)\s+" rf"{_BORROWER_CTA_NEGATIVE_ACTION_RE_FRAGMENT}\b",
    re.IGNORECASE,
)
_BORROWER_COPY_REPLACEMENT_CONTINUATION_RE = re.compile(
    r"(?:[\s,]+(?:to|a|an|review|schedule|request|start|book|arrange|discussion|"
    r"consultation|appointment|call|conversation|discuss|compare|explore|your|the|these|"
    r"available|current|mortgage|loan|refinance|home[- ]equity|options?|today|now)){0,16}[\s,]*",
    re.IGNORECASE,
)
_BORROWER_COPY_NEGATED_CTA_RE = re.compile(
    rf"{_BORROWER_COPY_CONDITIONAL_AUTONOMY_RE.pattern}|"
    rf"\b(?:we|our\s+team|[A-Za-z][A-Za-z&.'-]*(?:\s+[A-Za-z][A-Za-z&.'-]*){{0,3}})\s+"
    rf"(?:(?:did|do|does|have|has)\s+not|didn['’]t|don['’]t|doesn['’]t|"
    rf"haven['’]t|hasn['’]t|never)\s+"
    rf"(?:invite|invited|encourage|encouraged|ask|asked|urge|urged|recommend|recommended|"
    rf"advise|advised|request|requested)\s+(?:that\s+)?"
    rf"(?:you|borrowers?|homeowners?|applicants?|clients?|recipients?|customers?)\s+"
    rf"(?:to\s+)?{_BORROWER_CTA_ACTION_RE_FRAGMENT}\b|"
    rf"\b(?:we\s+)?(?:deny|denies|dispute|disputes|reject|rejects)\s+(?:that\s+)?"
    rf"(?:you|borrowers?)\s+(?:may|can|could|should|must|might)\s+"
    rf"{_BORROWER_CTA_ACTION_RE_FRAGMENT}\b|"
    rf"\b(?:(?:under\s+no\s+circumstances)|(?:we\s+ask\s+that\s+you\s+not)|"
    rf"(?:(?:we\s+)?(?:ask|request|recommend)(?:ed)?\s+that\s+you\s+not)|"
    rf"(?:(?:we\s+)?(?:request|recommend)(?:ed)?\s+you\s+not)|"
    rf"(?:(?:we\s+)?prefer(?:red)?\s+that\s+you\s+not)|"
    rf"(?:(?:asked|requested|advised|instructed)\s+not\s+to)|"
    rf"(?:(?:advise|advised)\s+(?:you\s+)?not\s+to)|"
    rf"(?:(?:urge|urged)\s+(?:you\s+)?not\s+to)|"
    rf"(?:(?:remember|be\s+sure)\s+not\s+to)|"
    rf"(?:not\s+(?:asking|inviting|requesting)\s+(?:you\s+)?to)|"
    rf"(?:not\s+(?:an?\s+)?(?:invitation|request|recommendation|permission|"
    rf"authorization|consent|call)\s+to)|"
    rf"(?:(?:does|do)\s+not\s+constitute\s+(?:an?\s+)?"
    rf"(?:permission|invitation|request|recommendation)\s+to)|"
    rf"(?:(?:doesn['’]t|don['’]t)\s+constitute\s+(?:an?\s+)?"
    rf"(?:permission|invitation|request|recommendation)\s+to)|"
    rf"(?:(?:does|do)\s+not\s+grant\s+(?:you\s+)?permission\s+to)|"
    rf"(?:(?:does|do)\s+not\s+authorize(?:\s+you)?\s+to)|"
    rf"(?:(?:doesn['’]t|don['’]t)\s+authorize(?:\s+you)?\s+to)|"
    rf"(?:no\s+(?:permission|authorization|consent)\s+to)|"
    rf"(?:no\s+(?:permission|authorization|consent)\s+(?:has|have)\s+been\s+"
    rf"(?:given|provided|granted)\s+to)|"
    rf"(?:(?:we\s+)?withhold(?:s|ing)?\s+(?:permission|authorization|consent)\s+to)|"
    rf"(?:(?:you|we)\s+(?:have|has)\s+(?:since\s+)?withdrawn\s+"
    rf"(?:permission|authorization|consent)\s+to)|"
    rf"(?:(?:we\s+)?(?:revoke|rescind|withdraw|retract|void)(?:s|ed|ing)?\s+"
    rf"(?:permission|authorization|consent)\s+to)|"
    rf"(?:no\s+legal\s+(?:right|basis)\s+to(?:\s+accept)?)|"
    rf"(?:(?:it\s+(?:is|would\s+be)|this\s+is)\s+(?:illegal|unlawful)\s+to)|"
    rf"(?:nothing\s+(?:herein\s+)?authorizes?(?:\s+you)?\s+to)|"
    rf"(?:(?:does|do)\s+not\s+permit(?:\s+you)?\s+to)|"
    rf"(?:without\s+(?:asking|inviting|requesting|encouraging)\s+(?:you\s+)?to)|"
    rf"(?:do\s+not|don['’]t|never|cannot|can\s+not|"
    rf"can['’]t|will\s+not|won['’]t|should\s+not|shouldn['’]t|must\s+not|"
    rf"mustn['’]t|may\s+not|shall\s+not|shan['’]t|need\s+not|needn['’]t|"
    rf"ought\s+not))[^.!?;:–—]{{0,80}}?\b"
    rf"{_BORROWER_CTA_NEGATIVE_ACTION_RE_FRAGMENT}\b(?:\s+(?:us|me))?|"
    rf"\b(?:opt(?:ed|s|ing)?\s+out\s+of\s+"
    rf"(?:calls?|emails?|texts?|messages?|replies|responses?|contact))\b|"
    rf"\b(?:consent\s+(?:does|do)\s+not\s+cover\s+"
    rf"(?:telephone\s+)?(?:calls?|emails?|texts?|messages?|replies|contact))\b|"
    rf"\b(?:(?:you|we)\s+withdrew\s+"
    rf"(?:(?:telephone|phone|call|email|text|message|reply|contact)\s+)?consent)\b|"
    rf"\b(?:(?:nobody|no\s+one)\s+(?:monitors?|reads?|staffs?)\s+"
    rf"(?:this|the)\s+(?:inbox|mailbox))\b|"
    rf"\b(?:messages?\s+to\s+(?:this|the)\s+(?:address|inbox|mailbox)\s+"
    rf"(?:bounce|bounces|fail|fails))\b|"
    rf"\b(?:calls?\s+(?:route|routes|go|goes)\s+nowhere)\b|"
    rf"\b(?:(?:not|never|no\s+longer|isn['’]t|aren['’]t|wasn['’]t|weren['’]t)"
    rf"\s+(?:[A-Za-z'-]+\s+){{0,3}}"
    rf"(?:allowed|permitted|possible|able|required|authorized)\b"
    rf"[^.!?;:–—]{{0,60}}?\bto|"
    rf"(?:unable|impossible|forbidden|prohibited|disallowed|unauthorized)\s+to|"
    rf"(?:denied\s+permission\s+to)|"
    rf"(?:lack(?:s|ed|ing)?\s+(?:permission|authorization|consent)\s+to))\s+"
    rf"{_BORROWER_CTA_ACTION_RE_FRAGMENT}\b|"
    rf"\b{_BORROWER_CTA_NEGATIVE_ACTION_RE_FRAGMENT}\b[^.!?;:–—]{{0,30}}"
    rf"\b(?:would|will|does|do)\s+violate\s+(?:the\s+)?law\b|"
    rf"\b(?:(?:it\s+is\s+)?against\s+the\s+law\s+to\s+|"
    rf"(?:the\s+)?(?:law|regulation)\s+(?:prohibits?|forbids?)\s+"
    rf"(?:you\s+from\s+)?)"
    rf"{_BORROWER_CTA_NEGATIVE_ACTION_RE_FRAGMENT}\b|"
    rf"\b(?:prohibited|forbidden|disallowed|prevented|barred|blocked)\s+"
    rf"(?:by\s+(?:the\s+)?law\s+)?from\s+"
    r"(?:contacting|calling|replying|responding|communicating|reaching\s+out|"
    r"scheduling|requesting|starting|reviewing|comparing|exploring|discussing|"
    r"talking|speaking)\b|"
    r"\b(?:(?:(?:please|kindly)\s+)?(?:refrain\s+from|avoid|stop)|"
    r"(?:we\s+)?(?:advise|recommend)\s+against|"
    r"(?:we\s+)?discourage(?:\s+you)?\s+from)\s+"
    r"(?:contacting|calling|replying|responding|communicating|reaching\s+out|"
    r"scheduling|requesting|starting|reviewing|comparing|exploring|discussing|"
    r"talking|speaking)\b|"
    r"\b(?:not\s+accepting|aren['’]t\s+accepting|isn['’]t\s+accepting|"
    r"do\s+not\s+accept|don['’]t\s+accept|will\s+not\s+accept|won['’]t\s+accept|"
    r"no\s+longer\s+accept|cannot\s+accept|can['’]t\s+accept)\s+"
    r"(?:calls?|contacts?|replies|responses?)\b|"
    r"\b(?:are|is)\s+no\s+longer\s+accepting\s+"
    r"(?:calls?|contacts?|replies|responses?)\b|"
    r"\b(?:unable|not\s+able)\s+to\s+accept\s+"
    r"(?:calls?|contacts?|replies|responses?)\b|"
    r"\b(?:not\s+taking|aren['’]t\s+taking|isn['’]t\s+taking|"
    r"no\s+longer\s+taking|unable\s+to\s+take|not\s+able\s+to\s+take)\s+"
    r"(?:calls?|contacts?|replies|responses?)\b|"
    r"\b(?:(?:we|our\s+team|the\s+team)\s+)?"
    r"(?:no\s+longer|never|do\s+not|don['’]t|cannot|can['’]t|"
    r"will\s+not|won['’]t)\s+take\s+(?:calls?|contacts?|replies|responses?)\b|"
    r"\b(?:(?:we|our\s+team|the\s+team)\s+)?"
    r"(?:no\s+longer|never|do\s+not|don['’]t|cannot|can['’]t|"
    r"will\s+not|won['’]t)\s+(?:receive|answer)\s+"
    r"(?:calls?|contacts?|replies|responses?)\b|"
    r"\b(?:(?:we|our\s+team|the\s+team)\s+)?"
    r"(?:(?:have|has)\s+(?:stopped|ceased)|(?:stopped|ceased))\s+"
    r"(?:monitoring|receiving|answering|operating|supporting)\s+"
    r"(?:this\s+|the\s+)?(?:inbox(?:es)?|mailbox(?:es)?|phone(?:s)?|phone\s+lines?|"
    r"lines?|calls?|contacts?|replies|responses?)\b|"
    r"\b(?:(?:we|our\s+team|the\s+team)\s+)?"
    r"(?:no\s+longer|never|do\s+not|don['’]t|cannot|can['’]t|"
    r"will\s+not|won['’]t)\s+(?:monitor|operate|support)\s+"
    r"(?:this\s+|the\s+)?(?:inbox(?:es)?|mailbox(?:es)?|phone(?:s)?|phone\s+lines?|"
    r"lines?)\b|"
    r"\b(?:messages?|calls?|replies|responses?)\b[^.!?;:–—]{0,40}\b"
    r"(?:go|are|is)\s+unanswered\b|"
    r"\b(?:blocked|automatically\s+declined)\s+(?:incoming\s+)?"
    r"(?:messages?|calls?|replies|responses?)\b|"
    r"\bincoming\s+(?:messages?|calls?|replies|responses?)\s+(?:are\s+)?"
    r"(?:blocked|automatically\s+declined)\b|"
    r"\b(?:inbox(?:es)?|mailbox(?:es)?)\s+(?:does|do)\s+not\s+receive\s+"
    r"(?:messages?|replies|responses?)\b|"
    r"\b(?:(?:we|our\s+team|the\s+team)\s+)?"
    r"(?:(?:has|have)\s+)?(?:stopped|ceased)\s+taking\s+"
    r"(?:calls?|contacts?|replies|responses?)\b|"
    r"\b(?:(?:we|our\s+team|the\s+team)\s+)?"
    r"(?:(?:has|have)\s+)?(?:stopped|ceased)\s+accepting\s+"
    r"(?:calls?|contacts?|replies|responses?)\b|"
    r"\b(?:calls?|contacts?|replies|responses?)\s+(?:are|is)\s+"
    r"(?:not|no\s+longer)\s+(?:being\s+)?taken\b|"
    r"\b(?:calls?|contacts?|replies|responses?)\s+(?:aren['’]t|isn['’]t)\s+"
    r"(?:being\s+)?taken\b|"
    r"\b(?:calls?|contacts?|replies|responses?)\s+"
    r"(?:cannot|can['’]t|will\s+not|won['’]t|should\s+not|shouldn['’]t)\s+be\s+taken\b|"
    r"\b(?:we|our\s+team|the\s+team)\s+(?:are|is)\s+not\s+taking\s+"
    r"(?:calls?|contacts?|replies|responses?)\b|"
    r"\b(?:calls?|contacts?|replies|responses?)\s+(?:are|is)\s+"
    r"(?:not|no\s+longer)\s+(?:being\s+)?accepted\b|"
    r"\b(?:calls?|contacts?|replies|responses?)\s+(?:aren['’]t|isn['’]t)\s+"
    r"(?:being\s+)?accepted\b|"
    rf"\b(?:calls?|contacts?|replies|responses?)\s+(?:are|is)\s+"
    rf"{_BORROWER_CTA_NEGATIVE_STATUS_RE_FRAGMENT}\b|"
    r"\b(?:will\s+not|won['’]t|do\s+not|don['’]t)\s+accept\s+"
    r"(?:calls?|contacts?|replies|responses?)\b|"
    rf"\b{_BORROWER_CTA_NEGATIVE_ACTION_RE_FRAGMENT}(?:\s+(?:us|me|to\s+this\s+message))?\s+"
    rf"(?:is|are)\s+(?:off\s+the\s+table|(?:(?:not|never|no\s+longer|not\s+currently)\s+"
    rf"{_BORROWER_CTA_POSITIVE_STATUS_RE_FRAGMENT})|"
    rf"{_BORROWER_CTA_NEGATIVE_STATUS_RE_FRAGMENT})\b|"
    rf"\b{_BORROWER_CTA_NEGATIVE_ACTION_RE_FRAGMENT}(?:\s+(?:us|me|to\s+this\s+message))?\s+"
    rf"(?:isn['’]t|aren['’]t)\s+{_BORROWER_CTA_POSITIVE_STATUS_RE_FRAGMENT}\b|"
    rf"\b{_BORROWER_CTA_TERM_RE_FRAGMENT}\b[^.!?;:–—]{{0,40}}?\b"
    rf"(?:no\s+longer\s+works?|(?:has|have)\s+stopped\s+working|stopped\s+working)\b|"
    rf"\bno\s+(?:need|reason|requirement|obligation|way)\b[^.!?;:–—]{{0,60}}?"
    rf"\b(?:to|that)\b[^.!?;:–—]{{0,40}}?\b{_BORROWER_CTA_ACTION_RE_FRAGMENT}\b|"
    rf"\b{_BORROWER_CTA_TERM_RE_FRAGMENT}\b[^.!?;:–—]{{0,100}}?\b(?:"
    rf"(?:is|are|was|were)\s+(?:[A-Za-z'-]+\s+){{0,3}}"
    rf"(?:(?:(?:not|never|no\s+longer)\s+"
    rf"(?:[A-Za-z'-]+\s+){{0,3}}{_BORROWER_CTA_POSITIVE_STATUS_RE_FRAGMENT})|"
    rf"{_BORROWER_CTA_NEGATIVE_STATUS_RE_FRAGMENT})|"
    rf"(?:isn['’]t|aren['’]t)\s+(?:[A-Za-z'-]+\s+){{0,3}}"
    rf"{_BORROWER_CTA_POSITIVE_STATUS_RE_FRAGMENT}|"
    rf"(?:may|might|will|would|can|could|should|must|shall)\s+"
    rf"(?:(?:not|never|no\s+longer)\s+(?:[A-Za-z'-]+\s+){{0,3}}"
    rf"(?:be\s+)?{_BORROWER_CTA_POSITIVE_STATUS_RE_FRAGMENT}|"
    rf"(?:[A-Za-z'-]+\s+){{0,3}}(?:be\s+)?{_BORROWER_CTA_NEGATIVE_STATUS_RE_FRAGMENT})|"
    rf"(?:cannot|can\s+not|can['’]t|won['’]t|couldn['’]t|wouldn['’]t|"
    rf"shouldn['’]t|mustn['’]t|need\s+not)\s+"
    rf"(?:[A-Za-z'-]+\s+){{0,3}}(?:be\s+)?{_BORROWER_CTA_POSITIVE_STATUS_RE_FRAGMENT}|"
    rf"(?:has|have)\s+(?:[A-Za-z'-]+\s+){{0,3}}(?:been\s+)?"
    rf"(?:(?:not|never)\s+(?:been\s+)?{_BORROWER_CTA_POSITIVE_STATUS_RE_FRAGMENT}|"
    rf"{_BORROWER_CTA_NEGATIVE_STATUS_RE_FRAGMENT})|"
    rf"(?:hasn['’]t|haven['’]t)\s+(?:[A-Za-z'-]+\s+){{0,3}}(?:been\s+)?"
    rf"{_BORROWER_CTA_POSITIVE_STATUS_RE_FRAGMENT}|"
    rf"(?:does|do)\s+not\s+(?:[A-Za-z'-]+\s+){{0,3}}"
    rf"{_BORROWER_CTA_POSITIVE_STATUS_RE_FRAGMENT}|"
    rf"(?:doesn['’]t|don['’]t)\s+(?:[A-Za-z'-]+\s+){{0,3}}"
    rf"{_BORROWER_CTA_POSITIVE_STATUS_RE_FRAGMENT}|"
    rf"(?:did\s+not|didn['’]t)\s+(?:[A-Za-z'-]+\s+){{0,3}}"
    rf"{_BORROWER_CTA_POSITIVE_STATUS_RE_FRAGMENT}|fail(?:ed|s))\b|"
    rf"\bno\s+{_BORROWER_CTA_TERM_RE_FRAGMENT}\s+(?:is|are)\s+"
    rf"{_BORROWER_CTA_POSITIVE_STATUS_RE_FRAGMENT}\b|"
    r"\bno\s+(?:response|reply|contact|call|action)\b[^.!?;:–—]{0,80}?\b"
    r"(?:needed|required|requested|necessary|expected)\b|"
    r"\b(?:not\s+a|no)\s+(?:call\s+to\s+action|invitation\s+to\s+"
    r"(?:contact|call|reply|review))\b",
    re.IGNORECASE,
)
_BORROWER_COPY_CTA_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\bgive\s+us\s+a\s+call\b|\breach\s+our\s+team\s+by\s+email\b|"
        r"\breach\s+us\s+by\s+email\b|\btelephone\s+us\b|"
        r"\bget\s+in\s+touch(?:\s+with\s+us)?\b|\bdrop\s+us\s+a\s+line\b|"
        r"\bconnect\s+with\s+us\b|"
        r"\b(?:email|text|message)\s+us\b|\bwrite\s+to\s+us\b|"
        r"\bsend\s+us\s+(?:an?\s+)?(?:message|email|text)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\breply\s+(?:yes\b|to\b|now\b|today\b)", re.IGNORECASE),
    re.compile(
        r"\brespond\s+(?:yes\b|now\b|today\b|to\s+(?:this|the)\s+(?:message|notice)\b)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\breach\s+out(?:\s+to\s+(?:us|a\s+loan\s+officer|our\s+team))?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:you\s+(?:may|can)\s+)?(?:contact|call)(?:\s+us)?\s+if\s+"
        r"[^.!?;:–—]{0,80}\b(?:want|would\s+like|choose)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:to\s+learn\s+more[,]?\s+)?(?:call\s+or\s+reply|reply\s+or\s+call)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:contact|call)\s+(?:us\b|me\b|today\b|now\b|to\b(?!\s+action\b)|"
        r"(?:(?:a|an|the|your|our)\s+)?(?:licensed\s+)?"
        r"(?:loan\s+officer|mortgage\s+professional|lender|team|advisor))",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bcontact\s+[A-Za-z][A-Za-z ]{1,80}\s+to\s+" r"(?:review|discuss|compare|schedule)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:talk|speak)\s+(?:to|with)\s+(?:us|me|"
        r"(?:(?:a|an|the|your|our)\s+)?(?:licensed\s+)?"
        r"(?:loan\s+officer|mortgage\s+professional|lender|team|advisor))\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:schedule|request|start|book|arrange)\s+(?:(?:a|an|the|your)\s+)?"
        r"(?:(?:quick|different|personalized|licensed|no[- ]obligation)\s+)?"
        r"(?:mortgage\s+|loan\s+|refinance\s+|home[- ]equity\s+)?"
        r"(?:review|call|conversation|consultation)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|[.!?]\s+)(?:please\s+)?(?:review|compare|explore|discuss)\s+"
        r"(?:(?:your|the|these|available|current|mortgage|loan|refinance|home[- ]equity)\b)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|[.!?]\s+)(?:please\s+)?(?:review|compare|explore|discuss)\b"
        r"[^.!?;:–—]{1,160}\bwith\s+(?:(?:a|an|the|your|our)\s+)?(?:licensed\s+)?"
        r"(?:loan\s+officer|mortgage\s+professional|lender|advisor)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:would|do)\s+you\s+(?:like|want|wish|be\s+willing)\s+to\b"
        r"[^.!?;:–—]{0,80}\b"
        r"(?:review|schedule|talk|speak|compare|explore|discuss)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bwould\s+(?:(?:a|an|this|that|your)\s+)?"
        r"(?:mortgage\s+|loan\s+|refinance\s+|home[- ]equity\s+)?review\s+"
        r"(?:be\s+useful|help|make\s+sense)\b",
        re.IGNORECASE,
    ),
)


def _positive_cta_clause(value: str, match: re.Match[str]) -> str:
    tail = value[match.start() :]
    boundary = _BORROWER_COPY_ACTION_SCOPE_BOUNDARY_RE.search(tail)
    clause_end = boundary.start() if boundary else len(tail)
    for negative in _BORROWER_COPY_NEGATED_CTA_RE.finditer(tail):
        if negative.start() == 0:
            continue
        separator = re.search(
            r"(?:,\s*(?:(?:and|but|while|however|yet|although|though)\s+)?"
            r"(?:(?:please|kindly)\s+)?(?:(?:you|borrowers?)\s+)?|"
            r"\b(?:and|but|while|however|yet|although|though)\b\s+"
            r"(?:(?:please|kindly)\s+)?(?:(?:you|borrowers?)\s+)?)$",
            tail[: negative.start()],
            re.IGNORECASE,
        )
        if separator is not None:
            clause_end = min(clause_end, separator.start())
    clause = tail[:clause_end]
    return _BORROWER_COPY_REPLACEMENT_TARGET_RE.sub(" ", clause)


def _positive_cta_actions(value: str, match: re.Match[str]) -> set[str]:
    clause = _positive_cta_clause(value, match)
    return _canonical_cta_actions(clause) | _cta_channel_actions(clause)


def _positive_cta_action_variants(value: str, match: re.Match[str]) -> list[set[str]]:
    clause = _positive_cta_clause(value, match)
    actions = _canonical_cta_actions(clause) | _cta_channel_actions(clause)
    alternative = _BORROWER_COPY_ALTERNATIVE_ACTION_RE.search(clause)
    if alternative is None:
        return [actions] if actions else []
    left = _canonical_cta_action_token(alternative.group("left"))
    right = _canonical_cta_action_token(alternative.group("right"))
    if left is None or right is None or left == right:
        return [actions] if actions else []
    shared = actions - {left, right}
    return [shared | {left}, shared | {right}]


def _dedupe_overlapping_positive_matches(
    matches: list[re.Match[str]],
) -> list[re.Match[str]]:
    independent: list[re.Match[str]] = []
    for match in matches:
        if any(existing.start() == match.start() for existing in independent):
            continue
        if any(
            existing.start() < match.start() < existing.end()
            and _BORROWER_COPY_ALTERNATIVE_ACTION_RE.search(existing.group(0)) is not None
            for existing in independent
        ):
            continue
        independent.append(match)
    return independent


def _negative_action_blocks_positive(
    negative_actions: set[str],
    positive_actions: set[str],
) -> bool:
    if "contact" in negative_actions and positive_actions:
        return True
    if "message" in negative_actions and positive_actions & {"message", "reply", "text"}:
        return True
    return bool((negative_actions - {"contact"}) & positive_actions)


def _is_explicit_cta_replacement(
    value: str,
    *,
    negative_match: re.Match[str],
    positive_match: re.Match[str],
) -> bool:
    """Bind replacement language to the immediately adjacent CTA clause."""

    negative_actions = _negative_actions_for_positive(
        value,
        negative_match=negative_match,
        positive_match=positive_match,
    )
    positive_actions = _positive_cta_actions(value, positive_match)
    if not negative_actions or not positive_actions:
        return False
    bridge = value[negative_match.end() : positive_match.start()]
    boundaries = list(_BORROWER_COPY_CLAUSE_BOUNDARY_RE.finditer(bridge))
    prefix = bridge[boundaries[-1].end() :] if boundaries else bridge
    if _BORROWER_COPY_REPLACEMENT_PREFIX_RE.fullmatch(
        prefix
    ) and not _negative_action_blocks_positive(negative_actions, positive_actions):
        return True
    tail = value[positive_match.end() :]
    boundary = _BORROWER_COPY_CLAUSE_BOUNDARY_RE.search(tail)
    clause_tail = tail[: boundary.start()] if boundary else tail
    replacement_actions = explicit_replacement_channel_actions(f"{bridge} {clause_tail}")
    if positive_actions & replacement_actions and not _negative_action_blocks_positive(
        negative_actions, positive_actions
    ):
        return True
    for replacement in _BORROWER_COPY_REPLACEMENT_POSTFIX_RE.finditer(clause_tail):
        before = clause_tail[: replacement.start()]
        after = clause_tail[replacement.end() :]
        if not _BORROWER_COPY_REPLACEMENT_CONTINUATION_RE.fullmatch(before):
            continue
        target = replacement.group("target")
        if target is not None:
            target_actions = _canonical_cta_actions(target)
            if target_actions & negative_actions:
                return True
            continue
        if not _BORROWER_COPY_REPLACEMENT_CONTINUATION_RE.fullmatch(after):
            continue
        if not _negative_action_blocks_positive(negative_actions, positive_actions):
            return True
    return False


def _hard_negatives_allow_positive_variant(
    value: str,
    *,
    positive: re.Match[str],
    positive_actions: set[str],
    hard_negatives: list[re.Match[str]],
) -> bool:
    for negative in hard_negatives:
        negative_actions = _negative_actions_for_positive(
            value,
            negative_match=negative,
            positive_match=positive,
        )
        if _negative_action_blocks_positive(negative_actions, positive_actions):
            return False
        # A hard governance fact with no safely inferred action family is not
        # order-sensitive permission to proceed. Fail closed before accepting
        # any affirmative borrower CTA.
        if not negative_actions:
            return False
        if negative.start() < positive.start() and not _is_explicit_cta_replacement(
            value,
            negative_match=negative,
            positive_match=positive,
        ):
            return False
    return True


def _soft_autonomy_cancels_positive(
    value: str,
    *,
    positive: re.Match[str],
    positive_actions: set[str],
    soft_negative: re.Match[str],
) -> bool:
    """Return true only when autonomy wording exposes a spurious review CTA.

    Statements such as ``no response is required`` preserve borrower choice;
    they do not prohibit an otherwise explicit, optional contact action.  They
    therefore must not cancel a real CTA merely because the response/action
    noun maps to the same channel.  The remaining check handles informational
    ``review this notice`` prose that the broad CTA grammar can otherwise
    mistake for an invitation.
    """

    soft_actions = _negative_actions_for_positive(
        value,
        negative_match=soft_negative,
        positive_match=positive,
    )
    positive_clause = _positive_cta_clause(value, positive)
    return (
        "review" in positive_actions
        and not positive_actions & {"contact", "call", "reply", "conversation", "initiate"}
        and re.search(r"\b(?:notice|message)\b", positive_clause, re.IGNORECASE) is not None
        and (not soft_actions or "reply" in soft_actions)
    )


def _has_affirmative_borrower_cta(value: str) -> bool:
    negation_scan_value = normalize_safe_affirmative_cta(value)
    negative_matches = negative_borrower_cta_evidence(negation_scan_value)
    negative_matches.extend(
        match
        for match in _BORROWER_COPY_NEGATED_CTA_RE.finditer(negation_scan_value)
        if not staffed_delivery_reconciles_negative(negation_scan_value, match)
    )
    negative_matches.sort(key=lambda match: match.start())
    positive_matches = _dedupe_overlapping_positive_matches(
        sorted(
            (
                match
                for pattern in _BORROWER_COPY_CTA_PATTERNS
                for match in pattern.finditer(negation_scan_value)
            ),
            key=lambda match: match.start(),
        )
    )
    independent_positive_matches = [
        match
        for match in positive_matches
        if not any(
            negative.start() <= match.start() < negative.end() for negative in negative_matches
        )
        and is_borrower_directed_cta(negation_scan_value, match)
    ]
    if not independent_positive_matches:
        return False
    hard_negative_matches = [
        match
        for match in negative_matches
        if _BORROWER_COPY_CONDITIONAL_AUTONOMY_RE.match(match.group(0))
        or not _BORROWER_COPY_SOFT_AUTONOMY_RE.match(match.group(0))
    ]
    allowed_positive_variants: list[tuple[re.Match[str], list[set[str]]]] = []
    for positive in independent_positive_matches:
        variants = [
            actions
            for actions in _positive_cta_action_variants(negation_scan_value, positive)
            if _hard_negatives_allow_positive_variant(
                negation_scan_value,
                positive=positive,
                positive_actions=actions,
                hard_negatives=hard_negative_matches,
            )
        ]
        if not variants:
            return False
        allowed_positive_variants.append((positive, variants))
    soft_negative_matches = [
        match
        for match in negative_matches
        if not _BORROWER_COPY_CONDITIONAL_AUTONOMY_RE.match(match.group(0))
        and _BORROWER_COPY_SOFT_AUTONOMY_RE.match(match.group(0))
    ]
    for positive, variants in allowed_positive_variants:
        uncancelled_variants = [
            actions
            for actions in variants
            if not any(
                _soft_autonomy_cancels_positive(
                    negation_scan_value,
                    positive=positive,
                    positive_actions=actions,
                    soft_negative=soft_negative,
                )
                for soft_negative in soft_negative_matches
            )
        ]
        if not uncancelled_variants:
            return False
    return True


def assert_public_non_numeric_campaign_summary(
    value: str,
    *,
    field_name: str,
    max_length: int,
) -> str:
    """Validate public strategy prose while keeping numeric facts in evidence."""

    text = assert_public_campaign_text(value, field_name=field_name, max_length=max_length)
    if _SUMMARY_NUMERIC_CLAIM_RE.search(text):
        raise ValueError(f"{field_name} must keep numeric facts in evidence")
    return text


def assert_public_campaign_text(value: object, *, field_name: str, max_length: int) -> str:
    """Normalize campaign text and reject PII-shaped or unresolved content."""

    if value is None:
        return ""
    text = re.sub(r"\s+", " ", str(value).strip())
    if len(text) > max_length:
        raise ValueError(f"{field_name} must be {max_length} characters or fewer")
    if contains_confidential_or_internal_text(text):
        raise ValueError(
            f"{field_name} cannot contain secrets, internal instructions, credentials, "
            "tokens, URLs, or endpoints"
        )
    if contains_mechanical_pii_or_raw_identifier(text) or any(
        re.search(pattern, text, re.IGNORECASE) for pattern in _PUBLIC_TEXT_DENYLIST
    ):
        raise ValueError(
            f"{field_name} cannot contain PII, raw identifiers, or unresolved placeholders"
        )
    if contains_prompt_injection_text(text):
        raise ValueError(f"{field_name} cannot contain instruction-override language")
    assert_no_protected_class_marketing_text(
        remove_configured_public_lender_phrase(text),
        field_name=field_name,
    )
    name_scan_text = remove_allowed_public_titlecase_phrases(
        normalize_safe_lender_invitation_for_name_scan(text)
    )
    is_canonical_platform_label = (
        field_name.endswith("generator_label") and text in _CANONICAL_PUBLIC_PLATFORM_LABELS
    )
    if not is_canonical_platform_label and (
        _HUMAN_NAME_SHAPE_RE.search(name_scan_text)
        or contains_contextual_human_name(name_scan_text)
        or contains_borrower_copy_contextual_name(name_scan_text)
        or contains_human_name_shape(name_scan_text)
    ):
        raise ValueError(f"{field_name} cannot contain human-name-shaped text")
    return text


def assert_public_campaign_json(value: object, *, field_name: str) -> None:
    """Recursively enforce public-text policy across campaign JSON values."""

    if isinstance(value, dict):
        for key, item in value.items():
            assert_public_campaign_json(key, field_name=field_name)
            assert_public_campaign_json(item, field_name=field_name)
    elif isinstance(value, list):
        for item in value:
            assert_public_campaign_json(item, field_name=field_name)
    elif isinstance(value, str):
        assert_public_campaign_text(value, field_name=field_name, max_length=1000)


def assert_borrower_campaign_copy(
    value: str,
    *,
    field_name: str,
    require_cta: bool | None = None,
) -> str:
    """Reject unsupported or internally framed borrower-facing campaign copy."""

    reviewed_health_scan = mask_protected_health_safe_contexts(value)
    if contains_unsupported_borrower_qualification_claim(
        value
    ) or _BORROWER_COPY_UNSUPPORTED_CLAIM_RE.search(reviewed_health_scan):
        raise ValueError(f"{field_name} contains an unsupported borrower-facing claim")
    should_require_cta = field_name.endswith("body") if require_cta is None else require_cta
    contains_candidate_cta = any(
        pattern.search(value) is not None for pattern in _BORROWER_COPY_CTA_PATTERNS
    )
    if (should_require_cta or contains_candidate_cta) and not _has_affirmative_borrower_cta(value):
        raise ValueError(f"{field_name} must include a clear review or contact call to action")
    return value


def _default_portfolio_criteria() -> "PortfolioCriteria":
    # Delayed import keeps this focused schema module independent while
    # preserving the request contract's empty-body default.
    from backend.schemas.portfolio import PortfolioCriteria

    return PortfolioCriteria()


class PortfolioOfferMixRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    offer_code: Literal[
        "purchase",
        "refi_plus_heloc",
        "heloc",
        "refi",
        "cash_out",
        "investor",
        "retention",
        "nurture",
    ]
    borrower_count: int = Field(ge=0)


class CampaignRecommendationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criteria: "PortfolioCriteria" = Field(default_factory=_default_portfolio_criteria)


class CampaignRecommendationVariant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    variant_name: Literal["Benefit-led", "Guidance-led"]
    subject: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=1000)
    hypothesis: str = Field(min_length=1, max_length=280)
    provenance_token: str | None = Field(default=None, min_length=32, max_length=4096)

    @field_validator("subject", "body", "hypothesis")
    @classmethod
    def _validate_public_copy(cls, value: str, info) -> str:
        text = assert_public_campaign_text(
            value,
            field_name=f"campaign recommendation {info.field_name}",
            max_length={"subject": 120, "body": 1000, "hypothesis": 280}[info.field_name],
        )
        if info.field_name in {"subject", "body"}:
            assert_borrower_campaign_copy(
                text,
                field_name=f"campaign recommendation {info.field_name}",
            )
        return text


class CampaignRecommendationEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=120)
    source_asset: Literal[
        "mip.semantics.portfolio_headline_metric_view",
        "mip.gold.borrower_360",
        "mip_app.call_dispositions",
        "mip_app.lead_outcomes",
    ]

    @field_validator("label", "value")
    @classmethod
    def _validate_public_evidence(cls, value: str, info) -> str:
        return assert_public_campaign_text(
            value,
            field_name=f"campaign evidence {info.field_name}",
            max_length={"label": 80, "value": 120}[info.field_name],
        )


class CampaignRecommendationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generation_mode: Literal["supervisor", "reviewed_fallback"]
    generator_label: str = Field(min_length=1, max_length=80)
    performance_status: Literal["qualified", "insufficient_sample", "unavailable"]
    audience_summary: str = Field(min_length=1, max_length=280)
    strategy: str = Field(min_length=1, max_length=500)
    variants: list[CampaignRecommendationVariant] = Field(min_length=2, max_length=2)
    holdout_pct: float = Field(ge=5, le=30)
    evidence: list[CampaignRecommendationEvidence] = Field(min_length=1, max_length=8)
    warnings: list[str] = Field(default_factory=list, max_length=4)

    @field_validator("generator_label", "audience_summary", "strategy")
    @classmethod
    def _validate_public_summary(cls, value: str, info) -> str:
        field_name = f"campaign recommendation {info.field_name}"
        max_length = {"generator_label": 80, "audience_summary": 280, "strategy": 500}[
            info.field_name
        ]
        if info.field_name in {"audience_summary", "strategy"}:
            return assert_public_non_numeric_campaign_summary(
                value,
                field_name=field_name,
                max_length=max_length,
            )
        text = assert_public_campaign_text(
            value,
            field_name=field_name,
            max_length=max_length,
        )
        return text

    @field_validator("warnings")
    @classmethod
    def _validate_warnings(cls, value: list[str]) -> list[str]:
        return [
            assert_public_campaign_text(item, field_name="campaign warning", max_length=240)
            for item in value
        ]


def bind_portfolio_criteria(criteria_type: type[BaseModel]) -> None:
    """Resolve the request model after the core portfolio criteria exists."""

    CampaignRecommendationRequest.model_rebuild(
        _types_namespace={"PortfolioCriteria": criteria_type},
    )


# Support direct imports of this focused module as well as the legacy
# backend.schemas.portfolio re-export. During a portfolio-first import, the
# parent module performs the bind after defining PortfolioCriteria.
if "backend.schemas.portfolio" not in sys.modules:
    from backend.schemas.portfolio import PortfolioCriteria as _PortfolioCriteria

    bind_portfolio_criteria(_PortfolioCriteria)
