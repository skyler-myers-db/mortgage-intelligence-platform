from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import ANY, MagicMock

import pytest
from databricks.sdk.errors import NotFound

from tools.databricks import (
    lakebase_oauth_role_bootstrap_orchestration as orchestration,
)
from tools.databricks.lakebase_oauth_role_bootstrap_admission import (
    BootstrapAdmissionOutcome,
    DatabaseCredentialLease,
    M2MSecretRejectionProof,
    OldTokenReuseProof,
)
from tools.databricks.lakebase_oauth_role_bootstrap_sessions import (
    BootstrapBackendIdentity,
)

APPLICATION_ID = "11111111-1111-4111-8111-111111111111"
SCIM_ID = "123456789012345"
CONTROL_APPLICATION_ID = "22222222-2222-4222-8222-222222222222"
EXPIRES_AT = datetime(2026, 7, 22, 13, 0, tzinfo=UTC)


class _RowsCursor:
    def __init__(self, responses: list[list[tuple[Any, ...]]]) -> None:
        self.responses = list(responses)
        self.current: list[tuple[Any, ...]] = []

    def execute(self, _statement: Any, _params: Any = None) -> None:
        self.current = self.responses.pop(0)

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.current


def _lease(*, token: str = "database-token-never-log") -> DatabaseCredentialLease:
    return DatabaseCredentialLease(
        host="reviewed.database.example",
        database_name="mip_app_state",
        database_user=APPLICATION_ID,
        application_name="mip-bootstrap-admission-22222222222242228222222222222222",
        request_id="22222222-2222-4222-8222-222222222222",
        expires_at=EXPIRES_AT,
        token=token,
    )


def _backend() -> BootstrapBackendIdentity:
    return BootstrapBackendIdentity(
        pid=202,
        role_oid=303,
        application_id=APPLICATION_ID,
        database_name="mip_app_state",
        application_name=_lease().application_name,
        backend_start=datetime(2026, 7, 22, 12, 50, tzinfo=UTC),
        backend_type="client backend",
        client_addr="10.0.0.8",
    )


class _Connection:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.autocommit = True
        self.cursor_value = SimpleNamespace(close=lambda: events.append("cursor:close"))

    def cursor(self) -> Any:
        self.events.append("connection:cursor")
        return self.cursor_value

    def commit(self) -> None:
        self.events.append("transaction:commit")

    def rollback(self) -> None:
        self.events.append("transaction:rollback")

    def close(self) -> None:
        self.events.append("connection:close")


class _SecretAPI:
    def __init__(
        self,
        events: list[str],
        *,
        account: bool = False,
        fail_at: str | None = None,
    ) -> None:
        self.events = events
        self.account = account
        self.fail_at = fail_at

    def create(self, principal_id: str, *, lifetime: str) -> Any:
        assert principal_id == SCIM_ID
        event = f"secret:create:{lifetime}"
        self.events.append(event)
        if self.fail_at == event:
            raise RuntimeError(f"injected {event}")
        return SimpleNamespace(id="secret-id", secret="m2m-secret-never-log")

    def delete(self, principal_id: str, secret_id: str) -> None:
        assert (principal_id, secret_id) == (SCIM_ID, "secret-id")
        self.events.append(f"secret:delete:{'account' if self.account else 'workspace'}")


def _clients(events: list[str], *, fail_at: str | None = None) -> tuple[Any, Any, Any]:
    workspace = SimpleNamespace(
        service_principal_secrets_proxy=_SecretAPI(events, fail_at=fail_at),
        database=SimpleNamespace(
            get_database_instance=lambda _name: SimpleNamespace(name="mip-app-state")
        ),
    )
    account = SimpleNamespace(
        service_principal_secrets=_SecretAPI(events, account=True),
    )
    bootstrap_client = SimpleNamespace(
        config=SimpleNamespace(auth_type="oauth-m2m"),
        current_user=SimpleNamespace(
            me=lambda: SimpleNamespace(
                application_id=APPLICATION_ID,
                user_name=APPLICATION_ID,
            )
        ),
        database=SimpleNamespace(
            generate_database_credential=MagicMock(),
            get_database_instance=lambda _name: SimpleNamespace(name="mip-app-state"),
        ),
    )
    return workspace, account, bootstrap_client


