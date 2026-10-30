"""Every pre-stop refusal exits 2 from the real deploy.sh before the lease and any stop (M0-PR5).

Each case runs the real ``scripts/deploy.sh`` (a full, non-dry run) in an
offline sandbox checkout: a stub ``databricks`` CLI that answers ``--version``
and the Supervisor listing from the fake estate and records anything else, and
a stub deploy interpreter (``MIP_DEPLOY_PYTHON``) that runs every real Python
step through ``tests/fixtures/prestop_launcher.py`` against that estate. The
steps before the probes that need a live workspace or build tooling (the pin
and closure checks, SQL render, bundle validate and plan, the runtime-secret
and pii-salt checks, the plan gate) answer success; the signed lease and every
App stop primitive are markers that end the run.

For every refusal code the run must exit 2 with that code printed, call no
stop primitive, leave the fake App RUNNING on the same deployment, and never
reach the lease. The positive control, a clean estate, reaches the lease.
Nothing here can reach Databricks: there is no credential, the CLI is a stub,
and the SDK client is the fake.
"""

from __future__ import annotations

import copy
import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures.client_profiles import IDENTITY_IDS, env_file_text, paychex_shaped_profile
from tests.fixtures.deploy_sandbox import INHERITED_PREFIXES, sandbox
from tests.fixtures.prestop_estate import (
    APP_CLIENT,
    BLUE_SUPERVISOR_ENDPOINT,
    CANONICAL,
    GOVERNED_SPACE,
    LEGACY_GATEWAY,
    RUNTIME_CLIENT,
    VERIFIER_CLIENT,
    acl_entry,
    clean_estate,
    rename_record_supervisor,
    write_estate,
)
from tools.databricks import prestop_refusals as codes

REPO = Path(__file__).resolve().parents[2]
# Canned success for the steps the offline sandbox cannot run for real.
CANNED = (
    "-m tools.clients.live_checks",
    "-m tools.python_deps_check",
    "-m tools.requirements_digest",
    "-m tools.databricks.app_runtime_closure",
    "-c import boto3, mlflow",
    "-m tools.frontend_deps_check",
    "-m tools.databricks.provision_runtime_secrets --check",
    "tools/render_sql.py",
    "-m tools.databricks.bundle_env",
    "-m tools.databricks.pii_salt_scope check",
    "-m tools.databricks.bundle_plan_gate check",
)
LEASE = "-m tools.databricks.app_deployment_lease"
STOPS = ("-m tools.databricks.stop_app_fail_closed", "-m tools.databricks.converge_app_release_access")


def _fake_python(tmp_path: Path) -> Path:
    canned = "\n".join(f"  {shlex.quote(prefix)}*) exit 0 ;;" for prefix in CANNED)
    stops = "\n".join(
        f"  {shlex.quote(prefix)}*) echo stop >> \"$PRESTOP_MARKERS\"; exit 97 ;;"
        for prefix in STOPS
    )
    script = tmp_path / "fake-python.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        'printf \'%s\\n\' "$*" >> "$PRESTOP_PY_LOG"\n'
        'case "$*" in\n'
        f"{canned}\n"
        "  '-m tools.databricks.agent_proxy_credential_bundle all-fields'*)\n"
        f"    printf '{IDENTITY_IDS['proxy']}\\tproxy-credential-EXAMPLE\\tproxy-secret-EXAMPLE\\n'; exit 0 ;;\n"
        f"  {shlex.quote(LEASE)}*) echo lease >> \"$PRESTOP_MARKERS\"; exit 97 ;;\n"
        f"{stops}\n"
        "esac\n"
        f'exec {shlex.quote(sys.executable)} "$PRESTOP_LAUNCHER" "$@"\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def _stub_cli(bin_dir: Path) -> None:
    cli = bin_dir / "databricks"
    cli.write_text(
        "#!/usr/bin/env bash\n"
        'printf \'%s\\n\' "$*" >> "$DATABRICKS_STUB_LOG"\n'
        'case "$*" in\n'
        "  --version) echo 'Databricks CLI v-test'; exit 0 ;;\n"
        "  'supervisor-agents list-supervisor-agents -o json')\n"
        f"    exec {shlex.quote(sys.executable)} -c "
        '\'import json,os; print(json.dumps(json.load(open(os.environ["MIP_TEST_PRESTOP_ESTATE"]))["agents"]))\' ;;\n'
        "  'apps stop'*) echo stop >> \"$PRESTOP_MARKERS\"; exit 97 ;;\n"
        "esac\n"
        'echo "unexpected Databricks CLI call: $*" >&2\n'
        "exit 97\n",
        encoding="utf-8",
    )
    cli.chmod(0o755)


