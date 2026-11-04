#!/usr/bin/env python3
"""The quiesce window's dead-man: restart blue when the runner died inside the window (M0-PR9).

``tools/databricks/quiesce_window_deadman.py`` arms it before the window's
stop as a one-time ``jobs submit`` run, as the deployer that submits it, from
the published, immutable release folder
``<bundle root>/app-src/<release id>/jobs/window_deadman.py`` (never the bundle
files root). It is self-contained, the standard library and the Databricks
SDK only, so that one published file is all it runs.

  python window_deadman.py --app NAME --app-id ID --journal PATH --marker PATH
      --deadline EPOCH --blue-restorable true|false --token HEX
      --window-sha256 HEX --blue-deployment-id ID --restore-source PATH
      [--start-timeout-seconds 600] [--poll-seconds 60]

Until the deadline it polls the window journal, and exits doing nothing once
the journal names this window (its ``window_sha256`` and lease, which ``--token``
digests) in any state but ``window_open``: the run moved on or closed it (and
its close also cancels this run). A journal of another window or none at all
is not yet this window's, so it keeps waiting. At the deadline, with the
journal still exactly this window's ``window_open``:

* ``blue_restorable`` false (a contract migration) leaves the App stopped and
  records ``deadman_left_stopped``;
* otherwise, only when the App is still the window's stopped blue (its id,
  STOPPED, nothing pending, blue its active deployment, its default source
  blue's restore source), it runs ``apps start``, waits for the deployment
  that start created (never "active changed") to be terminal and records
  ``deadman_restarted`` (SUCCEEDED) or ``deadman_start_failed`` with its id.

It never deploys, updates, stops or changes a grant: its only calls are
``apps.get``, ``apps.get_deployment``, ``apps.start``, the journal download
and one create-only upload of its marker (the AST contract in
``tests/unit/test_window_deadman.py``). The marker is unsigned, because the
proof-signing key never leaves the deployer's process. It is a pointer, never
proof: the next governed run re-proves the start deployment against the signed
journal and the App before it re-pins it (``app_deployment_rollback repin
--from-deadman``).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

MARKER_SCHEMA = "mip.quiesce-window-deadman-marker.v1"
TOKEN_DOMAIN = b"mip-window-deadman-v1\0"
TERMINAL_STATES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED"})
MARKER_STATES = frozenset(
    {
        "deadman_restarted",
        "deadman_start_failed",
        "deadman_start_timeout",
        "deadman_start_unreadable",
        "deadman_left_stopped",
        "deadman_skipped",
    }
)
# A journal still unreadable after these attempts at the deadline is a skip.
JOURNAL_READ_ATTEMPTS = 5
STOPPING_WAIT_S = 120
_PREFIX = "[mip-window-deadman]"
_HEX64 = re.compile(r"[0-9a-f]{64}")


def window_token(window_sha256: str, lease_id: str) -> str:
    """The window's dead-man token: its plan digest and its lease, as the arm derives it."""

    message = TOKEN_DOMAIN + window_sha256.encode("utf-8") + b"\0" + lease_id.encode("utf-8")
    return hashlib.sha256(message).hexdigest()


def _text(value: object) -> str:
    return "" if value is None else str(getattr(value, "value", value)).strip()


def _state(app: object) -> str:
    return _text(getattr(getattr(app, "compute_status", None), "state", None)).split(".")[-1].upper()


def _deployment_state(deployment: object) -> str:
    raw = getattr(getattr(deployment, "status", None), "state", None)
    return _text(raw).split(".")[-1].upper()


def _pending_id(app: object) -> str:
    return _text(getattr(getattr(app, "pending_deployment", None), "deployment_id", None))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    for flag in (
        "--app",
        "--app-id",
        "--journal",
        "--marker",
        "--token",
        "--window-sha256",
        "--blue-deployment-id",
        "--restore-source",
    ):
        parser.add_argument(flag, required=True)
    parser.add_argument("--deadline", required=True, type=int)
    parser.add_argument("--blue-restorable", required=True, choices=("true", "false"))
    parser.add_argument("--start-timeout-seconds", type=int, default=600)
    parser.add_argument("--poll-seconds", type=int, default=60)
    return parser


@dataclass(frozen=True)
class Window:
    """One armed window: the run's arguments, its workspace client and its clock."""

    workspace: Any
    args: argparse.Namespace
    clock: Callable[[], float]
    sleep: Callable[[float], None]


# --- the journal ------------------------------------------------------------


