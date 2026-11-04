"""WR5: the Lakebase handover's announced window, through the window library only (M2-PR8).

The release window's reasons (WR1-WR4) and digests are unchanged; WR5 is a
reviewed reason with its own read-only plan that opens only with the exact
digest ack (``MIP_ACK_QUIESCE_WINDOW``), refuses a signed install, pins the
App, the folder a start snapshots and the handover's remainder, and proves the
start. Its stop and start are the window library's: the owner-run entry holds
no stop primitive (the lexical contract scans it), and a harness runs the real
entry with a fake ``$PYTHON`` and ``databricks`` to check the order and the
EXIT trap.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.databricks import quiesce_window_plan as release
from tools.databricks import quiesce_window_wr5 as wr5
from tools.databricks import stop_primitive_map as stops
from tools.databricks.app_start_deployment import StartProofError

REPO = Path(__file__).resolve().parents[2]
ENTRY = REPO / "scripts" / "lakebase_handover_window.sh"
FACTS = wr5.HandoverWindowFacts(
    app_present=True,
    app_id="app-id-PLACEHOLDER",
    app_client_id="app-client-PLACEHOLDER",
    app_scim_id="app-scim-PLACEHOLDER",
    compute_state="ACTIVE",
    active_deployment_id="deployment-serving",
    active_source="/Workspace/Users/legacy-PLACEHOLDER/app-src/release",
    default_source="/Workspace/Users/legacy-PLACEHOLDER/app-src/release",
    handover_status="exhausted",
    handover_sha256="h" * 64,
    remainder=3,
)


def _acked(facts: wr5.HandoverWindowFacts) -> dict:
    digest = wr5.window_plan(facts, acknowledgement="")["public"]["window_sha256"]
    return wr5.window_plan(facts, acknowledgement=digest)


def test_wr5_is_a_reviewed_reason_the_release_never_plans() -> None:
    assert release.REASONS == ("WR1", "WR2", "WR3", "WR4")
    assert release.HANDOVER_REASON == "WR5"
    assert release.REVIEWED_REASON_NAMES["WR5"] == "lakebase_handover_window"
    assert set(release.REVIEWED_REASON_NAMES) == {*release.REASONS, "WR5"}
    every = release.WindowFacts(applicable=True, binding_state="diff", role_state="drift", migration_decision="apply", instance_update_sha256="c" * 64)
    assert every.reasons() == release.REASONS, "a release plan never carries WR5"
    assert "WR5" not in release.STEP_BUDGET_S


def test_the_release_canonical_body_is_unchanged() -> None:
    """The release digest covers exactly #307's fields, so WR1-WR4 digests are as before."""

    body = release.canonical_body(release.WindowFacts(applicable=True), blue_restorable=True)
    assert sorted(body) == sorted(
        [
            "schema", "reasons", "blue_deployment_id", "restore_source", "source_tree_sha256",
            "env_names_sha256", "blue_restorable", "binding_state", "binding_lines", "role_state",
            "verifier_role_state", "jobs_reader_role_state", "jobs_reader_grants", "migration_pending",
            "migration_pending_contract", "instance_update_sha256",
        ]
    )  # fmt: skip
    assert body["schema"] == "mip.quiesce-window-plan.canonical.v1"


