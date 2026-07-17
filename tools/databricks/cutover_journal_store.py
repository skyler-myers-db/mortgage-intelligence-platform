"""Signed workspace storage for destructive agent cutover journals."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from typing import Any

from databricks.sdk.errors import NotFound, ResourceDoesNotExist
from databricks.sdk.service.workspace import ImportFormat
from tools.databricks.cutover_journal_attestation import (
    sign_cutover_journal,
    verify_cutover_journal,
)

CUTOVER_JOURNAL_NAME = ".mip-agent-runtime-cutover.json"


def journal_path(application_id: str) -> str:
    normalized = application_id.strip()
    if not normalized or "/" in normalized or normalized in {".", ".."}:
        raise ValueError("agent-runtime application ID is invalid for workspace storage")
    return f"/Users/{normalized}/{CUTOVER_JOURNAL_NAME}"


def _load_payload(workspace: Any, *, runtime_application_id: str) -> dict[str, Any] | None:
    try:
        stream = workspace.workspace.download(journal_path(runtime_application_id))
    except (NotFound, ResourceDoesNotExist):
        return None
    raw = stream.read()
    try:
        payload = json.loads(bytes(raw).decode("utf-8"))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("agent cutover journal is invalid") from exc
    if not isinstance(payload, dict) or payload.get("version") != 3:
        raise RuntimeError("agent cutover journal version is invalid")
    verify_cutover_journal(payload)
    return payload


def _journal_values(payload: dict[str, Any]) -> dict[str, str]:
    supervisor_keys = {
        "canonical_name",
        "old_id",
        "old_endpoint",
        "old_endpoint_id",
        "old_creator",
        "old_create_time",
    }
    gateway_keys = {
        "old_gateway_endpoint",
        "old_gateway_endpoint_id",
        "old_gateway_creator",
        "old_gateway_delete_allowed",
    }
    values = {"canonical_name": str(payload.get("canonical_name") or "").strip()}
    supervisor = {
        key: str(payload.get(key) or "").strip() for key in supervisor_keys - {"canonical_name"}
    }
    gateway = {key: str(payload.get(key) or "").strip() for key in gateway_keys}
    if not values["canonical_name"]:
        raise RuntimeError("agent cutover journal is incomplete")
    if any(supervisor.values()) and not all(supervisor.values()):
        raise RuntimeError("agent cutover journal has an incomplete Supervisor tuple")
    if any(gateway.values()) and not all(gateway.values()):
        raise RuntimeError("agent cutover journal has an incomplete Gateway tuple")
    if gateway.get("old_gateway_delete_allowed") not in {"", "0", "1"}:
        raise RuntimeError("agent cutover journal has an invalid Gateway deletion policy")
    if not any(supervisor.values()) and not any(gateway.values()):
        raise RuntimeError("agent cutover journal has no pinned runtime resource")
    values.update({key: value for key, value in supervisor.items() if value})
    values.update({key: value for key, value in gateway.items() if value})
    return values


def read_cutover_journal(
    workspace: Any,
    *,
    runtime_application_id: str,
) -> dict[str, str] | None:
    """Read, authenticate, and validate one complete destructive tuple."""

    payload = read_signed_cutover_journal(
        workspace,
        runtime_application_id=runtime_application_id,
    )
    return None if payload is None else _journal_values(payload)


def read_signed_cutover_journal(
    workspace: Any,
    *,
    runtime_application_id: str,
) -> dict[str, Any] | None:
    """Read one complete journal while retaining its exact signed envelope."""

    payload = _load_payload(
        workspace,
        runtime_application_id=runtime_application_id,
    )
    if payload is not None:
        _journal_values(payload)
    return payload


def clear_cutover_journal_exact(
    workspace: Any,
    *,
    runtime_application_id: str,
) -> None:
    """Delete only the authenticated journal and prove authoritative absence."""

    expected = read_signed_cutover_journal(
        workspace,
        runtime_application_id=runtime_application_id,
    )
    if expected is None:
        return
    immediately_before = read_signed_cutover_journal(
        workspace,
        runtime_application_id=runtime_application_id,
    )
    if immediately_before != expected:
        raise RuntimeError("signed cutover journal changed before exact deletion")
    try:
        workspace.workspace.delete(journal_path(runtime_application_id))
    except Exception as delete_error:
        try:
            after_error = read_signed_cutover_journal(
                workspace,
                runtime_application_id=runtime_application_id,
            )
        except Exception as read_error:
            raise RuntimeError(
                "signed cutover journal state could not be authenticated after ambiguous deletion"
            ) from read_error
        if after_error is None:
            return
        if after_error != expected:
            raise RuntimeError(
                "signed cutover journal changed during ambiguous deletion; refusing retry"
            ) from delete_error
        raise RuntimeError(
            "signed cutover journal remained after ambiguous deletion; refusing retry"
        ) from delete_error
    remaining = read_signed_cutover_journal(
        workspace,
        runtime_application_id=runtime_application_id,
    )
    if remaining is None:
        return
    if remaining != expected:
        raise RuntimeError("signed cutover journal changed during exact deletion")
    raise RuntimeError("signed cutover journal remained after exact deletion")


def persist_cutover_journal(
    workspace: Any,
    *,
    runtime_application_id: str,
    payload: dict[str, Any],
) -> None:
    """Sign and persist a validated journal under the current proof key."""

    path = journal_path(runtime_application_id)
    workspace.workspace.mkdirs(str(Path(path).parent))
    signed_payload = sign_cutover_journal(payload)
    workspace.workspace.upload(
        path,
        io.BytesIO(json.dumps(signed_payload, sort_keys=True).encode("utf-8")),
        format=ImportFormat.AUTO,
        overwrite=True,
    )


def refresh_cutover_journal_attestation(
    workspace: Any,
    *,
    runtime_application_id: str,
) -> None:
    """Re-sign a previous-key journal under deploy authority's current key."""

    payload = _load_payload(
        workspace,
        runtime_application_id=runtime_application_id,
    )
    if payload is None:
        return
    # Validate tuple completeness before replacing its valid prior signature.
    _journal_values(payload)
    current_verify_key = os.environ.get("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", "").strip()
    if str(payload.get("attestation_verify_key") or "").strip() == current_verify_key:
        return
    persist_cutover_journal(
        workspace,
        runtime_application_id=runtime_application_id,
        payload=payload,
    )