def _deploy(
    tmp_path: Path, estate: dict[str, Any], **env: str
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any], list[str], list[str]]:
    checkout, base_env, cli_log = sandbox(tmp_path)
    # The sandbox's synthetic client profile, with the demo install's weekly
    # refresh off: a service-principal deployer cannot receive its failure mail.
    profile = paychex_shaped_profile(
        MIP_SCHEDULED_REFRESH="off", MIP_SCHEDULED_REFRESH_RECIPIENTS=None
    )
    (checkout / ".env.local").write_text(
        env_file_text({**profile, "DATABRICKS_WAREHOUSE_ID": "warehouse-1"}), encoding="utf-8"
    )
    _stub_cli(tmp_path / "bin")
    markers = tmp_path / "markers.log"
    estate_path = write_estate(tmp_path / "estate.json", estate)
    run_env = {
        name: value for name, value in base_env.items() if not name.startswith(INHERITED_PREFIXES)
    }
    run_env.update(
        {
            "PATH": base_env["PATH"],
            "HOME": str(tmp_path),
            "DATABRICKS_CONFIG_FILE": str(tmp_path / "no-databrickscfg"),
            "DATABRICKS_STUB_LOG": str(cli_log),
            "DATABRICKS_HOST": profile["DATABRICKS_HOST"],
            "DATABRICKS_TOKEN": "token-PLACEHOLDER",
            "MIP_DEPLOY_PYTHON": str(_fake_python(tmp_path)),
            "MIP_TEST_PRESTOP_ESTATE": str(estate_path),
            "PRESTOP_LAUNCHER": str(checkout / "tests" / "fixtures" / "prestop_launcher.py"),
            "PRESTOP_MARKERS": str(markers),
            "PRESTOP_PY_LOG": str(tmp_path / "python.log"),
            "MLFLOW_DISABLE_AGENT_HINT": "1",
            # The M2M client ids are the profile's roles; their secrets, the
            # signing keys and the approvals come from the profile itself.
            "DATABRICKS_CLIENT_ID": IDENTITY_IDS["normal"],
            "DATABRICKS_OPERATOR2_CLIENT_ID": IDENTITY_IDS["operator2"],
            "DATABRICKS_ADMIN_CLIENT_ID": IDENTITY_IDS["admin"],
            "DATABRICKS_RELEASE_PROBE_CLIENT_ID": IDENTITY_IDS["release_probe"],
            "DATABRICKS_VERIFIER_CLIENT_ID": VERIFIER_CLIENT,
            "DATABRICKS_AGENT_RUNTIME_CLIENT_ID": RUNTIME_CLIENT,
            "DATABRICKS_ACCOUNT_CLIENT_ID": IDENTITY_IDS["account_principal"],
            **env,
        }
    )
    result = subprocess.run(
        ["bash", "scripts/deploy.sh", "-t", "client", "--no-confirm", "--skip-silver", "--skip-smoke"],
        cwd=checkout,
        env=run_env,
        text=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        check=False,
        timeout=600,
    )
    after = json.loads(estate_path.read_text(encoding="utf-8"))
    reached = markers.read_text(encoding="utf-8").split() if markers.exists() else []
    cli_calls = cli_log.read_text(encoding="utf-8").splitlines() if cli_log.exists() else []
    return result, after, reached, cli_calls


def _assert_refused_before_the_lease(
    result: subprocess.CompletedProcess[str],
    before: dict[str, Any],
    after: dict[str, Any],
    reached: list[str],
    cli_calls: list[str],
    code: str,
) -> None:
    detail = result.stdout[-3000:] + result.stderr[-6000:]
    assert result.returncode == 2, detail
    assert f"[mip-prestop] REFUSED: {code}: " in result.stderr, detail
    assert reached == [], f"reached {reached}: a stop primitive or the lease ran"
    assert not any(call.startswith("apps stop") for call in cli_calls), cli_calls
    assert after["app"]["state"] == "RUNNING"
    assert after["app"]["active_deployment"] == before["app"]["active_deployment"]
    assert not any(call.startswith("STOP ") for call in after["calls"]), "the SDK stopped the App"


def _owner_equivalent_human(estate: dict[str, Any]) -> None:
    estate["grants"]["catalog/mip"].append(
        {"principal": "steward@example.invalid", "privileges": ["MANAGE", "ALL_PRIVILEGES"]}
    )


