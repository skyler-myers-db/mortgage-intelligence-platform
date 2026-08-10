"""Databricks-created ``users`` clone resolves as a reviewed workspace group.

Enabling automatic identity management splits the legacy workspace ``users``
group: ``users`` becomes entitlement-free and a Databricks-generated clone
carries the legacy entitlements — and the workspace assignment — forward. The
runtime inherits the clone without any grant of ours. Acceptance requires
proof the clone's membership is identical to ``users``; every other shape
still fails closed, and ``users`` itself keeps its exact original contract.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tools.databricks.workspace_system_group_evidence import (
    workspace_users_clone_group_evidence,
    workspace_users_group_evidence,
)

_USERS_ID = "workspace-users-id"
_CLONE_ID = "workspace-users-clone-id"
_CLONE_NAME = "users-clone-2026-08-03-2052-UTC (created by Databricks)"
_MEMBERS = ("5882225431657870", "75100133948918", "78072554043911")


def _group(
    group_id: str,
    display_name: str,
    *,
    entitlements: tuple[str, ...] = (),
    resource_type: str = "WorkspaceGroup",
    external_id: object = None,
    roles: tuple[str, ...] = (),
) -> SimpleNamespace:
    return SimpleNamespace(
        id=group_id,
        display_name=display_name,
        meta=SimpleNamespace(resource_type=resource_type),
        entitlements=[SimpleNamespace(value=value) for value in entitlements],
        roles=[SimpleNamespace(value=value) for value in roles],
        external_id=external_id,
    )


def _workspace(
    listed: tuple[SimpleNamespace, ...],
    *,
    members: dict[str, tuple[str, ...]] | None = None,
) -> SimpleNamespace:
    membership = members if members is not None else {
        _USERS_ID: _MEMBERS,
        _CLONE_ID: _MEMBERS,
    }

    def _get(group_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            id=group_id,
            members=[
                SimpleNamespace(value=value)
                for value in membership.get(group_id, ())
            ],
        )

    return SimpleNamespace(
        groups=SimpleNamespace(
            list=lambda **_kwargs: iter(listed),
            get=_get,
        )
    )


_LEGACY_ENTITLEMENTS = ("workspace-access", "databricks-sql-access")


def test_users_alone_resolves_as_the_only_system_group() -> None:
    workspace = _workspace((_group(_USERS_ID, "users", entitlements=_LEGACY_ENTITLEMENTS),))
    assert workspace_users_group_evidence(workspace) == {_USERS_ID: "users"}
    assert workspace_users_clone_group_evidence(workspace) == {}


def test_clone_never_joins_the_system_group_set() -> None:
    workspace = _workspace(
        (
            _group(_USERS_ID, "users"),
            _group(_CLONE_ID, _CLONE_NAME, entitlements=_LEGACY_ENTITLEMENTS),
        )
    )
    assert workspace_users_group_evidence(workspace) == {_USERS_ID: "users"}


def test_exact_clone_resolves_as_a_reviewed_group() -> None:
    workspace = _workspace(
        (
            _group(_USERS_ID, "users"),
            _group(_CLONE_ID, _CLONE_NAME, entitlements=_LEGACY_ENTITLEMENTS),
        )
    )
    assert workspace_users_clone_group_evidence(workspace) == {_CLONE_ID: _CLONE_NAME}


def test_clone_with_different_membership_is_rejected() -> None:
    workspace = _workspace(
        (
            _group(_USERS_ID, "users"),
            _group(_CLONE_ID, _CLONE_NAME, entitlements=_LEGACY_ENTITLEMENTS),
        ),
        members={_USERS_ID: _MEMBERS, _CLONE_ID: _MEMBERS[:2]},
    )
    with pytest.raises(RuntimeError, match="not an exact clone"):
        workspace_users_clone_group_evidence(workspace)


def test_clone_of_an_empty_users_group_is_rejected() -> None:
    workspace = _workspace(
        (
            _group(_USERS_ID, "users"),
            _group(_CLONE_ID, _CLONE_NAME, entitlements=_LEGACY_ENTITLEMENTS),
        ),
        members={_USERS_ID: (), _CLONE_ID: ()},
    )
    with pytest.raises(RuntimeError, match="not an exact clone"):
        workspace_users_clone_group_evidence(workspace)


@pytest.mark.parametrize(
    "name",
    [
        "users-clone-2026-08-03-2052-UTC",
        "users-clone (created by Databricks)",
        "users-clone-2026-08-03-2052-UTC (created by Databricks) extra",
        "prod-users-clone-2026-08-03-2052-UTC (created by Databricks)",
        "users-clone-20260803-2052-UTC (created by Databricks)",
    ],
)
def test_lookalike_group_names_stay_ordinary_memberships(name: str) -> None:
    workspace = _workspace(
        (
            _group(_USERS_ID, "users"),
            _group(_CLONE_ID, name, entitlements=_LEGACY_ENTITLEMENTS),
        )
    )
    assert workspace_users_clone_group_evidence(workspace) == {}
    assert workspace_users_group_evidence(workspace) == {_USERS_ID: "users"}


@pytest.mark.parametrize(
    "entitlements",
    [
        (*_LEGACY_ENTITLEMENTS, "allow-cluster-create"),
        ("workspace-access",),
        ("allow-cluster-create",),
        ("workspace-access", "workspace-access"),
    ],
)
def test_clone_entitlements_off_the_legacy_pair_are_rejected(
    entitlements: tuple[str, ...],
) -> None:
    workspace = _workspace(
        (
            _group(_USERS_ID, "users"),
            _group(_CLONE_ID, _CLONE_NAME, entitlements=entitlements),
        )
    )
    with pytest.raises(RuntimeError, match="ambiguous"):
        workspace_users_clone_group_evidence(workspace)


def test_clone_with_roles_or_external_id_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="incomplete or ambiguous"):
        workspace_users_clone_group_evidence(
            _workspace(
                (
                    _group(_USERS_ID, "users"),
                    _group(_CLONE_ID, _CLONE_NAME, roles=("arn:aws:iam::role",)),
                )
            )
        )
    with pytest.raises(RuntimeError, match="incomplete or ambiguous"):
        workspace_users_clone_group_evidence(
            _workspace(
                (
                    _group(_USERS_ID, "users"),
                    _group(_CLONE_ID, _CLONE_NAME, external_id="federated"),
                )
            )
        )


def test_account_scoped_clone_resource_type_is_rejected() -> None:
    workspace = _workspace(
        (
            _group(_USERS_ID, "users"),
            _group(_CLONE_ID, _CLONE_NAME, resource_type="Group"),
        )
    )
    with pytest.raises(RuntimeError, match="incomplete or ambiguous"):
        workspace_users_clone_group_evidence(workspace)


def test_two_clones_are_ambiguous_and_rejected() -> None:
    workspace = _workspace(
        (
            _group(_USERS_ID, "users"),
            _group(_CLONE_ID, _CLONE_NAME, entitlements=_LEGACY_ENTITLEMENTS),
            _group(
                "second-clone-id",
                "users-clone-2026-08-04-0900-UTC (created by Databricks)",
                entitlements=_LEGACY_ENTITLEMENTS,
            ),
        )
    )
    with pytest.raises(RuntimeError, match="incomplete or ambiguous"):
        workspace_users_clone_group_evidence(workspace)


def test_missing_users_group_still_fails_even_with_a_clone() -> None:
    workspace = _workspace(
        (_group(_CLONE_ID, _CLONE_NAME, entitlements=_LEGACY_ENTITLEMENTS),)
    )
    with pytest.raises(RuntimeError, match="did not resolve exactly once"):
        workspace_users_clone_group_evidence(workspace)
