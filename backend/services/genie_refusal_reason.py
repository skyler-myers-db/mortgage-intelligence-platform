"""Coarse Genie refusal families for the wire contract.

A withheld Genie turn already tells the lender WHY, in one of a fixed set of
answer sentences: the fair-lending template, the unreviewed-criterion
template, the PII template, and so on. ``refusal_reason`` names that family
as a machine token so the UI can render a helpful card (compliant rephrase
chips, "Edit question", the reviewed vocabulary) without parsing copy
(audit 2026-09-21 ``genie-05``).

Guard-oracle posture: the enum is deliberately COARSE. Every value maps to a
refusal sentence the product already shows, and nothing finer. In
particular the protected-class detectors (reviewed term, proxy, health
bank, ...) all collapse to ``protected_class``, the audit ledger's own
distinction between a direct term and a proxy is not exposed, and the
matched term itself never leaves the backend. No new detection is added
here: the family is derived from the classification the guard battery
already computed.

``refusal_report_hash`` is the full SHA-256 of the whitespace-normalized
lower-cased question. The false-positive report endpoint accepts only that
hash, so a refused prompt is never round-tripped or stored as text.
"""

from __future__ import annotations

import hashlib
import re
from typing import Literal, get_args

GenieRefusalReason = Literal[
    "protected_class",
    "unreviewed_criterion",
    "pii_request",
    "instruction_override",
    "outreach_instruction",
    "scope_bypass",
    "out_of_scope",
    "output_policy",
    "unknown",
]

GENIE_REFUSAL_REASONS: frozenset[str] = frozenset(get_args(GenieRefusalReason))

# ``protected_prompt_match`` state sentinels and the audit ledger's own codes
# fold onto the wire family that carries their answer sentence. Anything not
# listed is a direct reviewed-term match and reads as ``protected_class``.
_PROTECTED_AUDIT_CODE_TO_FAMILY: dict[str, GenieRefusalReason] = {
    "protected_class": "protected_class",
    "protected_class_proxy": "protected_class",
    "unreviewed_criterion": "unreviewed_criterion",
}

# Wire family -> governed audit ``refusal_reason`` (``_GOVERNED_REFUSAL_REASONS``
# in the audit metadata policy). Families with no governed audit code are
# recorded only on the report row itself; the audit row then carries the
# ``action_type`` and hash without a ``refusal_reason`` key.
GENIE_REFUSAL_AUDIT_CODES: dict[str, str] = {
    "protected_class": "protected_class",
    "unreviewed_criterion": "unreviewed_criterion",
    "pii_request": "pii_request",
    "instruction_override": "instruction_override",
    "scope_bypass": "scope_bypass",
    "out_of_scope": "out_of_scope",
}

_WHITESPACE_RE = re.compile(r"\s+")
REFUSAL_REPORT_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def refusal_family_for_protected_code(audit_code: str) -> GenieRefusalReason:
    """Map the guard's protected-class audit code to its coarse wire family."""

    return _PROTECTED_AUDIT_CODE_TO_FAMILY.get(audit_code, "protected_class")


def normalize_question_for_report(question: str) -> str:
    """Whitespace-collapse and lower-case so trivially different retypes match."""

    return _WHITESPACE_RE.sub(" ", question).strip().lower()


def refusal_report_hash(question: str) -> str:
    """Full SHA-256 (64 lowercase hex) of the normalized refused question."""

    normalized = normalize_question_for_report(question)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def is_refusal_report_hash(value: str) -> bool:
    return REFUSAL_REPORT_HASH_RE.fullmatch(value) is not None
