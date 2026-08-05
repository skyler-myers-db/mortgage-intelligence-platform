"""Tests for signed immutable Gateway model retirement records."""

from __future__ import annotations

import base64
import io
from types import SimpleNamespace
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from databricks.sdk.errors import ResourceAlreadyExists, ResourceDoesNotExist
from databricks.sdk.service.workspace import ImportFormat

from tools.databricks.gateway_model_archival_inventory import archive_experiment_name
from tools.databricks.gateway_model_retirement_record import (
    archived_head_path,
    canonical_json,
    completion_path,
    in_progress_path,
    load_retirement_record,
    operation_root,
    persist_retirement_record,
    record_sha256,
    sign_retirement_record,
    stage_path,
    verify_retirement_record,
)
from tools.databricks.gateway_resource_identity import gateway_experiment_name

_LEASE_ID = "11111111-1111-4111-8111-111111111111"
_MODEL_NAME = "mip.audit.mortgage_growth_supervisor_proxy_aaaaaaaaaaaa"
_RUNTIME_ID = "runtime-application-id"
_APP_ID = "app-application-id"
_PROXY_ID = "proxy-application-id"
_VERIFIER_ID = "verifier-application-id"
_ARCHIVE_OWNER = "governance@example.com"
_SIGNING_BYTES = b"r" * 32


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _verify_key(signing_bytes: bytes) -> str:
    private_key = Ed25519PrivateKey.from_private_bytes(signing_bytes)
    return _encode(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    )


@pytest.fixture(autouse=True)
def _proof_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", _encode(_SIGNING_BYTES))
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", _verify_key(_SIGNING_BYTES))
    monkeypatch.delenv("MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY", raising=False)
    monkeypatch.delenv("MIP_AI_GATEWAY_PROOF_HISTORICAL_VERIFY_KEYS", raising=False)


def _scope() -> dict[str, Any]:
    return {
        "version": 1,
        "kind": "gateway-model-retirement",
        "disposition": "archive",
        "app_name": "mip-app",
        "lease_id": _LEASE_ID,
        "source_git_sha": "a" * 40,
        "workspace_host": "https://workspace.cloud.databricks.com",
        "workspace_id": "123456789",
        "metastore_id": "metastore-id",
        "runtime_application_id": _RUNTIME_ID,
        "app_application_id": _APP_ID,
        "proxy_application_id": _PROXY_ID,
        "verifier_application_id": _VERIFIER_ID,
        "archive_owner": _ARCHIVE_OWNER,
        "governance_group": "account admins",
        "catalog": "mip",
        "model_family": "mip.audit.mortgage_growth_supervisor_proxy",
        "experiment_base": "mip-agent-runtime-gateway-proxy",
        "inference_schema": "audit",
        "inference_table_prefix": "mip_agent_gateway_growth_agent",
        "model_name": _MODEL_NAME,
    }


def _acl(principal: str) -> list[dict[str, Any]]:
    return [
        {
            "user_name": principal,
            "all_permissions": [
                {
                    "permission_level": "CAN_MANAGE",
                    "inherited": False,
                    "inherited_from_object": [],
                }
            ],
        }
    ]


def _governance_acl() -> list[dict[str, Any]]:
    return [
        {
            "group_name": "account admins",
            "all_permissions": [
                {
                    "permission_level": "CAN_MANAGE",
                    "inherited": False,
                    "inherited_from_object": [],
                }
            ],
        }
    ]


def _table(owner: str) -> dict[str, str]:
    return {
        "full_name": "mip.audit.mip_agent_gateway_growth_agent_aaaaaaaaaaaa_payload",
        "table_id": "table-id",
        "owner": owner,
        "storage_location": "s3://bucket/table",
        "data_source_format": "DELTA",
        "delta_latest_version": "7",
    }


