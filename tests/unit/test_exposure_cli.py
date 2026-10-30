"""The accepted-exposure CLI: the plan digest, its ack check and the owner-review inventory (M2-PR4).

``--emit-exposure-inventory`` only reads, prints candidates the policy refuses
until the owner fills in their review fields, and registers every identifying
value as a runner mask under GitHub Actions before it prints.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import databricks.sdk
import pytest

from tests.fixtures.prestop_estate import (
    APP_CLIENT,
    APP_NAME,
    APP_SCIM,
    DEPLOYER,
    GOVERNED_SPACE,
    RUNTIME_CLIENT,
    VERIFIER_CLIENT,
    FakeWorkspace,
    acl_entry,
    clean_estate,
)
from tests.unit import test_audit_agent_runtime_foreign_uc_access as uc_harness
from tests.unit.test_audit_agent_runtime_foreign_uc_access import _assignment
from tools.clients.json_fields import ExposureEntry
from tools.databricks.estate import cli
from tools.databricks.estate.accepted_exposure import ExposureRefusal, build_policy, load_policy

OTHER = "space-other-team"
TITLE = "Other Team Space"
GROUP = "app-readers-alpha"
ENTRY = {
    "kind": "workspace_permission",
    "securable": {"type": "GENIE_SPACE", "id": OTHER, "name": TITLE},
    "grantee": GROUP,
    "privileges": ["CAN_VIEW"],
    "source": "inherited:/directories/77",
    "binding": {"isolation_mode": "ISOLATED", "workspace_ids": ["4200000001"]},
    "identities": ["app"],
    "privilege_class": "read",
    "reason": "Another team shares its space with the App's reader group",
    "expires_at": "2099-12-31",
}


@pytest.fixture(autouse=True)
def _outside_github_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    for name in ("MIP_ACCEPTED_EXPOSURE_JSON", "MIP_ACK_ACCEPTED_EXPOSURE_PLAN", "MIP_GENIE_SPACE_NAME"):
        monkeypatch.delenv(name, raising=False)


def _digest(entries: list[dict[str, Any]]) -> str:
    parsed = [ExposureEntry.model_validate(item) for item in entries]
    return build_policy(parsed, now=datetime.now(UTC), static_control=lambda: []).digest


def test_plan_prints_the_digest_and_a_summary_without_values(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("MIP_ACCEPTED_EXPOSURE_JSON", json.dumps([ENTRY]))

    assert cli.main(["--plan"]) == 0

    out = capsys.readouterr().out
    assert f"sha256={_digest([ENTRY])} entries=1" in out
    assert "identities: app=1; classes: read=1; kinds: workspace_permission=1" in out
    assert "expires: 2099-12-31; already expired: 0" in out
    for value in (OTHER, TITLE, GROUP, "/directories/77", "4200000001", ENTRY["reason"]):
        assert value not in out


def test_check_accepts_only_the_recomputed_digest(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("MIP_ACCEPTED_EXPOSURE_JSON", json.dumps([ENTRY]))

    assert cli.main(["--check", "--ack-plan", _digest([ENTRY])]) == 0
    assert cli.main(["--check", "--ack-plan", "0" * 64]) == 2
    assert "REFUSED: the accepted-exposure plan is not acknowledged" in capsys.readouterr().err


def _estate() -> dict[str, Any]:
    estate = clean_estate()
    estate["principals"][GROUP] = {"kind": "group", "id": "group-app-readers", "members": [APP_SCIM]}
    estate["principals"]["shared-readers"] = {
        "kind": "group",
        "id": "group-shared",
        "members": ["runtime-scim", "verifier-scim"],
    }
    estate["principals"]["runtime-editors"] = {"kind": "group", "id": "group-editors", "members": ["runtime-scim"]}
    estate["genie"][OTHER] = [
        acl_entry("group_name", GROUP, "CAN_VIEW", "/directories/77"),
        acl_entry("group_name", "shared-readers", "CAN_RUN"),
        acl_entry("group_name", "runtime-editors", "CAN_EDIT"),
        acl_entry("group_name", "unrelated-team", "CAN_MANAGE"),
    ]
    estate["genie"][GOVERNED_SPACE].append(acl_entry("group_name", GROUP, "CAN_VIEW"))
    estate["genie_titles"] = {OTHER: TITLE, GOVERNED_SPACE: "Mortgage Lead Intelligence"}
    return estate


def _inventory(monkeypatch: pytest.MonkeyPatch, estate: dict[str, Any]) -> int:
    workspace = FakeWorkspace(estate)
    monkeypatch.setattr(databricks.sdk, "WorkspaceClient", lambda *_a, **_kw: workspace)
    return cli.main(
        [
            "--emit-exposure-inventory",
            "--app-name", APP_NAME,
            "--runtime-application-id", RUNTIME_CLIENT,
            "--verifier-application-id", VERIFIER_CLIENT,
            "--expected-inventory-principal", DEPLOYER,
            "--genie-space-name", "Mortgage Lead Intelligence",
        ]
    )  # fmt: skip


def test_the_inventory_lists_candidates_for_review_and_applies_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    estate = _estate()

    assert _inventory(monkeypatch, estate) == 0

    captured = capsys.readouterr()
    candidates = [json.loads(line) for line in captured.out.splitlines()]
    assert candidates == [{**ENTRY, "reason": cli._REVIEW["reason"], "expires_at": cli._REVIEW["expires_at"]}]
    assert "also reaches the verifier" in captured.err
    assert "elevated level for the agent-runtime" in captured.err
    assert "1 candidate entries for the owner's review; nothing was applied" in captured.err
    assert all(call.startswith("GET ") for call in estate["calls"]), "read-only"
    # A candidate is never usable as is: the owner must fill in the review fields.
    with pytest.raises(ExposureRefusal, match="holds a <placeholder>"):
        load_policy({"MIP_ACCEPTED_EXPOSURE_JSON": json.dumps(candidates)}, static_control=lambda: [])


def test_under_github_actions_every_identifying_value_is_masked_first(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    assert _inventory(monkeypatch, _estate()) == 0

    err = capsys.readouterr().err
    masks = {line.removeprefix("::add-mask::") for line in err.splitlines() if line.startswith("::add-mask::")}
    assert {OTHER, TITLE, GROUP, "/directories/77", "4200000001", "shared-readers", "runtime-editors"} <= masks
    assert not masks & {"users", "CAN_VIEW", "ISOLATED", "workspace_permission", "GENIE_SPACE", "app"}
    assert err.index(f"::add-mask::{GROUP}") < err.index("candidate entries"), "masked before printing"


def test_outside_github_actions_nothing_is_registered(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _inventory(monkeypatch, _estate()) == 0

    assert "::add-mask::" not in capsys.readouterr().err


def test_the_inventory_needs_its_identities(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(["--emit-exposure-inventory", "--app-name", APP_NAME])

    assert "requires --runtime-application-id" in capsys.readouterr().err


def test_uc_candidates_list_read_grants_and_note_elevated_ones() -> None:
    workspace = uc_harness._workspace(
        {
            ("catalog", "other"): [
                _assignment("USE_CATALOG", "SELECT", principal="account users"),
                _assignment("MODIFY", principal="writers-alpha"),
                _assignment("SELECT", principal="users", inherited_type="METASTORE", inherited_name="m"),
            ]
        }
    )
    runtime = uc_harness.APPLICATION_ID

    candidates, notes = cli.uc_candidates(workspace, catalog=uc_harness.CATALOG, runtime_application_id=runtime)

    assert candidates == [
        {
            "kind": "uc_grant",
            "securable": {"type": "CATALOG", "id": "other", "name": "other"},
            "grantee": "account users",
            "privileges": ["SELECT", "USE_CATALOG"],
            "source": "direct",
            "binding": {"isolation_mode": "OPEN", "workspace_ids": []},
            "identities": ["runtime"],
            "privilege_class": "read",
            **cli._REVIEW,
        }
    ]
    assert len(notes) == 1 and "'writers-alpha' holds MODIFY" in notes[0]


def test_the_runtime_matcher_accepts_a_reviewed_uc_candidate() -> None:
    workspace = uc_harness._workspace(
        {("catalog", "other"): [_assignment("USE_CATALOG", principal="account users")]}
    )
    [found], _notes = cli.uc_candidates(
        workspace, catalog=uc_harness.CATALOG, runtime_application_id=uc_harness.APPLICATION_ID
    )
    reviewed = {**found, "reason": "Owner reviewed", "expires_at": "2099-12-31"}
    env = {"MIP_ACCEPTED_EXPOSURE_JSON": json.dumps([reviewed])}
    matcher = load_policy(env, ack=_digest([reviewed]), static_control=lambda: []).matcher("runtime")

    proof = uc_harness._audit(
        uc_harness._workspace({("catalog", "other"): [_assignment("USE_CATALOG", principal="account users")]}),
        exposure=matcher,
    )
    assert "other" in proof.grant_audited_catalogs
    assert APP_CLIENT not in json.dumps(reviewed)
