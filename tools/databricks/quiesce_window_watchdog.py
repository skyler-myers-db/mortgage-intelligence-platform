#!/usr/bin/env python3
"""End a run's in-window mutation before the dead-man may start blue (M0-PR9, T5).

The dead-man starts blue at the job deadline D while the journal still reads
the window. In CI the job's own ``timeout-minutes`` kills the runner by D; a
local run's ``./scripts/deploy.sh --deadline <epoch>`` had nothing that did,
so a run still mutating past D (a WR3 migration, a role converge) would have
the dead-man start blue under it. Once a restorable window is open the window
library starts this watchdog in the background:

  python -m tools.databricks.quiesce_window_watchdog --run-pid PID --deadline-epoch D [--spare PID]

At ``fire_at(D)`` = D - ``DEADMAN_GUARD_S`` - ``WATCHDOG_LEAD_S`` it sends the
run SIGUSR1 (deploy.sh traps it: ``exit 143`` once the current command ends)
and then SIGTERM to every process the run started, except itself and the
``--spare`` trees (the lease heartbeat), twice. The run's EXIT trap disarms
it first, then compensates: it is still just outside the start guard, so it
starts and re-pins blue itself, or it leaves the window to the dead-man.

It fires only for its own run: it polls that the run is still its ancestor
and exits at once when it is not (the run ended and it was reparented, so a
reused pid is never signalled). It makes no Databricks call and writes no file.
``--check`` validates the arguments and prints ``fire_at`` without waiting.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterable

from tools.databricks.quiesce_window_budget import DEADMAN_GUARD_S, parse_deadline

# How long before the start guard the run is ended: its EXIT trap reads the
# journal and reaches ``quiesce_window start`` well inside it.
WATCHDOG_LEAD_S = 60
POLL_S = 30
SWEEPS = 2
SWEEP_PAUSE_S = 2.0
_PREFIX = "[mip-quiesce-window-watchdog]"

Table = list[tuple[int, int]]


def fire_at(deadline: int) -> int:
    """When the run's in-window mutation is ended: before the start guard, with a lead."""

    return deadline - DEADMAN_GUARD_S - WATCHDOG_LEAD_S


def process_table() -> Table:
    """(pid, parent pid) of every process, from ``ps`` (macOS and Linux)."""

    out = subprocess.run(
        ["ps", "-A", "-o", "pid=", "-o", "ppid="],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout
    rows = (line.split() for line in out.splitlines())
    return [(int(row[0]), int(row[1])) for row in rows if len(row) == 2 and all(map(str.isdigit, row))]


def descendants(root: int, table: Table) -> set[int]:
    """Every process below ``root`` in ``table``."""

    children: dict[int, list[int]] = {}
    for pid, parent in table:
        children.setdefault(parent, []).append(pid)
    found: set[int] = set()
    frontier = [root]
    while frontier:
        for child in children.get(frontier.pop(), ()):
            if child not in found and child != root:
                found.add(child)
                frontier.append(child)
    return found


def is_ancestor(run_pid: int, pid: int, table: Table) -> bool:
    """Whether ``run_pid`` is an ancestor of ``pid``: the watchdog's run is still alive."""

    parents = dict(table)
    seen: set[int] = set()
    while pid not in seen and pid > 1:
        seen.add(pid)
        pid = parents.get(pid, 0)
        if pid == run_pid:
            return True
    return False


def watch(
    *,
    run_pid: int,
    deadline: int,
    spare: Iterable[int] = (),
    self_pid: int | None = None,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
    table: Callable[[], Table] = process_table,
    send: Callable[[int, int], None] = os.kill,
) -> str:
    """Wait for ``fire_at(deadline)``, then end the run's processes: ``fired`` or ``orphaned``."""

    me = os.getpid() if self_pid is None else self_pid
    target = fire_at(deadline)
    while True:
        current = table()
        if not is_ancestor(run_pid, me, current):
            return "orphaned"
        remaining = target - clock()
        if remaining <= 0:
            break
        sleep(min(float(POLL_S), remaining))
    print(
        f"{_PREFIX} the start guard of the job deadline is near: ending this run's "
        "in-window mutation so its EXIT trap restores blue or leaves the window to the dead-man",
        file=sys.stderr,
    )
    send(run_pid, signal.SIGUSR1)
    for sweep in range(SWEEPS):
        current = table()
        kept = {me}
        for pid in spare:
            kept |= {pid, *descendants(pid, current)}
        for pid in sorted(descendants(run_pid, current) - kept):
            try:
                send(pid, signal.SIGTERM)
            except OSError:  # it ended meanwhile
                continue
        if sweep + 1 < SWEEPS:
            sleep(SWEEP_PAUSE_S)
    return "fired"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--run-pid", required=True, type=int)
    parser.add_argument("--deadline-epoch", default="")
    parser.add_argument("--spare", type=int, action="append", default=[])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    deadline = parse_deadline(args.deadline_epoch)
    if deadline is None or args.run_pid <= 1:
        print(f"{_PREFIX} a run pid and the job deadline (epoch seconds) are required", file=sys.stderr)
        return 2
    if args.check:
        print(fire_at(deadline))
        return 0
    outcome = watch(run_pid=args.run_pid, deadline=deadline, spare=args.spare)
    print(f"{_PREFIX} {outcome}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
