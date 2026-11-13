"""The ``databricks`` CLI of the past-the-lease release sandbox (test only, M2-PR3).

Answers exactly the calls a first install makes, through the grant and
reconcile steps and, for the core release (M2-PR7b), on through the publish,
the one activation (``apps start`` of the stopped App, then ``apps deploy``,
whose payload is recorded) and the data steps; it fails loudly (exit 97) on
anything else, the Supervisor agents listing and every serving-endpoint call
included: a core release never reads those APIs. Each call is
appended to ``DATABRICKS_STUB_LOG`` with the bundle variables it inherited,
so a test can read what ``databricks bundle run mip_lakebase_migrate`` would
have rendered. SQL statements are recorded in ``SANDBOX_STATE`` and answered
SUCCEEDED; the treatment table's property read returns its reviewed values.

``apps start`` and ``apps deploy`` print what the real CLI prints on stdout
when the mutation returns (``cmdio.Render`` of the App and of the created
deployment, JSON in the default text mode): the App with its service
principal's name and OAuth client ids, and the deployment with its id, its
source path and its creator. No read hands the shell those values first, so
a test can prove that neither result reaches the job log.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from tests.fixtures.release_sandbox_python import APP, CAPTURED_DEPLOYMENT, WATCHED, _save, _state

TREATMENT_PROPERTIES = [
    ["delta.appendOnly", "true"],
    ["delta.logRetentionDuration", "interval 2555 days"],
    ["delta.deletedFileRetentionDuration", "interval 2555 days"],
]


BUNDLE_FILES = "/Workspace/sandbox/files"
# What only a mutation's rendered result carries (no ``apps get`` parse reads these).
START_RESULT_ONLY = {
    "service_principal_name": "app-sp-name-sandbox",
    "oauth2_app_client_id": "oauth2-client-sandbox",
    "oauth2_app_integration_id": "oauth2-integration-sandbox",
}
DEPLOY_RESULT_ONLY = {
    "creator": "deployment-creator-sandbox",
    "deployment_artifacts": {"source_code_path": "/Workspace/sandbox/deployment-artifacts-sandbox"},
}


def _app(state: dict[str, Any]) -> dict[str, Any]:
    compute = "ACTIVE" if state.get("app_started") else "STOPPED"
    return {**APP, "compute_status": {"state": compute}, "url": "https://mip-app.example.invalid"}


def _bundle_summary() -> dict[str, Any]:
    deployer = json.loads(os.environ["MIP_TEST_DEPLOYER_JSON"])
    return {
        "workspace": {"file_path": BUNDLE_FILES, "current_user": {"userName": deployer}},
        "resources": {"apps": {"mip_app": {"source_code_path": BUNDLE_FILES}}},
    }


def _statement(body: dict[str, Any]) -> dict[str, Any]:
    state = _state()
    statement = str(body["statement"])
    state.setdefault("statements", []).append(statement)
    _save(state)
    rows: list[list[str]] = []
    if statement.startswith("SHOW TBLPROPERTIES "):
        rows = TREATMENT_PROPERTIES
    elif statement.startswith("SELECT COUNT(*) FROM system.information_schema.schemata"):
        rows = [["1"]]
    return {"status": {"state": "SUCCEEDED"}, "result": {"data_array": rows}}


def main() -> int:
    args = sys.argv[1:]
    with open(os.environ["DATABRICKS_STUB_LOG"], "a", encoding="utf-8") as log:
        log.write(json.dumps({"argv": args, "env": {name: os.environ.get(name) for name in WATCHED}}) + "\n")
    state = _state()
    if args == ["--version"]:
        print("Databricks CLI v-test")
        return 0
    if args == ["apps", "list", "-o", "json"]:
        print(json.dumps([_app(state)] if state.get("app_created") else []))
        return 0
    if args[:2] == ["apps", "create"]:
        state["app_created"] = True
        _save(state)
        print(json.dumps(_app(state)))
        return 0
    if args == ["apps", "start", APP["name"]]:
        state["app_started"] = True
        _save(state)
        print(json.dumps({**_app(state), **START_RESULT_ONLY}))
        return 0
    if args[:3] == ["apps", "deploy", APP["name"]] and args[3] == "--json":
        payload = json.loads(Path(args[4].removeprefix("@")).read_text(encoding="utf-8"))
        state.setdefault("deploys", []).append(payload)
        _save(state)
        created = {"deployment_id": CAPTURED_DEPLOYMENT, "mode": "SNAPSHOT", "status": {"state": "SUCCEEDED"}}
        print(json.dumps({**created, **DEPLOY_RESULT_ONLY}))
        return 0
    if args == ["bundle", "summary", "-t", "client", "-o", "json"]:
        print(json.dumps(_bundle_summary()))
        return 0
    if args[:3] == ["apps", "get", APP["name"]]:
        if not state.get("app_created"):
            print("App does not exist", file=sys.stderr)
            return 1
        print(json.dumps(_app(state)))
        return 0
    if args[:2] == ["bundle", "run"] and args[3:] == ["-t", "client"]:
        state.setdefault("bundle_runs", []).append(args[2])
        _save(state)
        return 0
    if args[:4] == ["api", "post", "/api/2.0/sql/statements/", "--json"]:
        print(json.dumps(_statement(json.loads(args[4]))))
        return 0
    print(f"unexpected Databricks CLI call: {args}", file=sys.stderr)
    return 97


if __name__ == "__main__":
    raise SystemExit(main())
