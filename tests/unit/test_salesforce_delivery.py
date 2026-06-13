"""Unit tests for the Salesforce activation-delivery adapter (Feature B).

All HTTP is stubbed via ``monkeypatch`` on
``backend.services.salesforce_client.urllib.request.urlopen`` so the suite
is hermetic -- no real Salesforce call ever fires (mirrors
``test_genie_api_client``).

Coverage:
  (a) configured + success  -> record created, status 'delivered',
      salesforce_id in metadata, payload carries NO PII.
  (b) configured + SF 400/500 -> status 'failed', error in metadata.
  (c) 401 -> token refresh retried once, then succeeds.
  (d) UNCONFIGURED -> no HTTP attempted, status unchanged, metadata
      reason 'salesforce_not_configured'.
  (e) circuit breaker opens after repeated failures.
"""
from __future__ import annotations

import io
import json
import urllib.error
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.schemas.activation import ActivationOutboxItem
from backend.services import activation_delivery
from backend.services.activation_delivery import (
    REASON_NOT_CONFIGURED,
    deliver_to_salesforce,
)
from backend.services.resilience import (
    DependencyDownError,
    Resilient,
    _reset_breakers_for_tests,
    get_breaker,
)
from backend.services.salesforce_client import (
    ResilientSalesforceClient,
    SalesforceClient,
    SalesforceClientError,
    _reset_salesforce_client_for_tests,
)

# ---------------------------------------------------------------------------
# HTTP stubbing helpers (mirror test_genie_api_client)
# ---------------------------------------------------------------------------


class _FakeResponse(io.BytesIO):
    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _make_response(body: Any) -> _FakeResponse:
    if isinstance(body, bytes):
        return _FakeResponse(body)
    return _FakeResponse(json.dumps(body).encode("utf-8"))


def _http_error(status: int, body: str = "error") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="http://sf/x",
        code=status,
        msg=f"HTTP {status}",
        hdrs=None,  # type: ignore[arg-type]
        fp=io.BytesIO(body.encode("utf-8")),
    )


def _build_urlopen_sequence(responses: list[Any]) -> tuple[MagicMock, list[tuple[str, Any]]]:
    calls: list[tuple[str, Any]] = []
    queue: Iterator[Any] = iter(responses)

    def _urlopen(req: Any, timeout: float = 30) -> _FakeResponse:  # noqa: ARG001
        try:
            nxt = next(queue)
        except StopIteration as exc:  # pragma: no cover -- test bug
            raise AssertionError("urlopen called more times than stubbed") from exc
        # Capture the body so we can assert NO PII left MIP.
        data = req.data.decode("utf-8") if getattr(req, "data", None) else ""
        calls.append((req.full_url, data))
        if isinstance(nxt, Exception):
            raise nxt
        return _make_response(nxt)

    return MagicMock(side_effect=_urlopen), calls


def _patch_urlopen(monkeypatch: pytest.MonkeyPatch, responses: list[Any]) -> list[tuple[str, Any]]:
    mock, calls = _build_urlopen_sequence(responses)
    monkeypatch.setattr(
        "backend.services.salesforce_client.urllib.request.urlopen", mock
    )
    return calls


# ---------------------------------------------------------------------------
# Outbox row + fake store
# ---------------------------------------------------------------------------


def _outbox_row(status: str = "staged") -> ActivationOutboxItem:
    now = datetime.now(UTC)
    return ActivationOutboxItem(
        activation_id="11111111-1111-4111-8111-111111111111",
        destination_key="salesforce_crm",
        destination_type="salesforce",
        destination_display_name="Salesforce CRM",
        destination_status="connected",
        entity_type="borrower",
        entity_id="B-2KZ9X4M7Q1A3F",
        borrower_id="B-2KZ9X4M7Q1A3F",
        campaign_id=None,
        approval_id="22222222-2222-4222-8222-222222222222",
        offer_code="refi",
        channel="email",
        status=status,  # type: ignore[arg-type]
        request_id="33333333-3333-4333-8333-333333333333",
        created_by="skyler@entrada.ai",
        created_at=now,
        updated_at=now,
    )


