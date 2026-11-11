"""The adopt and unadopt phases read no SQL warehouse (B1, wave 7 part 1).

Before adoption the deployer's bundle state binds no warehouse, and CI's
``.env.local`` carries no ``DATABRICKS_WAREHOUSE_ID`` (it is no client profile
field). The preflight's ``bundle warehouse`` block therefore refused every live
``--phase adopt|unadopt`` run with "binds no SQL warehouse yet (a first
install)", although neither phase runs SQL. The block now gives those two
phases no warehouse at all: it makes no bundle state call, leaves
``_GRANTS_WAREHOUSE_ID`` empty, unsets ``DATABRICKS_WAREHOUSE_ID`` (an ambient
export included) and reports ``none (--phase <phase> runs no SQL)``. Every
other phase runs the block exactly as before.

What this file pins:

* The block, driven in the harness: both adopt phases, live and dry, read no
  state and leave no warehouse. Every other phase behaves byte for byte like a
  run with no phase.
* The reading, mechanised. No line from the block to the end of the adopt
  apply names a warehouse, and neither does any function those lines reach,
  except through the pre-lease gate, which the adopt phases skip. The EXIT trap
  reaches a warehouse only through compensations whose arming flags nothing on
  that path sets. The Genie pin plan returns before its tool reads a warehouse.
* The wave-7 spec's structural guard: no Python tool the adopt path runs or
  imports, and no adopt helper the spec names, names a warehouse input
  (``git grep -nP``), apart from pinned lines the path never reaches.
* A live adopt and unadopt run in the release sandbox, from the first line to
  the apply's exit, with no warehouse anywhere: CI's shape.
* Fail closed if a step ever did need one: step 0a's empty-warehouse refusal,
  and the earlier one under the lease, refuse what the adopt block leaves; and
  a dry adopt run whose plan exit is excised stops at step 0a's refusal.
"""

from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures.client_profiles import core_tier_profile
from tests.fixtures.deploy_block_harness import block, code, drive, stub_tool
from tests.fixtures.deploy_script import DEPLOY_REVIEWED_LIBRARIES, deploy_entrypoint_text
from tests.fixtures.release_sandbox import Release, core_client_ids, first_install_estate, release
from tests.fixtures.release_sandbox_python import LEASE_ID

REPO = Path(__file__).resolve().parents[2]
START, END = "# >>> bundle warehouse >>>", "# <<< bundle warehouse <<<"
# The adopt phases' last line: the apply ends the run (both functions exit on
# every path, tests/unit/test_adopt_phase_deploy_contract.py).
APPLY_END = "# <<< adopt phase: apply <<<"
ADOPT_PHASES = ("adopt", "unadopt")
WAREHOUSE = "a0b1c2d3e4f5a6b7"
OPERATOR = "operator-warehouse"
AMBIENT = "ambient-warehouse"
ACK = "a" * 64
ACKS = {"MIP_ADOPT_PLAN_ACK": ACK, "MIP_ACK_ADOPT_LIVE": ACK, "MIP_ACK_UNADOPT": ACK}
# Broad on purpose: any warehouse id, flag, variable or source on the adopt
# path is a review question, not a pass.
NAMES_A_WAREHOUSE = re.compile(r"(?i)warehouse")
FUNCTION = re.compile(r"^([A-Za-z_]\w*)\(\) \{\n(.*?)^\}\n", re.M | re.S)


