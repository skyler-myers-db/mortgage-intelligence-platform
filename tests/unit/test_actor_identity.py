"""One forwarded-identity resolver for six readers (D-identity-review-a1).

``backend/services/actor_identity.py`` owns the edge-header read every
identity reader shares (health, session, audit ``resolve_actor``, visit
tracking, the authenticated RBAC gate and the backpressure bucket) and the
browser ``actor_cache_key`` derivation. These tests pin:

  (i)   the derivation byte for byte (a golden key computed once from the
        pre-move ``backend.api.health._actor_cache_key``), so a deploy of the
        move is an actor change to no open tab;
  (ii)  the secret precedence: current, then legacy, then the per-process
        secret, which is ONE per process;
  (iii) a parity table: every reader agrees with the shared helper for every
        header set, trust on and off, each keeping its own fallback;
  (iv)  health and session derive the same key for the same headers;
  (v)   the session's actor_cache_key is null exactly when actor_email is.
"""

from __future__ import annotations

import hashlib
import hmac

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.requests import Request

from backend.config.settings import settings
from backend.main import app
from backend.services import (
    actor_identity,
    audit_store,
    backpressure,
    health_probes,
    rbac,
    resilience,
    visit_tracking,
)
from backend.services.actor_identity import actor_cache_key, forwarded_identity

client = TestClient(app)

GOLDEN_SECRET = "test-secret-0123456789abcdef"
GOLDEN_IDENTITY = " Jane.Doe@Example.com "
# Computed ONCE from 0a30fca2's backend.api.health._actor_cache_key with the
# secret above; committed as a literal so a changed derivation cannot also
# change its own expectation.
GOLDEN_KEY = "actor_2ec47b8b33745216"

_LONG_EMAIL = "x" * 250 + "@example.com"  # 262 characters: over the bucket cap

# (label, headers, the identity the edge forwarded when trusted)
HEADER_SETS: list[tuple[str, dict[str, str], str | None]] = [
    ("email only", {"X-Forwarded-Email": "Jane.Doe@Example.com"}, "Jane.Doe@Example.com"),
    ("user only", {"X-Forwarded-User": "svc-user"}, "svc-user"),
    ("both", {"X-Forwarded-Email": "a@example.com", "X-Forwarded-User": "b-user"}, "a@example.com"),
    ("empty email plus user", {"X-Forwarded-Email": "", "X-Forwarded-User": "c-user"}, "c-user"),
    ("neither", {}, None),
    ("padded mixed case", {"X-Forwarded-Email": "  Mixed.Case@Example.COM "}, "  Mixed.Case@Example.COM "),
    ("over the bucket cap", {"X-Forwarded-Email": _LONG_EMAIL}, _LONG_EMAIL),
]


def _request(headers: dict[str, str]) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "query_string": b"",
            "headers": [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers.items()],
        }
    )


def _hmac_key(secret: str, identity: str) -> str:
    digest = hmac.new(secret.encode(), identity.strip().lower().encode(), hashlib.sha256).hexdigest()
    return f"actor_{digest[:16]}"


@pytest.fixture(autouse=True)
def _pinned_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mip_genie_action_secret_current", SecretStr(GOLDEN_SECRET))
    monkeypatch.setattr(settings, "mip_genie_action_secret", None)
    monkeypatch.setattr(health_probes, "probe_warehouse", lambda: True)
    monkeypatch.setattr(health_probes, "probe_lakebase", lambda: True)
    monkeypatch.setattr(health_probes, "probe_genie", lambda: True)
    resilience._reset_breakers_for_tests()
    health_probes._probe_cache.clear()


# ----------------------------------------------------------------- (i) golden


def test_golden_key_is_byte_identical_to_the_pre_move_derivation() -> None:
    assert actor_cache_key(GOLDEN_IDENTITY) == GOLDEN_KEY


def test_golden_key_normalises_only_inside_the_derivation() -> None:
    assert actor_cache_key("jane.doe@example.com") == GOLDEN_KEY
    assert actor_cache_key("JANE.DOE@EXAMPLE.COM\t") == GOLDEN_KEY


# ------------------------------------------------------- (ii) secret order


def test_secret_precedence_current_then_legacy_then_process(monkeypatch: pytest.MonkeyPatch) -> None:
    identity = "ops@example.com"
    monkeypatch.setattr(settings, "mip_genie_action_secret", SecretStr("legacy-secret"))
    assert actor_cache_key(identity) == _hmac_key(GOLDEN_SECRET, identity)

    monkeypatch.setattr(settings, "mip_genie_action_secret_current", None)
    assert actor_cache_key(identity) == _hmac_key("legacy-secret", identity)

    monkeypatch.setattr(settings, "mip_genie_action_secret", SecretStr("   "))
    process_key = _hmac_key(actor_identity._PROCESS_ACTOR_CACHE_SECRET, identity)
    assert actor_cache_key(identity) == process_key

    monkeypatch.setattr(settings, "mip_genie_action_secret", None)
    assert actor_cache_key(identity) == process_key


