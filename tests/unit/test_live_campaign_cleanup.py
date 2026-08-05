from __future__ import annotations

from copy import deepcopy
from typing import Any
from unittest.mock import MagicMock

import pytest

from tests.integration.live_campaign_cleanup import (
    CampaignFixtureTracker,
    archive_campaign_fixture,
    reconcile_campaign_fixture,
)
from tools.cleanup_live_campaign_fixtures import run_scoped_campaign_name


def test_live_campaign_cleanup_retries_cas_and_uses_only_public_api() -> None:
    states = iter(("pending_review", "rejected", "archived"))
    calls: list[tuple[str, str, dict[str, object] | None, str]] = []

    def request(
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        *,
        token: str = "operator-token",
    ) -> tuple[int, object]:
        calls.append((method, path, payload, token))
        if method == "GET":
            return 200, {"status": next(states)}
        if payload and payload["expected_status"] == "pending_review":
            return 409, {"detail": "concurrent transition"}
        return 200, {"status": "archived"}

    archive_campaign_fixture(
        request,
        campaign_id="campaign-id",
        admin_token="admin-token",
    )

    assert [call[0] for call in calls] == ["GET", "PATCH", "GET", "GET"]
    assert all(call[1] == "/api/campaigns/campaign-id" for call in calls)
    assert all(call[3] == "admin-token" for call in calls)


def test_run_scoped_campaign_name_requires_encoded_run_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIP_LIVE_CAMPAIGN_RUN_MARKER", "ghabcdearf")
    assert run_scoped_campaign_name("Live campaign audit contract") == (
        "Live campaign audit contract ghabcdearf"
    )

    monkeypatch.setenv("MIP_LIVE_CAMPAIGN_RUN_MARKER", "gha-123-1")
    with pytest.raises(RuntimeError, match="marker or label"):
        run_scoped_campaign_name("Live campaign audit contract")
    with pytest.raises(RuntimeError, match="marker or label"):
        run_scoped_campaign_name("Unreviewed fixture label")


def test_live_campaign_cleanup_failure_is_release_blocking() -> None:
    def request(*_args: Any, **_kwargs: Any) -> tuple[int, object]:
        return 503, {"detail": "unavailable"}

    with pytest.raises(AssertionError, match="governed campaign cleanup failed"):
        archive_campaign_fixture(
            request,
            campaign_id="campaign-id",
            admin_token="admin-token",
            attempts=2,
            retry_interval_seconds=0,
        )


def test_tracker_reconciles_create_commit_then_timeout_and_archive_timeout() -> None:
    campaign_id = "campaign-from-ambiguous-create"
    state = {"created": False, "status": "pending_review", "patches": 0}
    create_payload: dict[str, object] = {"name": "Isolated live fixture"}
    replay_payloads: list[dict[str, object]] = []

    def request(
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        *,
        token: str = "operator-token",
        idempotency_key: str | None = None,
    ) -> tuple[int, object]:
        if method == "POST":
            assert path == "/api/portfolio/create"
            assert idempotency_key == "create-key"
            assert payload is not None
            replay_payloads.append(deepcopy(payload))
            if not state["created"]:
                state["created"] = True
                raise TimeoutError("response lost after commit")
            return 200, {"campaign_id": campaign_id}
        if method == "GET":
            assert token == "admin-token"
            return 200, {"status": state["status"]}
        assert method == "PATCH"
        assert token == "admin-token"
        state["status"] = "archived"
        state["patches"] = int(state["patches"]) + 1
        raise TimeoutError("archive response lost after commit")

    tracker = CampaignFixtureTracker(default_token="operator-token")
    with pytest.raises(TimeoutError, match="after commit"):
        tracker.request(
            request,
            "POST",
            "/api/portfolio/create",
            create_payload,
            idempotency_key="create-key",
        )
    create_payload["name"] = "mutated after transport failure"

    tracker.cleanup(request, admin_token="admin-token", sleep=lambda _seconds: None)

    assert replay_payloads == [
        {"name": "Isolated live fixture"},
        {"name": "Isolated live fixture"},
    ]
    assert state == {"created": True, "status": "archived", "patches": 1}


