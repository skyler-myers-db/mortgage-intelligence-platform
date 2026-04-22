"""Real-infra drill helpers — stop/start SQL warehouse + Lakebase via SDK.

Used by ``tools/kill_drill/run_drill.sh`` when the operator passes
``--target warehouse-real`` or ``--target lakebase-real``. These targets
**actually stop production infrastructure** to prove the degraded-state
contract, so they are gated behind the ``--i-really-mean-it`` flag in
the shell script. This module is just the typed SDK shim; all
policy/safety checks live in the shell entry point so a misused import
here still fails loudly at the CLI layer.

Why a dedicated helper?
-----------------------
Bundling the SDK calls in a Python file (rather than inlining
``databricks warehouses stop`` subprocess calls in bash) gives us:

* One auth story — the SDK resolves ``DATABRICKS_HOST`` +
  ``DATABRICKS_TOKEN`` / profile / OAuth identically to the rest of the
  app, so "what auth does the drill run with" has the same answer as
  "what auth does the backend run with".
* Typed waiters — ``warehouses.start_and_wait`` + state polling against
  ``DatabaseInstanceState.AVAILABLE`` is provably correct, where a bash
  `jq | grep RUNNING` loop is brittle.
* Idempotency — if the warehouse is already STOPPED on entry we skip
  the stop call; drills can be restarted mid-run without side effects.

SDK surface relied on (pinned against the databricks-sdk in
requirements.txt; see tests/unit/test_provision_m2m_oauth.py and the
nightly parity job for live coverage):

* ``w.warehouses.get(id)`` / ``.stop(id)`` / ``.start_and_wait(id, timeout=...)``
* ``w.database.get_database_instance(name)`` /
  ``.update_database_instance(name, DatabaseInstance(stopped=True|False),
   update_mask="stopped")``

Note on Lakebase stop/start: the SDK does **not** expose explicit
``stop_database_instance`` / ``start_database_instance`` methods. The
canonical pattern is ``update_database_instance`` with
``stopped=true|false`` and an explicit ``update_mask`` — mirror of the
REST PATCH API at ``/api/2.0/database/instances/{name}``. We then poll
the ``.state`` field against ``DatabaseInstanceState`` until it reaches
``STOPPED`` or ``AVAILABLE``.

Usage
-----
    python tools/kill_drill/real_infra.py stop   warehouse <id>
    python tools/kill_drill/real_infra.py start  warehouse <id>
    python tools/kill_drill/real_infra.py stop   lakebase  <instance_name>
    python tools/kill_drill/real_infra.py start  lakebase  <instance_name>

Exit codes
----------
* 0 — resource is now in the requested end state (or was already there).
* 1 — timeout waiting for the end state.
* 2 — bad arguments or SDK surface missing.
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any

_TARGET_WAREHOUSE = "warehouse"
_TARGET_LAKEBASE = "lakebase"
_DEFAULT_TIMEOUT_S = 300.0
_POLL_INTERVAL_S = 5.0


def _diag(msg: str) -> None:
    print(f"[mip-kill-drill-infra] {msg}", file=sys.stderr, flush=True)


def _client() -> Any:
    from databricks.sdk import WorkspaceClient  # type: ignore

    return WorkspaceClient()


# ---------------------------------------------------------------------------
# Warehouse
# ---------------------------------------------------------------------------


def _warehouse_state(w: Any, warehouse_id: str) -> str:
    wh = w.warehouses.get(warehouse_id)
    state = getattr(wh, "state", None)
    return getattr(state, "value", str(state)) if state is not None else "UNKNOWN"


def stop_warehouse(
    warehouse_id: str,
    *,
    client: Any | None = None,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> str:
    """Stop a SQL warehouse. Idempotent: a STOPPED warehouse is a no-op."""
    w = client if client is not None else _client()
    state = _warehouse_state(w, warehouse_id)
    _diag(f"warehouse {warehouse_id} pre-stop state={state}")
    if state in ("STOPPED", "STOPPING"):
        _diag("already stopped/stopping; skipping stop call")
        return state
    w.warehouses.stop(warehouse_id)
    return _await_warehouse_state(w, warehouse_id, "STOPPED", timeout_s)


def start_warehouse(
    warehouse_id: str,
    *,
    client: Any | None = None,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> str:
    """Start a SQL warehouse and wait for RUNNING."""
    w = client if client is not None else _client()
    state = _warehouse_state(w, warehouse_id)
    _diag(f"warehouse {warehouse_id} pre-start state={state}")
    if state == "RUNNING":
        _diag("already running; skipping start call")
        return state
    # start_and_wait is idempotent on the SDK side — safe even if the
    # warehouse is already STARTING.
    w.warehouses.start_and_wait(warehouse_id, timeout=_timeout_td(timeout_s))
    return _warehouse_state(w, warehouse_id)


def _await_warehouse_state(w: Any, warehouse_id: str, target: str, timeout_s: float) -> str:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        current = _warehouse_state(w, warehouse_id)
        if current == target:
            _diag(f"warehouse {warehouse_id} reached {target}")
            return current
        _diag(f"warehouse {warehouse_id} state={current}; waiting for {target}")
        time.sleep(_POLL_INTERVAL_S)
    final = _warehouse_state(w, warehouse_id)
    raise TimeoutError(
        f"warehouse {warehouse_id} did not reach {target} within {timeout_s}s (last={final})"
    )


# ---------------------------------------------------------------------------
# Lakebase
# ---------------------------------------------------------------------------


def _lakebase_state(w: Any, instance_name: str) -> tuple[str, bool | None]:
    inst = w.database.get_database_instance(name=instance_name)
    state = getattr(inst, "state", None)
    return (
        getattr(state, "value", str(state)) if state is not None else "UNKNOWN",
        getattr(inst, "stopped", None),
    )


def stop_lakebase(
    instance_name: str,
    *,
    client: Any | None = None,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> str:
    """Stop a Lakebase database instance via PATCH stopped=true.

    The SDK exposes stop/start as ``update_database_instance`` with
    ``stopped`` toggled and an explicit ``update_mask="stopped"`` — NOT a
    dedicated stop/start verb. We then poll until ``state=STOPPED``.
    """
    from databricks.sdk.service.database import DatabaseInstance  # type: ignore

    w = client if client is not None else _client()
    state, stopped = _lakebase_state(w, instance_name)
    _diag(f"lakebase {instance_name} pre-stop state={state} stopped={stopped}")
    if state == "STOPPED" or stopped is True:
        _diag("already stopped; skipping update call")
        return state
    w.database.update_database_instance(
        name=instance_name,
        database_instance=DatabaseInstance(name=instance_name, stopped=True),
        update_mask="stopped",
    )
    return _await_lakebase_state(w, instance_name, "STOPPED", timeout_s)


def start_lakebase(
    instance_name: str,
    *,
    client: Any | None = None,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> str:
    from databricks.sdk.service.database import DatabaseInstance  # type: ignore

    w = client if client is not None else _client()
    state, stopped = _lakebase_state(w, instance_name)
    _diag(f"lakebase {instance_name} pre-start state={state} stopped={stopped}")
    if state == "AVAILABLE" and stopped is False:
        _diag("already available; skipping update call")
        return state
    w.database.update_database_instance(
        name=instance_name,
        database_instance=DatabaseInstance(name=instance_name, stopped=False),
        update_mask="stopped",
    )
    return _await_lakebase_state(w, instance_name, "AVAILABLE", timeout_s)


def _await_lakebase_state(w: Any, instance_name: str, target: str, timeout_s: float) -> str:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        current, stopped = _lakebase_state(w, instance_name)
        if current == target:
            _diag(f"lakebase {instance_name} reached {target} (stopped={stopped})")
            return current
        _diag(
            f"lakebase {instance_name} state={current} stopped={stopped}; "
            f"waiting for {target}"
        )
        time.sleep(_POLL_INTERVAL_S)
    final, _ = _lakebase_state(w, instance_name)
    raise TimeoutError(
        f"lakebase {instance_name} did not reach {target} within {timeout_s}s "
        f"(last={final})"
    )


def _timeout_td(seconds: float) -> Any:
    """Return a datetime.timedelta the SDK waiter accepts."""
    import datetime as _dt

    return _dt.timedelta(seconds=seconds)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="real_infra",
        description="Stop/start real Databricks infra for the kill-drill.",
    )
    parser.add_argument("verb", choices=("stop", "start"))
    parser.add_argument("target", choices=(_TARGET_WAREHOUSE, _TARGET_LAKEBASE))
    parser.add_argument("resource_id", help="warehouse id OR lakebase instance name")
    parser.add_argument("--timeout", type=float, default=_DEFAULT_TIMEOUT_S)
    args = parser.parse_args(argv)

    try:
        if args.target == _TARGET_WAREHOUSE:
            if args.verb == "stop":
                state = stop_warehouse(args.resource_id, timeout_s=args.timeout)
            else:
                state = start_warehouse(args.resource_id, timeout_s=args.timeout)
        else:  # lakebase
            if args.verb == "stop":
                state = stop_lakebase(args.resource_id, timeout_s=args.timeout)
            else:
                state = start_lakebase(args.resource_id, timeout_s=args.timeout)
    except TimeoutError as exc:
        _diag(f"TIMEOUT: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001
        _diag(f"ERROR {type(exc).__name__}: {exc}")
        return 1

    _diag(f"{args.verb} {args.target} {args.resource_id}: final state={state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