def _install_happy_path(
    monkeypatch: Any,
    events: list[str],
    *,
    fail_at: str | None = None,
    old_token_outcomes: list[BootstrapAdmissionOutcome] | None = None,
) -> tuple[_Connection, Any]:
    connection = _Connection(events)
    lease = _lease()
    backend = _backend()

    def step(name: str, value: Any = None) -> Any:
        events.append(name)
        if fail_at == name:
            raise RuntimeError(f"injected {name}")
        return value

    secret_empty_calls = 0

    def secret_empty(*_args: Any, **_kwargs: Any) -> int:
        nonlocal secret_empty_calls
        secret_empty_calls += 1
        return step(f"secret:empty:{secret_empty_calls}", 3)

    outcomes = list(old_token_outcomes or [BootstrapAdmissionOutcome.ADMITTED])

    def old_token(*_args: Any, positive_control: Any, **_kwargs: Any) -> Any:
        step("old-token:probe")
        positive_control()
        positive_control()
        outcome = outcomes.pop(0)
        if outcome is BootstrapAdmissionOutcome.ADMITTED:
            return OldTokenReuseProof(outcome=outcome, rejection_observations=3)
        return OldTokenReuseProof(
            outcome=outcome,
            rejection_observations=0,
            reuse_backend=BootstrapBackendIdentity(
                pid=404,
                role_oid=backend.role_oid,
                application_id=backend.application_id,
                database_name=backend.database_name,
                application_name="mip-bootstrap-reuse-33333333333343338333333333333333",
                backend_start=backend.backend_start,
                backend_type=backend.backend_type,
                client_addr=backend.client_addr,
            ),
        )

    monkeypatch.setattr(
        orchestration,
        "assert_bootstrap_lock_held",
        lambda *_args, **_kwargs: step("lock:held"),
    )
    monkeypatch.setattr(
        orchestration,
        "assert_zero_bootstrap_backends",
        lambda *_args, **_kwargs: step("sessions:zero"),
    )
    monkeypatch.setattr(orchestration, "prove_bootstrap_secret_planes_empty", secret_empty)
    monkeypatch.setattr(
        orchestration,
        "_converge_singleton_secret",
        lambda *_args, **_kwargs: step("secret:singleton"),
    )
    monkeypatch.setattr(
        orchestration,
        "_authenticate_exact_m2m",
        lambda *_args, **_kwargs: step("identity:authenticate"),
    )
    monkeypatch.setattr(
        orchestration,
        "capture_cached_m2m_access_token_expiry",
        lambda *_args, **_kwargs: step("oauth-expiry:capture", EXPIRES_AT),
    )
    monkeypatch.setattr(
        orchestration,
        "mint_database_credential_lease",
        lambda *_args, **_kwargs: step("credential:mint", lease),
    )
    monkeypatch.setattr(
        orchestration,
        "open_retained_bootstrap_backend",
        lambda *_args, **_kwargs: step("backend:open", connection),
    )
    monkeypatch.setattr(
        orchestration,
        "capture_bootstrap_backend_identity",
        lambda *_args, **_kwargs: step("backend:capture", backend),
    )
    monkeypatch.setattr(
        orchestration,
        "assert_exact_bootstrap_backend_inventory",
        lambda *_args, **_kwargs: step("backend:inventory"),
    )
    monkeypatch.setattr(
        orchestration,
        "_revoke_exact_secret_both_planes",
        lambda *_args, **_kwargs: step("secret:revoke", ()),
    )
    monkeypatch.setattr(
        orchestration,
        "retire_bootstrap_account_principal",
        lambda *_args, **_kwargs: step("principal:retire"),
    )
    monkeypatch.setattr(
        orchestration,
        "prove_exact_principal_absent_window",
        lambda *_args, **_kwargs: step("principal:absent"),
    )
    def prove_m2m(*_args: Any, positive_control: Any, **_kwargs: Any) -> Any:
        positive_control()
        return step(
            "cached-m2m:rejected",
            M2MSecretRejectionProof(rejection_observations=3),
        )

    monkeypatch.setattr(
        orchestration,
        "prove_destroyed_m2m_credential_rejected",
        prove_m2m,
    )
    monkeypatch.setattr(orchestration, "prove_old_database_token_reuse_rejected", old_token)
    monkeypatch.setattr(
        orchestration,
        "_admission_heartbeat",
        lambda *_args, **_kwargs: step("admission:heartbeat"),
    )
    monkeypatch.setattr(
        orchestration,
        "_capture_same_backend",
        lambda *_args, **_kwargs: step("backend:recapture"),
    )
    monkeypatch.setattr(
        orchestration,
        "_assert_principal_active_once",
        lambda *_args, **_kwargs: step("principal:active"),
    )
    monkeypatch.setattr(
        orchestration,
        "_assert_provider_auth_fresh",
        lambda *_args, **_kwargs: step("provider-auth:fresh"),
    )
    monkeypatch.setattr(
        orchestration,
        "_assert_transaction_survivability",
        lambda *_args, **_kwargs: step("transaction-timeout:safe"),
    )
    return connection, step


