"""ci.yml runs the second-client portability proof once per push, in its own job (M3-PR4).

The proof (tests/unit/test_client_portability_proof.py, with its in-process
half tests/unit/test_client_portability_integrity.py) replays three
deploy-dev.yml dispatches offline, so it has a job of its own,
``client-portability-proof``, and backend-tests ignores both modules instead
of running them a second time. This module lives outside the proof so that it
keeps running in backend-tests when the proof's job is edited away: deleting
the job, renaming the module or letting backend-tests drop it without the job
taking it fails here. So does switching the job off while it still exists: a
job or step ``if``, a ``continue-on-error``, a ``needs`` that can skip it, an
environment that can change what pytest collects, or a pytest command that
deselects, stops early or swallows the exit status (review of #330).

The job's steps are pinned whole, not by their keys (round 2 of that
review): a step that writes ``$GITHUB_ENV`` (``PYTEST_ADDOPTS=--collect-only``
for the proof step), rewrites the proof module or checks out another ref
changes the pinned list. So does a trigger filter (``paths``,
``paths-ignore``, ``branches-ignore``) that would skip the workflow, a
workflow-wide ``defaults`` shell, and a workflow-wide variable that changes
what the shell or pytest runs.

The pins read GitHub's semantics, not the file's spelling (round 3): a push
must trigger on ``main`` itself, from a branches list no negation that could
match ``main`` follows, and the shell's variables are refused by their exact
names. So an edit that keeps the proof running passes: a ``run-name``, an
explicit pull_request ``types`` list that keeps GitHub's three defaults, push
``tags`` beside the branches, or a workflow variable such as
``ENVIRONMENT_LABEL``. Each pin is a function of the parsed workflow, and the
two matrices at the end hold every refused route red and every accepted edit
green.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CI = ROOT / ".github" / "workflows" / "ci.yml"
PROOF = ("tests/unit/test_client_portability_integrity.py", "tests/unit/test_client_portability_proof.py")
JOB = "client-portability-proof"
# The job's keys and each step's: an allowlist, so a key that could skip the
# job, a step or the failure (``if``, ``continue-on-error``, ``needs``,
# ``env``, ``strategy``) fails here instead of passing unnoticed.
JOB_KEYS = {"name", "runs-on", "timeout-minutes", "permissions", "steps"}
STEP_KEYS = {"name", "uses", "with", "run"}
# The proof's pytest command, token for token: no -k, --deselect, -x, --lf or
# shell operator, so every collected test runs and its status is the step's.
PYTEST = ("pytest", "-rfE", "-p", "no:cacheprovider", "--durations=10", *PROOF)
# The job's steps, whole: what each one runs and how (review of #330, round 2).
STEPS = [
    {"uses": "actions/checkout@v6"},
    {"uses": "actions/setup-python@v6", "with": {"python-version": "${{ env.PYTHON_VERSION }}", "cache": "pip"}},
    {"name": "Install dependencies", "run": "python -m pip install --upgrade pip\npip install -r requirements-dev.txt\n"},
    {"name": "pytest (the portability proof)", "run": " ".join(PYTEST)},
]
# Workflow-wide variables that change what a run step's shell or pytest runs
# (round 3): the shell's own by exact name, so ENVIRONMENT_LABEL or
# PATHS_TO_LINT is no match; and by prefix the exported shell functions (a
# BASH_FUNC_pytest%% replaces the command), the dynamic loader, Python and
# pytest, past a reviewed few that change neither what pytest collects nor
# how it exits.
SHELL_ENV = frozenset({"ENV", "BASH_ENV", "BASHOPTS", "SHELLOPTS", "PATH"})
ENV_PREFIXES = ("BASH_FUNC_", "LD_", "DYLD_", "PYTHON", "PYTEST")
REVIEWED_ENV = frozenset({"PYTHON_VERSION", "PYTHONUNBUFFERED", "PYTHONDONTWRITEBYTECODE", "PYTHONIOENCODING"})
# The workflow's top-level keys: ``defaults`` (a workflow-wide shell) is not one.
WORKFLOW_KEYS = frozenset({"name", "run-name", "on", True, "concurrency", "env", "permissions", "jobs"})
# GitHub's default pull_request activity types: an explicit list keeps all three.
PR_TYPES = frozenset({"opened", "synchronize", "reopened"})


def _workflow() -> dict[str, Any]:
    return dict(yaml.safe_load(CI.read_text(encoding="utf-8")))


def _jobs() -> dict[str, Any]:
    return dict(_workflow()["jobs"])


def _runs(job: dict[str, Any]) -> list[str]:
    return [str(step.get("run", "")) for step in job["steps"]]


def _triggers(workflow: dict[Any, Any]) -> Any:
    # PyYAML reads the bare key ``on`` as the boolean True.
    return workflow.get("on", workflow.get(True))


# --- the pins, each on a parsed workflow ---------------------------------------------------


def _job_cannot_be_switched_off(workflow: dict[str, Any]) -> None:
    """Review of #330: the job and its steps carry no key that skips them, and pytest runs whole."""

    job = workflow["jobs"][JOB]
    assert set(job) == JOB_KEYS, sorted(set(job) ^ JOB_KEYS)
    assert 0 < int(job["timeout-minutes"]) <= 60
    for step in job["steps"]:
        assert set(step) <= STEP_KEYS, (step.get("name"), sorted(set(step) - STEP_KEYS))
    (pytest_run,) = [run for run in _runs(job) if run.lstrip().startswith("pytest")]
    assert tuple(pytest_run.split()) == PYTEST


