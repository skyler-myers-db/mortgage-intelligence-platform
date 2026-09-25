"""The real-PostgreSQL contracts run in CI instead of skipping (audit genie-01 item 4).

Two integration suites read ``MIP_TEST_POSTGRES_DSN`` and run the ACTUAL SQL
and DDL against a disposable database (the Genie completion-job store and
the Lakebase schema upgrade). Without a database they skip, which is what
they did on every CI run until this wiring. The backend job now carries a
pinned PostgreSQL service, and ONE serial step gets the DSN, runs every such
suite with ``-n 0`` (they all DROP/CREATE ``mip_app`` in one database) and
fails when the junit report shows zero tests or any skip.

``KNOWN_UNRUN_DSN_SUITES`` is the shrink-only list of what that step does not
run as a gate: a pytest node id (``file::test``) is deselected and re-run
last, where it must still fail (a pass is a stale entry and fails the step);
a bare file path would leave a whole suite out of the step. Never add an
entry to make CI green: fix the test or the schema, then delete the entry in
ci.yml and here in the same change.
"""
from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
DSN_ENV = "MIP_TEST_POSTGRES_DSN"
SERIAL_STEP = "pytest (real PostgreSQL contracts, serial)"
PARALLEL_STEP = "pytest (parallel + coverage gate)"
# Resolved in the registry on 2026-09-25: the newest 16.x-bookworm tag, and
# its OCI image-index digest (linux/amd64 + linux/arm64 manifests).
PINNED_IMAGE = (
    "postgres:16.15-bookworm@sha256:efedf3595f1d6f415c08568ba171029bf54052e754cc9f030e3f2412b21f3d67"
)
DSN = "host=127.0.0.1 port=5432 dbname=postgres user=postgres"

KNOWN_UNRUN_DSN_SUITES: dict[str, dict[str, str]] = {
    "tests/integration/test_lakebase_schema_upgrade.py::test_campaign_treatment_state_machine_freezes_ready_proof": {
        "owner": "genie-server (W4a; Lakebase schema-upgrade suite)",
        "reason": (
            "the ready_request_hash probe writes '7' * 64, the value _ready_campaign already "
            "stored, so the treatment trigger correctly sees no change and the probe reports an "
            "accepted mutation; a test-data defect (first run on real PostgreSQL), not a schema "
            "or Lakebase difference. Fix: probe with another hash, then delete this entry"
        ),
        "recorded": "2026-09-25",
    },
}
# Shrink-only: the keys recorded when the step was introduced. Never add one.
_KNOWN_UNRUN_AT_INTRODUCTION = frozenset(
    {
        "tests/integration/test_lakebase_schema_upgrade.py::test_campaign_treatment_state_machine_freezes_ready_proof",
    }
)


def _workflow() -> dict[str, Any]:
    return yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))


def _backend_job() -> dict[str, Any]:
    return _workflow()["jobs"]["backend-tests"]


def _step(name: str) -> dict[str, Any]:
    return next(step for step in _backend_job()["steps"] if step.get("name") == name)


def _step_names() -> list[str]:
    return [str(step.get("name", step.get("uses", ""))) for step in _backend_job()["steps"]]


def _serial_pytest_paths() -> list[str]:
    run = _step(SERIAL_STEP)["run"]
    command = re.search(r"^pytest -q -n 0 .*?(?<!\\)$", run, re.MULTILINE | re.DOTALL)
    assert command, "the serial step runs one `pytest -q -n 0 ...` command"
    return [token for token in command.group(0).split() if token.startswith("tests/") and token.endswith(".py")]


def _dsn_suites() -> list[str]:
    return sorted(
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "tests").rglob("*.py")
        if DSN_ENV in path.read_text(encoding="utf-8") and path.name != Path(__file__).name
    )


def test_the_backend_job_runs_a_pinned_trust_auth_postgres_service() -> None:
    service = _backend_job()["services"]["postgres"]

    assert service["image"] == PINNED_IMAGE
    assert re.fullmatch(r"postgres:16\.\d+-bookworm@sha256:[0-9a-f]{64}", service["image"])
    assert service["env"] == {"POSTGRES_HOST_AUTH_METHOD": "trust"}
    assert "POSTGRES_PASSWORD" not in CI_WORKFLOW.read_text(encoding="utf-8"), "no password literal anywhere"
    assert service["ports"] == ["5432:5432"]
    options = service["options"]
    assert '--health-cmd "pg_isready -U postgres"' in options
    for option in ("--health-interval 5s", "--health-timeout 5s", "--health-retries 20"):
        assert option in options


