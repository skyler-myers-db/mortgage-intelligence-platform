"""A bundle job run's ``Run URL:`` line never reaches the public release log.

``databricks bundle run <job>`` writes ``Run URL: <url>`` to its stderr when
the run starts (CLI v1.7.0, which deploy-dev.yml installs, and v1.10.0:
bundle/run/job.go ``jobRunMonitor.onProgress`` logs
``progress.NewJobRunUrlEvent`` through ``cmdio.LogString``, which writes the
command's error writer). The URL is ``https://<host>/jobs/<job id>/runs/<run
id>?o=<workspace id>``, or the Jobs API's legacy
``https://<host>/?o=<workspace id>#job/<job id>/run/<run id>`` when the CLI
cannot rewrite it. The host and the workspace id are profile masks; the job id
and the run id are not, and the project counts them as identifiers
(tests/unit/test_no_client_identifiers_committed.py's 16-digit shape names a
run id; the data phase's job runner prints job keys only; the adopt map masks
every live job id it reads). The progress lines (timestamp, run name, state)
and a failed task's error and trace carry neither.

``run_job_with_retry`` (scripts/deploy.sh), through which every ``bundle run``
of the release goes, runs each attempt through ``keep_run_url_off_the_log``:
the CLI's stderr passes a filter that withholds that one line and nothing
else, stdout is untouched, and the command's own status is returned.
"""

from __future__ import annotations

import shlex
from pathlib import Path

from tests.fixtures.deploy_block_harness import drive, function
from tests.fixtures.deploy_script import deploy_contract_text
from tests.fixtures.runner_mask_harness import carrying, merged_log, shell_function

HOST = "workspace-sandbox.example.invalid"
# Built here, never written whole: digit runs of a real id's length are what the
# committed-identifier scan looks for.
JOB = "8" * 15
RUN = "9" * 15
WORKSPACE = "6" * 10
MODERN = f"Run URL: https://{HOST}/jobs/{JOB}/runs/{RUN}?o=" + WORKSPACE
LEGACY = f"Run URL: https://{HOST}/?o={WORKSPACE}#job/{JOB}/run/{RUN}"
WITHHELD = "Run URL: withheld from the log (it names the job id and the run id)"
RUNNING = '2026-11-13 10:00:00 "mip_refresh_scores" RUNNING'
SUCCEEDED = '2026-11-13 10:04:00 "mip_refresh_scores" TERMINATED SUCCESS'
FAILED = '2026-11-13 10:04:00 "mip_refresh_scores" TERMINATED FAILED'
FAILURE = [
    "Task refresh_gold FAILED:",
    "AnalysisException: [TABLE_OR_VIEW_NOT_FOUND] mip.silver.borrower_features",
    'Traceback (most recent call last): File "refresh.py", line 12',
    "",
    "Error: run failed: Task refresh_gold failed with message: Workload failed.",
]
OUTPUT = ["Output:", "=======", "Task refresh_gold:", "refreshed 12 gold tables"]


def _cli(repo: Path, *, url: str = MODERN, rc: int = 0, tail: str = "\n") -> None:
    """A ``databricks`` on PATH that writes what the real one does for one job run.

    stderr: the run URL (``LogString`` appends a newline to the event's own), each
    state change, and on failure the failed task's error and trace and the CLI's
    error; stdout: the task output once the run succeeds.
    """

    stderr = [url, "", RUNNING, *([FAILED, *FAILURE] if rc else [SUCCEEDED])]
    stdout = [] if rc else OUTPUT
    bindir = repo / "bin"
    bindir.mkdir(exist_ok=True)
    cli = bindir / "databricks"
    cli.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> {shlex.quote(str(repo / 'cli.log'))}\n"
        f"printf '%s' {shlex.quote(chr(10).join(stderr) + tail)} >&2\n"
        + (f"printf '%s\\n' {' '.join(shlex.quote(line) for line in stdout)}\n" if stdout else "")
        + f"exit {rc}\n",
        encoding="utf-8",
    )
    cli.chmod(0o755)


# The real retry wrapper and the real filter; no retry waits.
WRAPPER = (
    'PATH="$PWD/bin:$PATH"\nsleep() { :; }\n'
    + function("withhold_run_url_lines")
    + function("keep_run_url_off_the_log")
    + function("run_job_with_retry")
)
JOB_RUN = 'run_job_with_retry databricks bundle run mip_refresh_scores -t client || echo "job failed: $?"\n'