def _execute(
    monkeypatch: Any,
    events: list[str],
    *,
    fail_at: str | None = None,
    old_token_outcomes: list[BootstrapAdmissionOutcome] | None = None,
    now_factory: Any = lambda: datetime(2026, 7, 22, 13, 2, tzinfo=UTC),
    sleep: Any = lambda _seconds: None,
) -> Any:
    connection, step = _install_happy_path(
        monkeypatch,
        events,
        fail_at=fail_at,
        old_token_outcomes=old_token_outcomes,
    )
    workspace, account, bootstrap_client = _clients(events, fail_at=fail_at)

    def preinvoke(_cursor: Any) -> None:
        step("contract:preinvoke")

    def precommit(_cursor: Any) -> None:
        step("contract:precommit")

    def mark() -> None:
        step("provider:marked")

    def mark_commit() -> None:
        step("provider:commit-marked")

    def mark_commit_completed() -> None:
        step("provider:commit-completed")

    def invoke(_cursor: Any) -> None:
        assert connection.autocommit is False
        step("provider:invoke")

    return orchestration.execute_admitted_provider_bootstrap(
        workspace,
        account,
        deployer_cursor=object(),
        workspace_client_factory=lambda **_kwargs: bootstrap_client,
        connect=lambda **_kwargs: connection,
        workspace_host="https://reviewed.cloud.databricks.com",
        instance_name="mip-app-state",
        database_name="mip_app_state",
        bootstrap_application_id=APPLICATION_ID,
        bootstrap_scim_id=SCIM_ID,
        bootstrap_display_name="signed-bootstrap-display",
        bootstrap_reservation_name="mip-bootstrap-reservation",
        bootstrap_external_id="signed-ownership-marker",
        control_application_id=CONTROL_APPLICATION_ID,
        control_client_secret="control-secret-never-log",
        expected_executor="deployer",
        bootstrap_lock_cursor=object(),
        bootstrap_lock_key=object(),
        presecret_contract=lambda: step("contract:presecret"),
        positive_control=lambda: step("control:fresh-deployer"),
        preinvoke_contract=preinvoke,
        precommit_contract=precommit,
        mark_provider_invocation=mark,
        mark_provider_commit=mark_commit,
        mark_provider_commit_completed=mark_commit_completed,
        invoke_provider=invoke,
        validate_provider_result=lambda _cursor: step("provider:validate"),
        transaction_diagnostics=[],
        now_factory=now_factory,
        sleep=sleep,
    )