def test_the_per_process_secret_is_the_one_health_uses(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no secret configured, the health body still derives from the ONE
    per-process secret this module owns (health keeps no copy)."""
    monkeypatch.setattr(settings, "mip_genie_action_secret_current", None)
    monkeypatch.setattr(settings, "mip_genie_action_secret", None)
    body = client.get("/api/v1/health", headers={"X-Forwarded-Email": "ops@example.com"}).json()
    assert body["actor_cache_key"] == _hmac_key(actor_identity._PROCESS_ACTOR_CACHE_SECRET, "ops@example.com")


# ------------------------------------------------------- (iii) parity table


@pytest.mark.parametrize("trusted", [True, False], ids=["trusted", "untrusted"])
@pytest.mark.parametrize(("label", "headers", "forwarded"), HEADER_SETS, ids=[row[0] for row in HEADER_SETS])
def test_every_reader_agrees_with_the_shared_helper(
    monkeypatch: pytest.MonkeyPatch,
    label: str,
    headers: dict[str, str],
    forwarded: str | None,
    trusted: bool,
) -> None:
    monkeypatch.setattr(settings, "trust_forwarded_headers", trusted)
    monkeypatch.setattr(settings, "default_actor", "demo.default@summit.example")
    request = _request(headers)
    expected = forwarded if trusted else None

    # The helper itself: RAW value, Email wins, an empty Email falls through.
    assert forwarded_identity(request) == expected, label

    # Health: authenticated body with the helper's key, else the anonymous one.
    body = client.get("/api/v1/health", headers=headers).json()
    if expected:
        assert body["actor_cache_key"] == actor_cache_key(expected)
    else:
        assert body == {"status": body["status"], "mode": "live"}

    # Session: the signed-in person is exactly the helper's value, and its
    # seed key is null exactly when that is (v).
    session = client.get("/api/v1/session", headers={**headers, "X-Forwarded-Groups": ""}).json()
    assert session["actor_email"] == expected
    assert session["actor_cache_key"] == (actor_cache_key(expected) if expected else None)
    assert (session["actor_cache_key"] is None) == (session["actor_email"] is None)

    # Visit tracking: never a fallback identity.
    assert visit_tracking.forwarded_actor(request) == expected

    # The authenticated-read gate: 401 exactly where the helper returns None.
    if expected:
        assert rbac.require_authenticated_actor(request) == expected
    else:
        with pytest.raises(HTTPException) as denied:
            rbac.require_authenticated_actor(request)
        assert denied.value.status_code == 401

    # Audit attribution: the helper's value, else its own fallbacks.
    before = audit_store.get_fallback_identity_count()
    actor = audit_store.resolve_actor(request)
    bumped = audit_store.get_fallback_identity_count() - before
    if not trusted:
        assert (actor, bumped) == (audit_store._UNTRUSTED_EDGE_ACTOR, 0)
    elif expected:
        assert (actor, bumped) == (expected, 0)
    else:
        assert (actor, bumped) == ("demo.default@summit.example", 1)

    # Backpressure bucket: normalised helper value, capped, else a marker.
    bucket = backpressure.actor_key_for_request(request)
    if not trusted:
        assert bucket == "untrusted-edge"
    elif expected and len(expected) <= 254:
        assert bucket == expected.strip().lower()
    else:
        assert bucket == "anonymous"


def test_admin_health_derives_the_same_key_for_its_admitted_actor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "admin_emails", "ops@example.com")
    headers = {"X-Forwarded-Email": "ops@example.com", "X-Forwarded-Groups": ""}
    admin = client.get("/api/admin/health", headers=headers).json()
    runtime = client.get("/api/v1/health", headers=headers).json()
    assert admin["actor_cache_key"] == runtime["actor_cache_key"] == actor_cache_key("ops@example.com")


# ------------------------------------------- (iv) health and session agree


@pytest.mark.parametrize("configured", [True, False], ids=["configured-secret", "process-secret"])
@pytest.mark.parametrize(
    "headers",
    [row[1] for row in HEADER_SETS if row[2]],
    ids=[row[0] for row in HEADER_SETS if row[2]],
)
def test_health_and_session_derive_the_same_key(
    monkeypatch: pytest.MonkeyPatch,
    headers: dict[str, str],
    configured: bool,
) -> None:
    if not configured:
        monkeypatch.setattr(settings, "mip_genie_action_secret_current", None)
    health = client.get("/api/v1/health", headers=headers).json()
    session = client.get("/api/v1/session", headers={**headers, "X-Forwarded-Groups": ""}).json()
    assert health["actor_cache_key"] == session["actor_cache_key"]
    assert health["actor_cache_key"].startswith("actor_")
