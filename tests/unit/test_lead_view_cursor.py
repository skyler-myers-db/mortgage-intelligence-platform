"""Signed Lead Queue view cursors (D-audit-reads-a): lead_view_cursor.

Each refusal has ONE closed reason (signature, filters, expired, page_cap,
malformed); a deployment with no secret outside local/test raises
LeadCursorUnavailable, never a bare RuntimeError; a cursor signed with the
previous secret still verifies; the readable payload never carries the actor;
and the view fingerprint covers request-level inputs only, deterministically.
"""

from __future__ import annotations

import base64
import json

import pytest
from pydantic import SecretStr

from backend.config.settings import settings
from backend.schemas.lead_query import lead_query_params
from backend.schemas.portfolio import PortfolioCriteria
from backend.services.lead_view_cursor import (
    CURSOR_TTL_S,
    LeadCursorRejected,
    LeadCursorUnavailable,
    decode_lead_cursor,
    encode_lead_cursor,
    lead_view_filter_digest,
    lead_view_filter_fingerprint,
    lead_view_fingerprint_candidates,
)
from backend.services.repositories.databricks_lead_order import RANK, LeadOrder

ACTOR = "lo.one@summit-mortgage.example"
VIEW = "0123456789abcdef0123456789abcdef"
NOW = 1_790_000_000
KEY = SecretStr("lead-cursor-key-0123456789abcdef")
OTHER_KEY = SecretStr("lead-cursor-key-fedcba9876543210")
HANDOFF = {
    "growth_agent_run_id": "run-1",
    "growth_agent_filters_fingerprint": "a" * 64,
    "growth_agent_cohort_fingerprint": "b" * 64,
    "growth_agent_source_snapshot": "snap-1",
    "tool_result_hash": "c" * 64,
}


@pytest.fixture(autouse=True)
def _deployed_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mip_genie_action_secret_current", KEY)
    monkeypatch.setattr(settings, "mip_genie_action_secret_previous", None)


def _digest(**overrides: object) -> str:
    params = lead_query_params(**overrides)  # type: ignore[arg-type]
    return lead_view_filter_digest(
        params, portfolio_criteria=None, order=RANK, growth_handoff=None, approval_request_batch=None
    )


def _fp(**overrides: object) -> str:
    value = lead_view_filter_fingerprint(_digest(**overrides))
    assert value is not None
    return value


def _cursor(**overrides: object) -> str:
    fields: dict[str, object] = {
        "actor": ACTOR,
        "view_id": VIEW,
        "page": 1,
        "fp": overrides["fp"] if "fp" in overrides else _fp(),
        "refreshed": "2026-09-29T06:00:00Z",
        "total": 1234,
        "handoff": None,
        "handoff_expires_at": None,
        "after": (-1, "B-0000000000499"),
        "now": NOW,
    }
    fields.update(overrides)
    return encode_lead_cursor(**fields)  # type: ignore[arg-type]


def _decode(value: str, *, actor: str = ACTOR, digest: str | None = None, now: int = NOW + 10):
    return decode_lead_cursor(
        value, actor=actor, fp_candidates=lead_view_fingerprint_candidates(digest or _digest()), now=now
    )


def _reason(value: str, **kwargs: object) -> str:
    with pytest.raises(LeadCursorRejected) as caught:
        _decode(value, **kwargs)  # type: ignore[arg-type]
    return caught.value.reason


def test_a_cursor_round_trips() -> None:
    cursor = _decode(_cursor(handoff=HANDOFF, handoff_expires_at=NOW + 7200))

    assert (cursor.view_id, cursor.page, cursor.total) == (VIEW, 1, 1234)
    assert cursor.refreshed == "2026-09-29T06:00:00Z"
    assert cursor.after == (-1, "B-0000000000499")
    assert cursor.handoff == HANDOFF
    assert cursor.exp == NOW + CURSOR_TTL_S


