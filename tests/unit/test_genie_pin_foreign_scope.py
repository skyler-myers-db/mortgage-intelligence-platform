"""Before an adoption, an App rollback scope that already exists refuses the step-0 Genie pin check.

The cutover runbook's 10B-0 dispatch expects the step-0 pin check
(``provision_genie_space --plan-pin``) to refuse an unpinned title match and
print its pin plan, whose sha256 10B-1 carries as ``genie_pin``. That holds
only while the App's rollback scope does not exist. The check reads the scope
as the CI deployer and verifies with the key step 4 generated, so a scope
that exists before the first adoption refuses: it is another deployer's (the
ownership proof), and a pin or rollback record in it is signed with a key
this install's environment does not hold. Either way no pin plan is printed,
so there is no digest to ack and no dispatch clears it.

Plans #1 and #2 stop at the client live checks, before the pin check, so the
runbook has the owner look, read-only, before step 7 starts the 14-day
sequence: ``python -m tools.databricks.app_rollback_secret_scope assert``.

Nothing stands in for the scope's ownership proof or a record's signature
check here: the fake secrets API below is what both read, and every write to
it is recorded, so each check is shown to change nothing.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from databricks.sdk.errors import NotFound
from databricks.sdk.service.workspace import AclPermission

from tests.fixtures.genie_pin_workspace import (
    APP,
    INSTANCE,
    SCOPE,
    TITLE,
    PinWorkspace,
    Space,
    signing_env,
)
from tools.databricks import app_rollback_record_contract as contract
from tools.databricks import app_rollback_secret_scope as rollback_scope
from tools.databricks import genie_space_pin as pin
from tools.databricks import provision_genie_space as pgs

ROOT = Path(__file__).resolve().parents[2]
LEGACY_OWNER = "legacy-owner@example.invalid"
CI_DEPLOYER = "00000000-0000-4000-8000-000000000001"
SPACE = "live-governed-space-0001"
OLD_KEY, STEP_4_KEY = signing_env(start=64), signing_env()
UNREADABLE = "error: the governed Genie space pin could not be read: RuntimeError"
FOREIGN_PIN = "error: the Genie pin record is not signed by this deploy's verification key"
ABSENT = "App rollback secret scope is absent"
OWNER_CHECK = (
    "DATABRICKS_CONFIG_PROFILE=<owner-profile> python -m tools.databricks.app_rollback_secret_scope "
    "assert --app-name <app-name> --scope <app-rollback-scope>"
)


class Secrets:
    """One workspace's secret scopes: the ACL, key inventory and values the proofs read; ``writes`` records each."""

    def __init__(self) -> None:
        self.acls: dict[str, dict[str, str]] = {}
        self.keys: dict[str, dict[str, str]] = {}
        self.writes: list[str] = []
        self.caller = ""

    def list_scopes(self) -> list[Any]:
        return [SimpleNamespace(name=name) for name in sorted(self.keys)]

    def create_scope(self, *, scope: str) -> None:
        self.writes.append(f"create_scope:{scope}")
        self.acls[scope] = {self.caller: "MANAGE"}
        self.keys[scope] = {}

    def list_acls(self, *, scope: str) -> list[Any]:
        return [SimpleNamespace(principal=who, permission=level) for who, level in sorted(self.acls[scope].items())]

    def put_acl(self, *, scope: str, principal: str, permission: AclPermission) -> None:
        self.writes.append(f"put_acl:{principal}")
        self.acls[scope][principal] = permission.value

    def put_secret(self, *, scope: str, key: str, string_value: str) -> None:
        self.writes.append(f"put_secret:{key}")
        self.keys[scope][key] = string_value

    def get_secret(self, scope: str, key: str) -> Any:
        if key not in self.keys.get(scope, {}):
            raise NotFound("no such secret")
        return SimpleNamespace(value=base64.b64encode(self.keys[scope][key].encode()).decode())

    def list_secrets(self, *, scope: str) -> list[Any]:
        return [SimpleNamespace(key=key) for key in sorted(self.keys[scope])]


def workspace_as(secrets: Secrets, caller: str) -> PinWorkspace:
    """The workspace one caller sees: the live App's Genie space under the governed title, and ``secrets``."""

    workspace = PinWorkspace(spaces=[Space(SPACE, managers=(LEGACY_OWNER,))], deployer=caller)
    workspace.secrets = secrets  # type: ignore[assignment]
    workspace.current_user = SimpleNamespace(me=lambda: SimpleNamespace(user_name=caller, application_id=""))
    return workspace


def scope_of(secrets: Secrets, owner: str, key: dict[str, str], *, records: tuple[str, ...]) -> None:
    """``owner`` claims the App's rollback scope and writes ``records`` there, signed with ``key``."""

    secrets.caller = owner
    workspace = workspace_as(secrets, owner)
    with pytest.MonkeyPatch.context() as then:
        for name, value in key.items():
            then.setenv(name, value)
        then.delenv("MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY", raising=False)
        rollback_scope.ensure_owned_app_rollback_scope(workspace, app_name=APP, scope=SCOPE)
        if "pin" in records:
            pin.write_pin(workspace, scope=SCOPE, app_name=APP, title=TITLE, space_id=SPACE, provenance="created")
        if "record" in records:
            record = {"version": contract.RECORD_VERSION, "app_name": APP, "genie_space_id": SPACE}
            contract._save_record(workspace, scope=SCOPE, record=record)