def _foreign_genie_manager(estate: dict[str, Any]) -> None:
    estate["genie"][GOVERNED_SPACE].append(
        acl_entry("service_principal_name", "foreign-app-sp-EXAMPLE", "CAN_MANAGE")
    )


def _legacy_gateway_query(estate: dict[str, Any]) -> None:
    estate["serving"][LEGACY_GATEWAY] = {
        "id": "endpoint-legacy",
        "acl": [acl_entry("service_principal_name", APP_CLIENT, "CAN_QUERY")],
    }


def _renamed_record_supervisor_query(estate: dict[str, Any]) -> None:
    rename_record_supervisor(estate)
    estate["serving"][BLUE_SUPERVISOR_ENDPOINT]["acl"] = [
        acl_entry("service_principal_name", APP_CLIENT, "CAN_QUERY")
    ]


def _unacknowledged_human_supervisor(estate: dict[str, Any]) -> None:
    estate["agents"][0]["creator"] = "operator@example.invalid"


def _reserved_name(estate: dict[str, Any]) -> None:
    estate["agents"].append(
        {
            "supervisor_agent_id": "supervisor-squatter",
            "display_name": f"{CANONICAL} [mip-agent-runtime]",
            "creator": "operator@example.invalid",
            "endpoint_name": "mas-squatter-endpoint",
            "create_time": "2026-02-02T03:04:05.678Z",
        }
    )


def _verifier_genie_grant(estate: dict[str, Any]) -> None:
    estate["genie"][GOVERNED_SPACE].append(
        acl_entry("service_principal_name", VERIFIER_CLIENT, "CAN_VIEW")
    )


def _users_genie_grant(estate: dict[str, Any]) -> None:
    estate["genie"]["space-other-team"] = [acl_entry("group_name", "users", "CAN_RUN")]


def _unresolved_runtime(estate: dict[str, Any]) -> None:
    del estate["principals"][RUNTIME_CLIENT]


def _owner_outside_policy(estate: dict[str, Any]) -> None:
    estate["metastore"]["owner"] = "someone-else@example.invalid"
    estate["principals"]["someone-else@example.invalid"] = {"kind": "user", "id": "someone-id"}


def _set(path: tuple[str, ...], value: Any) -> Any:
    def edit(estate: dict[str, Any]) -> None:
        target = estate
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value

    return edit


CASES: tuple[tuple[str, Any, dict[str, str]], ...] = (
    (codes.SUPERVISOR_RETIREMENT, _unacknowledged_human_supervisor, {}),
    (codes.RESERVED_REPLACEMENT_NAME, _reserved_name, {}),
    (codes.AUDITED_PRINCIPAL_UNRESOLVED, _unresolved_runtime, {}),
    (codes.GENIE_GROUP_GRANT, _users_genie_grant, {}),
    (codes.VERIFIER_GENIE_GRANT, _verifier_genie_grant, {}),
    (
        codes.ROLLBACK_RECORD_ABSENT,
        _set(("record",), "no server-owned last-good App rollback contract exists; run it"),
        {},
    ),
    (codes.ROLLBACK_RECORD_PRESENT, _set(("rebase_absence",), "present"), {"MIP_REBASE_UNVERIFIED_APP": "1"}),
    (codes.ROLLBACK_RECORD_TAMPERED, _set(("record",), "App rollback contract signature is invalid"), {}),
    (
        codes.ROLLBACK_RECORD_DRIFT,
        _set(("record",), "App service-principal identity drifted from the signed rollback contract"),
        {},
    ),
    (codes.ROLLBACK_RECORD_DRIFT, _set(("app", "pending"), True), {}),
    (codes.OWNER_POLICY, _owner_outside_policy, {}),
    (codes.OWNER_EQUIVALENT, _owner_equivalent_human, {}),
    (codes.OWNER_EQUIVALENT, _foreign_genie_manager, {}),
    (codes.SERVING_ENDPOINT, _legacy_gateway_query, {}),
    (codes.SERVING_ENDPOINT, _renamed_record_supervisor_query, {}),
    (codes.GENIE_BUDGET, _set(("genie_turn",), "BUDGET"), {}),
    (codes.PROBE_ERROR, _set(("genie_errors",), [GOVERNED_SPACE]), {}),
)


def test_every_reviewed_refusal_code_has_a_matrix_case() -> None:
    covered = {code for code, _edit, _env in CASES}
    assert covered == codes.REFUSAL_CODES


