from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace

import pytest

from tools.databricks.workspace_group_deletion import (
    delete_workspace_group_and_wait,
)


class _Groups:
    def __init__(self, *, delete_error: Exception | None = None) -> None:
        self.delete_error = delete_error
        self.deleted: list[str] = []

    def delete(self, group_id: str) -> None:
        self.deleted.append(group_id)
        if self.delete_error is not None:
            raise self.delete_error


def _script(values: list[str | None]) -> Callable[[], str | None]:
    remaining = iter(values)
    return lambda: next(remaining)


def test_deletion_accepts_ambiguous_commit_only_after_stable_dual_absence() -> None:
    groups = _Groups(delete_error=TimeoutError("response lost after commit"))
    client = SimpleNamespace(groups=groups)
    sleeps: list[float] = []

    delete_workspace_group_and_wait(
        client,
        group_id="group-id",
        expected_state="expected",
        inspect_exact_state=_script(
            ["expected", "expected", None, None, None]
        ),
        inspect_bound_state=_script(
            ["expected", "expected", None, None, None]
        ),
        assert_deletion_context=lambda: None,
        assert_single_writer=lambda: None,
        resource_label="test group",
        timeout_s=20,
        sleep=sleeps.append,
        clock=lambda: 0,
    )

    assert groups.deleted == ["group-id"]
    assert sleeps == [2, 2, 2, 2]


def test_deletion_rejects_transient_false_absence_when_group_reappears() -> None:
    groups = _Groups()
    client = SimpleNamespace(groups=groups)
    now = [0.0]
    exact = _script([None, "expected", "expected"])
    bound = _script([None, "expected", "expected"])

    def advance(seconds: float) -> None:
        now[0] += seconds

    with pytest.raises(RuntimeError, match="retirement did not converge"):
        delete_workspace_group_and_wait(
            client,
            group_id="group-id",
            expected_state="expected",
            inspect_exact_state=exact,
            inspect_bound_state=bound,
            assert_deletion_context=lambda: None,
            assert_single_writer=lambda: None,
            resource_label="test group",
            timeout_s=3,
            sleep=advance,
            clock=lambda: now[0],
        )

    assert groups.deleted == ["group-id"]


def test_deletion_rejects_deterministic_same_name_replacement() -> None:
    groups = _Groups()

    with pytest.raises(RuntimeError, match="deterministic binding changed"):
        delete_workspace_group_and_wait(
            SimpleNamespace(groups=groups),
            group_id="group-id",
            expected_state="expected",
            inspect_exact_state=lambda: None,
            inspect_bound_state=lambda: "replacement",
            assert_deletion_context=lambda: None,
            assert_single_writer=lambda: None,
            resource_label="test group",
            sleep=lambda _seconds: None,
        )

    assert groups.deleted == ["group-id"]


def test_deletion_lost_lease_blocks_mutation() -> None:
    groups = _Groups()

    with pytest.raises(RuntimeError, match="lease lost"):
        delete_workspace_group_and_wait(
            SimpleNamespace(groups=groups),
            group_id="group-id",
            expected_state="expected",
            inspect_exact_state=lambda: pytest.fail("postflight reached"),
            inspect_bound_state=lambda: pytest.fail("postflight reached"),
            assert_deletion_context=lambda: None,
            assert_single_writer=lambda: (_ for _ in ()).throw(
                RuntimeError("lease lost")
            ),
            resource_label="test group",
        )

    assert groups.deleted == []


def test_deletion_context_is_rechecked_during_postflight() -> None:
    groups = _Groups()
    context_checks = 0

    def endpoint_context() -> None:
        nonlocal context_checks
        context_checks += 1
        if context_checks == 2:
            raise RuntimeError("endpoint reappeared")

    with pytest.raises(RuntimeError, match="endpoint reappeared"):
        delete_workspace_group_and_wait(
            SimpleNamespace(groups=groups),
            group_id="group-id",
            expected_state="expected",
            inspect_exact_state=lambda: "expected",
            inspect_bound_state=lambda: "expected",
            assert_deletion_context=endpoint_context,
            assert_single_writer=lambda: None,
            resource_label="test group",
            sleep=lambda _seconds: None,
        )

    assert groups.deleted == ["group-id"]
    assert context_checks == 2