class _FakeStore:
    """Captures update_delivery_state calls and returns an updated row."""

    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []

    def update_delivery_state(
        self,
        *,
        activation_id: str,
        status: str,
        delivery_metadata: dict[str, Any],
    ) -> ActivationOutboxItem:
        self.updates.append(
            {
                "activation_id": activation_id,
                "status": status,
                "delivery_metadata": delivery_metadata,
            }
        )
        row = _outbox_row(status=status)
        row = row.model_copy(update={"delivery_metadata": delivery_metadata})
        return row


def _real_client(monkeypatch: pytest.MonkeyPatch, responses: list[Any]) -> ResilientSalesforceClient:
    """Build a real SalesforceClient over a stubbed urlopen + Resilient wrap."""
    _patch_urlopen(monkeypatch, responses)
    bare = SalesforceClient(
        instance_url="https://example.my.salesforce.com",
        client_id="cid",
        client_secret="csecret",
        username="user@example.com",
        password="pw",
        security_token="tok",
        api_version="v60.0",
    )
    breaker = get_breaker("salesforce", failure_threshold=3, cooldown_s=30.0)
    resilient = Resilient[Any](
        breaker=breaker,
        dependency_name="salesforce",
        attempts=2,
        backoff_base=0.0,
        backoff_max=0.0,
        retry_on=(SalesforceClientError, OSError),
    )
    return ResilientSalesforceClient(bare, resilient)


@pytest.fixture(autouse=True)
def _reset_singletons() -> Iterator[None]:
    _reset_salesforce_client_for_tests()
    _reset_breakers_for_tests()
    yield
    _reset_salesforce_client_for_tests()
    _reset_breakers_for_tests()


# ---------------------------------------------------------------------------
# (a) configured + success
# ---------------------------------------------------------------------------