def assert_retirement_journal(
    workspace: Any,
    *,
    runtime_application_id: str,
    canonical_name: str,
    old_id: str | None,
    old_endpoint: str | None,
    old_endpoint_id: str | None,
    old_creator: str | None,
    old_create_time: str | None,
    old_gateway_endpoint: str | None,
    old_gateway_endpoint_id: str | None,
    old_gateway_creator: str | None,
    old_gateway_delete_allowed: bool,
) -> None:
    """Authorize every destructive argument against the current signed record."""

    supervisor = (old_id, old_endpoint, old_endpoint_id, old_creator, old_create_time)
    if any(supervisor) and not all(supervisor):
        raise RuntimeError("old Supervisor retirement tuple is incomplete")
    gateway = (old_gateway_endpoint, old_gateway_endpoint_id, old_gateway_creator)
    if (any(gateway) or old_gateway_delete_allowed) and not all(gateway):
        raise RuntimeError("old Gateway retirement tuple is incomplete")
    requested = {"canonical_name": canonical_name}
    if all(supervisor):
        requested.update(
            old_id=str(old_id),
            old_endpoint=str(old_endpoint),
            old_endpoint_id=str(old_endpoint_id),
            old_creator=str(old_creator),
            old_create_time=str(old_create_time),
        )
    if all(gateway):
        requested.update(
            old_gateway_endpoint=str(old_gateway_endpoint),
            old_gateway_endpoint_id=str(old_gateway_endpoint_id),
            old_gateway_creator=str(old_gateway_creator),
            old_gateway_delete_allowed="1" if old_gateway_delete_allowed else "0",
        )
    journal = read_cutover_journal(
        workspace,
        runtime_application_id=runtime_application_id,
    )
    if journal is None:
        raise RuntimeError("destructive agent retirement requires a signed cutover journal")
    if journal != requested:
        raise RuntimeError("destructive agent retirement tuple does not match the signed journal")