def test_only_the_serial_step_sees_the_dsn_and_it_runs_after_the_parallel_step() -> None:
    workflow = _workflow()
    job = _backend_job()
    assert DSN_ENV not in (workflow.get("env") or {}), "never at workflow level"
    assert DSN_ENV not in (job.get("env") or {}), "never at job level"
    holders = [step.get("name") for step in job["steps"] if DSN_ENV in (step.get("env") or {})]
    assert holders == [SERIAL_STEP]
    assert _step(SERIAL_STEP)["env"][DSN_ENV] == DSN
    assert DSN_ENV not in str(_step(PARALLEL_STEP).get("run", ""))

    names = _step_names()
    assert names.index(PARALLEL_STEP) < names.index(SERIAL_STEP)
    for other_job, spec in workflow["jobs"].items():
        if other_job != "backend-tests":
            assert DSN_ENV not in yaml.safe_dump(spec), f"{other_job} must not carry the DSN"


def test_the_serial_step_runs_single_process_and_fails_on_zero_tests_or_a_skip() -> None:
    run = _step(SERIAL_STEP)["run"]

    assert re.search(r"^pytest -q -n 0 -rs --junitxml=postgres-contracts\.xml ", run, re.MULTILINE)
    assert 'ET.parse("postgres-contracts.xml")' in run
    assert "if tests == 0 or skipped > 0:" in run
    assert "sys.exit(1)" in run
    # The database takes the dedicated Lakebase database's public-schema
    # posture before any suite runs.
    assert run.index("REVOKE ALL ON SCHEMA public FROM PUBLIC") < run.index("pytest -q -n 0 -rs")


def test_every_dsn_suite_is_matched_by_the_serial_step() -> None:
    suites = _dsn_suites()
    assert {
        "tests/integration/test_genie_completion_jobs_postgres.py",
        "tests/integration/test_lakebase_schema_upgrade.py",
    } <= set(suites), "the two real-SQL suites read the DSN"

    globs = _serial_pytest_paths()
    assert "tests/integration/test_*_postgres.py" in globs, "a future test_*_postgres.py suite is picked up"
    whole_file_cuts = {key for key in KNOWN_UNRUN_DSN_SUITES if "::" not in key}
    for suite in suites:
        matched = any(fnmatch.fnmatchcase(suite, pattern) for pattern in globs)
        if suite in whole_file_cuts:
            assert not matched, f"{suite} is listed as unrun but the step still runs it"
        else:
            assert matched, f"{suite} reads {DSN_ENV} but the serial step would never run it"


def test_known_unrun_entries_are_governed_shrink_only_and_not_stale() -> None:
    assert set(KNOWN_UNRUN_DSN_SUITES) <= _KNOWN_UNRUN_AT_INTRODUCTION, "the list only shrinks"
    step_env = _step(SERIAL_STEP)["env"]
    listed_nodes = str(step_env.get("KNOWN_UNRUN", "")).split()
    assert listed_nodes == sorted(key for key in KNOWN_UNRUN_DSN_SUITES if "::" in key)

    for key, entry in KNOWN_UNRUN_DSN_SUITES.items():
        assert set(entry) == {"owner", "reason", "recorded"}
        assert all(value.strip() for value in entry.values())
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", entry["recorded"])
        file, _, test_name = key.partition("::")
        source = ROOT / file
        assert source.is_file(), f"stale entry: {file} is gone"
        if test_name:
            assert re.search(rf"^def {re.escape(test_name)}\(", source.read_text(encoding="utf-8"), re.MULTILINE), (
                f"stale entry: {key} no longer exists"
            )

    run = _step(SERIAL_STEP)["run"]
    # Each node is deselected from the gate, then re-run on its own: a pass
    # is a stale entry and fails the step.
    assert 'deselect+=(--deselect "$node")' in run
    assert 'pytest -q -n 0 -p no:cacheprovider "$node"' in run
    assert 'if [ "$status" -eq 0 ]; then' in run