# (who owns the scope, the key its records are signed with, the records) -> the pin check's one error line.
BEFORE_AN_ADOPTION = {
    "another deployer's scope, empty": ((LEGACY_OWNER, OLD_KEY, ()), UNREADABLE),
    "another deployer's scope, its pin and record": ((LEGACY_OWNER, OLD_KEY, ("pin", "record")), UNREADABLE),
    "this deployer's scope, a record another key signed": ((CI_DEPLOYER, OLD_KEY, ("record",)), UNREADABLE),
    "this deployer's scope, a pin another key signed": ((CI_DEPLOYER, OLD_KEY, ("pin",)), FOREIGN_PIN),
}


def step_0_pin_check(secrets: Secrets, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> Any:
    """The deploy's step-0 pin check as the CI deployer, with the step-4 verify key only; (rc, out, err lines)."""

    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", STEP_4_KEY["MIP_AI_GATEWAY_PROOF_VERIFY_KEY"])
    for name in ("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", "MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MIP_DEFAULT_CATALOG", "mip")
    monkeypatch.setattr(pgs, "_build_client", lambda args: workspace_as(secrets, CI_DEPLOYER))
    capsys.readouterr()
    rc = pgs.main(
        ["--plan-pin", "--space-name", TITLE, "--catalog", "mip", "--app-name", APP, "--pin-scope", SCOPE,
         "--lakebase-instance", INSTANCE, "--no-smoke-test"]
    )
    captured = capsys.readouterr()
    errors = [line for line in captured.err.splitlines() if line and not line.startswith("::add-mask::")]
    return rc, captured.out, errors


@pytest.mark.parametrize("case", sorted(BEFORE_AN_ADOPTION))
def test_a_scope_that_exists_before_the_adoption_refuses_the_pin_check_and_prints_no_pin_plan(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], case: str
) -> None:
    (owner, key, records), line = BEFORE_AN_ADOPTION[case]
    secrets = Secrets()
    scope_of(secrets, owner, key, records=records)
    written = list(secrets.writes)

    rc, out, errors = step_0_pin_check(secrets, monkeypatch, capsys)

    assert rc == pgs.EXIT_PIN_REFUSED
    assert errors == [line], "one line, the rule or the error class, never an id"
    assert "pin plan sha256" not in out and "candidate space" not in out, "no plan to ack, so no genie_pin"
    assert secrets.writes == written, "the check is read-only"


@pytest.mark.parametrize("own_scope", (False, True), ids=("no-scope", "this-deployers-own-empty-scope"))
def test_without_a_foreign_scope_the_pin_check_prints_the_pin_plan_10b_0_expects(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], own_scope: bool
) -> None:
    """The control: the same title match, and the plan and its digest are printed."""

    secrets = Secrets()
    if own_scope:
        scope_of(secrets, CI_DEPLOYER, STEP_4_KEY, records=())
    written = list(secrets.writes)

    rc, out, errors = step_0_pin_check(secrets, monkeypatch, capsys)

    assert rc == pgs.EXIT_PIN_REFUSED, "10B-0's expected stop"
    assert len(re.findall(r"pin plan sha256: [0-9a-f]{64}$", out, re.MULTILINE)) == 1
    assert len(errors) == 1 and "no pin names it, and no ack names it" in errors[0]
    assert secrets.writes == written


def owner_check(secrets: Secrets, monkeypatch: pytest.MonkeyPatch) -> int:
    """Step 7's read-only check, as the legacy owner: the tool's own ``main``."""

    monkeypatch.setenv("MIP_LAKEBASE_INSTANCE", INSTANCE)
    monkeypatch.setattr(rollback_scope, "WorkspaceClient", lambda: workspace_as(secrets, LEGACY_OWNER))
    return rollback_scope.main(["assert", "--app-name", APP, "--scope", SCOPE])


def test_the_owners_check_stops_with_absent_exactly_when_no_scope_exists(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``absent`` is the one clean answer; every state the pin check refuses answers otherwise."""

    empty = Secrets()
    with pytest.raises(RuntimeError, match=rf"\A{ABSENT}\Z"):
        owner_check(empty, monkeypatch)
    assert empty.writes == []

    for case, ((owner, key, records), _line) in sorted(BEFORE_AN_ADOPTION.items()):
        secrets = Secrets()
        scope_of(secrets, owner, key, records=records)
        written = list(secrets.writes)
        try:
            answer = f"exit {owner_check(secrets, monkeypatch)}"
        except RuntimeError as exc:
            answer = str(exc)
        assert answer != ABSENT, case
        assert secrets.writes == written, f"{case}: assert mode is read-only"
    assert "deterministic ownership and key inventory verified" in capsys.readouterr().out, (
        "the owner's own scope passes the check, and still refuses the CI deployer's pin check"
    )


def test_the_runbook_names_the_check_its_clean_answer_and_the_refusals_log_lines() -> None:
    runbook = " ".join((ROOT / "docs" / "clients" / "first-client-cutover.md").read_text(encoding="utf-8").split())
    for anchor in (
        OWNER_CHECK,
        f"`RuntimeError: {ABSENT}`",
        f"`{UNREADABLE}`",
        f"`{FOREIGN_PIN}`",
        "**The rollback-scope check (read-only).**",
        "and prints no pin plan",
        "| `<app-rollback-scope>` |",
    ):
        assert anchor in runbook, anchor
    step_7 = runbook[runbook.index("## Step 7. Unity Catalog ownership handover") : runbook.index("## Step 8.")]
    assert OWNER_CHECK in step_7, "the check is a step 7 precondition, before the 14-day sequence starts"
    # The walk ends where the gaps noted at their steps begin (one is left since wave 7c).
    walk = runbook[runbook.index("**10B walked with") : runbook.index("One smaller gap is noted")]
    assert "an App rollback scope that already exists" in walk
