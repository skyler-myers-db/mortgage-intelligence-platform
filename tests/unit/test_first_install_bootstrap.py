"""A first-install release stops with exit 3 for the owner bootstrap, then resumes (M1-PR6).

Two layers:

* ``tools/databricks/first_install_bootstrap.py`` against fakes: the signed
  awaiting marker is written only for a claimed, stopped, source-free first
  install, names exactly that claim, refuses tampering and any other claim,
  and the account probe reads a rule set the principal may not read as "not
  held".
* the shell gate (``scripts/lib/deploy_governed_phases_lifecycle.sh``) driven
  with stubs: the creating release exits 3 with the App kept (the EXIT trap's
  stop and first-install deletion both stand down), the release after the
  owner bootstrap resumes at the same point and is an ordinary first install
  again, and a run with no ``--phase`` never reaches the gate.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from databricks.sdk.errors import PermissionDenied

from backend.services.ai_gateway_proof_attestation import derive_gateway_proof_verify_key
from tests.fixtures.deploy_script import DEPLOY_SCRIPT
from tests.fixtures.quiesce_window_estate import FakeFiles
from tests.unit.test_app_deployment_rollback import SIGNING_KEY
from tools.databricks import first_install_bootstrap as bootstrap

REPO = Path(__file__).resolve().parents[2]
LIBRARY = REPO / "scripts" / "lib" / "deploy_governed_phases_lifecycle.sh"
APP = "mip-app"
RESOURCES = [{"name": "sql-warehouse", "sql_warehouse": {"id": "warehouse-1", "permission": "CAN_USE"}}]
RECORD = {
    "bootstrap_id": "00000000-0000-4000-8000-0000000000aa",
    "app_id": "app-object-0001",
    "app_service_principal_client_id": "00000000-0000-4000-8000-0000000000bb",
    "app_service_principal_scim_id": "4242",
    "app_resources": RESOURCES,
}
COMMON = {"app_name": APP, "lease_id": "lease-PLACEHOLDER", "source_git_sha": "c" * 40}
LOCATED = {"scope": "mip-app-rollback", "instance": "mip-app-state"}


@pytest.fixture(autouse=True)
def _keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", derive_gateway_proof_verify_key(SIGNING_KEY))
    monkeypatch.setattr(bootstrap, "assert_held", lambda workspace, **_: None)


class _Workspace:
    def __init__(self, *, state: str = "STOPPED", deployment: object = None) -> None:
        self.workspace = FakeFiles()
        app = SimpleNamespace(
            compute_status=SimpleNamespace(state=state), active_deployment=deployment, pending_deployment=None
        )
        self.apps = SimpleNamespace(get=lambda name: app)


def _journal(monkeypatch: pytest.MonkeyPatch, *, state: str = "recover", record: dict[str, Any] | None = None) -> None:
    monkeypatch.setattr(bootstrap.journal, "status", lambda workspace, **_: state)
    monkeypatch.setattr(bootstrap.journal, "_download", lambda workspace, **_: dict(record or RECORD))


def test_the_marker_names_the_claimed_first_install_and_a_resume_consumes_it(monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = _Workspace()
    _journal(monkeypatch)
    assert bootstrap.status(workspace, **COMMON, **LOCATED) == "absent"

    marker = bootstrap.mark(workspace, **COMMON, **LOCATED)
    assert {key: marker[key] for key in ("bootstrap_id", "app_id", "app_service_principal_client_id")} == {
        key: RECORD[key] for key in ("bootstrap_id", "app_id", "app_service_principal_client_id")
    }
    assert bootstrap.mark(workspace, **COMMON, **LOCATED) == marker, "a second stop keeps the one marker"
    assert bootstrap.status(workspace, **COMMON, **LOCATED) == "resume"
    bootstrap.consume(workspace, app_name=APP, lease_id=COMMON["lease_id"], source_git_sha=COMMON["source_git_sha"])
    assert bootstrap.status(workspace, **COMMON, **LOCATED) == "absent"


@pytest.mark.parametrize(
    ("journal_state", "workspace"),
    (
        ("signed", _Workspace()),
        ("unclaimed", _Workspace()),
        ("recover", _Workspace(state="ACTIVE")),
        ("recover", _Workspace(deployment=SimpleNamespace(deployment_id="d-1"))),
    ),
    ids=("signed", "unclaimed", "running", "deployed"),
)
def test_only_a_claimed_stopped_source_free_first_install_is_marked(
    monkeypatch: pytest.MonkeyPatch, journal_state: str, workspace: _Workspace
) -> None:
    _journal(monkeypatch, state=journal_state)
    with pytest.raises(bootstrap.Refused):
        bootstrap.mark(workspace, **COMMON, **LOCATED)
    assert workspace.workspace.data == {}, "nothing was written"


def test_a_marker_for_another_claim_or_a_tampered_marker_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = _Workspace()
    _journal(monkeypatch)
    bootstrap.mark(workspace, **COMMON, **LOCATED)
    # The journal now names another first install (a delete-and-redo happened).
    _journal(monkeypatch, record={**RECORD, "app_id": "app-object-0002"})
    with pytest.raises(bootstrap.Refused, match="does not name the journal's claimed first install"):
        bootstrap.status(workspace, **COMMON, **LOCATED)
    path = bootstrap.marker_path(APP)
    value = json.loads(workspace.workspace.data[path])
    value["app_id"] = "app-object-0002"
    workspace.workspace.data[path] = json.dumps(value).encode()
    with pytest.raises(bootstrap.Refused, match="signature is invalid"):
        bootstrap.status(workspace, **COMMON, **LOCATED)


def test_a_resume_needs_the_bindings_its_journal_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = _Workspace()
    _journal(monkeypatch)
    monkeypatch.setattr(bootstrap, "app_resource_contract", lambda workspace, **_: RESOURCES)
    monkeypatch.setattr(bootstrap, "validated_app_resource_contract", lambda value: value)
    bootstrap.verify_resume(workspace, app_name=APP, payload={"resources": RESOURCES})
    other = [{**RESOURCES[0], "sql_warehouse": {"id": "warehouse-2", "permission": "CAN_USE"}}]
    with pytest.raises(bootstrap.Refused, match="changed while it awaited the owner bootstrap"):
        bootstrap.verify_resume(workspace, app_name=APP, payload={"resources": other})


def _account(rules: Any) -> Any:
    def get_rule_set(*, name: str, etag: str) -> Any:
        assert name == f"accounts/acct-1/servicePrincipals/{RECORD['app_service_principal_client_id']}/ruleSets/default"
        if isinstance(rules, Exception):
            raise rules
        return SimpleNamespace(grant_rules=rules)

    return SimpleNamespace(access_control=SimpleNamespace(get_rule_set=get_rule_set))


@pytest.mark.parametrize(
    ("rules", "rc"),
    (
        ([SimpleNamespace(role=bootstrap.SP_MANAGER, principals=["servicePrincipals/acct-principal"])], 0),
        ([SimpleNamespace(role="roles/servicePrincipal.user", principals=["servicePrincipals/acct-principal"])], 3),
        ([SimpleNamespace(role=bootstrap.SP_MANAGER, principals=["servicePrincipals/someone-else"])], 3),
        (PermissionDenied("not a manager"), 3),
        (RuntimeError("network"), 2),
    ),
    ids=("held", "user-only", "another-manager", "unreadable", "undecided"),
)
def test_the_check_reads_service_principal_manager_read_only(monkeypatch: pytest.MonkeyPatch, rules: Any, rc: int) -> None:
    monkeypatch.setenv("DATABRICKS_ACCOUNT_ID", "acct-1")
    monkeypatch.setenv("DATABRICKS_ACCOUNT_CLIENT_ID", "acct-principal")
    argv = ["check", "--app-application-id", RECORD["app_service_principal_client_id"]]
    assert bootstrap.main(argv, account=_account(rules)) == rc


# --- the shell gate -------------------------------------------------------------


def _drive(tmp_path: Path, body: str, **env: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    calls = tmp_path / "calls.log"
    script = f"""set -euo pipefail