def _steps_are_pinned_whole(workflow: dict[str, Any]) -> None:
    """Review of #330, round 2: an allowlisted step key can still carry a step that switches the proof off."""

    assert workflow["jobs"][JOB]["steps"] == STEPS


def _reshapes(name: str) -> bool:
    return name in SHELL_ENV or (name.startswith(ENV_PREFIXES) and name not in REVIEWED_ENV)


def _env_cannot_reshape_the_run(workflow: dict[str, Any]) -> None:
    """Nothing workflow-wide reshapes what pytest collects or how it exits."""

    assert [name for name in workflow.get("env") or {} if _reshapes(str(name))] == []


def _could_match(pattern: str, ref: str) -> bool:
    """Whether a GitHub branch filter pattern can match ``ref`` (``*``, ``**``, ``?``, ``+``, ``[...]``, ``\\``).

    A pattern this reading cannot compile could match anything: fail closed.
    """

    out: list[str] = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if pattern.startswith("**", index):
            out.append(".*")
            index += 1
        elif char == "*":
            out.append("[^/]*")
        elif char in "?+":
            out.append(char)  # zero-or-one / one-or-more of the character before it
        elif char == "[" and (end := pattern.find("]", index + 1)) > index:
            out.append(pattern[index : end + 1])
            index = end
        elif char == "\\" and index + 1 < len(pattern):
            out.append(re.escape(pattern[index + 1]))
            index += 1
        else:
            out.append(re.escape(char))
        index += 1
    try:
        return re.fullmatch("".join(out), ref) is not None
    except re.error:
        return True


def _pull_request_runs_on_every_pr(trigger: Any) -> bool:
    """No filter, or a ``types`` list that keeps GitHub's three default activity types."""

    if trigger in (None, {}):
        return True
    if not isinstance(trigger, dict) or set(trigger) != {"types"} or not isinstance(trigger["types"], list):
        return False
    return set(trigger["types"]) >= PR_TYPES


def _push_runs_on_main(trigger: Any) -> bool:
    """A ``branches`` list holding ``main`` itself, which no negation that could match ``main`` follows.

    GitHub excludes a ref that a later negative pattern matches, and a
    scalar ``branches`` is one pattern, not a list ``in`` can search (round
    3). ``tags`` may sit beside it: a branch push still matches the branches.
    """

    if not isinstance(trigger, dict) or "branches" not in trigger or not set(trigger) <= {"branches", "tags"}:
        return False
    branches = trigger["branches"]
    if not isinstance(branches, list) or not all(isinstance(branch, str) for branch in branches) or "main" not in branches:
        return False
    return not any(branch.startswith("!") and _could_match(branch[1:], "main") for branch in branches)


def _triggers_run_it(workflow: dict[str, Any]) -> None:
    """No trigger filter or workflow-wide shell can skip or hollow out the proof job (review of #330, round 2)."""

    assert set(workflow) <= WORKFLOW_KEYS, sorted(map(str, set(workflow) - WORKFLOW_KEYS))
    triggers = _triggers(workflow)
    assert _pull_request_runs_on_every_pr(triggers["pull_request"]), triggers["pull_request"]
    assert _push_runs_on_main(triggers["push"]), triggers["push"]


