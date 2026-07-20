#!/usr/bin/env python3
"""Stop a Databricks App after failed deployment and prove terminal state."""

from __future__ import annotations

import argparse
import shlex
import time
from collections.abc import Callable
from pathlib import Path

from databricks.sdk import WorkspaceClient
from tools.databricks.workspace_auth import deployment_workspace_client


def _state(app: object) -> str:
    raw = getattr(getattr(app, "compute_status", None), "state", "")
    return str(getattr(raw, "value", raw) or "").split(".")[-1].strip().upper()


def _assert_identity(
    app: object,
    *,
    expected_app_id: str,
    expected_client_id: str,
    expected_scim_id: str,
) -> None:
    expected = (expected_app_id.strip(), expected_client_id.strip(), expected_scim_id.strip())
    if any(expected) != all(expected):
        raise ValueError("expected App identity must provide ID, client ID, and SCIM ID together")
    if not all(expected):
        return
    actual = (
        str(getattr(app, "id", "") or "").strip(),
        str(getattr(app, "service_principal_client_id", "") or "").strip(),
        str(getattr(app, "service_principal_id", "") or "").strip(),
    )
    if actual != expected:
        raise RuntimeError("Databricks App identity changed before fail-closed stop")


def stop_app_fail_closed(
    *,
    app_name: str,
    workspace: WorkspaceClient | None = None,
    attempts: int = 30,
    interval_s: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
    expected_app_id: str = "",
    expected_client_id: str = "",
    expected_scim_id: str = "",
) -> str:
    """Return ``absent`` or ``stopped``; raise unless one is proven.

    Stop is retried because Apps can reject it while a bundle-triggered
    deployment is still transitioning. Inventory ambiguity, unreadable state,
    or exhaustion is fatal to the deployment cleanup contract.
    """

    name = app_name.strip()
    if not name:
        raise ValueError("app_name must be non-empty")
    if attempts <= 0 or interval_s < 0:
        raise ValueError("attempts must be positive and interval_s non-negative")
    client = workspace or deployment_workspace_client()
    matches = [app for app in client.apps.list() if str(getattr(app, "name", "")) == name]
    if not matches:
        return "absent"
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one Databricks App named {name!r}")
    _assert_identity(
        matches[0],
        expected_app_id=expected_app_id,
        expected_client_id=expected_client_id,
        expected_scim_id=expected_scim_id,
    )

    last_state = ""
    last_stop_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        app = client.apps.get(name)
        _assert_identity(
            app,
            expected_app_id=expected_app_id,
            expected_client_id=expected_client_id,
            expected_scim_id=expected_scim_id,
        )
        last_state = _state(app)
        if last_state == "STOPPED":
            return "stopped"
        try:
            client.apps.stop(name)
            last_stop_error = None
        except Exception as exc:  # transient deployment conflicts are retryable
            last_stop_error = exc
        if attempt < attempts and interval_s:
            sleep(interval_s)
    detail = f"last compute state {last_state!r}"
    if last_stop_error is not None:
        detail += f"; last stop error {type(last_stop_error).__name__}"
    raise RuntimeError(f"Could not prove Databricks App {name!r} stopped ({detail})")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-name", required=True)
    parser.add_argument("--attempts", type=int, default=30)
    parser.add_argument("--interval-s", type=float, default=2.0)
    parser.add_argument("--expected-app-id", default="")
    parser.add_argument("--expected-client-id", default="")
    parser.add_argument("--expected-scim-id", default="")
    parser.add_argument(
        "--out-env",
        type=Path,
        help="Write the proven absent/stopped outcome for fail-closed compensation.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    outcome = stop_app_fail_closed(
        app_name=args.app_name,
        attempts=args.attempts,
        interval_s=args.interval_s,
        expected_app_id=args.expected_app_id,
        expected_client_id=args.expected_client_id,
        expected_scim_id=args.expected_scim_id,
    )
    if args.out_env is not None:
        args.out_env.write_text(
            f"MIP_APP_STOP_OUTCOME={shlex.quote(outcome)}\n",
            encoding="utf-8",
        )
        args.out_env.chmod(0o600)
    print(f"fail-closed App compensation verified: {outcome}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
