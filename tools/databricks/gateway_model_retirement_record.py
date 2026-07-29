"""Signed immutable Workspace Files records for Gateway model archival."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from tools.databricks.app_deployment_lease_support import key_registry
from tools.databricks.gateway_model_retirement_storage import (
    archived_head_path as archived_head_path,
)
from tools.databricks.gateway_model_retirement_storage import (
    completion_path as completion_path,
)
from tools.databricks.gateway_model_retirement_storage import (
    in_progress_path as in_progress_path,
)
from tools.databricks.gateway_model_retirement_storage import (
    load_retirement_record as load_retirement_record,
)
from tools.databricks.gateway_model_retirement_storage import (
    operation_root as operation_root,
)
from tools.databricks.gateway_model_retirement_storage import (
    persist_retirement_record as persist_retirement_record,
)
from tools.databricks.gateway_model_retirement_storage import (
    stage_path as stage_path,
)

ATTESTATION_ALGORITHM = "ed25519-gateway-model-retirement-v1"
_DOMAIN = b"mip-gateway-model-retirement-v1\0"
_SIGNATURE_FIELDS = {
    "attestation_alg",
    "attestation_verify_key",
    "attestation_signature",
}
_HEX_64 = re.compile(r"[0-9a-f]{64}\Z")
_GIT_SHA = re.compile(r"[0-9a-f]{40}\Z")
_MODEL_NAME = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\Z")
_APP_NAME = re.compile(r"[a-z0-9][a-z0-9_-]*\Z")
_PRINCIPAL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@-]*\Z")


def canonical_json(value: object) -> str:
    """Return the only serialized representation accepted by the journal."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def record_sha256(value: object) -> str:
    """Digest exact canonical JSON, including the signature when present."""

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _decode(value: str, *, length: int) -> bytes:
    try:
        decoded = base64.urlsafe_b64decode(value.strip() + "=" * (-len(value.strip()) % 4))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Gateway retirement record key is invalid") from exc
    if len(decoded) != length:
        raise RuntimeError("Gateway retirement record key has an invalid length")
    return decoded


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _message(record: Mapping[str, Any]) -> bytes:
    unsigned = {key: value for key, value in record.items() if key not in _SIGNATURE_FIELDS}
    return _DOMAIN + canonical_json(unsigned).encode("utf-8")


