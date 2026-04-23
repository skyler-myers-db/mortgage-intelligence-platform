"""R6-02: ``ensure_approval_idempotency_column`` retries on failure.

The prior implementation flipped the per-process
``_APPROVAL_REQUEST_ID_BOOTSTRAPPED`` flag on BOTH success and failure,
which meant a Lakebase brownout during the first approve/reject would
permanently convince the process that the R5-01 DDL had succeeded. Every
subsequent INSERT would then silently fail on an index that didn't
exist.

These tests pin the R6-02 fix:

* a failed bootstrap call leaves the flag False;
* a successful subsequent call flips it to True;
* a subsequent already-True call is a no-op (no extra execute calls).
"""
from __future__ import annotations

from typing import Any

import pytest

from backend.services import lakebase_bootstrap
from backend.services.lakebase import LakebaseError
from backend.services.lakebase_bootstrap import (
    _bootstrap_state_for_tests,
    _reset_bootstrap_for_tests,
    ensure_approval_idempotency_column,
)


class _FakeClient:
    """Minimal stand-in for ``LakebaseClient``.

    ``raise_on_call`` is a list of booleans consumed left-to-right: the
    Nth ``execute`` call raises ``LakebaseError`` when the Nth entry is
    True, succeeds otherwise. The bootstrap issues 2 statements per
    invocation (an ALTER + a CREATE INDEX); the list must cover them.
    """

    def __init__(self, raise_on_call: list[bool] | None = None) -> None:
        self.raise_on_call = raise_on_call or []
        self.calls: list[str] = []

    def execute(self, stmt: str, params: Any = None) -> None:
        _ = params
        idx = len(self.calls)
        self.calls.append(stmt)
        if idx < len(self.raise_on_call) and self.raise_on_call[idx]:
            raise LakebaseError("simulated Lakebase brownout")


@pytest.fixture(autouse=True)
def _reset_flag() -> Any:
    """Ensure every test starts with the flag cleared."""
    _reset_bootstrap_for_tests()
    yield
    _reset_bootstrap_for_tests()


def test_bootstrap_flag_stays_false_on_lakebase_error() -> None:
    """R6-02: a LakebaseError during the DDL must leave the flag False.

    The prior behaviour set it to True so the second attempt was a
    no-op, which permanently hid the outage -- the bootstrap never
    ran again in the process lifetime even after Lakebase recovered.
    """
    client = _FakeClient(raise_on_call=[True, True])

    ensure_approval_idempotency_column(client)  # type: ignore[arg-type]

    state = _bootstrap_state_for_tests()
    assert state["request_id_bootstrapped"] is False
    # The first statement was attempted; the second was not because the
    # first raised. Either way, the flag must be False.
    assert len(client.calls) == 1


def test_bootstrap_flag_stays_false_on_unexpected_error() -> None:
    """Any non-LakebaseError exception (schema drift, OS-level) must
    also leave the flag False so the next request retries."""

    class _Boom(_FakeClient):
        def execute(self, stmt: str, params: Any = None) -> None:
            _ = params
            self.calls.append(stmt)
            raise RuntimeError("unexpected")

    client = _Boom()
    ensure_approval_idempotency_column(client)  # type: ignore[arg-type]

    state = _bootstrap_state_for_tests()
    assert state["request_id_bootstrapped"] is False


def test_bootstrap_flag_flips_on_successful_retry_after_failure() -> None:
    """R6-02 core contract: a failed call followed by a successful call
    leaves the flag True. DDL is idempotent so the retry is safe."""
    client = _FakeClient(raise_on_call=[True])

    ensure_approval_idempotency_column(client)  # type: ignore[arg-type]
    assert _bootstrap_state_for_tests()["request_id_bootstrapped"] is False

    # Next call: clean client, bootstrap succeeds, flag flips True.
    clean = _FakeClient()
    ensure_approval_idempotency_column(clean)  # type: ignore[arg-type]
    assert _bootstrap_state_for_tests()["request_id_bootstrapped"] is True
    assert len(clean.calls) == 2  # ALTER + CREATE INDEX


def test_bootstrap_successful_call_flips_flag_and_runs_all_statements() -> None:
    """Happy path: the clean first call applies every statement and
    marks the process bootstrapped."""
    client = _FakeClient()

    ensure_approval_idempotency_column(client)  # type: ignore[arg-type]

    assert _bootstrap_state_for_tests()["request_id_bootstrapped"] is True
    # Matches lakebase_bootstrap._APPROVAL_REQUEST_ID_DDL tuple length.
    assert len(client.calls) == len(lakebase_bootstrap._APPROVAL_REQUEST_ID_DDL)


def test_bootstrap_second_call_after_success_is_noop() -> None:
    """A successful bootstrap should not re-execute statements on the
    second invocation -- that's the memoisation contract."""
    first = _FakeClient()
    ensure_approval_idempotency_column(first)  # type: ignore[arg-type]
    assert _bootstrap_state_for_tests()["request_id_bootstrapped"] is True

    second = _FakeClient()
    ensure_approval_idempotency_column(second)  # type: ignore[arg-type]
    # Flag still True, but the second client never saw an execute call.
    assert _bootstrap_state_for_tests()["request_id_bootstrapped"] is True
    assert second.calls == []