def _block(
    repo: Path,
    *,
    phase: str | None,
    state: str | None,
    operator: str = "",
    ambient: str = "",
    dry_run: int = 0,
) -> tuple[subprocess.CompletedProcess[str], bool]:
    """The real block with a stub bundle state (``None``: unreadable); (result, state read)."""

    repo.mkdir(parents=True, exist_ok=True)
    body = "raise SystemExit(2)" if state is None else f"print({state!r}, end='')"
    called = stub_tool(repo, "bundle_state", argv=["warehouse-id"], body=body)
    shell = (
        "deployment_control_value() { "
        f"[[ \"$1\" == DATABRICKS_WAREHOUSE_ID ]] && printf '%s' {operator!r} || printf ''; }}\n"
        + (f"export DATABRICKS_WAREHOUSE_ID={shlex.quote(ambient)}\n" if ambient else "")
        + block(START, END)
    )
    result = drive(
        repo,
        shell,
        dry_run=dry_run,
        env={} if phase is None else {"DEPLOY_PHASE": phase},
        epilogue=(
            'echo "warehouse=${_GRANTS_WAREHOUSE_ID} source=${WAREHOUSE_SOURCE}'
            ' exported=$(printenv DATABRICKS_WAREHOUSE_ID || echo UNSET)"\n'
        ),
    )
    return result, called.exists()


# --- the block, driven ------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "operator", "ambient"),
    [("", "", ""), (WAREHOUSE, OPERATOR, AMBIENT), (None, WAREHOUSE, AMBIENT)],
    ids=["ci-shaped", "state-operator-and-ambient", "unreadable-state"],
)
@pytest.mark.parametrize("dry_run", [0, 1], ids=["live", "dry-run"])
@pytest.mark.parametrize("phase", ADOPT_PHASES)
def test_the_adopt_phases_read_no_state_and_leave_no_warehouse(
    tmp_path: Path, phase: str, dry_run: int, state: str | None, operator: str, ambient: str
) -> None:
    result, read_state = _block(
        tmp_path, phase=phase, state=state, operator=operator, ambient=ambient, dry_run=dry_run
    )

    assert result.returncode == 0, result.stderr
    source = f"none (--phase {phase} runs no SQL)"
    assert f"  sql warehouse: {source}\n" in result.stdout
    # Unset, not exported empty: a child sees no ambient value either.
    assert f"warehouse= source={source} exported=UNSET\n" in result.stdout
    assert not read_state, "the adopt phases read no bundle state for a warehouse"
    for value in (WAREHOUSE, OPERATOR, AMBIENT):
        assert value not in result.stdout + result.stderr
    assert "reached-after-block" in result.stdout


# Each case the no-phase pins (tests/unit/test_bundle_state_warehouse.py) cover, plus an ambient export.
OTHER_PHASE_CASES: dict[str, dict[str, Any]] = {
    "state-bound": {"state": WAREHOUSE},
    "operator-agrees": {"state": WAREHOUSE, "operator": WAREHOUSE},
    "operator-differs": {"state": WAREHOUSE, "operator": OPERATOR},
    "first-install-operator": {"state": "", "operator": OPERATOR},
    "first-install-neither": {"state": ""},
    "unreadable-state": {"state": None, "operator": WAREHOUSE},
    "ambient-export": {"state": WAREHOUSE, "ambient": AMBIENT},
    "dry-run": {"state": WAREHOUSE, "dry_run": 1},
}


@pytest.mark.parametrize("case", OTHER_PHASE_CASES)
@pytest.mark.parametrize("phase", ["plan", "release", "data"])
def test_every_other_phase_runs_the_block_exactly_as_a_run_with_no_phase(
    tmp_path: Path, phase: str, case: str
) -> None:
    def view(
        result: subprocess.CompletedProcess[str], read_state: bool
    ) -> tuple[int, str, str, bool]:
        return result.returncode, result.stdout, result.stderr, read_state

    baseline = view(*_block(tmp_path / "no-phase", phase=None, **OTHER_PHASE_CASES[case]))
    phased = view(*_block(tmp_path / phase, phase=phase, **OTHER_PHASE_CASES[case]))

    assert phased == baseline
    assert "runs no SQL" not in baseline[1]


# --- the reading, mechanised ---------------------------------------------------------


