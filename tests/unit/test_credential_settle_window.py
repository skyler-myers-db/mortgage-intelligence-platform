"""New-credential propagation settle window for the identity-membership probe.

A freshly minted service-principal secret is eventually consistent at the
account token endpoint, so the first authentication can be rejected with
`invalid_client` for a few seconds. Deploy runs on 2026-08-01/03 failed at
step 4 roughly half the time for exactly this reason. The settle window must
absorb that narrow case WITHOUT becoming an authorization fallback.
"""

from __future__ import annotations

import pytest

from tools.databricks.converge_campaign_treatment_access import (
    read_identity_with_credential_settle,
)


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def test_returns_identity_on_first_success() -> None:
    clock = _Clock()

    result = read_identity_with_credential_settle(
        lambda: {"id": "sp-1"},
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )

    assert result == {"id": "sp-1"}
    assert clock.slept == []


@pytest.mark.parametrize(
    "code",
    ["invalid_client", "invalid_grant", "unauthenticated", "unauthorized_client"],
)
def test_absorbs_propagation_rejection_then_succeeds(code: str) -> None:
    clock = _Clock()
    attempts: list[int] = []

    def read_identity() -> dict[str, str]:
        attempts.append(len(attempts))
        if len(attempts) < 3:
            raise ValueError(f"{code}: Client authentication failed")
        return {"id": "sp-1"}

    result = read_identity_with_credential_settle(
        read_identity,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )

    assert result == {"id": "sp-1"}
    assert len(attempts) == 3
    assert clock.slept == [5.0, 5.0]


def test_non_auth_errors_propagate_immediately() -> None:
    clock = _Clock()

    def read_identity() -> dict[str, str]:
        raise RuntimeError("Target identity membership proof returned a malformed identity")

    with pytest.raises(RuntimeError, match="malformed identity"):
        read_identity_with_credential_settle(
            read_identity,
            sleep=clock.sleep,
            monotonic=clock.monotonic,
        )

    assert clock.slept == []


def test_persistent_rejection_still_fails_at_the_deadline() -> None:
    """The window must never become an authorization fallback."""
    clock = _Clock()
    attempts: list[int] = []

    def read_identity() -> dict[str, str]:
        attempts.append(len(attempts))
        raise ValueError("invalid_client: Client authentication failed")

    with pytest.raises(ValueError, match="invalid_client"):
        read_identity_with_credential_settle(
            read_identity,
            sleep=clock.sleep,
            monotonic=clock.monotonic,
        )

    # Bounded: 90s deadline at a 5s interval, then the rejection propagates.
    assert clock.now >= 90.0
    assert len(attempts) == 19