def test_provider_result_stays_uncommitted_until_retirement_proof(monkeypatch: Any) -> None:
    events: list[str] = []

    proof = _execute(monkeypatch, events)

    required_order = [
        "contract:presecret",
        "sessions:zero",
        "secret:empty:1",
        "secret:create:600s",
        "secret:singleton",
        "identity:authenticate",
        "oauth-expiry:capture",
        "credential:mint",
        "backend:open",
        "backend:capture",
        "backend:inventory",
        "secret:revoke",
        "secret:empty:2",
        "principal:active",
        "contract:preinvoke",
        "backend:recapture",
        "principal:active",
        "transaction-timeout:safe",
        "contract:preinvoke",
        "secret:empty:3",
        "provider-auth:fresh",
        "provider:marked",
        "provider:invoke",
        "provider:validate",
        "principal:retire",
        "principal:absent",
        "admission:heartbeat",
        "cached-m2m:rejected",
        "old-token:probe",
        "admission:heartbeat",
        "contract:precommit",
        "provider:validate",
        "provider:commit-marked",
        "transaction:commit",
        "provider:commit-completed",
    ]
    previous = -1
    for name in required_order:
        previous = events.index(name, previous + 1)
    assert proof.ready_for_commit is True
    assert "database-token-never-log" not in repr(proof)
    assert "m2m-secret-never-log" not in repr(events)
    assert events[-2:] == ["cursor:close", "connection:close"]


@pytest.mark.parametrize(
    "failure",
    [
        "contract:presecret",
        "sessions:zero",
        "secret:empty:1",
        "secret:create:600s",
        "secret:singleton",
        "identity:authenticate",
        "oauth-expiry:capture",
        "credential:mint",
        "backend:open",
        "backend:capture",
        "backend:inventory",
        "secret:revoke",
        "secret:empty:2",
        "principal:active",
        "contract:preinvoke",
        "backend:recapture",
        "provider-auth:fresh",
        "transaction-timeout:safe",
        "secret:empty:3",
    ],
)
def test_every_admission_failure_keeps_provider_invocation_at_zero(
    monkeypatch: Any,
    failure: str,
) -> None:
    events: list[str] = []

    with pytest.raises(RuntimeError, match="injected"):
        _execute(monkeypatch, events, fail_at=failure)

    assert "provider:marked" not in events
    assert "provider:invoke" not in events
    if "backend:open" in events and failure != "backend:open":
        assert "connection:close" in events


def test_retirement_always_waits_past_expiry_before_single_rejection_proof(
    monkeypatch: Any,
) -> None:
    events: list[str] = []
    now_values = iter(
        [
            datetime(2026, 7, 22, 12, 59, tzinfo=UTC),
            datetime(2026, 7, 22, 12, 59, 10, tzinfo=UTC),
            datetime(2026, 7, 22, 13, 1, tzinfo=UTC),
            datetime(2026, 7, 22, 13, 2, tzinfo=UTC),
        ]
    )

    proof = _execute(
        monkeypatch,
        events,
        old_token_outcomes=[BootstrapAdmissionOutcome.ADMITTED],
        now_factory=now_values.__next__,
        sleep=lambda seconds: events.append(f"expiry:sleep:{int(seconds)}"),
    )

    assert proof.ready_for_commit is True
    assert events.count("old-token:probe") == 1
    assert events.count("admission:heartbeat") == 3
    assert "expiry:sleep:15" in events
    assert events.index("expiry:sleep:15") < events.index("old-token:probe")
    assert events.index("provider:invoke") < events.index("expiry:sleep:15")
    assert events.index("old-token:probe") < events.index("transaction:commit")


def test_cached_m2m_rejection_is_proved_only_after_access_token_expiry(
    monkeypatch: Any,
) -> None:
    events: list[str] = []
    now_values = iter(
        [
            datetime(2026, 7, 22, 12, 59, tzinfo=UTC),
            datetime(2026, 7, 22, 12, 59, 10, tzinfo=UTC),
            datetime(2026, 7, 22, 13, 1, tzinfo=UTC),
            datetime(2026, 7, 22, 13, 2, tzinfo=UTC),
        ]
    )

    proof = _execute(
        monkeypatch,
        events,
        old_token_outcomes=[BootstrapAdmissionOutcome.ADMITTED],
        now_factory=now_values.__next__,
        sleep=lambda seconds: events.append(f"expiry:sleep:{int(seconds)}"),
    )

    assert proof.ready_for_commit is True
    assert events.count("cached-m2m:rejected") == 1
    assert events.count("old-token:probe") == 1
    assert events.index("expiry:sleep:15") < events.index("cached-m2m:rejected")
    assert events.index("provider:invoke") < events.index("cached-m2m:rejected")
    assert events.index("cached-m2m:rejected") < events.index("transaction:commit")


