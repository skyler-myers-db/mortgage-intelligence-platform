#!/usr/bin/env python3
"""The quiesce window's dead-man: restart blue when the runner died inside the window (M0-PR9).

``tools/databricks/quiesce_window_deadman.py`` arms it before the window's
stop as a one-time ``jobs submit`` run, as the deployer that submits it, from
the published, immutable release folder
``<bundle root>/app-src/<release id>/jobs/window_deadman.py`` (never the bundle
files root). It is self-contained, the standard library and the Databricks
SDK only, so that one published file is all it runs.

  python window_deadman.py --app NAME --app-id ID --journal PATH --marker PATH
      --armed-marker PATH --deadline EPOCH --blue-restorable true|false
      --token HEX --window-sha256 HEX --blue-deployment-id ID
      --restore-source PATH [--start-timeout-seconds 600] [--poll-seconds 60]

It first writes its create-only **armed** marker (``announce``): the arm
waits for that marker, with the run still RUNNING, before it lets the window
journal ``window_open`` and stop the App, so a run that never started (its
environment, the workspace file, its auth or the marker folder) refuses the
window instead of leaving it without a restart. Until the deadline it then
polls the window journal, and exits doing nothing once
the journal names this window (its ``window_sha256`` and lease, which ``--token``
digests) in any state but ``window_open`` or ``window_starting``: the run moved
on or closed it (and its close also cancels this run). A journal of another
window or none at all is not yet this window's, so it keeps waiting. At the
deadline, with the journal still exactly this window's ``window_open`` or
``window_starting`` (the run's intent: it never journals one within the start
guard of the deadline, so at the deadline it means the runner died before or
during its start, and the App checks below tell which):

* ``blue_restorable`` false (a contract migration) leaves the App stopped and
  records ``deadman_left_stopped``;
* otherwise, only when the App is still the window's stopped blue (its id,
  STOPPED, nothing pending, no other active deployment, blue its newest
  deployment of ANY state and SUCCEEDED, its default source blue's restore
  source: no start of the run's ever happened), it lists the App's
  deployment ids, runs ``apps start`` and finds the deployment that start
  created as the one id outside that list (never "active changed"), waits
  for it to be terminal and records ``deadman_restarted`` (SUCCEEDED) or
  ``deadman_start_failed`` with its id.

Spike S7 measured the start this relies on: a STOPPED App's GET has no
``active_deployment``, so blue is judged by its newest deployment (a FAILED
one becomes active and moves the default source, and a start reruns it); the
start response and an immediate GET show no new deployment, whose id appears
only once the compute is ACTIVE, 96-108 s later on a trivial App, for about
1 s as pending and then as active. The id is polled at most every 5 s, and
the find and the terminal wait share the one ``--start-timeout-seconds``
budget from the start call. Two new ids are ``deadman_start_unreadable``
(``start_ambiguous``), none by then ``deadman_start_unreadable``
(``start_timeout``).

It never deploys, updates, stops or changes a grant: its only calls are
``apps.get``, ``apps.list_deployments``, ``apps.get_deployment``,
``apps.start``, the journal download and the create-only uploads of its
armed marker and its marker (the AST contract in
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
ARMED_SCHEMA = "mip.quiesce-window-deadman-armed.v1"
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
# The start's new id shows in pending_deployment for about 1 s (spike S7).
START_ID_POLL_S = 5.0
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


def _active_id(app: object) -> str:
    return _text(getattr(getattr(app, "active_deployment", None), "deployment_id", None))


def _newest(deployments: list[Any]) -> Any:
    """The newest deployment of ANY state (create_time, update_time, id), or None."""

    return max(
        deployments,
        key=lambda item: tuple(
            _text(getattr(item, name, None)) for name in ("create_time", "update_time", "deployment_id")
        ),
        default=None,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    for flag in (
        "--app",
        "--app-id",
        "--journal",
        "--marker",
        "--armed-marker",
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

# The journal states the dead-man may restart from: the open window, and the
# run's intent to start, which at the deadline means its runner died.
RESTARTABLE_STATES = frozenset({"window_open", "window_starting"})


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
    """Exactly this window, open or only intending its start: state, blue, source, restorability."""

    return (
        journal is not None
        and names_this_window(window, journal)
        and journal.get("state") in RESTARTABLE_STATES
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
    if _pending_id(app) or getattr(app, "pending_deployment", None) is not None:
        return "pending_deployment"
    # A STOPPED App's GET has no active deployment (spike S7); one shown is blue's.
    if _active_id(app) not in {"", args.blue_deployment_id}:
        return "active_not_blue"
    deployments = list(apps.list_deployments(args.app))
    newest = _newest(deployments)
    if (
        any(not _text(getattr(item, "deployment_id", None)) for item in deployments)
        or _text(getattr(newest, "deployment_id", None)) != args.blue_deployment_id
        or _deployment_state(newest) != "SUCCEEDED"
    ):
        return "newest_not_blue"
    if _text(getattr(app, "default_source_code_path", None)) != args.restore_source:
        return "default_source_drift"
    return ""


def _deployment_ids(window: Window) -> set[str] | None:
    ids = [_text(getattr(item, "deployment_id", None)) for item in window.workspace.apps.list_deployments(window.args.app)]
    return None if any(not item for item in ids) else set(ids)


def start_blue(window: Window) -> tuple[str, str, float]:
    """``apps start`` without waiting: (the deployment it created, or "", a reason, the start deadline).

    The id is the one outside the deployment ids listed before the start, in
    ``pending_deployment``, ``active_deployment`` or ``list_deployments``,
    polled until the start budget ends.
    """

    apps, args = window.workspace.apps, window.args
    baseline = _deployment_ids(window)
    if not baseline:
        return "", "start_unreadable", window.clock()
    apps.start(args.app)  # never awaited: compute ACTIVE is no deployment
    deadline = window.clock() + args.start_timeout_seconds
    while True:
        app = apps.get(args.app)
        listed = _deployment_ids(window)
        if listed is None:
            return "", "start_unreadable", deadline
        created = sorted(({_pending_id(app), _active_id(app)} | listed) - baseline - {""})
        if len(created) > 1:
            return "", "start_ambiguous", deadline
        if created:
            return created[0], "", deadline
        if window.clock() >= deadline:
            return "", "start_timeout", deadline
        window.sleep(START_ID_POLL_S)


def wait_for_terminal(window: Window, deployment_id: str, deadline: float) -> str:
    """The start deployment's terminal state, or "" when the start budget ran out."""

    while True:
        deployment = window.workspace.apps.get_deployment(window.args.app, deployment_id)
        state = _deployment_state(deployment)
        if state in TERMINAL_STATES:
            return state
        if window.clock() >= deadline:
            return ""
        window.sleep(10.0)


