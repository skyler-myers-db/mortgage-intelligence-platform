"""Deterministic App rollback records of every version, for the v7 contract tests (M2-PR7).

Ed25519 signing is deterministic, so a record built here from the fixed key
and fields always serializes to the same bytes: the golden v5 and v6 digests
in tests/unit/test_app_rollback_record_v7.py were computed from these
builders on the pre-v7 tree. Every value is synthetic.
"""

from __future__ import annotations

import base64
import copy
from types import SimpleNamespace
from typing import Any

from backend.agents.gateway_contract import (
    GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION,
    gateway_exact_resource_digest,
)
from backend.services.ai_gateway_proof_attestation import derive_gateway_proof_verify_key
from tools.databricks.app_rollback_record_builder import build_app_rollback_record
from tools.databricks.gateway_legacy_rollback import (
    LEGACY_GATEWAY_RESOURCE_FIELDS,
    legacy_gateway_resource_digest,
)

APP_NAME = "mip-app"
SCOPE = "mip-app-rollback"
LAKEBASE = "mip-app-state"
GIT_SHA = "a" * 40
LEASE_ID = "11111111-1111-4111-8111-111111111111"
SIGNING_KEY = base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("=")
VERIFY_KEY = derive_gateway_proof_verify_key(SIGNING_KEY)
SNAPSHOT = "/Workspace/Users/app-id/src/deployment-blue"
GENIE_SPACE_ID = "genie-space-id"
APP_RESOURCES: list[dict[str, object]] = [
    {
        "name": "genie_space",
        "genie_space": {
            "name": "mortgage_lead_intelligence",
            "space_id": GENIE_SPACE_ID,
            "permission": "CAN_RUN",
        },
    },
    {"name": "sql_warehouse", "sql_warehouse": {"id": "warehouse-id", "permission": "CAN_USE"}},
]
GATEWAY_RESOURCES = {
    **{field: f"golden-{field}" for field in LEGACY_GATEWAY_RESOURCE_FIELDS},
    "proof_version": GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION,
    "workspace_host": "https://workspace.cloud.databricks.com",
    "catalog": "mip",
    "genie_space_id": GENIE_SPACE_ID,
    "proxy_caller_application_id": "proxy-client",
    "proxy_caller_credential_id": "proxy-credential",
    "proxy_caller_secret_reference": "{{secrets/mip-agent-proxy/oauth-client-secret-proxy-credential}}",
}
LEGACY_RESOURCES = {
    **{field: f"legacy-{field}" for field in LEGACY_GATEWAY_RESOURCE_FIELDS},
    "proof_version": GATEWAY_RUNTIME_RESOURCE_PROOF_VERSION,
    "workspace_host": "https://workspace.cloud.databricks.com",
}
# The live install's env-name shape, names only (cf. test_lender_identity_call_sites.D0_ENV_NAMES).
CORE_ENV = {
    "APP_ENV": "sandbox",
    "MIP_LAKEBASE_INSTANCE": LAKEBASE,
    "LAKEBASE_INSTANCE_NAME": LAKEBASE,
    "MIP_DEFAULT_CATALOG": "mip",
    "MIP_GIT_SHA": GIT_SHA,
}


def signing_env(monkeypatch: Any) -> None:
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", VERIFY_KEY)
    monkeypatch.delenv("MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY", raising=False)
    monkeypatch.delenv("MIP_AI_GATEWAY_PROOF_HISTORICAL_VERIFY_KEYS", raising=False)
    monkeypatch.setenv("MIP_LAKEBASE_INSTANCE", LAKEBASE)
    monkeypatch.setenv("LAKEBASE_INSTANCE_NAME", LAKEBASE)


def payload(source: str, env: dict[str, str] | None = None) -> dict[str, object]:
    values = {**CORE_ENV, "MIP_APP_DEPLOYMENT_LEASE_ID": LEASE_ID, **(env or {})}
    return {
        "source_code_path": source,
        "mode": "SNAPSHOT",
        "env_vars": [
            {"name": "DATABRICKS_WAREHOUSE_ID", "value_from": "sql_warehouse"},
            *({"name": name, "value": value} for name, value in sorted(values.items())),
        ],
    }


def _deployment() -> SimpleNamespace:
    return SimpleNamespace(
        deployment_id="deployment-blue",
        deployment_artifacts=SimpleNamespace(source_code_path=SNAPSHOT),
    )