def test_wr5_opens_only_with_its_exact_digest_ack() -> None:
    unacked = wr5.window_plan(FACTS, acknowledgement="")
    wrong = wr5.window_plan(FACTS, acknowledgement="0" * 64)
    acked = _acked(FACTS)
    assert unacked["public"]["refusals"] == ["window_unacked"] and unacked["public"]["ack"] == "absent"
    assert wrong["public"]["ack"] == "mismatch" and wrong["public"]["code"] == "refused"
    assert acked["public"]["code"] == "window" and acked["public"]["refusals"] == []
    assert acked["public"]["reasons"] == ["WR5"]
    assert acked["public"]["predicted_seconds"] == release.STEP_BUDGET_S["stop"] + 300 + release.STEP_BUDGET_S["start"]
    assert release.body_sha256(acked["canonical"]) == acked["public"]["window_sha256"]


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"signed_record": True}, "window_wr5_signed_install"),
        ({"handover_status": "applied"}, "window_handover_not_exhausted"),
        ({"remainder": 0}, "window_handover_not_exhausted"),
        ({"quiesce_journal_state": "window_open"}, "window_journal_open"),
        ({"pending_deployment_id": "deployment-pending"}, "window_pending_deployment"),
        ({"compute_state": "STOPPED"}, "window_app_not_active"),
        ({"default_source": "/Workspace/Users/legacy-PLACEHOLDER/app-src/other"}, "window_start_source_drift"),
        ({"app_scim_id": ""}, "window_facts_incomplete"),
    ],
)
def test_each_unsafe_fact_refuses_even_when_acked(change: dict, code: str) -> None:
    plan = _acked(replace(FACTS, **change))
    assert code in plan["public"]["refusals"] and plan["public"]["code"] == "refused"
    assert plan["remedies"][code] == wr5.REMEDIES[code]


@pytest.mark.parametrize("field", ["handover_sha256", "remainder", "active_deployment_id", "default_source", "app_id"])
def test_the_digest_pins_the_app_the_start_folder_and_the_remainder(field: str) -> None:
    value = getattr(FACTS, field)
    changed = replace(FACTS, **{field: value + 1 if isinstance(value, int) else value + "-changed"})
    assert wr5.window_plan(changed, acknowledgement="")["public"]["window_sha256"] != wr5.window_plan(FACTS, acknowledgement="")["public"]["window_sha256"]


def _workspace(*, active: str = "deployment-started", pending: str = "") -> SimpleNamespace:
    app = SimpleNamespace(
        active_deployment=SimpleNamespace(deployment_id=active),
        pending_deployment=SimpleNamespace(deployment_id=pending) if pending else None,
    )
    return SimpleNamespace(apps=SimpleNamespace(get=lambda _name: app))


def _deployment(state: str = "SUCCEEDED", source: str = FACTS.default_source) -> SimpleNamespace:
    return SimpleNamespace(status=SimpleNamespace(state=state), source_code_path=source)


def test_the_start_proof_needs_a_new_succeeded_deployment_from_the_planned_folder() -> None:
    plan = _acked(FACTS)
    waited: list[str] = []

    def wait(_ws, *, app_name: str, deployment_id: str):  # noqa: ANN202
        waited.append(deployment_id)
        return _deployment()

    created = wr5.prove_start(_workspace(pending="deployment-new"), app_name="mip-app", plan=plan, wait=wait, wait_active=lambda *_a, **_k: None)
    assert created == "deployment-new" and waited == ["deployment-new"]
    cases = [
        (_workspace(active="deployment-serving"), _deployment(), "start_unreadable"),
        (_workspace(), _deployment(state="FAILED"), "start_not_succeeded"),
        (_workspace(), _deployment(source="/Workspace/elsewhere"), "start_source_path"),
    ]
    for workspace, deployment, code in cases:
        with pytest.raises(StartProofError) as refused:
            wr5.prove_start(workspace, app_name="mip-app", plan=plan, wait=lambda *_a, _d=deployment, **_k: _d, wait_active=lambda *_a, **_k: None)
        assert refused.value.code == code
    tampered = {**plan, "canonical": {**plan["canonical"], "start_source": "/Workspace/elsewhere"}}
    with pytest.raises(StartProofError, match="not a passed, intact plan"):
        wr5.prove_start(_workspace(), app_name="mip-app", plan=tampered)


