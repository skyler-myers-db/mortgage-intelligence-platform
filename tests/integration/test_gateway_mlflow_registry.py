from __future__ import annotations

import base64
from pathlib import Path

import mlflow
import pytest

from backend.agents.gateway_contract import GATEWAY_MODEL_CANONICAL_TAGS
from backend.services.ai_gateway_proof_attestation import derive_gateway_proof_verify_key
from tools.databricks.gateway_model_attestation import (
    sign_gateway_model_contract,
    verify_gateway_model_contract,
)


def test_mlflow_register_model_persists_atomic_gateway_tags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the real MLflow registry API used by provisioning."""

    assert mlflow.__version__ == "3.15.1"
    original_tracking = mlflow.get_tracking_uri()
    original_registry = mlflow.get_registry_uri()
    database_uri = f"sqlite:///{tmp_path / 'mlflow.db'}"
    signing_key = base64.urlsafe_b64encode(b"l" * 32).decode("ascii").rstrip("=")
    monkeypatch.setenv("MIP_ALLOW_RUNTIME_MODEL_ATTESTATION_SIGNING", "1")
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY", signing_key)
    monkeypatch.setenv(
        "MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY",
        derive_gateway_proof_verify_key(signing_key),
    )
    try:
        mlflow.set_tracking_uri(database_uri)
        mlflow.set_registry_uri(database_uri)
        client = mlflow.MlflowClient()
        experiment_id = client.create_experiment("gateway-registry-proof")
        logged = client.create_logged_model(
            experiment_id,
            name="mortgage-growth-supervisor-proxy",
        )
        client.finalize_logged_model(logged.model_id, "READY")
        model_source = f"models:/{logged.model_id}"
        contract = {
            "full_name": "mip.audit.proxy_0123456789ab",
            "model_source": model_source,
            "source_hash": "a" * 64,
            "supervisor_id": "supervisor-123",
            "supervisor_endpoint_id": "supervisor-endpoint-456",
            "upstream_endpoint": "managed-supervisor",
            "runtime_application_id": "runtime-client",
            "model_family": "mip.audit.proxy",
            "experiment_base": "mip-agent-runtime-gateway-proxy",
            "catalog": "mip",
            "genie_space_id": "space-123",
            "inference_schema": "audit",
            "inference_table_prefix": "mip_agent_gateway_growth_agent",
        }
        registration_tags = sign_gateway_model_contract(**contract)

        registered = mlflow.register_model(
            model_source,
            contract["full_name"],
            tags=registration_tags,
            await_registration_for=10,
        )
        exact = client.get_model_version(contract["full_name"], registered.version)

        assert exact.source == model_source
        assert exact.tags == registration_tags
        assert set(exact.tags) == GATEWAY_MODEL_CANONICAL_TAGS
        assert all(len(key) <= 256 and len(value) <= 256 for key, value in exact.tags.items())
        assert verify_gateway_model_contract(tags=exact.tags, **contract)
    finally:
        mlflow.set_tracking_uri(original_tracking)
        mlflow.set_registry_uri(original_registry)
