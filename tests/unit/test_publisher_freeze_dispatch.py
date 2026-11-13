"""A ``phase=thaw`` dispatch of deploy-dev.yml, replayed offline step by step (wave 7c).

The replay (tests/fixtures/dispatch_replay.py) renders every step's header
from the workflow itself, as the runner logs it, and runs every step the
offline sandbox can. For a marker phase that proves, against the real
workflow: check-dispatch admits the phase; the dispatch preflight holds it to
the profile ack and the protections ack, and exports the thaw's
``freeze_thaw`` ack for the job; ``scripts/deploy.sh`` never runs; and the
marker step's own header, which the public log prints, binds no secret. The
step itself needs a workspace, so only its header is rendered here:
tests/unit/test_publisher_freeze.py runs the tool against the in-memory one.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from tests.fixtures import client_portability as cp
from tests.fixtures.dispatch_replay import github_file, replay
from tools.clients import dispatch_acks
from tools.clients import profile as views

CLIENT = cp.CLIENT
RUN_STEP = cp.CLIENT_PHASE
FREEZE_STEP = "Freeze or thaw the emergency publisher"
THAW_ACK = hashlib.sha256(b"a thaw plan").hexdigest()


def test_a_thaw_dispatch_runs_the_marker_step_in_place_of_deploy_sh_and_binds_no_secret(tmp_path: Path) -> None:
    # acme, the synthetic second client: no tracked file holds one of its values once the
    # clone drops its fixture, so a marker phase passes the tree scan as a release does.
    values = cp.acme_values()
    profile = cp.profile(values)
    record = cp.recorders(tmp_path)

    replayed = replay(
        tmp_path,
        values=values,
        client=CLIENT,
        prepare=cp.prepare(record),
        inputs=lambda sha: {
            "phase": "thaw",
            "owner_attestation": dispatch_acks.profile_ack(CLIENT, views.digest(profile)),
            "operation_acks": f"freeze_thaw={THAW_ACK}",
            # GitHub's API is unreachable in the sandbox (S8's worst case).
            "protections_unverified_ack": dispatch_acks.protections_ack(CLIENT, CLIENT, sha),
        },
    )

    for job in replayed.jobs.values():
        for name, result in job.results.items():
            assert result.returncode == 0, (job.name, name, result.stdout[-3000:], result.stderr[-3000:])
    deploy = replayed.jobs["deploy"]
    assert "Refuse a malformed client key or phase" in replayed.jobs["check-dispatch"].results
    preflight = deploy.results["Preflight the dispatch"].stdout
    assert "### deploy-dev dispatch preflight (--phase thaw)" in preflight and "- verdict: passed" in preflight
    assert "- profile ack: exact for this profile digest" in preflight, "a marker phase needs it, like a release"
    assert "- protections_unverified ack: exact for this client, environment and commit" in preflight
    assert "- operation acks: MIP_ACK_FREEZE_THAW" in preflight and THAW_ACK not in preflight
    # The preflight's export reaches the marker step through GITHUB_ENV, never through its own env block.
    assert github_file(tmp_path / "github.env")["MIP_ACK_FREEZE_THAW"] == THAW_ACK

    logged = [step for step, _line, _is_header in deploy.lines]
    assert RUN_STEP not in logged and RUN_STEP not in deploy.results, "scripts/deploy.sh never runs for a marker phase"
    assert record.cli_calls() == [] and record.executions() == [], "no Databricks CLI call and no deploy.sh child at all"
    assert logged.index("Preflight the dispatch") < logged.index(FREEZE_STEP) < logged.index("Record the quiesce window journal state")
    header = [line for step, line, is_header in deploy.lines if step == FREEZE_STEP and is_header]
    assert header == [
        'Run python -m tools.clients.publisher_freeze --phase "$MIP_DISPATCH_PHASE" --env-file .env.local',
        "  env:",
        "    MIP_REDACT_FOREIGN_NAMES: 1",
        "    DATABRICKS_CONFIG_PROFILE: DEFAULT",
        "    MIP_DISPATCH_PHASE: thaw",
    ], "what the public log prints before the step: the job's switch, the profile name and the phase"
    private = cp.identifying_values(values)
    assert values["MIP_AI_GATEWAY_PROOF_SIGNING_KEY"] in private
    for line in header:
        assert not [value for value in (*private, THAW_ACK) if value in line]
    # Nothing the whole job logs, masked as the runner masks it, holds the signing key or an identifier.
    for _step, line in deploy.shown():
        assert not [value for value in private if value in line], line[:120]
    assert not (replayed.checkout / ".env.local").exists(), "the last step removes the profile files"