def _functions() -> dict[str, str]:
    """Every shell function of the command of record, comments stripped, by name."""

    text = deploy_entrypoint_text() + "".join(
        path.read_text(encoding="utf-8") for path in DEPLOY_REVIEWED_LIBRARIES
    )
    found = {match.group(1): code(match.group(2)) for match in FUNCTION.finditer(text)}
    assert len(found) == len(
        re.findall(r"^[A-Za-z_]\w*\(\) \{$", text, re.M)
    ), "each definition once"
    return found


def _reached(
    functions: dict[str, str], text: str, *, skip: frozenset[str] = frozenset()
) -> set[str]:
    """Every function ``text`` can call, transitively, never entering one in ``skip``."""

    calls = re.compile(r"\b(" + "|".join(sorted(functions, key=len, reverse=True)) + r")\b")
    seen: set[str] = set()
    stack = [name for name in calls.findall(text) if name not in skip]
    while stack:
        name = stack.pop()
        if name not in seen:
            seen.add(name)
            stack.extend(callee for callee in calls.findall(functions[name]) if callee not in skip)
    return seen


def _readers(functions: dict[str, str], names: set[str]) -> list[str]:
    return sorted(name for name in names if NAMES_A_WAREHOUSE.search(functions[name]))


def _adopt_path() -> str:
    """The top-level lines from the block's end to the adopt apply's end, comments stripped."""

    text = deploy_entrypoint_text()
    path = text[text.index(END) : text.index(APPLY_END)]
    assert not re.search(r"^[A-Za-z_]\w*\(\) \{$", path, re.M), "the path defines no function"
    return code(path)


# The pre-lease gate's guard: the adopt phases skip it (and its catalog bootstrap
# skip rule, the one warehouse reader behind it).
GATE_SKIP = (
    'if [[ "${DEPLOY_PHASE:-}" != "adopt" && "${DEPLOY_PHASE:-}" != "unadopt" && \\\n'
    '      "${DEPLOY_PHASE:-}" != "data" ]]; then\n'
    "  run_bundle_plan_gate pre-lease\nfi\n"
)


def test_nothing_from_the_block_to_the_adopt_exits_names_a_warehouse() -> None:
    functions, path = _functions(), _adopt_path()

    assert [line for line in path.splitlines() if NAMES_A_WAREHOUSE.search(line)] == []
    assert GATE_SKIP in path and path.count("run_bundle_plan_gate") == 1
    reached = _reached(functions, path, skip=frozenset({"run_bundle_plan_gate"}))
    # Positive control: the walk enters the adopt functions and adopt-live's.
    assert {
        "run_adopt_phase_plan",
        "run_adopt_live_plan",
        "run_adopt_phase_apply",
        "run_adopt_live_apply",
    } <= reached
    assert _readers(functions, reached) == []
    # Positive control: without the skip the same walk finds the gate's reader.
    assert "check_catalog_bootstrap_skip_rule" in _readers(functions, _reached(functions, path))


# The EXIT trap's compensations that reach a warehouse, each with the flag that
# arms it: each returns 0 first unless its flag is 1.
ARMED_BY = {
    "stop_app_after_failed_deploy": "APP_FAIL_CLOSED_ARMED",
    "compensate_preactivation_app_acl": "PREACTIVATION_APP_ACL_MUTATED",
    "compensate_agent_proxy_access": "AGENT_PROXY_ACCESS_MUTATED",
    "compensate_verifier_gateway_access": "VERIFIER_GATEWAY_CUTOVER_MUTATED",
    "revoke_agent_runtime_bootstrap_grants": "AGENT_RUNTIME_BOOTSTRAP_GRANTS_ACTIVE",
}
# The captured-green retirement runs only in that upgrade state.
CAPTURED_RETIREMENT = "complete_captured_runtime_retirement_journal"
CAPTURED_STATE = 'APP_UPGRADE_STATE="green_captured_cleanup_pending"'
# The secondary treatment quiescence runs only after an armed stop failed.
SECONDARY_QUIESCE = "quiesce_app_treatment_after_failed_stop"
TRAP_STOP = (
    "    if stop_app_after_failed_deploy; then\n"
    "      app_stopped=1\n"
    "    else\n"
    "      compensation_failed=1\n"
    f"      if ! {SECONDARY_QUIESCE}; then\n"
)
TRAPS = (
    "trap restore_rendered_sql_fail_closed EXIT",
    "trap quiesce_window_deadline_reached USR1",
    "trap on_error ERR",
)


