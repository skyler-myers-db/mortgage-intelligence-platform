"""The signed quiesce-window journal: one-way states, compare-and-swap, signature (M0-PR8)."""

from __future__ import annotations

import json

import pytest

from backend.services.ai_gateway_proof_attestation import derive_gateway_proof_verify_key
from tests.fixtures.quiesce_window_estate import APP_NAME, FakeFiles
from tests.unit.test_app_deployment_rollback import SIGNING_KEY
from tools.databricks import quiesce_window_journal as journal

OPEN = {
    "state": "window_open",
    "window_sha256": "a" * 64,
    "reasons": ["WR3"],
    "blue_deployment_id": "deployment-blue",
    "restore_source": "/Workspace/Users/deployer/app-src/release",
    "source_tree_sha256": "b" * 64,
    "blue_restorable": True,
    "lease_id": "lease-PLACEHOLDER",
    "source_git_sha": "c" * 40,
}


@pytest.fixture(autouse=True)
def _keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", derive_gateway_proof_verify_key(SIGNING_KEY))


class _Workspace:
    def __init__(self) -> None:
        self.workspace = FakeFiles()


def _write(workspace: _Workspace, expected: dict | None, changes: dict) -> dict:
    return journal.write(
        workspace, app_name=APP_NAME, expected=expected, changes=changes, assert_lease_held=lambda: None
    )


def test_a_window_moves_open_started_proven_closed() -> None:
    workspace = _Workspace()
    assert journal.read(workspace, app_name=APP_NAME) is None
    opened = _write(workspace, None, OPEN)
    started = _write(workspace, opened, {"state": "window_started", "start_deployment_id": "d-1"})
    proven = _write(workspace, started, {"state": "window_proven"})
    closed = _write(
        workspace, proven, {"state": "closed", "repinned_deployment_id": "d-1", "closed_by": "repin"}
    )
    assert closed["blue_deployment_id"] == "deployment-blue", "later states keep the window's facts"
    assert journal.state_of(journal.read(workspace, app_name=APP_NAME)) == "closed"
    reopened = _write(workspace, closed, {**OPEN, "window_sha256": "d" * 64})
    assert reopened["start_deployment_id"] == "", "a new window starts from a clean record"


@pytest.mark.parametrize(
    ("path", "target"),
    (
        ((), "window_started"),
        (("window_open",), "window_proven"),
        (("window_open", "window_started", "window_refused"), "window_started"),
        (("window_open", "window_started"), "closed"),
    ),
)
def test_a_transition_out_of_order_refuses(path: tuple[str, ...], target: str) -> None:
    workspace = _Workspace()
    current = None
    for state in path:
        current = _write(workspace, current, OPEN if state == "window_open" else {"state": state})
    with pytest.raises(RuntimeError, match="transition is not allowed"):
        _write(workspace, current, {"state": target})


def test_a_write_against_a_stale_read_refuses() -> None:
    workspace = _Workspace()
    opened = _write(workspace, None, OPEN)
    _write(workspace, opened, {"state": "window_started", "start_deployment_id": "d-1"})
    with pytest.raises(RuntimeError, match="changed since it was read"):
        _write(workspace, opened, {"state": "window_started", "start_deployment_id": "d-2"})


def test_a_tampered_journal_fails_its_signature() -> None:
    workspace = _Workspace()
    _write(workspace, None, OPEN)
    path = journal.journal_path(APP_NAME)
    record = json.loads(workspace.workspace.data[path])
    record["blue_restorable"] = False
    workspace.workspace.data[path] = json.dumps(record).encode()
    with pytest.raises(RuntimeError, match="signature is invalid"):
        journal.read(workspace, app_name=APP_NAME)


def test_an_unreviewed_field_never_reaches_the_journal() -> None:
    with pytest.raises(RuntimeError, match="unreviewed field"):
        _write(_Workspace(), None, {**OPEN, "note": "free text"})


def test_the_write_asserts_the_lease_before_it_uploads() -> None:
    workspace = _Workspace()

    def lost() -> None:
        raise RuntimeError("lease lost")

    with pytest.raises(RuntimeError, match="lease lost"):
        journal.write(workspace, app_name=APP_NAME, expected=None, changes=OPEN, assert_lease_held=lost)
    assert journal.read(workspace, app_name=APP_NAME) is None