def test_tracker_rejects_unreplayable_create_before_mutation() -> None:
    request = MagicMock(return_value=(200, {"campaign_id": "unexpected"}))
    tracker = CampaignFixtureTracker(default_token="operator-token")

    with pytest.raises(RuntimeError, match="idempotency key"):
        tracker.request(
            request,
            "POST",
            "/api/portfolio/create",
            {"name": "unsafe"},
        )

    request.assert_not_called()


def test_tracker_rejects_payload_or_actor_drift_for_one_idempotency_key() -> None:
    request = MagicMock(return_value=(200, {"campaign_id": "campaign-id"}))
    tracker = CampaignFixtureTracker(default_token="operator-token")
    tracker.request(
        request,
        "POST",
        "/api/portfolio/create",
        {"name": "original"},
        idempotency_key="create-key",
    )

    with pytest.raises(RuntimeError, match="different intent"):
        tracker.request(
            request,
            "POST",
            "/api/portfolio/create",
            {"name": "changed"},
            token="other-operator-token",
            idempotency_key="create-key",
        )

    assert request.call_count == 1


def test_explicit_conflict_probe_reaches_server_but_preserves_original_cleanup() -> None:
    campaign_id = "campaign-from-original-intent"
    posts: list[dict[str, object]] = []
    state = {"archived": False}

    def request(
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        *,
        token: str = "operator-token",
        idempotency_key: str | None = None,
    ) -> tuple[int, object]:
        if method == "POST":
            assert payload is not None
            posts.append(deepcopy(payload))
            if payload["name"] == "conflict":
                return 409, {"detail": "different campaign payload"}
            return 200, {"campaign_id": campaign_id}
        if method == "GET":
            return 200, {"status": "archived" if state["archived"] else "pending_review"}
        assert method == "PATCH"
        state["archived"] = True
        return 200, {"status": "archived"}

    tracker = CampaignFixtureTracker(default_token="operator-token")
    tracker.request(
        request,
        "POST",
        "/api/portfolio/create",
        {"name": "original"},
        idempotency_key="create-key",
    )

    status, _body = tracker.conflict_probe(
        request,
        "POST",
        "/api/portfolio/create",
        {"name": "conflict"},
        idempotency_key="create-key",
    )
    assert status == 409
    tracker.cleanup(request, admin_token="admin-token", sleep=lambda _seconds: None)

    assert posts == [
        {"name": "original"},
        {"name": "conflict"},
    ]
    assert state == {"archived": True}


def test_conflict_probe_rejects_unresolved_original_and_actor_drift() -> None:
    def timeout_request(*_args: object, **_kwargs: object) -> tuple[int, object]:
        raise TimeoutError("no confirmed response")

    unresolved = CampaignFixtureTracker(default_token="operator-token")
    with pytest.raises(TimeoutError):
        unresolved.request(
            timeout_request,
            "POST",
            "/api/portfolio/create",
            {"name": "original"},
            idempotency_key="unresolved-key",
        )
    with pytest.raises(RuntimeError, match="captured, different intent"):
        unresolved.conflict_probe(
            MagicMock(),
            "POST",
            "/api/portfolio/create",
            {"name": "conflict"},
            idempotency_key="unresolved-key",
        )

    confirmed_request = MagicMock(return_value=(200, {"campaign_id": "campaign-id"}))
    confirmed = CampaignFixtureTracker(default_token="operator-token")
    confirmed.request(
        confirmed_request,
        "POST",
        "/api/portfolio/create",
        {"name": "original"},
        idempotency_key="confirmed-key",
    )
    with pytest.raises(RuntimeError, match="captured, different intent"):
        confirmed.conflict_probe(
            confirmed_request,
            "POST",
            "/api/portfolio/create",
            {"name": "conflict"},
            token="other-operator-token",
            idempotency_key="confirmed-key",
        )
    assert confirmed_request.call_count == 1