def _guard(body: str) -> str:
    """A function's first statement, when it is ``[[ ... ]] || return 0``, joined across continuations."""

    head = body[: body.index("]] || return 0")].replace("\\\n", " ")
    assert "\n" not in head.strip(), "the guard is the first statement"
    return " ".join(head.split())


def test_the_exit_trap_reaches_a_warehouse_only_through_compensations_the_adopt_path_never_arms() -> (
    None
):
    functions, text = _functions(), deploy_entrypoint_text()
    for trap in TRAPS:
        assert trap in text, trap
    roots = " ".join(trap.split()[1] for trap in TRAPS)

    for name, flag in ARMED_BY.items():
        assert _guard(functions[name]).startswith(f'[[ "$DRY_RUN" -eq 0 && "${flag}" -eq 1'), name
    assert (
        _guard(functions[CAPTURED_RETIREMENT])
        == '[[ "${APP_UPGRADE_STATE:-first_install}" == "green_captured_cleanup_pending"'
    )
    trap_body = functions["restore_rendered_sql_fail_closed"]
    assert TRAP_STOP in trap_body and trap_body.count(SECONDARY_QUIESCE) == 1
    # ...and nothing else calls it, so skipping it below skips that one call.
    assert [name for name, body in functions.items() if SECONDARY_QUIESCE in body] == [
        "restore_rendered_sql_fail_closed"
    ]
    assert SECONDARY_QUIESCE not in code(FUNCTION.sub("", text))
    guarded = frozenset({*ARMED_BY, CAPTURED_RETIREMENT, SECONDARY_QUIESCE})
    assert _readers(functions, _reached(functions, roots, skip=guarded)) == []
    assert _readers(
        functions, _reached(functions, roots)
    ), "positive control: the guarded ones do reach a warehouse"

    # Nothing up to the adopt apply's end arms one: its top-level lines set each
    # flag to 0 only, and no function they can call sets one to 1.
    top = code(FUNCTION.sub("", text[: text.index(APPLY_END)]))
    reached = _reached(functions, top)
    for flag in ARMED_BY.values():
        assert set(re.findall(rf"(?<![\w$]){flag}=(\S*)", top)) == {"0"}, flag
        assert [
            name for name in reached if re.search(rf"(?<![\w$]){flag}=1\b", functions[name])
        ] == [], flag
        assert any(
            re.search(rf"(?<![\w$]){flag}=1\b", body) for body in functions.values()
        ) or re.search(
            rf"(?<![\w$]){flag}=1\b", text[text.index(APPLY_END) :]
        ), f"positive control: something past the adopt exits arms {flag}"
    assert (
        CAPTURED_STATE not in top
        and [name for name in reached if CAPTURED_STATE in functions[name]] == []
    )


def test_the_genie_pin_plan_returns_before_its_tool_reads_a_warehouse() -> None:
    path = _adopt_path()
    source = (REPO / "tools" / "databricks" / "provision_genie_space.py").read_text(
        encoding="utf-8"
    )

    assert re.findall(r"-m tools\.databricks\.provision_genie_space (\S+)", path) == ["--plan-pin"]
    plan_pin_return = source.index(
        '        print("  read-only pin plan: nothing was changed")\n        return 0\n'
    )
    assert plan_pin_return < source.index("args.warehouse_id")