PINS: tuple[Callable[[dict[str, Any]], None], ...] = (
    _job_cannot_be_switched_off,
    _steps_are_pinned_whole,
    _env_cannot_reshape_the_run,
    _triggers_run_it,
)


def _refusals(workflow: dict[str, Any]) -> list[str]:
    """The pins a workflow fails."""

    failed = []
    for pin in PINS:
        try:
            pin(workflow)
        except (AssertionError, KeyError, TypeError, ValueError):
            failed.append(pin.__name__)
    return failed


# --- the committed ci.yml --------------------------------------------------------------------


def test_the_proof_job_runs_exactly_the_proof_modules_with_the_dev_set() -> None:
    job = _jobs()[JOB]
    runs = _runs(job)

    assert all((ROOT / module).is_file() for module in PROOF)
    assert job["runs-on"] == "ubuntu-latest" and job["permissions"] == {"contents": "read"}
    assert any("pip install -r requirements-dev.txt" in run for run in runs)
    pytest_runs = [run for run in runs if run.lstrip().startswith("pytest")]
    assert len(pytest_runs) == 1
    assert tuple(token for token in pytest_runs[0].split() if token.startswith("tests/")) == PROOF
    assert "-rfE" in pytest_runs[0].split()
    # Offline: no secret, no OIDC token, no environment.
    assert "secrets." not in yaml.safe_dump(job) and "id-token" not in job.get("permissions", {})
    assert "environment" not in job


def test_backend_tests_ignore_the_proof_modules_and_nothing_else() -> None:
    runs = _runs(_jobs()["backend-tests"])
    parallel = [run for run in runs if "--cov=backend" in run]

    assert len(parallel) == 1
    ignored = [token.removeprefix("--ignore=") for token in parallel[0].split() if token.startswith("--ignore")]
    assert tuple(ignored) == PROOF


def test_the_proof_job_cannot_be_switched_off_while_it_stays_in_the_file() -> None:
    """Review of #330: the pin above held with ``if: false`` and ``continue-on-error: true`` planted."""

    _job_cannot_be_switched_off(_workflow())
    _env_cannot_reshape_the_run(_workflow())


def test_the_proof_jobs_steps_are_pinned_whole() -> None:
    _steps_are_pinned_whole(_workflow())


def test_the_workflow_runs_on_every_pull_request_and_main_push() -> None:
    _triggers_run_it(_workflow())


# --- the routes each pin refuses, and the edits it lets through -----------------------------


def _set(path: tuple[Any, ...], value: Any) -> Callable[[dict[str, Any]], None]:
    """A mutation: ``value`` at ``path`` (``"on"`` reads PyYAML's True key)."""

    def mutate(workflow: dict[str, Any]) -> None:
        node: Any = workflow
        for key in path[:-1]:
            node = node[True if key == "on" and True in node else key]
        node[path[-1]] = value

    return mutate


def _insert_step(step: dict[str, Any]) -> Callable[[dict[str, Any]], None]:
    return lambda workflow: workflow["jobs"][JOB]["steps"].insert(3, step)


def _edit_run(index: int, suffix: str) -> Callable[[dict[str, Any]], None]:
    def mutate(workflow: dict[str, Any]) -> None:
        workflow["jobs"][JOB]["steps"][index]["run"] += suffix

    return mutate


def _env(name: str) -> Callable[[dict[str, Any]], None]:
    def mutate(workflow: dict[str, Any]) -> None:
        workflow.setdefault("env", {})[name] = "1"

    return mutate


def _push_branches(branches: Any) -> Callable[[dict[str, Any]], None]:
    return _set(("on", "push", "branches"), branches)


