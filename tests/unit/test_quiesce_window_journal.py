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


def test_a_window_moves_open_starting_started_proven_closed() -> None:
    workspace = _Workspace()
    assert journal.read(workspace, app_name=APP_NAME) is None
    opened = _write(workspace, None, OPEN)
    starting = _write(workspace, opened, {"state": "window_starting"})
    started = _write(workspace, starting, {"state": "window_started", "start_deployment_id": "d-1"})
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
        (("window_open",), "window_started"),
        (("window_open", "window_starting"), "closed"),
        (("window_open", "window_starting", "window_refused"), "window_started"),
        (("window_open", "window_starting", "window_started"), "closed"),
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
    _write(workspace, opened, {"state": "window_starting"})
    with pytest.raises(RuntimeError, match="changed since it was read"):
        _write(workspace, opened, {"state": "window_starting"})


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


# --- the owner-start close (wave 7c) --------------------------------------------------------

OWNER_CLOSE = {"state": "closed", "closed_by": "owner_start_repin", "repinned_deployment_id": "d-owner"}
PATHS = {
    "window_open": ("window_open",),
    "window_starting": ("window_open", "window_starting"),
    "window_started": ("window_open", "window_starting", "window_started"),
    "window_proven": ("window_open", "window_starting", "window_started", "window_proven"),
    "window_refused": ("window_open", "window_starting", "window_refused"),
}


def _at(workspace: _Workspace, state: str, opened: dict | None = None) -> dict:
    current = None
    for step in PATHS[state]:
        current = _write(workspace, current, (opened or OPEN) if step == "window_open" else {"state": step})
    assert current is not None
    return current


def test_window_open_journals_the_start_baseline_and_later_states_keep_it() -> None:
    workspace = _Workspace()
    current = _at(workspace, "window_refused", {**OPEN, "start_baseline_ids": ["deployment-blue", "deployment-old"]})
    assert current["start_baseline_ids"] == ["deployment-blue", "deployment-old"]
    assert set(current) - {"attestation_alg", "attestation_verify_key", "attestation_signature"} == journal.FIELDS
    assert journal.JOURNAL_VERSION == 2 and "start_baseline_ids" in journal.FIELDS, "the reviewed field set is unchanged"


@pytest.mark.parametrize("state", sorted(PATHS))
def test_an_owner_start_re_pin_closes_every_open_restorable_state(state: str) -> None:
    assert frozenset(PATHS) == journal.OWNER_START_CLOSABLE
    workspace = _Workspace()
    current = _at(workspace, state)
    for incomplete in (
        {**OWNER_CLOSE, "repinned_deployment_id": ""},
        {**OWNER_CLOSE, "repinned_deployment_id": "  "},
        {key: value for key, value in OWNER_CLOSE.items() if key != "repinned_deployment_id"},
    ):
        with pytest.raises(RuntimeError, match="transition is not allowed"):
            _write(workspace, current, incomplete)
    closed = _write(workspace, current, OWNER_CLOSE)
    assert (closed["state"], closed["closed_by"], closed["repinned_deployment_id"]) == ("closed", "owner_start_repin", "d-owner")
    assert closed["start_deployment_id"] == current["start_deployment_id"], "the governed start's id is kept"
    with pytest.raises(RuntimeError, match="transition is not allowed"):
        _write(workspace, closed, OWNER_CLOSE)  # closed is closed


@pytest.mark.parametrize("state", ("window_refused", "window_started"))
@pytest.mark.parametrize("closed_by", ("repin", "deadman_repin", "green_activation", ""))
def test_no_other_close_leaves_a_refused_or_a_started_window(state: str, closed_by: str) -> None:
    workspace = _Workspace()
    current = _at(workspace, state)
    with pytest.raises(RuntimeError, match="transition is not allowed"):
        _write(workspace, current, {"state": "closed", "closed_by": closed_by, "repinned_deployment_id": "d-1"})
    assert journal.read(workspace, app_name=APP_NAME)["state"] == state


def test_a_contract_window_never_takes_the_owner_start_close() -> None:
    # window_open -> closed is a contract window's own transition (green activation), so the
    # journal must refuse that same move when it claims to be an owner-start re-pin.
    workspace = _Workspace()
    contract = _write(workspace, None, {**OPEN, "blue_restorable": False})
    with pytest.raises(RuntimeError, match="transition is not allowed"):
        _write(workspace, contract, OWNER_CLOSE)
    activated = _write(workspace, contract, {"state": "closed", "closed_by": "green_activation"})
    assert activated["closed_by"] == "green_activation"


def test_the_dead_mans_close_is_unchanged() -> None:
    workspace = _Workspace()
    starting = _at(workspace, "window_starting")
    closed = _write(
        workspace, starting, {"state": "closed", "closed_by": "deadman_repin", "repinned_deployment_id": "d-1"}
    )
    assert closed["closed_by"] == "deadman_repin"
