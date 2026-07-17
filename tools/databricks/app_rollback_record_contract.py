"""Validate and persist signed Databricks App rollback records."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from backend.agents.gateway_contract import (
    GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION,
    gateway_exact_resource_digest,
)
from databricks.sdk.errors import NotFound, ResourceDoesNotExist
from tools.databricks.app_rollback_resource_contract import (
    app_resource_contract_digest,
    validated_app_resource_contract,
)

RECORD_VERSION = 4
DEFAULT_KEY_PREFIX = "app-last-good-v4"
RECORD_ATTESTATION_ALG = "ed25519-app-rollback-v4"


def _text(value: object) -> str:
    return str(getattr(value, "value", value) or "").strip()


def _record_key(app_name: str) -> str:
    normalized = app_name.strip()
    if not normalized or any(
        char not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for char in normalized
    ):
        raise ValueError("app name is invalid for the rollback-contract key")
    return f"{DEFAULT_KEY_PREFIX}-{normalized}"


def _decode_key(value: str, *, expected_len: int) -> bytes:
    try:
        decoded = base64.urlsafe_b64decode(value.strip() + "=" * (-len(value.strip()) % 4))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("App rollback attestation key is invalid") from exc
    if len(decoded) != expected_len:
        raise RuntimeError("App rollback attestation key has an invalid length")
    return decoded


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _attestation_payload(record: dict[str, Any]) -> bytes:
    unsigned = {
        key: value
        for key, value in record.items()
        if key not in {"attestation_alg", "attestation_verify_key", "attestation_signature"}
    }
    canonical = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return b"mip-app-rollback\0" + canonical


def _sign_record(record: dict[str, Any]) -> dict[str, Any]:
    signing_key = os.environ.get("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", "").strip()
    verify_key = os.environ.get("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", "").strip()
    private = Ed25519PrivateKey.from_private_bytes(_decode_key(signing_key, expected_len=32))
    derived_verify = _encode(
        private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    )
    if derived_verify != verify_key:
        raise RuntimeError("App rollback signing and verification keys do not match")
    signed = copy.deepcopy(record)
    signed.update(
        attestation_alg=RECORD_ATTESTATION_ALG,
        attestation_verify_key=verify_key,
        attestation_signature=_encode(private.sign(_attestation_payload(record))),
    )
    return signed


def _verify_record_attestation(record: dict[str, Any]) -> None:
    configured = os.environ.get("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", "").strip()
    previous = os.environ.get("MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY", "").strip()
    record_key = str(record.get("attestation_verify_key") or "").strip()
    if record.get("attestation_alg") != RECORD_ATTESTATION_ALG or record_key not in {
        configured,
        previous,
    } - {""}:
        raise RuntimeError("App rollback contract attestation identity is invalid")
    try:
        public = Ed25519PublicKey.from_public_bytes(_decode_key(record_key, expected_len=32))
        signature = _decode_key(str(record.get("attestation_signature") or ""), expected_len=64)
        public.verify(signature, _attestation_payload(record))
    except (InvalidSignature, RuntimeError, ValueError) as exc:
        raise RuntimeError("App rollback contract signature is invalid") from exc


def _secret_value(workspace: Any, *, scope: str, key: str) -> str | None:
    try:
        encoded = _text(workspace.secrets.get_secret(scope, key).value)
    except (NotFound, ResourceDoesNotExist):
        return None
    if not encoded:
        raise RuntimeError("App rollback contract secret is empty")
    try:
        return base64.b64decode(encoded, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError("App rollback contract secret is invalid") from exc


def _payload_digest(payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _validated_payload(
    value: object,
    *,
    require_immutable_source: bool = True,
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise RuntimeError("App rollback payload is not an object")
    payload = copy.deepcopy(value)
    source = str(payload.get("source_code_path") or "").strip()
    if not source.startswith("/Workspace/Users/"):
        raise RuntimeError("App rollback source is outside a workspace user home")
    if require_immutable_source and "/src/" not in source:
        raise RuntimeError("App rollback source is not an immutable deployment artifact")
    if payload.get("mode") != "SNAPSHOT":
        raise RuntimeError("App rollback payload must use SNAPSHOT mode")
    env_vars = payload.get("env_vars")
    if not isinstance(env_vars, list) or not env_vars:
        raise RuntimeError("App rollback payload has no complete environment")
    names: set[str] = set()
    for item in env_vars:
        if not isinstance(item, dict):
            raise RuntimeError("App rollback environment entry is invalid")
        name = str(item.get("name") or "").strip()
        value_keys = {key for key in ("value", "value_from") if str(item.get(key) or "").strip()}
        if not name or name in names or len(value_keys) != 1:
            raise RuntimeError("App rollback environment is incomplete or ambiguous")
        names.add(name)
    allowed = {"source_code_path", "mode", "env_vars", "command"}
    if set(payload) - allowed:
        raise RuntimeError("App rollback payload contains unsupported fields")
    return payload


def _validated_gateway_resources(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or any(
        not isinstance(key, str) or not isinstance(item, str) for key, item in value.items()
    ):
        raise RuntimeError("App rollback Gateway resource contract is invalid")
    resources = dict(value)
    digest = resources.pop("resource_digest", "")
    try:
        actual_digest = gateway_exact_resource_digest(resources)
    except ValueError as exc:
        raise RuntimeError("App rollback Gateway resource contract is invalid") from exc
    if (
        resources.get("proof_version") != GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION
        or not digest
        or digest != actual_digest
    ):
        raise RuntimeError("App rollback Gateway resource contract digest is invalid")
    return {**resources, "resource_digest": digest}


def _validated_record(value: object, *, app_name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("version") != RECORD_VERSION:
        raise RuntimeError("App rollback contract version is invalid")
    if value.get("app_name") != app_name:
        raise RuntimeError("App rollback contract names a different App")
    payload = _validated_payload(value.get("payload"))
    if value.get("payload_sha256") != _payload_digest(payload):
        raise RuntimeError("App rollback payload digest does not match")
    deployment_id = str(value.get("deployment_id") or "").strip()
    git_sha = str(value.get("git_sha") or "").strip()
    app_client_id = str(value.get("app_service_principal_client_id") or "").strip()
    app_scim_id = str(value.get("app_service_principal_scim_id") or "").strip()
    binding = value.get("gateway_binding_sha256")
    gateway_resources = _validated_gateway_resources(value.get("gateway_resources"))
    app_resources = validated_app_resource_contract(value.get("app_resources"))
    if value.get("app_resources_sha256") != app_resource_contract_digest(app_resources):
        raise RuntimeError("App rollback resource binding digest does not match")
    if not deployment_id or len(git_sha) != 40 or not app_client_id or not app_scim_id:
        raise RuntimeError("App rollback contract lacks immutable deployment identity")
    if binding is not None and (not isinstance(binding, str) or len(binding) != 64):
        raise RuntimeError("App rollback Gateway binding is invalid")
    return {
        **value,
        "payload": payload,
        "deployment_id": deployment_id,
        "git_sha": git_sha,
        "app_service_principal_client_id": app_client_id,
        "app_service_principal_scim_id": app_scim_id,
        "gateway_binding_sha256": binding,
        "gateway_resources": gateway_resources,
        "app_resources": app_resources,
        "app_resources_sha256": app_resource_contract_digest(app_resources),
    }


def _load_record(workspace: Any, *, app_name: str, scope: str) -> dict[str, Any]:
    raw = _secret_value(workspace, scope=scope, key=_record_key(app_name))
    if raw is None:
        raise RuntimeError(
            "no server-owned last-good App rollback contract exists; run the explicit "
            "bootstrap command before upgrading this existing App"
        )
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("App rollback contract is not valid JSON") from exc
    if not isinstance(decoded, dict):
        raise RuntimeError("App rollback contract is not an object")
    _verify_record_attestation(decoded)
    return _validated_record(decoded, app_name=app_name)


def _save_record(workspace: Any, *, scope: str, record: dict[str, Any]) -> None:
    signed = _sign_record(record)
    serialized = json.dumps(signed, sort_keys=True, separators=(",", ":"))
    workspace.secrets.put_secret(
        scope=scope,
        key=_record_key(str(record["app_name"])),
        string_value=serialized,
    )
    persisted = _secret_value(
        workspace,
        scope=scope,
        key=_record_key(str(record["app_name"])),
    )
    if persisted != serialized:
        raise RuntimeError("App rollback contract write did not converge exactly")
