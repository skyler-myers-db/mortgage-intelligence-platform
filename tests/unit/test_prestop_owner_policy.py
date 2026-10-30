"""The pre-stop approved-owner probe runs the converger's own policy before the stop (M0-PR5)."""

from __future__ import annotations

from typing import Any

import pytest

from tests.fixtures.prestop_estate import (
    APP_CLIENT,
    APP_SCIM,
    FakeAccount,
    FakeWorkspace,
    clean_estate,
)
from tools.databricks import prestop_owner_policy as probe
from tools.databricks.prestop_refusals import OWNER_POLICY

APPROVED = ("mip-data-owners",)


def _run(
    estate: dict[str, Any],
    *,
    app_client_id: str = APP_CLIENT,
    app_groups: frozenset[str] = frozenset({"users"}),
    account: Any = None,
) -> tuple[list[str], list[str], FakeWorkspace]:
    workspace = FakeWorkspace(estate)

    def factory() -> Any:
        if isinstance(account, Exception):
            raise account
        return account or FakeAccount(estate)

    refusals, notes = probe.owner_policy_refusals(
        workspace,
        app_client_id=app_client_id,
        catalog="mip",
        approved_owner_principals=APPROVED,
        app_groups=app_groups,
        account_factory=factory,
    )
    assert all(refusal.code == OWNER_POLICY for refusal in refusals)
    return [refusal.message for refusal in refusals], notes, workspace


def test_approved_owners_on_every_governed_object_pass() -> None:
    refusals, notes, workspace = _run(clean_estate())

    assert refusals == []
    assert notes == ["approved-owner policy holds on 4 governed objects"]
    assert all(call.startswith("GET ") for call in workspace.estate["calls"]), "read-only"


def test_a_metastore_owner_outside_the_contract_refuses_by_name() -> None:
    estate = clean_estate()
    estate["metastore"]["owner"] = "platform-lead@example.invalid"
    estate["principals"]["platform-lead@example.invalid"] = {"kind": "user", "id": "lead-id"}

    refusals, _notes, _workspace = _run(estate)

    assert len(refusals) == 1
    assert "the metastore owner 'platform-lead@example.invalid'" in refusals[0]
    assert "outside the explicit approved-owner contract" in refusals[0]
    assert "add that exact owner to MIP_UC_APPROVED_OWNER_PRINCIPALS" in refusals[0]


def test_the_app_service_principal_cannot_own_the_treatment_table() -> None:
    estate = clean_estate()
    estate["catalogs"]["mip"]["tables"]["audit.campaign_treatment_snapshot"] = APP_CLIENT

    refusals, _notes, _workspace = _run(estate)

    assert len(refusals) == 1
    assert "the treatment table owner" in refusals[0]
    assert "Target App service principal cannot own governed UC objects" in refusals[0]


def _owned_by_group(members: list[str]) -> dict[str, Any]:
    estate = clean_estate()
    estate["catalogs"]["mip"]["owner"] = "mip-data-owners"
    estate["principals"]["mip-data-owners"]["members"] = members
    return estate


def test_a_group_owner_the_app_visibly_belongs_to_refuses() -> None:
    refusals, _notes, _workspace = _run(
        _owned_by_group([APP_SCIM]), app_groups=frozenset({"users", "mip-data-owners"})
    )

    assert len(refusals) == 1
    assert "the governed catalog owner 'mip-data-owners'" in refusals[0]
    assert "member of approved owner group" in refusals[0]


def test_a_group_owner_without_the_app_passes_the_read_only_membership_question() -> None:
    refusals, _notes, _workspace = _run(_owned_by_group([]))

    assert refusals == []


def test_an_owner_absent_from_account_scim_refuses() -> None:
    estate = clean_estate()
    estate["catalogs"]["mip"]["schemas"]["audit"] = "mip-data-owners-old"

    refusals, _notes, _workspace = _run(estate)

    assert len(refusals) == 1 and "the audit schema owner 'mip-data-owners-old'" in refusals[0]


def test_an_account_client_that_cannot_be_built_refuses_every_object() -> None:
    refusals, _notes, _workspace = _run(
        _owned_by_group([]), account=RuntimeError("account OAuth configuration missing")
    )

    assert refusals and all("fails the approved-owner policy" in item for item in refusals)


def test_an_unresolvable_app_identity_refuses_rather_than_pass() -> None:
    refusals, _notes, _workspace = _run(clean_estate(), app_client_id="app-client-unknown")

    assert len(refusals) == 1
    assert "cannot evaluate the approved-owner policy before step 0a" in refusals[0]


def test_no_app_defers_the_policy_to_after_creation() -> None:
    refusals, notes, workspace = _run(clean_estate(), app_client_id="")

    assert refusals == [] and workspace.estate["calls"] == []
    assert "runs against its SP after creation" in notes[0]


@pytest.mark.parametrize("group", ("mip-data-owners", "users"))
def test_the_read_only_membership_probe_answers_from_visible_groups(group: str) -> None:
    ask = probe.read_only_membership_probe(frozenset({"users", "mip-data-owners"}))

    assert ask(None, "account-sp", APP_CLIENT, "group-id", group) is True
    assert ask(None, "account-sp", APP_CLIENT, "group-id", "unrelated") is False
