"""The read-only OAuth role check never mutates a role, the App or the control plane."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import psycopg
import pytest

from tests.unit import test_converge_lakebase_oauth_role as role_tests
from tools.databricks import converge_lakebase_oauth_role as converger
from tools.databricks import converge_lakebase_oauth_role_check as check_module
from tools.databricks.converge_lakebase_oauth_role_check import READ_ONLY_SESSION, check_role

APP = "service-principal-id"
CREATOR = "deployer@example.com"
SAFE = converger.SAFE_OAUTH_PROFILE
_IDENTIFIERS = (
    APP,
    "service-principal-scim-id",
    CREATOR,
    "mip-app-state",
    "mip_app_state",
    "mip-app",
)
# Every client method the check may call. generate_database_credential issues
# the deployer's own Lakebase login token for the read session; it creates no
# role, grant or secret and changes no estate state.
_READ_ONLY_CLIENT_CALLS = {
    "service_principals.list",
    "service_principals.get",
    "current_user.me",
    "database.get_database_instance",
    "database.get_database_instance_role",
    "database.generate_database_credential",
}
_MUTATING_MARKERS = ("EPHEMERAL_BOOTSTRAP", "CONTROL_PLANE_DELETE", "BEGIN_TRANSACTION")


def _client_calls(client: MagicMock) -> set[str]:
    names = set()
    for name, _args, _kwargs in client.mock_calls:
        parts = [part for part in name.replace("()", "").split(".") if part]
        if parts and not parts[-1].startswith("__"):
            names.add(".".join(parts))
    return names


def _check(
    state: role_tests._State,
    *,
    role_contract: str = "app",
    allow_absent_provider_schema: bool = True,
) -> list[str]:
    client = role_tests._client(state)
    client.service_principals.list.side_effect = lambda **_: iter(
        [MagicMock(id="service-principal-scim-id", application_id=APP)]
    )
    drift = check_role(
        client,
        account_client=None,
        instance_name="mip-app-state",
        database_name="mip_app_state",
        application_id=APP,
        role_contract=role_contract,
        connect=lambda **_kwargs: role_tests._Connection(state),
        allow_absent_provider_schema=allow_absent_provider_schema,
    )
    assert state.executed[0] == READ_ONLY_SESSION
    for statement in state.executed[1:]:
        assert statement.startswith(("SELECT", "WITH")), statement
    assert not any(marker in state.executed for marker in _MUTATING_MARKERS)
    assert state.deleted == 0
    observed = _client_calls(client)
    assert {"service_principals.list", "database.get_database_instance"} <= observed
    assert observed <= _READ_ONLY_CLIENT_CALLS, sorted(observed)
    for line in drift:
        assert not any(identifier in line for identifier in _IDENTIFIERS), line
    return drift


@pytest.mark.parametrize("app_state", ["RUNNING", "STOPPED"])
def test_exact_role_reports_no_drift_while_the_app_serves(app_state: str) -> None:
    state = role_tests._State(
        SAFE,
        dependencies=[(42, "pg_class", 0, "a", "mip_app", "campaigns", "", "r")],
        app_state=app_state,
    )

    assert _check(state) == []
    assert state.app_state == app_state


@pytest.mark.parametrize(
    ("state_kwargs", "expected"),
    [
        (
            {"profile": None},
            [
                "role: absent (convergence creates a LOGIN-only role; "
                "an App role needs a stopped App)"
            ],
        ),
        (
            {"profile": converger.LEGACY_API_OAUTH_PROFILE},
            [
                "role: legacy REPLICATION profile "
                "(convergence rotates the role; an App role needs a stopped App)"
            ],
        ),
        (
            {"profile": (True, False, False, False, False, True, True)},
            ["role: unreviewed attributes (convergence refuses)"],
        ),
        (
            {"profile": SAFE, "relationships": [(APP, CREATOR, True, False, False, "cloud_admin")]},
            [
                "role: the one-use creator membership remains "
                "(convergence rotates the role; an App role needs a stopped App)"
            ],
        ),
        (
            {"profile": SAFE, "relationships": [(APP, "other-role", True, False, False, "x")]},
            ["role: unreviewed role relationships (convergence refuses)"],
        ),
        (
            {"profile": SAFE, "dependencies": [(42, "pg_class", 0, "a", "public", "x", "", "r")]},
            ["role: unreviewed ACL or shared dependencies (convergence refuses)"],
        ),
        (
            {"profile": SAFE, "identity_type": "USER"},
            ["role: control-plane identity is not an exact service principal"],
        ),
    ],
)
def test_role_drift_is_reported_symbolically(
    state_kwargs: dict[str, Any], expected: list[str]
) -> None:
    profile = state_kwargs.pop("profile")
    state = role_tests._State(profile, app_state="RUNNING", **state_kwargs)

    assert _check(state) == expected


def test_settings_drift_is_reported() -> None:
    state = role_tests._State(SAFE, app_state="RUNNING")
    state.settings = (5, None, "********", None)

    assert _check(state) == [
        "role: settings differ from the reviewed contract (convergence refuses)"
    ]


def test_open_public_schema_boundary_is_drift() -> None:
    state = role_tests._State(SAFE, app_state="RUNNING")

    drift = _check(state, allow_absent_provider_schema=False)

    assert "public-schema boundary is not closed (convergence closes it)" in drift


@pytest.mark.parametrize(
    ("stale", "expected"),
    [
        ([object()], "1 stale one-use bootstrap identity found (convergence recovers it first)"),
        (
            RuntimeError("ambiguous marker"),
            "one-use bootstrap identity markers are ambiguous (convergence refuses)",
        ),
    ],
)
def test_bootstrap_residue_is_reported_and_never_recovered(
    monkeypatch: pytest.MonkeyPatch, stale: object, expected: str
) -> None:
    def inventory(*_args: object, **_kwargs: object) -> list[object]:
        if isinstance(stale, BaseException):
            raise stale
        return stale  # type: ignore[return-value]

    monkeypatch.setattr(check_module, "exact_bootstrap_principals", inventory)

    assert _check(role_tests._State(SAFE, app_state="RUNNING")) == [expected]


def test_cli_check_exits_one_on_drift_without_touching_the_app(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    state = role_tests._State(converger.LEGACY_API_OAUTH_PROFILE, app_state="RUNNING")
    client = role_tests._client(state)
    client.service_principals.list.side_effect = lambda **_: iter(
        [MagicMock(id="service-principal-scim-id", application_id=APP)]
    )
    monkeypatch.setattr("databricks.sdk.WorkspaceClient", lambda **_: client)
    monkeypatch.setattr("databricks.sdk.config.Config", lambda **_: None)
    monkeypatch.setattr(
        "tools.databricks.lakebase_oauth_role_account_principal.account_client_from_env",
        lambda: None,
    )
    monkeypatch.setattr(psycopg, "connect", lambda **_kwargs: role_tests._Connection(state))
    argv = [
        "--lakebase-instance", "mip-app-state",
        "--lakebase-database", "mip_app_state",
        "--application-id", APP,
        "--role-contract", "app",
        "--app-name", "mip-app",
        "--stop-app-for-mutation",
        "--repair-legacy-replication",
        "--check",
    ]  # fmt: skip

    assert converger.main(argv) == 1

    output = capsys.readouterr().out
    assert "[mip-lakebase-oauth-role] drift role-contract=app: role: legacy REPLICATION" in output
    assert state.profile == converger.LEGACY_API_OAUTH_PROFILE
    assert state.app_state == "RUNNING"
    assert not client.apps.stop.called
    assert _client_calls(client) <= _READ_ONLY_CLIENT_CALLS


def test_check_cannot_be_combined_with_bootstrap_recovery() -> None:
    with pytest.raises(SystemExit) as excinfo:
        converger.main(
            [
                "--lakebase-instance",
                "i",
                "--lakebase-database",
                "d",
                "--application-id",
                APP,
                "--role-contract",
                "app",
                "--recover-bootstrap-only",
                "--check",
            ]  # fmt: skip
        )
    assert excinfo.value.code == 2
