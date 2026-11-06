"""The deploy interpreter of the past-the-lease release sandbox (test only, M2-PR3).

tests/fixtures/release_sandbox.py points ``MIP_DEPLOY_PYTHON`` at a shell stub
that runs this file with the deploy's original arguments. Every invocation is
appended to ``SANDBOX_PY_LOG`` as one JSON line: its arguments and the
identity-bearing environment names it was handed. Then, by what it is:

* the real code, run through tests/fixtures/prestop_launcher.py against the
  fake estate: the client profile resolver (``-m tools.clients``), the
  pre-stop probes, every inline ``-c`` or heredoc program and every script
  this file does not answer itself;
* the reviewed function grant verifier: its real ``main`` with a fake
  workspace that holds exactly the grants the deploy applied (the CLI stub
  records them), so its tier-aware argument contract runs for real;
* the Lakebase role converger: a first install has no role yet, so each
  converge takes the create path, whose one-use creator first runs its real
  control check (tools/databricks/lakebase_oauth_role_bootstrap_control.py)
  on the credential the deploy handed it. A refusal fails the step loudly. A
  ``--recover-bootstrap-only`` call finds no creator to recover;
* the App payload builder, for real (it reads only the profile), so a test
  reads the payload each activation deployed;
* a scripted answer for each tool whose output the deploy reads (the lease,
  the first-install journal, the bundle summary, the release id, the stop
  outcome, the resource bindings, the Genie space id, the catalog bootstrap
  skip rule, the quiesce window plan of a first install and the core
  capture's binding env, M2-PR7b), kept in ``SANDBOX_STATE``;
* success for every other repository tool, which would otherwise need a live
  workspace (the check-only preflight tools, every convergence and audit).

Nothing here can reach Databricks: there is no credential, the CLI is a stub
and the SDK client is the estate's fake.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path
from typing import Any

LAUNCHER = Path(__file__).with_name("prestop_launcher.py")
# The names whose presence in a child's environment the test asserts on.
WATCHED = (
    "MIP_LAKEBASE_BOOTSTRAP_CONTROL_CLIENT_ID",
    "MIP_LAKEBASE_BOOTSTRAP_CONTROL_CLIENT_SECRET",
    "DATABRICKS_AGENT_RUNTIME_CLIENT_ID",
    "DATABRICKS_VERIFIER_CLIENT_ID",
    "BUNDLE_VAR_require_ai_gateway_verifier",
    "BUNDLE_VAR_ai_gateway_verifier_client_id",
)
APP = {
    "name": "mip-app",
    "id": "app-id-sandbox",
    "service_principal_client_id": "app-client-EXAMPLE",
    "service_principal_id": "app-scim-sandbox",
}
WAREHOUSE = "warehouse-1"
LEASE_ID = "lease-sandbox-1"
# Real code: the profile resolver and the probes, which read the fake estate.
REAL_MODULES = (
    "tools.clients",
    "tools.databricks.deploy_prestop_probes",
    "tools.databricks.app_deploy_payload",
)
CAPTURED_DEPLOYMENT = "deployment-green-sandbox"


def _state() -> dict[str, Any]:
    path = Path(os.environ["SANDBOX_STATE"])
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _save(state: dict[str, Any]) -> None:
    Path(os.environ["SANDBOX_STATE"]).write_text(json.dumps(state, indent=1), encoding="utf-8")


def _option(args: list[str], name: str) -> str:
    return args[args.index(name) + 1]


def _write_env(path: str, values: dict[str, str]) -> None:
    Path(path).write_text("".join(f"{k}={shlex.quote(v)}\n" for k, v in values.items()), encoding="utf-8")


def _delegate(args: list[str]) -> None:
    os.execv(sys.executable, [sys.executable, str(LAUNCHER), *args])


def _lease(args: list[str]) -> int:
    action = args[0]
    if action == "recovery-root":
        _write_env(
            _option(args, "--out-env"),
            {
                "MIP_APP_DEPLOYMENT_RECOVERY_ROOT": "",
                "MIP_APP_DEPLOYMENT_RECOVERY_LEASE_ID": "",
                "MIP_APP_DEPLOYMENT_RECOVERY_CANDIDATES": "",
            },
        )
    elif action == "acquire":
        state = _state()
        state["lease_writer"] = _option(args, "--writer-application-id")
        _save(state)
        _write_env(_option(args, "--out-env"), {"MIP_APP_DEPLOYMENT_LEASE_ID": LEASE_ID})
    elif action == "heartbeat":
        # deploy.sh probes this process before every command and kills it on exit.
        os.execv("/bin/sleep", ["sleep", "900"])
    elif action == "release":
        state = _state()
        state["lease_released"] = _option(args, "--lease-id")
        _save(state)
    return 0


def _journal(args: list[str]) -> int:
    state = _state()
    action = args[0]
    if action == "status":
        status = state.get("journal", "absent")
        ids = (
            (APP["id"], APP["service_principal_client_id"], APP["service_principal_id"])
            if status == "recover"
            else ("", "", "")
        )
        _write_env(
            _option(args, "--out-env"),
            dict(
                zip(
                    (
                        "MIP_FIRST_INSTALL_JOURNAL_STATUS",
                        "MIP_FIRST_INSTALL_APP_ID",
                        "MIP_FIRST_INSTALL_APP_CLIENT_ID",
                        "MIP_FIRST_INSTALL_APP_SCIM_ID",
                    ),
                    (status, *ids),
                    strict=True,
                )
            ),
        )
    elif action == "prepare":
        Path(_option(args, "--out-payload")).write_text(
            Path(_option(args, "--payload")).read_text(encoding="utf-8"), encoding="utf-8"
        )
        state["journal"] = "prepared"
    elif action == "claim":
        state["journal"] = "recover"
    elif action == "delete":
        state["journal"] = "absent"
        state["first_install_deleted"] = True
    _save(state)
    return 0


def _bundle_env(args: list[str]) -> int:
    if args[:1] == ["summary"]:
        print(
            json.dumps(
                {
                    "resources": {
                        "sql_warehouses": {"mip_serverless_sql": {"id": WAREHOUSE}},
                        "jobs": {"mip_lakebase_migrate": {"id": "1"}},
                        "apps": {"mip_app": {"source_code_path": "/Workspace/sandbox/files"}},
                    },
                    "workspace": {"file_path": "/Workspace/sandbox/files"},
                }
            )
        )
    return 0


def _function_grants(args: list[str]) -> int:
    """The real verifier over the grants the CLI stub saw applied."""

    from types import SimpleNamespace

    from tools.databricks import verify_reviewed_function_execute_grants as verifier

    granted = {
        (statement.split(" TO ", 1)[1].strip("`"), statement.split(" ON FUNCTION ", 1)[1].split(" TO ")[0])
        for statement in _state().get("statements", [])
        if statement.startswith("GRANT EXECUTE ON FUNCTION ")
    }

    def get_effective(_kind: str, full_name: str, *, principal: str, **_kwargs: Any) -> Any:
        privileges = [SimpleNamespace(privilege="EXECUTE", inherited_from_type="", inherited_from_name="")]
        held = (principal, full_name) in granted
        return SimpleNamespace(
            privilege_assignments=[SimpleNamespace(principal=principal, privileges=privileges if held else [])],
            next_page_token=None,
        )

    verifier.deployment_workspace_client = lambda: SimpleNamespace(  # type: ignore[assignment]
        grants=SimpleNamespace(get_effective=get_effective)
    )
    return verifier.main(args)


def _converge_lakebase_role(args: list[str]) -> int:
    """The create path's first check, for real: no role exists on a first install."""

    from tools.databricks.lakebase_oauth_role_bootstrap_control import bootstrap_control_credentials

    target = _option(args, "--application-id")
    if "--recover-bootstrap-only" in args:
        return 0  # the compensation's recovery of a creator this run never left behind
    try:
        control, _secret = bootstrap_control_credentials(os.environ, target_application_id=target)
    except RuntimeError as exc:
        print(f"[sandbox] converge_lakebase_oauth_role --application-id {target}: {exc}", file=sys.stderr)
        return 1
    state = _state()
    state.setdefault("lakebase_role_creates", []).append(
        {"role_contract": _option(args, "--role-contract"), "target": target, "control": control}
    )
    _save(state)
    return 0