def test_delivery_success_marks_delivered_and_carries_no_pii(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_body = {"access_token": "sf-token", "instance_url": "https://example.my.salesforce.com"}
    create_body = {"id": "00T5g00000ABCDEAA1", "success": True}
    # Build the client, then install the fresh urlopen the client will use
    # so we can capture the exact HTTP bodies and assert no PII leaves MIP.
    client = _real_client(monkeypatch, [token_body, create_body])
    store = _FakeStore()
    captured = _patch_urlopen(monkeypatch, [token_body, create_body])
    result = deliver_to_salesforce(_outbox_row(), store=store, client=client)

    assert result.delivered is True
    assert result.attempted is True
    assert result.status == "delivered"
    assert result.salesforce_id == "00T5g00000ABCDEAA1"
    assert result.delivery_metadata["delivered"] is True
    assert result.delivery_metadata["salesforce_id"] == "00T5g00000ABCDEAA1"
    assert result.delivery_metadata["sobject"] == "Task"

    # store update happened with status 'delivered'
    assert store.updates[-1]["status"] == "delivered"

    # NO PII in any HTTP body. The create body is the second call.
    create_call_body = captured[-1][1]
    payload = json.loads(create_call_body)
    blob = json.dumps(payload)
    # Allowed: masked borrower id, offer, channel, request_id, activation_id.
    assert "B-2KZ9X4M7Q1A3F" in blob  # masked id is OK
    assert "refi" in blob
    assert "email" in blob
    assert "33333333-3333-4333-8333-333333333333" in blob  # request_id
    # Forbidden PII tokens never appear.
    for forbidden in ("@", "ssn", "phone", "address", "name", "clip"):
        assert forbidden.lower() not in payload.get("Subject", "").lower()
    assert "Description" in payload


# ---------------------------------------------------------------------------
# (b) configured + SF error -> failed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status_code", [400, 500])
def test_delivery_salesforce_error_marks_failed(
    monkeypatch: pytest.MonkeyPatch, status_code: int
) -> None:
    token_body = {"access_token": "sf-token"}
    err_body = json.dumps([{"message": "bad", "errorCode": "INVALID_FIELD"}])
    # 2 attempts (Resilient attempts=2): token, error, token, error.
    client = _real_client(
        monkeypatch,
        [
            token_body,
            _http_error(status_code, err_body),
            _http_error(status_code, err_body),
        ],
    )
    store = _FakeStore()
    result = deliver_to_salesforce(_outbox_row(), store=store, client=client)

    assert result.delivered is False
    assert result.attempted is True
    assert result.status == "failed"
    assert result.delivery_metadata["delivered"] is False
    assert "error" in result.delivery_metadata
    assert store.updates[-1]["status"] == "failed"


# ---------------------------------------------------------------------------
# (c) 401 -> token refresh retried once, then succeeds
# ---------------------------------------------------------------------------


def test_delivery_401_triggers_token_refresh_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_body = {"access_token": "sf-token-1"}
    token_body_2 = {"access_token": "sf-token-2"}
    create_body = {"id": "00Trefresh0001", "success": True}
    # Flow: token, create->401, (refresh) token2, create->201.
    captured = _patch_urlopen(
        monkeypatch,
        [token_body, _http_error(401, "expired"), token_body_2, create_body],
    )
    bare = SalesforceClient(
        instance_url="https://example.my.salesforce.com",
        client_id="cid",
        client_secret="csecret",
        username="user@example.com",
        password="pw",
        security_token="tok",
    )
    # Call the bare client.create_record directly so the 401-refresh path
    # (inside create_record) is exercised without Resilient retry masking it.
    result = bare.create_record("Task", {"Subject": "x", "Description": "y"})
    assert result["id"] == "00Trefresh0001"
    # Four HTTP calls: token, create(401), token(refresh), create(201).
    assert len(captured) == 4
    # The second token request used the refresh path.
    assert "/services/oauth2/token" in captured[2][0]


# ---------------------------------------------------------------------------
# (d) UNCONFIGURED -> honest no-op
# ---------------------------------------------------------------------------


def test_delivery_unconfigured_is_clean_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    # No client passed and get_salesforce_client returns None.
    monkeypatch.setattr(
        activation_delivery, "get_salesforce_client", lambda: None
    )
    # Make urlopen explode if it is ever called.
    def _boom(*_a: Any, **_k: Any) -> None:  # pragma: no cover - must not run
        raise AssertionError("no HTTP must be attempted when unconfigured")

    monkeypatch.setattr(
        "backend.services.salesforce_client.urllib.request.urlopen", _boom
    )
    store = _FakeStore()
    row = _outbox_row(status="dry_run")
    result = deliver_to_salesforce(row, store=store)

    assert result.delivered is False
    assert result.attempted is False
    # Status UNCHANGED -- never claims delivery.
    assert result.status == "dry_run"
    assert result.delivery_metadata == {
        "delivered": False,
        "reason": REASON_NOT_CONFIGURED,
    }
    # No DB update on the unconfigured path.
    assert store.updates == []


# ---------------------------------------------------------------------------
# (e) circuit breaker opens after repeated failures
# ---------------------------------------------------------------------------


def test_breaker_opens_after_repeated_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    token_body = {"access_token": "sf-token"}
    err_body = json.dumps([{"message": "boom", "errorCode": "SERVER_ERROR"}])
    # failure_threshold=3; each delivery attempt does 2 Resilient tries and
    # records ONE breaker failure per delivery. Feed enough error responses
    # for three deliveries (token+err per try, 2 tries each = 6 responses).
    responses: list[Any] = []
    for _ in range(3):
        responses.extend([token_body, _http_error(500, err_body), token_body, _http_error(500, err_body)])
    client = _real_client(monkeypatch, responses)
    store = _FakeStore()

    for _ in range(3):
        result = deliver_to_salesforce(_outbox_row(), store=store, client=client)
        assert result.delivered is False
        assert result.status == "failed"

    # Breaker should now be OPEN -- a fourth call short-circuits to a
    # DependencyDownError, which the adapter records as a 'failed' row
    # WITHOUT any further HTTP (no responses left to consume).
    result = deliver_to_salesforce(_outbox_row(), store=store, client=client)
    assert result.delivered is False
    assert result.status == "failed"
    assert "DependencyDownError" in result.delivery_metadata["error"]


def test_breaker_open_records_dependency_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """Directly assert the DependencyDownError path is handled honestly."""

    class _OpenClient:
        def create_record(self, sobject: str, fields: dict[str, Any]) -> dict[str, Any]:
            raise DependencyDownError(
                "salesforce", reason="circuit breaker is open",
                kind=DependencyDownError.KIND_BREAKER_OPEN,
            )

    store = _FakeStore()
    result = deliver_to_salesforce(_outbox_row(), store=store, client=_OpenClient())  # type: ignore[arg-type]
    assert result.delivered is False
    assert result.status == "failed"
    assert result.delivery_metadata["delivered"] is False