def _stage_unsigned() -> dict[str, Any]:
    source = "models:/m-reviewed-gateway"
    version_tags = {"mip.gateway.contract.version": "3"}
    versions = [
        {
            "version": "1",
            "status": "READY",
            "attestation_epoch": "current",
            "source": source,
            "source_sha256": record_sha256(source),
            "run_id": "source-run-id",
            "logged_model_id": "m-reviewed-gateway",
            "tags": version_tags,
            "tags_sha256": record_sha256(version_tags),
        }
    ]
    experiment_tags = {"mlflow.ownerEmail": _RUNTIME_ID}
    experiment_acl = _acl(_RUNTIME_ID)
    serving_inventory: list[dict[str, Any]] = []
    serving_references: list[dict[str, Any]] = []
    protected: list[dict[str, Any]] = []
    return {
        **_scope(),
        "phase": "staged",
        "model_owner": _RUNTIME_ID,
        "versions": versions,
        "versions_sha256": record_sha256(versions),
        "model_sources": [source],
        "logged_model_ids": ["m-reviewed-gateway"],
        "source_run_ids": ["source-run-id"],
        "experiment_id": "experiment-id",
        "experiment_original_name": gateway_experiment_name(
            base_experiment_name="mip-agent-runtime-gateway-proxy",
            contract_hash="aaaaaaaaaaaa",
            runtime_application_id=_RUNTIME_ID,
        ),
        "experiment_archive_name": archive_experiment_name(
            archive_owner=_ARCHIVE_OWNER,
            app_name="mip-app",
            model_name=_MODEL_NAME,
        ),
        "experiment_artifact_location": "dbfs:/experiments/experiment-id",
        "experiment_lifecycle_state": "active",
        "experiment_owner": _RUNTIME_ID,
        "experiment_tags": experiment_tags,
        "experiment_tags_sha256": record_sha256(experiment_tags),
        "experiment_acl": experiment_acl,
        "experiment_acl_sha256": record_sha256(experiment_acl),
        "inference_tables": [_table(_RUNTIME_ID)],
        "expected_absent_inference_tables": [
            "mip.audit.mip_agent_gateway_growth_agent_aaaaaaaaaaaa_payload_assessment_logs",
            "mip.audit.mip_agent_gateway_growth_agent_aaaaaaaaaaaa_payload_request_logs",
        ],
        "serving_inventory": serving_inventory,
        "serving_inventory_sha256": record_sha256(serving_inventory),
        "serving_references": serving_references,
        "serving_references_sha256": record_sha256(serving_references),
        "protected_allocation_contracts": protected,
        "protected_allocation_contracts_sha256": record_sha256(protected),
        "created_at": "2026-07-28T20:00:00+00:00",
    }


def _effective_access() -> list[dict[str, Any]]:
    identities = (
        ("runtime", _RUNTIME_ID),
        ("app", _APP_ID),
        ("proxy", _PROXY_ID),
        ("verifier", _VERIFIER_ID),
    )
    return [
        {
            "role": role,
            "application_id": application_id,
            "groups_sha256": record_sha256([]),
            "abac_policies_sha256": record_sha256([]),
            "resources": [
                {
                    "securable_type": "function",
                    "full_name": _MODEL_NAME,
                    "privileges": {},
                },
                {
                    "securable_type": "table",
                    "full_name": _table(_ARCHIVE_OWNER)["full_name"],
                    "privileges": {},
                },
            ],
            "experiment_permissions": [],
        }
        for role, application_id in identities
    ]


