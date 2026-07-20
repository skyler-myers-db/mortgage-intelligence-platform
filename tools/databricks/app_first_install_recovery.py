#!/usr/bin/env python3
"""Reconcile interrupted first-install intent from authoritative audit proof."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from tools.databricks import app_first_install_journal as journal
from tools.databricks.app_deployment_lease import assert_held
from tools.databricks.app_first_install_audit import find_app_create_proof

_AUDIT_POLL_SECONDS = 30.0


def _now() -> datetime:
    return datetime.now(UTC)


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _expected_request(record: dict[str, Any], *, app_name: str) -> dict[str, Any]:
    return {
        "name": app_name,
        "description": record["marked_description"],
        "resources": record["app_resources"],
    }


def _marker(record: dict[str, Any]) -> str:
    return f"[{journal.MARKER_PREFIX}{record['bootstrap_id']}]"


def recover_unclaimed_from_audit(
    workspace: Any,
    *,
    app_name: str,
    lease_id: str,
    source_git_sha: str,
    warehouse_id: str,
    now: datetime | None = None,
) -> None:
    """Claim a crashed create only from its authoritative Apps audit event."""

    assert_held(
        workspace,
        app_name=app_name,
        lease_id=lease_id,
        source_git_sha=source_git_sha,
        now=now,
    )
    record = journal._download(workspace, app_name=app_name)
    if record is None:
        raise RuntimeError("first-install audit recovery has no signed journal")
    claim_fields = (
        record["app_id"],
        record["app_service_principal_client_id"],
        record["app_service_principal_scim_id"],
        record["claimed_at"],
        record["claim_proof_kind"],
        record["create_audit_event_id"],
        record["create_audit_request_id"],
    )
    if any(claim_fields):
        raise RuntimeError("first-install audit recovery requires an unclaimed journal")
    current_workspace_id = journal._text(
        journal._field(getattr(workspace, "config", None), "workspace_id")
    )
    if current_workspace_id != record["workspace_id"]:
        raise RuntimeError("first-install audit recovery workspace changed")
    authorized_from = datetime.fromisoformat(record["prepared_at"])
    authorized_until = datetime.fromisoformat(record["create_authorized_until"])
    settlement_until = datetime.fromisoformat(record["audit_settlement_until"])
    while True:
        assert_held(
            workspace,
            app_name=app_name,
            lease_id=lease_id,
            source_git_sha=source_git_sha,
            now=now,
        )
        journal._assert_app_metadata(workspace, app_name=app_name, record=record)
        try:
            proof = find_app_create_proof(
                workspace,
                warehouse_id=warehouse_id,
                workspace_id=record["workspace_id"],
                actor=record["creator"],
                authorized_from=authorized_from,
                authorized_until=authorized_until,
                marker=_marker(record),
                expected_request=_expected_request(record, app_name=app_name),
            )
            break
        except RuntimeError as exc:
            if str(exc) != "first-install create audit proof is not available yet":
                raise
        current = now or _now()
        if current >= settlement_until:
            raise RuntimeError(
                "first-install create audit proof was unavailable after settlement"
            )
        if now is not None:
            raise RuntimeError("first-install audit settlement remains open; retry later")
        _sleep(min(_AUDIT_POLL_SECONDS, (settlement_until - current).total_seconds()))
    app = journal._assert_app_metadata(workspace, app_name=app_name, record=record)
    app_id = journal._text(journal._field(app, "id"))
    client_id = journal._text(journal._field(app, "service_principal_client_id"))
    scim_id = journal._text(journal._field(app, "service_principal_id"))
    compute = journal._text(
        journal._field(journal._field(app, "compute_status"), "state")
    ).split(".")[-1].upper()
    if (
        app_id != proof.app_id
        or not client_id
        or not scim_id
        or compute != "STOPPED"
        or journal._field(app, "active_deployment") is not None
        or journal._field(app, "pending_deployment") is not None
    ):
        raise RuntimeError(
            "live first-install App does not match its audited source-free creation"
        )
    journal._persist_identity_claim(
        workspace,
        app_name=app_name,
        record=record,
        app_id=app_id,
        client_id=client_id,
        scim_id=scim_id,
        proof_kind="system_access_audit",
        lease_id=lease_id,
        source_git_sha=source_git_sha,
        audit_event_id=proof.event_id,
        audit_request_id=proof.request_id,
        now=now,
    )


def clear_absent(
    workspace: Any,
    *,
    app_name: str,
    lease_id: str,
    source_git_sha: str,
    warehouse_id: str,
    now: datetime | None = None,
) -> None:
    """Clear expired intent only after the signed audit settlement interval."""

    assert_held(
        workspace,
        app_name=app_name,
        lease_id=lease_id,
        source_git_sha=source_git_sha,
        now=now,
    )
    record = journal._download(workspace, app_name=app_name)
    if record is None:
        return
    authorized_until = datetime.fromisoformat(record["create_authorized_until"])
    settlement_until = datetime.fromisoformat(record["audit_settlement_until"])
    while True:
        if journal._app_or_none(workspace, app_name=app_name) is not None:
            raise RuntimeError("cannot clear a first-install journal while its App exists")
        query_started_at = now or _now()
        try:
            find_app_create_proof(
                workspace,
                warehouse_id=warehouse_id,
                workspace_id=record["workspace_id"],
                actor=record["creator"],
                authorized_from=datetime.fromisoformat(record["prepared_at"]),
                authorized_until=authorized_until,
                marker=_marker(record),
                expected_request=_expected_request(record, app_name=app_name),
            )
        except RuntimeError as exc:
            if str(exc) != "first-install create audit proof is not available yet":
                raise
        else:
            raise RuntimeError("cannot clear a first-install journal with audited App creation")
        if journal._app_or_none(workspace, app_name=app_name) is not None:
            raise RuntimeError("cannot clear a first-install journal while its App exists")
        current = now or _now()
        if query_started_at < settlement_until:
            if now is not None:
                raise RuntimeError("first-install audit settlement remains open; retry later")
            if current < settlement_until:
                _sleep(
                    min(
                        _AUDIT_POLL_SECONDS,
                        (settlement_until - current).total_seconds(),
                    )
                )
            continue
        break
    assert_held(
        workspace,
        app_name=app_name,
        lease_id=lease_id,
        source_git_sha=source_git_sha,
        now=current,
    )
    if journal._app_or_none(workspace, app_name=app_name) is not None:
        raise RuntimeError("cannot clear a first-install journal while its App exists")
    journal._delete_record_exact(
        workspace,
        app_name=app_name,
        expected=record,
        lease_id=lease_id,
        source_git_sha=source_git_sha,
        now=current,
    )