def v6_record() -> dict[str, Any]:
    """An unsigned v6 record exactly as capture builds one."""

    resources = {
        **GATEWAY_RESOURCES,
        "resource_digest": gateway_exact_resource_digest(GATEWAY_RESOURCES),
    }
    return build_app_rollback_record(
        app_name=APP_NAME,
        deployment=_deployment(),
        payload=payload("/Workspace/Users/deployer/.bundle/mip/client/files"),
        git_sha=GIT_SHA,
        gateway_binding="b" * 64,
        gateway_resources=resources,
        app_resources=copy.deepcopy(APP_RESOURCES),
        app_service_principal_client_id="app-client",
        app_service_principal_scim_id="app-scim-id",
        expected_lakebase_instance=LAKEBASE,
        record_version=6,
    )


def v5_record() -> dict[str, Any]:
    """An unsigned legacy proxyless v5 record."""

    resources = {
        **LEGACY_RESOURCES,
        "resource_digest": legacy_gateway_resource_digest(LEGACY_RESOURCES),
    }
    return build_app_rollback_record(
        app_name=APP_NAME,
        deployment=_deployment(),
        payload=payload("/Workspace/Users/deployer/.bundle/mip/client/files"),
        git_sha=GIT_SHA,
        gateway_binding=None,
        gateway_resources=resources,
        app_resources=copy.deepcopy(APP_RESOURCES),
        app_service_principal_client_id="app-client",
        app_service_principal_scim_id="app-scim-id",
        expected_lakebase_instance=LAKEBASE,
        record_version=5,
    )


class Secrets:
    """Secret values by (scope, key), the SDK's base64 encoding included."""

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}
        self.puts: list[str] = []

    def get_secret(self, scope: str, key: str) -> SimpleNamespace:
        from databricks.sdk.errors import ResourceDoesNotExist

        if (scope, key) not in self.values:
            raise ResourceDoesNotExist("missing")
        return SimpleNamespace(value=base64.b64encode(self.values[(scope, key)].encode()).decode())

    def put_secret(self, *, scope: str, key: str, string_value: str) -> None:
        self.puts.append(key)
        self.values[(scope, key)] = string_value


# --- v7 (M2-PR7) -----------------------------------------------------------------------

DEPLOYER = "00000000-0000-4000-8000-000000000001"
CLIENT_KEY = "client-one"
LEGACY_FOLDER = "/Workspace/Users/owner@lender.example/.bundle/mortgage-intelligence-platform/dev/app-src/0123abcd"
TREE = "7" * 64
ACL = [
    {"principal": "group:admins", "permission": "CAN_MANAGE", "inherited": True},
    {"principal": "service_principal:normal-client", "permission": "CAN_USE", "inherited": False},
]


def adopted_folder(deployment_id: str = "deployment-blue", tree: str = TREE) -> str:
    from tools.databricks import app_release_contract as contract

    name = contract.adopted_folder_name(deployment_id=deployment_id, tree_sha256=tree)
    return contract.adopted_folder_path(
        principal=DEPLOYER, target="client", root_leaf=CLIENT_KEY, folder=name
    )


def core_env_names() -> list[str]:
    return sorted(str(item["name"]) for item in payload("/unused")["env_vars"])  # type: ignore[union-attr]


def v7_core_fields(**overrides: Any) -> dict[str, Any]:
    """The fields build_v7_record takes for an adopted core record (no restore payload yet)."""

    fields: dict[str, Any] = {
        "tier": "core",
        "provenance": "adopted",
        "app_name": APP_NAME,
        "deployment_id": "deployment-blue",
        "git_sha": GIT_SHA,
        "app_service_principal_client_id": "app-client",
        "app_service_principal_scim_id": "app-scim-id",
        "app_resources": copy.deepcopy(APP_RESOURCES),
        "app_acl": copy.deepcopy(ACL),
        "treatment_mode": "quiesce",
        "env_names": core_env_names(),
        "source_code_path": LEGACY_FOLDER,
        "source_tree_sha256": TREE,
        "restore_source_path": adopted_folder(),
        "frontend_provenance": "verified",
        "restore_payload": None,
        "predecessor_deployment_id": None,
        "ack_purpose": "adopt_live",
        "ack_sha256": "1" * 64,
    }
    fields.update(overrides)
    return fields


def v7_core_record(**overrides: Any) -> dict[str, Any]:
    from tools.databricks.app_rollback_record_v7 import build_v7_record

    return build_v7_record(expected_lakebase_instance=LAKEBASE, **v7_core_fields(**overrides))