def sign_retirement_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Sign one validated record with the server-owned proof authority."""

    unsigned = {str(key): value for key, value in record.items()}
    if _SIGNATURE_FIELDS.intersection(unsigned):
        raise RuntimeError("Gateway retirement record is already signed")
    signing_key = os.environ.get("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", "").strip()
    verify_key = os.environ.get("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", "").strip()
    private_key = Ed25519PrivateKey.from_private_bytes(_decode(signing_key, length=32))
    derived_key = _encode(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    )
    if derived_key != verify_key or verify_key not in key_registry():
        raise RuntimeError("Gateway retirement signing authority is not trusted")
    signed = {
        **unsigned,
        "attestation_alg": ATTESTATION_ALGORITHM,
        "attestation_verify_key": verify_key,
        "attestation_signature": _encode(private_key.sign(_message(unsigned))),
    }
    validate_retirement_record(signed)
    return signed


def verify_retirement_record(record: object) -> dict[str, Any]:
    """Verify schema, domain-separated signature, and proof-key trust."""

    if not isinstance(record, Mapping):
        raise RuntimeError("Gateway retirement record is not an object")
    normalized = {str(key): value for key, value in record.items()}
    validate_retirement_record(normalized)
    verify_key = str(normalized.get("attestation_verify_key") or "").strip()
    if (
        normalized.get("attestation_alg") != ATTESTATION_ALGORITHM
        or verify_key not in key_registry()
    ):
        raise RuntimeError("Gateway retirement record attestation identity is invalid")
    try:
        public_key = Ed25519PublicKey.from_public_bytes(_decode(verify_key, length=32))
        signature = _decode(
            str(normalized.get("attestation_signature") or ""),
            length=64,
        )
        public_key.verify(signature, _message(normalized))
    except (InvalidSignature, RuntimeError, TypeError, ValueError) as exc:
        raise RuntimeError("Gateway retirement record signature is invalid") from exc
    return normalized


def _text(record: Mapping[str, Any], field: str) -> str:
    value = record.get(field)
    if type(value) is not str or not value or value != value.strip():
        raise RuntimeError(f"Gateway retirement record {field} is invalid")
    return value


def _sha(record: Mapping[str, Any], field: str) -> str:
    value = _text(record, field)
    if _HEX_64.fullmatch(value) is None:
        raise RuntimeError(f"Gateway retirement record {field} is invalid")
    return value


def _string_list(record: Mapping[str, Any], field: str) -> list[str]:
    value = record.get(field)
    if not isinstance(value, list) or any(
        type(item) is not str or not item or item != item.strip() for item in value
    ):
        raise RuntimeError(f"Gateway retirement record {field} is invalid")
    if value != sorted(value) or len(value) != len(set(value)):
        raise RuntimeError(f"Gateway retirement record {field} is not canonical")
    return value


def _object_list(record: Mapping[str, Any], field: str) -> list[Mapping[str, Any]]:
    value = record.get(field)
    if not isinstance(value, list) or any(not isinstance(item, Mapping) for item in value):
        raise RuntimeError(f"Gateway retirement record {field} is invalid")
    return value


def _validate_acl_entries(entries: list[Mapping[str, Any]]) -> None:
    digests: list[str] = []
    principal_keys: set[tuple[str, str]] = set()
    for entry in entries:
        if set(entry) - {
            "all_permissions",
            "display_name",
            "group_name",
            "service_principal_name",
            "user_name",
        }:
            raise RuntimeError("Gateway retirement experiment ACL entry is invalid")
        principal_values = [
            (field, str(entry.get(field) or ""))
            for field in ("group_name", "service_principal_name", "user_name")
            if str(entry.get(field) or "")
        ]
        if any(value != value.strip() for _field, value in principal_values):
            raise RuntimeError("Gateway retirement experiment ACL principal is not canonical")
        permissions = entry.get("all_permissions")
        if len(principal_values) != 1 or not isinstance(permissions, list) or not permissions:
            raise RuntimeError("Gateway retirement experiment ACL entry is invalid")
        principal_key = principal_values[0]
        if principal_key in principal_keys:
            raise RuntimeError("Gateway retirement experiment ACL principal is duplicated")
        principal_keys.add(principal_key)
        for permission in permissions:
            if not isinstance(permission, Mapping) or set(permission) - {
                "inherited",
                "inherited_from_object",
                "permission_level",
            }:
                raise RuntimeError("Gateway retirement experiment ACL permission is invalid")
            level = permission.get("permission_level")
            inherited = permission.get("inherited")
            sources = permission.get("inherited_from_object", [])
            if (
                type(level) is not str
                or level not in {"CAN_READ", "CAN_EDIT", "CAN_MANAGE"}
                or type(inherited) is not bool
                or not isinstance(sources, list)
                or any(type(source) is not str or not source for source in sources)
            ):
                raise RuntimeError("Gateway retirement experiment ACL permission is invalid")
        digests.append(record_sha256(entry))
    if digests != sorted(digests) or len(digests) != len(set(digests)):
        raise RuntimeError("Gateway retirement experiment ACL is not canonical")


def _validate_serving_inventory(inventory: list[Mapping[str, Any]]) -> None:
    names: list[str] = []
    endpoint_ids: set[str] = set()
    for endpoint in inventory:
        if set(endpoint) != {
            "name",
            "endpoint_id",
            "creator",
            "state",
            "config_version",
            "pending_config_version",
            "ai_gateway_inference_table",
            "configurations",
        }:
            raise RuntimeError("Gateway retirement serving endpoint is invalid")
        name = _text(endpoint, "name")
        endpoint_id = _text(endpoint, "endpoint_id")
        _text(endpoint, "creator")
        if endpoint_id in endpoint_ids:
            raise RuntimeError("Gateway retirement serving endpoint ID is duplicated")
        endpoint_ids.add(endpoint_id)
        names.append(name)
        if (
            not isinstance(endpoint.get("state"), dict | None)
            or type(endpoint.get("config_version")) is not str
            or type(endpoint.get("pending_config_version")) is not str
            or not isinstance(endpoint.get("ai_gateway_inference_table"), Mapping)
        ):
            raise RuntimeError("Gateway retirement serving endpoint state is invalid")
        inference = endpoint["ai_gateway_inference_table"]
        if inference and (
            set(inference)
            != {"catalog_name", "enabled", "schema_name", "table_name_prefix"}
            or type(inference.get("enabled")) is not bool
            or any(
                type(inference.get(field)) is not str or not inference[field]
                for field in ("catalog_name", "schema_name", "table_name_prefix")
            )
        ):
            raise RuntimeError(
                "Gateway retirement serving inference-table configuration is invalid"
            )
        configurations = endpoint.get("configurations")
        if not isinstance(configurations, list):
            raise RuntimeError("Gateway retirement serving configurations are invalid")
        digests: list[str] = []
        for reference in configurations:
            if not isinstance(reference, Mapping) or set(reference) != {
                "endpoint_name",
                "endpoint_id",
                "endpoint_creator",
                "phase",
                "collection",
                "index",
                "entity_name",
                "entity_version",
                "entity_id",
                "traffic_percentage",
            }:
                raise RuntimeError("Gateway retirement serving reference is invalid")
            if (
                reference.get("endpoint_name") != name
                or reference.get("endpoint_id") != endpoint_id
                or reference.get("endpoint_creator") != endpoint["creator"]
                or reference.get("phase") not in {"current", "pending"}
                or reference.get("collection")
                not in {
                    "served_entities",
                    "served_models",
                    "traffic_routes",
                    "inference_table",
                }
                or type(reference.get("index")) is not int
                or int(reference["index"]) < 0
                or type(reference.get("entity_name")) is not str
                or not reference["entity_name"]
                or type(reference.get("entity_version")) is not str
                or type(reference.get("entity_id")) is not str
                or type(reference.get("traffic_percentage")) is not str
            ):
                raise RuntimeError("Gateway retirement serving reference is invalid")
            percentage = reference["traffic_percentage"]
            if reference["collection"] == "traffic_routes":
                if not percentage.isdigit() or not 0 <= int(percentage) <= 100:
                    raise RuntimeError("Gateway retirement traffic percentage is invalid")
            elif percentage:
                raise RuntimeError("Gateway retirement non-route traffic is invalid")
            digests.append(record_sha256(reference))
        if digests != sorted(digests) or len(digests) != len(set(digests)):
            raise RuntimeError("Gateway retirement serving configurations are not canonical")
    if names != sorted(names) or len(names) != len(set(names)):
        raise RuntimeError("Gateway retirement serving endpoint inventory is not canonical")


def _validate_tables(
    tables: list[Mapping[str, Any]],
    *,
    catalog: str,
    expected_owner: str | None = None,
) -> list[str]:
    names: list[str] = []
    for table in tables:
        if set(table) != {
            "full_name",
            "table_id",
            "owner",
            "storage_location",
            "data_source_format",
            "delta_latest_version",
        }:
            raise RuntimeError("Gateway retirement inference-table evidence is invalid")
        full_name = _text(table, "full_name")
        _text(table, "table_id")
        owner = _text(table, "owner")
        _text(table, "storage_location")
        data_format = _text(table, "data_source_format")
        delta_version = table.get("delta_latest_version")
        if type(delta_version) is not str or (
            data_format.upper() == "DELTA" and not delta_version
        ):
            raise RuntimeError("Gateway retirement table version evidence is invalid")
        if expected_owner is not None and owner != expected_owner:
            raise RuntimeError("Gateway retirement completion table owner is invalid")
        names.append(full_name)
    if (
        names != sorted(names)
        or len(names) != len(set(names))
        or any(name.split(".")[0] != catalog for name in names)
    ):
        raise RuntimeError("Gateway retirement inference-table scope is invalid")
    return names


def _validate_protected_allocations(
    protected: list[Mapping[str, Any]],
    *,
    target_model: str,
) -> None:
    keys: list[tuple[str, str]] = []
    for allocation in protected:
        if set(allocation) != {
            "kind",
            "gateway_model_name",
            "contract",
            "contract_sha256",
        }:
            raise RuntimeError("Gateway retirement protected allocation is invalid")
        kind = _text(allocation, "kind")
        model = allocation.get("gateway_model_name")
        contract = allocation.get("contract")
        if type(model) is not str or not isinstance(contract, Mapping):
            raise RuntimeError("Gateway retirement protected allocation is invalid")
        contract_digest = _sha(allocation, "contract_sha256")
        if contract_digest != record_sha256(contract):
            raise RuntimeError("Gateway retirement protected contract digest is invalid")
        keys.append((kind, contract_digest))
    if keys != sorted(keys) or len(keys) != len(set(keys)):
        raise RuntimeError("Gateway retirement protected allocations are not canonical")
    if any(item.get("gateway_model_name") == target_model for item in protected):
        raise RuntimeError("Gateway retirement target is protected by rollback state")


def _validate_effective_access(
    evidence: list[Mapping[str, Any]],
    *,
    model_name: str,
    table_names: list[str],
    identities: Mapping[str, str],
) -> None:
    if [item.get("role") for item in evidence] != [
        "runtime",
        "app",
        "proxy",
        "verifier",
    ]:
        raise RuntimeError("Gateway retirement access evidence roles are invalid")
    application_ids: set[str] = set()
    for item in evidence:
        if set(item) != {
            "role",
            "application_id",
            "groups_sha256",
            "abac_policies_sha256",
            "resources",
            "experiment_permissions",
        }:
            raise RuntimeError("Gateway retirement access evidence is invalid")
        application_id = _text(item, "application_id")
        if application_id != identities[str(item["role"])]:
            raise RuntimeError("Gateway retirement access identity escaped record scope")
        if application_id in application_ids:
            raise RuntimeError("Gateway retirement access identities are duplicated")
        application_ids.add(application_id)
        _sha(item, "groups_sha256")
        _sha(item, "abac_policies_sha256")
        if item.get("experiment_permissions") != []:
            raise RuntimeError("Gateway retirement experiment access is not empty")
        resources = item.get("resources")
        if not isinstance(resources, list) or not resources:
            raise RuntimeError("Gateway retirement UC access evidence is invalid")
        for resource in resources:
            if (
                not isinstance(resource, Mapping)
                or set(resource) != {"securable_type", "full_name", "privileges"}
                or resource.get("securable_type") not in {"function", "table"}
                or not isinstance(resource.get("privileges"), Mapping)
                or resource["privileges"]
            ):
                raise RuntimeError("Gateway retirement UC access is not empty")
        resource_keys = [
            (str(resource["securable_type"]), str(resource["full_name"]))
            for resource in resources
        ]
        expected_resources = [
            ("function", model_name),
            *(("table", table_name) for table_name in sorted(table_names)),
        ]
        if resource_keys != expected_resources:
            raise RuntimeError("Gateway retirement UC access evidence is incomplete")


def _validate_expected_tables(
    record: Mapping[str, Any],
    *,
    present: list[str],
    absent: list[str],
) -> None:
    suffix = str(record["model_name"]).rsplit("_", 1)[-1]
    base = (
        f"{record['catalog']}.{record['inference_schema']}."
        f"{record['inference_table_prefix']}_{suffix}_payload"
    )
    expected = {
        base,
        f"{base}_request_logs",
        f"{base}_assessment_logs",
    }
    if set(present).union(absent) != expected:
        raise RuntimeError("Gateway retirement table-family evidence is incomplete")


def _validate_completion_acl(
    entries: list[Mapping[str, Any]],
    *,
    governance_group: str,
    archive_owner: str,
) -> None:
    governance_direct = 0
    for entry in entries:
        group = str(entry.get("group_name") or "").strip()
        named = {
            str(entry.get("service_principal_name") or "").strip(),
            str(entry.get("user_name") or "").strip(),
        } - {""}
        if group not in {"", "admins", governance_group} or named - {archive_owner}:
            raise RuntimeError("Gateway retirement completion ACL has an unexpected principal")
        permissions = entry.get("all_permissions")
        assert isinstance(permissions, list)
        if any(permission.get("permission_level") != "CAN_MANAGE" for permission in permissions):
            raise RuntimeError("Gateway retirement completion ACL is not governance-only")
        if group == governance_group and any(
            permission.get("inherited") is False for permission in permissions
        ):
            governance_direct += 1
    if governance_direct != 1:
        raise RuntimeError("Gateway retirement completion lacks direct governance CAN_MANAGE")


def _validate_scope(record: Mapping[str, Any]) -> None:
    if _APP_NAME.fullmatch(_text(record, "app_name")) is None:
        raise RuntimeError("Gateway retirement record App name is invalid")
    try:
        UUID(_text(record, "lease_id"))
    except ValueError as exc:
        raise RuntimeError("Gateway retirement record lease ID is invalid") from exc
    if _GIT_SHA.fullmatch(_text(record, "source_git_sha")) is None:
        raise RuntimeError("Gateway retirement record source Git SHA is invalid")
    if _MODEL_NAME.fullmatch(_text(record, "model_name")) is None:
        raise RuntimeError("Gateway retirement record model identity is invalid")
    if record.get("disposition") != "archive":
        raise RuntimeError("Gateway retirement record disposition is invalid")
    workspace_id = record.get("workspace_id")
    if type(workspace_id) not in {str, int} or not str(workspace_id).strip():
        raise RuntimeError("Gateway retirement record workspace ID is invalid")
    for field in (
        "workspace_host",
        "runtime_application_id",
        "app_application_id",
        "proxy_application_id",
        "verifier_application_id",
        "archive_owner",
        "governance_group",
        "metastore_id",
        "catalog",
        "model_family",
        "experiment_base",
        "inference_schema",
        "inference_table_prefix",
    ):
        _text(record, field)
    if _PRINCIPAL.fullmatch(str(record["archive_owner"])) is None:
        raise RuntimeError("Gateway retirement archive owner is invalid")
    expected_archive_name = (
        f"/Users/{record['archive_owner']}/.mip-gateway-archive/"
        f"{record['app_name']}/{record_sha256(record['model_name'])[:24]}"
    )
    if (
        record["experiment_archive_name"] != expected_archive_name
        or record["experiment_original_name"] == expected_archive_name
    ):
        raise RuntimeError("Gateway retirement archive experiment name is invalid")


def _validate_stage(record: Mapping[str, Any]) -> None:
    required = {
        "version",
        "kind",
        "phase",
        "disposition",
        "app_name",
        "lease_id",
        "source_git_sha",
        "workspace_host",
        "workspace_id",
        "metastore_id",
        "runtime_application_id",
        "app_application_id",
        "proxy_application_id",
        "verifier_application_id",
        "archive_owner",
        "governance_group",
        "catalog",
        "model_family",
        "experiment_base",
        "inference_schema",
        "inference_table_prefix",
        "model_name",
        "model_owner",
        "versions",
        "versions_sha256",
        "model_sources",
        "logged_model_ids",
        "source_run_ids",
        "experiment_id",
        "experiment_original_name",
        "experiment_archive_name",
        "experiment_artifact_location",
        "experiment_lifecycle_state",
        "experiment_owner",
        "experiment_tags",
        "experiment_tags_sha256",
        "experiment_acl",
        "experiment_acl_sha256",
        "inference_tables",
        "expected_absent_inference_tables",
        "serving_inventory",
        "serving_inventory_sha256",
        "serving_references",
        "serving_references_sha256",
        "protected_allocation_contracts",
        "protected_allocation_contracts_sha256",
        "created_at",
        *_SIGNATURE_FIELDS,
    }
    if set(record) != required:
        raise RuntimeError("Gateway retirement stage record has an invalid schema")
    _validate_scope(record)
    if record.get("version") != 1 or record.get("kind") != "gateway-model-retirement":
        raise RuntimeError("Gateway retirement stage record version is invalid")
    if record.get("phase") != "staged":
        raise RuntimeError("Gateway retirement stage record phase is invalid")
    if _text(record, "model_owner") != _text(record, "runtime_application_id"):
        raise RuntimeError("Gateway retirement stage model is not runtime-owned")
    versions = record.get("versions")
    if not isinstance(versions, list) or not versions:
        raise RuntimeError("Gateway retirement stage has no model versions")
    numbers: list[int] = []
    for version in versions:
        if not isinstance(version, Mapping) or set(version) != {
            "version",
            "status",
            "source",
            "source_sha256",
            "run_id",
            "logged_model_id",
            "attestation_epoch",
            "tags",
            "tags_sha256",
        }:
            raise RuntimeError("Gateway retirement model-version evidence is invalid")
        try:
            number = int(_text(version, "version"))
        except ValueError as exc:
            raise RuntimeError("Gateway retirement model version is invalid") from exc
        if number < 1 or version.get("status") != "READY":
            raise RuntimeError("Gateway retirement model version is not READY")
        _text(version, "source")
        if _sha(version, "source_sha256") != record_sha256(version["source"]):
            raise RuntimeError("Gateway retirement model-version source digest is invalid")
        _text(version, "run_id")
        _text(version, "logged_model_id")
        if version.get("attestation_epoch") not in {"current", "previous"}:
            raise RuntimeError("Gateway retirement model attestation epoch is invalid")
        tags = version.get("tags")
        if not isinstance(tags, Mapping) or any(
            type(key) is not str or type(value) is not str
            for key, value in tags.items()
        ):
            raise RuntimeError("Gateway retirement model-version tags are invalid")
        if _sha(version, "tags_sha256") != record_sha256(tags):
            raise RuntimeError("Gateway retirement model-version tag digest is invalid")
        numbers.append(number)
    if numbers != sorted(numbers) or len(numbers) != len(set(numbers)):
        raise RuntimeError("Gateway retirement model versions are not canonical")
    if _sha(record, "versions_sha256") != record_sha256(versions):
        raise RuntimeError("Gateway retirement model-version digest is invalid")
    for field in (
        "experiment_id",
        "experiment_original_name",
        "experiment_archive_name",
        "experiment_artifact_location",
        "experiment_lifecycle_state",
        "experiment_owner",
        "created_at",
    ):
        _text(record, field)
    model_sources = _string_list(record, "model_sources")
    logged_models = _string_list(record, "logged_model_ids")
    source_runs = _string_list(record, "source_run_ids")
    if (
        model_sources != sorted({str(item["source"]) for item in versions})
        or logged_models != sorted({str(item["logged_model_id"]) for item in versions})
        or source_runs != sorted({str(item["run_id"]) for item in versions})
    ):
        raise RuntimeError("Gateway retirement source identity is not exhaustive")
    tags = record.get("experiment_tags")
    if not isinstance(tags, Mapping) or any(
        type(key) is not str or type(value) is not str for key, value in tags.items()
    ):
        raise RuntimeError("Gateway retirement experiment tags are invalid")
    if _sha(record, "experiment_tags_sha256") != record_sha256(tags):
        raise RuntimeError("Gateway retirement experiment tag digest is invalid")
    acl = _object_list(record, "experiment_acl")
    _validate_acl_entries(acl)
    if _sha(record, "experiment_acl_sha256") != record_sha256(acl):
        raise RuntimeError("Gateway retirement experiment ACL digest is invalid")
    tables = _object_list(record, "inference_tables")
    table_names = _validate_tables(tables, catalog=str(record["catalog"]))
    absent_tables = _string_list(record, "expected_absent_inference_tables")
    if set(table_names).intersection(absent_tables):
        raise RuntimeError("Gateway retirement table is both present and expected absent")
    _validate_expected_tables(record, present=table_names, absent=absent_tables)
    inventory = _object_list(record, "serving_inventory")
    _validate_serving_inventory(inventory)
    if _sha(record, "serving_inventory_sha256") != record_sha256(inventory):
        raise RuntimeError("Gateway retirement serving-inventory digest is invalid")
    references = record.get("serving_references")
    if not isinstance(references, list) or references:
        raise RuntimeError("Gateway retirement requires zero serving references")
    if _sha(record, "serving_references_sha256") != record_sha256(references):
        raise RuntimeError("Gateway retirement serving-reference digest is invalid")
    protected = _object_list(record, "protected_allocation_contracts")
    _validate_protected_allocations(
        protected,
        target_model=str(record["model_name"]),
    )
    if _sha(record, "protected_allocation_contracts_sha256") != record_sha256(protected):
        raise RuntimeError("Gateway retirement protected-allocation digest is invalid")


def _validate_completion(record: Mapping[str, Any]) -> None:
    required = {
        "version",
        "kind",
        "phase",
        "disposition",
        "app_name",
        "lease_id",
        "source_git_sha",
        "workspace_host",
        "workspace_id",
        "metastore_id",
        "runtime_application_id",
        "app_application_id",
        "proxy_application_id",
        "verifier_application_id",
        "archive_owner",
        "governance_group",
        "catalog",
        "model_family",
        "experiment_base",
        "inference_schema",
        "inference_table_prefix",
        "model_name",
        "stage_record_sha256",
        "versions_sha256",
        "inference_tables",
        "expected_absent_inference_tables",
        "model_owner",
        "experiment_id",
        "experiment_original_name",
        "experiment_archive_name",
        "experiment_artifact_location",
        "experiment_lifecycle_state",
        "experiment_owner",
        "experiment_tags",
        "experiment_tags_sha256",
        "experiment_acl",
        "experiment_acl_sha256",
        "serving_inventory",
        "serving_inventory_sha256",
        "serving_references",
        "serving_references_sha256",
        "protected_allocation_contracts",
        "protected_allocation_contracts_sha256",
        "effective_access",
        "effective_access_sha256",
        "completed_at",
        *_SIGNATURE_FIELDS,
    }
    if set(record) != required:
        raise RuntimeError("Gateway retirement completion record has an invalid schema")
    _validate_scope(record)
    if (
        record.get("version") != 1
        or record.get("kind") != "gateway-model-retirement"
        or record.get("phase") != "completed"
    ):
        raise RuntimeError("Gateway retirement completion record version is invalid")
    for field in (
        "stage_record_sha256",
        "versions_sha256",
        "experiment_acl_sha256",
        "experiment_tags_sha256",
        "serving_inventory_sha256",
        "serving_references_sha256",
        "protected_allocation_contracts_sha256",
        "effective_access_sha256",
    ):
        _sha(record, field)
    tables = _object_list(record, "inference_tables")
    table_names = _validate_tables(
        tables,
        catalog=str(record["catalog"]),
        expected_owner=str(record["archive_owner"]),
    )
    absent_tables = _string_list(record, "expected_absent_inference_tables")
    if set(table_names).intersection(absent_tables):
        raise RuntimeError("Gateway retirement table is both present and expected absent")
    _validate_expected_tables(record, present=table_names, absent=absent_tables)
    if _text(record, "model_owner") != _text(record, "archive_owner"):
        raise RuntimeError("Gateway retirement completion owner is invalid")
    acl = _object_list(record, "experiment_acl")
    _validate_acl_entries(acl)
    _validate_completion_acl(
        acl,
        governance_group=str(record["governance_group"]),
        archive_owner=str(record["archive_owner"]),
    )
    if _sha(record, "experiment_acl_sha256") != record_sha256(acl):
        raise RuntimeError("Gateway retirement completion ACL digest is invalid")
    inventory = _object_list(record, "serving_inventory")
    _validate_serving_inventory(inventory)
    if record["serving_inventory_sha256"] != record_sha256(inventory):
        raise RuntimeError("Gateway retirement completion serving inventory is invalid")
    references = record.get("serving_references")
    if references != [] or record["serving_references_sha256"] != record_sha256(references):
        raise RuntimeError("Gateway retirement completion serving references are not empty")
    protected = _object_list(record, "protected_allocation_contracts")
    _validate_protected_allocations(
        protected,
        target_model=str(record["model_name"]),
    )
    if record["protected_allocation_contracts_sha256"] != record_sha256(protected):
        raise RuntimeError("Gateway retirement completion protected allocations are invalid")
    effective_access = _object_list(record, "effective_access")
    _validate_effective_access(
        effective_access,
        model_name=str(record["model_name"]),
        table_names=table_names,
        identities={
            "runtime": str(record["runtime_application_id"]),
            "app": str(record["app_application_id"]),
            "proxy": str(record["proxy_application_id"]),
            "verifier": str(record["verifier_application_id"]),
        },
    )
    if record["effective_access_sha256"] != record_sha256(effective_access):
        raise RuntimeError("Gateway retirement completion access evidence is invalid")
    for field in (
        "experiment_id",
        "experiment_original_name",
        "experiment_archive_name",
        "experiment_artifact_location",
        "experiment_lifecycle_state",
        "experiment_owner",
        "completed_at",
    ):
        _text(record, field)
    tags = record.get("experiment_tags")
    if not isinstance(tags, Mapping) or _sha(
        record,
        "experiment_tags_sha256",
    ) != record_sha256(tags):
        raise RuntimeError("Gateway retirement completion experiment tags are invalid")


def validate_retirement_record(record: Mapping[str, Any]) -> None:
    """Reject noncanonical or partially bound retirement evidence."""

    phase = str(record.get("phase") or "")
    if phase == "staged":
        _validate_stage(record)
    elif phase == "completed":
        _validate_completion(record)
    else:
        raise RuntimeError("Gateway retirement record phase is invalid")
