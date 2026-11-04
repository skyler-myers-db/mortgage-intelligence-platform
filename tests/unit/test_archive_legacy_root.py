"""The legacy root archive: plan-only by default, refuses bound paths, never deletes (M2-PR6).

The synthetic install of tests/fixtures/adopt_estate.py after its adoption:
the deployer's state binds the 16 legacy keys; the dashboards stay in the
legacy ``resources`` folder (pinned until spike S4) and the App runs from the
legacy ``app-src`` folder, so both are bound paths, and so is the legacy
``state`` folder that unadopt reads.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from databricks.sdk.service.workspace import ObjectType

from tests.fixtures import adopt_estate as es
from tools.clients import archive_legacy_root as archive
from tools.databricks.app_release_contract import api_path

ROOTS = archive.Roots(es.LEGACY_ROOT, es.DEPLOYER_ROOT)
APP_SOURCE = f"{es.LEGACY_ROOT}/app-src/0123456789ab-fedcba987654"


def _estate() -> es.Estate:
    estate = es.adopted_estate()
    tree = estate.tree
    tree.add_file(api_path(f"{es.LEGACY_ROOT}/files/app.yaml"), b"command: [python]\n")
    tree.add_file(api_path(f"{es.LEGACY_ROOT}/files/jobs/refresh"), b"# Databricks notebook source\n", ObjectType.NOTEBOOK)
    tree.add_file(api_path(f"{es.LEGACY_ROOT}/artifacts/.internal/build.whl"), b"wheel")
    tree.add_file(api_path(f"{APP_SOURCE}/app.yaml"), b"command: [python]\n")
    for key in ("mip_executive_dashboard", "mip_segment_dashboard"):
        tree.add_file(api_path(f"{es.DASHBOARD_PARENT}/{es.DEV}synthetic-{key}.lvdash.json"), b"{}")
    return estate


def _codes(plan: archive.ArchivePlan) -> list[tuple[str, str]]:
    return [(refusal.code, refusal.where) for refusal in plan.refusals]


def test_the_default_plan_refuses_every_move_that_covers_a_bound_path() -> None:
    estate = _estate()

    plan = archive.compute(estate, ROOTS)

    children = [move["source"].rsplit("/", 1)[-1] for move in plan.document["moves"]]
    assert children == ["app-src", "artifacts", "files", "resources", "state"]
    bound = {where for code, where in _codes(plan) if code == "bound_path"}
    assert bound == {"move.1", "move.4", "move.5"}, "app-src (App source), resources (dashboards), state"
    reasons = {refusal.reason for refusal in plan.refusals}
    assert "covers a path apps.mip_app uses" in reasons
    assert "covers a path dashboards.mip_executive_dashboard uses" in reasons
    assert "covers a path legacy.state uses" in reasons


def test_a_move_inside_a_bound_folder_refuses_too() -> None:
    assert archive.intersects(f"{es.LEGACY_ROOT}/resources", f"{es.DASHBOARD_PARENT}/d.lvdash.json")
    assert archive.intersects(f"{APP_SOURCE}/app.yaml", APP_SOURCE), "a move inside a bound folder"
    assert archive.intersects(api_path(f"{es.LEGACY_ROOT}/state"), f"{es.LEGACY_ROOT}/state"), "API or /Workspace form"
    assert not archive.intersects(f"{es.LEGACY_ROOT}/files", f"{es.LEGACY_ROOT}/files-old/x")


def test_jobs_still_running_from_the_legacy_files_protect_them() -> None:
    estate = _estate()
    estate.files_root = f"{es.LEGACY_ROOT}/files"

    plan = archive.compute(estate, ROOTS, ("files",))

    assert ("bound_path", "move.1") in _codes(plan)
    assert any("jobs." in refusal.reason for refusal in plan.refusals)


def test_only_the_unbound_children_plan_cleanly_and_the_plan_is_read_only() -> None:
    estate = _estate()
    before = dict(estate.tree.nodes)

    plan = archive.compute(estate, ROOTS, ("artifacts", "files"))

    assert plan.refusals == []
    assert [move["dest"] for move in plan.document["moves"]] == [
        f"{es.DEPLOYER_ROOT}/legacy-archive/dev/artifacts",
        f"{es.DEPLOYER_ROOT}/legacy-archive/dev/files",
    ]
    assert sum(len(move["objects"]) for move in plan.document["moves"]) == 3
    assert estate.tree.nodes == before
    assert {op for op, _ in estate.tree.calls} <= {"download", "list", "get_status"}


def test_apply_copies_create_only_byte_verified_and_never_deletes() -> None:
    estate = _estate()
    plan = archive.compute(estate, ROOTS, ("artifacts", "files"))

    counts = archive.apply(estate, ROOTS, plan.sha256, ("artifacts", "files"))
    again = archive.apply(estate, ROOTS, archive.compute(estate, ROOTS, ("artifacts", "files")).sha256, ("artifacts", "files"))

    assert counts == {"copied": 3, "kept": 0} and again == {"copied": 0, "kept": 3}
    archived = api_path(f"{es.DEPLOYER_ROOT}/legacy-archive/dev")
    assert estate.tree.nodes[f"{archived}/files/jobs/refresh"].kind is ObjectType.NOTEBOOK
    assert estate.tree.nodes[f"{archived}/files/app.yaml"].data == b"command: [python]\n"
    assert estate.tree.exists(api_path(f"{es.LEGACY_ROOT}/files/app.yaml")), "the original stays"
    assert estate.tree.ops("delete") == []


def test_apply_refuses_a_stale_ack_or_a_refused_plan_before_any_copy() -> None:
    estate = _estate()
    stale = archive.compute(estate, ROOTS, ("files",)).sha256
    estate.tree.add_file(api_path(f"{es.LEGACY_ROOT}/files/late.txt"), b"late")

    with pytest.raises(archive.ArchiveRefused, match="not the acknowledged digest"):
        archive.apply(estate, ROOTS, stale, ("files",))
    with pytest.raises(archive.ArchiveRefused, match="refuses"):
        archive.apply(estate, ROOTS, archive.compute(estate, ROOTS).sha256)
    assert estate.tree.ops("upload") == [] and estate.tree.ops("mkdirs") == []


def test_an_install_that_is_not_adopted_refuses() -> None:
    estate = es.Estate()
    estate.tree.add_file(api_path(f"{es.LEGACY_ROOT}/files/app.yaml"), b"x")

    plan = archive.compute(estate, ROOTS, ("files",))

    assert ("not_adopted", "apps.mip_app") in _codes(plan)


def test_main_is_plan_only_by_default_and_prints_no_path(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    estate = _estate()
    report = tmp_path / "report.json"

    rc = archive.main(["--only", "files", "--report-out", str(report)], roots=lambda: ROOTS, workspace_factory=lambda: estate)

    out = capsys.readouterr()
    assert rc == 0 and estate.tree.ops("upload") == []
    assert "legacy-owner" not in out.out + out.err and "/Workspace" not in out.out + out.err
    assert json.loads(report.read_text())["legacy_root"] == es.LEGACY_ROOT
    assert oct(report.stat().st_mode & 0o777) == "0o600"
    sha = archive.compute(estate, ROOTS, ("files",)).sha256
    assert f"--apply --ack-plan {sha}" in out.out
    assert archive.main(["--only", "files", "--apply", "--ack-plan", sha], roots=lambda: ROOTS, workspace_factory=lambda: estate) == 0
    assert estate.tree.ops("delete") == []


def test_every_copy_is_create_only_and_nothing_deletes() -> None:
    import ast

    tree = ast.parse(Path(archive.__file__).read_text(encoding="utf-8"))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)]
    uploads = [call for call in calls if call.func.attr == "upload"]  # type: ignore[union-attr]
    assert len(uploads) == 2
    for call in uploads:
        overwrite = [keyword.value for keyword in call.keywords if keyword.arg == "overwrite"]
        assert len(overwrite) == 1 and isinstance(overwrite[0], ast.Constant) and overwrite[0].value is False
    assert not [call for call in calls if call.func.attr in {"delete", "trash", "rename", "move"}]  # type: ignore[union-attr]
