"""Signed confirmation tokens for governed Genie actions.

Single responsibility: the HMAC confirmation-token contract. That is the
claim material a token binds (the validated borrower ids and the audited
criteria digest), the rotation-aware key resolution
(``MIP_GENIE_ACTION_SECRET_CURRENT`` / ``_PREVIOUS`` / ``_KID`` /
``_PREVIOUS_KID``), and signing plus verification of every signed Genie
surface (action confirmations and progress claims share one audited secret
path).

The operator-facing rotation contract is pinned here by
``tests/unit/test_disaster_recovery_contract.py``. Persistence, idempotency,
and action routing live in ``backend.services.genie_actions``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from backend.config.runtime_secret_policy import (
    require_distinct_rotation_secrets,
    require_strong_runtime_secret,
    runtime_secret_text,
)
from backend.config.settings import settings
from backend.schemas.common import validate_public_borrower_id
from backend.services.genie_action_filters import _MAX_ACTION_FILTER_VALUES
from backend.services.genie_answers import GenieMessageResponse
from backend.services.genie_trusted_assets import trusted_assets

_ACTION_TOKEN_TTL_S = 2 * 60 * 60
_PROCESS_ACTION_SECRET = secrets.token_urlsafe(32)
_LOCAL_TEST_APP_ENVS = frozenset({"local", "test"})
_PLACEHOLDER_ACTION_SECRETS = frozenset(
    {
        "redacted",
        "changeme",
        "change-me",
        "change_me",
        "placeholder",
        "example",
        "your-secret",
        "your_secret",
    }
)
_LIVE_CAMPAIGN_RUN_MARKER_RE = re.compile(r"gha[a-j]+r[a-j]+")


def borrower_ids(ids: list[str]) -> list[str]:
    out: list[str] = []
    for value in ids:
        try:
            borrower_id = validate_public_borrower_id(str(value))
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="Genie action includes invalid borrower id",
            ) from exc
        if borrower_id not in out:
            out.append(borrower_id)
    if len(out) > _MAX_ACTION_FILTER_VALUES:
        raise HTTPException(
            status_code=400,
            detail="Genie action returned too many borrower filters to replay safely",
        )
    return out


def criteria_summary(criteria: dict[str, Any]) -> tuple[str, list[str], list[str], str | None]:
    """Return the audited digest for reviewed Genie action criteria."""

    source_assets = _validated_source_assets(criteria)
    criteria_keys = sorted(str(k) for k in criteria)
    canonical_payload = {
        str(k): criteria[k] for k in sorted(criteria, key=lambda value: str(value))
    }
    canonical = json.dumps(
        canonical_payload,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    criteria_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    visualization_kind = criteria.get("visualization_kind")
    return (
        criteria_hash,
        criteria_keys,
        source_assets,
        str(visualization_kind) if visualization_kind else None,
    )


def _validated_source_assets(criteria: dict[str, Any]) -> list[str]:
    assets = [str(v) for v in criteria.get("source_assets", []) if isinstance(v, str)]
    trusted = set(trusted_assets())
    invalid = [asset for asset in assets if asset not in trusted]
    if invalid:
        raise HTTPException(status_code=400, detail="Genie action includes untrusted source assets")
    return assets[:10]


def _configured_secret_bytes(
    configured: Any,
    *,
    name: str,
    require_strong: bool,
) -> bytes | None:
    value = runtime_secret_text(
        configured,
        extra_placeholders=_PLACEHOLDER_ACTION_SECRETS,
    )
    if value is None:
        return None
    if require_strong:
        value = require_strong_runtime_secret(
            value,
            name=name,
            extra_placeholders=_PLACEHOLDER_ACTION_SECRETS,
        )
    return value.encode("utf-8")


def _action_token_key_id() -> str:
    value = (settings.mip_genie_action_secret_kid or "").strip()
    return value or "v1"


def _current_action_token_key() -> tuple[str, bytes]:
    app_env = (settings.app_env or "").strip().lower()
    require_strong = app_env not in _LOCAL_TEST_APP_ENVS
    try:
        secret = _configured_secret_bytes(
            settings.mip_genie_action_secret_current,
            name="MIP_GENIE_ACTION_SECRET_CURRENT",
            require_strong=require_strong,
        )
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    if secret is not None:
        return _action_token_key_id(), secret

    if app_env not in _LOCAL_TEST_APP_ENVS:
        raise RuntimeError(
            "MIP_GENIE_ACTION_SECRET_CURRENT is required outside local/test app environments"
        )

    legacy_secret = _configured_secret_bytes(
        settings.mip_genie_action_secret,
        name="MIP_GENIE_ACTION_SECRET",
        require_strong=False,
    )
    if legacy_secret is not None:
        return _action_token_key_id(), legacy_secret
    return "process", _PROCESS_ACTION_SECRET.encode("utf-8")


def _previous_action_token_key() -> tuple[str, bytes] | None:
    app_env = (settings.app_env or "").strip().lower()
    try:
        secret = _configured_secret_bytes(
            settings.mip_genie_action_secret_previous,
            name="MIP_GENIE_ACTION_SECRET_PREVIOUS",
            require_strong=app_env not in _LOCAL_TEST_APP_ENVS,
        )
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    if secret is None:
        return None
    current = _current_action_token_key()[1].decode("utf-8")
    try:
        require_distinct_rotation_secrets(
            current,
            secret.decode("utf-8"),
            current_name="MIP_GENIE_ACTION_SECRET_CURRENT",
            previous_name="MIP_GENIE_ACTION_SECRET_PREVIOUS",
        )
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    key_id = (settings.mip_genie_action_secret_previous_kid or "").strip() or "previous"
    return key_id, secret


def _action_token_keys(*, kid_hint: str | None = None) -> list[tuple[str, bytes]]:
    keys = [_current_action_token_key()]
    previous = _previous_action_token_key()
    if previous is not None:
        keys.append(previous)
    if kid_hint:
        matching = [item for item in keys if item[0] == kid_hint]
        nonmatching = [item for item in keys if item[0] != kid_hint]
        return matching + nonmatching
    return keys


def _action_token_secret() -> bytes:
    """Return the current signing secret.

    Kept as a small compatibility helper for tests and adjacent modules; new
    verification code uses ``_action_token_keys`` so previous-key grace windows
    can validate in-flight tokens during rotation.
    """

    _key_id, secret = _current_action_token_key()
    return secret


def current_action_token_secret_for_cache() -> str:
    """Return a stable, non-logged secret string for actor-cache hashing."""

    return _action_token_secret().decode("utf-8")


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padded = value + ("=" * (-len(value) % 4))
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _action_token_claims(
    *,
    actor: str,
    action_type: str,
    borrower_ids: list[str],
    criteria: dict[str, Any],
    route: str | None,
    conversation_id: str | None,
    message_id: str | None,
    question_hash: str | None,
    request_id: str,
    expires_at: int,
    nonce: str,
    key_id: str | None = None,
    live_campaign_run_marker: str | None = None,
) -> dict[str, Any]:
    criteria_hash, _criteria_keys, source_assets, _visualization_kind = criteria_summary(criteria)
    claims = {
        "v": 1,
        "actor": actor,
        "action_type": action_type,
        "borrower_ids": sorted(set(borrower_ids)),
        "conversation_id": conversation_id or "",
        "criteria_hash": criteria_hash,
        "exp": expires_at,
        "message_id": message_id or "",
        "nonce": nonce,
        "question_hash": question_hash or "",
        "request_id": request_id,
        "route": route or "",
        "trusted_assets": sorted(set(source_assets)),
    }
    if key_id is not None:
        claims["kid"] = key_id
    if live_campaign_run_marker is not None:
        claims["live_campaign_run_marker"] = live_campaign_run_marker
    return claims


def normalize_live_campaign_run_marker(value: object) -> str | None:
    """Accept only the non-PII marker format derived by the live workflow."""

    if value is None or value == "":
        return None
    marker = str(value).strip()
    if len(marker) > 40 or not _LIVE_CAMPAIGN_RUN_MARKER_RE.fullmatch(marker):
        raise ValueError("live campaign run marker is invalid")
    return marker


def _sign_action_claims(claims: dict[str, Any]) -> str:
    canonical = json.dumps(claims, sort_keys=True, separators=(",", ":"), default=str)
    body = _b64url_encode(canonical.encode("utf-8"))
    sig = hmac.new(
        _action_token_secret(),
        body.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{body}.{_b64url_encode(sig)}"


def issue_response_action_tokens(
    response: GenieMessageResponse,
    *,
    actor: str,
    live_campaign_run_marker: str | None = None,
) -> None:
    normalized_run_marker = normalize_live_campaign_run_marker(live_campaign_run_marker)
    signed_actions = []
    for action in response.actions:
        expires_at = int(time.time()) + _ACTION_TOKEN_TTL_S
        request_id = action.request_id or f"genie-action-{uuid4()}"
        action.request_id = request_id
        key_id, _secret = _current_action_token_key()
        try:
            claims = _action_token_claims(
                actor=actor,
                action_type=action.action_type,
                borrower_ids=borrower_ids(action.borrower_ids),
                criteria=action.criteria,
                route=action.route,
                conversation_id=response.conversation_id,
                message_id=response.message_id,
                question_hash=response.question_hash,
                request_id=request_id,
                expires_at=expires_at,
                nonce=secrets.token_urlsafe(12),
                key_id=key_id,
                live_campaign_run_marker=(
                    normalized_run_marker
                    if action.action_type == "create_draft_campaign"
                    else None
                ),
            )
        except HTTPException:
            # Response actions are optional affordances. If a raw Genie answer
            # returns an oversized or unsafe replay action, preserve the answer
            # and proof but omit the unsafe confirmation path. Confirmed action
            # requests still use the strict validators below.
            continue
        action.confirmation_token = _sign_action_claims(claims)
        signed_actions.append(action)
    response.actions = signed_actions


def sign_genie_claims(claims: dict[str, Any]) -> str:
    """Public HMAC signer for Genie-surface claims (progress tokens, actions).

    Same key material, rotation grace, and wire format as action tokens so
    one audited secret path covers every signed Genie surface. Callers must
    include a distinct ``kind`` claim; verifiers reject foreign kinds.
    """

    return _sign_action_claims(claims)


def decode_genie_claims(token: str) -> dict[str, Any]:
    """Public verifier counterpart to :func:`sign_genie_claims`."""

    return _decode_action_token(token)


def genie_claims_key_id() -> str:
    """Expose the current signing key id for claims that carry ``kid``."""

    return _current_action_token_key()[0]


def _decode_action_token(token: str) -> dict[str, Any]:
    try:
        body, supplied_sig = token.split(".", 1)
        claims = json.loads(_b64url_decode(body).decode("utf-8"))
        if not isinstance(claims, dict):
            raise ValueError("claims body is not an object")
        actual_sig = _b64url_decode(supplied_sig)
        kid_hint = str(claims.get("kid") or "") or None
        for _key_id, secret in _action_token_keys(kid_hint=kid_hint):
            expected_sig = hmac.new(
                secret,
                body.encode("ascii"),
                hashlib.sha256,
            ).digest()
            if hmac.compare_digest(actual_sig, expected_sig):
                break
        else:
            raise ValueError("bad signature")
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail="Genie action confirmation token is invalid",
        ) from exc
    if not isinstance(claims, dict):
        raise HTTPException(status_code=400, detail="Genie action confirmation token is invalid")
    return claims