def test_the_wr5_modes_are_read_only_and_the_entry_holds_no_stop_primitive() -> None:
    modes = stops.ENTRY_POINTS["tools.databricks.quiesce_window"].modes
    assert modes["plan-wr5"] is False and modes["await-wr5"] is False
    assert ENTRY in stops.shell_files()
    assert stops.repo_shell_violations() == []
    text = ENTRY.read_text(encoding="utf-8")
    for primitive in ("databricks apps start mip-app", '"$PYTHON" -m tools.databricks.stop_app_fail_closed --app-name "$A"'):
        assert stops.shell_violations("scripts/lakebase_handover_window.sh", text + f"\n{primitive}\n"), primitive
    window = (REPO / stops.WINDOW_LIBRARY).read_text(encoding="utf-8")
    for function in ("run_lakebase_handover_window", "start_lakebase_handover_window", "compensate_lakebase_handover_window"):
        assert f"\n{function}() {{\n" in window, f"{function} lives in the window library"
    assert "run_lakebase_handover_window" in text and "trap 'compensate_lakebase_handover_window' EXIT" in text


def test_the_python_side_of_wr5_reaches_no_primitive() -> None:
    sites = stops.python_primitive_sites()
    assert sites == set(stops.PRIMITIVE_SITES)
    assert not any("wr5" in path or "handover" in path for path, _function, _method in sites)


FAKE_PYTHON = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$CALLS"
out=""; prev=""
for arg in "$@"; do
  if [[ "$prev" == "--out-env" ]]; then out="$arg"; fi
  prev="$arg"
done
case "$*" in
  *"--window-context"*) printf 'WR5_APP_NAME=mip-app\nWR5_SCOPE=rollback-scope\nWR5_LAKEBASE_INSTANCE=instance\n' ;;
  *"quiesce_window plan-wr5"*)
    [[ -z "${PLAN_REFUSES:-}" ]] || exit 2
    printf 'MIP_WR5_WINDOW_SHA256=w\nMIP_WR5_HANDOVER_SHA256=h\nMIP_WR5_APP_ID=app-id\nMIP_WR5_APP_CLIENT_ID=app-client\nMIP_WR5_APP_SCIM_ID=app-scim\n' > "$out" ;;
  *"stop_app_fail_closed"*) exit "${STOP_RC:-0}" ;;
  *"--in-window"*) exit "${IN_WINDOW_RC:-0}" ;;
  *"await-wr5"*)
    if [[ -n "${AWAIT_FAILS_ONCE:-}" && ! -e "$CALLS.awaited" ]]; then touch "$CALLS.awaited"; exit 1; fi ;;