def test_conflict_probe_archives_an_unexpected_second_campaign() -> None:
    original_id = "original-campaign-id"
    conflict_id = "unexpected-conflict-campaign-id"
    statuses = {original_id: "pending_review", conflict_id: "pending_review"}

    def request(
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        **_kwargs: object,
    ) -> tuple[int, object]:
        if method == "POST":
            assert payload is not None
            campaign_id = conflict_id if payload["name"] == "conflict" else original_id
            return 200, {"campaign_id": campaign_id}
        campaign_id = path.rsplit("/", 1)[-1]
        if method == "GET":
            return 200, {"status": statuses[campaign_id]}
        statuses[campaign_id] = "archived"
        return 200, {"status": "archived"}

    tracker = CampaignFixtureTracker(default_token="operator-token")
    tracker.request(
        request,
        "POST",
        "/api/portfolio/create",
        {"name": "original"},
        idempotency_key="create-key",
    )
    status, body = tracker.conflict_probe(
        request,
        "POST",
        "/api/portfolio/create",
        {"name": "conflict"},
        idempotency_key="create-key",
    )
    assert (status, body) == (200, {"campaign_id": conflict_id})

    tracker.cleanup(request, admin_token="admin-token", sleep=lambda _seconds: None)

    assert statuses == {original_id: "archived", conflict_id: "archived"}


def test_conflict_probe_reconciles_commit_then_timeout_before_cleanup() -> None:
    original_id = "original-campaign-id"
    conflict_id = "ambiguous-conflict-campaign-id"
    statuses = {original_id: "pending_review", conflict_id: "pending_review"}
    conflict_committed = False
    conflict_posts = 0

    def request(
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        **_kwargs: object,
    ) -> tuple[int, object]:
        nonlocal conflict_committed, conflict_posts
        if method == "POST":
            assert payload is not None
            if payload["name"] == "conflict":
                conflict_posts += 1
                if not conflict_committed:
                    conflict_committed = True
                    raise TimeoutError("conflict committed before response")
                return 200, {"campaign_id": conflict_id}
            return 200, {"campaign_id": original_id}
        campaign_id = path.rsplit("/", 1)[-1]
        if method == "GET":
            return 200, {"status": statuses[campaign_id]}
        statuses[campaign_id] = "archived"
        return 200, {"status": "archived"}

    tracker = CampaignFixtureTracker(default_token="operator-token")
    tracker.request(
        request,
        "POST",
        "/api/portfolio/create",
        {"name": "original"},
        idempotency_key="create-key",
    )
    with pytest.raises(TimeoutError, match="before response"):
        tracker.conflict_probe(
            request,
            "POST",
            "/api/portfolio/create",
            {"name": "conflict"},
            idempotency_key="create-key",
        )

    tracker.cleanup(request, admin_token="admin-token", sleep=lambda _seconds: None)

    assert conflict_posts == 2
    assert statuses == {original_id: "archived", conflict_id: "archived"}


def test_ambiguous_create_cleanup_waits_through_the_full_build_lease() -> None:
    campaign_id = "campaign-after-lease-reclaim"
    clock = {"elapsed": 0.0, "status": "draft"}
    replay_attempts = 0

    def sleep(seconds: float) -> None:
        clock["elapsed"] = float(clock["elapsed"]) + seconds

    def request(
        method: str,
        path: str,
        _payload: dict[str, object] | None = None,
        **_kwargs: object,
    ) -> tuple[int, object]:
        nonlocal replay_attempts
        if method == "POST":
            replay_attempts += 1
            if replay_attempts == 1:
                raise TimeoutError("reservation response lost")
            if float(clock["elapsed"]) < 300.0:
                return 409, {"detail": "campaign materialization is already in progress"}
            return 200, {"campaign_id": campaign_id}
        if method == "GET":
            return 200, {
                "status": clock["status"],
                "treatment_state": "ready",
            }
        clock["status"] = "archived"
        return 200, {"status": "archived"}

    tracker = CampaignFixtureTracker(default_token="operator-token")
    with pytest.raises(TimeoutError, match="response lost"):
        tracker.request(
            request,
            "POST",
            "/api/portfolio/create",
            {"name": "original"},
            idempotency_key="create-key",
        )

    tracker.cleanup(
        request,
        admin_token="admin-token",
        sleep=sleep,
    )

    assert float(clock["elapsed"]) >= 300.0
    assert replay_attempts >= 62
    assert clock["status"] == "archived"


