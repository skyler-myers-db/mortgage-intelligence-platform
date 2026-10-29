"""MIP_TEMP_CREDENTIAL_PLANE: workspace-plane probe secrets, default account plane unchanged."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from tests.fixtures.oauth_credential_session import InMemoryCredentialSession
from tools.databricks import oauth_credential_creation
from tools.databricks.campaign_treatment_identity_probe import (
    target_group_membership_probe,
    target_identity_groups_probe,
)
from tools.databricks.identity_boundary_probes import (
    probe_target_managed_query_group_administration_boundary,
)
from tools.databricks.oauth_credential_quarantine import (
    CredentialMutationContext,
    CredentialMutationQuarantineError,
)
from tools.databricks.temporary_credential_plane import (
    TEMP_CREDENTIAL_PLANE_ENV,
    temporary_credential_plane,
)

ACCOUNT_SCIM_ID = "account-sp-id"
WORKSPACE_SCIM_ID = "workspace-sp-id"
APPLICATION_ID = "app-client"
HOST = "https://workspace.example.invalid"


@dataclass
class _Secrets:
    """One plane's secret API: records every call and keeps an exact live set."""

    plane: str
    calls: list[tuple[object, ...]]
    allow_mutation: bool = True
    extra_on_create: bool = False
    live: set[str] = field(default_factory=set)

    def list(self, principal_id: str) -> object:
        self.calls.append((self.plane, "list", principal_id))
        return (SimpleNamespace(id=credential_id) for credential_id in sorted(self.live))

    def create(self, principal_id: str, *, lifetime: str) -> object:
        self.calls.append((self.plane, "create", principal_id, lifetime))
        assert self.allow_mutation, f"{self.plane} plane must not mint"
        self.live.add("temporary-secret-id")
        if self.extra_on_create:
            self.live.add("concurrent-secret-id")
            raise TimeoutError("create response lost after an ambiguous commit")
        return SimpleNamespace(id="temporary-secret-id", secret="temporary-value")

    def delete(self, principal_id: str, credential_id: str) -> None:
        self.calls.append((self.plane, "delete", principal_id, credential_id))
        assert self.allow_mutation, f"{self.plane} plane must not delete"
        self.live.remove(credential_id)


