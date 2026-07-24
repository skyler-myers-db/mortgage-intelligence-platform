"""Build immutable versioned Databricks App rollback records."""

from __future__ import annotations

import copy
from typing import Any

from tools.databricks.app_deployment_state import immutable_source
from tools.databricks.app_rollback_record_contract import (
    RECORD_VERSION,
    _payload_digest,
    _text,
    _validated_gateway_resources,
    _validated_payload,
)
from tools.databricks.app_rollback_resource_contract import (
    app_resource_contract_digest,
    validated_app_resource_contract,
)
from tools.databricks.gateway_legacy_rollback import validated_legacy_gateway_resources


def build_app_rollback_record(
    *,
    app_name: str,
    deployment: object,
    payload: dict[str, object],
    git_sha: str,
    gateway_binding: str | None,
    gateway_resources: dict[str, str],
    app_resources: list[dict[str, object]],
    app_service_principal_client_id: str,
    app_service_principal_scim_id: str,
    expected_lakebase_instance: str,
    pending_proxy_credential_retirement_ids: tuple[str, ...] = (),
    record_version: int = RECORD_VERSION,
) -> dict[str, Any]:
    immutable_payload = copy.deepcopy(payload)
    immutable_payload["source_code_path"] = immutable_source(deployment)
    immutable_payload = _validated_payload(
        immutable_payload,
        expected_lakebase_instance=expected_lakebase_instance,
    )
    resources = (
        _validated_gateway_resources(gateway_resources)
        if record_version == RECORD_VERSION
        else validated_legacy_gateway_resources(gateway_resources)
    )
    validated_app_resources = validated_app_resource_contract(app_resources)
    record = {
        "version": record_version,
        "app_name": app_name,
        "deployment_id": _text(getattr(deployment, "deployment_id", None)),
        "app_service_principal_client_id": app_service_principal_client_id,
        "app_service_principal_scim_id": app_service_principal_scim_id,
        "git_sha": git_sha,
        "gateway_binding_sha256": gateway_binding,
        "gateway_resources": resources,
        "app_resources": validated_app_resources,
        "app_resources_sha256": app_resource_contract_digest(validated_app_resources),
        "payload": immutable_payload,
        "payload_sha256": _payload_digest(immutable_payload),
    }
    if record_version == RECORD_VERSION:
        record["pending_proxy_credential_retirement_ids"] = list(
            pending_proxy_credential_retirement_ids
        )
    return record
