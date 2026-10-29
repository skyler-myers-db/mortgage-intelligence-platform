"""A service-principal deployer holds the signed App deployment lease by its application id."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.services.ai_gateway_proof_attestation import derive_gateway_proof_verify_key
from tests.unit.test_app_deployment_lease import NOW, SIGNING_KEY, WRITER_ID, _workspace
from tools.databricks import app_deployment_lease as lease

SP_HOLDER = "00000000-0000-4000-8000-0000000000aa"
SOURCE = "a" * 40


@pytest.fixture(autouse=True)
def _keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.setenv(
        "MIP_AI_GATEWAY_PROOF_VERIFY_KEY",
        derive_gateway_proof_verify_key(SIGNING_KEY),
    )
    monkeypatch.setenv("DATABRICKS_AGENT_RUNTIME_CLIENT_ID", WRITER_ID)


class _ServicePrincipals:
    def __init__(self, matches: list[object]) -> None:
        self.matches = matches
        self.filters: list[str] = []

    def list(self, *, filter: str) -> list[object]:
        self.filters.append(filter)
        return list(self.matches)


def _sp_workspace(matches: list[object] | None = None) -> SimpleNamespace:
    workspace = _workspace(SP_HOLDER)
    assert isinstance(workspace, SimpleNamespace)
    workspace.service_principals = _ServicePrincipals(
        [SimpleNamespace(id="sp-scim-id", application_id=SP_HOLDER)] if matches is None else matches
    )
    return workspace


def _acl(workspace: SimpleNamespace) -> set[tuple[object, object, str]]:
    return {
        (entry.user_name, entry.service_principal_name, entry.all_permissions[0].permission_level)
        for entry in workspace.workspace.access_control_list
    }


def _acquire(workspace: SimpleNamespace) -> str:
    return lease.acquire(
        workspace,
        app_name="mip-app",
        source_git_sha=SOURCE,
        writer_application_id=WRITER_ID,
        now=NOW,
    )


def test_service_principal_holder_acquires_asserts_renews_and_releases() -> None:
    workspace = _sp_workspace()

    lease_id = _acquire(workspace)

    assert _acl(workspace) == {
        (None, SP_HOLDER, "CAN_MANAGE"),
        (None, WRITER_ID, "CAN_READ"),
    }
    record = lease.assert_held(
        workspace, app_name="mip-app", lease_id=lease_id, source_git_sha=SOURCE, now=NOW
    )
    assert record["holder"] == SP_HOLDER
    lease.renew(
        workspace, app_name="mip-app", lease_id=lease_id, source_git_sha=SOURCE, now=NOW
    )
    lease.release(workspace, app_name="mip-app", lease_id=lease_id)
    released = lease._download(workspace, app_name="mip-app")
    assert released is not None and released["state"] == "released"
    assert set(workspace.service_principals.filters) == {f'applicationId eq "{SP_HOLDER}"'}


def test_service_principal_holder_acl_drift_fails_closed() -> None:
    workspace = _sp_workspace()
    lease_id = _acquire(workspace)
    holder_entry, writer_entry = sorted(
        workspace.workspace.access_control_list,
        key=lambda entry: entry.service_principal_name != SP_HOLDER,
    )

    workspace.workspace.access_control_list = [writer_entry]
    with pytest.raises(RuntimeError, match="directory ACL did not converge"):
        lease.assert_held(
            workspace, app_name="mip-app", lease_id=lease_id, source_git_sha=SOURCE, now=NOW
        )

    # The same id recorded as a user is a different principal, never the holder.
    workspace.workspace.access_control_list = [
        SimpleNamespace(
            user_name=SP_HOLDER,
            service_principal_name=None,
            group_name=None,
            all_permissions=holder_entry.all_permissions,
        ),
        writer_entry,
    ]
    with pytest.raises(RuntimeError, match="unexpected accessor"):
        lease.assert_held(
            workspace, app_name="mip-app", lease_id=lease_id, source_git_sha=SOURCE, now=NOW
        )

    # An entry naming the holder alongside another principal is not the holder.
    workspace.workspace.access_control_list = [
        SimpleNamespace(
            user_name="someone@example.com",
            service_principal_name=SP_HOLDER,
            group_name=None,
            all_permissions=holder_entry.all_permissions,
        ),
        writer_entry,
    ]
    with pytest.raises(RuntimeError, match="unexpected accessor"):
        lease.assert_held(
            workspace, app_name="mip-app", lease_id=lease_id, source_git_sha=SOURCE, now=NOW
        )


@pytest.mark.parametrize(
    "matches",
    [
        [],
        [
            SimpleNamespace(id="sp-a", application_id=SP_HOLDER),
            SimpleNamespace(id="sp-b", application_id=SP_HOLDER),
        ],
        [SimpleNamespace(id="sp-near", application_id=SP_HOLDER.upper())],
    ],
    ids=["missing", "ambiguous", "inexact"],
)
def test_unresolved_service_principal_holder_fails_closed_before_any_write(
    matches: list[object],
) -> None:
    workspace = _sp_workspace(matches)

    with pytest.raises(RuntimeError, match="exactly one workspace service principal"):
        _acquire(workspace)

    assert workspace.workspace.data == {}
    assert workspace.workspace.set_permissions_calls == 0


def test_holder_that_becomes_ambiguous_after_acquire_fails_assertion() -> None:
    workspace = _sp_workspace()
    lease_id = _acquire(workspace)
    workspace.service_principals.matches = [
        SimpleNamespace(id="sp-a", application_id=SP_HOLDER),
        SimpleNamespace(id="sp-b", application_id=SP_HOLDER),
    ]

    with pytest.raises(RuntimeError, match="exactly one workspace service principal"):
        lease.assert_held(
            workspace, app_name="mip-app", lease_id=lease_id, source_git_sha=SOURCE, now=NOW
        )


def test_holder_lookup_error_fails_closed() -> None:
    workspace = _sp_workspace()

    def unavailable(**_kwargs: object) -> list[object]:
        raise ConnectionError("scim unavailable")

    workspace.service_principals.list = unavailable

    with pytest.raises(RuntimeError, match="could not be resolved"):
        _acquire(workspace)


def test_user_holder_keeps_a_user_name_entry_without_any_principal_lookup() -> None:
    workspace = _workspace()
    assert isinstance(workspace, SimpleNamespace)
    workspace.service_principals = SimpleNamespace(
        list=lambda **_kwargs: pytest.fail("a user holder needs no service-principal lookup")
    )

    lease_id = _acquire(workspace)
    lease.assert_held(
        workspace, app_name="mip-app", lease_id=lease_id, source_git_sha=SOURCE, now=NOW
    )

    assert _acl(workspace) == {
        ("deployer@example.com", None, "CAN_MANAGE"),
        (None, WRITER_ID, "CAN_READ"),
    }
