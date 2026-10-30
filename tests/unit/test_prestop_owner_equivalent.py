"""Owner-equivalent grantees on the governed catalog, schemas and space (attack G12, M0-PR5)."""

from __future__ import annotations

from typing import Any

import pytest

from tests.fixtures.prestop_estate import (
    APP_CLIENT,
    DEPLOYER,
    GOVERNED_SPACE,
    FakeWorkspace,
    acl_entry,
    clean_estate,
)
from tools.databricks import prestop_owner_equivalent as probe
from tools.databricks.prestop_genie_grants import genie_acls
from tools.databricks.prestop_refusals import OWNER_EQUIVALENT

STEWARD = "steward@example.invalid"
FOREIGN_SP = "foreign-app-sp-EXAMPLE"
_NOBODY_BUT_THE_DEPLOYER = probe.approved_principals(
    approved_owners="", deployer=DEPLOYER, genie_managers=""
)


def _refusals(
    estate: dict[str, Any], *, approved_owners: str = "", genie_managers: str = ""
) -> tuple[list[str], list[str], FakeWorkspace]:
    workspace = FakeWorkspace(estate)
    refusals, notes = probe.owner_equivalent_refusals(
        workspace,
        catalog="mip",
        genie_space_id=GOVERNED_SPACE,
        acls=genie_acls(workspace),
        approved=probe.approved_principals(
            approved_owners=approved_owners, deployer=DEPLOYER, genie_managers=genie_managers
        ),
    )
    assert all(refusal.code == OWNER_EQUIVALENT for refusal in refusals)
    return [refusal.message for refusal in refusals], notes, workspace


def test_a_clean_estate_has_only_approved_owner_equivalent_grantees() -> None:
    refusals, notes, _workspace = _refusals(clean_estate())

    assert refusals == []
    assert "every owner-equivalent grantee is an approved principal" in notes


def test_a_human_with_manage_on_the_catalog_refuses_until_approved() -> None:
    estate = clean_estate()
    estate["grants"]["catalog/mip"].append(
        {"principal": STEWARD, "privileges": ["MANAGE", "ALL_PRIVILEGES", "SELECT"]}
    )

    refusals, _notes, _workspace = _refusals(estate)

    assert len(refusals) == 1
    assert f"principal {STEWARD!r} holds ALL_PRIVILEGES, MANAGE on catalog mip" in refusals[0]
    assert f"REVOKE ALL PRIVILEGES, MANAGE ON CATALOG `mip` FROM `{STEWARD}`" in refusals[0]
    assert "approve that exact principal in MIP_UC_APPROVED_OWNER_PRINCIPALS" in refusals[0]
    approved, _notes, _workspace = _refusals(estate, approved_owners=f"mip-data-owners,{STEWARD}")
    assert approved == []


def test_external_use_schema_on_a_schema_names_that_schema() -> None:
    estate = clean_estate()
    estate["grants"]["schema/mip.gold"].append(
        {"principal": "analytics-team", "privileges": ["EXTERNAL_USE_SCHEMA", "USE_SCHEMA"]}
    )

    refusals, _notes, _workspace = _refusals(estate)

    assert len(refusals) == 1
    assert "holds EXTERNAL_USE_SCHEMA on schema mip.gold" in refusals[0]
    assert "REVOKE EXTERNAL USE SCHEMA ON SCHEMA `mip`.`gold` FROM `analytics-team`" in refusals[0]


def test_a_foreign_sp_with_can_manage_on_the_governed_space_refuses() -> None:
    estate = clean_estate()
    estate["genie"][GOVERNED_SPACE].append(
        acl_entry("service_principal_name", FOREIGN_SP, "CAN_MANAGE")
    )

    refusals, _notes, _workspace = _refusals(estate)

    assert len(refusals) == 1
    assert (
        f"service principal {FOREIGN_SP!r} holds CAN_MANAGE on Genie space {GOVERNED_SPACE}"
        in refusals[0]
    )
    assert f"remove {FOREIGN_SP!r}'s CAN_MANAGE on Genie space {GOVERNED_SPACE}" in refusals[0]
    assert "approve that exact principal in MIP_GENIE_MANAGER_PRINCIPALS" in refusals[0]


def test_an_approved_group_passes_on_both_surfaces() -> None:
    estate = clean_estate()
    estate["grants"]["catalog/mip"].append(
        {"principal": "mip-data-owners", "privileges": ["MANAGE"]}
    )
    estate["genie"][GOVERNED_SPACE].append(acl_entry("group_name", "genie-curators", "CAN_EDIT"))

    refused, _notes, _workspace = _refusals(estate, approved_owners="mip-data-owners")
    assert len(refused) == 1 and "'genie-curators'" in refused[0]

    refusals, _notes, _workspace = _refusals(
        estate, approved_owners="mip-data-owners", genie_managers="Genie-Curators"
    )
    assert refusals == [], "approval is case-insensitive, like the owner policy"


def test_an_inherited_space_manager_names_its_folder_and_cannot_be_removed_on_the_space() -> None:
    estate = clean_estate()
    estate["genie"][GOVERNED_SPACE].append(
        acl_entry("user_name", STEWARD, "CAN_MANAGE", "/directories/5")
    )

    refusals, _notes, _workspace = _refusals(estate)

    assert len(refusals) == 1
    assert "(inherited from /Workspace/Shared)" in refusals[0]
    assert "cannot be removed on the space" in refusals[0]


