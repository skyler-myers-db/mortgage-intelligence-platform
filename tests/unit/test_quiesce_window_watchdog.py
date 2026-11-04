"""A restorable window's run never keeps mutating once the dead-man may start blue (M0-PR9).

Review: only GitHub's job timeout enforced the deadline, so a local governed
run that overran its ``--deadline`` kept mutating inside the window while the
dead-man started blue under it. ``tools/databricks/quiesce_window_watchdog.py``
ends the run's in-window mutation a minute before the start guard: SIGUSR1 to
the run (deploy.sh traps it, ``exit 143``), then SIGTERM to every process the
run started but itself and the lease heartbeat. It never signals anything once
the run is gone, and the run's EXIT trap disarms it first.
"""

from __future__ import annotations

import signal

import pytest

from tools.databricks import quiesce_window_watchdog as watchdog
from tools.databricks.quiesce_window_budget import DEADMAN_GUARD_S

RUN, MUTATION, GRANDCHILD, HEARTBEAT, HEARTBEAT_CHILD, SELF, OTHER = 100, 200, 300, 201, 301, 202, 900
DEADLINE = 1_800_000_000
# The deploy run, its in-window mutation (a python under a subshell), its lease
# heartbeat and the watchdog itself; and an unrelated process.
TABLE = [
    (RUN, 50),
    (MUTATION, RUN),
    (GRANDCHILD, MUTATION),
    (HEARTBEAT, RUN),
    (HEARTBEAT_CHILD, HEARTBEAT),
    (SELF, RUN),
    (OTHER, 1),
]


class Clock:
    def __init__(self, now: float) -> None:
        self.now, self.sleeps = now, 0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps += 1
        self.now += seconds


def test_it_fires_before_the_start_guard_so_the_exit_trap_may_still_start_blue() -> None:
    assert watchdog.fire_at(DEADLINE) == DEADLINE - DEADMAN_GUARD_S - watchdog.WATCHDOG_LEAD_S
    assert watchdog.fire_at(DEADLINE) < DEADLINE - DEADMAN_GUARD_S


def test_the_process_tree_helpers() -> None:
    assert watchdog.descendants(RUN, TABLE) == {MUTATION, GRANDCHILD, HEARTBEAT, HEARTBEAT_CHILD, SELF}
    assert watchdog.descendants(OTHER, TABLE) == set()
    assert watchdog.is_ancestor(RUN, SELF, TABLE) and watchdog.is_ancestor(RUN, GRANDCHILD, TABLE)
    assert not watchdog.is_ancestor(RUN, OTHER, TABLE)
    assert len(watchdog.process_table()) > 1, "ps lists this machine's processes"


def test_at_the_guard_it_ends_the_run_and_its_mutation_but_spares_the_heartbeat() -> None:
    clock = Clock(float(watchdog.fire_at(DEADLINE) - 95))
    sent: list[tuple[int, int]] = []
    outcome = watchdog.watch(
        run_pid=RUN,
        deadline=DEADLINE,
        spare=[HEARTBEAT],
        self_pid=SELF,
        clock=clock,
        sleep=clock.sleep,
        table=lambda: TABLE,
        send=lambda pid, sig: sent.append((pid, sig)),
    )
    assert outcome == "fired" and clock.now >= watchdog.fire_at(DEADLINE)
    assert sent[0] == (RUN, signal.SIGUSR1), "the run's trap first, so it runs nothing after the killed command"
    terminated = [pid for pid, sig in sent[1:] if sig == signal.SIGTERM]
    assert sorted(set(terminated)) == [MUTATION, GRANDCHILD] and len(terminated) == 2 * watchdog.SWEEPS
    assert not {pid for pid, _sig in sent} & {SELF, HEARTBEAT, HEARTBEAT_CHILD, OTHER}


@pytest.mark.parametrize("gone_after", (0, 2), ids=("at-start", "mid-wait"))
def test_it_never_signals_once_the_run_is_gone(gone_after: int) -> None:
    clock = Clock(float(watchdog.fire_at(DEADLINE) - 120))
    reads = [0]
    orphaned = [(SELF, 1), *[row for row in TABLE if row[0] not in {RUN, SELF}]]

    def table() -> list[tuple[int, int]]:
        reads[0] += 1
        return TABLE if reads[0] <= gone_after else orphaned

    sent: list[tuple[int, int]] = []
    outcome = watchdog.watch(
        run_pid=RUN,
        deadline=DEADLINE,
        self_pid=SELF,
        clock=clock,
        sleep=clock.sleep,
        table=table,
        send=lambda pid, sig: sent.append((pid, sig)),
    )
    assert outcome == "orphaned" and sent == []


def test_a_process_that_ended_meanwhile_never_stops_the_sweep() -> None:
    clock = Clock(float(watchdog.fire_at(DEADLINE)))
    sent: list[int] = []

    def send(pid: int, sig: int) -> None:
        if pid == MUTATION:
            raise ProcessLookupError(pid)
        sent.append(pid)

    assert watchdog.watch(
        run_pid=RUN, deadline=DEADLINE, self_pid=SELF, clock=clock, sleep=clock.sleep, table=lambda: TABLE, send=send
    ) == "fired"
    assert GRANDCHILD in sent


def test_check_prints_when_it_fires_and_refuses_without_a_deadline(capsys: pytest.CaptureFixture[str]) -> None:
    assert watchdog.main(["--check", "--run-pid", str(RUN), "--deadline-epoch", str(DEADLINE)]) == 0
    assert capsys.readouterr().out.strip() == str(watchdog.fire_at(DEADLINE))
    for deadline in ("", "tomorrow"):
        assert watchdog.main(["--check", "--run-pid", str(RUN), "--deadline-epoch", deadline]) == 2
    assert watchdog.main(["--check", "--run-pid", "1", "--deadline-epoch", str(DEADLINE)]) == 2
