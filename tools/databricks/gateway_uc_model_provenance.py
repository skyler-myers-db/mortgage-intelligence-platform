"""Validate immutable Gateway model families during the runtime UC audit."""

from __future__ import annotations

import re
from typing import Any

from backend.agents.gateway_contract import gateway_model_version_tags
from tools.databricks.gateway_model_attestation import (
    gateway_model_attestation_record_key,
    gateway_model_contract_from_tags,
    verify_gateway_model_contract,
)
from tools.databricks.mlflow_uc_model_versions import (
    authoritative_model_version,
    model_version_field,
    model_version_tags,
)
from tools.databricks.provision_gateway_responses_agent import gateway_resource_hash

_MODEL_VERSION_SEARCH_PAGE_SIZE = 1000


def _search_model_versions(model_registry: Any, *, full_name: str) -> list[Any]:
    versions: list[Any] = []
    page_token: str | None = None
    seen_tokens: set[str] = set()
    while True:
        page = model_registry.search_model_versions(
            filter_string=f"name='{full_name}'",
            max_results=_MODEL_VERSION_SEARCH_PAGE_SIZE,
            page_token=page_token,
        )
        versions.extend(page)
        next_token = str(getattr(page, "token", "") or "").strip()
        if not next_token:
            return versions
        if next_token in seen_tokens:
            raise RuntimeError("Gateway model-version provenance search repeated a page token")
        seen_tokens.add(next_token)
        page_token = next_token


def _authoritative_version(model_registry: Any, search_result: Any, *, full_name: str) -> Any:
    authoritative = authoritative_model_version(
        model_registry,
        search_result,
        expected_model_name=full_name,
    )
    version = model_version_field(authoritative, "version")
    status = model_version_field(authoritative, "status").upper()
    if status != "READY":
        raise RuntimeError(
            f"Gateway model {full_name} v{version} is not ready ({status or 'MISSING'})"
        )
    return authoritative


def assert_gateway_model_provenance(
    *,
    model_registry: Any,
    full_name: str,
    model_family: str,
    experiment_base: str,
    supervisor_id: str,
    supervisor_endpoint_id: str,
    runtime_application_id: str,
    workspace_host: str,
    catalog: str,
    genie_space_id: str,
    inference_schema: str,
    inference_table_prefix: str,
    candidate_model: str,
    proxy_caller_application_id: str,
    proxy_caller_credential_id: str,
    proxy_caller_secret_reference: str,
) -> None:
    """Require signed source/allocation provenance for every visible family version."""

    versions = _search_model_versions(model_registry, full_name=full_name)
    if not versions:
        raise RuntimeError(f"Gateway model {full_name} has no registered versions")
    for search_result in versions:
        version = _authoritative_version(model_registry, search_result, full_name=full_name)
        tags = model_version_tags(
            version,
            resource=f"Gateway model {full_name} v{version.version}",
        )
        version_number = str(getattr(version, "version", None) or "").strip()
        model_source = str(getattr(version, "source", None) or "").strip()
        resolved_tags = gateway_model_version_tags(tags)
        source_hash = resolved_tags.contract["source_hash"]
        upstream = resolved_tags.contract["upstream_endpoint"]
        attested_contract = gateway_model_contract_from_tags(tags)
        attested_supervisor_id = attested_contract["supervisor_id"]
        attested_supervisor_endpoint_id = attested_contract["supervisor_endpoint_id"]
        attested_runtime_application_id = attested_contract["runtime_application_id"]
        if (
            not version_number
            or not model_source
            or not re.fullmatch(r"[0-9a-f]{64}", source_hash)
            or not upstream
        ):
            raise RuntimeError(f"Gateway model {full_name} has an incomplete version contract")
        contract_hash = gateway_resource_hash(
            source_hash=source_hash,
            supervisor_id=attested_supervisor_id,
            supervisor_endpoint_id=attested_supervisor_endpoint_id,
            runtime_application_id=attested_runtime_application_id,
            workspace_host=workspace_host,
            model_name=model_family,
            experiment_name=experiment_base,
            inference_schema=inference_schema,
            inference_table_prefix=inference_table_prefix,
            attestation_verify_key=gateway_model_attestation_record_key(tags),
            proxy_caller_application_id=proxy_caller_application_id,
            proxy_caller_credential_id=proxy_caller_credential_id,
            proxy_caller_secret_reference=proxy_caller_secret_reference,
        )
        if full_name.rsplit("_", 1)[-1] != contract_hash[:12]:
            raise RuntimeError(f"Gateway model {full_name} lacks source-bound contract provenance")
        contract = {
            "full_name": full_name,
            "model_source": model_source,
            "source_hash": source_hash,
            "supervisor_id": attested_supervisor_id,
            "supervisor_endpoint_id": attested_supervisor_endpoint_id,
            "upstream_endpoint": upstream,
            "runtime_application_id": attested_runtime_application_id,
            "model_family": model_family,
            "experiment_base": experiment_base,
            "catalog": catalog,
            "genie_space_id": genie_space_id,
            "inference_schema": inference_schema,
            "inference_table_prefix": inference_table_prefix,
        }
        current = verify_gateway_model_contract(tags=tags, **contract)
        candidate = f"Gateway candidate model {full_name} v{version_number}"
        identity_changed = (
            attested_supervisor_id != supervisor_id
            or attested_supervisor_endpoint_id != supervisor_endpoint_id
            or attested_runtime_application_id != runtime_application_id
        )
        if full_name == candidate_model and identity_changed:
            raise RuntimeError(f"{candidate} uses a different immutable runtime identity")
        if full_name == candidate_model and not current:
            raise RuntimeError(f"{candidate} uses a previous attestation epoch")
