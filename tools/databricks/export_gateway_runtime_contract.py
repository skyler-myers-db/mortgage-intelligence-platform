#!/usr/bin/env python3
"""Resolve and export the exact source-bound Gateway/Supervisor contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any
from urllib.parse import quote

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from mlflow import MlflowClient  # noqa: E402

from backend.agents.gateway_contract import (  # noqa: E402
    DEFAULT_GATEWAY_AGENT_EXPERIMENT,
    DEFAULT_GATEWAY_ENDPOINT,
    GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION,
    gateway_exact_resource_digest,
    gateway_inference_table_family,
    gateway_model_family,
    gateway_proxy_source_hash,
    gateway_runtime_binding_hash,
    reviewed_workspace_https_origin,
)
from backend.agents.reviewed_uc_function_contract import (  # noqa: E402
    assert_reviewed_function_set,
)
from databricks.sdk import WorkspaceClient  # noqa: E402
from tools.databricks.agent_runtime_access import assert_runtime_creator  # noqa: E402
from tools.databricks.agentic_env_file import merge_agentic_env_values  # noqa: E402
from tools.databricks.experiment_acl_contract import (  # noqa: E402
    resolve_exact_experiment_acl,
)
from tools.databricks.gateway_model_attestation import (  # noqa: E402
    gateway_model_attestation_record_key,
)
from tools.databricks.gateway_runtime_resource_binding import (  # noqa: E402
    assert_gateway_runtime_resource_binding,
    gateway_runtime_resource_binding_environment,
)
from tools.databricks.provision_agentic_resources import (  # noqa: E402
    assert_exact_supervisor_contract,
)
from tools.databricks.provision_gateway_responses_agent import (  # noqa: E402
    GatewayAgentDeployment,
    gateway_agent_model_name,
    gateway_endpoint_configuration_matches,
    gateway_experiment_name,
    gateway_inference_table_prefix,
    gateway_resource_hash,
    verify_gateway_responses_agent,
)
from tools.databricks.supervisor_agent_contract import (  # noqa: E402
    canonical_supervisor_contract_json,
    supervisor_contract_hash,
)


@dataclass(frozen=True)
class ExactGatewayRuntimeProof:
    """Canonical live resource facts suitable for durable rollback signing."""

    contract: Mapping[str, str]
    digest: str


def _supervisors(client: Any) -> list[Mapping[str, Any]]:
    rows: list[Mapping[str, Any]] = []
    ids: set[str] = set()
    page_token: str | None = None
    seen_tokens: set[str] = set()
    while True:
        query: dict[str, Any] = {"page_size": 100}
        if page_token:
            query["page_token"] = page_token
        response = client.api_client.do(
            "GET",
            "/api/2.1/supervisor-agents",
            query=query,
        )
        if not isinstance(response, Mapping):
            raise RuntimeError("Supervisor inventory is malformed")
        page = response.get("supervisor_agents", [])
        if not isinstance(page, list):
            raise RuntimeError("Supervisor inventory is malformed")
        for row in page:
            if not isinstance(row, Mapping):
                raise RuntimeError("Supervisor inventory is malformed")
            supervisor_id = str(row.get("supervisor_agent_id") or "").strip()
            if not supervisor_id or supervisor_id in ids:
                raise RuntimeError("Supervisor inventory has a duplicate or missing identity")
            ids.add(supervisor_id)
            rows.append(row)
        raw_next = response.get("next_page_token")
        if raw_next is None or raw_next == "":
            return rows
        if not isinstance(raw_next, str) or not raw_next.strip():
            raise RuntimeError("Supervisor inventory page token is malformed")
        page_token = raw_next.strip()
        if page_token in seen_tokens:
            raise RuntimeError("Supervisor inventory pagination cycled")
        seen_tokens.add(page_token)


def _supervisor_by_id(client: Any, supervisor_id: str) -> Mapping[str, Any]:
    response = client.api_client.do(
        "GET",
        f"/api/2.1/supervisor-agents/{quote(supervisor_id, safe='')}",
    )
    if not isinstance(response, Mapping):
        raise RuntimeError("Supervisor metadata is malformed")
    return response


def _enum_text(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip()


def resolve_exact_resource_proof(
    client: Any,
    *,
    supervisor_name: str,
    catalog: str,
    genie_space_id: str,
    runtime_application_id: str,
    reviewed_function_owner: str | None = None,
    proxy_caller_application_id: str | None = None,
    proxy_caller_credential_id: str | None = None,
    proxy_caller_secret_reference: str | None = None,
    supervisor_id: str | None = None,
    gateway_endpoint: str | None = None,
    gateway_model_family_name: str | None = None,
    gateway_experiment_base_name: str | None = None,
    gateway_table_prefix: str | None = None,
    expected: Mapping[str, str] | None = None,
    model_registry: Any | None = None,
    tracking_client: Any | None = None,
    require_resource_binding: bool = False,
    allow_legacy_reviewed_function_contract: bool = False,
) -> ExactGatewayRuntimeProof:
    """Re-read and authenticate every live fact needed for a safe rollback."""

    try:
        workspace_host = reviewed_workspace_https_origin(
            str(getattr(getattr(client, "config", None), "host", "") or "")
        )
    except ValueError as exc:
        raise RuntimeError("authenticated Gateway workspace host is invalid") from exc
    registry = model_registry or MlflowClient(
        tracking_uri="databricks",
        registry_uri="databricks-uc",
    )
    experiments = tracking_client or MlflowClient(tracking_uri="databricks")
    if type(allow_legacy_reviewed_function_contract) is not bool or (
        allow_legacy_reviewed_function_contract and expected is None
    ):
        raise RuntimeError("legacy reviewed-function compatibility scope is invalid")
    stored: dict[str, str] | None = None
    if expected is not None:
        if any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in expected.items()
        ):
            raise RuntimeError("stored Gateway rollback contract is invalid")
        stored = dict(expected)
        stored_digest = stored.pop("resource_digest", "")
        try:
            valid_stored_digest = gateway_exact_resource_digest(stored)
        except ValueError as exc:
            raise RuntimeError("stored Gateway rollback contract is invalid") from exc
        if not stored_digest or stored_digest != valid_stored_digest:
            raise RuntimeError("stored Gateway rollback contract digest is invalid")
        required = {
            "catalog",
            "gateway_endpoint",
            "gateway_experiment_base",
            "gateway_experiment_acl_json",
            "gateway_experiment_acl_sha256",
            "gateway_experiment_id",
            "gateway_experiment_name",
            "gateway_inference_table",
            "gateway_inference_table_family",
            "gateway_model_family",
            "gateway_model_name",
            "gateway_model_source",
            "gateway_model_version",
            "gateway_resource_hash",
            "gateway_source_hash",
            "genie_space_id",
            "runtime_application_id",
            "workspace_host",
            "proxy_caller_application_id",
            "proxy_caller_credential_id",
            "proxy_caller_secret_reference",
            "supervisor_display_name",
            "supervisor_canonical_name",
            "supervisor_contract_json",
            "supervisor_contract_sha256",
            "supervisor_endpoint",
            "supervisor_endpoint_id",
            "supervisor_id",
        }
        if not required.issubset(stored):
            raise RuntimeError("stored Gateway rollback contract is incomplete")
        if not stored.get("gateway_model_version", "").isdigit():
            raise RuntimeError("stored Gateway rollback model version is invalid")
        if (
            hashlib.sha256(stored["gateway_experiment_acl_json"].encode("utf-8")).hexdigest()
            != stored["gateway_experiment_acl_sha256"]
        ):
            raise RuntimeError("stored Gateway experiment ACL digest is invalid")
        requested = {
            "catalog": catalog,
            "genie_space_id": genie_space_id,
            "runtime_application_id": runtime_application_id,
            "workspace_host": workspace_host,
            "supervisor_canonical_name": supervisor_name,
            **(
                {"proxy_caller_application_id": proxy_caller_application_id}
                if proxy_caller_application_id
                else {}
            ),
            **(
                {"proxy_caller_credential_id": proxy_caller_credential_id}
                if proxy_caller_credential_id
                else {}
            ),
            **(
                {"proxy_caller_secret_reference": proxy_caller_secret_reference}
                if proxy_caller_secret_reference
                else {}
            ),
        }
        if any(stored.get(key) != value for key, value in requested.items()):
            raise RuntimeError("stored Gateway rollback contract scope drifted")
        if gateway_endpoint and gateway_endpoint != stored.get("gateway_endpoint"):
            raise RuntimeError("stored Gateway rollback endpoint does not match the request")
    resolved_reviewed_function_owner = str(reviewed_function_owner or "")
    if (
        not resolved_reviewed_function_owner
        or resolved_reviewed_function_owner
        != resolved_reviewed_function_owner.strip()
        or (
            reviewed_function_owner is not None
            and reviewed_function_owner != resolved_reviewed_function_owner
        )
    ):
        raise RuntimeError("reviewed-function owner binding is invalid")
    immutable_supervisor_id = (
        stored["supervisor_id"] if stored is not None else supervisor_id
    )
    if immutable_supervisor_id:
        direct = _supervisor_by_id(client, immutable_supervisor_id)
        matches = (
            [direct]
            if str(direct.get("supervisor_agent_id") or "").strip()
            == immutable_supervisor_id
            else []
        )
    else:
        supervisors = _supervisors(client)
        matches = [
            row
            for row in supervisors
            if str(row.get("display_name") or "").strip() == supervisor_name
        ]
    if len(matches) != 1:
        raise RuntimeError(
            f"expected exactly one Supervisor named {supervisor_name!r}, found {len(matches)}"
        )
    supervisor_id = str(matches[0].get("supervisor_agent_id") or "").strip()
    upstream = str(matches[0].get("endpoint_name") or "").strip()
    if not supervisor_id or not upstream:
        raise RuntimeError("managed Supervisor identity or endpoint is missing")
    live_supervisor_display_name = str(matches[0].get("display_name") or "").strip()
    if stored is not None and (
        live_supervisor_display_name
        not in {
            stored["supervisor_display_name"],
            stored["supervisor_canonical_name"],
        }
        or upstream != stored["supervisor_endpoint"]
    ):
        raise RuntimeError("stored Supervisor immutable identity drifted")
    assert_runtime_creator(
        matches[0].get("creator"),
        application_id=runtime_application_id,
        resource="managed Supervisor agent",
    )
    if stored is None:
        supervisor_contract_json = canonical_supervisor_contract_json(
            genie_space_id=genie_space_id,
            catalog=catalog,
        )
        supervisor_contract_sha256 = supervisor_contract_hash(
            genie_space_id=genie_space_id,
            catalog=catalog,
        )
        expected_supervisor_contract = None
    else:
        supervisor_contract_json = stored["supervisor_contract_json"]
        supervisor_contract_sha256 = stored["supervisor_contract_sha256"]
        if (
            hashlib.sha256(supervisor_contract_json.encode("utf-8")).hexdigest()
            != supervisor_contract_sha256
        ):
            raise RuntimeError("stored Supervisor contract digest is invalid")
        try:
            decoded_supervisor_contract = json.loads(supervisor_contract_json)
        except json.JSONDecodeError as exc:
            raise RuntimeError("stored Supervisor contract is invalid") from exc
        if not isinstance(decoded_supervisor_contract, dict):
            raise RuntimeError("stored Supervisor contract is invalid")
        expected_supervisor_contract = decoded_supervisor_contract
    assert_exact_supervisor_contract(
        supervisor_id,
        genie_space_id=genie_space_id,
        catalog=catalog,
        expected_contract=expected_supervisor_contract,
    )
    assert_reviewed_function_set(
        client,
        catalog=catalog,
        expected_owner=resolved_reviewed_function_owner,
        allow_legacy_segment_determinism=allow_legacy_reviewed_function_contract,
    )
    upstream_details = client.serving_endpoints.get(upstream)
    assert_runtime_creator(
        getattr(upstream_details, "creator", None),
        application_id=runtime_application_id,
        resource="managed Supervisor endpoint",
    )
    upstream_endpoint_id = str(getattr(upstream_details, "id", "") or "").strip()
    if not upstream_endpoint_id:
        raise RuntimeError("managed Supervisor endpoint has no immutable ID")
    resolved_proxy_application_id = (
        stored["proxy_caller_application_id"]
        if stored is not None
        else str(proxy_caller_application_id or "").strip()
    )
    resolved_proxy_credential_id = (
        stored["proxy_caller_credential_id"]
        if stored is not None
        else str(proxy_caller_credential_id or "").strip()
    )
    resolved_proxy_secret_reference = (
        stored["proxy_caller_secret_reference"]
        if stored is not None
        else str(proxy_caller_secret_reference or "").strip()
    )
    if not all(
        (
            resolved_proxy_application_id,
            resolved_proxy_credential_id,
            resolved_proxy_secret_reference,
        )
    ):
        raise RuntimeError("Gateway Supervisor proxy credential binding is required")

    source_hash = (
        stored["gateway_source_hash"]
        if stored is not None
        else gateway_proxy_source_hash(
            upstream_endpoint=upstream,
            catalog=catalog,
            genie_space_id=genie_space_id,
        )
    )
    model_family = (
        stored["gateway_model_family"]
        if stored is not None
        else gateway_model_family_name or gateway_model_family(catalog=catalog)
    )
    inference_family = (
        stored["gateway_inference_table_family"]
        if stored is not None
        else gateway_inference_table_family(catalog=catalog)
    )
    inference_parts = inference_family.split(".", 2)
    if len(inference_parts) != 3:
        raise RuntimeError("Gateway inference-table family is invalid")
    _inference_catalog, inference_schema, inference_table_prefix = inference_parts
    if stored is None and gateway_table_prefix:
        inference_table_prefix = gateway_table_prefix
        inference_family = ".".join([_inference_catalog, inference_schema, inference_table_prefix])
    if _inference_catalog != catalog or not model_family.startswith(f"{catalog}."):
        raise RuntimeError("Gateway model/table families are outside the target catalog")
    experiment_base = (
        stored["gateway_experiment_base"]
        if stored is not None
        else gateway_experiment_base_name or DEFAULT_GATEWAY_AGENT_EXPERIMENT
    )
    resource_hash = (
        stored["gateway_resource_hash"]
        if stored is not None
        else gateway_resource_hash(
            source_hash=source_hash,
            supervisor_id=supervisor_id,
            supervisor_endpoint_id=upstream_endpoint_id,
            runtime_application_id=runtime_application_id,
            workspace_host=workspace_host,
            model_name=model_family,
            experiment_name=experiment_base,
            inference_schema=inference_schema,
            inference_table_prefix=inference_table_prefix,
            attestation_verify_key=os.environ.get("MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY", ""),
            proxy_caller_application_id=resolved_proxy_application_id,
            proxy_caller_credential_id=resolved_proxy_credential_id,
            proxy_caller_secret_reference=resolved_proxy_secret_reference,
        )
    )
    expected_model_name = (
        stored["gateway_model_name"]
        if stored is not None
        else gateway_agent_model_name(
            base_model_name=model_family,
            contract_hash=resource_hash,
        )
    )
    expected_experiment_name = (
        stored["gateway_experiment_name"]
        if stored is not None
        else gateway_experiment_name(
            base_experiment_name=experiment_base,
            contract_hash=resource_hash,
            runtime_application_id=runtime_application_id,
        )
    )
    expected_inference_table = (
        stored["gateway_inference_table"]
        if stored is not None
        else ".".join(
            [
                catalog,
                inference_schema,
                gateway_inference_table_prefix(
                    base_prefix=inference_table_prefix,
                    contract_hash=resource_hash,
                ),
            ]
        )
    )
    candidate_names: list[str]
    if stored is not None:
        candidate_names = [stored["gateway_endpoint"]]
    elif gateway_endpoint:
        candidate_names = [gateway_endpoint]
    else:
        candidate_names = sorted(
            {
                str(
                    (item.get("name") if isinstance(item, Mapping) else getattr(item, "name", ""))
                    or ""
                )
                for item in client.serving_endpoints.list()
                if str(
                    (item.get("name") if isinstance(item, Mapping) else getattr(item, "name", ""))
                    or ""
                )
                == DEFAULT_GATEWAY_ENDPOINT
                or str(
                    (item.get("name") if isinstance(item, Mapping) else getattr(item, "name", ""))
                    or ""
                ).startswith(f"{DEFAULT_GATEWAY_ENDPOINT}-")
            }
            - {""}
        )
    exact: list[GatewayAgentDeployment] = []
    failures: list[RuntimeError] = []
    for candidate_name in candidate_names:
        details = client.serving_endpoints.get(candidate_name)
        entities = getattr(getattr(details, "config", None), "served_entities", None) or []
        if len(entities) != 1:
            continue
        entity = entities[0]
        if str(getattr(entity, "entity_name", "") or "") != expected_model_name:
            continue
        try:
            model_version = int(str(getattr(entity, "entity_version", "") or ""))
        except ValueError:
            continue
        if stored is not None and model_version != int(stored["gateway_model_version"]):
            continue
        experiment_id = (
            stored["gateway_experiment_id"]
            if stored is not None
            else str(
                (getattr(entity, "environment_vars", None) or {}).get("MLFLOW_EXPERIMENT_ID") or ""
            ).strip()
        )
        if not experiment_id:
            continue
        try:
            version_details = registry.get_model_version(
                expected_model_name,
                str(model_version),
            )
        except Exception as exc:  # noqa: BLE001 - candidate cannot prove its model
            failures.append(RuntimeError("could not read candidate Gateway model version"))
            failures[-1].__cause__ = exc
            continue
        live_model_source = str(getattr(version_details, "source", "") or "").strip()
        version_tags = {
            str(key): str(value)
            for key, value in dict(getattr(version_details, "tags", None) or {}).items()
        }
        model_attestation_verify_key = gateway_model_attestation_record_key(version_tags)
        model_source = stored["gateway_model_source"] if stored is not None else live_model_source
        if not model_source:
            failures.append(RuntimeError("candidate Gateway model version has no source"))
            continue
        candidate = GatewayAgentDeployment(
            endpoint=candidate_name,
            supervisor_id=supervisor_id,
            supervisor_endpoint_id=upstream_endpoint_id,
            upstream_endpoint=upstream,
            runtime_application_id=runtime_application_id,
            workspace_host=workspace_host,
            proxy_caller_application_id=resolved_proxy_application_id,
            proxy_caller_credential_id=resolved_proxy_credential_id,
            proxy_caller_secret_reference=resolved_proxy_secret_reference,
            model_name=expected_model_name,
            model_version=model_version,
            model_source=model_source,
            model_attestation_verify_key=model_attestation_verify_key,
            model_family=model_family,
            source_hash=source_hash,
            resource_hash=resource_hash,
            inference_table=expected_inference_table,
            inference_table_prefix=inference_table_prefix,
            experiment_base=experiment_base,
            experiment_name=expected_experiment_name,
            experiment_id=experiment_id,
            catalog=catalog,
            genie_space_id=genie_space_id,
        )
        if getattr(details, "pending_config", None) is not None:
            failures.append(RuntimeError("Gateway endpoint has a pending config update"))
            continue
        # A retained human-owned canonical endpoint is irrelevant when its
        # model/config does not match the expected green contract.  Filter it
        # before applying the runtime-ownership gate so a valid versioned green
        # candidate can still be discovered.
        if not gateway_endpoint_configuration_matches(details, candidate):
            continue
        try:
            verify_gateway_responses_agent(
                client,
                candidate,
                model_registry=registry,
                tracking_client=experiments,
            )
        except RuntimeError as exc:
            failures.append(exc)
            continue
        exact.append(candidate)
    if len(exact) != 1:
        if stored is not None:
            if failures:
                raise RuntimeError(
                    "live Gateway resource proof does not match the stored rollback contract"
                ) from failures[0]
            raise RuntimeError(
                "live Gateway resource proof does not match the stored rollback contract"
            )
        if not exact and len(candidate_names) == 1 and failures:
            raise failures[0]
        raise RuntimeError(
            "expected exactly one source-bound runtime Gateway endpoint, " f"found {len(exact)}"
        )
    deployment = exact[0]
    gateway_details = client.serving_endpoints.get(deployment.endpoint)
    gateway_endpoint_id = str(getattr(gateway_details, "id", "") or "").strip()
    if not gateway_endpoint_id:
        raise RuntimeError("Gateway endpoint has no immutable ID")
    model_details = client.registered_models.get(deployment.model_name)
    model_owner = str(getattr(model_details, "owner", "") or "").strip()
    experiment = experiments.get_experiment(deployment.experiment_id)
    experiment_owner = str(
        (getattr(experiment, "tags", None) or {}).get("mlflow.ownerEmail") or ""
    ).strip()
    experiment_acl = resolve_exact_experiment_acl(
        client,
        experiment_id=deployment.experiment_id,
        runtime_application_id=runtime_application_id,
    )
    proof_contract = {
        "proof_version": GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION,
        "catalog": catalog,
        "genie_space_id": genie_space_id,
        "runtime_application_id": runtime_application_id,
        "workspace_host": workspace_host,
        "supervisor_canonical_name": (
            stored["supervisor_canonical_name"] if stored else supervisor_name
        ),
        "supervisor_display_name": (
            stored["supervisor_display_name"] if stored else live_supervisor_display_name
        ),
        "supervisor_contract_json": supervisor_contract_json,
        "supervisor_contract_sha256": supervisor_contract_sha256,
        "supervisor_id": supervisor_id,
        "supervisor_creator": str(matches[0].get("creator") or "").strip(),
        "supervisor_endpoint": deployment.upstream_endpoint,
        "supervisor_endpoint_id": upstream_endpoint_id,
        "supervisor_endpoint_creator": str(getattr(upstream_details, "creator", "") or "").strip(),
        "gateway_endpoint": deployment.endpoint,
        "gateway_endpoint_id": gateway_endpoint_id,
        "gateway_endpoint_creator": str(getattr(gateway_details, "creator", "") or "").strip(),
        "gateway_endpoint_description": str(getattr(gateway_details, "description", "") or ""),
        "gateway_endpoint_task": _enum_text(getattr(gateway_details, "task", None)),
        "gateway_endpoint_route_optimized": str(
            bool(getattr(gateway_details, "route_optimized", None))
        ).lower(),
        "gateway_endpoint_budget_policy": "none",
        "gateway_endpoint_email_notifications": "none",
        "gateway_endpoint_deprecated_rate_limits": "[]",
        "gateway_source_hash": deployment.source_hash,
        "gateway_resource_hash": deployment.resource_hash,
        "gateway_model_family": deployment.model_family,
        "gateway_model_name": deployment.model_name,
        "gateway_model_version": str(deployment.model_version),
        "gateway_model_source": deployment.model_source,
        "gateway_model_owner": model_owner,
        "gateway_experiment_base": deployment.experiment_base,
        "gateway_experiment_acl_json": experiment_acl.canonical_json,
        "gateway_experiment_acl_sha256": experiment_acl.sha256,
        "gateway_experiment_name": deployment.experiment_name,
        "gateway_experiment_id": deployment.experiment_id,
        "gateway_experiment_owner": experiment_owner,
        "gateway_inference_table_family": inference_family,
        "gateway_inference_table": deployment.inference_table,
        "proxy_caller_application_id": deployment.proxy_caller_application_id,
        "proxy_caller_credential_id": deployment.proxy_caller_credential_id,
        "proxy_caller_secret_reference": deployment.proxy_caller_secret_reference,
    }
    digest = gateway_exact_resource_digest(proof_contract)
    if require_resource_binding:
        assert_gateway_runtime_resource_binding(
            gateway_details,
            contract=proof_contract,
        )
    if expected is not None:
        expected_fields = {str(key): str(value) for key, value in expected.items()}
        expected_digest = expected_fields.pop("resource_digest", None)
        unknown = sorted(set(expected_fields) - set(proof_contract))
        drifted = sorted(
            key for key, value in expected_fields.items() if proof_contract.get(key) != value
        )
        if unknown or drifted or (expected_digest is not None and expected_digest != digest):
            raise RuntimeError(
                "live Gateway resource proof does not match the stored rollback contract"
            )
    return ExactGatewayRuntimeProof(
        contract=MappingProxyType(proof_contract),
        digest=digest,
    )


def resolve_contract(
    client: Any,
    *,
    supervisor_name: str,
    catalog: str,
    genie_space_id: str,
    runtime_application_id: str,
    reviewed_function_owner: str,
    proxy_caller_application_id: str,
    proxy_caller_credential_id: str,
    proxy_caller_secret_reference: str,
    supervisor_id: str | None = None,
    gateway_endpoint: str | None = None,
    gateway_model_family_name: str | None = None,
    gateway_experiment_base_name: str | None = None,
    gateway_table_prefix: str | None = None,
    model_registry: Any | None = None,
    tracking_client: Any | None = None,
) -> dict[str, str]:
    """Export App runtime variables from the authenticated live resource proof."""

    proof = resolve_exact_resource_proof(
        client,
        supervisor_name=supervisor_name,
        catalog=catalog,
        genie_space_id=genie_space_id,
        runtime_application_id=runtime_application_id,
        reviewed_function_owner=reviewed_function_owner,
        proxy_caller_application_id=proxy_caller_application_id,
        proxy_caller_credential_id=proxy_caller_credential_id,
        proxy_caller_secret_reference=proxy_caller_secret_reference,
        supervisor_id=supervisor_id,
        gateway_endpoint=gateway_endpoint,
        gateway_model_family_name=gateway_model_family_name,
        gateway_experiment_base_name=gateway_experiment_base_name,
        gateway_table_prefix=gateway_table_prefix,
        model_registry=model_registry,
        tracking_client=tracking_client,
        require_resource_binding=True,
    )
    facts = proof.contract
    binding_hash = gateway_runtime_binding_hash(
        endpoint=facts["gateway_endpoint"],
        supervisor_id=facts["supervisor_id"],
        upstream_endpoint=facts["supervisor_endpoint"],
        runtime_application_id=runtime_application_id,
        workspace_host=facts["workspace_host"],
        model_name=facts["gateway_model_name"],
        model_version=int(facts["gateway_model_version"]),
        inference_table=facts["gateway_inference_table"],
        proxy_caller_application_id=facts["proxy_caller_application_id"],
        proxy_caller_credential_id=facts["proxy_caller_credential_id"],
        proxy_caller_secret_reference=facts["proxy_caller_secret_reference"],
    )
    binding_environment = gateway_runtime_resource_binding_environment(
        client.serving_endpoints.get(facts["gateway_endpoint"])
    )
    return {
        "MIP_AGENT_SERVING_ENDPOINT": facts["gateway_endpoint"],
        "MIP_AGENT_SUPERVISOR_ENDPOINT": facts["supervisor_endpoint"],
        "MIP_AGENT_SUPERVISOR_ENDPOINT_ID": facts["supervisor_endpoint_id"],
        "MIP_AGENT_SUPERVISOR_ID": facts["supervisor_id"],
        "MIP_AGENT_RUNTIME_CLIENT_ID": runtime_application_id,
        "MIP_REVIEWED_FUNCTION_OWNER": reviewed_function_owner,
        "MIP_AGENT_PROXY_CLIENT_ID": facts["proxy_caller_application_id"],
        "MIP_AGENT_PROXY_CREDENTIAL_ID": facts["proxy_caller_credential_id"],
        "MIP_AGENT_PROXY_SECRET_REFERENCE": facts["proxy_caller_secret_reference"],
        "MIP_AI_GATEWAY_ENDPOINT": facts["gateway_endpoint"],
        "MIP_AI_GATEWAY_INFERENCE_TABLE": facts["gateway_inference_table"],
        "MIP_AI_GATEWAY_AGENT_MODEL": facts["gateway_model_name"],
        "MIP_AI_GATEWAY_AGENT_MODEL_VERSION": facts["gateway_model_version"],
        "MIP_AI_GATEWAY_AGENT_MODEL_SOURCE": facts["gateway_model_source"],
        "MIP_AI_GATEWAY_EXPERIMENT_NAME": facts["gateway_experiment_name"],
        "MIP_AI_GATEWAY_EXPERIMENT_ID": facts["gateway_experiment_id"],
        "MIP_EXPECTED_AGENT_GATEWAY_BINDING_SHA256": binding_hash,
        "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_SHA256": proof.digest,
        **binding_environment,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    output = parser.add_mutually_exclusive_group(required=True)
    output.add_argument(
        "--github-env",
        type=Path,
        help="Append raw GitHub Actions environment-file rows.",
    )
    output.add_argument(
        "--shell-env",
        type=Path,
        help="Strictly merge POSIX-shell-quoted rows safe to source from deploy.sh.",
    )
    parser.add_argument("--supervisor-name", default="Mortgage Growth Agent")
    parser.add_argument("--supervisor-id")
    parser.add_argument("--gateway-endpoint", default=os.environ.get("MIP_AI_GATEWAY_ENDPOINT"))
    parser.add_argument(
        "--gateway-model-family",
        default=os.environ.get("MIP_AI_GATEWAY_AGENT_MODEL_FAMILY"),
    )
    parser.add_argument(
        "--gateway-experiment-base",
        default=os.environ.get("MIP_AI_GATEWAY_AGENT_EXPERIMENT_BASE"),
    )
    parser.add_argument(
        "--gateway-table-prefix",
        default=os.environ.get("MIP_AI_GATEWAY_TABLE_PREFIX"),
    )
    parser.add_argument("--catalog", default=os.environ.get("MIP_DEFAULT_CATALOG", "mip"))
    parser.add_argument("--genie-space-id", default=os.environ.get("GENIE_SPACE_ID", ""))
    parser.add_argument(
        "--runtime-application-id",
        default=os.environ.get("DATABRICKS_AGENT_RUNTIME_CLIENT_ID", ""),
    )
    parser.add_argument(
        "--reviewed-function-owner",
        default=os.environ.get("MIP_REVIEWED_FUNCTION_OWNER", ""),
    )
    parser.add_argument(
        "--proxy-caller-application-id",
        default=os.environ.get("DATABRICKS_AGENT_PROXY_CLIENT_ID", ""),
    )
    parser.add_argument(
        "--proxy-caller-credential-id",
        default=os.environ.get("DATABRICKS_AGENT_PROXY_CREDENTIAL_ID", ""),
    )
    parser.add_argument(
        "--proxy-caller-secret-reference",
        default=os.environ.get("MIP_AGENT_PROXY_SECRET_REFERENCE", ""),
    )
    args = parser.parse_args(argv)
    if not args.genie_space_id:
        parser.error("--genie-space-id or GENIE_SPACE_ID is required")
    if not args.runtime_application_id:
        parser.error("--runtime-application-id or DATABRICKS_AGENT_RUNTIME_CLIENT_ID is required")
    if not args.reviewed_function_owner:
        parser.error(
            "--reviewed-function-owner or MIP_REVIEWED_FUNCTION_OWNER is required"
        )
    if not (
        args.proxy_caller_application_id
        and args.proxy_caller_credential_id
        and args.proxy_caller_secret_reference
    ):
        parser.error("complete Supervisor proxy caller binding is required")
    contract = resolve_contract(
        WorkspaceClient(),
        supervisor_name=args.supervisor_name,
        catalog=args.catalog,
        genie_space_id=args.genie_space_id,
        runtime_application_id=args.runtime_application_id,
        reviewed_function_owner=args.reviewed_function_owner,
        proxy_caller_application_id=args.proxy_caller_application_id,
        proxy_caller_credential_id=args.proxy_caller_credential_id,
        proxy_caller_secret_reference=args.proxy_caller_secret_reference,
        supervisor_id=args.supervisor_id,
        gateway_endpoint=args.gateway_endpoint,
        gateway_model_family_name=args.gateway_model_family,
        gateway_experiment_base_name=args.gateway_experiment_base,
        gateway_table_prefix=args.gateway_table_prefix,
        model_registry=MlflowClient(
            tracking_uri="databricks",
            registry_uri="databricks-uc",
        ),
        tracking_client=MlflowClient(tracking_uri="databricks"),
    )
    output_path = args.github_env or args.shell_env
    assert output_path is not None
    if args.github_env is not None:
        with output_path.open("a", encoding="utf-8") as handle:
            for key, value in contract.items():
                handle.write(f"{key}={value}\n")
    else:
        if not output_path.exists():
            output_path.touch()
        merge_agentic_env_values(output_path, contract)
    print(
        "[gateway-contract] exported source-bound endpoint, model version, "
        "Supervisor, inference table, and binding digest"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
