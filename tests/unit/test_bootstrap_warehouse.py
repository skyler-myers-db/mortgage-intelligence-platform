"""A first install's bootstrap SQL warehouse comes from the client profile (B1, wave 7 part 2).

A new client's deployer has no bundle state yet, so ``bundle_state
warehouse-id`` prints nothing, and CI's ``.env.local`` carries no
``DATABRICKS_WAREHOUSE_ID`` (it is no profile field). Every live plan and
release refused. The profile's optional identifying field
``MIP_BOOTSTRAP_WAREHOUSE_ID`` now names the warehouse a first install's steps
borrow until the step-4 apply creates the bundle's own:

* ``python -m tools.databricks.bundle_state bootstrap-warehouse-id`` proves it
  read-only (one state download, one ``apps.get``, one ``warehouses.get``):
  the state binds neither the warehouse nor the App, no App exists, the
  profile names no legacy root, and the warehouse is that id, live and PRO.
  It prints the id (masked first) or refuses with exit 2, never naming it.
* The preflight's ``bundle warehouse`` block admits it only when the state
  binds no warehouse, in plan, release or no phase (an allowlist: any other
  phase refuses it unchecked), and only when the check prints exactly the id
  the shell read (16 lowercase hex characters, a shape the shell checks
  itself). The state's warehouse always wins; the adopt phases never read
  it; the data phase refuses it, and the refusal for no warehouse at all
  says so in every phase's own words (one text: every other phase runs the
  block as a run with no phase does,
  tests/unit/test_adopt_phases_read_no_warehouse.py); a dry run never
  checks or prints it; an operator value naming another refuses.

The pins of the operator-only path and of the adopt phases stay where they are
(tests/unit/test_bundle_state_warehouse.py,
tests/unit/test_adopt_phases_read_no_warehouse.py), unedited.
"""

from __future__ import annotations

import io
import json
import shlex
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from databricks.sdk.errors import NotFound, PermissionDenied
from databricks.sdk.service.sql import (
    GetWarehouseResponse,
    GetWarehouseResponseWarehouseType,
    State,
)

from tests.fixtures.client_profiles import DEMO_PROFILE, IDENTITY_IDS, env_file_text
from tests.fixtures.deploy_block_harness import block, drive, function
from tests.fixtures.deploy_script import deploy_entrypoint_text
from tests.fixtures.live_workspace import bundle_state
from tools.clients.environment import RawProfile, resolve
from tools.databricks import bundle_state as state

BOOT = "b0b1b2b3b4b5b6b7"
STATE_WAREHOUSE = "a0b1c2d3e4f5a6b7"
OTHER = "c0c1c2c3c4c5c6c7"
HOST = DEMO_PROFILE["DATABRICKS_HOST"].removeprefix("https://")
PROFILE = {**DEMO_PROFILE, "MIP_BOOTSTRAP_WAREHOUSE_ID": BOOT}
STATE_PATH = (
    f"/Users/{IDENTITY_IDS['deployer']}/.bundle/mortgage-intelligence-platform/"
    f"{DEMO_PROFILE['MIP_CLIENT']}/state/resources.json"
)


@pytest.fixture(autouse=True)
def _outside_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)


# --- the read-only check --------------------------------------------------------------


