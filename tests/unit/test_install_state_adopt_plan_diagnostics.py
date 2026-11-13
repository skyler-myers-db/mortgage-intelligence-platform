"""A plan the parser refuses at 10B names its field privately, never in the public log (wave 7f).

The first governed parse of the whole bundle's plan is the adopt phase's
pre-bind plan (the first-client cutover's 10B-1). A ``PlanShapeError`` there
used to print only its class and write no report. Now the public line carries
``malformed_plan``, the symbolic resource and the field class, and the private
report carries the parser's message and the document's value-free skeleton.
The repository's run logs are public, and in a post-bind plan a change path
can carry a remote map key, so the message never reaches the public line.

Driven against the synthetic 16-key estate (tests/fixtures/adopt_estate.py);
``MAP_KEY`` stands for a remote map key in a change path and ``VALUE`` for a
value, and neither may reach stdout or stderr.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from tests.fixtures import adopt_estate as es
from tools.databricks import install_state_adopt as adopt

MAP_KEY = "zyrplax-remote-map-key"
VALUE = "zyrplax-remote-value"
JOB = "resources.jobs.mip_ref_seed"


class Lease:
    lease_id = "lease-1"
    source_git_sha = "0" * 40

    def __call__(self) -> None:
        return None


def _unknown_change_field(text: str) -> str:
    """One change desc with a field the parser does not know, at a path keyed by a remote map key."""

    document = json.loads(text)
    entry = document["plan"][JOB]
    entry.setdefault("changes", {})[f"tags['{MAP_KEY}']"] = {"action": "skip", "old": VALUE, "why": VALUE}
    return json.dumps(document, indent=2)


def _duplicate_remote_key(text: str) -> str:
    """A remote object whose map repeats a key, which the parser refuses at any depth."""

    document = json.loads(text)
    document["plan"][JOB]["remote_state"] = {"tags": {MAP_KEY: VALUE}}
    rendered = json.dumps(document, indent=2)
    pair = f'"{MAP_KEY}": "{VALUE}"'
    assert rendered.count(pair) == 1
    return rendered.replace(pair, f"{pair}, {pair}")


def _main(estate: es.Estate, cli: es.FakeBundleCli, argv: list[str]) -> int:
    return adopt.main(
        argv,
        inputs=es.inputs,
        workspace_factory=lambda: estate,
        cli_factory=lambda: cli,
        lease_factory=lambda _workspace, _args: Lease(),
    )


def _apply_args(sha: str, report: Path) -> list[str]:
    return [
        "apply", "--phase", "adopt", "--ack-plan", sha, "--app-name", "a", "--lease-id", "l",
        "--source-git-sha", "0" * 40, "--report-out", str(report),
    ]  # fmt: skip


@pytest.mark.parametrize("phase", ("adopt", "unadopt"))
def test_a_pre_bind_plan_the_parser_refuses_names_its_field_class_and_reports_privately(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], phase: str
) -> None:
    estate = es.Estate()
    cli = es.FakeBundleCli(estate, malform=_unknown_change_field)
    report = tmp_path / "report.json"

    rc = _main(estate, cli, ["plan", "--phase", phase, "--report-out", str(report)])

    out, err = capsys.readouterr()
    assert rc == 2
    assert f"[mip-adopt] REFUSED malformed_plan jobs.mip_ref_seed (changes): {adopt.MALFORMED_REMEDY}" in err
    assert MAP_KEY not in out + err and VALUE not in out + err
    assert "PlanShapeError" not in out + err, "the field class replaces the bare class name"
    assert stat.S_IMODE(report.stat().st_mode) == 0o600
    record = json.loads(report.read_text(encoding="utf-8"))["malformed_plan"]
    assert (record["code"], record["resource"], record["field"]) == ("malformed_plan", "jobs.mip_ref_seed", "changes")
    assert MAP_KEY in record["message"] and "unknown field(s) why" in record["message"]
    skeleton = record["skeleton"]
    assert skeleton["json"] is True
    changes = skeleton["shape"]["plan"][JOB]["changes"][f"tags['{MAP_KEY}']"]
    assert changes == {"action": "str", "old": "str", "why": "str"}
    assert VALUE not in report.read_text(encoding="utf-8"), "the message and the skeleton carry no value"
    assert cli.edits() == []


def test_a_duplicate_field_anywhere_is_public_only_as_its_class(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    estate = es.Estate()
    report = tmp_path / "report.json"

    rc = _main(estate, es.FakeBundleCli(estate, malform=_duplicate_remote_key), ["plan", "--phase", "adopt", "--report-out", str(report)])

    out, err = capsys.readouterr()
    assert rc == 2
    assert "[mip-adopt] REFUSED malformed_plan bundle.plan (duplicate_field): " in err
    assert MAP_KEY not in out + err and VALUE not in out + err
    record = json.loads(report.read_text(encoding="utf-8"))["malformed_plan"]
    assert record["message"] == f"duplicate field {MAP_KEY!r}"
    assert record["skeleton"]["shape"]["plan"][JOB]["remote_state"]["tags"] == {
        MAP_KEY: "str",
        "__duplicate_keys__": [MAP_KEY],
    }


def test_the_recompute_under_the_lease_refuses_before_any_edit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    estate = es.Estate()
    sha = adopt.compute_plan("adopt", es.inputs(), es.FakeBundleCli(estate), estate).sha256
    cli = es.FakeBundleCli(estate, malform=_unknown_change_field)
    report = tmp_path / "report.json"

    assert _main(estate, cli, _apply_args(sha, report)) == 2

    out, err = capsys.readouterr()
    assert "[mip-adopt] REFUSED malformed_plan jobs.mip_ref_seed (changes): " in err
    assert MAP_KEY not in out + err
    assert cli.edits() == [] and estate.bound() == {}
    assert MAP_KEY in json.loads(report.read_text(encoding="utf-8"))["malformed_plan"]["message"]


def test_a_post_bind_plan_the_parser_refuses_undoes_the_binds_and_reports_privately(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    estate = es.Estate()
    sha = adopt.compute_plan("adopt", es.inputs(), es.FakeBundleCli(estate), estate).sha256
    # Plan call 1 is the recompute under the lease; call 2 is the post-bind plan.
    cli = es.FakeBundleCli(estate, malform=_unknown_change_field, malform_from=2)
    report = tmp_path / "report.json"

    rc = _main(estate, cli, _apply_args(sha, report))

    out, err = capsys.readouterr()
    assert rc == 2
    assert (
        "[mip-adopt] REFUSED bind_failed: PlanShapeError during the binds "
        "(malformed_plan jobs.mip_ref_seed (changes))"
    ) in err
    assert "nothing is left changed: every bind this run made was undone" in err
    assert MAP_KEY not in out + err and VALUE not in out + err
    assert [call[0] for call in cli.edits()] == ["bind"] * 16 + ["unbind"] * 16
    assert estate.bound() == {}
    written = json.loads(report.read_text(encoding="utf-8"))
    assert written["plan_sha256"] == sha, "the report keeps the acknowledged plan"
    record = written["malformed_plan"]
    assert (record["resource"], record["field"]) == ("jobs.mip_ref_seed", "changes")
    assert MAP_KEY in record["message"]
    assert VALUE not in report.read_text(encoding="utf-8")
