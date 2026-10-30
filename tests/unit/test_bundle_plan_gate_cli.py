"""The bundle plan gate's command line: exit 2 with a remedy, names only in the full report.

``python -m tools.databricks.bundle_plan_gate check`` is what deploy.sh runs.
Each refusal prints one public line (code, symbolic names, remedy) and exits
2; resolved names go only to the 0600 ``--report-out`` file. Plans are the
sanitized CLI v1.10.0 fixtures (tests/fixtures/bundle_plan_fixtures.py), and
the recording workspace proves the lookups stay read-only.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures import bundle_plan_fixtures as fx
from tools.databricks import bundle_plan_gate as gate
from tools.databricks.bundle_plan_shape import parse_plan

INSTANCE = "resources.database_instances.mip_app_state"


def _main(
    tmp_path: Path,
    doc: dict[str, Any] | str,
    workspace: fx.RecordingWorkspace,
    capsys: pytest.CaptureFixture[str],
    *extra: str,
) -> tuple[int, str, str, Path]:
    plan = tmp_path / "plan.json"
    plan.write_text(doc if isinstance(doc, str) else fx.dumps(doc), encoding="utf-8")
    report = tmp_path / "report.json"
    rc = gate.main(
        [
            "check",
            "--plan",
            str(plan),
            "--position",
            "pre-lease",
            "--report-out",
            str(report),
            *extra,
        ],
        workspace_factory=lambda: workspace,
    )
    out, err = capsys.readouterr()
    return rc, out, err, report


def test_the_cli_exits_2_with_one_remedy_and_keeps_names_out_of_its_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = fx.RecordingWorkspace(fx.existing_install())

    rc, out, err, report = _main(tmp_path, fx.first_install(), workspace, capsys)

    assert rc == 2
    refused = [line for line in err.splitlines() if "REFUSED" in line]
    assert len(refused) == 1 and refused[0].startswith(
        "[mip-plan-gate] REFUSED not_adopted apps.mip_app ("
    )
    assert gate.REMEDIES["not_adopted"] in refused[0]
    assert "synthetic-" not in out + err and "example.invalid" not in out + err
    summary = json.loads(out.split("public summary: ", 1)[1].splitlines()[0])
    assert summary["verdict"] == "refused" and [r["code"] for r in summary["refusals"]] == [
        "not_adopted"
    ]
    assert stat.S_IMODE(report.stat().st_mode) == 0o600
    full = json.loads(report.read_text(encoding="utf-8"))
    assert "synthetic-mip_app" in json.dumps(full["refusals"][0]["detail"])
    assert workspace.mutating_calls() == []


@pytest.mark.parametrize(
    ("entries", "code"),
    (
        ({"resources.jobs.retired_job": fx.entry("delete")}, "delete"),
        ({INSTANCE: fx.instance_update()}, "instance_update_unacked"),
        ({"resources.secret_scopes.extra": fx.entry("skip")}, "unknown_kind"),
    ),
)
def test_each_refusal_exits_2_with_its_remedy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], entries: dict[str, Any], code: str
) -> None:
    rc, _out, err, _report = _main(
        tmp_path, fx.with_entries(fx.adopted(), entries), fx.RecordingWorkspace(), capsys
    )

    assert rc == 2
    assert gate.REMEDIES[code] in err
    assert f"REFUSED {code} " in err


def test_the_cli_reads_the_acknowledgement_from_the_environment(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = fx.with_entries(fx.adopted(), {INSTANCE: fx.instance_update()})
    digest = gate.instance_update_digest(parse_plan(fx.dumps(doc)))

    rc, _out, err, _report = _main(tmp_path, doc, fx.RecordingWorkspace(), capsys)
    assert rc == 2 and f"{gate.ACK_ENV}={digest}" in err

    monkeypatch.setenv(gate.ACK_ENV, digest)
    rc, out, _err, _report = _main(tmp_path, doc, fx.RecordingWorkspace(), capsys)
    assert rc == 0 and "pre-lease plan gate passed" in out


@pytest.mark.parametrize("text", ("", "not json", '{"plan_version": 1, "plan": {}}'))
def test_an_unreadable_plan_refuses_as_malformed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], text: str
) -> None:
    rc, out, err, report = _main(tmp_path, text, fx.RecordingWorkspace(), capsys)

    assert rc == 2
    assert "REFUSED malformed_plan bundle.plan" in err
    assert json.loads(report.read_text(encoding="utf-8"))["refusals"][0]["code"] == "malformed_plan"
    assert "public summary" in out


def test_a_missing_plan_file_refuses(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = gate.main(["check", "--plan", str(tmp_path / "absent.json"), "--position", "pre-apply"])

    assert rc == 2
    assert (
        "REFUSED malformed_plan bundle.plan (unreadable: FileNotFoundError)"
        in capsys.readouterr().err
    )