# The wave-7 spec's structural guard on the Python the adopt path runs. The live
# sandbox run below cannot stand in for it: tests/fixtures/release_sandbox_python.py
# answers these tools without running their code. -P, not -E: an -E word boundary
# matches nothing in git grep on macOS, so -E would pass vacuously.
WAREHOUSE_INPUT = r"(_GRANTS_WAREHOUSE_ID|DATABRICKS_WAREHOUSE_ID|--warehouse-id|\bwarehouse_id\b)"
# A tool the path runs (-m) or imports into an inline interpreter, and a script it
# runs by path.
TOOL_MODULE = re.compile(r"-m[\s\\]+(tools(?:\.\w+)+)|(?m:^\s*(?:from|import)\s+(tools(?:\.\w+)+))")
TOOL_SCRIPT = re.compile(r"\btools/[\w/]+\.py\b")
# Named by the spec, because no line on the path names them as a tool: the adopt
# lifecycle library, the adopt helpers the path's tools import, and the
# preflight's live and install checks.
NAMED_SOURCES = (
    "scripts/lib/deploy_adopt_phase_lifecycle.sh",
    "tools/databricks/adopt_live_facts.py",
    "tools/databricks/adopted_source_copy.py",
    "tools/databricks/genie_space_pin.py",
    "tools/clients/live_checks.py",
    "tools/clients/install_checks.py",
)
NAMED_GLOB = "tools/databricks/prestop_*.py"
# The two sources on the path that do name one. Each such line is pinned up to
# where the path stops reading the file, so a new one is a review question.
# provision_genie_space runs with --plan-pin only, which returns before it uses a
# warehouse (the test above). Up to that return it names one in its docstring,
# the --warehouse-id argument's definition and the dry-run plan text. The path
# imports two validators from app_deploy_payload; that file's one line is the
# App payload's env binding, which nothing on the path builds.
REVIEWED_LINES: dict[str, tuple[str | None, tuple[str, ...]]] = {
    "tools/databricks/provision_genie_space.py": (
        '        print("  read-only pin plan: nothing was changed")\n        return 0\n',
        (
            "* Warehouse ID comes from ``--warehouse-id`` (scripts/deploy.sh passes the",
            "warehouse its bundle state binds) or ``DATABRICKS_WAREHOUSE_ID``.",
            '"--warehouse-id",',
            'default=os.environ.get("DATABRICKS_WAREHOUSE_ID"),',
            'print("      warehouse_id = --warehouse-id (the bundle\'s warehouse)")',
        ),
    ),
    "tools/databricks/app_deploy_payload.py": (
        None,
        ('{"name": "DATABRICKS_WAREHOUSE_ID", "value_from": "sql_warehouse"},',),
    ),
}
PAYLOAD_IMPORTS = ["salesforce_switch_refusal", "validated_otel_endpoint"]


def _module_source(module: str) -> str:
    base = REPO.joinpath(*module.split("."))
    for source in (base.with_suffix(".py"), base / "__main__.py"):
        if source.is_file():
            return source.relative_to(REPO).as_posix()
    raise AssertionError(f"{module} names no source in the repo")


def _adopt_path_tools() -> tuple[set[str], str]:
    """Every Python source the adopt path runs or imports, and the text that names them."""

    functions, path = _functions(), _adopt_path()
    reached = _reached(functions, path, skip=frozenset({"run_bundle_plan_gate"}))
    text = path + "".join(functions[name] for name in sorted(reached))
    modules = {run or imported for run, imported in TOOL_MODULE.findall(text)}
    scripts = set(TOOL_SCRIPT.findall(text))
    # Each tools name on the path is one the patterns read: none is missed.
    assert set(re.findall(r"\btools(?:\.\w+)+", text)) == modules
    assert set(re.findall(r"\btools/[\w/.]+", text)) == scripts
    for script in scripts:
        assert (REPO / script).is_file(), script
    return {_module_source(module) for module in modules} | scripts, text


