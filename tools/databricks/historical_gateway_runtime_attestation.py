"""Authenticate governed historical Gateway endpoints for bounded retirement."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Sequence
from typing import Any

from backend.agents.gateway_contract import (
    DEFAULT_GATEWAY_ENDPOINT,
    GATEWAY_ENDPOINT_DESCRIPTION,
    GATEWAY_RUNTIME_RESOURCE_ENV,
    LEGACY_GATEWAY_ENDPOINT,
)
from backend.agents.gateway_live_resource_contract import (
    assert_live_historical_gateway_runtime_resources,
)
from tools.databricks.agent_runtime_access import assert_runtime_creator
from tools.databricks.cutover_journal_store import read_cutover_journal
from tools.databricks.gateway_legacy_rollback import (
    LEGACY_GATEWAY_RESOURCE_FIELDS,
    PRIOR_GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION,
    PRIOR_V2_GATEWAY_RESOURCE_FIELDS,
    PRIOR_V2_LEGACY_GATEWAY_RESOURCE_FIELDS,
    assert_live_legacy_gateway_resources,
    assert_live_prior_v2_gateway_resources,
)
from tools.databricks.gateway_runtime_resource_binding import (
    gateway_runtime_resource_binding_environment,
)
from tools.databricks.historical_agent_endpoint_types import GatewayPin
from tools.databricks.historical_gateway_attestation import attest_legacy_gateway
from tools.databricks.m2m_access_policy import is_reserved_gateway_endpoint

_HASH = r"[0-9a-f]{12}"


def gateway_family(name: str, prefix: str) -> bool:
    if prefix == LEGACY_GATEWAY_ENDPOINT:
        return name == prefix
    if prefix == DEFAULT_GATEWAY_ENDPOINT:
        return is_reserved_gateway_endpoint(name)
    return (
        re.fullmatch(
            rf"{re.escape(prefix)}(?:-{_HASH}(?:-mq1)?)?",
            name,
        )
        is not None
    )


def assert_retirement_gateway_pin_from_signed_journal(
    workspace: Any,
    *,
    pin: GatewayPin,
    runtime_application_id: str,
    canonical_name: str,
) -> None:
    """Bind a retirement-only Gateway pin to the authenticated cutover journal."""

    journal = read_cutover_journal(
        workspace,
        runtime_application_id=runtime_application_id,
    )
    signed_tuple = None
    if journal is not None:
        signed_tuple = (
            journal.get("canonical_name"),
            journal.get("old_gateway_endpoint"),
            journal.get("old_gateway_endpoint_id"),
            journal.get("old_gateway_creator"),
        )
    if signed_tuple != (
        canonical_name,
        pin.name,
        pin.endpoint_id,
        pin.creator,
    ):
        raise RuntimeError(
            "historical Gateway retirement tuple is not bound to the signed cutover journal"
        )


def _trusted_resource_binding(name: str, binding: dict[str, str]) -> None:
    trusted_keys = {
        os.environ.get("MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY", "").strip(),
        os.environ.get(
            "MIP_GATEWAY_MODEL_ATTESTATION_PREVIOUS_VERIFY_KEY",
            "",
        ).strip(),
    } - {""}
    embedded_keys = {
        binding.get("MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY", "").strip(),
        binding.get(
            "MIP_GATEWAY_MODEL_ATTESTATION_PREVIOUS_VERIFY_KEY",
            "",
        ).strip(),
    } - {""}
    if not trusted_keys or not embedded_keys or not embedded_keys.issubset(trusted_keys):
        raise RuntimeError(
            f"Gateway endpoint {name!r} resource-proof trust epoch is not configured"
        )


def _signed_resource_contract(
    workspace: Any,
    *,
    name: str,
    binding: dict[str, str],
) -> dict[str, str]:
    _trusted_resource_binding(name, binding)
    contract_json = binding.get(
        "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_CONTRACT_JSON",
        "",
    )
    try:
        decoded_contract = json.loads(contract_json)
    except (TypeError, json.JSONDecodeError):
        decoded_contract = None
    legacy_contract = (
        isinstance(decoded_contract, dict)
        and set(decoded_contract) == LEGACY_GATEWAY_RESOURCE_FIELDS
    )
    prior_v2_contract = (
        isinstance(decoded_contract, dict)
        and decoded_contract.get("proof_version") == PRIOR_GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION
        and set(decoded_contract)
        in {
            PRIOR_V2_LEGACY_GATEWAY_RESOURCE_FIELDS,
            PRIOR_V2_GATEWAY_RESOURCE_FIELDS,
        }
    )
    try:
        if prior_v2_contract:
            verified = assert_live_prior_v2_gateway_resources(
                workspace,
                expected={
                    **decoded_contract,
                    "resource_digest": binding.get(
                        "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SHA256",
                        "",
                    ),
                },
            )
            return {key: value for key, value in verified.items() if key != "resource_digest"}
        if legacy_contract:
            verified = assert_live_legacy_gateway_resources(
                workspace,
                expected={
                    **decoded_contract,
                    "resource_digest": binding.get(
                        "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SHA256",
                        "",
                    ),
                },
            )
            return {key: value for key, value in verified.items() if key != "resource_digest"}
        return assert_live_historical_gateway_runtime_resources(
            workspace,
            environment=binding,
        )
    except (RuntimeError, ValueError) as exc:
        raise RuntimeError(
            f"Gateway endpoint {name!r} has an invalid runtime-resource proof"
        ) from exc


def live_gateway_contract(
    workspace: Any,
    details: Any,
    *,
    name: str,
    gateway_prefixes: Sequence[str],
    runtime_application_id: str,
    supervisor_name: str,
    catalog: str,
    genie_space_id: str,
    assert_single_writer: Callable[[], None],
) -> dict[str, str]:
    """Authenticate one historical Gateway without mutating or re-signing it."""

    if not any(gateway_family(name, prefix) for prefix in gateway_prefixes):
        raise RuntimeError("Gateway candidate is outside the governed name family")
    endpoint_id = str(getattr(details, "id", None) or "").strip()
    creator = str(getattr(details, "creator", None) or "").strip()
    if not endpoint_id:
        raise RuntimeError(f"Gateway endpoint {name!r} has no immutable ID")
    assert_runtime_creator(
        creator,
        application_id=runtime_application_id,
        resource=f"historical Gateway endpoint {name}",
    )
    if getattr(details, "pending_config", None) is not None:
        raise RuntimeError(f"historical Gateway endpoint {name!r} has a pending update")
    binding = gateway_runtime_resource_binding_environment(details)
    proof_fields = GATEWAY_RUNTIME_RESOURCE_ENV - {
        "MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY",
        "MIP_GATEWAY_MODEL_ATTESTATION_PREVIOUS_VERIFY_KEY",
    }
    if set(binding) & proof_fields:
        contract = _signed_resource_contract(
            workspace,
            name=name,
            binding=binding,
        )
    else:
        contract = attest_legacy_gateway(
            workspace,
            details,
            endpoint_name=name,
            endpoint_prefixes=gateway_prefixes,
            runtime_application_id=runtime_application_id,
            supervisor_name=supervisor_name,
            catalog=catalog,
            genie_space_id=genie_space_id,
            assert_single_writer=assert_single_writer,
        )
    exact_scope = {
        "gateway_endpoint": name,
        "gateway_endpoint_id": endpoint_id,
        "gateway_endpoint_creator": creator,
        "runtime_application_id": runtime_application_id,
        "supervisor_canonical_name": supervisor_name,
        "catalog": catalog,
        "genie_space_id": genie_space_id,
        "gateway_endpoint_description": GATEWAY_ENDPOINT_DESCRIPTION,
    }
    if any(contract.get(key) != expected for key, expected in exact_scope.items()):
        raise RuntimeError(f"Gateway endpoint {name!r} signed identity or scope drifted")
    return contract
