"""Runtime-side consumption and reconciliation of Gateway lifecycle proof."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from tools.databricks.gateway_model_lifecycle_proof import (
    GatewayModelLifecycleProof,
    GatewayModelLifecycleState,
    consume_gateway_model_lifecycle_proof,
)


@dataclass(frozen=True)
class RuntimeGatewayLifecycleBoundary:
    """Consumed lifecycle states indexed by exact model and allocation suffix."""

    enabled: bool
    states: Mapping[str, GatewayModelLifecycleState]
    states_by_suffix: Mapping[str, GatewayModelLifecycleState]


def consume_runtime_gateway_lifecycle_boundary(
    proof: GatewayModelLifecycleProof | None,
    *,
    application_id: str,
    expected_inventory_principal: str | None,
    catalog: str,
    metastore_id: str,
    workspace_id: str,
    model_family: str,
    candidate_model: str,
) -> RuntimeGatewayLifecycleBoundary:
    """Consume and bind one admin proof to the authenticated runtime boundary."""

    if proof is None:
        return RuntimeGatewayLifecycleBoundary(False, {}, {})
    consumed = consume_gateway_model_lifecycle_proof(proof)
    expected_principal = (expected_inventory_principal or "").strip()
    actual_identity = (
        consumed.application_id,
        consumed.inventory_principal,
        consumed.catalog,
        consumed.metastore_id,
        consumed.workspace_id,
        consumed.model_family,
        consumed.candidate_model,
    )
    expected_identity = (
        application_id,
        expected_principal,
        catalog,
        metastore_id,
        workspace_id,
        model_family,
        candidate_model,
    )
    if actual_identity != expected_identity:
        raise RuntimeError(
            "Gateway model lifecycle proof does not match the runtime boundary"
        )
    states = consumed.by_model
    by_suffix = {
        name.rsplit("_", 1)[-1]: state
        for name, state in states.items()
    }
    if len(by_suffix) != len(states):
        raise RuntimeError("Gateway model lifecycle proof reuses a model suffix")
    return RuntimeGatewayLifecycleBoundary(True, states, by_suffix)


def classify_runtime_inference_table(
    *,
    state: GatewayModelLifecycleState | None,
    owner: str,
    principal: str,
    table_full_name: str,
    inference_suffix: str,
    candidate_suffix: str,
    actual_sources: Mapping[str, Any],
    runtime_direct: set[Any],
) -> str:
    """Classify one visible inference table as exact active or archived state."""

    if owner != principal:
        if (
            state is None
            or state.disposition != "archived"
            or state.owner != owner
            or table_full_name not in state.inference_tables
        ):
            raise RuntimeError(
                f"Gateway inference table {table_full_name} ownership is not "
                "covered by an exact archived-allocation proof"
            )
        if actual_sources:
            raise RuntimeError(
                f"archived Gateway inference table {table_full_name} remains "
                "accessible to agent-runtime"
            )
        return "archived"
    if state is not None and (
        state.disposition != "active"
        or state.owner != principal
        or table_full_name not in state.inference_tables
    ):
        raise RuntimeError(
            f"runtime-owned Gateway inference table {table_full_name} "
            "contradicts its lifecycle proof"
        )
    if state is None and inference_suffix != candidate_suffix:
        raise RuntimeError(
            f"historical Gateway inference table {table_full_name} lacks "
            "an active allocation proof"
        )
    if any(sources != runtime_direct for sources in actual_sources.values()):
        raise RuntimeError(
            f"agent-runtime inference table {table_full_name.rsplit('.', 1)[-1]} "
            "lacks exact direct runtime ownership"
        )
    return "active"


def classify_runtime_gateway_model(
    *,
    state: GatewayModelLifecycleState | None,
    owner: str,
    principal: str,
    full_name: str,
    candidate_model: str,
    actual_sources: Mapping[str, Any],
    runtime_direct: set[Any],
) -> str:
    """Classify one visible reviewed model as exact active or archived state."""

    if owner != principal:
        if (
            state is None
            or state.disposition != "archived"
            or state.owner != owner
            or full_name == candidate_model
        ):
            raise RuntimeError(
                f"Gateway model {full_name} ownership is not covered by an "
                "exact archived-allocation proof"
            )
        if actual_sources:
            raise RuntimeError(
                f"archived Gateway model {full_name} remains accessible to agent-runtime"
            )
        return "archived"
    if state is not None and (
        state.disposition != "active" or state.owner != principal
    ):
        raise RuntimeError(
            f"runtime-owned Gateway model {full_name} contradicts its lifecycle proof"
        )
    if full_name != candidate_model and state is None:
        raise RuntimeError(
            f"historical Gateway model {full_name} lacks an active allocation proof"
        )
    if any(sources != runtime_direct for sources in actual_sources.values()):
        raise RuntimeError(
            f"agent-runtime Gateway model {full_name} lacks exact direct runtime ownership"
        )
    return "active"


def assert_runtime_gateway_lifecycle_inventory(
    boundary: RuntimeGatewayLifecycleBoundary,
    *,
    reviewed_model_suffixes: set[str],
    archived_model_suffixes: set[str],
    reviewed_inference_tables: set[str],
    archived_inference_tables: set[str],
) -> None:
    """Reconcile the consumed proof with every visible active and archived artifact."""

    if not boundary.enabled:
        return
    active_states = {
        state.model_name: state
        for state in boundary.states.values()
        if state.disposition == "active"
    }
    archived_states = {
        state.model_name: state
        for state in boundary.states.values()
        if state.disposition == "archived"
    }
    visible_active_names = {
        name
        for name in boundary.states
        if name.rsplit("_", 1)[-1] in reviewed_model_suffixes
    }
    if visible_active_names != set(active_states):
        raise RuntimeError(
            "Gateway model lifecycle proof does not match visible active allocations"
        )
    visible_archived_names = {
        name
        for name in boundary.states
        if name.rsplit("_", 1)[-1] in archived_model_suffixes
    }
    if not visible_archived_names.issubset(archived_states):
        raise RuntimeError(
            "Gateway model lifecycle proof misclassifies a visible archived allocation"
        )
    expected_active_tables = {
        table_name
        for state in active_states.values()
        for table_name in state.inference_tables
    }
    if reviewed_inference_tables != expected_active_tables:
        raise RuntimeError(
            "Gateway model lifecycle proof does not match visible active inference tables"
        )
    expected_archived_tables = {
        table_name
        for state in archived_states.values()
        for table_name in state.inference_tables
    }
    if not archived_inference_tables.issubset(expected_archived_tables):
        raise RuntimeError(
            "Gateway model lifecycle proof misclassifies a visible archived inference table"
        )
