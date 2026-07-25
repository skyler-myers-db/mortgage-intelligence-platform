"""Proof-authority journal for crash-safe managed-Supervisor creation."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from backend.agents.supervisor_contract import (
    SUPERVISOR_INSTRUCTIONS,
    canonical_supervisor_contract_json,
)
from backend.services.ai_gateway_proof_attestation import (
    derive_gateway_proof_verify_key,
)
from databricks.sdk.errors import NotFound, ResourceDoesNotExist
from databricks.sdk.service.workspace import ImportFormat
from tools.databricks.app_deployment_lease import LEASE_ROOT, assert_held
from tools.databricks.app_deployment_lease_support import key_registry

JOURNAL_VERSION = 2
SUPPORTED_JOURNAL_VERSIONS = frozenset({1, 2})
ATTESTATION_ALGORITHM = "ed25519-supervisor-creation-v1"
MARKER_PREFIX = "mip-supervisor-create:"
CREATE_AUTHORIZATION_WINDOW = timedelta(minutes=15)
AUDIT_SETTLEMENT_DELAY = timedelta(hours=1)
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}\Z")
_APP = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_SIGNED_FIELDS = {
    "attestation_algorithm",
    "attestation_signature",
    "attestation_verify_key",
}


def _text(value: object) -> str:
    return str(getattr(value, "value", value) or "").strip()


def _field(value: object, name: str) -> object:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _decode(value: str, *, length: int) -> bytes:
    try:
        decoded = base64.urlsafe_b64decode(value.strip() + "=" * (-len(value.strip()) % 4))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Supervisor creation journal key is invalid") from exc
    if len(decoded) != length:
        raise RuntimeError("Supervisor creation journal key has an invalid length")
    return decoded


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _canonical(record: dict[str, Any]) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _message(record: dict[str, Any]) -> bytes:
    unsigned = {key: value for key, value in record.items() if key not in _SIGNED_FIELDS}
    return b"mip-supervisor-creation-v1\0" + _canonical(unsigned).encode("utf-8")


def _sign(record: dict[str, Any]) -> dict[str, Any]:
    signing = os.environ.get("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", "").strip()
    verify = os.environ.get("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", "").strip()
    if not signing or not verify or derive_gateway_proof_verify_key(signing) != verify:
        raise RuntimeError("Supervisor creation journal signing identity is invalid")
    private = Ed25519PrivateKey.from_private_bytes(_decode(signing, length=32))
    unsigned = {key: value for key, value in record.items() if key not in _SIGNED_FIELDS}
    return {
        **unsigned,
        "attestation_algorithm": ATTESTATION_ALGORITHM,
        "attestation_verify_key": verify,
        "attestation_signature": _encode(private.sign(_message(unsigned))),
    }


def _verify(record: object) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise RuntimeError("Supervisor creation journal is malformed")
    verify = _text(record.get("attestation_verify_key"))
    if record.get("attestation_algorithm") != ATTESTATION_ALGORITHM or verify not in key_registry():
        raise RuntimeError("Supervisor creation journal attestation identity is invalid")
    try:
        Ed25519PublicKey.from_public_bytes(_decode(verify, length=32)).verify(
            _decode(_text(record.get("attestation_signature")), length=64),
            _message(record),
        )
    except (InvalidSignature, RuntimeError, ValueError) as exc:
        raise RuntimeError("Supervisor creation journal signature is invalid") from exc
    return dict(record)


def path(app_name: str) -> str:
    normalized = app_name.strip()
    if _APP.fullmatch(normalized) is None:
        raise ValueError("Supervisor creation journal App name is invalid")
    return f"{LEASE_ROOT}/{normalized}.supervisor-creation.json"


def marker(intent_id: str) -> str:
    return f"[{MARKER_PREFIX}{str(UUID(intent_id))}]"


def temporary_name(target_name: str, intent_id: str) -> str:
    return f"{target_name} {marker(intent_id)}"


def temporary_instructions(
    intent_id: str,
    *,
    instructions: str | None = None,
) -> str:
    reviewed = SUPERVISOR_INSTRUCTIONS if instructions is None else instructions
    return f"{reviewed} {marker(intent_id)}"


def _workspace_id(workspace: Any) -> str:
    workspace_id = _text(workspace.get_workspace_id())
    if not workspace_id.isdecimal():
        raise RuntimeError("Supervisor creation journal workspace identity is unavailable")
    return workspace_id


def _contract(
    *,
    genie_space_id: str,
    catalog: str,
) -> tuple[str, str]:
    contract_json = canonical_supervisor_contract_json(
        genie_space_id=genie_space_id,
        catalog=catalog,
    )
    return contract_json, hashlib.sha256(contract_json.encode("utf-8")).hexdigest()


def matches_current_policy(
    record: dict[str, Any],
    *,
    canonical_name: str,
    genie_space_id: str,
    catalog: str,
) -> bool:
    """Return whether the signed snapshot remains authorized by successor code."""

    contract_json, contract_hash = _contract(
        genie_space_id=genie_space_id,
        catalog=catalog,
    )
    return (
        record.get("disposition", "active") == "active"
        and record["canonical_name"] == canonical_name
        and record["genie_space_id"] == genie_space_id
        and record["catalog"] == catalog
        and record["contract_json"] == contract_json
        and record["contract_sha256"] == contract_hash
    )


def _required_fields(version: int) -> set[str]:
    fields = {
        "version",
        "app_name",
        "intent_id",
        "origin_lease_id",
        "admitted_lease_id",
        "recovery_root_lease_id",
        "origin_source_git_sha",
        "admitted_source_git_sha",
        "workspace_id",
        "runtime_application_id",
        "canonical_name",
        "target_name",
        "temporary_name",
        "temporary_instructions",
        "genie_space_id",
        "catalog",
        "contract_json",
        "contract_sha256",
        "prepared_at",
        "create_authorized_until",
        "audit_settlement_until",
        "supervisor_id",
        "endpoint",
        "endpoint_id",
        "creator",
        "create_time",
        "claimed_at",
        "claim_proof_kind",
        "create_audit_event_id",
        "create_audit_request_id",
        *_SIGNED_FIELDS,
    }
    if version >= 2:
        fields.add("disposition")
    return fields


def _historical_contract(record: dict[str, Any]) -> tuple[dict[str, Any], str, str]:
    """Validate the immutable contract snapshot without rebuilding successor code."""

    contract_json = record["contract_json"]
    try:
        contract = json.loads(contract_json)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Supervisor creation journal contract is malformed") from exc
    if (
        not isinstance(contract, dict)
        or set(contract) != {"description", "instructions", "tools", "examples"}
        or not isinstance(contract["description"], str)
        or not contract["description"].strip()
        or not isinstance(contract["instructions"], str)
        or not contract["instructions"].strip()
        or not isinstance(contract["tools"], list)
        or not contract["tools"]
        or contract["examples"] != []
    ):
        raise RuntimeError("Supervisor creation journal contract is malformed")
    tool_ids: set[str] = set()
    for tool in contract["tools"]:
        if not isinstance(tool, dict):
            raise RuntimeError("Supervisor creation journal contract is malformed")
        tool_id = _text(tool.get("tool_id"))
        tool_type = _text(tool.get("tool_type"))
        if (
            not tool_id
            or _ID.fullmatch(tool_id) is None
            or tool_id in tool_ids
            or not tool_type
            or _ID.fullmatch(tool_type) is None
            or set(tool) != {"tool_id", "tool_type", "description", tool_type}
            or not isinstance(tool.get("description"), str)
            or not _text(tool.get("description"))
            or not isinstance(tool.get(tool_type), dict)
        ):
            raise RuntimeError("Supervisor creation journal contract is malformed")
        tool_ids.add(tool_id)
    canonical = json.dumps(
        contract,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    contract_hash = hashlib.sha256(contract_json.encode("utf-8")).hexdigest()
    return contract, canonical, contract_hash


def validated_record(
    value: object,
    *,
    app_name: str,
    workspace_id: str,
    runtime_application_id: str,
) -> dict[str, Any]:
    record = _verify(value)
    version = record.get("version")
    if (
        type(version) is not int
        or version not in SUPPORTED_JOURNAL_VERSIONS
        or set(record) != _required_fields(version)
    ):
        raise RuntimeError("Supervisor creation journal is incomplete")
    strings = _required_fields(version) - {"version"}
    if any(not isinstance(record.get(field), str) for field in strings):
        raise RuntimeError("Supervisor creation journal is malformed")
    try:
        intent_id = str(UUID(record["intent_id"]))
        origin_lease_id = str(UUID(record["origin_lease_id"]))
        admitted_lease_id = str(UUID(record["admitted_lease_id"]))
        recovery_root = str(UUID(record["recovery_root_lease_id"]))
        prepared_at = datetime.fromisoformat(record["prepared_at"])
        authorized_until = datetime.fromisoformat(record["create_authorized_until"])
        settlement_until = datetime.fromisoformat(record["audit_settlement_until"])
    except ValueError as exc:
        raise RuntimeError("Supervisor creation journal identity is invalid") from exc
    contract, contract_json, contract_hash = _historical_contract(record)
    identity = tuple(
        _text(record[field])
        for field in (
            "supervisor_id",
            "endpoint",
            "endpoint_id",
            "creator",
            "create_time",
            "claimed_at",
            "claim_proof_kind",
        )
    )
    audit = (
        _text(record["create_audit_event_id"]),
        _text(record["create_audit_request_id"]),
    )
    origin_source_sha = _text(record["origin_source_git_sha"])
    admitted_source_sha = _text(record["admitted_source_git_sha"])
    if (
        record["app_name"] != app_name
        or record["workspace_id"] != workspace_id
        or record["runtime_application_id"] != runtime_application_id
        or any(
            len(source_sha) != 40
            or any(character not in "0123456789abcdef" for character in source_sha)
            for source_sha in (origin_source_sha, admitted_source_sha)
        )
        or not record["canonical_name"]
        or not record["target_name"]
        or record["temporary_name"] != temporary_name(record["target_name"], intent_id)
        or record["temporary_instructions"]
        != temporary_instructions(
            intent_id,
            instructions=contract["instructions"],
        )
        or record["contract_json"] != contract_json
        or record["contract_sha256"] != contract_hash
        or (version >= 2 and record["disposition"] not in {"active", "retire_only"})
        or prepared_at.tzinfo is None
        or authorized_until.tzinfo is None
        or settlement_until.tzinfo is None
        or authorized_until <= prepared_at
        or settlement_until != authorized_until + AUDIT_SETTLEMENT_DELAY
        or (any(identity) and not all(identity))
        or (any(audit) and not all(audit))
        or (not any(identity) and any(audit))
        or (
            all(identity)
            and record["creator"] != runtime_application_id
            and record["creator"].casefold() != runtime_application_id.casefold()
        )
        or (
            all(identity)
            and record["claim_proof_kind"] not in {"create_response", "system_access_audit"}
        )
        or (record["claim_proof_kind"] == "create_response" and any(audit))
        or (record["claim_proof_kind"] == "system_access_audit" and not all(audit))
    ):
        raise RuntimeError("Supervisor creation journal scope or claim is invalid")
    return {
        **record,
        "intent_id": intent_id,
        "origin_lease_id": origin_lease_id,
        "admitted_lease_id": admitted_lease_id,
        "recovery_root_lease_id": recovery_root,
        "origin_source_git_sha": origin_source_sha,
        "admitted_source_git_sha": admitted_source_sha,
        "contract_json": contract_json,
        "contract_sha256": contract_hash,
    }


def download(
    workspace: Any,
    *,
    app_name: str,
    runtime_application_id: str,
) -> dict[str, Any] | None:
    try:
        stream = workspace.workspace.download(path(app_name))
    except (NotFound, ResourceDoesNotExist):
        return None
    try:
        value = json.loads(stream.read().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Supervisor creation journal is not valid JSON") from exc
    return validated_record(
        value,
        app_name=app_name,
        workspace_id=_workspace_id(workspace),
        runtime_application_id=runtime_application_id,
    )


def _upload(
    workspace: Any,
    *,
    app_name: str,
    runtime_application_id: str,
    record: dict[str, Any],
    expected: dict[str, Any] | None,
) -> None:
    signed = validated_record(
        _sign(record),
        app_name=app_name,
        workspace_id=_workspace_id(workspace),
        runtime_application_id=runtime_application_id,
    )
    if (
        download(
            workspace,
            app_name=app_name,
            runtime_application_id=runtime_application_id,
        )
        != expected
    ):
        raise RuntimeError("Supervisor creation journal changed before persistence")
    try:
        workspace.workspace.upload(
            path(app_name),
            io.BytesIO(_canonical(signed).encode("utf-8")),
            format=ImportFormat.AUTO,
            overwrite=expected is not None,
        )
    except Exception as write_error:  # noqa: BLE001 - resolve ambiguous server commit
        if (
            download(
                workspace,
                app_name=app_name,
                runtime_application_id=runtime_application_id,
            )
            != signed
        ):
            raise RuntimeError(
                "Supervisor creation journal write did not commit exactly"
            ) from write_error
    if (
        download(
            workspace,
            app_name=app_name,
            runtime_application_id=runtime_application_id,
        )
        != signed
    ):
        raise RuntimeError("Supervisor creation journal did not persist exactly")


def prepare(
    workspace: Any,
    *,
    app_name: str,
    lease_id: str,
    source_git_sha: str,
    runtime_application_id: str,
    canonical_name: str,
    target_name: str,
    genie_space_id: str,
    catalog: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Persist a signed, temporary-name create intent before runtime mutation."""

    lease = assert_held(
        workspace,
        app_name=app_name,
        lease_id=lease_id,
        source_git_sha=source_git_sha,
        now=now,
    )
    workspace_id = _workspace_id(workspace)
    existing = download(
        workspace,
        app_name=app_name,
        runtime_application_id=runtime_application_id,
    )
    static_scope = (
        app_name,
        _text(lease.get("recovery_root_lease_id")),
        workspace_id,
        runtime_application_id,
    )
    if existing is not None:
        observed = tuple(
            existing[key]
            for key in (
                "app_name",
                "recovery_root_lease_id",
                "workspace_id",
                "runtime_application_id",
            )
        )
        if observed != static_scope:
            raise RuntimeError(
                "pending Supervisor creation journal belongs to another recovery scope"
            )
        disposition = (
            "retire_only"
            if existing.get("disposition") == "retire_only"
            or not matches_current_policy(
                existing,
                canonical_name=canonical_name,
                genie_space_id=genie_space_id,
                catalog=catalog,
            )
            else "active"
        )
        if (
            existing["version"] == JOURNAL_VERSION
            and existing.get("disposition") == disposition
            and existing["admitted_lease_id"] == lease_id
            and existing["admitted_source_git_sha"] == source_git_sha
        ):
            return existing
        adopted = {
            **existing,
            "version": JOURNAL_VERSION,
            "disposition": disposition,
            "admitted_lease_id": lease_id,
            "admitted_source_git_sha": source_git_sha,
        }
        _upload(
            workspace,
            app_name=app_name,
            runtime_application_id=runtime_application_id,
            record=adopted,
            expected=existing,
        )
        return (
            download(
                workspace,
                app_name=app_name,
                runtime_application_id=runtime_application_id,
            )
            or {}
        )
    contract_json, contract_hash = _contract(
        genie_space_id=genie_space_id,
        catalog=catalog,
    )
    prepared = now or datetime.now(UTC)
    try:
        lease_expiry = datetime.fromisoformat(_text(lease.get("expires_at")))
    except ValueError as exc:
        raise RuntimeError("Supervisor creation lease expiration is invalid") from exc
    if lease_expiry.tzinfo is None or lease_expiry <= prepared:
        raise RuntimeError("Supervisor creation lease is expired")
    authorized_until = min(lease_expiry, prepared + CREATE_AUTHORIZATION_WINDOW)
    intent_id = str(uuid4())
    record = {
        "version": JOURNAL_VERSION,
        "disposition": "active",
        "app_name": app_name,
        "intent_id": intent_id,
        "origin_lease_id": lease_id,
        "admitted_lease_id": lease_id,
        "recovery_root_lease_id": _text(lease.get("recovery_root_lease_id")),
        "origin_source_git_sha": source_git_sha,
        "admitted_source_git_sha": source_git_sha,
        "workspace_id": workspace_id,
        "runtime_application_id": runtime_application_id,
        "canonical_name": canonical_name,
        "target_name": target_name,
        "temporary_name": temporary_name(target_name, intent_id),
        "temporary_instructions": temporary_instructions(intent_id),
        "genie_space_id": genie_space_id,
        "catalog": catalog,
        "contract_json": contract_json,
        "contract_sha256": contract_hash,
        "prepared_at": prepared.isoformat(),
        "create_authorized_until": authorized_until.isoformat(),
        "audit_settlement_until": (authorized_until + AUDIT_SETTLEMENT_DELAY).isoformat(),
        "supervisor_id": "",
        "endpoint": "",
        "endpoint_id": "",
        "creator": "",
        "create_time": "",
        "claimed_at": "",
        "claim_proof_kind": "",
        "create_audit_event_id": "",
        "create_audit_request_id": "",
    }
    _upload(
        workspace,
        app_name=app_name,
        runtime_application_id=runtime_application_id,
        record=record,
        expected=None,
    )
    return (
        download(
            workspace,
            app_name=app_name,
            runtime_application_id=runtime_application_id,
        )
        or {}
    )


