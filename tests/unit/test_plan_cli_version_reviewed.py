"""A plan any CLI but the reviewed one made is refused before the lease, at every governed site.

The parser, the plan gate and the adopt map were reviewed, and spike S10
measured, on one Databricks CLI, ``REVIEWED_CLI_VERSION``. The drift test
(test_governed_cli_version.py) pins what CI installs; this file proves the
binary that made a plan was that one: its ``cli_version`` must equal
``REVIEWED_CLI_VERSION`` exactly, or the parser, the gate's ``evaluate``, the
adopt plan's ``compute_plan`` and the monitor scheduler gate refuse
``malformed_plan`` on ``bundle.plan``. The public line names the two version
strings and the existing remedy; the private record keeps the parser message.
The owner's adopt preflight plans no CLI document: its declared plan says so
(``DECLARED_CLI_VERSION``) and is held to exactly that.
"""

from __future__ import annotations

import ast
import dataclasses
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures import adopt_estate as es
from tests.fixtures import bundle_plan_fixtures as fx
from tools.databricks import adopt_preflight as preflight
from tools.databricks import bundle_plan_gate as gate
from tools.databricks import install_state_adopt as adopt
from tools.databricks import monitor_scheduler_gate as monitor
from tools.databricks.bundle_plan_adopt import POSITION, AdoptExpectation
from tools.databricks.bundle_plan_shape import (
    DECLARED_CLI_VERSION,
    REVIEWED_CLI_VERSION,
    BundlePlan,
    CliVersionRefused,
    PlanShapeError,
    cli_version_refusal,
    parse_plan,
    require_reviewed_cli,
)

REPO = Path(__file__).resolve().parents[2]
REVIEWED = f'reviewed "{REVIEWED_CLI_VERSION}"'
# Every other string refuses: none, a tag, padding, a pre-release, a patch, the old pin.
OTHERS = {
    "": f'cli_version "", {REVIEWED}',
    "v1.10.0": f'cli_version "v1.10.0", {REVIEWED}',
    "1.10.0 ": f"cli_version (not a version string), {REVIEWED}",
    "1.10.0-dev": f'cli_version "1.10.0-dev", {REVIEWED}',
    "1.10.1": f'cli_version "1.10.1", {REVIEWED}',
    "1.7.0": f'cli_version "1.7.0", {REVIEWED}',
}
INJECTED = "1.10.0\n::error::zyrplax-injected"


def _made_by(cli_version: str | None, doc: dict[str, Any] | None = None) -> str:
    """A fixture plan as that CLI would print it; ``None`` leaves the field out."""

    document = fx.adopted() if doc is None else doc
    if cli_version is None:
        document.pop("cli_version")
    else:
        document["cli_version"] = cli_version
    return fx.dumps(document)


# --- the parser --------------------------------------------------------------------------------


def test_the_reviewed_cli_parses() -> None:
    assert parse_plan(_made_by(REVIEWED_CLI_VERSION)).cli_version == REVIEWED_CLI_VERSION == "1.10.0"


@pytest.mark.parametrize(("found", "reason"), [*OTHERS.items(), (None, OTHERS[""])])
def test_any_other_cli_version_refuses_as_malformed_on_the_document(found: str | None, reason: str) -> None:
    with pytest.raises(CliVersionRefused) as raised:
        parse_plan(_made_by(found))

    error = raised.value
    assert isinstance(error, PlanShapeError), "every caller's existing PlanShapeError path takes it"
    assert (error.resource, error.field, error.reason) == ("bundle.plan", "cli_version", reason)
    assert error.public == f"bundle.plan ({reason})"
    record = error.private()
    assert (record["code"], record["field"]) == ("malformed_plan", "cli_version")
    assert repr(found or "") in record["message"], "the private message keeps the exact value"
    assert record["skeleton"] is not None and record["skeleton"]["json"] is True


def test_the_cli_version_refuses_before_any_entry_is_read() -> None:
    """The root cause first: another CLI's plan with an unknown field names the CLI, not the field."""

    doc = fx.adopted()
    doc["plan"]["resources.jobs.mip_ref_seed"]["drift"] = True
    with pytest.raises(PlanShapeError, match="unknown field"):
        parse_plan(_made_by(REVIEWED_CLI_VERSION, json.loads(json.dumps(doc))))

    with pytest.raises(CliVersionRefused) as raised:
        parse_plan(_made_by("1.7.0", doc))
    assert raised.value.public == f"bundle.plan ({OTHERS['1.7.0']})"