def test_only_the_platform_admins_group_is_accepted_by_name_and_only_on_genie() -> None:
    estate = clean_estate()  # the governed space carries the inherited admins CAN_MANAGE
    estate["grants"]["catalog/mip"].append({"principal": "admins", "privileges": ["MANAGE"]})
    estate["genie"][GOVERNED_SPACE].append(acl_entry("user_name", "admins", "CAN_MANAGE"))

    refusals, _notes, _workspace = _refusals(estate)

    assert len(refusals) == 2
    assert "principal 'admins' holds MANAGE on catalog mip" in refusals[0]
    assert "user 'admins' holds CAN_MANAGE" in refusals[1], "a user named admins is not the group"


def test_read_only_grants_are_not_owner_equivalent() -> None:
    estate = clean_estate()
    estate["grants"]["catalog/mip"].append(
        {"principal": STEWARD, "privileges": ["USE_CATALOG", "SELECT", "BROWSE"]}
    )
    estate["genie"][GOVERNED_SPACE].append(acl_entry("user_name", STEWARD, "CAN_RUN"))

    assert _refusals(estate)[0] == []


@pytest.mark.parametrize(
    ("fault", "where"),
    (
        ("grants_errors", "catalog/mip"),
        ("grants_errors", "schema/mip.gold"),
        ("grants_errors", "schemas/mip"),
    ),
)
def test_a_grants_or_listing_read_error_refuses_and_is_never_read_as_no_grantees(
    fault: str, where: str
) -> None:
    estate = clean_estate()
    estate[fault].append(where)

    refusals, _notes, _workspace = _refusals(estate)

    assert len(refusals) == 1
    assert "cannot read the owner-equivalent grantees" in refusals[0]
    assert "refuses rather than read an unreadable grant list as empty" in refusals[0]


def test_a_permissions_read_error_on_the_governed_space_refuses() -> None:
    workspace = FakeWorkspace(clean_estate())
    refusals, _notes = probe.owner_equivalent_refusals(
        workspace,
        catalog="mip",
        genie_space_id=GOVERNED_SPACE,
        acls=[("space-other", {"access_control_list": []})],
        approved=_NOBODY_BUT_THE_DEPLOYER,
    )

    assert len(refusals) == 1
    assert "is not exactly once among the admin-visible spaces" in refusals[0].message
    unread, _notes = probe.owner_equivalent_refusals(
        workspace,
        catalog="mip",
        genie_space_id=GOVERNED_SPACE,
        acls=None,
        approved=_NOBODY_BUT_THE_DEPLOYER,
    )
    assert "the Genie space ACLs were not read" in unread[0].message


def test_an_absent_catalog_has_no_grantees_and_no_pinned_space_is_noted() -> None:
    estate = clean_estate()
    estate["catalogs"] = {}
    workspace = FakeWorkspace(estate)

    refusals, notes = probe.owner_equivalent_refusals(
        workspace, catalog="mip", genie_space_id="", acls=None, approved=_NOBODY_BUT_THE_DEPLOYER
    )

    assert refusals == []
    assert "no governed Genie space exists yet; its managers are not audited" in notes


def test_the_probe_only_reads() -> None:
    estate = clean_estate()
    estate["grants"]["catalog/mip"].append({"principal": APP_CLIENT, "privileges": ["MANAGE"]})

    refusals, _notes, workspace = _refusals(estate)

    assert len(refusals) == 1, "the App's own MANAGE is not approved"
    assert all(call.startswith("GET ") for call in workspace.estate["calls"])


def test_the_approvals_are_the_owners_and_deployer_plus_genie_managers_on_the_space() -> None:
    approved = probe.approved_principals(
        approved_owners=" mip-data-owners , owner@example.invalid",
        deployer=" Deployer@Example.invalid ",
        genie_managers="genie-curators,",
    )

    owners = {"mip-data-owners", "owner@example.invalid", "deployer@example.invalid"}
    assert approved.uc == frozenset(owners)
    assert approved.genie == frozenset({*owners, "genie-curators"})


def test_a_genie_manager_approval_never_approves_a_unity_catalog_grant() -> None:
    # MIP_GENIE_MANAGER_PRINCIPALS approves CAN_EDIT or CAN_MANAGE on the
    # governed space; MANAGE on the catalog is ownership-grade and needs the
    # owner approval, as its remedy says.
    estate = clean_estate()
    estate["grants"]["catalog/mip"].append(
        {"principal": "genie-curators", "privileges": ["ALL_PRIVILEGES", "MANAGE"]}
    )
    estate["genie"][GOVERNED_SPACE].append(acl_entry("group_name", "genie-curators", "CAN_EDIT"))

    refusals, _notes, _workspace = _refusals(estate, genie_managers="genie-curators")

    assert len(refusals) == 1
    assert "principal 'genie-curators' holds ALL_PRIVILEGES, MANAGE on catalog mip" in refusals[0]
    assert "approve that exact principal in MIP_UC_APPROVED_OWNER_PRINCIPALS" in refusals[0]
    approved, _notes, _workspace = _refusals(estate, approved_owners="genie-curators")
    assert approved == [], "an approved owner may hold both"
