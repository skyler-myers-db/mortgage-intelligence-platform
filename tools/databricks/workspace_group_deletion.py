"""Bounded exact postflight for eventually consistent WorkspaceGroup deletion."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, TypeVar

WORKSPACE_GROUP_DELETION_TIMEOUT_SECONDS = 120
_POLL_SECONDS = 2
_STABLE_ABSENCE_OBSERVATIONS = 3
_State = TypeVar("_State")


def delete_workspace_group_and_wait(
    client: Any,
    *,
    group_id: str,
    expected_state: _State,
    inspect_exact_state: Callable[[], _State | None],
    inspect_bound_state: Callable[[], _State | None],
    assert_deletion_context: Callable[[], None],
    assert_single_writer: Callable[[], None],
    resource_label: str,
    timeout_s: int = WORKSPACE_GROUP_DELETION_TIMEOUT_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Delete one exact group and prove stable absence on both SCIM read surfaces."""

    immutable_id = group_id.strip()
    label = resource_label.strip()
    if not immutable_id or not label:
        raise ValueError("WorkspaceGroup deletion requires an immutable ID and resource label")
    if timeout_s <= 0:
        raise ValueError("WorkspaceGroup deletion timeout must be positive")
    deadline = clock() + timeout_s
    assert_deletion_context()
    assert_single_writer()
    delete_error: Exception | None = None
    try:
        client.groups.delete(immutable_id)
    except Exception as exc:  # noqa: BLE001 - postflight distinguishes ambiguous commit
        delete_error = exc
    absent_observations = 0
    while True:
        assert_deletion_context()
        exact = inspect_exact_state()
        bound = inspect_bound_state()
        if exact is not None and exact != expected_state:
            raise RuntimeError(f"{label} changed after deletion")
        if bound is not None and bound != expected_state:
            raise RuntimeError(f"{label} deterministic binding changed after deletion")
        if exact is None and bound is None:
            absent_observations += 1
            if absent_observations >= _STABLE_ABSENCE_OBSERVATIONS:
                return
        else:
            absent_observations = 0
        if clock() >= deadline:
            if delete_error is not None:
                raise RuntimeError(
                    f"{label} delete failed and absence is unproven"
                ) from delete_error
            raise RuntimeError(f"{label} retirement did not converge")
        sleep(_POLL_SECONDS)
