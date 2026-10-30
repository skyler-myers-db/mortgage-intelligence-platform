"""``recovery_state`` is the journal's signed recovery state, read without the lease.

The deploy's pre-lease bundle plan gate reads it to defer an App an
interrupted first install left unbound. It must classify exactly as
``status`` does, need no lease (none is held before the gate), and write
nothing; ``status`` keeps asserting the lease, since only it may authorize a
recovery mutation.
"""

from __future__ import annotations

import pytest

from backend.services.ai_gateway_proof_attestation import derive_gateway_proof_verify_key
from tests.unit.test_app_deployment_lease import (
    SIGNING_KEY,
    WRITER_ID,
    _claim_created_app,
    _first_install_payload,
    _journaled_app,
    _missing_rollback,
    _workspace,
)
from tools.databricks import app_deployment_lease as lease
from tools.databricks import app_first_install_journal as first_install

COMMON = {
    "app_name": "mip-app",
    "rollback_scope": "mip-app-rollback",
    "expected_lakebase_instance": "mip-lakebase",
}


@pytest.fixture(autouse=True)
def _keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.setenv(
        "MIP_AI_GATEWAY_PROOF_VERIFY_KEY", derive_gateway_proof_verify_key(SIGNING_KEY)
    )
    monkeypatch.setenv("DATABRICKS_AGENT_RUNTIME_CLIENT_ID", WRITER_ID)


def _interrupted_first_install(monkeypatch: pytest.MonkeyPatch, *, claimed: bool) -> object:
    """A journal-owned App left by a released lease, as the next run's gate sees it."""

    workspace = _workspace()
    creation = lease.acquire(workspace, app_name="mip-app", source_git_sha="a" * 40)
    marked = first_install.prepare(
        workspace,
        app_name="mip-app",
        lease_id=creation,
        source_git_sha="a" * 40,
        payload=_first_install_payload(),
    )
    workspace.apps.app = _journaled_app(marked)  # type: ignore[attr-defined]
    if claimed:
        _claim_created_app(workspace, lease_id=creation)
    monkeypatch.setattr(first_install, "_load_record", _missing_rollback)
    lease.release(workspace, app_name="mip-app", lease_id=creation)
    return workspace


@pytest.mark.parametrize(("claimed", "state"), ((False, "unclaimed"), (True, "recover")))
def test_the_bare_read_needs_no_lease_and_matches_status(
    monkeypatch: pytest.MonkeyPatch, claimed: bool, state: str
) -> None:
    workspace = _interrupted_first_install(monkeypatch, claimed=claimed)
    files_before = dict(workspace.workspace.data)  # type: ignore[attr-defined]

    assert first_install.recovery_state(workspace, **COMMON) == state
    assert workspace.workspace.data == files_before, "the bare read wrote nothing"  # type: ignore[attr-defined]
    assert workspace.apps.delete_calls == 0  # type: ignore[attr-defined]

    held = lease.acquire(workspace, app_name="mip-app", source_git_sha="b" * 40)
    assert (
        first_install.status(workspace, lease_id=held, source_git_sha="b" * 40, **COMMON) == state
    )


def test_status_still_requires_the_held_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = _interrupted_first_install(monkeypatch, claimed=True)

    with pytest.raises(RuntimeError):
        first_install.status(workspace, lease_id="not-held", source_git_sha="b" * 40, **COMMON)
    assert first_install.recovery_state(workspace, **COMMON) == "recover"


def test_an_app_the_journal_does_not_own_is_never_read_as_owned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _interrupted_first_install(monkeypatch, claimed=True)
    workspace.apps.app.description = "someone else's App"  # type: ignore[attr-defined]

    with pytest.raises(RuntimeError, match="does not match the signed first-install"):
        first_install.recovery_state(workspace, **COMMON)


def test_no_journal_reads_absent() -> None:
    assert first_install.recovery_state(_workspace(), **COMMON) == "absent"