def _in_order(lines: list[str], expected: list[str]) -> bool:
    """``expected`` occurs in ``lines`` as a subsequence (the filter may reorder stderr against stdout)."""

    found = iter(lines)
    return all(any(line == want for line in found) for want in expected)


def _no_ids(lines: list[str]) -> None:
    for value in (JOB, RUN):
        assert not carrying(lines, value), (value, lines)


def test_a_run_prints_no_job_or_run_id_and_keeps_its_progress_and_output(tmp_path: Path) -> None:
    _cli(tmp_path)
    code, lines = merged_log(tmp_path, WRAPPER + JOB_RUN)

    assert code == 0, lines
    _no_ids(lines)
    assert lines[0] == "$ databricks bundle run mip_refresh_scores -t client", "the command echo is unchanged"
    assert lines.count(WITHHELD) == 1, lines
    assert _in_order(lines, [WITHHELD, "", RUNNING, SUCCEEDED]), lines
    assert _in_order(lines, OUTPUT), lines
    assert lines[-1] == "reached-after-block" and not [line for line in lines if line.startswith("job failed")]


def test_the_legacy_run_url_is_withheld_too(tmp_path: Path) -> None:
    _cli(tmp_path, url=LEGACY)
    code, lines = merged_log(tmp_path, WRAPPER + JOB_RUN)

    assert code == 0, lines
    _no_ids(lines)
    assert lines.count(WITHHELD) == 1, lines


def test_a_failed_run_keeps_every_failure_line_retries_and_still_fails(tmp_path: Path) -> None:
    _cli(tmp_path, rc=1)
    code, lines = merged_log(tmp_path, WRAPPER + JOB_RUN)

    assert code == 0, lines
    _no_ids(lines)
    assert (tmp_path / "cli.log").read_text(encoding="utf-8").count("bundle run mip_refresh_scores") == 3
    assert lines.count(WITHHELD) == 3, "each attempt's line is withheld"
    for line in [FAILED, *[line for line in FAILURE if line]]:
        assert lines.count(line) == 3, (line, lines)
    assert lines.count("[deploy] job run attempt 1 failed (likely a local network flap) — retrying in 15s") == 1
    assert lines[-2:] == ["job failed: 1", "reached-after-block"], "the wrapper still reports the failure"


def test_the_filter_returns_the_commands_own_status_and_keeps_the_streams_apart(tmp_path: Path) -> None:
    # A last line without a newline is kept; stdout never passes the filter.
    _cli(tmp_path, rc=3, tail="")
    shell = (
        WRAPPER
        + 'keep_run_url_off_the_log databricks bundle run mip_refresh_scores -t client || echo "status: $?"\n'
        + '_cli_ok() { printf \'%s\\n\' "$1"; printf \'%s\\n\' "$2" >&2; }\n'
        + f'keep_run_url_off_the_log _cli_ok {shlex.quote(OUTPUT[0])} {shlex.quote(MODERN)} && echo "status: 0"\n'
    )
    result = drive(tmp_path, shell, dry_run=0, env={}, epilogue="")

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == ["status: 3", OUTPUT[0], "status: 0", "reached-after-block"]
    assert result.stderr.splitlines() == [WITHHELD, "", RUNNING, FAILED, *FAILURE, WITHHELD]
    assert JOB not in result.stderr and RUN not in result.stderr


def test_a_line_that_only_looks_alike_passes_unchanged(tmp_path: Path) -> None:
    code, lines = merged_log(
        tmp_path, WRAPPER + "printf '%s\\n' 'Run URLs are withheld' 'run url: lower case' | withhold_run_url_lines"
    )

    assert code == 0 and lines == ["Run URLs are withheld", "run url: lower case", "reached-after-block"], lines


def test_every_bundle_job_run_of_the_release_goes_through_the_retry_wrapper() -> None:
    # The wrapper is where the line is withheld: a bare ``databricks bundle run`` would print it.
    reconcile = shell_function("run_job_and_reconcile_reviewed_function_grants")
    assert '  run_job_with_retry "$@" || _job_rc=$?\n' in reconcile
    lines = deploy_contract_text().splitlines()
    sites = [
        (index, line.strip())
        for index, line in enumerate(lines)
        if "databricks bundle run " in line and not line.lstrip().startswith(("#", "echo", "step "))
    ]
    assert len(sites) >= 6, sites
    for index, line in sites:
        through_retry = line.startswith("run_job_with_retry databricks bundle run ")
        through_reconcile = line.startswith("databricks bundle run ") and lines[index - 2].strip() == (
            "run_job_and_reconcile_reviewed_function_grants \\"
        )
        assert through_retry or through_reconcile, line