_COLLECT_ONLY = 'echo "PYTEST_ADDOPTS=--collect-only" >> "$GITHUB_ENV"\n'
RESHAPING_ENV = (
    "ENV", "PATH", "BASHOPTS", "SHELLOPTS", "BASH_FUNC_pytest%%", "LD_PRELOAD", "DYLD_INSERT_LIBRARIES",
    "PYTHONPATH", "PYTHONSTARTUP", "PYTEST_ADDOPTS", "PYTEST_PLUGINS",
)
UNRELATED_ENV = ("ENVIRONMENT_LABEL", "PATHS_TO_LINT", "ENVIRONMENT", "BASH_VERSION_PIN", "PYTHONUNBUFFERED", "PYTHONIOENCODING")
# Each route switches the proof off, skips it or hollows it out: each must fail a pin.
REFUSED: dict[str, Callable[[dict[str, Any]], None]] = {
    # Review of #330, round 1.
    "job if": _set(("jobs", JOB, "if"), "false"),
    "job continue-on-error": _set(("jobs", JOB, "continue-on-error"), True),
    "job needs": _set(("jobs", JOB, "needs"), ["frontend-tests"]),
    "job env": _set(("jobs", JOB, "env"), {"PYTEST_ADDOPTS": "--collect-only"}),
    "step if": _set(("jobs", JOB, "steps", 3, "if"), "false"),
    "step continue-on-error": _set(("jobs", JOB, "steps", 3, "continue-on-error"), True),
    "pytest -k": _edit_run(3, " -k nothing"),
    "pytest || true": _edit_run(3, " || true"),
    # Round 2: the nine routes its pins were written against.
    "a step writing $GITHUB_ENV": _insert_step({"name": "Tune pytest", "run": _COLLECT_ONLY}),
    "the install step writing $GITHUB_ENV": _edit_run(2, _COLLECT_ONLY),
    "a step rewriting the proof module": _insert_step(
        {"name": "Prepare", "run": "printf 'def test_ok():\\n    pass\\n' > tests/unit/test_client_portability_proof.py"}
    ),
    "checkout of another ref": _set(("jobs", JOB, "steps", 0, "with"), {"ref": "main"}),
    "pull_request paths-ignore": _set(("on", "pull_request"), {"paths-ignore": ["scripts/**", "tests/**", "tools/**"]}),
    "push paths": _set(("on", "push", "paths"), ["docs/**"]),
    "workflow defaults shell": _set(("defaults",), {"run": {"shell": "true {0}"}}),
    "BASH_ENV": _env("BASH_ENV"),
    # Round 3: a push that no longer triggers on main, read as GitHub reads it.
    "push '!main' after main": _push_branches(["main", "!main", "feature/**"]),
    "push '!ma*' after main": _push_branches(["main", "!ma*"]),
    "push '!**' after main": _push_branches(["main", "!**"]),
    "push branches as one scalar": _push_branches("maintenance"),
    "push branches without main": _push_branches(["feature/**"]),
    "push branches-ignore": _set(("on", "push", "branches-ignore"), ["main"]),
    "push tags only": _set(("on", "push"), {"tags": ["v*"]}),
    "pull_request types without synchronize": _set(("on", "pull_request"), {"types": ["opened", "reopened"]}),
    "pull_request types as one scalar": _set(("on", "pull_request"), {"types": "opened"}),
    "pull_request branches": _set(("on", "pull_request"), {"branches": ["release/**"]}),
    # Round 3: the shell's and the interpreter's variables, by exact name or by family.
    **{f"workflow env {name}": _env(name) for name in RESHAPING_ENV},
}
# Each edit keeps the proof running on every pull request and main push: no pin may refuse it (round 3).
ACCEPTED: dict[str, Callable[[dict[str, Any]], None]] = {
    "the committed ci.yml": lambda workflow: None,
    "a run-name": _set(("run-name",), "ci for ${{ github.ref_name }}"),
    "pull_request with GitHub's default types": _set(("on", "pull_request"), {"types": ["opened", "synchronize", "reopened"]}),
    "pull_request types beyond the defaults": _set(
        ("on", "pull_request"), {"types": ["opened", "synchronize", "reopened", "ready_for_review"]}
    ),
    "push tags beside the branches": _set(("on", "push", "tags"), ["v*"]),
    "push another branch": _push_branches(["main", "feature/**", "release/**"]),
    "push a negation that cannot match main": _push_branches(["main", "feature/**", "!feature/old/**"]),
    "a workflow_dispatch trigger": _set(("on", "workflow_dispatch"), None),
    "a merge_group trigger": _set(("on", "merge_group"), None),
    **{f"workflow env {name}": _env(name) for name in UNRELATED_ENV},
}


@pytest.mark.parametrize("route", sorted(REFUSED))
def test_every_switch_off_route_is_refused(route: str) -> None:
    workflow = copy.deepcopy(_workflow())
    REFUSED[route](workflow)

    assert _refusals(workflow), route


@pytest.mark.parametrize("edit", sorted(ACCEPTED))
def test_an_edit_that_keeps_the_proof_running_is_accepted(edit: str) -> None:
    workflow = copy.deepcopy(_workflow())
    ACCEPTED[edit](workflow)

    assert _refusals(workflow) == [], edit