def _completion_unsigned(stage: dict[str, Any]) -> dict[str, Any]:
    experiment_tags = {"mlflow.ownerEmail": _ARCHIVE_OWNER}
    experiment_acl = _governance_acl()
    serving_inventory: list[dict[str, Any]] = []
    protected: list[dict[str, Any]] = []
    effective_access = _effective_access()
    return {
        **_scope(),
        "phase": "completed",
        "stage_record_sha256": record_sha256(stage),
        "versions_sha256": stage["versions_sha256"],
        "inference_tables": [_table(_ARCHIVE_OWNER)],
        "expected_absent_inference_tables": stage[
            "expected_absent_inference_tables"
        ],
        "model_owner": _ARCHIVE_OWNER,
        "experiment_id": stage["experiment_id"],
        "experiment_original_name": stage["experiment_original_name"],
        "experiment_archive_name": stage["experiment_archive_name"],
        "experiment_artifact_location": stage["experiment_artifact_location"],
        "experiment_lifecycle_state": stage["experiment_lifecycle_state"],
        "experiment_owner": _ARCHIVE_OWNER,
        "experiment_tags": experiment_tags,
        "experiment_tags_sha256": record_sha256(experiment_tags),
        "experiment_acl": experiment_acl,
        "experiment_acl_sha256": record_sha256(experiment_acl),
        "serving_inventory": serving_inventory,
        "serving_inventory_sha256": record_sha256(serving_inventory),
        "serving_references": [],
        "serving_references_sha256": record_sha256([]),
        "protected_allocation_contracts": protected,
        "protected_allocation_contracts_sha256": record_sha256(protected),
        "effective_access": effective_access,
        "effective_access_sha256": record_sha256(effective_access),
        "completed_at": "2026-07-28T20:01:00+00:00",
    }


class _WorkspaceFiles:
    def __init__(self) -> None:
        self.records: dict[str, bytes] = {}
        self.mkdirs_calls: list[str] = []
        self.upload_calls: list[tuple[str, ImportFormat, bool]] = []
        self.upload_mode = "success"
        self.download_error: Exception | None = None

    def mkdirs(self, path: str) -> None:
        self.mkdirs_calls.append(path)

    def upload(
        self,
        path: str,
        content: io.BytesIO,
        *,
        format: ImportFormat,
        overwrite: bool,
    ) -> None:
        self.upload_calls.append((path, format, overwrite))
        if path in self.records:
            raise ResourceAlreadyExists("immutable record exists")
        payload = content.read()
        if self.upload_mode == "commit_then_raise":
            self.records[path] = payload
            raise OSError("response lost")
        if self.upload_mode == "no_commit":
            raise OSError("upload failed")
        if self.upload_mode == "corrupt_commit":
            self.records[path] = b"{}"
            raise OSError("response lost after foreign commit")
        self.records[path] = payload

    def download(self, path: str) -> io.BytesIO:
        if self.download_error is not None:
            raise self.download_error
        if path not in self.records:
            raise ResourceDoesNotExist("record missing")
        return io.BytesIO(self.records[path])


def _workspace(files: _WorkspaceFiles | None = None) -> Any:
    return SimpleNamespace(workspace=files or _WorkspaceFiles())


def test_stage_and_completion_records_round_trip_under_server_key() -> None:
    stage = sign_retirement_record(_stage_unsigned())
    completion = sign_retirement_record(_completion_unsigned(stage))

    assert verify_retirement_record(stage) == stage
    assert verify_retirement_record(completion) == completion
    assert stage["attestation_verify_key"] == _verify_key(_SIGNING_BYTES)
    assert completion["stage_record_sha256"] == record_sha256(stage)


def test_record_paths_are_lease_scoped_and_model_name_is_not_exposed() -> None:
    root = operation_root("mip-app", _MODEL_NAME, _LEASE_ID)

    assert _MODEL_NAME not in root
    assert root.endswith(f"/{_LEASE_ID}")
    assert stage_path("mip-app", _MODEL_NAME, _LEASE_ID) == f"{root}/stage.json"
    assert completion_path("mip-app", _MODEL_NAME, _LEASE_ID) == f"{root}/complete.json"
    assert archived_head_path("mip-app", _MODEL_NAME).endswith("/archived.json")
    assert in_progress_path("mip-app", _MODEL_NAME).endswith("/in-progress.json")
    assert _LEASE_ID not in in_progress_path("mip-app", _MODEL_NAME)


def test_record_rejects_extra_schema_and_signed_payload_tampering() -> None:
    unsigned = _stage_unsigned()
    unsigned["operator_action"] = "delete"
    with pytest.raises(RuntimeError, match="invalid schema"):
        sign_retirement_record(unsigned)

    signed = sign_retirement_record(_stage_unsigned())
    signed["created_at"] = "2026-07-28T20:00:02+00:00"
    with pytest.raises(RuntimeError, match="signature is invalid"):
        verify_retirement_record(signed)


