"""Authoritative family reconciliation for unprotected Gateway allocations."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from tools.databricks.gateway_model_archival import (
    GatewayModelArchiveScope,
    _field,
    archive_gateway_model,
)
from tools.databricks.gateway_model_archival_protection import (
    discover_protected_allocation_contracts,
)
from tools.databricks.gateway_model_retirement_record import record_sha256


def archive_unprotected_gateway_models(
    workspace: Any,
    model_registry: Any,
    tracking_client: Any,
    *,
    scope: GatewayModelArchiveScope,
    resolve_delta_version: Callable[[str], str],
) -> tuple[dict[str, Any], ...]:
    """Archive every exact family model absent from authenticated release protection."""

    pattern = re.compile(rf"{re.escape(scope.model_family)}_[0-9a-f]{{12}}\Z")

    def protection_inventory() -> tuple[dict[str, Any], ...]:
        return discover_protected_allocation_contracts(
            workspace,
            model_registry,
            tracking_client,
            app_name=scope.app_name,
            runtime_application_id=scope.runtime_application_id,
            rollback_scope=scope.rollback_scope,
            expected_lakebase_instance=scope.expected_lakebase_instance,
            model_family=scope.model_family,
            experiment_base=scope.experiment_base,
            catalog=scope.catalog,
            inference_schema=scope.inference_schema,
            inference_table_prefix=scope.inference_table_prefix,
        )

    def model_inventory() -> dict[str, str]:
        models: dict[str, str] = {}
        for model in workspace.registered_models.list(include_browse=True):
            model_name = _field(model, "full_name")
            if pattern.fullmatch(model_name) is None:
                continue
            if model_name in models:
                raise RuntimeError("Gateway archival reconciliation found duplicate models")
            models[model_name] = _field(model, "owner")
        return models

    protected = protection_inventory()
    protected_sha256 = record_sha256(protected)
    protected_models = {
        str(item.get("gateway_model_name") or "")
        for item in protected
        if pattern.fullmatch(str(item.get("gateway_model_name") or "")) is not None
    }
    models = model_inventory()
    if not models and not protected_models:
        if (
            record_sha256(protection_inventory()) != protected_sha256
            or model_inventory()
        ):
            raise RuntimeError("Gateway archival empty reconciliation inventory changed")
        return ()
    if not models or not protected_models or not protected_models.issubset(models):
        raise RuntimeError("Gateway archival reconciliation protection is incomplete")
    if any(
        models[model_name] != scope.runtime_application_id
        for model_name in protected_models
    ):
        raise RuntimeError("protected Gateway allocation is not runtime-owned")
    historical_models = sorted(set(models) - protected_models)
    for model_name in historical_models:
        archive_gateway_model(
            workspace,
            model_registry,
            tracking_client,
            scope=scope,
            model_name=model_name,
            resolve_delta_version=resolve_delta_version,
        )
    final_protected = protection_inventory()
    final_models = model_inventory()
    expected_final_owners = {
        model_name: (
            scope.runtime_application_id
            if model_name in protected_models
            else scope.archive_owner
        )
        for model_name in models
    }
    if (
        record_sha256(final_protected) != protected_sha256
        or final_models != expected_final_owners
    ):
        raise RuntimeError("Gateway archival reconciliation inventory changed")
    return tuple(
        archive_gateway_model(
            workspace,
            model_registry,
            tracking_client,
            scope=scope,
            model_name=model_name,
            resolve_delta_version=resolve_delta_version,
        )
        for model_name in historical_models
    )