def test_post_invocation_validation_failure_rolls_back_exact_transaction(
    monkeypatch: Any,
) -> None:
    events: list[str] = []

    with pytest.raises(RuntimeError, match="injected provider:validate"):
        _execute(monkeypatch, events, fail_at="provider:validate")

    assert "provider:marked" in events
    assert "provider:invoke" in events
    assert "transaction:rollback" in events
    assert "transaction:commit" not in events
    assert events[-2:] == ["cursor:close", "connection:close"]


@pytest.mark.parametrize(
    "failure",
    [
        "principal:retire",
        "principal:absent",
        "admission:heartbeat",
        "cached-m2m:rejected",
        "old-token:probe",
        "contract:precommit",
        "provider:commit-marked",
    ],
)
def test_every_postinvoke_retirement_failure_rolls_back_before_commit(
    monkeypatch: Any,
    failure: str,
) -> None:
    events: list[str] = []

    with pytest.raises(RuntimeError, match="injected"):
        _execute(monkeypatch, events, fail_at=failure)

    assert events.index("provider:invoke") < events.index(failure)
    assert "transaction:rollback" in events
    assert "transaction:commit" not in events
    assert events[-2:] == ["cursor:close", "connection:close"]


@pytest.mark.parametrize(
    "now",
    [
        datetime(2026, 7, 22, 12, 58, tzinfo=UTC),
        datetime(2026, 7, 22, 12, 59, 31, tzinfo=UTC),
        datetime(2026, 7, 22, 13, 0, tzinfo=UTC),
    ],
)
def test_provider_invocation_requires_bounded_unexpired_auth(now: datetime) -> None:
    with pytest.raises(RuntimeError, match="too close to expiry"):
        orchestration._assert_provider_auth_fresh(
            lease=_lease(),
            m2m_access_token_expires_at=EXPIRES_AT,
            now=now,
        )


def test_provider_invocation_accepts_more_than_two_minutes_of_auth_headroom() -> None:
    orchestration._assert_provider_auth_fresh(
        lease=_lease(),
        m2m_access_token_expires_at=EXPIRES_AT,
        now=datetime(2026, 7, 22, 12, 57, 59, tzinfo=UTC),
    )


@pytest.mark.parametrize("timeouts", [(1.0, 0.0, None), (0.0, 1.0, None), (0.0, 0.0, 1.0)])
def test_provider_transaction_rejects_any_timeout_that_remains_enabled(
    timeouts: tuple[float, float, float | None],
) -> None:
    cursor = MagicMock()
    cursor.fetchone.side_effect = [(None,), timeouts]

    with pytest.raises(RuntimeError, match="timeouts remain enabled"):
        orchestration._assert_transaction_survivability(cursor)


@pytest.mark.parametrize(
    ("initial_transaction_timeout", "final_timeouts"),
    [(None, (0.0, 0.0, None)), ("5min", (0.0, 0.0, 0.0))],
)
def test_provider_transaction_disables_every_supported_timeout(
    initial_transaction_timeout: str | None,
    final_timeouts: tuple[float, float, float | None],
) -> None:
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        (initial_transaction_timeout,),
        final_timeouts,
    ]

    orchestration._assert_transaction_survivability(cursor)


@pytest.mark.parametrize(
    "final_timeouts",
    [
        None,
        ("not-a-number", 0.0, None),
        (float("nan"), 0.0, None),
        (0.0, float("inf"), None),
    ],
)
def test_provider_transaction_rejects_malformed_timeout_proof(
    final_timeouts: tuple[Any, ...] | None,
) -> None:
    cursor = MagicMock()
    cursor.fetchone.side_effect = [(None,), final_timeouts]

    with pytest.raises(RuntimeError, match="timeout"):
        orchestration._assert_transaction_survivability(cursor)


