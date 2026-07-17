"""Shared immutable contract for the governed Supervisor proxy endpoint."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from collections.abc import Mapping
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

DEFAULT_GATEWAY_AGENT_MODEL = "mip.audit.mortgage_growth_supervisor_proxy"
DEFAULT_GATEWAY_AGENT_EXPERIMENT = "mip-agent-runtime-gateway-proxy"
DEFAULT_GATEWAY_ENDPOINT = "mip-growth-agent-gateway"
LEGACY_GATEWAY_ENDPOINT = "mip-agent-gateway"
DEFAULT_GATEWAY_INFERENCE_TABLE = "mip.audit.mip_agent_gateway_growth_agent"
GATEWAY_PROXY_SOURCE_HASH_TAG = "mip.proxy_source_hash"
GATEWAY_UPSTREAM_TAG = "mip.upstream_supervisor_endpoint"
GATEWAY_MODEL_REQUIREMENTS = (
    "mlflow==3.14.0",
    "databricks-sdk==0.103.0",
    "cryptography==48.0.1",
)
GATEWAY_STATIC_ENV = {
    "ENABLE_LANGCHAIN_STREAMING": "true",
    "ENABLE_MLFLOW_TRACING": "true",
    "RETURN_REQUEST_ID_IN_RESPONSE": "true",
}
GATEWAY_WORKLOAD_SIZE = "Small"
GATEWAY_WORKLOAD_TYPE = "CPU"
GATEWAY_SCALE_TO_ZERO_ENABLED = True
GATEWAY_BURST_SCALING_ENABLED = False
GATEWAY_ROUTE_OPTIMIZED = False
GATEWAY_TRAFFIC_PERCENTAGE = 100
GATEWAY_ENDPOINT_DESCRIPTION = (
    "MIP governed ResponsesAgent boundary delegating product planning "
    "to the managed Mortgage Growth Agent Supervisor."
)
GATEWAY_DEPLOYMENT_SPEC_VERSION = "gateway-supervisor-proxy-v3-runtime-contract"
GATEWAY_PROXY_SOURCE = Path(__file__).with_name("mortgage_growth_supervisor_proxy.py")
GATEWAY_PROXY_TRANSITIVE_SOURCES = (
    GATEWAY_PROXY_SOURCE,
    Path(__file__),
    Path(__file__).with_name("gateway_live_resource_contract.py"),
    Path(__file__).with_name("reviewed_uc_function_contract.py"),
    Path(__file__).with_name("supervisor_contract.py"),
    Path(__file__).parents[1] / "services" / "ai_gateway_proof_attestation.py",
)
GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION = "gateway-runtime-resource-proof-v2"
GATEWAY_RUNTIME_RESOURCE_ATTESTATION_ALG = "ed25519-gateway-runtime-resource-v1"
GATEWAY_RUNTIME_RESOURCE_ENV = frozenset(
    {
        "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_CONTRACT_JSON",
        "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SHA256",
        "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SIGNATURE",
        "MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY",
        "MIP_GATEWAY_MODEL_ATTESTATION_PREVIOUS_VERIFY_KEY",
    }
)
_GATEWAY_RUNTIME_RESOURCE_FIELDS = frozenset(
    {
        "proof_version",
        "catalog",
        "genie_space_id",
        "runtime_application_id",
        "supervisor_canonical_name",
        "supervisor_display_name",
        "supervisor_contract_json",
        "supervisor_contract_sha256",
        "supervisor_id",
        "supervisor_creator",
        "supervisor_endpoint",
        "supervisor_endpoint_id",
        "supervisor_endpoint_creator",
        "gateway_endpoint",
        "gateway_endpoint_id",
        "gateway_endpoint_creator",
        "gateway_endpoint_description",
        "gateway_endpoint_task",
        "gateway_endpoint_route_optimized",
        "gateway_endpoint_budget_policy",
        "gateway_endpoint_email_notifications",
        "gateway_endpoint_deprecated_rate_limits",
        "gateway_source_hash",
        "gateway_resource_hash",
        "gateway_model_family",
        "gateway_model_name",
        "gateway_model_version",
        "gateway_model_source",
        "gateway_model_owner",
        "gateway_experiment_base",
        "gateway_experiment_acl_json",
        "gateway_experiment_acl_sha256",
        "gateway_experiment_name",
        "gateway_experiment_id",
        "gateway_experiment_owner",
        "gateway_inference_table_family",
        "gateway_inference_table",
    }
)
_UC_IDENTIFIER = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]*\Z")


def _catalog(value: str) -> str:
    normalized = value.strip()
    if _UC_IDENTIFIER.fullmatch(normalized) is None:
        raise ValueError("Gateway target catalog is invalid")
    return normalized


def gateway_model_family(*, catalog: str) -> str:
    """Derive the governed model family from the selected target catalog."""

    return f"{_catalog(catalog)}.audit.mortgage_growth_supervisor_proxy"


def gateway_inference_table_family(*, catalog: str) -> str:
    """Derive the governed inference-table family from the target catalog."""

    return f"{_catalog(catalog)}.audit.mip_agent_gateway_growth_agent"


def gateway_experiment_base(
    *,
    runtime_application_id: str,
    experiment_family: str = DEFAULT_GATEWAY_AGENT_EXPERIMENT,
) -> str:
    """Return the deterministic runtime-owned experiment home, never `/Shared`."""

    application_id = runtime_application_id.strip()
    family = experiment_family.strip()
    if (
        not application_id
        or "/" in application_id
        or application_id in {".", ".."}
        or not family
        or "/" in family
        or family in {".", ".."}
    ):
        raise ValueError("Gateway MLflow experiment identity is invalid")
    return f"/Users/{application_id}/{family}"


def gateway_proxy_source_hash(*, upstream_endpoint: str, catalog: str, genie_space_id: str) -> str:
    """Bind reviewed proxy bytes, runtime pins, and every transitive resource."""

    deployment_spec = "\0".join(
        [
            GATEWAY_DEPLOYMENT_SPEC_VERSION,
            upstream_endpoint,
            genie_space_id,
            f"{catalog}.gold.fn_build_cohort",
            f"{catalog}.gold.fn_segment_counts",
            f"{catalog}.gold.fn_lead_queue_url",
            *GATEWAY_MODEL_REQUIREMENTS,
        ]
    ).encode("utf-8")
    source_bytes = b"\0".join(path.read_bytes() for path in GATEWAY_PROXY_TRANSITIVE_SOURCES)
    return hashlib.sha256(source_bytes + b"\0" + deployment_spec).hexdigest()


def gateway_resource_allocation_hash(
    *,
    source_hash: str,
    supervisor_id: str,
    supervisor_endpoint_id: str,
    runtime_application_id: str,
    model_name: str,
    experiment_name: str,
    inference_schema: str,
    inference_table_prefix: str,
    attestation_verify_key: str,
    environment: Mapping[str, str] = GATEWAY_STATIC_ENV,
    workload_size: str = GATEWAY_WORKLOAD_SIZE,
    workload_type: str = GATEWAY_WORKLOAD_TYPE,
    scale_to_zero_enabled: bool = GATEWAY_SCALE_TO_ZERO_ENABLED,
    burst_scaling_enabled: bool = GATEWAY_BURST_SCALING_ENABLED,
    route_optimized: bool = GATEWAY_ROUTE_OPTIMIZED,
    traffic_percentage: int = GATEWAY_TRAFFIC_PERCENTAGE,
    description: str = GATEWAY_ENDPOINT_DESCRIPTION,
) -> str:
    """Bind every mutable input and trust epoch to immutable green resources."""

    supervisor_identity = supervisor_id.strip()
    supervisor_endpoint_identity = supervisor_endpoint_id.strip()
    runtime_identity = runtime_application_id.strip()
    if not supervisor_identity or not supervisor_endpoint_identity or not runtime_identity:
        raise ValueError("Gateway Supervisor endpoint and runtime identities are required")
    verify_key = attestation_verify_key.strip()
    if not verify_key:
        raise ValueError("Gateway model attestation verification key is required")

    contract = {
        "source_hash": source_hash,
        "supervisor_id": supervisor_identity,
        "supervisor_endpoint_id": supervisor_endpoint_identity,
        "runtime_application_id": runtime_identity,
        "model_name": model_name,
        "experiment_name": experiment_name,
        "inference_schema": inference_schema,
        "inference_table_prefix": inference_table_prefix,
        "attestation_verify_key": verify_key,
        "environment": dict(environment),
        "workload_size": workload_size,
        "workload_type": workload_type,
        "scale_to_zero_enabled": scale_to_zero_enabled,
        "burst_scaling_enabled": burst_scaling_enabled,
        "endpoint_policy": {
            "budget_policy_id": None,
            "email_notifications": None,
            "rate_limits": None,
            "route_optimized": route_optimized,
        },
        "description": description,
        "traffic": {
            "route_field": "served_entity_name",
            "traffic_percentage": traffic_percentage,
        },
    }
    canonical = json.dumps(contract, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def gateway_runtime_binding_hash(
    *,
    endpoint: str,
    supervisor_id: str,
    upstream_endpoint: str,
    runtime_application_id: str,
    model_name: str,
    model_version: int,
    inference_table: str,
) -> str:
    """Return a non-secret digest for deployed-App/runtime contract parity."""

    canonical = "\0".join(
        [
            endpoint,
            supervisor_id,
            upstream_endpoint,
            runtime_application_id,
            model_name,
            str(model_version),
            inference_table,
        ]
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def gateway_exact_resource_digest(contract: Mapping[str, str]) -> str:
    """Digest a complete, canonical live Gateway resource proof."""

    if any(
        not isinstance(key, str) or not key or not isinstance(value, str) or not value
        for key, value in contract.items()
    ):
        raise ValueError("Gateway exact resource contract is incomplete")
    normalized = dict(contract)
    if normalized.get("proof_version") != GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION:
        raise ValueError("Gateway exact resource contract is incomplete")
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def canonical_gateway_runtime_resource_contract(contract: Mapping[str, str]) -> str:
    """Return exact canonical JSON for the complete live Gateway proof."""

    normalized = dict(contract)
    if set(normalized) != _GATEWAY_RUNTIME_RESOURCE_FIELDS:
        raise ValueError("Gateway runtime resource contract fields are incomplete")
    gateway_exact_resource_digest(normalized)
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"))


def parse_gateway_runtime_resource_contract(value: str) -> dict[str, str]:
    """Parse canonical contract bytes without accepting equivalent encodings."""

    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Gateway runtime resource contract JSON is invalid") from exc
    if not isinstance(decoded, dict) or any(
        not isinstance(key, str) or not isinstance(item, str) for key, item in decoded.items()
    ):
        raise ValueError("Gateway runtime resource contract JSON is invalid")
    contract = dict(decoded)
    if canonical_gateway_runtime_resource_contract(contract) != value:
        raise ValueError("Gateway runtime resource contract JSON is not canonical")
    return contract


def _attestation_key(value: str, *, length: int) -> bytes:
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Gateway runtime resource attestation key is invalid") from exc
    if len(decoded) != length:
        raise RuntimeError("Gateway runtime resource attestation key has an invalid length")
    return decoded


def _resource_attestation_payload(contract_json: str) -> bytes:
    parse_gateway_runtime_resource_contract(contract_json)
    return (
        GATEWAY_RUNTIME_RESOURCE_ATTESTATION_ALG.encode("ascii")
        + b"\0"
        + contract_json.encode("utf-8")
    )


def sign_gateway_runtime_resource_contract(contract: Mapping[str, str]) -> str:
    """Sign an exact live proof only inside the authorized provisioner."""

    if os.environ.get("MIP_ALLOW_RUNTIME_MODEL_ATTESTATION_SIGNING", "").strip() != "1":
        raise RuntimeError("Gateway runtime resource signing is disabled in this process")
    signing_key = os.environ.get("MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY", "").strip()
    verify_key = os.environ.get("MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY", "").strip()
    private = Ed25519PrivateKey.from_private_bytes(_attestation_key(signing_key, length=32))
    derived = (
        base64.urlsafe_b64encode(
            private.public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
        )
        .decode("ascii")
        .rstrip("=")
    )
    if not verify_key or derived != verify_key:
        raise RuntimeError("Gateway runtime resource signing and verification keys do not match")
    contract_json = canonical_gateway_runtime_resource_contract(contract)
    signature = private.sign(_resource_attestation_payload(contract_json))
    return base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")


def verify_gateway_runtime_resource_contract(
    *,
    contract_json: str,
    signature: str,
    current_verify_key: str,
    previous_verify_key: str = "",
) -> dict[str, str]:
    """Authenticate canonical resource facts under current or previous trust."""

    contract = parse_gateway_runtime_resource_contract(contract_json)
    trusted = {current_verify_key.strip(), previous_verify_key.strip()} - {""}
    if not trusted:
        raise RuntimeError("Gateway runtime resource verification key is missing")
    encoded_signature = _attestation_key(signature.strip(), length=64)
    payload = _resource_attestation_payload(contract_json)
    for key in trusted:
        try:
            public = Ed25519PublicKey.from_public_bytes(_attestation_key(key, length=32))
            public.verify(encoded_signature, payload)
            return contract
        except (InvalidSignature, RuntimeError, ValueError):
            continue
    raise RuntimeError("Gateway runtime resource contract signature is invalid")


def gateway_runtime_resource_environment(
    contract: Mapping[str, str],
    *,
    signature: str,
    current_verify_key: str,
    previous_verify_key: str = "",
) -> dict[str, str]:
    """Build the non-secret environment binding consumed by App and proxy."""

    contract_json = canonical_gateway_runtime_resource_contract(contract)
    digest = gateway_exact_resource_digest(contract)
    environment = {
        "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_CONTRACT_JSON": contract_json,
        "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SHA256": digest,
        "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SIGNATURE": signature.strip(),
        "MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY": current_verify_key.strip(),
    }
    if previous_verify_key.strip():
        environment["MIP_GATEWAY_MODEL_ATTESTATION_PREVIOUS_VERIFY_KEY"] = (
            previous_verify_key.strip()
        )
    if (
        not environment["MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SIGNATURE"]
        or not environment["MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY"]
    ):
        raise RuntimeError("Gateway runtime resource environment is incomplete")
    return environment


def verified_gateway_runtime_resource_environment(
    environment: Mapping[str, str],
) -> dict[str, str]:
    """Authenticate and recompute the exact resource digest from an environment."""

    required = GATEWAY_RUNTIME_RESOURCE_ENV - {"MIP_GATEWAY_MODEL_ATTESTATION_PREVIOUS_VERIFY_KEY"}
    if not required.issubset(environment):
        raise RuntimeError("Gateway runtime resource environment is incomplete")
    contract_json = str(
        environment.get("MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_CONTRACT_JSON") or ""
    ).strip()
    contract = verify_gateway_runtime_resource_contract(
        contract_json=contract_json,
        signature=str(
            environment.get("MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SIGNATURE") or ""
        ).strip(),
        current_verify_key=str(
            environment.get("MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY") or ""
        ).strip(),
        previous_verify_key=str(
            environment.get("MIP_GATEWAY_MODEL_ATTESTATION_PREVIOUS_VERIFY_KEY") or ""
        ).strip(),
    )
    expected_digest = str(
        environment.get("MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SHA256") or ""
    ).strip()
    if gateway_exact_resource_digest(contract) != expected_digest:
        raise RuntimeError("Gateway runtime resource contract digest is invalid")
    return contract
