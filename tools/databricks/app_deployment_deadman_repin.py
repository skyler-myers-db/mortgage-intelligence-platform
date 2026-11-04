"""Re-pin the deployment the dead-man started, under the next run's lease (M0-PR9).

``app_deployment_rollback repin --from-deadman``: a runner died inside a
restorable quiesce window, and the dead-man (``jobs/window_deadman.py``)
started blue at the deadline. Its marker is unsigned, so it is a pointer and
never proof. Before anything is written every claim is re-proved against the
signed window journal and the App, with the window's own start-and-re-pin
proofs:

1. the journal verifies (signature, key registry, reviewed fields), reads
   ``window_open`` and is restorable;
2. the marker of exactly that window (its token digests the journal's window
   sha256 and lease) reads ``deadman_restarted``, names that window's blue,
   and its start deployment is not blue;
3. the window's ``await`` proofs, read-only: the App's compute is ACTIVE and
   its exact active deployment is that start deployment (``get_deployment``
   returns that id), and blue's restore folder still has the journal's
   full-tree digest;
4. ``repin_started_deployment`` (``app_deployment_repin``): the signed record
   still names blue; the start deployment is SUCCEEDED, the App's exact
   active deployment, the newest SUCCEEDED, with nothing pending; its source
   is blue's restore source, its ``/src/`` snapshot has the journal's
   full-tree digest and its env-name set is the record's; the live bindings
   are the record's or exactly this run's bundle payload; health against it;
   then a new record version (same provenance, ``predecessor_deployment_id``)
   under this run's lease, read back, and health again;
5. the journal closes (``closed_by: deadman_repin``), under this run's lease.

Any refusal leaves the App serving the dead-man's start, the record and the
journal as they were, and the run exits before step 0a's signed-blue proof.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tools.databricks import quiesce_window_journal as journal
from tools.databricks.app_deployment_state import active_deployment_id
from tools.databricks.app_rollback_record_contract import _text
from tools.databricks.app_start_deployment import tree_digest
from tools.databricks.quiesce_window_deadman import read_marker, window_token

RECOVERABLE_MARKER = "deadman_restarted"


class DeadmanRepinRefused(RuntimeError):
    """The dead-man's restart is not provably the window's blue; nothing was written."""


def proven_deadman_start(
    workspace: Any, *, app_name: str, digest: Callable[[Any, str], str] = tree_digest
) -> tuple[dict[str, Any], str]:
    """The open window and the start deployment its dead-man recorded, re-proved read-only."""

    current = journal.read(workspace, app_name=app_name)
    if journal.state_of(current) != "window_open" or current is None:
        raise DeadmanRepinRefused("no window is open: there is nothing the dead-man restarted")
    if current.get("blue_restorable") is not True:
        raise DeadmanRepinRefused("a contract window is never restarted from blue")
    token = window_token(str(current["window_sha256"]), str(current["lease_id"]))
    marker = read_marker(workspace, app_name=app_name, token=token)
    if marker is None or marker.get("state") != RECOVERABLE_MARKER:
        raise DeadmanRepinRefused("the window's dead-man recorded no restart")
    if (
        marker.get("app_name") != app_name
        or marker.get("token") != token
        or marker.get("window_sha256") != current["window_sha256"]
        or marker.get("blue_deployment_id") != current["blue_deployment_id"]
    ):
        raise DeadmanRepinRefused("the dead-man's marker is not this window's")
    start_id = str(marker.get("start_deployment_id") or "").strip()
    if not start_id or start_id == current["blue_deployment_id"]:
        raise DeadmanRepinRefused("the dead-man's marker names no new start deployment")
    app = workspace.apps.get(app_name)
    state = _text(getattr(getattr(app, "compute_status", None), "state", None))
    if state.split(".")[-1].upper() != "ACTIVE":
        raise DeadmanRepinRefused("the App compute is not ACTIVE")
    started = workspace.apps.get_deployment(app_name, start_id)
    if (
        _text(getattr(started, "deployment_id", None)) != start_id
        or active_deployment_id(workspace, app_name=app_name) != start_id
    ):
        raise DeadmanRepinRefused("the App does not serve the dead-man's start deployment")
    if digest(workspace, str(current["restore_source"])) != current["source_tree_sha256"]:
        raise DeadmanRepinRefused("blue's restore source changed since the window opened")
    return current, start_id


def repin_from_deadman(
    workspace: Any,
    *,
    app_name: str,
    deployment_lease_id: str,
    deployment_source_git_sha: str,
    repin: Callable[..., dict[str, Any]],
    assert_lease_held: Callable[[], None],
    applied_app_resources: list[dict[str, object]] | None = None,
) -> dict[str, Any]:
    """Prove and re-pin the dead-man's start under this lease, then close its journal."""

    if not deployment_lease_id or not deployment_source_git_sha:
        raise DeadmanRepinRefused("the re-pin runs only under this run's lease")
    current, start_id = proven_deadman_start(workspace, app_name=app_name)
    record = repin(
        deployment_id=start_id,
        expected_rollback_deployment_id=str(current["blue_deployment_id"]),
        expected_tree_sha256=str(current["source_tree_sha256"]),
        applied_app_resources=applied_app_resources,
    )
    if record.get("deployment_id") != start_id:
        raise DeadmanRepinRefused("the re-pinned record does not name the dead-man's start")
    journal.write(
        workspace,
        app_name=app_name,
        expected=current,
        changes={
            "state": "closed",
            "start_deployment_id": start_id,
            "repinned_deployment_id": start_id,
            "closed_by": "deadman_repin",
        },
        assert_lease_held=assert_lease_held,
    )
    return record


def repin_deadman_start(
    workspace: Any,
    *,
    app_name: str,
    scope: str,
    base_url: str,
    bearer_token: str,
    deployment_lease_id: str,
    deployment_source_git_sha: str,
    applied_app_resources: list[dict[str, object]] | None = None,
) -> dict[str, Any]:
    """The CLI's ``repin --from-deadman``: the rollback's own re-pin, then the journal close."""

    from tools.databricks import app_deployment_rollback as rollback

    def repin(**proofs: Any) -> dict[str, Any]:
        return rollback.repin_started_deployment(
            workspace,
            app_name=app_name,
            scope=scope,
            base_url=base_url,
            bearer_token=bearer_token,
            deployment_lease_id=deployment_lease_id,
            deployment_source_git_sha=deployment_source_git_sha,
            **proofs,
        )

    return repin_from_deadman(
        workspace,
        app_name=app_name,
        deployment_lease_id=deployment_lease_id,
        deployment_source_git_sha=deployment_source_git_sha,
        repin=repin,
        assert_lease_held=rollback.held_assertion(
            workspace,
            app_name=app_name,
            lease_id=deployment_lease_id,
            source_git_sha=deployment_source_git_sha,
            operation="dead-man window close",
        ),
        applied_app_resources=applied_app_resources,
    )
