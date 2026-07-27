"""Bounded fresh-credential convergence for agent-proxy denial."""

from __future__ import annotations

import time
from collections.abc import Callable

CUSTOMER_DENIAL_PROJECTION_TIMEOUT_SECONDS = 120.0
CUSTOMER_DENIAL_PROJECTION_POLL_SECONDS = 2.0


class ManagedCustomerCapabilityProjectionPending(RuntimeError):
    """The admin plane is empty but the target identity still projects a group."""


def wait_for_customer_resource_denial_boundary(
    *,
    probe: Callable[[], None],
    sleep: Callable[[float], object] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    timeout_seconds: float = CUSTOMER_DENIAL_PROJECTION_TIMEOUT_SECONDS,
) -> None:
    """Retry only a stale managed-group projection with a caller-fresh client."""

    if timeout_seconds <= 0:
        raise ValueError("customer-resource denial projection timeout must be positive")
    deadline = clock() + timeout_seconds
    while True:
        try:
            probe()
            return
        except ManagedCustomerCapabilityProjectionPending as exc:
            if clock() >= deadline:
                raise RuntimeError(
                    "agent-proxy customer-capability denial projection did not converge"
                ) from exc
            sleep(CUSTOMER_DENIAL_PROJECTION_POLL_SECONDS)
