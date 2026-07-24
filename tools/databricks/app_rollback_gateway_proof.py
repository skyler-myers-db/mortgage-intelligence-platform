"""Resolve exact live Gateway and reviewed-function proof for App rollback."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from tools.databricks.app_rollback_record_contract import (
    LEGACY_RECORD_VERSION,
    RECORD_VERSION,
    _validated_gateway_resources,
    _validated_payload,
)
from tools.databricks.export_gateway_runtime_contract import ExactGatewayRuntimeProof
from tools.databricks.gateway_legacy_rollback import (
    validated_legacy_gateway_resources,
)


def _payload_environment(payload: dict[str, object]) -> dict[str, str]:
    env_vars = cast(list[object], payload["env_vars"])
    return {
        str(item["name"]): str(item.get("value") or "")
        for item in env_vars
        if isinstance(item, dict) and "value" in item
    }


def resolve_stored_gateway_resource_proof(
    workspace: Any,
    *,
    record: dict[str, Any],
    candidate_reviewed_function_owner: str | None,
    authenticate_owner: Callable[..., str],
    assert_function_set: Callable[..., None],
    assert_legacy_resources: Callable[..., dict[str, str]],
    resolve_exact_resource_proof: Callable[..., ExactGatewayRuntimeProof],
) -> ExactGatewayRuntimeProof:
    """Validate every supported signed-record generation without widening it."""

    if record.get("version", RECORD_VERSION) == LEGACY_RECORD_VERSION:
        if candidate_reviewed_function_owner is not None:
            raise RuntimeError(
                "legacy App rollback proof cannot use candidate function-owner authority"
            )
        legacy = validated_legacy_gateway_resources(record.get("gateway_resources"))
        verified = assert_legacy_resources(workspace, expected=legacy)
        owner = authenticate_owner(workspace, catalog=legacy["catalog"])
        assert_function_set(
            workspace,
            catalog=legacy["catalog"],
            expected_owner=owner,
            allow_legacy_segment_determinism=True,
        )
        return ExactGatewayRuntimeProof(
            contract={
                key: value
                for key, value in verified.items()
                if key != "resource_digest"
            },
            digest=verified["resource_digest"],
        )

    resources = _validated_gateway_resources(record.get("gateway_resources"))
    owner = candidate_reviewed_function_owner or ""
    legacy_function_contract = False
    if candidate_reviewed_function_owner is not None:
        authenticated_owner = authenticate_owner(
            workspace,
            catalog=resources["catalog"],
        )
        if candidate_reviewed_function_owner != authenticated_owner:
            raise RuntimeError(
                "candidate reviewed-function owner is not the authenticated deployer"
            )
        owner = authenticated_owner
    else:
        payload = _validated_payload(record.get("payload"))
        owner = _payload_environment(payload).get("MIP_REVIEWED_FUNCTION_OWNER", "")
        legacy_function_contract = not owner
    if legacy_function_contract:
        owner = authenticate_owner(workspace, catalog=resources["catalog"])
    return resolve_exact_resource_proof(
        workspace,
        supervisor_name=resources["supervisor_canonical_name"],
        catalog=resources["catalog"],
        genie_space_id=resources["genie_space_id"],
        runtime_application_id=resources["runtime_application_id"],
        reviewed_function_owner=owner,
        supervisor_id=resources["supervisor_id"],
        gateway_endpoint=resources["gateway_endpoint"],
        expected=resources,
        require_resource_binding=True,
        allow_legacy_reviewed_function_contract=legacy_function_contract,
    )
