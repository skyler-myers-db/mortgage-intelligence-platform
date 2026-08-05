"""Serializable output contract for MIP agentic resource provisioning."""

from __future__ import annotations

import shlex
from dataclasses import dataclass

from backend.agents.reviewed_uc_function_contract import (
    authenticated_reviewed_function_owner,
)


@dataclass(frozen=True)
class SupervisorAgentBinding:
    """Managed Supervisor selected by the immutable provisioning flow."""

    supervisor_id: str
    display_name: str
    endpoint: str
    replaced_supervisor_id: str | None = None
    replaced_supervisor_endpoint: str | None = None
    replaced_supervisor_creator: str | None = None
    replaced_supervisor_create_time: str | None = None


def resolve_reviewed_function_owner(
    workspace: object,
    catalog: str,
    configured_owner: object,
    capture_authenticated_owner: bool,
) -> str:
    owner = str(configured_owner or "").strip()
    if not capture_authenticated_owner:
        return owner
    authenticated_owner = authenticated_reviewed_function_owner(
        workspace,
        catalog=catalog,
    )
    if owner and owner != authenticated_owner:
        raise RuntimeError(
            "configured reviewed-function owner differs from the authenticated deployer"
        )
    return authenticated_owner


@dataclass(frozen=True)
class ProvisionedResources:
    """Exact resource names exported to the subsequently deployed App."""

    lakebase_sync_catalog: str
    lakebase_sync_schema: str
    lakebase_sync_tables: tuple[str, ...]
    agent_supervisor_id: str | None = None
    agent_supervisor_name: str | None = None
    agent_serving_endpoint: str | None = None
    agent_supervisor_endpoint: str | None = None
    agent_supervisor_endpoint_id: str | None = None
    ai_gateway_endpoint: str | None = None
    ai_gateway_inference_table: str | None = None
    ai_gateway_agent_model: str | None = None
    ai_gateway_agent_model_version: int | None = None
    ai_gateway_agent_model_family: str | None = None
    ai_gateway_experiment_base: str | None = None
    ai_gateway_table_prefix: str | None = None
    replaced_supervisor_id: str | None = None
    replaced_supervisor_endpoint: str | None = None
    replaced_supervisor_creator: str | None = None
    replaced_supervisor_create_time: str | None = None
    agent_runtime_application_id: str | None = None
    agent_proxy_application_id: str | None = None
    agent_proxy_credential_id: str | None = None
    agent_proxy_secret_reference: str | None = None
    reviewed_function_owner: str | None = None

    def env_lines(self) -> list[str]:
        """Render shell-safe, sourceable assignments without losing explicit names."""

        def assignment(key: str, value: str) -> str:
            return f"{key}={shlex.quote(value)}"

        rows = [
            assignment("MIP_LAKEBASE_SYNC", "1"),
            assignment("MIP_LAKEBASE_SYNC_CATALOG", self.lakebase_sync_catalog),
            assignment("MIP_LAKEBASE_SYNC_SCHEMA", self.lakebase_sync_schema),
            assignment("MIP_LAKEBASE_SYNC_TABLES", ",".join(self.lakebase_sync_tables)),
        ]
        if self.reviewed_function_owner:
            rows.append(
                assignment(
                    "MIP_REVIEWED_FUNCTION_OWNER",
                    self.reviewed_function_owner,
                )
            )
        if self.agent_supervisor_id and self.agent_serving_endpoint:
            rows.extend(
                [
                    assignment("MIP_AGENT_ORCHESTRATOR", "1"),
                    assignment("MIP_AGENT_SUPERVISOR_ID", self.agent_supervisor_id),
                    assignment(
                        "MIP_AGENT_RUNTIME_CLIENT_ID",
                        self.agent_runtime_application_id or "",
                    ),
                    assignment(
                        "MIP_AGENT_PROXY_CLIENT_ID",
                        self.agent_proxy_application_id or "",
                    ),
                    assignment(
                        "MIP_AGENT_PROXY_CREDENTIAL_ID",
                        self.agent_proxy_credential_id or "",
                    ),
                    assignment(
                        "MIP_AGENT_PROXY_SECRET_REFERENCE",
                        self.agent_proxy_secret_reference or "",
                    ),
                    assignment("MIP_AGENT_SUPERVISOR_NAME", self.agent_supervisor_name or ""),
                    assignment("MIP_AGENT_SERVING_ENDPOINT", self.agent_serving_endpoint),
                    assignment(
                        "MIP_AGENT_SUPERVISOR_ENDPOINT",
                        self.agent_supervisor_endpoint or self.agent_serving_endpoint,
                    ),
                    assignment(
                        "MIP_AGENT_SUPERVISOR_ENDPOINT_ID",
                        self.agent_supervisor_endpoint_id or "",
                    ),
                ]
            )
        if self.replaced_supervisor_id:
            rows.extend(
                [
                    assignment(
                        "MIP_REPLACED_AGENT_SUPERVISOR_ID",
                        self.replaced_supervisor_id,
                    ),
                    assignment(
                        "MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT",
                        self.replaced_supervisor_endpoint or "",
                    ),
                    assignment(
                        "MIP_REPLACED_AGENT_SUPERVISOR_CREATOR",
                        self.replaced_supervisor_creator or "",
                    ),
                    assignment(
                        "MIP_REPLACED_AGENT_SUPERVISOR_CREATE_TIME",
                        self.replaced_supervisor_create_time or "",
                    ),
                ]
            )
        if self.ai_gateway_endpoint and self.ai_gateway_inference_table:
            rows.extend(
                [
                    assignment("MIP_AI_GATEWAY", "1"),
                    assignment("MIP_AI_GATEWAY_ENDPOINT", self.ai_gateway_endpoint),
                    assignment("MIP_AI_GATEWAY_INFERENCE_TABLE", self.ai_gateway_inference_table),
                    assignment("MIP_AI_GATEWAY_AGENT_MODEL", self.ai_gateway_agent_model or ""),
                    assignment(
                        "MIP_AI_GATEWAY_AGENT_MODEL_VERSION",
                        str(self.ai_gateway_agent_model_version or ""),
                    ),
                    assignment(
                        "MIP_AI_GATEWAY_AGENT_MODEL_FAMILY",
                        self.ai_gateway_agent_model_family or "",
                    ),
                    assignment(
                        "MIP_AI_GATEWAY_AGENT_EXPERIMENT_BASE",
                        self.ai_gateway_experiment_base or "",
                    ),
                    assignment(
                        "MIP_AI_GATEWAY_TABLE_PREFIX",
                        self.ai_gateway_table_prefix or "",
                    ),
                ]
            )
        return rows
