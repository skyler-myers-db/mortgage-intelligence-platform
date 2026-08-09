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

import os
import socket
import sys
import threading

_DEADLINE_SECONDS = 120.0
_WATCHDOG_SECONDS = 900.0


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