def test_zero_backend_preflight_pins_one_exact_role_and_no_sessions() -> None:
    cursor = _RowsCursor([[(303, APPLICATION_ID)], []])

    orchestration.assert_zero_bootstrap_backends(cursor, application_id=APPLICATION_ID)


@pytest.mark.parametrize(
    "responses",
    [
        [[], []],
        [[(303, APPLICATION_ID), (304, APPLICATION_ID)], []],
        [[(303, APPLICATION_ID)], [(202, 303, APPLICATION_ID, "unexpected")]],
    ],
)
def test_zero_backend_preflight_rejects_role_or_session_ambiguity(
    responses: list[list[tuple[Any, ...]]],
) -> None:
    with pytest.raises(RuntimeError):
        orchestration.assert_zero_bootstrap_backends(
            _RowsCursor(responses),
            application_id=APPLICATION_ID,
        )


def test_singleton_secret_convergence_retries_only_until_exact_id(monkeypatch: Any) -> None:
    inventory = MagicMock(side_effect=[RuntimeError("propagating"), "wrong-id", "secret-id"])
    monkeypatch.setattr(orchestration, "assert_singleton_bootstrap_secret_planes", inventory)
    monkeypatch.setattr(orchestration.time, "sleep", lambda _seconds: None)

    orchestration._converge_singleton_secret(
        object(),
        object(),
        service_principal_id=SCIM_ID,
        expected_secret_id="secret-id",
        attempts=3,
    )

    assert inventory.call_count == 3


@pytest.mark.parametrize(
    ("application_id", "user_name"),
    [("wrong", APPLICATION_ID), (APPLICATION_ID, "wrong"), ("", "")],
)
def test_exact_m2m_auth_rejects_any_identity_divergence(
    application_id: str,
    user_name: str,
) -> None:
    client = SimpleNamespace(
        config=SimpleNamespace(auth_type="oauth-m2m"),
        current_user=SimpleNamespace(
            me=lambda: SimpleNamespace(
                application_id=application_id,
                user_name=user_name,
            )
        )
    )

    with pytest.raises(RuntimeError, match="wrong identity"):
        orchestration._authenticate_exact_m2m(
            client,
            application_id=APPLICATION_ID,
            principal_label="test identity",
        )


def test_exact_m2m_auth_rejects_pat_control_before_identity_read() -> None:
    identity = MagicMock()
    client = SimpleNamespace(
        config=SimpleNamespace(auth_type="pat"),
        current_user=SimpleNamespace(me=identity),
    )

    with pytest.raises(RuntimeError, match="fresh OAuth-M2M"):
        orchestration._authenticate_exact_m2m(
            client,
            application_id=CONTROL_APPLICATION_ID,
            principal_label="Lakebase bootstrap positive control",
        )

    identity.assert_not_called()


def test_one_plane_delete_error_is_resolved_only_by_stable_two_plane_absence(
    monkeypatch: Any,
) -> None:
    present = {"value": True}

    class WorkspaceSecrets:
        def delete(self, _principal_id: str, _secret_id: str) -> None:
            present["value"] = False

        def list(self, _principal_id: str) -> Any:
            return iter([SimpleNamespace(id="secret-id")] if present["value"] else [])

    class AccountSecrets:
        def delete(self, _principal_id: str, _secret_id: str) -> None:
            raise OSError("ambiguous shared-plane delete")

        def list(self, _principal_id: str) -> Any:
            return iter([SimpleNamespace(id="secret-id")] if present["value"] else [])

    workspace = SimpleNamespace(service_principal_secrets_proxy=WorkspaceSecrets())
    account = SimpleNamespace(service_principal_secrets=AccountSecrets())
    monkeypatch.setattr(orchestration, "assert_bootstrap_lock_held", lambda *_a, **_k: None)
    monkeypatch.setattr(orchestration.time, "sleep", lambda _seconds: None)

    diagnostics = orchestration._revoke_exact_secret_both_planes(
        workspace,
        account,
        service_principal_id=SCIM_ID,
        secret_id="secret-id",
        bootstrap_lock_cursor=object(),
        bootstrap_lock_key=object(),
    )
    observations = orchestration.prove_bootstrap_secret_planes_empty(
        workspace,
        account,
        service_principal_id=SCIM_ID,
        attempts=3,
    )

    assert diagnostics == ("account: OSError",)
    assert observations == 3


