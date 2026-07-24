"""Immutable names and identities for the governed Gateway Agent resources."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.agents.gateway_contract import (
    DEFAULT_GATEWAY_AGENT_MODEL,
    gateway_experiment_base,
    gateway_model_family,
    gateway_proxy_source_hash,
)
from tools.databricks.agent_runtime_access import assert_runtime_creator


@dataclass(frozen=True)
class GatewayAgentDeployment:
    endpoint: str
    supervisor_id: str
    supervisor_endpoint_id: str
    upstream_endpoint: str
    runtime_application_id: str
    proxy_caller_application_id: str
    proxy_caller_credential_id: str
    proxy_caller_secret_reference: str
    model_name: str
    model_version: int
    model_source: str
    model_attestation_verify_key: str
    model_family: str
    source_hash: str
    resource_hash: str
    inference_table: str
    inference_table_prefix: str
    experiment_base: str
    experiment_name: str
    experiment_id: str
    catalog: str
    genie_space_id: str


def gateway_agent_source_hash(*, upstream_endpoint: str, catalog: str, genie_space_id: str) -> str:
    return gateway_proxy_source_hash(
        upstream_endpoint=upstream_endpoint,
        catalog=catalog,
        genie_space_id=genie_space_id,
    )


def gateway_inference_table_prefix(*, base_prefix: str, contract_hash: str) -> str:
    """Give every immutable Gateway endpoint its own Databricks-created table family."""

    return f"{base_prefix}_{contract_hash[:12]}"


def gateway_agent_model_name(*, base_model_name: str, contract_hash: str) -> str:
    """Return a runtime-owned UC model name isolated from legacy human artifacts."""

    return f"{base_model_name}_{contract_hash[:12]}"


def gateway_experiment_name(
    *,
    base_experiment_name: str,
    contract_hash: str,
    runtime_application_id: str,
) -> str:
    """Return a runtime-owned MLflow experiment isolated per reviewed contract."""

    base = gateway_experiment_base(
        runtime_application_id=runtime_application_id,
        experiment_family=base_experiment_name,
    )
    return f"{base}-{contract_hash[:12]}"


def _target_model_family(*, configured: str, catalog: str) -> str:
    """Keep custom model leaves but always place them in the selected catalog."""

    parts = configured.strip().split(".")
    if len(parts) != 3 or any(not part for part in parts):
        raise ValueError("Gateway model family must be a three-part Unity Catalog name")
    default_parts = DEFAULT_GATEWAY_AGENT_MODEL.split(".")
    if parts[0] != catalog:
        if parts[1:] != default_parts[1:]:
            raise ValueError("Gateway model family belongs to a different target catalog")
        return gateway_model_family(catalog=catalog)
    return configured.strip()


def _resolve_exact_experiment(
    tracking_client: Any,
    *,
    experiment_name: str,
    experiment_id: str,
    runtime_application_id: str,
) -> Any:
    """Resolve name and immutable ID independently and require runtime ownership."""

    try:
        by_name = tracking_client.get_experiment_by_name(experiment_name)
        by_id = tracking_client.get_experiment(experiment_id)
    except Exception as exc:  # noqa: BLE001 - live experiment proof is fail-closed
        raise RuntimeError("could not resolve the exact Gateway MLflow experiment") from exc
    if by_name is None or by_id is None:
        raise RuntimeError("Gateway MLflow experiment is missing")
    expected = (experiment_id, experiment_name)
    for resolved in (by_name, by_id):
        actual = (
            str(getattr(resolved, "experiment_id", "") or "").strip(),
            str(getattr(resolved, "name", "") or "").strip(),
        )
        if actual != expected:
            raise RuntimeError("Gateway MLflow experiment name/ID binding drifted")
        lifecycle_raw = getattr(resolved, "lifecycle_stage", None)
        lifecycle = str(getattr(lifecycle_raw, "value", lifecycle_raw) or "").lower()
        if lifecycle != "active":
            raise RuntimeError("Gateway MLflow experiment is not active")
        assert_runtime_creator(
            (getattr(resolved, "tags", None) or {}).get("mlflow.ownerEmail"),
            application_id=runtime_application_id,
            resource=f"MLflow experiment {experiment_name}",
        )
    return by_id