def test_reconciliation_spaces_transport_and_http_failures_before_success() -> None:
    outcomes: list[object] = [
        TimeoutError("first response lost"),
        (503, {"detail": "unavailable"}),
        OSError("connection reset"),
        (200, {"campaign_id": "recovered-id"}),
    ]
    sleeps: list[float] = []

    def request(*_args: object, **_kwargs: object) -> tuple[int, object]:
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        assert isinstance(outcome, tuple)
        return outcome

    campaign_id = reconcile_campaign_fixture(
        request,
        payload={"name": "original"},
        idempotency_key="create-key",
        token="operator-token",
        sleep=sleeps.append,
    )

    assert campaign_id == "recovered-id"
    assert sleeps == [5.0, 5.0, 5.0]


def test_archive_retries_transient_patch_and_confirms_with_get() -> None:
    patch_attempts = 0
    status = "pending_review"
    calls: list[str] = []

    def request(
        method: str,
        _path: str,
        _payload: dict[str, object] | None = None,
        **_kwargs: object,
    ) -> tuple[int, object]:
        nonlocal patch_attempts, status
        calls.append(method)
        if method == "GET":
            return 200, {"status": status, "treatment_state": "ready"}
        patch_attempts += 1
        if patch_attempts == 1:
            return 503, {"detail": "temporary dependency failure"}
        status = "archived"
        return 200, {"status": "archived"}

    archive_campaign_fixture(
        request,
        campaign_id="campaign-id",
        admin_token="admin-token",
        retry_interval_seconds=0,
    )

    assert calls == ["GET", "PATCH", "GET", "GET", "PATCH", "GET"]


def test_archive_accepts_scalar_success_on_final_attempt_only_after_get() -> None:
    calls: list[str] = []
    status = "draft"

    def request(
        method: str,
        _path: str,
        _payload: dict[str, object] | None = None,
        **_kwargs: object,
    ) -> tuple[int, object]:
        nonlocal status
        calls.append(method)
        if method == "GET":
            return 200, {"status": status}
        status = "archived"
        return 200, "mutation response intentionally ignored"

    archive_campaign_fixture(
        request,
        campaign_id="campaign-id",
        admin_token="admin-token",
        attempts=1,
        retry_interval_seconds=0,
    )

    assert calls == ["GET", "PATCH", "GET"]


def test_archive_reconciles_final_patch_commit_then_transport_loss() -> None:
    calls: list[str] = []
    status = "draft"

    def request(
        method: str,
        _path: str,
        _payload: dict[str, object] | None = None,
        **_kwargs: object,
    ) -> tuple[int, object]:
        nonlocal status
        calls.append(method)
        if method == "GET":
            return 200, {"status": status}
        status = "archived"
        raise OSError("response lost after commit")

    archive_campaign_fixture(
        request,
        campaign_id="campaign-id",
        admin_token="admin-token",
        attempts=1,
        retry_interval_seconds=0,
    )

    assert calls == ["GET", "PATCH", "GET"]


@pytest.mark.parametrize("patch_status", [409, 429, 503])
def test_archive_reconciles_final_retryable_response_after_commit(patch_status: int) -> None:
    calls: list[str] = []
    status = "draft"

    def request(
        method: str,
        _path: str,
        _payload: dict[str, object] | None = None,
        **_kwargs: object,
    ) -> tuple[int, object]:
        nonlocal status
        calls.append(method)
        if method == "GET":
            return 200, {"status": status}
        status = "archived"
        return patch_status, {"detail": "ambiguous retryable response"}

    archive_campaign_fixture(
        request,
        campaign_id="campaign-id",
        admin_token="admin-token",
        attempts=1,
        retry_interval_seconds=0,
    )

    assert calls == ["GET", "PATCH", "GET"]


def test_archive_does_not_confirm_definitive_patch_rejection() -> None:
    calls: list[str] = []

    def request(
        method: str,
        _path: str,
        _payload: dict[str, object] | None = None,
        **_kwargs: object,
    ) -> tuple[int, object]:
        calls.append(method)
        if method == "GET":
            return 200, {"status": "draft"}
        return 403, {"detail": "forbidden"}

    with pytest.raises(AssertionError, match="governed campaign cleanup failed"):
        archive_campaign_fixture(
            request,
            campaign_id="campaign-id",
            admin_token="admin-token",
            attempts=1,
            retry_interval_seconds=0,
        )

    assert calls == ["GET", "PATCH"]
