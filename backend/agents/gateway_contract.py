"""Shared immutable contract for the governed Supervisor proxy endpoint."""

from __future__ import annotations

import hashlib
from pathlib import Path

DEFAULT_GATEWAY_AGENT_MODEL = "mip.audit.mortgage_growth_supervisor_proxy"
DEFAULT_GATEWAY_ENDPOINT = "mip-growth-agent-gateway"
DEFAULT_GATEWAY_INFERENCE_TABLE = "mip.audit.mip_agent_gateway_growth_agent"
GATEWAY_PROXY_SOURCE_HASH_TAG = "mip.proxy_source_hash"
GATEWAY_UPSTREAM_TAG = "mip.upstream_supervisor_endpoint"
GATEWAY_MODEL_REQUIREMENTS = ("mlflow==3.14.0", "databricks-sdk==0.103.0")
GATEWAY_DEPLOYMENT_SPEC_VERSION = "gateway-supervisor-proxy-v1"
GATEWAY_PROXY_SOURCE = Path(__file__).with_name("mortgage_growth_supervisor_proxy.py")


def gateway_proxy_source_hash(*, upstream_endpoint: str) -> str:
    """Bind reviewed proxy bytes, runtime pins, and its exact upstream name."""

    deployment_spec = "\0".join(
        [
            GATEWAY_DEPLOYMENT_SPEC_VERSION,
            upstream_endpoint,
            *GATEWAY_MODEL_REQUIREMENTS,
        ]
    ).encode("utf-8")
    return hashlib.sha256(
        GATEWAY_PROXY_SOURCE.read_bytes() + b"\0" + deployment_spec
    ).hexdigest()


def gateway_runtime_binding_hash(
    *,
    endpoint: str,
    supervisor_id: str,
    upstream_endpoint: str,
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
            model_name,
            str(model_version),
            inference_table,
        ]
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