def test_a_tampered_byte_is_a_signature_refusal() -> None:
    value = _cursor()
    payload, signature = value.split(".")
    raw = bytearray(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    raw[raw.index(b"1234")] = ord("9")
    forged = base64.urlsafe_b64encode(bytes(raw)).decode().rstrip("=")

    assert _reason(f"{forged}.{signature}") == "signature"


def test_another_actors_cursor_is_a_signature_refusal() -> None:
    assert _reason(_cursor(), actor="lo.two@summit-mortgage.example") == "signature"


def test_the_actor_binding_ignores_case() -> None:
    assert _decode(_cursor(), actor=ACTOR.upper()).view_id == VIEW


def test_a_changed_filter_is_a_filters_refusal() -> None:
    assert _reason(_cursor(), digest=_digest(state="TX")) == "filters"


def test_a_changed_sort_is_a_filters_refusal() -> None:
    sorted_digest = lead_view_filter_digest(
        lead_query_params(),
        portfolio_criteria=None,
        order=LeadOrder.of("equity", "desc"),
        growth_handoff=None,
        approval_request_batch=None,
    )

    assert _reason(_cursor(), digest=sorted_digest) == "filters"


@pytest.mark.parametrize("handoff", [None, "other-handoff"])
def test_a_dropped_or_swapped_handoff_is_a_filters_refusal(handoff: str | None) -> None:
    def digest(raw: str | None) -> str:
        return lead_view_filter_digest(
            lead_query_params(), portfolio_criteria=None, order=RANK, growth_handoff=raw, approval_request_batch=None
        )

    minted = _cursor(fp=lead_view_filter_fingerprint(digest("signed-handoff")))

    assert _reason(minted, digest=digest(handoff)) == "filters"
    assert _decode(minted, digest=digest("signed-handoff")).view_id == VIEW


def test_an_expired_cursor_is_refused() -> None:
    assert _reason(_cursor(), now=NOW + CURSOR_TTL_S + 1) == "expired"


def test_a_cursor_never_outlives_its_handoff() -> None:
    value = _cursor(handoff=HANDOFF, handoff_expires_at=NOW + 600)

    assert _decode(value, now=NOW + 599).exp == NOW + 600
    assert _reason(value, now=NOW + 601) == "expired"


def test_page_ten_is_never_served() -> None:
    assert _decode(_cursor(page=9)).page == 9
    assert _reason(_cursor(page=10)) == "page_cap"


@pytest.mark.parametrize("value", ["", "no-dot", "a.b.c", "!!!.???", "x" * 2049, "e30.AAAA"])
def test_a_malformed_cursor_is_refused(value: str) -> None:
    assert _reason(value) in {"malformed", "signature"}


def test_a_signed_cursor_with_a_bad_shape_is_malformed() -> None:
    assert _reason(_cursor(view_id="NOT-HEX")) == "malformed"
    assert _reason(_cursor(after=(True, "B-1"))) == "malformed"


def test_a_cursor_signed_with_the_previous_secret_verifies(monkeypatch: pytest.MonkeyPatch) -> None:
    minted = _cursor()
    monkeypatch.setattr(settings, "mip_genie_action_secret_current", OTHER_KEY)
    monkeypatch.setattr(settings, "mip_genie_action_secret_previous", KEY)

    # The fingerprint rotated too: the cursor's fp is one of the candidates.
    assert _decode(minted).view_id == VIEW


def test_a_rotated_away_secret_no_longer_verifies(monkeypatch: pytest.MonkeyPatch) -> None:
    minted = _cursor()
    monkeypatch.setattr(settings, "mip_genie_action_secret_current", OTHER_KEY)

    assert _reason(minted) == "signature"


def test_no_secret_outside_local_is_unavailable_never_a_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mip_genie_action_secret_current", None)
    monkeypatch.setattr(settings, "mip_genie_action_secret", None)
    monkeypatch.setattr(settings, "app_env", "production")

    with pytest.raises(LeadCursorUnavailable):
        _cursor(fp="f" * 64)
    with pytest.raises(LeadCursorUnavailable):
        decode_lead_cursor("abc.def", actor=ACTOR, fp_candidates=[])
    assert lead_view_filter_fingerprint(_digest()) is None


def test_the_readable_payload_carries_no_actor_or_email() -> None:
    payload = _cursor().split(".")[0]
    text = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode()

    assert "@" not in text
    assert ACTOR.split("@")[0] not in text
    assert set(json.loads(text)) == {"v", "view", "page", "fp", "refreshed", "total", "iat", "exp", "handoff", "after"}


def test_the_fingerprint_is_keyed_deterministic_and_order_insensitive() -> None:
    first = _digest(states="TX,IL", zips="60617,75217", cities="CHICAGO~IL,AUSTIN~TX")
    again = _digest(states="IL,TX", zips="75217,60617", cities="AUSTIN~TX,CHICAGO~IL")

    assert first == again
    assert lead_view_filter_fingerprint(first) == lead_view_filter_fingerprint(again)
    assert lead_view_filter_fingerprint(first) != first


def test_a_portfolio_criteria_request_fingerprints_deterministically() -> None:
    criteria = PortfolioCriteria(marketing_eligibility="Eligible only", product="HELOC", occupancy="Owner-occupied")
    params = lead_query_params(product="HELOC", occupancy="Owner-occupied")  # type: ignore[arg-type]

    def digest() -> str:
        return lead_view_filter_digest(
            params, portfolio_criteria=criteria, order=RANK, growth_handoff=None, approval_request_batch=None
        )

    assert digest() == digest()
    assert digest() != _digest(product="HELOC", occupancy="Owner-occupied")


def test_the_fingerprint_excludes_limit_and_lakebase_resolved_lists() -> None:
    # limit is not a LeadQueryParams field and never reaches the digest; an
    # assignee filter is hashed as the request's email, not its borrowers.
    assert _digest(assigned_to="LO.One@summit-mortgage.example") == _digest(
        assigned_to="lo.one@summit-mortgage.example"
    )
    assert _digest(approval_status="pending") != _digest()