def test_principal_heartbeat_requires_direct_absence_and_empty_relationships() -> None:
    def absent(_principal_id: str) -> Any:
        raise NotFound("absent")

    workspace = SimpleNamespace(
        service_principals=SimpleNamespace(get=absent),
        apps=SimpleNamespace(list=lambda: iter([])),
    )
    account = SimpleNamespace(
        service_principals=SimpleNamespace(get=absent),
        workspaces=SimpleNamespace(list=lambda: iter([])),
        workspace_assignment=SimpleNamespace(list=lambda _workspace_id: iter([])),
    )

    orchestration._assert_principal_absent_once(
        workspace,
        account,
        service_principal_id=SCIM_ID,
        application_id=APPLICATION_ID,
    )


def test_preinvoke_principal_fence_requires_exact_active_identity_on_both_planes(
    monkeypatch: Any,
) -> None:
    display_name = "signed-bootstrap-display"
    principal = SimpleNamespace(
        id=SCIM_ID,
        application_id=APPLICATION_ID,
        display_name=display_name,
        external_id="",
        active=True,
        groups=[],
        roles=[],
        entitlements=[],
    )
    boundary = MagicMock()
    app_boundary = MagicMock()
    monkeypatch.setattr(
        orchestration,
        "assert_account_workspace_assignment_boundary",
        boundary,
    )
    monkeypatch.setattr(orchestration, "assert_no_workspace_app_binding", app_boundary)

    orchestration._assert_principal_active_once(
        SimpleNamespace(service_principals=SimpleNamespace(get=lambda _id: principal)),
        SimpleNamespace(service_principals=SimpleNamespace(get=lambda _id: principal)),
        service_principal_id=SCIM_ID,
        application_id=APPLICATION_ID,
        display_name=display_name,
    )

    boundary.assert_called_once_with(
        ANY,
        ANY,
        principal_id=SCIM_ID,
        application_id=APPLICATION_ID,
        display_name=display_name,
        expected_workspace_active=True,
    )
    app_boundary.assert_called_once_with(ANY, application_ids={APPLICATION_ID})


def test_preinvoke_principal_fence_rejects_inactive_identity() -> None:
    principal = SimpleNamespace(
        id=SCIM_ID,
        application_id=APPLICATION_ID,
        display_name="signed-bootstrap-display",
        external_id="",
        active=False,
        groups=[],
        roles=[],
        entitlements=[],
    )
    client = SimpleNamespace(service_principals=SimpleNamespace(get=lambda _id: principal))

    with pytest.raises(RuntimeError, match="active contract drifted"):
        orchestration._assert_principal_active_once(
            client,
            client,
            service_principal_id=SCIM_ID,
            application_id=APPLICATION_ID,
            display_name="signed-bootstrap-display",
        )


def test_retained_backend_recapture_rejects_any_identity_change(monkeypatch: Any) -> None:
    changed = BootstrapBackendIdentity(
        pid=999,
        role_oid=_backend().role_oid,
        application_id=APPLICATION_ID,
        database_name="mip_app_state",
        application_name=_lease().application_name,
        backend_start=_backend().backend_start,
        backend_type="client backend",
        client_addr="10.0.0.8",
    )
    monkeypatch.setattr(
        orchestration,
        "capture_bootstrap_backend_identity",
        lambda *_args, **_kwargs: changed,
    )

    with pytest.raises(RuntimeError, match="identity changed"):
        orchestration._capture_same_backend(
            object(),
            lease=_lease(),
            expected=_backend(),
        )


def test_secret_bearing_values_are_repr_redacted() -> None:
    lease = _lease(token="never-print-database-token")

    assert "never-print-database-token" not in repr(lease)
    assert "token=" not in repr(lease)
    source = __import__("inspect").getsource(orchestration)
    assert "print(" not in source
    assert ".info(" not in source
    assert ".error(" not in source