# --- the markers ------------------------------------------------------------


def announce(window: Window) -> None:
    """The create-only armed marker: this run started and can write beside the journal.

    An armed marker of this same window that is already there (the same run,
    started again by the fake Jobs service or a resumed test) is accepted;
    any other content raises, and the run fails before its wait.
    """

    from databricks.sdk.service.workspace import ImportFormat

    args = window.args
    armed = {
        "schema": ARMED_SCHEMA,
        "app_name": args.app,
        "token": args.token,
        "window_sha256": args.window_sha256,
        "state": "deadman_armed",
        "recorded_at": datetime.fromtimestamp(window.clock(), UTC).isoformat(timespec="seconds"),
    }
    try:
        window.workspace.workspace.upload(
            args.armed_marker,
            io.BytesIO(json.dumps(armed, sort_keys=True).encode("utf-8")),
            format=ImportFormat.AUTO,
            overwrite=False,
        )
    except Exception as exc:  # noqa: BLE001 - only this window's own marker is accepted
        if type(exc).__name__ != "ResourceAlreadyExists":
            raise
        existing = json.loads(window.workspace.workspace.download(args.armed_marker).read().decode("utf-8"))
        if not isinstance(existing, dict) or any(
            existing.get(key) != armed[key] for key in ("schema", "app_name", "token", "window_sha256")
        ):
            raise RuntimeError("another armed marker is in this window's place") from exc
    print(f"{_PREFIX} armed for window {args.window_sha256[:12]} until {args.deadline}")


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
        print(f"{_PREFIX} the journal is not this window's window_open or window_starting; nothing to do")
        return 0
    if window.args.blue_restorable != "true" or journal.get("blue_restorable") is not True:
        record(window, "deadman_left_stopped", reason="contract_migration")
        return 0
    refusal = blue_refusal(window)
    if refusal:
        record(window, "deadman_skipped", reason=refusal)
        return 1
    created, reason, deadline = start_blue(window)
    if not created:
        record(window, "deadman_start_unreadable", reason=reason)
        return 1
    terminal = wait_for_terminal(window, created, deadline)
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


def window_from_argv(
    argv: list[str] | None,
    *,
    workspace_factory: Callable[[], Any] | None = None,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> Window | None:
    """The armed window from the run's parameters, or None when they are not valid."""

    args = _parser().parse_args(argv)
    if not _HEX64.fullmatch(args.token) or not _HEX64.fullmatch(args.window_sha256):
        print(f"{_PREFIX} the token and the window digest must be sha256 hex", file=sys.stderr)
        return None
    return Window((workspace_factory or _workspace)(), args, clock, sleep)


def main(
    argv: list[str] | None = None,
    *,
    workspace_factory: Callable[[], Any] | None = None,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    window = window_from_argv(argv, workspace_factory=workspace_factory, clock=clock, sleep=sleep)
    if window is None:
        return 2
    announce(window)
    return run(window)


if __name__ == "__main__":
    raise SystemExit(main())
