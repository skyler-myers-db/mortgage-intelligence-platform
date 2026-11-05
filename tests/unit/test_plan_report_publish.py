"""A plan's PlanReport halves and the release's deployment pin (M1-PR7).

``plan_report_publish summary`` puts ``PlanReport.public_summary()``, and
nothing else, in the step summary; ``publish`` writes ``PlanReport.full()``
create-only to the deployer's own plan-reports folder. ``active_deployment_pin``
prints, and a release requires, the sha256 of the App's active deployment id,
never the id.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from databricks.sdk.errors import NotFound

from tools.databricks import active_deployment_pin as pin
from tools.databricks import plan_report_publish as publisher
from tools.databricks.deploy_plan_report import LookupRecord, PlanReport, Refusal

DEPLOYER = "00000000-0000-4000-8000-000000000001"
FOREIGN = ("other_team_catalog_zyrplax", "owner.zyrplax@other-team.example")


def _report() -> PlanReport:
    return PlanReport(
        position="pre-lease",
        app_resource="apps.mip_app",
        app_bound=True,
        plan_sha256="a" * 64,
        plan_version=2,
        cli_version="1.10.0",
        lineage="lineage-zyrplax",
        counts={"create": 1, "update": 2},
        refusals=(
            Refusal(
                code="collision",
                resource="catalogs.mip",
                remedy="adopt it first",
                detail={"name": FOREIGN[0], "owner": FOREIGN[1]},
            ),
        ),
        lookups=(
            LookupRecord("catalogs.mip", "catalogs", (FOREIGN[0],), "metastore", "catalogs.get", "exists"),
        ),
    )


def test_the_step_summary_carries_the_public_summary_and_nothing_of_the_full_report(tmp_path: Path) -> None:
    report = _report()
    public = tmp_path / "public.json"
    public.write_text(json.dumps(report.public_summary()), encoding="utf-8")
    summary = tmp_path / "summary.md"

    assert publisher.main(["summary", "--public", str(public), "--out", str(summary)]) == 0
    text = summary.read_text(encoding="utf-8")
    assert "### Bundle plan gate (pre-lease): refused" in text
    assert "refusal codes: collision" in text and "catalogs.mip" in text
    for private in (*FOREIGN, "lineage-zyrplax", "adopt it first"):
        assert private not in text, "resolved names, owners, lineage and remedies stay in the full report"
    full = tmp_path / "full.json"
    full.write_text(json.dumps(report.full()), encoding="utf-8")
    assert publisher.main(["summary", "--public", str(full), "--out", str(summary)]) == 2, "the full report never reaches it"


class _Workspace:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.folders: list[str] = []
        self.workspace = SimpleNamespace(mkdirs=self._mkdirs, upload=self._upload, download=self._download)

    def _mkdirs(self, path: str) -> None:
        self.folders.append(path)

    def _upload(self, path: str, content: Any, *, format: Any, overwrite: bool) -> None:  # noqa: A002 - the SDK's name
        assert overwrite is False, "create-only"
        if path in self.files:
            raise FileExistsError(path)
        self.files[path] = content.read()

    def _download(self, path: str) -> io.BytesIO:
        return io.BytesIO(self.files[path])


def test_the_full_report_goes_create_only_to_the_deployers_plan_reports_folder(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    full = tmp_path / "full.json"
    full.write_text(json.dumps(_report().full()), encoding="utf-8")
    workspace = _Workspace()
    argv = ["publish", "--full", str(full), "--principal", DEPLOYER.upper(), "--client", "client-one", "--run-id", "123", "--run-attempt", "2"]

    assert publisher.main(argv, workspace_factory=lambda: workspace) == 0
    root = f"/Users/{DEPLOYER}/.bundle/mortgage-intelligence-platform/client-one"
    assert list(workspace.files) == [f"{root}/plan-reports/123-2.json"]
    assert workspace.folders == [f"{root}/plan-reports"]
    assert json.loads(workspace.files[f"{root}/plan-reports/123-2.json"]) == _report().full()
    out = capsys.readouterr().out
    assert DEPLOYER not in out and DEPLOYER.upper() not in out, "the path names the deployer; the line does not"
    assert publisher.main(argv, workspace_factory=lambda: workspace) == 2, "a run's report is written once"
    assert publisher.main([*argv[:-2], "--run-attempt", "2; rm -rf"], workspace_factory=lambda: workspace) == 2


def _app(deployment_id: str | None) -> Any:
    active = None if deployment_id is None else SimpleNamespace(deployment_id=deployment_id)
    return SimpleNamespace(apps=SimpleNamespace(get=lambda name: SimpleNamespace(name=name, active_deployment=active)))


def test_the_active_deployment_pin_prints_and_checks_only_a_digest(capsys: pytest.CaptureFixture[str]) -> None:
    deployment = "01f0deadbeefcafe0000000000000001"
    digest = hashlib.sha256(deployment.encode()).hexdigest()

    assert pin.main(["digest", "--app-name", "mip-app"], workspace_factory=lambda: _app(deployment)) == 0
    assert capsys.readouterr().out == f"[active-deployment] mip-app active deployment sha256: {digest} (read-only)\n"
    assert pin.main(["check", "--app-name", "mip-app", "--expect", digest], workspace_factory=lambda: _app(deployment)) == 0
    assert pin.main(["check", "--app-name", "mip-app", "--expect", "f" * 64], workspace_factory=lambda: _app(deployment)) == 2
    err = capsys.readouterr().err
    assert "no longer the one the dispatch expected" in err and deployment not in err
    assert pin.main(["check", "--app-name", "mip-app", "--expect", "none"], workspace_factory=lambda: _app(None)) == 0

    def absent() -> Any:
        def get(_name: str) -> Any:
            raise NotFound("no such app")

        return SimpleNamespace(apps=SimpleNamespace(get=get))

    assert pin.main(["check", "--app-name", "mip-app", "--expect", "none"], workspace_factory=absent) == 0
    assert pin.main(["check", "--app-name", "mip-app", "--expect", "id-not-digest"], workspace_factory=absent) == 2