def _core_rollback(args: list[str]) -> int:
    """The core capture (M2-PR7b): its own tests prove it; here, record it and bind green."""

    state = _state()
    state.setdefault("core_rollback", []).append(args[0])
    _save(state)
    failure = json.loads(os.environ.get("SANDBOX_FAIL_JSON") or "{}").get(args[0])
    if failure:
        print(f"[mip-core-blue] REFUSED sandbox: {args[0]} fails (injected)", file=sys.stderr)
        return int(failure)
    if "--out-env" in args:
        _write_env(
            _option(args, "--out-env"),
            {
                "MIP_APP_ROLLBACK_RECORD_VERSION": "7",
                "MIP_APP_ROLLBACK_TIER": "core",
                "MIP_APP_ROLLBACK_PROVENANCE": "captured",
                "MIP_APP_ROLLBACK_DEPLOYMENT_ID": CAPTURED_DEPLOYMENT,
            },
        )
    return 0


def main() -> int:
    args = sys.argv[1:]
    with open(os.environ["SANDBOX_PY_LOG"], "a", encoding="utf-8") as log:
        log.write(json.dumps({"argv": args, "env": {name: os.environ.get(name) for name in WATCHED}}) + "\n")
    if args[:1] == ["-c"] and args[1] == "import boto3, mlflow":
        return 0
    if args[:1] == ["tools/render_sql.py"]:
        return 0
    if args[:1] == ["tools/oauth_m2m_mint.py"]:
        Path(_option(args, "--output-file")).write_text("token-PLACEHOLDER\n", encoding="utf-8")
        return 0
    if args[:1] != ["-m"]:
        _delegate(args)
    module, rest = args[1], args[2:]
    if module in REAL_MODULES:
        _delegate(args)
    handlers = {
        "tools.databricks.app_deployment_lease": _lease,
        "tools.databricks.app_first_install_journal": _journal,
        "tools.databricks.bundle_env": _bundle_env,
        "tools.databricks.verify_reviewed_function_execute_grants": _function_grants,
        "tools.databricks.converge_lakebase_oauth_role": _converge_lakebase_role,
        "tools.databricks.app_core_rollback": _core_rollback,
    }
    if module in handlers:
        return handlers[module](rest)
    if module == "tools.databricks.bundle_state":
        return 0  # a first install: this deployer's state binds no warehouse yet
    if module == "tools.databricks.app_release_contract":
        print(f"{_option(rest, '--expected-commit')[:12]}-{'0' * 12}")
        return 0
    if module == "tools.databricks.provision_genie_space" and "--plan-pin" not in rest:
        Path("genie").mkdir(exist_ok=True)
        Path("genie/space_id.txt").write_text("space-governed\n", encoding="utf-8")
        return 0
    if module == "tools.databricks.app_resource_bindings" and rest[:1] == ["build"]:
        Path(_option(rest, "--out")).write_text("{}\n", encoding="utf-8")
        return 0
    if module == "tools.databricks.catalog_bootstrap_skip_rule":
        return 3  # a first install has no live catalog to equal the bootstrap: run it
    if module == "tools.databricks.quiesce_window" and rest[:1] == ["plan"]:
        # A first install: no App, so no window (M0-PR8; its own tests cover the plan).
        Path(_option(rest, "--out")).write_text('{"code": "not_applicable", "reasons": []}\n', encoding="utf-8")
        return 0
    if module == "tools.databricks.stop_app_fail_closed":
        if "--out-env" in rest:
            _write_env(_option(rest, "--out-env"), {"MIP_APP_STOP_OUTCOME": "stopped"})
        return 0
    if module.startswith("tools."):
        return 0  # a tool that would need a live workspace or build tooling
    _delegate(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