class _Strict(SimpleNamespace):
    """A namespace whose every unstubbed attribute fails the test: the read surface is closed."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"bootstrap-warehouse-id reached an unstubbed SDK surface: {name}")


def _warehouse(
    warehouse_id: str = BOOT,
    state_: State | None = State.RUNNING,
    kind: GetWarehouseResponseWarehouseType | None = GetWarehouseResponseWarehouseType.PRO,
) -> GetWarehouseResponse:
    return GetWarehouseResponse(id=warehouse_id, state=state_, warehouse_type=kind, enable_serverless_compute=True)


class _Workspace(_Strict):
    """Exactly the three GETs the check may make, each recorded."""

    def __init__(
        self,
        *,
        body: object = None,
        state_error: BaseException | None = None,
        app: BaseException | object | None = None,
        warehouse: BaseException | GetWarehouseResponse | None = None,
    ) -> None:
        calls: list[tuple[str, str]] = []

        def download(path: str) -> Any:
            calls.append(("workspace.download", path))
            if state_error is not None:
                raise state_error
            if body is None:
                raise NotFound("no state")
            return io.BytesIO(body if isinstance(body, bytes) else json.dumps(body).encode())

        def get_app(*, name: str) -> Any:
            calls.append(("apps.get", name))
            found = NotFound("no app") if app is None else app
            if isinstance(found, BaseException):
                raise found
            return found

        def get_warehouse(*, id: str) -> Any:  # noqa: A002 - the SDK's keyword
            calls.append(("warehouses.get", id))
            found = _warehouse() if warehouse is None else warehouse
            if isinstance(found, BaseException):
                raise found
            return found

        super().__init__(
            calls=calls,
            workspace=_Strict(download=download),
            apps=_Strict(get=get_app),
            warehouses=_Strict(get=get_warehouse),
        )


def _check(tmp_path: Path, workspace: _Workspace, values: dict[str, str] = PROFILE) -> int:
    env_file = tmp_path / ".env.local"
    env_file.write_text(env_file_text(values), encoding="utf-8")
    return state.main(["bootstrap-warehouse-id"], environ={}, env_file=env_file, client=workspace)


@pytest.mark.parametrize("warehouse_state", [State.RUNNING, State.STOPPED, State.STARTING])
def test_a_first_install_prints_the_checked_bootstrap_warehouse(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], warehouse_state: State
) -> None:
    workspace = _Workspace(warehouse=_warehouse(state_=warehouse_state))

    assert _check(tmp_path, workspace) == 0

    out = capsys.readouterr()
    assert out.out == f"{BOOT}\n" and out.err == ""
    assert workspace.calls == [
        ("workspace.download", STATE_PATH),
        ("apps.get", DEMO_PROFILE["MIP_APP_NAME"]),
        ("warehouses.get", BOOT),
    ], "GET-only: the state, the App by name, the warehouse by id; nothing else"


def test_under_actions_the_bootstrap_warehouse_is_masked_on_stderr_first(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    assert _check(tmp_path, _Workspace()) == 0

    out = capsys.readouterr()
    assert out.out == f"{BOOT}\n", "the id alone on stdout: the shell compares it exactly"
    assert out.err == f"::add-mask::{BOOT}\n"


# Each refusal: (workspace, profile values, the check's words on stderr).
REFUSALS: dict[str, tuple[_Workspace, dict[str, str], str]] = {
    "field-unset": (_Workspace(), DEMO_PROFILE, "sets no MIP_BOOTSTRAP_WAREHOUSE_ID"),
    "state-binds-the-warehouse": (
        _Workspace(body=bundle_state(**{"sql_warehouses.mip_serverless_sql": STATE_WAREHOUSE})),
        PROFILE,
        "binds the bundle's own SQL warehouse",
    ),
    "state-binds-the-app": (_Workspace(body=bundle_state(**{"apps.mip_app": "mip-app"})), PROFILE, "binds the App"),
    "the-app-exists": (_Workspace(app=SimpleNamespace(name="mip-app")), PROFILE, "the App exists"),
    "apps-get-denied": (
        _Workspace(app=PermissionDenied(f"denied on {HOST}")),
        PROFILE,
        "does not exist yet: PermissionDenied",
    ),
    "warehouse-not-found": (_Workspace(warehouse=NotFound(f"{BOOT} not found")), PROFILE, "names no SQL warehouse"),
    "warehouse-denied": (
        _Workspace(warehouse=PermissionDenied(f"{BOOT} denied on {HOST}")),
        PROFILE,
        "warehouse: PermissionDenied",
    ),
    "another-id": (_Workspace(warehouse=_warehouse(warehouse_id=OTHER)), PROFILE, "is another one"),
    "deleted": (_Workspace(warehouse=_warehouse(state_=State.DELETED)), PROFILE, "deleted, being deleted"),
    "deleting": (_Workspace(warehouse=_warehouse(state_=State.DELETING)), PROFILE, "deleted, being deleted"),
    "no-state": (_Workspace(warehouse=_warehouse(state_=None)), PROFILE, "has no state"),
    "classic": (
        _Workspace(warehouse=_warehouse(kind=GetWarehouseResponseWarehouseType.CLASSIC)),
        PROFILE,
        "not pro or serverless",
    ),
    "type-unspecified": (
        _Workspace(warehouse=_warehouse(kind=GetWarehouseResponseWarehouseType.TYPE_UNSPECIFIED)),
        PROFILE,
        "not pro or serverless",
    ),
    "no-type": (_Workspace(warehouse=_warehouse(kind=None)), PROFILE, "not pro or serverless"),
    "state-unreadable": (
        _Workspace(state_error=PermissionDenied(f"denied for {IDENTITY_IDS['deployer']}")),
        PROFILE,
        "PermissionDenied",
    ),
    "unknown-state-version": (
        _Workspace(body=bundle_state() | {"state_version": 1}),
        PROFILE,
        "version is not the reviewed one",
    ),
    "no-live-id": (
        _Workspace(body=bundle_state(**{"apps.mip_app": ""})),
        PROFILE,
        "has no live id",
    ),
}


@pytest.mark.parametrize("case", REFUSALS)
def test_every_unproven_bootstrap_refuses_naming_the_check_never_a_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], case: str
) -> None:
    workspace, values, needle = REFUSALS[case]

    assert _check(tmp_path, workspace, values) == 2

    out = capsys.readouterr()
    assert out.out == "", "nothing on stdout: the shell's capture stays empty"
    assert needle in out.err, out.err
    for value in (BOOT, OTHER, STATE_WAREHOUSE, IDENTITY_IDS["deployer"], HOST):
        assert value not in out.err, value


def test_an_adopted_profile_never_reaches_a_warehouse_read() -> None:
    """Defence in depth behind the profile rule: a legacy root refuses before any GET."""

    profile = resolve(RawProfile(values=PROFILE)).model_copy(
        update={"legacy_bundle_root": "/Workspace/Users/legacy@example.invalid/.bundle/mortgage-intelligence-platform/x"}
    )
    workspace = _Workspace()

    with pytest.raises(state.BundleStateError, match="is for a first install"):
        state.bootstrap_warehouse_id(workspace, profile, None)
    assert workspace.calls == []


def test_the_cli_takes_exactly_one_of_its_two_commands(capsys: pytest.CaptureFixture[str]) -> None:
    for argv in (["bootstrap-warehouse-id", "--extra"], ["bootstrap-warehouse-id", "warehouse-id"], ["warehouse"]):
        assert state.main(argv) == 2, argv
    assert "usage: python -m tools.databricks.bundle_state {warehouse-id|bootstrap-warehouse-id}" in capsys.readouterr().err


# --- the preflight block ---------------------------------------------------------------


START, END = "# >>> bundle warehouse >>>", "# <<< bundle warehouse <<<"
FUNCTIONS = ("check_bootstrap_warehouse", "note_bootstrap_warehouse")
VERIFIER_REFUSED = "the client profile's MIP_BOOTSTRAP_WAREHOUSE_ID was refused (see above); nothing was changed."
NOT_EXACT = "the bootstrap warehouse check did not print exactly the client profile's MIP_BOOTSTRAP_WAREHOUSE_ID"
NOT_THIS_PHASE = "MIP_BOOTSTRAP_WAREHOUSE_ID is read only by a plan, a release or a run with no phase"


@dataclass(frozen=True)
class Preflight:
    result: subprocess.CompletedProcess[str]
    calls: list[list[str]]  # each bundle_state argv, in order
    controls: list[str]  # each name deployment_control_value was asked for
    outcome: tuple[str, str, str] | None  # (warehouse, source, exported) once past the block

    @property
    def transcript(self) -> str:
        return self.result.stdout + self.result.stderr


def _preflight(
    repo: Path,
    *,
    phase: str | None = None,
    state_output: str | None = "",
    bootstrap: str = BOOT,
    verifier: str | None = f"{BOOT}\n",
    operator: str = "",
    ambient: str = "",
    dry_run: int = 0,
) -> Preflight:
    """The real block and its two functions; a bundle_state stub answering both commands.

    ``state_output`` and ``verifier`` are what ``warehouse-id`` and
    ``bootstrap-warehouse-id`` print (``None``: refuse with exit 2).
    """

    repo.mkdir(parents=True, exist_ok=True)
    package = repo / "tools" / "databricks"
    package.mkdir(parents=True, exist_ok=True)
    for init in (repo / "tools" / "__init__.py", package / "__init__.py"):
        init.write_text("", encoding="utf-8")
    calls, controls, outcome = repo / "calls.jsonl", repo / "controls.txt", repo / "outcome.txt"
    (package / "bundle_state.py").write_text(
        textwrap.dedent(
            f"""\
            import json, sys
            answers = {{"warehouse-id": {state_output!r}, "bootstrap-warehouse-id": {verifier!r}}}
            with open({str(calls)!r}, "a") as log:
                log.write(json.dumps(sys.argv[1:]) + "\\n")
            assert len(sys.argv) == 2 and sys.argv[1] in answers, sys.argv
            answer = answers[sys.argv[1]]
            if answer is None:
                print("[bundle-state] refused by the stub", file=sys.stderr)
                raise SystemExit(2)
            sys.stdout.write(answer)
            """
        ),
        encoding="utf-8",
    )
    values = {"DATABRICKS_WAREHOUSE_ID": operator, "MIP_BOOTSTRAP_WAREHOUSE_ID": bootstrap}
    lookup = "".join(f"    {name}) printf '%s' {shlex.quote(value)} ;;\n" for name, value in values.items())
    shell = (
        "".join(function(name) for name in FUNCTIONS)
        + "deployment_control_value() {\n"
        + f"  printf '%s\\n' \"$1\" >> {shlex.quote(str(controls))}\n"
        + f'  case "$1" in\n{lookup}    *) printf \'\' ;;\n  esac\n}}\n'
        + (f"export DATABRICKS_WAREHOUSE_ID={shlex.quote(ambient)}\n" if ambient else "")
        + block(START, END)
    )
    result = drive(
        repo,
        shell,
        dry_run=dry_run,
        env={} if phase is None else {"DEPLOY_PHASE": phase},
        # The outcome goes to a file, so a test can prove the id never printed.
        epilogue=(
            'printf \'%s\\n\' "${_GRANTS_WAREHOUSE_ID}" "${WAREHOUSE_SOURCE}" '
            f'"$(printenv DATABRICKS_WAREHOUSE_ID || echo UNSET)" > {shlex.quote(str(outcome))}\n'
        ),
    )
    lines = outcome.read_text(encoding="utf-8").splitlines() if outcome.exists() else None
    return Preflight(
        result,
        [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()] if calls.exists() else [],
        controls.read_text(encoding="utf-8").split() if controls.exists() else [],
        None if lines is None else (lines[0], lines[1], lines[2]),
    )


def _passed(run: Preflight) -> None:
    assert run.result.returncode == 0, run.transcript
    assert "reached-after-block" in run.result.stdout


def _refused(run: Preflight, message: str) -> None:
    assert run.result.returncode == 2, run.transcript
    assert message in run.result.stderr, run.result.stderr
    assert "reached-after-block" not in run.result.stdout and run.outcome is None


@pytest.mark.parametrize("operator", ["", BOOT], ids=["profile-only", "operator-agrees"])
@pytest.mark.parametrize("phase", [None, "plan", "release"])
def test_a_first_install_runs_on_the_profiles_checked_bootstrap_warehouse(
    tmp_path: Path, phase: str | None, operator: str
) -> None:
    run = _preflight(tmp_path, phase=phase, operator=operator)

    _passed(run)
    assert run.calls == [["warehouse-id"], ["bootstrap-warehouse-id"]]
    assert run.outcome == (BOOT, "first-install-bootstrap", BOOT)
    assert "  sql warehouse: first-install-bootstrap\n" in run.result.stdout
    assert "-m tools.databricks.bundle_state bootstrap-warehouse-id" in run.result.stdout
    assert BOOT not in run.transcript, "the id is never printed"


@pytest.mark.parametrize(
    "verifier",
    [
        None,
        "",
        f"{OTHER}\n",
        f"::add-mask::{BOOT}\n{BOOT}\n",
        f"{BOOT}\n{BOOT}\n",
        f"{BOOT.upper()}\n",
        f"{BOOT} \n",
        f" {BOOT}",
        BOOT[:15],
        f"{BOOT}0",
    ],
    ids=[
        "refused",
        "empty",
        "another-id",
        "add-mask-two-lines",
        "twice",
        "uppercase",
        "trailing-space",
        "leading-space",
        "15-characters",
        "17-characters",
    ],
)
@pytest.mark.parametrize("phase", [None, "plan", "release"])
def test_the_block_takes_only_exactly_the_id_it_read_from_the_check(
    tmp_path: Path, phase: str | None, verifier: str | None
) -> None:
    run = _preflight(tmp_path, phase=phase, verifier=verifier)

    _refused(run, VERIFIER_REFUSED if verifier is None else NOT_EXACT)
    assert run.calls == [["warehouse-id"], ["bootstrap-warehouse-id"]]
    assert BOOT not in run.transcript


@pytest.mark.parametrize(
    "malformed",
    ["NOT-A-WAREHOUSE", "warehouse-1", BOOT.upper(), BOOT[:15], f"{BOOT}0", "--out=/x", "<bootstrap-warehouse-id>"],
    ids=["words", "a-name", "uppercase", "15-characters", "17-characters", "an-option", "the-placeholder"],
)
@pytest.mark.parametrize("phase", [None, "plan", "release"])
def test_a_malformed_id_refuses_even_when_the_check_echoes_it(
    tmp_path: Path, phase: str | None, malformed: str
) -> None:
    """The shape half of the exact-id check: equality alone would export what the shell read.

    The profile schema refuses these upstream and the real check prints only
    a schema-checked id, so this is defence in depth; the value would
    otherwise become DATABRICKS_WAREHOUSE_ID and every ``--warehouse-id``.
    """

    run = _preflight(tmp_path, phase=phase, bootstrap=malformed, verifier=f"{malformed}\n")

    _refused(run, NOT_EXACT)
    assert run.calls == [["warehouse-id"], ["bootstrap-warehouse-id"]]
    assert malformed not in run.transcript


@pytest.mark.parametrize("phase", ["supersede", "adopt-live", "rollback", "DATA", "Release", "plan "])
def test_a_phase_the_allowlist_does_not_name_refuses_the_bootstrap_unchecked(tmp_path: Path, phase: str) -> None:
    """Plan, release or no phase: a phase added to deploy.sh later is refused until it is listed."""

    run = _preflight(tmp_path, phase=phase)

    _refused(run, NOT_THIS_PHASE)
    assert run.calls == [["warehouse-id"]], "never checked"
    assert BOOT not in run.transcript

    bound = _preflight(tmp_path / "state-bound", phase=phase, state_output=STATE_WAREHOUSE, verifier=None)
    _passed(bound)
    assert bound.outcome == (STATE_WAREHOUSE, "bundle-state", STATE_WAREHOUSE), "the state's warehouse still wins"


@pytest.mark.parametrize("phase", ["adopt", "unadopt"])
def test_the_check_itself_refuses_the_adopt_phases_the_block_never_hands_it(tmp_path: Path, phase: str) -> None:
    result = drive(
        tmp_path,
        function("check_bootstrap_warehouse") + f"check_bootstrap_warehouse {BOOT} ''\n",
        dry_run=0,
        env={"DEPLOY_PHASE": phase},
        epilogue="",
    )

    assert result.returncode == 2, result.stdout + result.stderr
    assert NOT_THIS_PHASE in result.stderr and "bundle_state" not in result.stdout + result.stderr
    assert "reached-after-block" not in result.stdout and BOOT not in result.stdout + result.stderr


@pytest.mark.parametrize("phase", [None, "plan", "release", "data"])
def test_the_bundle_states_warehouse_wins_and_the_bootstrap_is_not_read(tmp_path: Path, phase: str | None) -> None:
    run = _preflight(tmp_path, phase=phase, state_output=STATE_WAREHOUSE, verifier=None)

    _passed(run)
    assert run.calls == [["warehouse-id"]], "the check never runs once the state binds a warehouse"
    assert run.outcome == (STATE_WAREHOUSE, "bundle-state", STATE_WAREHOUSE)
    assert "  bootstrap warehouse: not read (this deployer's bundle state binds the bundle's own)\n" in run.result.stdout
    assert BOOT not in run.transcript


@pytest.mark.parametrize("phase", [None, "plan", "release"])
def test_an_operator_warehouse_naming_another_than_the_bootstrap_refuses_before_the_check(
    tmp_path: Path, phase: str | None
) -> None:
    run = _preflight(tmp_path, phase=phase, operator=OTHER)

    _refused(run, "DATABRICKS_WAREHOUSE_ID names another warehouse than the client profile's MIP_BOOTSTRAP_WAREHOUSE_ID")
    assert run.calls == [["warehouse-id"]]
    assert BOOT not in run.transcript and OTHER not in run.transcript


def test_the_data_phase_refuses_a_bootstrap_warehouse(tmp_path: Path) -> None:
    run = _preflight(tmp_path, phase="data")

    _refused(run, "the data phase runs on this deployer's bundle warehouse only")
    assert run.calls == [["warehouse-id"]], "never checked"
    assert BOOT not in run.transcript


@pytest.mark.parametrize("phase", [None, "plan", "release", "data"])
def test_a_dry_run_never_checks_or_prints_the_bootstrap(tmp_path: Path, phase: str | None) -> None:
    run = _preflight(tmp_path, phase=phase, state_output=None, verifier=None, dry_run=1)

    _passed(run)
    assert run.calls == [], "no state read and no check"
    assert run.outcome == ("dry-run-bundle-warehouse", "dry-run (bundle state unread)", "dry-run-bundle-warehouse")
    assert "  bootstrap warehouse: configured (a live run proves it read-only; its id is never printed)\n" in (
        run.result.stdout
    )
    assert BOOT not in run.transcript


@pytest.mark.parametrize("dry_run", [0, 1], ids=["live", "dry-run"])
@pytest.mark.parametrize("phase", ["adopt", "unadopt"])
def test_the_adopt_phases_never_read_the_bootstrap(tmp_path: Path, phase: str, dry_run: int) -> None:
    run = _preflight(tmp_path, phase=phase, ambient=OTHER, dry_run=dry_run)

    _passed(run)
    assert run.calls == [] and run.controls == ["DATABRICKS_WAREHOUSE_ID"], "the profile field is not even read"
    assert run.outcome == ("", f"none (--phase {phase} runs no SQL)", "UNSET")
    assert "bootstrap warehouse" not in run.transcript and BOOT not in run.transcript


@pytest.mark.parametrize("phase", [None, "plan", "release", "data"])
def test_with_neither_the_refusal_keeps_its_words_and_names_the_profile_field(
    tmp_path: Path, phase: str | None
) -> None:
    run = _preflight(tmp_path, phase=phase, bootstrap="", verifier=None)

    _refused(run, "binds no SQL warehouse yet (a first install)")
    assert "set the client profile's MIP_BOOTSTRAP_WAREHOUSE_ID to a pro or serverless warehouse" in run.result.stderr
    assert run.calls == [["warehouse-id"]]
    # One text for every phase, so it also says which phases the field serves:
    # the data phase refuses it, and its remedy is the release.
    assert (
        "Only a plan or a release reads that field: the data phase runs on the bundle's own warehouse, "
        "which the first release creates (run --phase release first)."
    ) in run.result.stderr


def test_in_the_data_phase_the_field_the_refusal_names_would_be_refused_and_an_operators_value_is_not(
    tmp_path: Path,
) -> None:
    """Why the refusal names the phases: an operator who only set the field would meet a second refusal."""

    followed = _preflight(tmp_path / "field-set", phase="data")
    _refused(followed, "the data phase runs on this deployer's bundle warehouse only")

    # A local operator's own warehouse is no bootstrap of the profile's: that path is unchanged.
    local = _preflight(tmp_path / "operator-only", phase="data", bootstrap="", operator=OTHER, verifier=None)
    _passed(local)
    assert local.outcome == (OTHER, "first-install-bootstrap", OTHER) and local.calls == [["warehouse-id"]]


def test_the_bootstrap_functions_are_called_only_from_the_block_and_only_with_the_field_set() -> None:
    text = deploy_entrypoint_text()
    body = block(START, END)

    for name in FUNCTIONS:
        assert text.count(f"{name}() {{\n") == 1, name
        calls = [line.strip() for line in text.splitlines() if name in line and f"{name}() {{" not in line]
        assert calls and all(line in body for line in calls), (name, calls)
    assert (
        '  elif [[ -n "$_PROFILE_BOOTSTRAP_WAREHOUSE" ]]; then\n'
        '    check_bootstrap_warehouse "$_PROFILE_BOOTSTRAP_WAREHOUSE" "$_OPERATOR_WAREHOUSE_ID"\n'
        '    _GRANTS_WAREHOUSE_ID="$_PROFILE_BOOTSTRAP_WAREHOUSE" WAREHOUSE_SOURCE="first-install-bootstrap"\n'
    ) in body
    assert '  [[ -z "$_PROFILE_BOOTSTRAP_WAREHOUSE" ]] || note_bootstrap_warehouse\n' in body


# --- the docs say what the code does ---------------------------------------------------


def _flat(relative: str) -> str:
    return " ".join((Path(__file__).resolve().parents[2] / relative).read_text(encoding="utf-8").split())


def test_the_docs_no_longer_call_a_first_install_blocked_at_the_warehouse() -> None:
    onboarding = _flat("docs/clients/onboarding.md")
    assert "**No longer blocked by the warehouse** (B1 is fixed" in onboarding
    assert "blocked through CI today" not in onboarding, "section 5 once cited a Status row that says otherwise"
    assert "The warehouse preflight no longer stops it through CI (section 4" in onboarding
    assert "A `phase=data` dispatch before the first release refuses there with or without the field" in onboarding
    runbook = _flat("docs/clients/first-client-cutover.md")
    assert "| B1 (fixed: #344 and #348) |" in runbook
    assert "what remains of it blocks a new client's first install only" not in runbook


def test_the_deployment_guide_names_the_allowlist_the_masks_and_the_data_phases_remedy() -> None:
    guide = _flat("docs/deployment.md")
    assert "(an allowlist: any other phase refuses the field unchecked)" in guide
    assert "registering its id as a runner mask in the parse that reads it from the bundle summary" in guide
    assert "in the data phase the remedy is the first release" in guide
    assert "The new id is registered as a runner mask before the provisioner prints it" in guide