esac
exit 0
"""
FAKE_DATABRICKS = """#!/usr/bin/env bash
printf 'databricks %s\\n' "$*" >> "$CALLS"
exit "${START_RC:-0}"
"""


def _run_entry(tmp_path: Path, **env: str) -> tuple[int, list[str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, body in (("fake-python", FAKE_PYTHON), ("databricks", FAKE_DATABRICKS)):
        (bin_dir / name).write_text(body, encoding="utf-8")
        (bin_dir / name).chmod(0o755)
    calls = tmp_path / "calls.log"
    bash = shutil.which("bash", path="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin") or "bash"
    result = subprocess.run(
        [bash, str(ENTRY), "--journal", str(tmp_path / "journal.json")],
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}", "PYTHON": str(bin_dir / "fake-python"), "CALLS": str(calls), **env},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    lines = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    return result.returncode, lines


def _kinds(lines: list[str]) -> list[str]:
    order = ("--window-context", "plan-wr5", "--window-open", "stop_app_fail_closed", "--in-window", "databricks apps start", "await-wr5", "--window-close")
    return [next(kind for kind in order if kind in line) for line in lines]


@pytest.mark.skipif(sys.platform == "win32", reason="bash harness")
def test_the_entry_runs_plan_journal_stop_apply_start_prove_close_in_order(tmp_path: Path) -> None:
    rc, lines = _run_entry(tmp_path)
    assert rc == 0
    assert _kinds(lines) == ["--window-context", "plan-wr5", "--window-open", "stop_app_fail_closed", "--in-window", "databricks apps start", "await-wr5", "--window-close"]
    stop = next(line for line in lines if "stop_app_fail_closed" in line)
    assert "--expected-app-id app-id --expected-client-id app-client --expected-scim-id app-scim" in stop


def test_a_refused_plan_never_stops_or_starts(tmp_path: Path) -> None:
    rc, lines = _run_entry(tmp_path, PLAN_REFUSES="1")
    assert rc == 2
    assert _kinds(lines) == ["--window-context", "plan-wr5"]


def test_an_exhausted_window_pass_still_starts_and_closes(tmp_path: Path) -> None:
    rc, lines = _run_entry(tmp_path, IN_WINDOW_RC="4")
    assert rc == 4
    assert _kinds(lines)[-3:] == ["databricks apps start", "await-wr5", "--window-close"]


@pytest.mark.parametrize("failure", [{"STOP_RC": "1"}, {"AWAIT_FAILS_ONCE": "1"}])
def test_the_exit_trap_starts_an_app_the_window_left_stopped(tmp_path: Path, failure: dict[str, str]) -> None:
    rc, lines = _run_entry(tmp_path, **failure)
    assert rc != 0
    kinds = _kinds(lines)
    assert kinds[-2:] == ["databricks apps start", "await-wr5"], kinds
    assert "--window-close" not in kinds


def test_plan_wr5_reads_the_exhausted_journal_and_opens_only_with_the_ack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import re
    import stat

    from tests.fixtures.fake_lakebase import DEPLOYER, FakeServer
    from tools.clients import lakebase_handover
    from tools.databricks import quiesce_window
    from tools.databricks.quiesce_window_facts import NOT_FOUND_RECORD

    server = FakeServer()
    server.hold("TABLE:mip_app.campaigns", 3600.0)
    digest = lakebase_handover.plan(server.read_session, target=DEPLOYER).sha256
    lakebase_handover.apply(
        server.read_session,
        server.connect,
        target=DEPLOYER,
        ack=digest,
        journal_path=tmp_path / "journal.json",
        clock=lakebase_handover.Clock(now=server.clock, sleep=server.sleep),
    )
    app = SimpleNamespace(
        id=FACTS.app_id,
        service_principal_client_id=FACTS.app_client_id,
        service_principal_id=FACTS.app_scim_id,
        compute_status=SimpleNamespace(state="ACTIVE"),
        active_deployment=SimpleNamespace(deployment_id=FACTS.active_deployment_id, source_code_path=FACTS.active_source),
        default_source_code_path=FACTS.default_source,
        pending_deployment=None,
    )

    def unsigned(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError(f"{NOT_FOUND_RECORD} for this App")

    monkeypatch.setattr(wr5, "app_or_none", lambda _ws, *, app_name: app)
    monkeypatch.setattr(wr5, "_load_record", unsigned)
    monkeypatch.setattr(wr5.journal, "read", lambda _ws, *, app_name: None)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    argv = [
        "plan-wr5", "--app-name", "mip-app", "--lakebase-handover-journal", str(tmp_path / "journal.json"),
        "--out", str(tmp_path / "plan.json"), "--out-env", str(tmp_path / "plan.env"),
    ]  # fmt: skip
    monkeypatch.delenv(release.ACK_ENV, raising=False)
    assert quiesce_window.main(argv, workspace_factory=lambda: object()) == 2
    out = capsys.readouterr()
    assert "REFUSED window_unacked" in out.err and not (tmp_path / "plan.env").exists()
    window = re.search(r"window sha256 ([0-9a-f]{64})", out.out)
    assert window is not None and stat.S_IMODE((tmp_path / "plan.json").stat().st_mode) == 0o600
    monkeypatch.setenv(release.ACK_ENV, window.group(1))
    assert quiesce_window.main(argv, workspace_factory=lambda: object()) == 0
    env = (tmp_path / "plan.env").read_text(encoding="utf-8")
    assert f"MIP_WR5_WINDOW_SHA256={window.group(1)}" in env and f"MIP_WR5_APP_SCIM_ID={FACTS.app_scim_id}" in env