def test_stage_rejects_serving_reference_and_protected_target() -> None:
    referenced = _stage_unsigned()
    referenced["serving_references"] = [{"entity_name": _MODEL_NAME}]
    referenced["serving_references_sha256"] = record_sha256(
        referenced["serving_references"]
    )
    with pytest.raises(RuntimeError, match="requires zero serving references"):
        sign_retirement_record(referenced)

    protected = _stage_unsigned()
    contract = {"gateway_model_name": _MODEL_NAME}
    protected["protected_allocation_contracts"] = [
        {
            "kind": "signed-blue",
            "gateway_model_name": _MODEL_NAME,
            "contract": contract,
            "contract_sha256": record_sha256(contract),
        }
    ]
    protected["protected_allocation_contracts_sha256"] = record_sha256(
        protected["protected_allocation_contracts"]
    )
    with pytest.raises(RuntimeError, match="protected by rollback state"):
        sign_retirement_record(protected)


def test_record_requires_deterministic_private_archive_experiment_home() -> None:
    shared = _stage_unsigned()
    shared["experiment_archive_name"] = "/Shared/gateway-model-archive"

    with pytest.raises(RuntimeError, match="archive experiment name is invalid"):
        sign_retirement_record(shared)


def test_record_rejects_semantic_duplicate_acl_principal() -> None:
    duplicate = _stage_unsigned()
    duplicate["experiment_acl"] = [
        {
            "service_principal_name": _RUNTIME_ID,
            "all_permissions": [
                {
                    "permission_level": "CAN_MANAGE",
                    "inherited": False,
                    "inherited_from_object": [],
                }
            ],
        },
        {
            "service_principal_name": _RUNTIME_ID,
            "display_name": "same runtime principal",
            "all_permissions": [
                {
                    "permission_level": "CAN_READ",
                    "inherited": False,
                    "inherited_from_object": [],
                }
            ],
        },
    ]
    duplicate["experiment_acl_sha256"] = record_sha256(
        duplicate["experiment_acl"]
    )

    with pytest.raises(RuntimeError, match="ACL principal is duplicated"):
        sign_retirement_record(duplicate)


def test_completion_rejects_nonempty_access_and_inventory_digest_drift() -> None:
    stage = sign_retirement_record(_stage_unsigned())
    access = _completion_unsigned(stage)
    access["effective_access"][0]["resources"][0]["privileges"] = {"EXECUTE": []}
    access["effective_access_sha256"] = record_sha256(access["effective_access"])
    with pytest.raises(RuntimeError, match="UC access is not empty"):
        sign_retirement_record(access)

    inventory = _completion_unsigned(stage)
    inventory["serving_inventory_sha256"] = "f" * 64
    with pytest.raises(RuntimeError, match="serving inventory is invalid"):
        sign_retirement_record(inventory)


def test_completion_rejects_present_absent_overlap_and_owner_drift() -> None:
    stage = sign_retirement_record(_stage_unsigned())
    overlap = _completion_unsigned(stage)
    overlap["expected_absent_inference_tables"] = [
        overlap["inference_tables"][0]["full_name"]
    ]
    with pytest.raises(RuntimeError, match="both present and expected absent"):
        sign_retirement_record(overlap)

    owner = _completion_unsigned(stage)
    owner["model_owner"] = _RUNTIME_ID
    with pytest.raises(RuntimeError, match="completion owner is invalid"):
        sign_retirement_record(owner)


def test_untrusted_signer_and_retired_verification_key_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other_bytes = b"s" * 32
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", _verify_key(other_bytes))
    with pytest.raises(RuntimeError, match="signing authority is not trusted"):
        sign_retirement_record(_stage_unsigned())

    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", _verify_key(_SIGNING_BYTES))
    signed = sign_retirement_record(_stage_unsigned())
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", _verify_key(other_bytes))
    with pytest.raises(RuntimeError, match="attestation identity is invalid"):
        verify_retirement_record(signed)


