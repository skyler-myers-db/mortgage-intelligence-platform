"""Circuit-breaker state machine and the process-wide breaker registry.

Single responsibility: decide whether a call to a flaky dependency is
allowed to run right now, and hand every caller the same breaker
instance per dependency name so routers, repositories, and the health
probe observe one coherent state.

``DependencyDownError`` lives here because it is the breaker's own
refusal signal -- the typed exception routers catch and translate to
HTTP 503 with ``retryable: true``.

``backend.services.resilience`` re-exports everything in this module, so
existing import sites keep working unchanged.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from threading import Lock

from backend.services.observability import (
    emit,
    record_breaker_state_change,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Typed exception -- routers catch this one class and translate to 503.
# ---------------------------------------------------------------------------


class DependencyDownError(RuntimeError):
    """Raised when a dependency circuit breaker refuses a call.

    ``dependency`` is the short name the UI shows ("warehouse",
    "lakebase"); ``reason`` is the operator-facing string (underlying
    exception or "breaker open"); ``kind`` is the machine-readable
    classification the frontend keys on to pick a retry cadence.

    R6-05: ``kind`` distinguishes "warming_up" (first-request cold-start
    against a suspended warehouse, fast retry OK) from "breaker_open"
    (breaker already tripped by prior flap, give it the cooldown window
    before retrying) from "retries_exhausted" (retry budget blown by a
    harder outage). The legacy ``retryable: true`` field stays on the
    wire so existing UI code keeps working; the additive ``kind`` lets
    the frontend (in a parallel cycle) pick a smarter backoff.
    """

    KIND_WARMING_UP = "warming_up"
    KIND_BREAKER_OPEN = "breaker_open"
    KIND_RETRIES_EXHAUSTED = "retries_exhausted"
    _ALLOWED_KINDS = frozenset({KIND_WARMING_UP, KIND_BREAKER_OPEN, KIND_RETRIES_EXHAUSTED})

    def __init__(
        self,
        dependency: str,
        *,
        reason: str,
        last_error: BaseException | None = None,
        kind: str = KIND_WARMING_UP,
    ) -> None:
        super().__init__(f"{dependency} dependency is down: {reason}")
        self.dependency = dependency
        self.reason = reason
        self.last_error = last_error
        # Defensively clamp to the allowed set so a typo in a future
        # call site can't ship a freeform string to the frontend.
        self.kind = kind if kind in self._ALLOWED_KINDS else self.KIND_WARMING_UP


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


class CircuitBreaker:
    """Three-state breaker (CLOSED, OPEN, HALF_OPEN).

    - CLOSED: calls go through. Each failure increments a counter;
      when the counter reaches ``failure_threshold`` the breaker
      transitions to OPEN.
    - OPEN: calls are refused for ``cooldown_s`` seconds. After the
      cool-down elapses, the next ``allow()`` returns True and the
      breaker transitions to HALF_OPEN.
    - HALF_OPEN: a small number of probes (``half_open_probes``) are
      permitted. One success closes the breaker; one failure re-opens
      and restarts the cool-down.

    Thread-safety is at the method level via ``threading.Lock``; every
    state transition happens under the lock.
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int = 5,
        cooldown_s: float = 30.0,
        half_open_probes: int = 1,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if cooldown_s <= 0:
            raise ValueError("cooldown_s must be > 0")
        if half_open_probes < 1:
            raise ValueError("half_open_probes must be >= 1")
        self._name = name
        self._failure_threshold = failure_threshold
        self._cooldown_s = cooldown_s
        self._half_open_probes = half_open_probes
        self._now = now
        self._state = self.CLOSED
        self._failure_count = 0
        self._opened_at: float | None = None
        self._probes_in_flight = 0
        self._lock = Lock()

    @property
    def name(self) -> str:
        return self._name

    @property
    def state(self) -> str:
        # Trigger a passive transition from OPEN to HALF_OPEN if the
        # cool-down elapsed between calls to ``allow``.
        with self._lock:
            self._maybe_half_open_locked()
            return self._state

    def _maybe_half_open_locked(self) -> None:
        """Promote OPEN -> HALF_OPEN when the cool-down elapsed.

        Caller must hold ``self._lock``.
        """
        if self._state != self.OPEN or self._opened_at is None:
            return
        if (self._now() - self._opened_at) >= self._cooldown_s:
            self._state = self.HALF_OPEN
            self._probes_in_flight = 0
            # Slice-13: structured event for ops dashboards / grep.
            emit(
                log,
                "circuit_breaker_state_change",
                dependency=self._name,
                from_state=self.OPEN,
                to_state=self.HALF_OPEN,
                name=self._name,
                failure_count=self._failure_count,
                cooldown_s=self._cooldown_s,
            )
            record_breaker_state_change(
                name=self._name, from_state=self.OPEN, to_state=self.HALF_OPEN
            )

    def allow(self) -> bool:
        """Return True when a call is permitted.

        In HALF_OPEN the caller must take the slot by actually making
        the call; ``record_success`` / ``record_failure`` retire the
        probe. We cap concurrent probes at ``half_open_probes`` so a
        thundering herd can't pile onto a still-broken dependency.
        """
        with self._lock:
            self._maybe_half_open_locked()
            if self._state == self.CLOSED:
                return True
            if self._state == self.HALF_OPEN:
                if self._probes_in_flight < self._half_open_probes:
                    self._probes_in_flight += 1
                    return True
                return False
            # OPEN
            return False

    def record_success(self) -> None:
        with self._lock:
            if self._state == self.HALF_OPEN:
                emit(
                    log,
                    "circuit_breaker_state_change",
                    dependency=self._name,
                    from_state=self.HALF_OPEN,
                    to_state=self.CLOSED,
                    name=self._name,
                    failure_count=self._failure_count,
                    cooldown_s=self._cooldown_s,
                )
                record_breaker_state_change(
                    name=self._name,
                    from_state=self.HALF_OPEN,
                    to_state=self.CLOSED,
                )
                self._state = self.CLOSED
                self._failure_count = 0
                self._opened_at = None
                self._probes_in_flight = 0
                return
            # CLOSED success path -- reset counter.
            self._failure_count = 0

    def record_failure(self) -> None:
        with self._lock:
            if self._state == self.HALF_OPEN:
                # One failed probe re-opens and restarts the cool-down.
                emit(
                    log,
                    "circuit_breaker_state_change",
                    level=logging.WARNING,
                    dependency=self._name,
                    from_state=self.HALF_OPEN,
                    to_state=self.OPEN,
                    name=self._name,
                    failure_count=self._failure_count,
                    cooldown_s=self._cooldown_s,
                )
                record_breaker_state_change(
                    name=self._name,
                    from_state=self.HALF_OPEN,
                    to_state=self.OPEN,
                )
                self._state = self.OPEN
                self._opened_at = self._now()
                self._probes_in_flight = 0
                return
            if self._state == self.OPEN:
                # Already open; leave ``opened_at`` alone.
                return
            # CLOSED
            self._failure_count += 1
            if self._failure_count >= self._failure_threshold:
                emit(
                    log,
                    "circuit_breaker_state_change",
                    level=logging.WARNING,
                    dependency=self._name,
                    from_state=self.CLOSED,
                    to_state=self.OPEN,
                    name=self._name,
                    failure_count=self._failure_count,
                    cooldown_s=self._cooldown_s,
                )
                record_breaker_state_change(
                    name=self._name,
                    from_state=self.CLOSED,
                    to_state=self.OPEN,
                )
                self._state = self.OPEN
                self._opened_at = self._now()

    def reset(self) -> None:
        """Force back to CLOSED. Test-only."""
        with self._lock:
            self._state = self.CLOSED
            self._failure_count = 0
            self._opened_at = None
            self._probes_in_flight = 0

    def force_open_for_placeholder_config(self) -> None:
        """Jam the breaker OPEN because the dependency was configured with a
        placeholder value that will guaranteed-fail. Used at boot for the
        Genie client when GENIE_SPACE_ID is still a bundle-default placeholder
        (``00000000PLACEHOLDER``). Holds the state until reset/cooldown is
        consumed; the cooldown will re-probe and the placeholder check will
        trip the next boot.

        Round-3 hole-finder #18, 2026-04-23.
        """
        with self._lock:
            self._state = self.OPEN
            self._failure_count = self._failure_threshold
            self._opened_at = self._now()
            self._probes_in_flight = 0

    def force_close_if_config_changed(self, predicate: Callable[[], bool]) -> bool:
        """Force CLOSED when ``predicate()`` returns True.

        R6-18 escape hatch. ``force_open_for_placeholder_config`` jams the
        breaker OPEN at boot when a placeholder config value (e.g.
        ``GENIE_SPACE_ID=00000000PLACEHOLDER``) would guaranteed-fail. The
        cooldown is irrelevant: every half-open probe will still fail on
        the same placeholder, so the breaker flip-flops until the process
        restarts.

        But Databricks Apps supports env-var rotation WITHOUT a restart.
        When the operator fixes the config at runtime, the breaker has no
        way to notice unless we probe it. This method lets the Genie
        client (or any other caller) evaluate a cheap predicate before
        ``allow()`` and, if the predicate now returns True, close the
        breaker and let the next real probe through.

        ``predicate`` should be a pure read of the current config (e.g.
        ``lambda: not is_placeholder_space_id(settings.genie_space_id)``).
        Returns True when the breaker was actually closed -- callers can
        use that to emit a structured recovery log.
        """
        if not predicate():
            return False
        with self._lock:
            if self._state == self.CLOSED:
                return False
            emit(
                log,
                "circuit_breaker_state_change",
                dependency=self._name,
                from_state=self._state,
                to_state=self.CLOSED,
                name=self._name,
                failure_count=self._failure_count,
                cooldown_s=self._cooldown_s,
                reason="config_changed",
            )
            record_breaker_state_change(
                name=self._name,
                from_state=self._state,
                to_state=self.CLOSED,
            )
            self._state = self.CLOSED
            self._failure_count = 0
            self._opened_at = None
            self._probes_in_flight = 0
            return True


# ---------------------------------------------------------------------------
# Named-singleton registry -- routers + health endpoint share one breaker
# instance per dependency so the "circuit_breakers" status in /api/health is
# coherent with the one the repositories actually use.
# ---------------------------------------------------------------------------


_BREAKERS: dict[str, CircuitBreaker] = {}
_BREAKERS_LOCK = Lock()


def get_breaker(
    name: str,
    *,
    failure_threshold: int = 5,
    cooldown_s: float = 30.0,
    half_open_probes: int = 1,
) -> CircuitBreaker:
    """Return (and lazily construct) the process-wide breaker for ``name``."""
    with _BREAKERS_LOCK:
        existing = _BREAKERS.get(name)
        if existing is not None:
            return existing
        created = CircuitBreaker(
            name,
            failure_threshold=failure_threshold,
            cooldown_s=cooldown_s,
            half_open_probes=half_open_probes,
        )
        _BREAKERS[name] = created
        return created


def all_breakers() -> dict[str, CircuitBreaker]:
    with _BREAKERS_LOCK:
        return dict(_BREAKERS)


def _reset_breakers_for_tests() -> None:
    """Test helper -- drop every cached breaker."""
    with _BREAKERS_LOCK:
        _BREAKERS.clear()