def test_a_version_outside_the_version_characters_is_never_echoed() -> None:
    for found in (INJECTED, "x" * 65, "1.10.0\t", "1,10,0"):
        with pytest.raises(CliVersionRefused) as raised:
            parse_plan(_made_by(found))
        assert raised.value.public == f"bundle.plan (cli_version (not a version string), {REVIEWED})"
    assert "zyrplax" not in raised.value.public and "\n" not in raised.value.public


def test_a_non_string_cli_version_keeps_its_type_refusal() -> None:
    with pytest.raises(PlanShapeError) as raised:
        parse_plan(_made_by(10))  # type: ignore[arg-type]
    assert not isinstance(raised.value, CliVersionRefused) and raised.value.public == "bundle.plan (cli_version)"


def test_the_expected_version_is_a_closed_pair() -> None:
    """No caller can widen the check: only the reviewed CLI, or no CLI at all."""

    for widened in ("1.7.0", "1.10.1", " 1.10.0", "*"):
        with pytest.raises(ValueError, match="reviewed CLI, or from no CLI"):
            parse_plan(_made_by(widened), expected_cli_version=widened)
        with pytest.raises(ValueError, match="reviewed CLI, or from no CLI"):
            cli_version_refusal(widened, widened)
    declared = parse_plan(_made_by(DECLARED_CLI_VERSION), expected_cli_version=DECLARED_CLI_VERSION)
    assert declared.cli_version == DECLARED_CLI_VERSION == ""
    with pytest.raises(CliVersionRefused) as raised:
        parse_plan(_made_by(REVIEWED_CLI_VERSION), expected_cli_version=DECLARED_CLI_VERSION)
    assert raised.value.reason == f'cli_version "{REVIEWED_CLI_VERSION}", reviewed ""'


def _other_cli(plan: BundlePlan, version: str = "1.7.0") -> BundlePlan:
    """A parsed plan relabelled, as a consumer handed a plan from anywhere would see it."""

    return dataclasses.replace(plan, cli_version=version)


def test_require_reviewed_cli_refuses_a_parsed_plan_from_another_cli() -> None:
    plan = parse_plan(_made_by(REVIEWED_CLI_VERSION))

    assert require_reviewed_cli(plan) is plan
    for found, reason in OTHERS.items():
        with pytest.raises(CliVersionRefused) as raised:
            require_reviewed_cli(_other_cli(plan, found))
        assert raised.value.reason == reason


# --- the plan gate (pre-lease, pre-apply, post-bind) -------------------------------------------


@pytest.mark.parametrize("position", ("pre-lease", "pre-apply"))
@pytest.mark.parametrize(("found", "reason"), [*OTHERS.items(), (INJECTED, OTHERS["1.10.0 "])])
def test_the_gate_cli_refuses_another_cli_with_both_versions_and_the_remedy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], position: str, found: str, reason: str
) -> None:
    plan, report, public = tmp_path / "plan.json", tmp_path / "report.json", tmp_path / "public.json"
    plan.write_text(_made_by(found, fx.first_install()), encoding="utf-8")
    built: list[str] = []

    rc = gate.main(
        ["check", "--plan", str(plan), "--position", position, "--report-out", str(report), "--public-out", str(public)],
        workspace_factory=lambda: built.append("workspace") or fx.RecordingWorkspace(),
    )

    out, err = capsys.readouterr()
    assert rc == 2 and built == [], "no lookup: a plan another CLI made is judged by nothing else"
    refused = [line for line in err.splitlines() if "REFUSED" in line]
    assert refused == [f"[mip-plan-gate] REFUSED malformed_plan bundle.plan ({reason}): {gate.REMEDIES['malformed_plan']}"]
    assert "zyrplax" not in out + err
    assert json.loads(public.read_text(encoding="utf-8"))["refusals"] == [
        {"code": "malformed_plan", "resource": "bundle.plan", "related": []}
    ]
    detail = json.loads(report.read_text(encoding="utf-8"))["refusals"][0]["detail"]
    assert detail["reason"] == reason and repr(found) in detail["message"]


