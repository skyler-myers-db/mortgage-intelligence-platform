"""The treatment converger judges the metastore by exactly MIP_UC_METASTORE_OWNER (M2-PR4).

``MetastoreOwnerPolicy`` is ``ApprovedOwnerPolicy`` over exactly the profile's
metastore owner: the install's approved owners and the deployer no longer own
the metastore implicitly, and the alias and group-membership exclusions stay.
Each refusal happens before any grant statement.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.unit import test_converge_campaign_treatment_access as harness
from tools.databricks.converge_campaign_treatment_access import (
    converge_campaign_treatment_access as converge,
)
from tools.databricks.uc_owner_policy import METASTORE_OWNER_ENV

GROUP = "Customer-Metastore-Admins"


def _run(workspace: Any, **kwargs: Any) -> bool:
    kwargs.setdefault("account_factory", lambda: harness._mirrored_account(workspace))
    kwargs.setdefault("assert_single_writer", lambda: None)
    return converge(
        warehouse_id="warehouse-1",
        catalog="mip",
        principal="app-client",
        mode="quiesce",
        workspace=workspace,
        **kwargs,
    )


def _refused(workspace: Any, execution: Any, match: str, **kwargs: Any) -> None:
    with pytest.raises(RuntimeError, match=match):
        _run(workspace, **kwargs)
    assert execution.statements == [], "refused before any grant statement"


def test_the_configured_owner_passes() -> None:
    workspace, execution, _ = harness._workspace(metastore_owner="deployer")

    assert _run(workspace, metastore_owner="deployer")
    assert execution.table_actions == ["SELECT"]


def test_the_owner_comes_from_the_environment_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    workspace, execution, _ = harness._workspace(metastore_owner="deployer")
    monkeypatch.setenv(METASTORE_OWNER_ENV, "deployer")

    assert _run(workspace) and execution.table_actions == ["SELECT"]
    monkeypatch.delenv(METASTORE_OWNER_ENV)
    unset, unset_execution, _ = harness._workspace(metastore_owner="deployer")
    _refused(unset, unset_execution, f"{METASTORE_OWNER_ENV} must name the metastore's exact owner")


def test_the_deployer_is_not_an_implicit_metastore_owner() -> None:
    workspace, execution, _ = harness._workspace(metastore_owner="deployer", owner_group=GROUP)

    _refused(workspace, execution, "metastore owner is not exactly", metastore_owner=GROUP)


def test_an_approved_owner_is_not_an_implicit_metastore_owner() -> None:
    workspace, execution, _ = harness._workspace(metastore_owner=GROUP, owner_group=GROUP)

    _refused(
        workspace,
        execution,
        "metastore owner is not exactly",
        metastore_owner="deployer",
        approved_owner_principals={GROUP},
    )


def test_a_case_variant_of_the_configured_owner_refuses() -> None:
    workspace, execution, _ = harness._workspace(metastore_owner="Deployer")

    _refused(workspace, execution, "metastore owner is not exactly", metastore_owner="deployer")


def test_a_metastore_owner_group_containing_the_app_sp_refuses() -> None:
    workspace, execution, _ = harness._workspace(
        metastore_owner=GROUP, owner_group=GROUP, target_is_owner_group_member=True
    )
    probed: list[str] = []

    _refused(
        workspace,
        execution,
        "member of approved owner group",
        metastore_owner=GROUP,
        group_membership_probe=lambda *args: probed.append(args[-1]) or True,
    )
    assert probed == [GROUP]


def test_a_metastore_owner_group_without_the_app_sp_passes_once_proven() -> None:
    workspace, _execution, _ = harness._workspace(
        owner=GROUP, metastore_owner=GROUP, owner_group=GROUP
    )
    probed: list[str] = []

    assert _run(
        workspace,
        metastore_owner=GROUP,
        approved_owner_principals={GROUP},
        group_membership_probe=lambda *args: probed.append(args[-1]) or False,
    )
    # The metastore and the governed objects share one membership proof.
    assert probed == [GROUP]


@pytest.mark.parametrize("alias", ["app-client", "app-sp", "sp-id"])
def test_a_configured_alias_of_the_app_sp_refuses(alias: str) -> None:
    workspace, execution, _ = harness._workspace(metastore_owner=alias)

    _refused(
        workspace,
        execution,
        f"Target App service principal cannot own governed UC objects: {METASTORE_OWNER_ENV} names it",
        metastore_owner=alias,
    )


def test_an_observed_alias_owner_refuses() -> None:
    workspace, execution, _ = harness._workspace(metastore_owner="app-sp")

    _refused(workspace, execution, "metastore owner is not exactly", metastore_owner="deployer")


@pytest.mark.parametrize("vocabulary", ["users", "account users", "Account Users", "admins", " admins "])
def test_users_account_users_and_admins_are_never_the_configured_owner(vocabulary: str) -> None:
    workspace, execution, _ = harness._workspace(metastore_owner=vocabulary.strip())

    _refused(workspace, execution, f"{METASTORE_OWNER_ENV} must", metastore_owner=vocabulary)


@pytest.mark.parametrize("vocabulary", ["users", "account users", "admins"])
def test_users_account_users_and_admins_never_own_the_metastore(vocabulary: str) -> None:
    workspace, execution, _ = harness._workspace(metastore_owner=vocabulary)

    _refused(
        workspace,
        execution,
        "users, account users or admins, which never satisfy",
        metastore_owner="deployer",
    )


def test_an_owner_the_api_does_not_return_refuses() -> None:
    # metastores.get is metastore-admin only (SDK 0.103.0); S0(b) asks whether a
    # non-admin read returns the owner. Either way an absent owner refuses.
    workspace, execution, _ = harness._workspace()
    workspace.metastores.get = lambda metastore_id: type("M", (), {"metastore_id": metastore_id})()

    _refused(workspace, execution, "noncanonical owner", metastore_owner="deployer")