def test_a_clean_estate_reaches_the_lease(tmp_path: Path) -> None:
    result, after, reached, cli_calls = _deploy(tmp_path, clean_estate())

    detail = result.stdout[-3000:] + result.stderr[-6000:]
    assert "[mip-prestop] pre-stop probes passed (read-only)" in result.stdout, detail
    assert reached == ["lease"], detail
    assert after["app"]["state"] == "RUNNING"
    assert not any(call.startswith("apps stop") for call in cli_calls)
    # Every probe ran against the fake estate, read-only: the Supervisor listing
    # through the stub CLI, the governed space's ACL and the governed catalog's
    # grants through the fake SDK client.
    assert "supervisor-agents list-supervisor-agents -o json" in cli_calls
    assert f"GET /api/2.0/permissions/genie/{GOVERNED_SPACE}" in after["calls"]
    assert "GET grants/catalog/mip" in after["calls"]
    assert all(call.startswith("GET ") for call in after["calls"]), after["calls"]
    for note in (
        "signed last-good record verified",
        "approved-owner policy holds",
        "every owner-equivalent grantee is an approved principal",
        "App serving access is limited to record-pinned endpoints (1 reached)",
        f"one Genie turn on the governed space {GOVERNED_SPACE} completed",
    ):
        assert note in result.stdout, note


def test_an_active_deployment_other_than_the_record_reaches_the_step_0a_restore(
    tmp_path: Path,
) -> None:
    # The re-run that recovers a run killed after green activation: step 0a's
    # ensure restores the record's deployment, so no probe refuses it.
    estate = clean_estate()
    estate["app"]["active_deployment"] = "deployment-green"

    result, after, reached, _cli_calls = _deploy(tmp_path, estate)

    detail = result.stdout[-3000:] + result.stderr[-6000:]
    assert reached == ["lease"], detail
    assert "the active deployment deployment-green is not the record's deployment-blue" in result.stdout
    assert after["app"]["state"] == "RUNNING"
    assert after["app"]["active_deployment"] == "deployment-green"


def test_the_approval_lists_reach_the_probe_through_the_environment_only(
    tmp_path: Path,
) -> None:
    # The profile approves owner@summit.example as an owner and
    # genie-managers-alpha as a Genie manager (tests/fixtures/client_profiles.py).
    # Both lists are IDENTIFYING, and deploy.sh echoes every command it runs.
    estate = clean_estate()
    estate["grants"]["catalog/mip"].append(
        {"principal": "owner@summit.example", "privileges": ["MANAGE"]}
    )
    estate["genie"][GOVERNED_SPACE].append(
        acl_entry("group_name", "genie-managers-alpha", "CAN_MANAGE")
    )

    result, _after, reached, _cli_calls = _deploy(tmp_path, estate)

    detail = result.stdout[-3000:] + result.stderr[-6000:]
    assert reached == ["lease"], detail
    assert "every owner-equivalent grantee is an approved principal" in result.stdout
    for value in ("owner@summit.example", "genie-managers-alpha"):
        assert value not in result.stdout + result.stderr, value


@pytest.mark.parametrize(
    ("code", "edit", "env"),
    CASES,
    ids=[f"{code}-{getattr(edit, '__name__', 'set')}" for code, edit, _env in CASES],
)
def test_each_refusal_exits_2_before_the_lease_and_any_stop(
    tmp_path: Path, code: str, edit: Any, env: dict[str, str]
) -> None:
    estate = copy.deepcopy(clean_estate())
    edit(estate)

    result, after, reached, cli_calls = _deploy(tmp_path, estate, **env)

    _assert_refused_before_the_lease(result, estate, after, reached, cli_calls, code)
    assert "nothing was changed and the App was not stopped by this run" in result.stderr


@pytest.mark.parametrize("value", ("1", "true"))
def test_a_remediation_request_refuses_before_any_databricks_call(
    tmp_path: Path, value: str
) -> None:
    result, after, reached, cli_calls = _deploy(
        tmp_path, clean_estate(), MIP_REMEDIATE_FOREIGN_CATALOG_BINDINGS=value
    )

    assert result.returncode == 2, result.stderr[-3000:]
    assert "MIP_REMEDIATE_FOREIGN_CATALOG_BINDINGS is refused" in result.stderr
    assert reached == [] and cli_calls == [], "refused before any Databricks call"
    assert after["calls"] == [] and after["app"]["state"] == "RUNNING"