def test_the_gate_cli_passes_the_reviewed_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text(_made_by(REVIEWED_CLI_VERSION), encoding="utf-8")

    rc = gate.main(["check", "--plan", str(plan), "--position", "pre-lease"], workspace_factory=fx.RecordingWorkspace)

    assert rc == 0 and f"cli {REVIEWED_CLI_VERSION}" in capsys.readouterr().out


@pytest.mark.parametrize("position", ("pre-lease", "pre-apply", POSITION))
def test_evaluate_refuses_a_parsed_plan_from_another_cli_before_any_lookup(position: str) -> None:
    plan = _other_cli(parse_plan(_made_by(REVIEWED_CLI_VERSION, fx.first_install())))
    adopt_mode = AdoptExpectation(frozenset(), frozenset()) if position == POSITION else None

    def never() -> Any:
        raise AssertionError("evaluate looked a name up for a plan another CLI made")

    report = gate.evaluate(
        plan, position=position, app_key="mip_app", acknowledgement="", workspace_factory=never, adopt=adopt_mode
    )

    assert report.codes == ("malformed_plan",) and report.lookups == ()
    (refusal,) = report.refusals
    assert (refusal.resource, refusal.detail["reason"]) == ("bundle.plan", OTHERS["1.7.0"])
    assert report.public_summary()["verdict"] == "refused"


# --- the adopt and unadopt plan (10B-1), its recompute and the post-bind plan --------------------


def _relabel(version: str) -> Any:
    def malform(text: str) -> str:
        document = json.loads(text)
        document["cli_version"] = version
        return json.dumps(document, indent=2)

    return malform


class _Lease:
    lease_id = "lease-1"
    source_git_sha = "0" * 40

    def __call__(self) -> None:
        return None


def _adopt_main(estate: es.Estate, cli: Any, argv: list[str]) -> int:
    return adopt.main(
        argv,
        inputs=es.inputs,
        workspace_factory=lambda: estate,
        cli_factory=lambda: cli,
        lease_factory=lambda _workspace, _args: _Lease(),
    )


def _apply(sha: str, report: Path) -> list[str]:
    return [
        "apply", "--phase", "adopt", "--ack-plan", sha, "--app-name", "a", "--lease-id", "l",
        "--source-git-sha", "0" * 40, "--report-out", str(report),
    ]  # fmt: skip


@pytest.mark.parametrize("phase", ("adopt", "unadopt"))
def test_the_adopt_plan_refuses_another_cli_publicly_by_both_versions(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], phase: str
) -> None:
    estate = es.Estate()
    cli = es.FakeBundleCli(estate, malform=_relabel("1.7.0"))
    report = tmp_path / "report.json"

    rc = _adopt_main(estate, cli, ["plan", "--phase", phase, "--report-out", str(report)])

    _out, err = capsys.readouterr()
    assert rc == 2
    assert f"[mip-adopt] REFUSED malformed_plan bundle.plan ({OTHERS['1.7.0']}): {adopt.MALFORMED_REMEDY}" in err
    record = json.loads(report.read_text(encoding="utf-8"))["malformed_plan"]
    assert (record["resource"], record["field"]) == ("bundle.plan", "cli_version") and "'1.7.0'" in record["message"]
    assert cli.edits() == []


class _OtherCli:
    """A BundleCli whose plans another CLI made, handed over already parsed."""

    def __init__(self, inner: es.FakeBundleCli) -> None:
        self.inner = inner
        self.errors: list[str] = []

    def plan(self) -> BundlePlan:
        return _other_cli(self.inner.plan())

    def bind(self, *args: Any) -> None:
        raise AssertionError("no bind for a plan another CLI made")

    unbind = bind


def test_compute_plan_refuses_a_parsed_plan_from_another_cli_before_any_state_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    estate = es.Estate()

    class Untouched:
        def __getattr__(self, name: str) -> Any:
            raise AssertionError(f"the state was read ({name}) for a plan another CLI made")

    with pytest.raises(CliVersionRefused):
        adopt.compute_plan("adopt", es.inputs(), _OtherCli(es.FakeBundleCli(estate)), Untouched())

    assert _adopt_main(estate, _OtherCli(es.FakeBundleCli(estate)), ["plan", "--phase", "adopt"]) == 2
    assert f"[mip-adopt] REFUSED malformed_plan bundle.plan ({OTHERS['1.7.0']}): " in capsys.readouterr().err


