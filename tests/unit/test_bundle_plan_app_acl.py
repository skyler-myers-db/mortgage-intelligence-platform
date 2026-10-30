"""The pre-lease plan gate previews the App ACL the release convergence will set (M0-PR5)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures import bundle_plan_fixtures as fx
from tests.fixtures.prestop_estate import APP_NAME, FakeWorkspace, acl_entry, clean_estate
from tools.databricks import bundle_plan_gate as gate
from tools.databricks.bundle_plan_app_acl import app_acl_preview, with_app_acl_preview
from tools.databricks.deploy_plan_report import PlanReport

IDENTITIES = {
    "release-probe": "release-probe-client-EXAMPLE",
    "normal": "normal-client-EXAMPLE",
    "operator2": "operator2-client-EXAMPLE",
    "admin": "admin-client-EXAMPLE",
}
BUSINESS_USER = "analyst@example.invalid"


def _estate(*acl: dict[str, Any]) -> dict[str, Any]:
    estate = clean_estate()
    estate["app"]["acl"] = list(acl)
    return estate


def _runtime_acl() -> list[dict[str, Any]]:
    return [
        acl_entry("service_principal_name", IDENTITIES[role], "CAN_USE")
        for role in ("normal", "operator2", "admin")
    ]


def _report(estate: dict[str, Any], **overrides: str) -> PlanReport:
    identities = {
        "release_probe_application_id": IDENTITIES["release-probe"],
        "normal_application_id": IDENTITIES["normal"],
        "operator2_application_id": IDENTITIES["operator2"],
        "admin_application_id": IDENTITIES["admin"],
        **overrides,
    }
    return with_app_acl_preview(
        PlanReport(position="pre-lease", app_resource="apps.mip_app", app_bound=True),
        workspace_factory=lambda: FakeWorkspace(estate),
        app_name=APP_NAME,
        **identities,
    )


def test_a_business_user_grant_is_previewed_as_removed() -> None:
    estate = _estate(
        acl_entry("group_name", "admins", "CAN_MANAGE", True),
        acl_entry("user_name", "owner@example.invalid", "CAN_MANAGE"),
        acl_entry("user_name", BUSINESS_USER, "CAN_USE"),
    )

    report = _report(estate)

    assert report.refusals == ()
    public = report.public_summary()["app_acl"]
    assert public == {"code": "changes", "keep": 1, "add": 3, "remove": 1, "change": 0}
    assert BUSINESS_USER not in json.dumps(report.public_summary()), "principals stay private"
    full = report.full()["app_acl"]
    assert full["remove"] == [{"kind": "user_name", "principal": BUSINESS_USER, "level": "CAN_USE"}]
    assert full["keep"][0]["principal"] == "owner@example.invalid"
    assert {grant["principal"] for grant in full["add"]} == {
        IDENTITIES["normal"],
        IDENTITIES["operator2"],
        IDENTITIES["admin"],
    }


def test_a_converged_acl_previews_unchanged_and_a_manager_demotion_as_a_change() -> None:
    assert _report(_estate(*_runtime_acl())).public_summary()["app_acl"]["code"] == "unchanged"

    probe_grant = acl_entry("service_principal_name", IDENTITIES["release-probe"], "CAN_USE")
    report = _report(_estate(*_runtime_acl(), probe_grant))
    assert report.public_summary()["app_acl"] == {
        "code": "changes",
        "keep": 3,
        "add": 0,
        "remove": 1,
        "change": 0,
    }


def test_an_absent_app_has_nothing_to_preview() -> None:
    estate = clean_estate()
    estate["app"] = None

    assert _report(estate).public_summary()["app_acl"] == {
        "code": "app_absent",
        "keep": 0,
        "add": 0,
        "remove": 0,
        "change": 0,
    }


@pytest.mark.parametrize(
    "entry",
    (
        acl_entry("user_name", BUSINESS_USER, "CAN_USE", True),
        acl_entry("user_name", BUSINESS_USER, "CAN_VIEW"),
        acl_entry("service_principal_name", IDENTITIES["admin"], "CAN_MANAGE"),
    ),
    ids=("inherited-non-manager", "unexpected-level", "release-identity-manages"),
)
def test_an_acl_the_converger_would_refuse_refuses_before_the_lease(entry: dict[str, Any]) -> None:
    report = _report(_estate(entry))

    assert report.codes == ("app_acl",)
    assert report.refusals[0].resource == "apps.mip_app.permissions"
    assert report.public_summary()["app_acl"]["code"] == "refused"


def test_an_unreadable_acl_or_bad_identities_refuse_never_preview_unchanged() -> None:
    workspace = FakeWorkspace(_estate())

    def broken(_name: str) -> Any:
        raise RuntimeError("PERMISSION_DENIED reading the App ACL")

    workspace.apps.get_permissions = broken
    report = with_app_acl_preview(
        PlanReport(position="pre-lease", app_resource="apps.mip_app", app_bound=True),
        workspace_factory=lambda: workspace,
        app_name=APP_NAME,
        release_probe_application_id=IDENTITIES["release-probe"],
        normal_application_id=IDENTITIES["normal"],
        operator2_application_id=IDENTITIES["operator2"],
        admin_application_id=IDENTITIES["admin"],
    )
    assert report.codes == ("app_acl",)
    assert "PERMISSION_DENIED" in report.full()["refusals"][0]["detail"]["reason"]

    duplicated = _report(_estate(), admin_application_id=IDENTITIES["normal"])
    assert duplicated.codes == ("app_acl",)


def test_the_preview_only_reads() -> None:
    estate = _estate(acl_entry("user_name", BUSINESS_USER, "CAN_USE"))
    workspace = FakeWorkspace(estate)

    app_acl_preview(workspace, app_name=APP_NAME, identities=IDENTITIES)

    assert all(call.startswith("GET ") for call in estate["calls"])


def _plan(tmp_path: Path) -> Path:
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(fx.adopted()), encoding="utf-8")
    return path


def test_the_gate_cli_writes_the_preview_to_the_private_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    estate = _estate(acl_entry("user_name", BUSINESS_USER, "CAN_USE"))
    report_path = tmp_path / "report.json"
    args = [
        "check", "--plan", str(_plan(tmp_path)), "--position", "pre-lease",
        "--report-out", str(report_path), "--app-acl-app-name", APP_NAME,
        "--release-probe-application-id", IDENTITIES["release-probe"],
        "--normal-application-id", IDENTITIES["normal"],
        "--operator2-application-id", IDENTITIES["operator2"],
        "--admin-application-id", IDENTITIES["admin"],
    ]  # fmt: skip

    rc = gate.main(args, workspace_factory=lambda: _merged(estate))

    out = capsys.readouterr().out
    assert rc == 0, out
    assert "app ACL preview: changes (0 keep, 3 add, 1 remove, 0 change" in out
    assert BUSINESS_USER not in out
    full = json.loads(report_path.read_text(encoding="utf-8"))
    assert full["app_acl"]["remove"][0]["principal"] == BUSINESS_USER


class _Merged(fx.RecordingWorkspace):
    """The plan fixtures' recording workspace, with the fake estate's App reads."""

    def __init__(self, estate: dict[str, Any]) -> None:
        super().__init__()
        self._fake = FakeWorkspace(estate)

    def __getattr__(self, service: str) -> Any:
        if service == "apps":
            return self._fake.apps
        return super().__getattr__(service)


def _merged(estate: dict[str, Any]) -> Any:
    return _Merged(estate)


@pytest.mark.parametrize(
    "extra",
    (
        ["--app-acl-app-name", APP_NAME],
        [
            "--position", "pre-apply", "--app-acl-app-name", APP_NAME,
            "--release-probe-application-id", "a", "--normal-application-id", "b",
            "--operator2-application-id", "c", "--admin-application-id", "d",
        ],
    ),
    ids=("incomplete", "pre-apply"),
)  # fmt: skip
def test_the_gate_cli_takes_the_preview_arguments_together_and_pre_lease_only(
    tmp_path: Path, extra: list[str]
) -> None:
    position = [] if "--position" in extra else ["--position", "pre-lease"]
    with pytest.raises(SystemExit) as raised:
        gate.main(["check", "--plan", str(_plan(tmp_path)), *position, *extra])
    assert raised.value.code == 2