def claim(
    workspace: Any,
    *,
    app_name: str,
    lease_id: str,
    source_git_sha: str,
    runtime_application_id: str,
    supervisor_id: str,
    endpoint: str,
    endpoint_id: str,
    creator: str,
    create_time: str,
    proof_kind: str,
    audit_event_id: str = "",
    audit_request_id: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Proof-authority transition from signed intent to immutable live tuple."""

    assert_held(
        workspace,
        app_name=app_name,
        lease_id=lease_id,
        source_git_sha=source_git_sha,
        now=now,
    )
    current = download(
        workspace,
        app_name=app_name,
        runtime_application_id=runtime_application_id,
    )
    if current is None:
        raise RuntimeError("Supervisor creation claim has no signed intent")
    if (
        current["admitted_lease_id"] != lease_id
        or current["admitted_source_git_sha"] != source_git_sha
    ):
        raise RuntimeError("Supervisor creation claim uses a stale admitted deployment")
    values = (supervisor_id, endpoint, endpoint_id, creator, create_time)
    if any(_ID.fullmatch(value.strip()) is None for value in values):
        raise RuntimeError("Supervisor creation claim identity is invalid")
    expected_claim = {
        "supervisor_id": supervisor_id,
        "endpoint": endpoint,
        "endpoint_id": endpoint_id,
        "creator": creator,
        "create_time": create_time,
        "claim_proof_kind": proof_kind,
        "create_audit_event_id": audit_event_id,
        "create_audit_request_id": audit_request_id,
    }
    if current["supervisor_id"]:
        if any(current[key] != value for key, value in expected_claim.items()):
            raise RuntimeError("Supervisor creation journal already claims another tuple")
        return current
    claimed = {
        **current,
        **expected_claim,
        "claimed_at": (now or datetime.now(UTC)).isoformat(),
    }
    _upload(
        workspace,
        app_name=app_name,
        runtime_application_id=runtime_application_id,
        record=claimed,
        expected=current,
    )
    return (
        download(
            workspace,
            app_name=app_name,
            runtime_application_id=runtime_application_id,
        )
        or {}
    )


def clear(
    workspace: Any,
    *,
    app_name: str,
    lease_id: str,
    source_git_sha: str,
    runtime_application_id: str,
    expected: dict[str, Any],
) -> None:
    """Delete an exact claim after caller proves binding or durable retirement handoff."""

    assert_held(
        workspace,
        app_name=app_name,
        lease_id=lease_id,
        source_git_sha=source_git_sha,
    )
    if (
        not expected.get("supervisor_id")
        or expected["admitted_lease_id"] != lease_id
        or expected["admitted_source_git_sha"] != source_git_sha
        or download(
            workspace,
            app_name=app_name,
            runtime_application_id=runtime_application_id,
        )
        != expected
    ):
        raise RuntimeError("Supervisor creation journal changed before clear")
    try:
        workspace.workspace.delete(path(app_name))
    except Exception as delete_error:  # noqa: BLE001 - resolve ambiguous server commit
        if (
            download(
                workspace,
                app_name=app_name,
                runtime_application_id=runtime_application_id,
            )
            is not None
        ):
            raise RuntimeError(
                "Supervisor creation journal deletion did not converge"
            ) from delete_error
    if (
        download(
            workspace,
            app_name=app_name,
            runtime_application_id=runtime_application_id,
        )
        is not None
    ):
        raise RuntimeError("Supervisor creation journal remained after clear")


def clear_absent_intent(
    workspace: Any,
    *,
    app_name: str,
    lease_id: str,
    source_git_sha: str,
    runtime_application_id: str,
    expected: dict[str, Any],
    assert_live_absent: Any,
) -> None:
    """Clear one unclaimed intent only after caller proves settled audit absence."""

    assert_held(
        workspace,
        app_name=app_name,
        lease_id=lease_id,
        source_git_sha=source_git_sha,
    )
    if (
        expected.get("supervisor_id")
        or expected["admitted_lease_id"] != lease_id
        or expected["admitted_source_git_sha"] != source_git_sha
        or download(
            workspace,
            app_name=app_name,
            runtime_application_id=runtime_application_id,
        )
        != expected
    ):
        raise RuntimeError("Supervisor creation absent intent changed before clear")
    assert_live_absent()
    try:
        workspace.workspace.delete(path(app_name))
    except Exception as delete_error:  # noqa: BLE001
        if (
            download(
                workspace,
                app_name=app_name,
                runtime_application_id=runtime_application_id,
            )
            is not None
        ):
            raise RuntimeError(
                "Supervisor creation absent-intent deletion did not converge"
            ) from delete_error
    if (
        download(
            workspace,
            app_name=app_name,
            runtime_application_id=runtime_application_id,
        )
        is not None
    ):
        raise RuntimeError("Supervisor creation absent intent remained after clear")


def base_create_payload(record: dict[str, Any]) -> dict[str, str]:
    contract = json.loads(record["contract_json"])
    return {
        "display_name": record["temporary_name"],
        "description": contract["description"],
        "instructions": record["temporary_instructions"],
    }