def read_journal(window: Window) -> dict[str, Any] | None:
    """The journal as written, or None when there is none yet."""

    try:
        stream = window.workspace.workspace.download(window.args.journal)
    except Exception as exc:  # noqa: BLE001 - only a missing file is "none yet"
        if type(exc).__name__ in {"NotFound", "ResourceDoesNotExist"}:
            return None
        raise
    value = json.loads(stream.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("the window journal is not an object")
    return value


def names_this_window(window: Window, journal: dict[str, Any] | None) -> bool:
    """The journal is this window's: its app, its plan digest and its lease (the token)."""

    if journal is None or journal.get("app_name") != window.args.app:
        return False
    digest = str(journal.get("window_sha256") or "")
    lease = str(journal.get("lease_id") or "")
    return digest == window.args.window_sha256 and window_token(digest, lease) == window.args.token


def still_open(window: Window, journal: dict[str, Any] | None) -> bool:
    """Exactly this window's ``window_open``: state, blue, restore source, restorability."""

    return (
        journal is not None
        and names_this_window(window, journal)
        and journal.get("state") == "window_open"
        and journal.get("blue_deployment_id") == window.args.blue_deployment_id
        and journal.get("restore_source") == window.args.restore_source
        and isinstance(journal.get("blue_restorable"), bool)
    )


def wait_for_deadline(window: Window) -> bool:
    """Poll until the deadline; False once the journal shows this window moved on."""

    args = window.args
    while window.clock() < args.deadline:
        try:
            journal = read_journal(window)
        except Exception as exc:  # noqa: BLE001 - a transient read never ends the wait
            print(f"{_PREFIX} journal unreadable ({type(exc).__name__}); still waiting")
        else:
            if names_this_window(window, journal) and not still_open(window, journal):
                return False
        window.sleep(max(1.0, min(float(args.poll_seconds), args.deadline - window.clock())))
    return True


def journal_at_deadline(window: Window) -> dict[str, Any] | None:
    for attempt in range(JOURNAL_READ_ATTEMPTS):
        try:
            return read_journal(window)
        except Exception as exc:  # noqa: BLE001 - retried, then a recorded skip
            print(f"{_PREFIX} journal unreadable at the deadline ({type(exc).__name__})")
            if attempt + 1 < JOURNAL_READ_ATTEMPTS:
                window.sleep(10.0)
    raise RuntimeError("journal_unreadable")


# --- the App ----------------------------------------------------------------


def blue_refusal(window: Window) -> str:
    """Why the App is not the window's stopped blue, or "" when it is."""

    apps, args = window.workspace.apps, window.args
    app = apps.get(args.app)
    if _text(getattr(app, "id", None)) not in {"", args.app_id}:
        return "app_identity"
    started = window.clock()
    while _state(app) == "STOPPING" and window.clock() - started < STOPPING_WAIT_S:
        window.sleep(10.0)
        app = apps.get(args.app)
    if _state(app) != "STOPPED":
        return "app_not_stopped"
    if _pending_id(app):
        return "pending_deployment"
    active = _text(getattr(getattr(app, "active_deployment", None), "deployment_id", None))
    if active != args.blue_deployment_id:
        return "active_not_blue"
    default_source = _text(getattr(app, "default_source_code_path", None))
    if default_source and default_source != args.restore_source:
        return "default_source_drift"
    return ""


def start_blue(window: Window) -> str:
    """``apps start`` without waiting; the id of the deployment it created, or ""."""

    waiter = window.workspace.apps.start(window.args.app)
    created = _pending_id(getattr(waiter, "response", None))
    return created or _pending_id(window.workspace.apps.get(window.args.app))


def wait_for_terminal(window: Window, deployment_id: str) -> str:
    """The start deployment's terminal state, or "" when the start budget ran out."""

    deadline = window.clock() + window.args.start_timeout_seconds
    while True:
        deployment = window.workspace.apps.get_deployment(window.args.app, deployment_id)
        state = _deployment_state(deployment)
        if state in TERMINAL_STATES:
            return state
        if window.clock() >= deadline:
            return ""
        window.sleep(10.0)


# --- the marker -------------------------------------------------------------


def record(window: Window, state: str, *, start_id: str = "", terminal: str = "", reason: str = "") -> None:
    """One create-only marker: never overwritten, never proof on its own."""

    if state not in MARKER_STATES:
        raise ValueError("dead-man marker state is not reviewed")
    from databricks.sdk.service.workspace import ImportFormat

    args = window.args
    marker = {
        "schema": MARKER_SCHEMA,
        "app_name": args.app,
        "token": args.token,
        "window_sha256": args.window_sha256,
        "blue_deployment_id": args.blue_deployment_id,
        "state": state,
        "start_deployment_id": start_id,
        "terminal_state": terminal,
        "reason": reason,
        "recorded_at": datetime.fromtimestamp(window.clock(), UTC).isoformat(timespec="seconds"),
    }
    window.workspace.workspace.upload(
        args.marker,
        io.BytesIO(json.dumps(marker, sort_keys=True).encode("utf-8")),
        format=ImportFormat.AUTO,
        overwrite=False,
    )
    print(f"{_PREFIX} {state}{f' ({reason})' if reason else ''}")


# --- the run ----------------------------------------------------------------


def run(window: Window) -> int:
    if not wait_for_deadline(window):
        print(f"{_PREFIX} the window moved on before the deadline; nothing to do")
        return 0
    try:
        journal = journal_at_deadline(window)
    except RuntimeError:
        record(window, "deadman_skipped", reason="journal_unreadable")
        return 1
    if journal is None or not still_open(window, journal):
        print(f"{_PREFIX} the journal is not this window's window_open; nothing to do")
        return 0
    if window.args.blue_restorable != "true" or journal.get("blue_restorable") is not True:
        record(window, "deadman_left_stopped", reason="contract_migration")
        return 0
    refusal = blue_refusal(window)
    if refusal:
        record(window, "deadman_skipped", reason=refusal)
        return 1
    created = start_blue(window)
    if not created:
        record(window, "deadman_start_unreadable")
        return 1
    terminal = wait_for_terminal(window, created)
    if not terminal:
        record(window, "deadman_start_timeout", start_id=created)
        return 1
    restarted = terminal == "SUCCEEDED"
    record(
        window,
        "deadman_restarted" if restarted else "deadman_start_failed",
        start_id=created,
        terminal=terminal,
    )
    return 0 if restarted else 1


def _workspace() -> Any:
    from databricks.sdk import WorkspaceClient

    return WorkspaceClient()


def main(
    argv: list[str] | None = None,
    *,
    workspace_factory: Callable[[], Any] | None = None,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    args = _parser().parse_args(argv)
    if not _HEX64.fullmatch(args.token) or not _HEX64.fullmatch(args.window_sha256):
        print(f"{_PREFIX} the token and the window digest must be sha256 hex", file=sys.stderr)
        return 2
    return run(Window((workspace_factory or _workspace)(), args, clock, sleep))


if __name__ == "__main__":
    raise SystemExit(main())