def test_the_recompute_under_the_lease_refuses_another_cli_before_any_edit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    estate = es.Estate()
    sha = adopt.compute_plan("adopt", es.inputs(), es.FakeBundleCli(estate), estate).sha256
    cli = es.FakeBundleCli(estate, malform=_relabel("1.10.1"))

    assert _adopt_main(estate, cli, _apply(sha, tmp_path / "report.json")) == 2

    err = capsys.readouterr().err
    assert f"[mip-adopt] REFUSED malformed_plan bundle.plan ({OTHERS['1.10.1']}): " in err
    assert cli.edits() == [] and estate.bound() == {}


def test_a_post_bind_plan_from_another_cli_undoes_the_binds(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    estate = es.Estate()
    sha = adopt.compute_plan("adopt", es.inputs(), es.FakeBundleCli(estate), estate).sha256
    cli = es.FakeBundleCli(estate, malform=_relabel("1.7.0"), malform_from=2)

    assert _adopt_main(estate, cli, _apply(sha, tmp_path / "report.json")) == 2

    err = capsys.readouterr().err
    # One stable class for every parser refusal (CliVersionRefused included), as both docs print it.
    assert (
        f"[mip-adopt] REFUSED bind_failed: PlanShapeError during the binds (malformed_plan bundle.plan ({OTHERS['1.7.0']}))"
        in err.splitlines()
    )
    assert "CliVersionRefused" not in err
    assert "nothing is left changed: every bind this run made was undone" in err
    assert estate.bound() == {}


# --- the monitor scheduler gate (beside the plan gate, same document) ----------------------------


def test_the_monitor_gate_refuses_another_cli_without_a_live_read() -> None:
    def never() -> Any:
        raise AssertionError("the monitor gate read the workspace for a plan another CLI made")

    for found, reason in OTHERS.items():
        assert monitor.check(_made_by(found), workspace_factory=never) == [
            f"the bundle plan could not be read (malformed_plan bundle.plan ({reason}))"
        ]


# --- the owner's adopt preflight: a declared plan no CLI made ------------------------------------


def test_the_preflight_declares_its_plan_no_cli_made() -> None:
    bundle = {"targets": {"client": {"mode": "production"}}, "resources": {"jobs": {"mip_ref_seed": {"name": "n"}}}}

    plan = preflight.declared_plan(bundle, {})

    assert plan.cli_version == DECLARED_CLI_VERSION == ""
    with pytest.raises(CliVersionRefused):
        require_reviewed_cli(plan)  # never a governed plan


# --- every caller is reviewed -------------------------------------------------------------------


def _parse_plan_calls(source: str) -> list[tuple[str, ...]]:
    """Each ``parse_plan(...)`` call's keyword arguments, as ``name=expression`` strings."""

    calls = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name == "parse_plan":
                calls.append(tuple(f"{kw.arg}={ast.unparse(kw.value)}" for kw in node.keywords))
    return calls


def test_only_the_preflight_expects_a_plan_no_cli_made() -> None:
    tracked = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.py"], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout.split("\0")
    sources = {
        path: (REPO / path).read_text(encoding="utf-8", errors="replace")
        for path in tracked
        if path and not path.startswith("tests/")
    }
    callers = {
        path: calls for path, source in sources.items() if "parse_plan" in source and (calls := _parse_plan_calls(source))
    }

    assert callers == {
        "tools/databricks/adopt_preflight.py": [("expected_cli_version=DECLARED_CLI_VERSION",)],
        "tools/databricks/bundle_plan_gate.py": [()],
        "tools/databricks/install_state_adopt.py": [()],
        "tools/databricks/monitor_scheduler_gate.py": [()],
    }, "a new caller, or a caller expecting anything but the reviewed CLI, is reviewed here first"
    assert sorted(path for path, source in sources.items() if "DECLARED_CLI_VERSION" in source) == [
        "tools/databricks/adopt_preflight.py",
        "tools/databricks/bundle_plan_shape.py",
    ]
    # Positive control: the walker sees a widened call however it is spelled.
    assert _parse_plan_calls("shape.parse_plan(t, expected_cli_version='1.7.0')") == [
        ("expected_cli_version='1.7.0'",)
    ]