@pytest.fixture
def sessions(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    monkeypatch.setattr(oauth_credential_creation, "_STABILITY_INTERVAL_SECONDS", 0)
    recorded: list[dict[str, object]] = []

    def begin(
        fence: Any,
        *,
        label: str,
        principal_id: str,
        context: CredentialMutationContext,
    ) -> InMemoryCredentialSession:
        recorded.append({"principal_id": principal_id, "context": context})
        return InMemoryCredentialSession(
            fence, principal_id=principal_id, retirement_mode=context.retirement_mode
        )

    monkeypatch.setattr(oauth_credential_creation, "begin_credential_mutation_session", begin)
    return recorded


def _planes(
    *,
    account_mutates: bool,
    workspace_mutates: bool,
    extra_on_create: bool = False,
) -> tuple[list[tuple[object, ...]], SimpleNamespace, SimpleNamespace, _Secrets]:
    calls: list[tuple[object, ...]] = []
    account = SimpleNamespace(
        service_principal_secrets=_Secrets("account", calls, allow_mutation=account_mutates)
    )
    proxy = _Secrets(
        "workspace", calls, allow_mutation=workspace_mutates, extra_on_create=extra_on_create
    )
    deployer = SimpleNamespace(service_principal_secrets_proxy=proxy)
    return calls, account, deployer, proxy


def _target_workspace(**_kwargs: object) -> object:
    identity = {
        "id": WORKSPACE_SCIM_ID,
        "userName": APPLICATION_ID,
        "groups": [{"value": "users-id", "display": "users"}],
    }
    return SimpleNamespace(
        api_client=SimpleNamespace(do=lambda *_args, **_kwargs: identity),
        groups=SimpleNamespace(patch=lambda **_kwargs: pytest.fail("no groups to administer")),
    )


def _campaign_probe(account: object, deployer: object | None) -> dict[str, str]:
    return target_identity_groups_probe(
        account,  # type: ignore[arg-type]
        ACCOUNT_SCIM_ID,
        APPLICATION_ID,
        expected_workspace_scim_id=WORKSPACE_SCIM_ID,
        workspace_host=HOST,
        assert_single_writer=lambda: None,
        workspace_factory=_target_workspace,  # type: ignore[arg-type]
        deployer_workspace=deployer,
    )


def _membership_probe(account: object, deployer: object | None) -> dict[str, str]:
    # The owner-group policy entry point: its hop into target_identity_groups_probe
    # must carry the deployer client, or the workspace plane fails closed.
    is_member = target_group_membership_probe(
        account,  # type: ignore[arg-type]
        ACCOUNT_SCIM_ID,
        APPLICATION_ID,
        "users-id",
        "users",
        expected_workspace_scim_id=WORKSPACE_SCIM_ID,
        workspace_host=HOST,
        assert_single_writer=lambda: None,
        workspace_factory=_target_workspace,  # type: ignore[arg-type]
        deployer_workspace=deployer,
    )
    return {"users-id": "users"} if is_member else {}


def _administration_probe(account: object, deployer: object | None) -> dict[str, str]:
    return probe_target_managed_query_group_administration_boundary(
        account,
        account_sp_id=ACCOUNT_SCIM_ID,
        application_id=APPLICATION_ID,
        expected_workspace_scim_id=WORKSPACE_SCIM_ID,
        workspace_host=HOST,
        account_id="account-id",
        group_bindings=(),
        assert_single_writer=lambda: None,
        admin_workspace=deployer,
        workspace_factory=_target_workspace,
    )


PROBES = pytest.mark.parametrize(
    ("probe", "sink"),
    [
        (_campaign_probe, "temporary:target-identity-membership-probe"),
        (_membership_probe, "temporary:target-identity-membership-probe"),
        (_administration_probe, "temporary:managed-group-administration-probe"),
    ],
    ids=["campaign-identity", "owner-group-membership", "managed-group-administration"],
)


@pytest.mark.parametrize("value", [None, "", "   ", "account", " account "])
def test_unset_empty_or_account_selects_the_account_plane(
    monkeypatch: pytest.MonkeyPatch,
    value: str | None,
) -> None:
    if value is None:
        monkeypatch.delenv(TEMP_CREDENTIAL_PLANE_ENV, raising=False)
    else:
        monkeypatch.setenv(TEMP_CREDENTIAL_PLANE_ENV, value)

    assert temporary_credential_plane() == "account"


@pytest.mark.parametrize("value", ["Workspace", "WORKSPACE", "both", "proxy", "account,workspace"])
def test_unknown_plane_fails_closed(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(TEMP_CREDENTIAL_PLANE_ENV, value)

    with pytest.raises(RuntimeError, match="must be 'account' or 'workspace'"):
        temporary_credential_plane()


@PROBES
def test_workspace_plane_mints_lists_and_cleans_up_through_the_deployer_proxy(
    monkeypatch: pytest.MonkeyPatch,
    sessions: list[dict[str, object]],
    probe: Any,
    sink: str,
) -> None:
    monkeypatch.setenv(TEMP_CREDENTIAL_PLANE_ENV, "workspace")
    calls, account, deployer, proxy = _planes(account_mutates=False, workspace_mutates=True)

    assert probe(account, deployer) == {"users-id": "users"}

    workspace_calls = [call for call in calls if call[0] == "workspace"]
    assert ("workspace", "create", WORKSPACE_SCIM_ID, "300s") in workspace_calls
    assert [call for call in workspace_calls if call[1] == "delete"] == [
        ("workspace", "delete", WORKSPACE_SCIM_ID, "temporary-secret-id")
    ]
    assert {call[2] for call in workspace_calls} == {WORKSPACE_SCIM_ID}
    # Only diagnostic account-plane reads remain; the account plane never mutates.
    assert {call[1] for call in calls if call[0] == "account"} <= {"list"}
    assert proxy.live == set()
    assert len(sessions) == 1
    context = sessions[0]["context"]
    assert isinstance(context, CredentialMutationContext)
    assert sessions[0]["principal_id"] == WORKSPACE_SCIM_ID
    assert (
        context.authority_scope,
        context.provider_api,
        context.operation_mode,
        context.sink_descriptor,
        context.credential_lifetime_seconds,
        context.authority_identity,
    ) == (
        "workspace",
        "workspace.service_principal_secrets_proxy",
        "temporary_probe",
        sink,
        300,
        APPLICATION_ID,
    )


@PROBES
def test_default_plane_stays_on_the_account_plane_even_with_a_deployer_client(
    monkeypatch: pytest.MonkeyPatch,
    sessions: list[dict[str, object]],
    probe: Any,
    sink: str,
) -> None:
    monkeypatch.delenv(TEMP_CREDENTIAL_PLANE_ENV, raising=False)
    calls, account, deployer, _proxy = _planes(account_mutates=True, workspace_mutates=False)

    assert probe(account, deployer) == {"users-id": "users"}

    assert [call for call in calls if call[0] == "workspace"] == []
    assert ("account", "create", ACCOUNT_SCIM_ID, "300s") in calls
    assert ("account", "delete", ACCOUNT_SCIM_ID, "temporary-secret-id") in calls
    assert account.service_principal_secrets.live == set()
    context = sessions[0]["context"]
    assert isinstance(context, CredentialMutationContext)
    assert sessions[0]["principal_id"] == ACCOUNT_SCIM_ID
    assert (context.authority_scope, context.provider_api, context.sink_descriptor) == (
        "account",
        "account.service_principal_secrets",
        sink,
    )


@PROBES
def test_workspace_plane_without_a_deployer_client_fails_closed_before_any_call(
    monkeypatch: pytest.MonkeyPatch,
    sessions: list[dict[str, object]],
    probe: Any,
    sink: str,
) -> None:
    monkeypatch.setenv(TEMP_CREDENTIAL_PLANE_ENV, "workspace")
    calls, account, _deployer, _proxy = _planes(account_mutates=False, workspace_mutates=False)

    with pytest.raises(RuntimeError, match="requires the deployer workspace client"):
        probe(account, None)

    assert calls == [] and sessions == []


@PROBES
def test_workspace_plane_ambiguous_create_is_quarantined_not_retried(
    monkeypatch: pytest.MonkeyPatch,
    sessions: list[dict[str, object]],
    probe: Any,
    sink: str,
) -> None:
    monkeypatch.setenv(TEMP_CREDENTIAL_PLANE_ENV, "workspace")
    calls, account, deployer, proxy = _planes(
        account_mutates=False, workspace_mutates=True, extra_on_create=True
    )

    with pytest.raises(CredentialMutationQuarantineError) as raised:
        probe(account, deployer)

    assert raised.value.principal_id == WORKSPACE_SCIM_ID
    assert raised.value.candidate_ids == frozenset({"temporary-secret-id", "concurrent-secret-id"})
    assert [call for call in calls if call[1] == "create"] == [
        ("workspace", "create", WORKSPACE_SCIM_ID, "300s")
    ]
    assert [call for call in calls if call[1] == "delete"] == []
    assert proxy.live == {"temporary-secret-id", "concurrent-secret-id"}
