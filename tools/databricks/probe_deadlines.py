"""Hard deadlines for CLI probes with no legitimate long network read.

Extracted from ``audit_agent_runtime_foreign_uc_access`` when the size gate
flagged it (2026-08-09). The incident behind every layer here: six deploy
runs wedged ~60 minutes each in ``PySSL_select`` reading an accounts-API
response the server held open and never answered (stack-sampled every time;
``lsof`` showed CLOSE_WAIT zombies beside the wedged ESTABLISHED socket).
``Config.http_timeout_seconds``, a requests-Session default, and
``socket.setdefaulttimeout`` were each defeated in turn by an explicit
``settimeout(None)`` deeper in some stack, and one wedge outlived even the
socket clamp — so the wall-clock watchdog is the only layer trusted
absolutely.
"""

from __future__ import annotations

import faulthandler
import os
import socket
import subprocess
import sys
import threading

_DEADLINE_SECONDS = 120.0
# Env-tunable: recovery walks a 30+-record ledger where most streaming
# reads stall once before clearing on retry (2026-08-10 dumps sat in the
# bounded join while the walk progressed) — that legitimately needs more
# wall-clock than the default.
_WATCHDOG_SECONDS = float(os.environ.get("MIP_PROBE_WATCHDOG_S", "") or 900.0)
_STARTUP_SETTLE_ENV = "MIP_PROBE_STARTUP_SETTLE_S"


def install_probe_deadlines(*, label: str) -> None:
    """Bound every socket and the whole process for one CLI probe run.

    1. ``setdefaulttimeout`` covers sockets that never call ``settimeout``.
    2. ``settimeout(None)`` is clamped to a real deadline so no library at
       any layer can create an unbounded socket in this process.
    3. A daemon timer hard-exits the process after ``_WATCHDOG_SECONDS`` —
       immune to any connection state; the deploy sees a failed step within
       minutes instead of losing an hour per wedge.
    """

    socket.setdefaulttimeout(_DEADLINE_SECONDS)
    original_settimeout = socket.socket.settimeout

    def _bounded_settimeout(self: socket.socket, value: float | None) -> None:
        original_settimeout(self, _DEADLINE_SECONDS if value is None else value)

    socket.socket.settimeout = _bounded_settimeout  # type: ignore[method-assign]

    def _watchdog_abort() -> None:
        print(
            f"[{label}] watchdog: probe exceeded {int(_WATCHDOG_SECONDS)}s "
            "wall-clock; aborting so the deploy fails visibly instead of hanging",
            file=sys.stderr,
            flush=True,
        )
        os._exit(3)

    watchdog = threading.Timer(_WATCHDOG_SECONDS, _watchdog_abort)
    watchdog.daemon = True
    watchdog.start()

    # Periodic thread dumps: every component of the wedging flow passes in
    # isolation (2026-08-10 discriminators), so when the wedge recurs the
    # dump names the exact blocked line instead of another theory.
    faulthandler.dump_traceback_later(180, repeat=True, file=sys.stderr)


def bounded_workspace_read(
    workspace: object,
    path: str,
    *,
    attempts: int = 5,
    deadline_seconds: float = 20.0,
) -> bytes:
    """Download one workspace file with per-attempt deadlines and retries.

    Transport note (2026-08-10): the Python SDK's streaming download stalled
    on ~every ledger record for hours (faulthandler captures inside
    ``urllib3.response.stream``) while the Go CLI's ``workspace export``
    answered the same paths in 1-2 seconds every time. The CLI is therefore
    the primary transport, authenticated by the same environment the SDK
    client uses; the bounded SDK thread-read remains as fallback for
    environments without the CLI. ``NotFound``/``ResourceDoesNotExist``
    keep their shape so callers keep their missing-record contracts.
    """

    from databricks.sdk.errors import NotFound
    from databricks.sdk.errors.platform import ResourceDoesNotExist

    last_error: BaseException | None = None
    # Opt-in via env: the deploy wrappers set this so live runs use the CLI;
    # unit tests with mocked workspace clients never leak network calls.
    cli_attempts = 2 if os.environ.get("MIP_PROBE_CLI_TRANSPORT", "").strip() else 0
    for _attempt in range(cli_attempts):
        try:
            completed = subprocess.run(
                ["databricks", "workspace", "export", path],
                capture_output=True,
                timeout=30,
                check=False,
            )
        except FileNotFoundError as exc:
            last_error = exc
            break
        except subprocess.TimeoutExpired as exc:
            last_error = exc
            continue
        if completed.returncode == 0:
            return completed.stdout
        stderr = completed.stderr.decode(errors="replace")
        if (
            "RESOURCE_DOES_NOT_EXIST" in stderr
            or "does not exist" in stderr
            or "doesn't exist" in stderr
        ):
            raise ResourceDoesNotExist(f"workspace path missing: {path}")
        last_error = RuntimeError(f"cli export failed ({completed.returncode}): {stderr[:200]}")

    for _attempt in range(attempts):
        outcome: list[object] = []

        def _download(target: list[object] = outcome) -> None:
            try:
                target.append(workspace.workspace.download(path).read())  # type: ignore[attr-defined]
            except BaseException as exc:  # noqa: BLE001 - dispatched below
                target.append(exc)

        worker = threading.Thread(target=_download, daemon=True)
        worker.start()
        worker.join(deadline_seconds)
        if worker.is_alive():
            last_error = RuntimeError(f"workspace read stalled: {path}")
            continue
        result = outcome[0] if outcome else RuntimeError("workspace read returned nothing")
        if isinstance(result, (NotFound, ResourceDoesNotExist)):
            raise result
        if isinstance(result, BaseException):
            last_error = result
            continue
        return result  # type: ignore[return-value]
    raise RuntimeError(
        f"workspace read failed after {attempts} bounded attempts: {path}"
    ) from last_error