def _warehouse_inputs(sources: set[str]) -> list[tuple[str, int, str]]:
    """``git grep -nP`` for a warehouse input over ``sources``: (path, line, stripped text)."""

    result = subprocess.run(
        [
            "git",
            "grep",
            "--untracked",
            "--no-color",
            "-nP",
            WAREHOUSE_INPUT,
            "--",
            *sorted(sources),
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode in (0, 1), result.stderr  # 1: no match; anything else fails closed
    found = []
    for line in result.stdout.splitlines():
        path, number, text = line.split(":", 2)
        found.append((path, int(number), text.strip()))
    return found


def test_no_python_tool_on_the_adopt_path_names_a_warehouse_input() -> None:
    derived, text = _adopt_path_tools()
    # Positive control: the walk finds the tools the sandbox answers without running them.
    assert {
        "tools/databricks/install_state_adopt.py",
        "tools/databricks/adopt_live.py",
        "tools/databricks/deploy_prestop_probes.py",
        "tools/databricks/pii_salt_scope.py",
        "tools/databricks/provision_runtime_secrets.py",
        "tools/databricks/provision_genie_space.py",
        "tools/databricks/app_deploy_payload.py",
    } <= derived
    imported = re.findall(r"from tools\.databricks\.app_deploy_payload import (\w+)", text)
    assert sorted(set(imported)) == PAYLOAD_IMPORTS
    runs = {run for run, _ in TOOL_MODULE.findall(text) if run}
    assert "tools.databricks.app_deploy_payload" not in runs, "imported, never run"
    named = {path.relative_to(REPO).as_posix() for path in REPO.glob(NAMED_GLOB)}
    assert len(named) >= 9, NAMED_GLOB
    for source in NAMED_SOURCES:
        assert (REPO / source).is_file(), source
    sources = derived | named | set(NAMED_SOURCES)

    found = _warehouse_inputs(sources)

    for reviewed, (cut, lines) in REVIEWED_LINES.items():
        assert reviewed in sources, reviewed
        source = (REPO / reviewed).read_text(encoding="utf-8")
        end = (
            len(source.splitlines())
            if cut is None
            else source[: source.index(cut) + len(cut)].count("\n")
        )
        # Also the positive control: -P finds every one of these lines.
        assert [line for path, number, line in found if path == reviewed and number <= end] == list(
            lines
        )
    assert [hit for hit in found if hit[0] not in REVIEWED_LINES] == []
    # Positive control: the word-bounded alternative matches on its own (past the
    # Genie tool's cut), which an -E word boundary on macOS would not.
    assert [
        line
        for _, _, line in found
        if not re.search(r"_GRANTS_WAREHOUSE_ID|DATABRICKS_WAREHOUSE_ID|--warehouse-id", line)
    ]
    # Positive control: the same grep finds the signed blue proof's warehouse.
    assert _warehouse_inputs({"scripts/lib/deploy_step_signed_blue_proof.sh"})


# --- a live adopt phase, end to end, offline -----------------------------------------


def _adopt_run(tmp_path: Path, **kwargs: Any) -> Release:
    return release(
        tmp_path,
        core_tier_profile(),
        first_install_estate(),
        client_ids=core_client_ids(),
        operator_warehouse=None,
        **kwargs,
    )


def _after_the_blocks_read(run: Release) -> list[list[str]]:
    """The argv of every interpreter call after the block's own read of the operator control."""

    argvs = [call["argv"] for call in run.python_calls]
    assert argvs.count(["-", "DATABRICKS_WAREHOUSE_ID"]) == 1
    return argvs[argvs.index(["-", "DATABRICKS_WAREHOUSE_ID"]) + 1 :]


@pytest.mark.parametrize("phase", ADOPT_PHASES)
def test_a_live_adopt_phase_runs_to_its_exit_with_no_warehouse_anywhere(
    tmp_path: Path, phase: str
) -> None:
    """CI's shape (B1): no warehouse in ``.env.local``, the shell or the deployer's state.

    The sandbox's adopt tools answer as their own suites prove; this runs the
    deploy script's adopt path for real, under the lease, to the apply's exit.
    """

    run = _adopt_run(tmp_path, flags=("--phase", phase), env={"GITHUB_ACTIONS": "true", **ACKS})
    out = run.result.stdout

    assert run.result.returncode == 0, out[-4000:] + run.result.stderr[-4000:]
    assert f"  sql warehouse: none (--phase {phase} runs no SQL)\n" in out
    assert f"[deploy] {phase} phase complete: bundle state edited exactly as acknowledged" in out
    modules = run.modules()
    assert ["tools.databricks.install_state_adopt", "apply", "--phase", phase] in [
        module[:4] for module in modules
    ]
    assert ["tools.databricks.adopt_live", "apply"] in [module[:2] for module in modules]
    assert not any(module[0] == "tools.databricks.bundle_state" for module in modules)
    assert [
        argv for argv in _after_the_blocks_read(run) if NAMES_A_WAREHOUSE.search(" ".join(argv))
    ] == []
    assert [call["argv"] for call in run.cli_calls] == [["--version"]]
    assert run.state["lease_released"] == LEASE_ID, "the EXIT trap released the lease"


# --- fail closed, if a step ever did need one -------------------------------------------


STEP_0A_REFUSAL = (
    'if [[ -z "${_GRANTS_WAREHOUSE_ID:-}" ]]; then\n'
    '  echo "${RED}[deploy] no SQL warehouse was resolved — cannot govern treatment access.${RST}" >&2\n'
    "  exit 4\n"
    "fi\n"
)
# Under the lease, before step 0a: the first warehouse reader past the apply's exit.
LEASE_REFUSAL = (
    '  if [[ -z "$_GRANTS_WAREHOUSE_ID" ]]; then\n'
    '    echo "${RED}[deploy] the preflight resolved no SQL warehouse for early privilege reconciliation.${RST}" >&2\n'
    "    exit 1\n"
    "  fi\n"
)


@pytest.mark.parametrize(
    ("refusal", "rc", "dry_run"),
    [(STEP_0A_REFUSAL, 4, 0), (STEP_0A_REFUSAL, 4, 1), (LEASE_REFUSAL, 1, 0)],
    ids=["step-0a-live", "step-0a-dry-run", "under-the-lease"],
)
@pytest.mark.parametrize("phase", ADOPT_PHASES)
def test_what_the_adopt_block_leaves_meets_an_empty_warehouse_refusal(
    tmp_path: Path, phase: str, refusal: str, rc: int, dry_run: int
) -> None:
    text = deploy_entrypoint_text()
    assert text.count(refusal) == 1 and text.index(APPLY_END) < text.index(refusal)
    stub_tool(tmp_path, "bundle_state", argv=["warehouse-id"], body=f"print({WAREHOUSE!r}, end='')")
    shell = (
        f"deployment_control_value() {{ printf '%s' {OPERATOR!r}; }}\n"
        f"export DATABRICKS_WAREHOUSE_ID={AMBIENT}\n" + block(START, END) + "\n" + refusal
    )

    result = drive(tmp_path, shell, dry_run=dry_run, env={"DEPLOY_PHASE": phase}, epilogue="")

    assert result.returncode == rc, result.stdout + result.stderr
    assert refusal.split("[deploy] ")[1].split("${RST}")[0] in result.stderr
    assert "reached-after-block" not in result.stdout


def test_a_dry_adopt_run_past_its_plan_exit_stops_at_step_0as_refusal(tmp_path: Path) -> None:
    run = _adopt_run(tmp_path, excise="adopt phase: plan", flags=("--phase", "adopt", "--dry-run"))

    assert run.result.returncode == 4, run.result.stdout[-4000:] + run.result.stderr[-4000:]
    assert "  sql warehouse: none (--phase adopt runs no SQL)\n" in run.result.stdout
    assert (
        "[deploy] no SQL warehouse was resolved — cannot govern treatment access."
        in run.result.stderr
    )
    assert [
        argv for argv in _after_the_blocks_read(run) if NAMES_A_WAREHOUSE.search(" ".join(argv))
    ] == []
    assert [call["argv"] for call in run.cli_calls] == [["--version"]]