def test_persist_is_immutable_and_idempotent_for_exact_retry() -> None:
    files = _WorkspaceFiles()
    workspace = _workspace(files)
    record = sign_retirement_record(_stage_unsigned())
    path = stage_path("mip-app", _MODEL_NAME, _LEASE_ID)

    persist_retirement_record(workspace, path, record)
    persist_retirement_record(workspace, path, record)

    assert load_retirement_record(workspace, path) == record
    assert files.upload_calls == [
        (path, ImportFormat.AUTO, False),
        (path, ImportFormat.AUTO, False),
    ]


def test_persist_rejects_immutable_conflict() -> None:
    files = _WorkspaceFiles()
    workspace = _workspace(files)
    path = stage_path("mip-app", _MODEL_NAME, _LEASE_ID)
    first = sign_retirement_record(_stage_unsigned())
    changed = _stage_unsigned()
    changed["created_at"] = "2026-07-28T20:00:01+00:00"
    second = sign_retirement_record(changed)
    persist_retirement_record(workspace, path, first)

    with pytest.raises(RuntimeError, match="immutable record already differs"):
        persist_retirement_record(workspace, path, second)


def test_persist_accepts_exact_commit_after_lost_response() -> None:
    files = _WorkspaceFiles()
    files.upload_mode = "commit_then_raise"
    workspace = _workspace(files)
    record = sign_retirement_record(_stage_unsigned())
    path = stage_path("mip-app", _MODEL_NAME, _LEASE_ID)

    persist_retirement_record(workspace, path, record)

    assert load_retirement_record(workspace, path) == record


def test_persist_rechecks_lease_after_mkdirs_before_upload() -> None:
    files = _WorkspaceFiles()
    workspace = _workspace(files)
    record = sign_retirement_record(_stage_unsigned())
    path = stage_path("mip-app", _MODEL_NAME, _LEASE_ID)
    checks = 0

    def assert_held() -> None:
        nonlocal checks
        checks += 1
        if checks == 2:
            raise RuntimeError("deployment lease is no longer held")

    with pytest.raises(RuntimeError, match="lease is no longer held"):
        persist_retirement_record(
            workspace,
            path,
            record,
            assert_before_mutation=assert_held,
        )

    assert checks == 2
    assert files.mkdirs_calls == [path.rsplit("/", 1)[0]]
    assert files.upload_calls == []
    assert files.records == {}


def test_persist_rejects_no_commit_foreign_commit_and_ambiguous_readback() -> None:
    record = sign_retirement_record(_stage_unsigned())
    path = stage_path("mip-app", _MODEL_NAME, _LEASE_ID)

    no_commit = _WorkspaceFiles()
    no_commit.upload_mode = "no_commit"
    with pytest.raises(RuntimeError, match="failed without an exact commit"):
        persist_retirement_record(_workspace(no_commit), path, record)

    foreign = _WorkspaceFiles()
    foreign.upload_mode = "corrupt_commit"
    with pytest.raises(RuntimeError, match="commit is ambiguous"):
        persist_retirement_record(_workspace(foreign), path, record)

    ambiguous = _WorkspaceFiles()
    ambiguous.upload_mode = "no_commit"
    ambiguous.download_error = OSError("readback unavailable")
    with pytest.raises(RuntimeError, match="commit is ambiguous"):
        persist_retirement_record(_workspace(ambiguous), path, record)


def test_load_returns_none_only_for_absence_and_rejects_invalid_json() -> None:
    files = _WorkspaceFiles()
    workspace = _workspace(files)
    path = stage_path("mip-app", _MODEL_NAME, _LEASE_ID)
    assert load_retirement_record(workspace, path) is None

    files.records[path] = b"not-json"
    with pytest.raises(RuntimeError, match="not valid JSON"):
        load_retirement_record(workspace, path)


def test_canonical_json_and_digest_are_order_independent_for_mappings() -> None:
    left = {"b": 2, "a": {"d": 4, "c": 3}}
    right = {"a": {"c": 3, "d": 4}, "b": 2}

    assert canonical_json(left) == canonical_json(right)
    assert record_sha256(left) == record_sha256(right)
