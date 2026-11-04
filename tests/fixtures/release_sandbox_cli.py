"""The ``databricks`` CLI of the past-the-lease release sandbox (test only, M2-PR3).

Answers exactly the calls a first install makes through the grant and
reconcile steps, and fails loudly (exit 97) on anything else. Each call is
appended to ``DATABRICKS_STUB_LOG`` with the bundle variables it inherited,
so a test can read what ``databricks bundle run mip_lakebase_migrate`` would
have rendered. SQL statements are recorded in ``SANDBOX_STATE`` and answered
SUCCEEDED; the treatment table's property read returns its reviewed values.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from tests.fixtures.release_sandbox_python import APP, WATCHED, _save, _state

TREATMENT_PROPERTIES = [
    ["delta.appendOnly", "true"],
    ["delta.logRetentionDuration", "interval 2555 days"],
    ["delta.deletedFileRetentionDuration", "interval 2555 days"],
]


def _app(state: dict[str, Any]) -> dict[str, Any]:
    return {**APP, "compute_status": {"state": "STOPPED"}, "url": "https://mip-app.example.invalid"}


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
    if args == ["supervisor-agents", "list-supervisor-agents", "-o", "json"]:
        print("[]")
        return 0
    if args == ["apps", "list", "-o", "json"]:
        print(json.dumps([_app(state)] if state.get("app_created") else []))
        return 0
    if args[:2] == ["apps", "create"]:
        state["app_created"] = True
        _save(state)
        print(json.dumps(_app(state)))
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