DRY_RUN=0
DEPLOY_PHASE="${{DEPLOY_PHASE-release}}"
PYTHON=py_stub
_GRANTS_APP_NAME=mip-app
MIP_APP_DEPLOYMENT_LEASE_ID=lease-PLACEHOLDER
SOURCE_GIT_SHA={"c" * 40}
APP_ROLLBACK_SECRET_SCOPE=mip-app-rollback
MIP_LAKEBASE_INSTANCE=mip-app-state
APP_SP_CLIENT_ID=app-client
_EXISTING_APP_SP_CLIENT_ID=""
FIRST_INSTALL_RESUMED=0
FIRST_INSTALL_APP_CREATED="${{CREATED:-1}}"
FIRST_INSTALL_APP_BOUND="${{CREATED:-1}}"
APP_FAIL_CLOSED_ARMED=1
APP_UPGRADE_STATE=unverified_existing
RED="" YLW="" GRN="" RST=""
step() {{ echo "step: $*"; }}
run() {{ "$@"; }}
run_with_account_identity() {{ "$@"; }}
run_with_proof_signing_authority() {{ "$@"; }}
py_stub() {{
  printf '%s\\n' "$3" >> {str(calls)!r}
  case "$3" in
    check) return "${{CHECK_RC:-0}}" ;;
    status) echo "${{STATUS:-absent}}" ;;
  esac
}}
trap 'echo "exit created=$FIRST_INSTALL_APP_CREATED bound=$FIRST_INSTALL_APP_BOUND armed=$APP_FAIL_CLOSED_ARMED resumed=$FIRST_INSTALL_RESUMED state=$APP_UPGRADE_STATE"' EXIT
. {str(LIBRARY)!r}
{body}
echo CONTINUED
"""
    result = subprocess.run(["bash", "-c", script], text=True, capture_output=True, env={"PATH": os.environ["PATH"], **env}, check=False)
    return result, calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []


RESUME = """if first_install_awaits_owner_bootstrap; then
  resume_first_install_after_owner_bootstrap
