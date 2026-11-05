"""Purpose-named exact digest acks: the deferred owner operation attestation's substitute (M2-PR7).

The owner hardware-key attestation layer (plan M1-PR5) is deferred and the
review mode is ``single_maintainer_audited``. Every high-risk operation it
would have gated prints the sha256 of its canonical read-only plan instead,
and a mutating run must be handed exactly that digest through a one-run shell
export (``MIP_ACK_<PURPOSE>``, read by scripts/deploy.sh from its own
environment and passed on as a flag, for the purposes in ``ACK_ENV``; the
core release reads ``restore_unverified_frontend``'s since M2-PR7b) or the
flag of the command that needs it (``override_freeze``, ``tier_transition``),
never a ``.env.local`` line. A refusal names exactly that route (``REMEDY``).
The tool recomputes the plan immediately before it mutates and refuses on any
difference, with nothing changed.

The canonical body the digest covers names its purpose, so an ack for one
purpose can never satisfy another: the same plan acknowledged for
``adopt_live`` and for ``supersede`` has two different digests. The purposes
are the plan's named attestation reasons: ``adopt_live``, ``supersede``,
``unadopt`` (the tombstone; the plan lists it among the operation reasons),
``restore_unverified_frontend``, ``no_rollback_for``, ``override_freeze`` and
``tier_transition`` (M2-PR7b: a release whose tier is not its signed blue's;
tools/databricks/blue_tier_gate.py).

A digest is not a signature: anyone holding the plan can compute it. It
proves the operator reviewed this exact plan, not who the operator is; the
signed lease and the deploy's identities bound who may mutate.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping

PURPOSES = (
    "adopt_live",
    "supersede",
    "unadopt",
    "restore_unverified_frontend",
    "no_rollback_for",
    "override_freeze",
    "tier_transition",
)
# The one-run shell export scripts/deploy.sh reads, for the purposes it reads one for.
ACK_ENV = {
    purpose: f"MIP_ACK_{purpose.upper()}"
    for purpose in (
        "adopt_live",
        "supersede",
        "unadopt",
        "no_rollback_for",
        "restore_unverified_frontend",
    )
}
# How each purpose's ack is handed in: that export, else the flag of the one
# command that takes it.
REMEDY = {
    **{
        purpose: f"export {name}=<its sha256> for this run only (never in .env.local)"
        for purpose, name in ACK_ENV.items()
    },
    "override_freeze": "pass --override-freeze-ack <its sha256> to this one publish",
    # No release reads it from the shell yet: the one release it can serve, an
    # agentic release over a core blue, is wired by M4-PR1 (blue_tier_gate.py).
    "tier_transition": (
        "pass --tier-transition-ack <its sha256> to the one blue proof that refused"
    ),
}
_DOMAIN = "mip-operation-ack-v1"
_SHA256 = re.compile(r"[0-9a-f]{64}")


class AckRefused(RuntimeError):
    """An ack is missing, malformed or not the recomputed plan's digest."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _purpose(purpose: str) -> str:
    if purpose not in PURPOSES:
        raise ValueError(f"unreviewed operation ack purpose {purpose!r}")
    return purpose


def canonical_body(purpose: str, plan: Mapping[str, object]) -> bytes:
    """The bytes an ack digests: the domain, the purpose and the plan, canonical JSON."""

    document = {"ack": _DOMAIN, "purpose": _purpose(purpose), "plan": dict(plan)}
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "ascii"
    )


def ack_digest(purpose: str, plan: Mapping[str, object]) -> str:
    """The sha256 an operator exports to acknowledge ``plan`` for ``purpose``."""

    return hashlib.sha256(canonical_body(purpose, plan)).hexdigest()


def require_ack(purpose: str, plan: Mapping[str, object], given: str | None) -> str:
    """Refuse unless ``given`` is exactly this plan's ``purpose`` digest; return the digest."""

    expected = ack_digest(purpose, plan)
    supplied = (given or "").strip()
    if not supplied:
        raise AckRefused(
            "ack_missing",
            f"the {purpose} ack is required: review the read-only plan, then {REMEDY[purpose]}",
        )
    if _SHA256.fullmatch(supplied) is None:
        raise AckRefused("ack_malformed", f"the {purpose} ack is not a 64-hex sha256")
    if not hmac.compare_digest(supplied, expected):
        raise AckRefused(
            "ack_mismatch",
            f"the recomputed {purpose} plan is {expected}, not the acknowledged digest; "
            "nothing was changed",
        )
    return expected
