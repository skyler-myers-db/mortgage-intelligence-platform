"""Signed digests that bind a composed Growth Agent plan to its review.

Audit 2026-09-21 ``critic-01``: a lender reviews a composed plan and clicks
Run; the server must run exactly that plan. Compose signs the canonical,
registry-validated plan together with the actor, the reviewed objective and
the state scope; execute verifies the signature before it re-validates and
runs the posted plan. Nothing is stored: the HMAC binds the reviewed plan
statelessly, so no Lakebase table or migration is needed.

The key is the rotation-aware Genie action key (``MIP_GENIE_ACTION_SECRET_*``),
reused with its own domain separator exactly as the Lead Queue handoff proof
does (``growth_agent_handoff``): current and previous keys verify during a
rotation, and a digest names the key id it was signed with.

Wire format: ``v1.<kid>.<iat>.<signature>``, where ``signature`` is the 43
character unpadded base64url HMAC-SHA256 of the canonical claims. The claims
never travel: the verifier rebuilds them from the posted request, so a digest
carries no actor, objective or plan text. Nothing here logs the digest, the
objective, the plan, the states or the actor.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import re
import time
from typing import Literal

from backend.schemas.agent_plan import ComposedPlan
from backend.services.genie_actions import _action_token_keys, _current_action_token_key
from backend.services.observability import emit

log = logging.getLogger(__name__)

PLAN_DIGEST_DOMAIN = b"mip.growth-agent.plan-digest.v1\x00"
PLAN_DIGEST_TTL_S = 2 * 60 * 60
_FUTURE_SKEW_S = 60
_WIRE_RE = re.compile(r"^v1\.([A-Za-z0-9_-]{1,32})\.([0-9]{10})\.([A-Za-z0-9_-]{43})$")
_KID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

PlanDigestConflictReason = Literal[
    "digest_mismatch",
    "digest_expired",
    "plan_revalidation_failed",
    "plan_changed",
]


class PlanDigestConflict(ValueError):
    """The posted plan is not the reviewed plan the server signed (HTTP 409)."""

    def __init__(self, reason: PlanDigestConflictReason) -> None:
        super().__init__(reason)
        self.reason: PlanDigestConflictReason = reason


class PlanDigestUnavailable(RuntimeError):
    """No signing key resolves, so no plan can be verified (HTTP 503)."""


def canonical_plan_json(plan: ComposedPlan) -> str:
    """The one serialization both sides sign and compare: sorted, compact, ASCII."""

    return json.dumps(
        plan.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def issue_plan_digest(
    *,
    actor: str,
    objective: str,
    states: list[str],
    plan: ComposedPlan,
    now: int | None = None,
) -> str | None:
    """Sign a validated plan for this actor, objective and scope.

    Returns ``None`` (and emits a warning event) when the deployment has no
    usable signing key: the plan can still be reviewed, but the UI disables
    Run and the execute endpoint answers 503. It never signs with a fallback.
    """

    try:
        kid, secret = _current_action_token_key()
    except (RuntimeError, ValueError) as exc:
        _emit_unavailable("signing_key_missing", error_type=type(exc).__name__)
        return None
    if not _KID_RE.fullmatch(kid):
        _emit_unavailable("signing_key_id_unsupported", error_type=None)
        return None
    binding = _actor_binding(actor, secret)
    if binding is None:
        _emit_unavailable("actor_missing", error_type=None)
        return None
    iat = int(time.time()) if now is None else int(now)
    claims = _claims(
        kid=kid,
        iat=iat,
        actor_binding=binding,
        objective=objective,
        states=states,
        plan=plan,
    )
    signature = _b64url_encode(_sign(secret, claims))
    return f"v1.{kid}.{iat:010d}.{signature}"


def verify_plan_digest(
    digest: str,
    *,
    actor: str,
    objective: str,
    states: list[str],
    plan: ComposedPlan,
    now: int | None = None,
) -> None:
    """Raise unless ``digest`` signs exactly this actor, objective, scope and plan.

    ``PlanDigestConflict("digest_mismatch")``: malformed, unknown key id,
    wrong signature (any bound claim differs) or an ``iat`` more than 60 s in
    the future. ``PlanDigestConflict("digest_expired")``: authentic but older
    than the two-hour review window. ``PlanDigestUnavailable``: no signing
    key resolves at all.
    """

    match = _WIRE_RE.fullmatch(digest or "")
    if match is None:
        raise PlanDigestConflict("digest_mismatch")
    kid, iat_text, signature_text = match.groups()
    try:
        supplied = _b64url_decode(signature_text)
    except (ValueError, binascii.Error) as exc:
        raise PlanDigestConflict("digest_mismatch") from exc
    try:
        keys = _action_token_keys(kid_hint=kid)
    except (RuntimeError, ValueError) as exc:
        raise PlanDigestUnavailable("no plan-digest signing key resolves") from exc

    iat = int(iat_text)
    verified = False
    for key_id, secret in keys:
        # Only the key the digest names: an unknown kid is a mismatch, never
        # a fall-through to whichever key happens to verify.
        if key_id != kid:
            continue
        binding = _actor_binding(actor, secret)
        if binding is None:
            break
        expected = _sign(
            secret,
            _claims(
                kid=kid,
                iat=iat,
                actor_binding=binding,
                objective=objective,
                states=states,
                plan=plan,
            ),
        )
        if hmac.compare_digest(supplied, expected):
            verified = True
            break
    if not verified:
        raise PlanDigestConflict("digest_mismatch")

    current = int(time.time()) if now is None else int(now)
    if iat > current + _FUTURE_SKEW_S:
        raise PlanDigestConflict("digest_mismatch")
    if current - iat > PLAN_DIGEST_TTL_S:
        raise PlanDigestConflict("digest_expired")


def _claims(
    *,
    kid: str,
    iat: int,
    actor_binding: str,
    objective: str,
    states: list[str],
    plan: ComposedPlan,
) -> dict[str, object]:
    return {
        "v": 1,
        "kid": kid,
        "iat": iat,
        "actor": actor_binding,
        "objective": hashlib.sha256(objective.encode("utf-8")).hexdigest(),
        "states": list(states),
        "plan": json.loads(canonical_plan_json(plan)),
    }


def _sign(secret: bytes, claims: dict[str, object]) -> bytes:
    canonical = json.dumps(claims, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hmac.new(secret, PLAN_DIGEST_DOMAIN + canonical.encode("ascii"), hashlib.sha256).digest()


def _actor_binding(actor: str, secret: bytes) -> str | None:
    normalized = (actor or "").strip().lower()
    if not normalized:
        return None
    return hmac.new(
        secret,
        PLAN_DIGEST_DOMAIN + b"actor\x00" + normalized.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _emit_unavailable(reason: str, *, error_type: str | None) -> None:
    emit(
        log,
        "growth_agent_plan_digest_unavailable",
        level=logging.WARNING,
        outcome="unsigned",
        unavailable_reason=reason,
        error_type=error_type,
    )


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    raw = base64.urlsafe_b64decode((value + "=" * (-len(value) % 4)).encode("ascii"))
    if _b64url_encode(raw) != value:
        raise ValueError("non-canonical base64url encoding")
    return raw


__all__ = [
    "PLAN_DIGEST_DOMAIN",
    "PLAN_DIGEST_TTL_S",
    "PlanDigestConflict",
    "PlanDigestConflictReason",
    "PlanDigestUnavailable",
    "canonical_plan_json",
    "issue_plan_digest",
    "verify_plan_digest",
]