fi
require_first_install_owner_bootstrap"""


def test_a_creating_release_without_the_owner_bootstrap_exits_3_and_keeps_the_app(tmp_path: Path) -> None:
    result, calls = _drive(tmp_path, "require_first_install_owner_bootstrap", CHECK_RC="3")
    assert result.returncode == 3, result.stderr
    assert "awaiting owner bootstrap --post-first-install" in result.stderr
    assert calls == ["check", "mark"]
    assert "CONTINUED" not in result.stdout
    # The EXIT trap's stop and the first-install deletion both stand down: the App is kept.
    assert "exit created=0 bound=0 armed=0" in result.stdout


def test_the_release_after_the_owner_bootstrap_resumes_at_the_same_point(tmp_path: Path) -> None:
    result, calls = _drive(tmp_path, RESUME, CREATED="0", STATUS="resume", CHECK_RC="0")
    assert result.returncode == 0, result.stderr
    assert calls == ["status", "check", "consume"]
    assert "resume the first install that awaited the owner bootstrap" in result.stdout
    # From here it is an ordinary first install, compensated as one.
    assert "CONTINUED" in result.stdout
    assert "exit created=1 bound=1 armed=1 resumed=0 state=first_install" in result.stdout


def test_a_resume_before_the_owner_bootstrap_stops_again_without_a_second_marker(tmp_path: Path) -> None:
    result, calls = _drive(tmp_path, RESUME, CREATED="0", STATUS="resume", CHECK_RC="3")
    assert result.returncode == 3 and calls == ["status", "check"]
    assert "exit created=0 bound=0 armed=0 resumed=1" in result.stdout


def test_an_undecided_check_refuses_and_leaves_the_compensation_armed(tmp_path: Path) -> None:
    result, calls = _drive(tmp_path, "require_first_install_owner_bootstrap", CHECK_RC="2")
    assert result.returncode == 2 and calls == ["check"]
    assert "exit created=1 bound=1 armed=1" in result.stdout, "a created App is deleted as today"


@pytest.mark.parametrize("phase", ("", "plan", "data"))
def test_only_a_release_reaches_the_gate(tmp_path: Path, phase: str) -> None:
    result, calls = _drive(tmp_path, RESUME, DEPLOY_PHASE=phase, CHECK_RC="3", STATUS="resume")
    assert result.returncode == 0 and calls == [] and "CONTINUED" in result.stdout


def test_the_gate_sits_after_the_apps_identity_and_before_its_first_proof() -> None:
    from tests.fixtures.deploy_script import deploy_entrypoint_text

    text = deploy_entrypoint_text()
    steps = text.index('step "preflight — check .env.local, databricks CLI, venv"')
    bind = text.index("tools.databricks.bundle_env deployment bind", steps)
    triplet = text.index('APP_SP_CLIENT_ID="dry-run-app-client-id"', bind)
    gate = text.index("\nrequire_first_install_owner_bootstrap\n", steps)
    role = text.index('step "converge App Lakebase OAuth role to exact LOGIN-only profile"', steps)
    assert bind < triplet < gate < role
    assert text.count("\nrequire_first_install_owner_bootstrap\n") == 1
    # The resume branch replaces only the deletion of the journal-owned App.
    recover = text.index('"$FIRST_INSTALL_JOURNAL_STATUS" == "recover"', steps)
    assert text.index("if first_install_awaits_owner_bootstrap; then", recover) < text.index(
        "      recover_interrupted_first_install_app\n", recover
    )
    assert "FIRST_INSTALL_RESUMED=0\n" in DEPLOY_SCRIPT.read_text(encoding="utf-8")


def test_the_exit_traps_stop_and_first_install_deletion_stand_down_for_the_kept_app(tmp_path: Path) -> None:
    from tests.fixtures.deploy_block_harness import function

    tripwire = tmp_path / "tripwire.log"
    stubs = "".join(
        f"{name}() {{ echo {name} >> {str(tripwire)!r}; return 1; }}\n"
        for name in (
            "refresh_first_install_journal_status",
            "compensate_quiesce_window_state",
            "compensate_contract_activation",
            "stop_and_quiesce_unproven_app",
            "recover_journaled_first_install_lakebase_bootstrap",
            "run_with_proof_signing_authority",
        )
    )
    script = (
        "set -euo pipefail\nDRY_RUN=0\nAPP_FAIL_CLOSED_ARMED=0\nAPP_FAIL_CLOSED_NAME=mip-app\n"
        "FIRST_INSTALL_APP_CREATED=0\nFIRST_INSTALL_APP_BOUND=0\nPYTHON=false\nRED='' YLW='' RST=''\n"
        + stubs
        + function("stop_app_after_failed_deploy")
        + function("cleanup_failed_first_install_app")
        + "stop_app_after_failed_deploy\ncleanup_failed_first_install_app\necho stood-down\n"
    )
    result = subprocess.run(["bash", "-c", script], text=True, capture_output=True, check=False)
    assert result.returncode == 0 and "stood-down" in result.stdout, result.stderr
    assert not tripwire.exists(), "no stop, quiesce, recovery or deletion ran"
