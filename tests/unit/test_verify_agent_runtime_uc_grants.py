from __future__ import annotations

import base64
import copy
from enum import Enum
from types import SimpleNamespace
from typing import Any

import pytest
from databricks.sdk.errors import PermissionDenied
from mlflow.entities.model_registry.model_version_search import ModelVersionSearch

from backend.agents.gateway_contract import (
    DEFAULT_GATEWAY_AGENT_EXPERIMENT,
)
from backend.services.ai_gateway_proof_attestation import derive_gateway_proof_verify_key
from tools.databricks import verify_agent_runtime_uc_grants as verifier
from tools.databricks.agent_runtime_uc_baseline import (
    CatalogBindingEvidence,
    ControlPlaneForeignCatalogProof,
    _issue_control_plane_foreign_catalog_proof,
)
from tools.databricks.gateway_model_attestation import sign_gateway_model_contract
from tools.databricks.provision_gateway_responses_agent import gateway_resource_hash

APPLICATION_ID = "runtime-client"
SUPERVISOR_ID = "supervisor-123"
SUPERVISOR_ENDPOINT_ID = "supervisor-endpoint-456"
CATALOG = "mip"
GENIE_SPACE_ID = "01f-runtime-genie"
MODEL_FAMILY = "mip.audit.mortgage_growth_supervisor_proxy"
TABLE_PREFIX = "mip_agent_gateway_growth_agent"
UPSTREAM = "mip-mortgage-growth-supervisor-0123456789ab"
PROXY_CLIENT_ID = "proxy-client"
PROXY_CREDENTIAL_ID = "proxy-credential"
PROXY_SECRET_REFERENCE = "{{secrets/mip-agent-proxy/oauth-client-secret-proxy-credential}}"
WORKSPACE_HOST = "https://workspace.cloud.databricks.com"
SIGNING_KEY = base64.urlsafe_b64encode(b"u" * 32).decode("ascii").rstrip("=")
VERIFY_KEY = derive_gateway_proof_verify_key(SIGNING_KEY)
PREVIOUS_SIGNING_KEY = base64.urlsafe_b64encode(b"v" * 32).decode("ascii").rstrip("=")
PREVIOUS_VERIFY_KEY = derive_gateway_proof_verify_key(PREVIOUS_SIGNING_KEY)


class _FalseyList(list[object]):
    def __bool__(self) -> bool:
        return False


class _StringSubclass(str):
    pass


class _ForeignPrivilege(Enum):
    SELECT = "SELECT"


class _ForeignSecurableType(Enum):
    SCHEMA = "SCHEMA"


def _contract(
    *,
    upstream: str = UPSTREAM,
    experiment: str = DEFAULT_GATEWAY_AGENT_EXPERIMENT,
    source_hash: str | None = None,
    supervisor_id: str = SUPERVISOR_ID,
    supervisor_endpoint_id: str = SUPERVISOR_ENDPOINT_ID,
    runtime_application_id: str = APPLICATION_ID,
    verify_key: str = VERIFY_KEY,
) -> tuple[str, str, str]:
    source_hash = source_hash or "a" * 64
    contract_hash = gateway_resource_hash(
        source_hash=source_hash,
        supervisor_id=supervisor_id,
        supervisor_endpoint_id=supervisor_endpoint_id,
        runtime_application_id=runtime_application_id,
        workspace_host=WORKSPACE_HOST,
        model_name=MODEL_FAMILY,
        experiment_name=experiment,
        inference_schema="audit",
        inference_table_prefix=TABLE_PREFIX,
        attestation_verify_key=verify_key,
        proxy_caller_application_id=PROXY_CLIENT_ID,
        proxy_caller_credential_id=PROXY_CREDENTIAL_ID,
        proxy_caller_secret_reference=PROXY_SECRET_REFERENCE,
    )
    suffix = contract_hash[:12]
    return (
        f"{MODEL_FAMILY}_{suffix}",
        f"{TABLE_PREFIX}_{suffix}_payload",
        source_hash,
    )


MODEL, TABLE, SOURCE_HASH = _contract()


@pytest.fixture(autouse=True)
def _attestation_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIP_ALLOW_RUNTIME_MODEL_ATTESTATION_SIGNING", "1")
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY", VERIFY_KEY)
    monkeypatch.delenv("MIP_GATEWAY_MODEL_ATTESTATION_PREVIOUS_VERIFY_KEY", raising=False)


def _assignment(
    *privileges: str,
    principal: str = APPLICATION_ID,
    inherited_type: str | None = None,
    inherited_name: str | None = None,
) -> object:
    return SimpleNamespace(
        principal=principal,
        privileges=[
            SimpleNamespace(
                privilege=value,
                inherited_from_type=inherited_type,
                inherited_from_name=inherited_name,
            )
            for value in privileges
        ],
    )


class _Grants:
    def __init__(self, values: dict[tuple[str, str], set[str]]) -> None:
        self.values = values
        self.calls: list[tuple[str, str, str | None]] = []

    def get_effective(
        self,
        securable_type: str,
        full_name: str,
        *,
        principal: str,
        max_results: int,
        page_token: str | None,
    ) -> object:
        self.calls.append((securable_type, full_name, page_token))
        privileges = sorted(self.values.get((securable_type, full_name), set()))
        if (securable_type, full_name) == ("function", "mip.gold.fn_build_cohort"):
            if page_token is None:
                return SimpleNamespace(privilege_assignments=[], next_page_token="page-2")
            assert page_token == "page-2"
        assignment_kwargs: dict[str, str] = {}
        if (
            securable_type == "metastore"
            or full_name.startswith("system.")
            or full_name == "system"
        ):
            assignment_kwargs["principal"] = "account users"
        if full_name in verifier._SYSTEM_AI_MODELS or full_name in {
            f"system.ai.{name}" for name in verifier._SYSTEM_AI_FUNCTIONS
        }:
            assignment_kwargs.update(
                inherited_type="SCHEMA",
                inherited_name="system.ai",
            )
        if full_name.startswith(("other.information_schema", f"{CATALOG}.information_schema")):
            assignment_kwargs["principal"] = "account users"
        if full_name == "samples":
            assignment_kwargs["principal"] = "account users"
        elif full_name.startswith("samples."):
            assignment_kwargs.update(
                principal="account users",
                inherited_type="CATALOG",
                inherited_name="samples",
            )
        if full_name == "samples.information_schema":
            inherited = [
                SimpleNamespace(
                    privilege=value,
                    inherited_from_type="CATALOG",
                    inherited_from_name="samples",
                )
                for value in privileges
            ]
            inherited.append(
                SimpleNamespace(
                    privilege="USE_SCHEMA",
                    inherited_from_type=None,
                    inherited_from_name=None,
                )
            )
            return SimpleNamespace(
                privilege_assignments=[
                    SimpleNamespace(principal="account users", privileges=inherited)
                ],
                next_page_token=None,
            )
        if full_name == "samples.information_schema.tables":
            return SimpleNamespace(
                privilege_assignments=[
                    SimpleNamespace(
                        principal="account users",
                        privileges=[
                            SimpleNamespace(
                                privilege="SELECT",
                                inherited_from_type="CATALOG",
                                inherited_from_name="samples",
                            ),
                            SimpleNamespace(
                                privilege="SELECT",
                                inherited_from_type=None,
                                inherited_from_name=None,
                            ),
                        ],
                    )
                ],
                next_page_token=None,
            )
        return SimpleNamespace(
            privilege_assignments=(
                [_assignment(*privileges, **assignment_kwargs)] if privileges else []
            ),
            next_page_token=None,
        )


class _ModelRegistry:
    def __init__(self, tags: dict[str, dict[str, str]]) -> None:
        self.tags = tags
        self.set_calls: list[tuple[str, str, str, str]] = []

    def search_model_versions(
        self,
        query: str | None = None,
        *,
        filter_string: str | None = None,
        max_results: int | None = None,
        page_token: str | None = None,
    ) -> list[object]:
        assert max_results in (None, 1000)
        assert page_token is None
        query = filter_string or query
        assert query is not None
        prefix = "name='"
        assert query.startswith(prefix) and query.endswith("'")
        name = query[len(prefix) : -1]
        model_tags = self.tags.get(name)
        return (
            []
            if model_tags is None
            else [
                ModelVersionSearch(
                    name=name,
                    version="1",
                    creation_timestamp=1,
                    source="models:/m-reviewed-proxy",
                    run_id="run-reviewed-proxy",
                    status="READY",
                )
            ]
        )

    def set_model_version_tag(self, name: str, _version: str, key: str, value: str) -> None:
        self.set_calls.append((name, _version, key, value))
        self.tags[name][key] = value

    def get_model_version(self, name: str, version: str) -> object:
        return SimpleNamespace(
            name=name,
            version=version,
            source="models:/m-reviewed-proxy",
            tags=dict(self.tags[name]),
            status="READY",
        )


def _provenance(
    *,
    full_name: str = MODEL,
    source_hash: str = SOURCE_HASH,
    upstream: str = UPSTREAM,
    experiment: str = DEFAULT_GATEWAY_AGENT_EXPERIMENT,
    model_source: str = "models:/m-reviewed-proxy",
    supervisor_id: str = SUPERVISOR_ID,
    supervisor_endpoint_id: str = SUPERVISOR_ENDPOINT_ID,
    runtime_application_id: str = APPLICATION_ID,
) -> dict[str, str]:
    return sign_gateway_model_contract(
        full_name=full_name,
        model_source=model_source,
        source_hash=source_hash,
        supervisor_id=supervisor_id,
        supervisor_endpoint_id=supervisor_endpoint_id,
        upstream_endpoint=upstream,
        runtime_application_id=runtime_application_id,
        model_family=MODEL_FAMILY,
        experiment_base=experiment,
        catalog=CATALOG,
        genie_space_id=GENIE_SPACE_ID,
        inference_schema="audit",
        inference_table_prefix=TABLE_PREFIX,
    )


def _workspace(
    overrides: dict[tuple[str, str], set[str]] | None = None,
    *,
    model: str = MODEL,
    table: str = TABLE,
    extra_models: list[object] | None = None,
    model_owner: str = APPLICATION_ID,
    table_owner: str = APPLICATION_ID,
) -> object:
    values: dict[tuple[str, str], set[str]] = {
        ("metastore", "metastore-id"): {"USE_MARKETPLACE_ASSETS"},
        ("catalog", CATALOG): {"USE_CATALOG"},
        ("schema", "mip.gold"): {"USE_SCHEMA"},
        ("schema", "mip.audit"): {"USE_SCHEMA"},
        ("function", "mip.gold.fn_build_cohort"): {"EXECUTE"},
        ("function", "mip.gold.fn_segment_counts"): {"EXECUTE"},
        ("function", "mip.gold.fn_lead_queue_url"): {"EXECUTE"},
        ("table", f"mip.audit.{table}"): {"ALL_PRIVILEGES"},
        ("function", model): {"ALL_PRIVILEGES"},
        ("catalog", "system"): {"USE_CATALOG"},
        ("schema", "system.ai"): {"EXECUTE", "READ_VOLUME", "SELECT", "USE_SCHEMA"},
        ("schema", "system.data_quality_monitoring"): {"USE_SCHEMA"},
        ("schema", "system.information_schema"): {"USE_SCHEMA"},
        ("function", "system.ai.ai_classify"): {"EXECUTE"},
        ("function", "system.ai.meta_llama_3_70b"): {"EXECUTE"},
        ("table", "system.information_schema.tables"): {"SELECT"},
        ("schema", f"{CATALOG}.information_schema"): {"USE_SCHEMA"},
        ("schema", "other.information_schema"): {"USE_SCHEMA"},
        ("table", "other.information_schema.tables"): {"SELECT"},
        ("catalog", "samples"): set(verifier._SAMPLES_CATALOG_PRIVILEGES),
        ("schema", "samples.tpch"): set(verifier._SAMPLES_SCHEMA_PRIVILEGES),
        ("schema", "samples.information_schema"): set(verifier._SAMPLES_SCHEMA_PRIVILEGES),
        ("table", "samples.tpch.orders"): {"SELECT"},
        ("table", "samples.information_schema.tables"): {"SELECT"},
        ("volume", "samples.tpch.datasets"): {"READ_VOLUME"},
        ("function", "samples.tpch.sample_model"): {"EXECUTE"},
    }
    values.update(
        {
            ("table", f"{CATALOG}.information_schema.{name}"): {"SELECT"}
            for name in verifier._CATALOG_INFORMATION_SCHEMA_TABLES
        }
    )
    values.update(overrides or {})
    schemas: dict[str, list[object]] = {
        CATALOG: [
            SimpleNamespace(name="gold", full_name="mip.gold", owner="admin"),
            SimpleNamespace(name="audit", full_name="mip.audit", owner="admin"),
            SimpleNamespace(name="ref", full_name="mip.ref", owner="admin"),
            SimpleNamespace(
                name="information_schema",
                full_name=f"{CATALOG}.information_schema",
                owner="System user",
            ),
        ],
        "other": [
            SimpleNamespace(name="sandbox", full_name="other.sandbox", owner="admin"),
            SimpleNamespace(
                name="information_schema",
                full_name="other.information_schema",
                owner="System user",
            ),
        ],
        "system": [
            SimpleNamespace(name="ai", full_name="system.ai", owner="System user"),
            SimpleNamespace(
                name="data_quality_monitoring",
                full_name="system.data_quality_monitoring",
                owner="databricks-dqm-platform-principal",
            ),
            SimpleNamespace(
                name="information_schema",
                full_name="system.information_schema",
                owner="System user",
            ),
            SimpleNamespace(name="billing", full_name="system.billing", owner="System user"),
        ],
        "samples": [
            SimpleNamespace(name="tpch", full_name="samples.tpch", owner="System user"),
            SimpleNamespace(
                name="information_schema",
                full_name="samples.information_schema",
                owner="System user",
            ),
        ],
    }
    functions: dict[tuple[str, str], list[object]] = {
        (CATALOG, "gold"): [
            SimpleNamespace(name=name, full_name=f"mip.gold.{name}", owner="admin")
            for name in (*sorted(verifier.ALLOWED_FUNCTIONS), "fn_unreviewed")
        ],
        (CATALOG, "audit"): [],
        (CATALOG, "ref"): [],
        (CATALOG, "information_schema"): [],
        ("other", "sandbox"): [
            SimpleNamespace(
                name="fn_secret",
                full_name="other.sandbox.fn_secret",
                owner="admin",
            )
        ],
        ("other", "information_schema"): [],
        ("system", "ai"): [
            SimpleNamespace(
                name="ai_classify",
                full_name="system.ai.ai_classify",
                owner="System user",
            )
        ],
        ("system", "data_quality_monitoring"): [],
        ("system", "information_schema"): [],
        ("system", "billing"): [],
        ("samples", "tpch"): [],
        ("samples", "information_schema"): [],
    }
    tables: dict[tuple[str, str], list[object]] = {
        (CATALOG, "gold"): [
            SimpleNamespace(
                name="borrower_360",
                full_name="mip.gold.borrower_360",
                owner="admin",
            )
        ],
        (CATALOG, "audit"): [
            SimpleNamespace(
                name=table,
                full_name=f"mip.audit.{table}",
                catalog_name=CATALOG,
                schema_name="audit",
                owner=table_owner,
            ),
            SimpleNamespace(
                name="action_audit",
                full_name="mip.audit.action_audit",
                owner="admin",
            ),
        ],
        (CATALOG, "ref"): [
            SimpleNamespace(
                name="offer_rules",
                full_name="mip.ref.offer_rules",
                owner="admin",
            )
        ],
        (CATALOG, "information_schema"): [
            *(
                SimpleNamespace(
                    name=name,
                    full_name=f"{CATALOG}.information_schema.{name}",
                    owner="System user",
                )
                for name in sorted(verifier._CATALOG_INFORMATION_SCHEMA_TABLES)
            ),
            SimpleNamespace(
                name="future_metadata",
                full_name=f"{CATALOG}.information_schema.future_metadata",
                owner="System user",
            ),
        ],
        ("other", "sandbox"): [
            SimpleNamespace(name="secret", full_name="other.sandbox.secret", owner="admin")
        ],
        ("other", "information_schema"): [
            SimpleNamespace(
                name="tables",
                full_name="other.information_schema.tables",
                owner="System user",
            )
        ],
        ("system", "ai"): [],
        ("system", "data_quality_monitoring"): [
            SimpleNamespace(
                name="table_results",
                full_name="system.data_quality_monitoring.table_results",
                owner="databricks-dqm-platform-principal",
            )
        ],
        ("system", "information_schema"): [
            SimpleNamespace(
                name="tables",
                full_name="system.information_schema.tables",
                owner="System user",
            )
        ],
        ("system", "billing"): [
            SimpleNamespace(
                name="usage",
                full_name="system.billing.usage",
                owner="System user",
            )
        ],
        ("samples", "tpch"): [
            SimpleNamespace(
                name="orders",
                full_name="samples.tpch.orders",
                owner="System user",
            )
        ],
        ("samples", "information_schema"): [
            SimpleNamespace(
                name="tables",
                full_name="samples.information_schema.tables",
                owner="System user",
            )
        ],
    }
    volumes: dict[tuple[str, str], list[object]] = {
        (CATALOG, "gold"): [],
        (CATALOG, "audit"): [
            SimpleNamespace(name="proofs", full_name="mip.audit.proofs", owner="admin")
        ],
        (CATALOG, "ref"): [],
        (CATALOG, "information_schema"): [],
        ("other", "sandbox"): [
            SimpleNamespace(name="private", full_name="other.sandbox.private", owner="admin")
        ],
        ("other", "information_schema"): [],
        ("system", "ai"): [],
        ("system", "data_quality_monitoring"): [],
        ("system", "information_schema"): [],
        ("system", "billing"): [],
        ("samples", "tpch"): [
            SimpleNamespace(
                name="datasets",
                full_name="samples.tpch.datasets",
                owner="System user",
            )
        ],
        ("samples", "information_schema"): [],
    }
    models: dict[str, list[object]] = {
        CATALOG: [
            SimpleNamespace(
                full_name=model,
                catalog_name=CATALOG,
                owner=model_owner,
                browse_only=False,
            ),
            SimpleNamespace(
                full_name="mip.audit.unrelated_model",
                catalog_name=CATALOG,
                owner="admin",
                browse_only=not bool(values.get(("function", "mip.audit.unrelated_model"))),
            ),
            *(extra_models or []),
        ],
        "other": [
            SimpleNamespace(
                full_name="other.sandbox.secret_model",
                catalog_name="other",
                owner="admin",
                browse_only=not bool(values.get(("function", "other.sandbox.secret_model"))),
            )
        ],
        "system": [
            SimpleNamespace(
                full_name="system.ai.meta_llama_3_70b",
                catalog_name="system",
                schema_name="ai",
                owner="System user",
            )
        ],
        "samples": [
            SimpleNamespace(
                full_name="samples.tpch.sample_model",
                catalog_name="samples",
                schema_name="tpch",
                owner="System user",
            )
        ],
    }
    for schema in schemas[CATALOG]:
        schema.catalog_name = CATALOG
    for inventory in (functions, tables, volumes):
        for (item_catalog, item_schema), items in inventory.items():
            if item_catalog != CATALOG:
                continue
            for item in items:
                item.catalog_name = item_catalog
                item.schema_name = item_schema
    for catalog_models in models.values():
        for item in catalog_models:
            item_catalog, item_schema, item_name = item.full_name.split(".", 2)
            item.catalog_name = item_catalog
            item.schema_name = item_schema
            item.name = item_name
    grants = _Grants(values)
    return SimpleNamespace(
        config=SimpleNamespace(workspace_id="workspace-id", host=WORKSPACE_HOST),
        get_workspace_id=lambda: "workspace-id",
        service_principals=SimpleNamespace(
            list=lambda **_kwargs: iter(
                [SimpleNamespace(application_id=APPLICATION_ID, id="runtime-scim-id")]
            )
        ),
        groups=SimpleNamespace(
            get=lambda group_id: SimpleNamespace(
                id=group_id,
                display_name="users",
                meta=SimpleNamespace(resource_type="WorkspaceGroup"),
            )
        ),
        metastores=SimpleNamespace(
            current=lambda: SimpleNamespace(metastore_id="metastore-id"),
            get=lambda _id: SimpleNamespace(owner="deployer@example.com"),
        ),
        current_user=SimpleNamespace(
            me=lambda: SimpleNamespace(
                user_name=APPLICATION_ID,
                application_id=APPLICATION_ID,
                id="runtime-scim-id",
                groups=[],
            )
        ),
        catalogs=SimpleNamespace(
            list=lambda **_kwargs: iter(
                [
                    SimpleNamespace(
                        name=CATALOG,
                        owner="admin",
                        catalog_type="MANAGED_CATALOG",
                        isolation_mode="OPEN",
                    ),
                    SimpleNamespace(
                        name="other",
                        owner="admin",
                        catalog_type="MANAGED_CATALOG",
                        isolation_mode="OPEN",
                    ),
                    SimpleNamespace(
                        name="system",
                        owner="System user",
                        catalog_type="SYSTEM_CATALOG",
                        isolation_mode="OPEN",
                    ),
                    SimpleNamespace(
                        name="samples",
                        owner="System user",
                        catalog_type="MANAGED_CATALOG",
                        isolation_mode="OPEN",
                    ),
                    SimpleNamespace(
                        name="__databricks_internal",
                        owner="System user",
                        catalog_type="INTERNAL_CATALOG",
                        isolation_mode="OPEN",
                    ),
                ]
            )
        ),
        schemas=SimpleNamespace(list=lambda catalog, **_kwargs: iter(schemas[catalog])),
        functions=SimpleNamespace(
            list=lambda catalog, schema, **_kwargs: iter(functions[(catalog, schema)])
        ),
        tables=SimpleNamespace(
            list=lambda catalog, schema, **_kwargs: iter(tables[(catalog, schema)])
        ),
        volumes=SimpleNamespace(
            list=lambda catalog, schema, **_kwargs: iter(volumes[(catalog, schema)])
        ),
        registered_models=SimpleNamespace(
            list=lambda **_kwargs: iter(
                [model for catalog_models in models.values() for model in catalog_models]
            )
        ),
        grants=grants,
    )


def _add_runtime_managed_online_catalog(
    workspace: Any,
    *,
    catalog: str = "online_state",
    information_schema_owner: str = "online-owner@example.com",
    information_table_owner: str = "online-owner@example.com",
) -> None:
    catalogs = list(workspace.catalogs.list(include_browse=True))
    catalogs.append(
        SimpleNamespace(
            name=catalog,
            owner="online-owner@example.com",
            catalog_type="MANAGED_ONLINE_CATALOG",
            isolation_mode="OPEN",
        )
    )
    workspace.catalogs.list = lambda **_kwargs: iter(catalogs)

    original_schema_list = workspace.schemas.list
    workspace.schemas.list = lambda selected, **kwargs: (
        iter(
            [
                SimpleNamespace(
                    name="information_schema",
                    full_name=f"{catalog}.information_schema",
                    owner=information_schema_owner,
                )
            ]
        )
        if selected == catalog
        else original_schema_list(selected, **kwargs)
    )

    original_function_list = workspace.functions.list
    workspace.functions.list = lambda selected, schema, **kwargs: (
        iter([]) if selected == catalog else original_function_list(selected, schema, **kwargs)
    )

    original_table_list = workspace.tables.list
    workspace.tables.list = lambda selected, schema, **kwargs: (
        iter(
            [
                SimpleNamespace(
                    name="tables",
                    full_name=f"{catalog}.information_schema.tables",
                    owner=information_table_owner,
                )
            ]
        )
        if selected == catalog
        else original_table_list(selected, schema, **kwargs)
    )

    original_volume_list = workspace.volumes.list
    workspace.volumes.list = lambda selected, schema, **kwargs: (
        iter([]) if selected == catalog else original_volume_list(selected, schema, **kwargs)
    )


def _verify(
    workspace: Any,
    *,
    model: str = MODEL,
    experiment: str = DEFAULT_GATEWAY_AGENT_EXPERIMENT,
    registry_tags: dict[str, dict[str, str]] | None = None,
    model_registry: Any | None = None,
    foreign_control_plane_proof: ControlPlaneForeignCatalogProof | None = None,
) -> None:
    verifier.verify_effective_uc_boundary(
        workspace,
        application_id=APPLICATION_ID,
        supervisor_id=SUPERVISOR_ID,
        supervisor_endpoint_id=SUPERVISOR_ENDPOINT_ID,
        catalog=CATALOG,
        gateway_model=model,
        gateway_model_family=MODEL_FAMILY,
        gateway_experiment_base=experiment,
        genie_space_id=GENIE_SPACE_ID,
        inference_table_prefix=TABLE_PREFIX,
        proxy_caller_application_id=PROXY_CLIENT_ID,
        proxy_caller_credential_id=PROXY_CREDENTIAL_ID,
        proxy_caller_secret_reference=PROXY_SECRET_REFERENCE,
        model_registry=model_registry or _ModelRegistry(registry_tags or {model: _provenance()}),
        foreign_control_plane_proof=foreign_control_plane_proof,
    )


def _foreign_proof(**overrides: Any) -> ControlPlaneForeignCatalogProof:
    audited_catalogs = overrides.pop("audited_catalogs", frozenset({"other"}))
    values = {
        "application_id": APPLICATION_ID,
        "catalog": CATALOG,
        "metastore_id": "metastore-id",
        "workspace_id": "workspace-id",
        "grant_audited_catalogs": audited_catalogs,
        "binding_denied_catalogs": (),
    }
    values.update(overrides)
    return _issue_control_plane_foreign_catalog_proof(**values)


def _binding_denied_proof() -> ControlPlaneForeignCatalogProof:
    return _issue_control_plane_foreign_catalog_proof(
        application_id=APPLICATION_ID,
        catalog=CATALOG,
        metastore_id="metastore-id",
        workspace_id="workspace-id",
        grant_audited_catalogs=frozenset(),
        binding_denied_catalogs=(
            CatalogBindingEvidence(
                catalog="other",
                owner="admin",
                catalog_type="MANAGED_CATALOG",
                isolation_mode="ISOLATED",
                bindings=(
                    (
                        "2478181912221244",
                        "BINDING_TYPE_READ_WRITE",
                    ),
                ),
            ),
        ),
    )


def test_effective_runtime_uc_boundary_passes_and_reads_all_pages() -> None:
    workspace = _workspace()

    _verify(workspace)

    assert ("function", "mip.gold.fn_build_cohort", "page-2") in workspace.grants.calls
    assert ("table", f"mip.audit.{TABLE}", None) in workspace.grants.calls
    assert ("schema", "mip.information_schema", None) in workspace.grants.calls
    assert ("table", "mip.information_schema.tables", None) in workspace.grants.calls
    assert (
        "table",
        f"mip.information_schema.{sorted(verifier._CATALOG_INFORMATION_SCHEMA_TABLES)[-1]}",
        None,
    ) in workspace.grants.calls
    assert ("table", "other.sandbox.secret", None) in workspace.grants.calls


def test_effective_runtime_uc_boundary_accepts_owner_only_gateway_artifacts() -> None:
    workspace = _workspace(
        {
            ("table", f"mip.audit.{TABLE}"): set(),
            ("function", MODEL): set(),
        }
    )

    _verify(workspace)

    assert ("table", f"mip.audit.{TABLE}", None) in workspace.grants.calls
    assert ("function", MODEL, None) in workspace.grants.calls


def test_effective_runtime_uc_boundary_uses_one_validated_audit_table_snapshot() -> None:
    workspace = _workspace()
    original = workspace.tables.list
    audit_calls = 0

    def changing_inventory(catalog: str, schema: str, **kwargs: Any) -> object:
        nonlocal audit_calls
        if (catalog, schema) == (CATALOG, "audit"):
            audit_calls += 1
            if audit_calls > 1:
                return iter(
                    [
                        SimpleNamespace(
                            name=TABLE,
                            full_name=f"{CATALOG}.audit.{TABLE}",
                            owner="human@example.com",
                        )
                    ]
                )
        return original(catalog, schema, **kwargs)

    workspace.tables.list = changing_inventory

    _verify(workspace)

    assert audit_calls == 1


def test_effective_runtime_uc_boundary_rejects_duplicate_audit_schema() -> None:
    workspace = _workspace()
    original = workspace.schemas.list
    mip_schemas = list(original(CATALOG, include_browse=True))
    audit = next(item for item in mip_schemas if item.name == "audit")
    mip_schemas.append(copy.deepcopy(audit))

    def duplicate_audit_schema(catalog: str, **kwargs: Any) -> object:
        if catalog == CATALOG:
            return iter(mip_schemas)
        return original(catalog, **kwargs)

    workspace.schemas.list = duplicate_audit_schema

    with pytest.raises(RuntimeError, match="duplicate names"):
        _verify(workspace)


@pytest.mark.parametrize(
    ("resource", "schema_name"),
    [
        ("function", "gold"),
        ("table", "audit"),
        ("volume", "audit"),
    ],
)
def test_effective_runtime_uc_boundary_rejects_duplicate_mip_child(
    resource: str,
    schema_name: str,
) -> None:
    workspace = _workspace()
    api = getattr(workspace, f"{resource}s")
    original = api.list

    def duplicate_child(
        catalog: str,
        schema: str,
        **kwargs: Any,
    ) -> object:
        items = list(original(catalog, schema, **kwargs))
        if (catalog, schema) == (CATALOG, schema_name):
            items.append(copy.deepcopy(items[0]))
        return iter(items)

    api.list = duplicate_child

    with pytest.raises(RuntimeError, match="duplicate identity"):
        _verify(workspace)


def test_effective_runtime_uc_boundary_rejects_duplicate_registered_model() -> None:
    workspace = _workspace()
    models = list(workspace.registered_models.list(include_browse=True))
    models.append(copy.deepcopy(next(item for item in models if item.full_name == MODEL)))
    workspace.registered_models.list = lambda **_kwargs: iter(models)

    with pytest.raises(RuntimeError, match="duplicate identities"):
        _verify(workspace)


@pytest.mark.parametrize("full_name", ["mip..orphan_model", "mip.audit."])
def test_effective_runtime_uc_boundary_rejects_empty_registered_model_tuple_component(
    full_name: str,
) -> None:
    workspace = _workspace(
        extra_models=[SimpleNamespace(full_name=full_name, owner="admin")],
    )

    with pytest.raises(RuntimeError, match="incomplete parent identity"):
        _verify(workspace)


@pytest.mark.parametrize(
    ("table_owner", "model_owner"),
    [
        ("human@example.com", APPLICATION_ID),
        (APPLICATION_ID, "human@example.com"),
        (APPLICATION_ID.upper(), APPLICATION_ID),
        (APPLICATION_ID, APPLICATION_ID.upper()),
        (f" {APPLICATION_ID}", APPLICATION_ID),
        (APPLICATION_ID, f"{APPLICATION_ID} "),
        (_StringSubclass(APPLICATION_ID), APPLICATION_ID),
        (APPLICATION_ID, _StringSubclass(APPLICATION_ID)),
    ],
)
def test_effective_runtime_uc_boundary_rejects_owner_drift_without_explicit_artifact_grants(
    table_owner: str,
    model_owner: str,
) -> None:
    workspace = _workspace(
        {
            ("table", f"mip.audit.{TABLE}"): set(),
            ("function", MODEL): set(),
        },
        table_owner=table_owner,
        model_owner=model_owner,
    )

    with pytest.raises(RuntimeError, match="ownership|noncanonical owner"):
        _verify(workspace)


@pytest.mark.parametrize(
    ("securable_type", "full_name"),
    [
        ("table", f"mip.audit.{TABLE}"),
        ("function", MODEL),
    ],
)
def test_effective_runtime_uc_boundary_propagates_owned_artifact_permission_denial(
    securable_type: str,
    full_name: str,
) -> None:
    workspace = _workspace()
    original = workspace.grants.get_effective

    def deny_owned_artifact(*args: Any, **kwargs: Any) -> object:
        if args[:2] == (securable_type, full_name):
            raise PermissionDenied("owned artifact permissions unavailable")
        return original(*args, **kwargs)

    workspace.grants.get_effective = deny_owned_artifact

    with pytest.raises(PermissionDenied, match="owned artifact permissions unavailable"):
        _verify(workspace)


def test_effective_runtime_uc_boundary_rejects_empty_owned_artifact_assignment() -> None:
    workspace = _workspace()
    original = workspace.grants.get_effective
    table_full_name = f"mip.audit.{TABLE}"

    def return_empty_assignment(*args: Any, **kwargs: Any) -> object:
        if args[:2] == ("table", table_full_name):
            return SimpleNamespace(
                privilege_assignments=[SimpleNamespace(principal=APPLICATION_ID, privileges=[])],
                next_page_token=None,
            )
        return original(*args, **kwargs)

    workspace.grants.get_effective = return_empty_assignment

    with pytest.raises(RuntimeError, match="empty privilege assignment"):
        _verify(workspace)


@pytest.mark.parametrize("response", [None, object()])
def test_effective_runtime_uc_boundary_rejects_invalid_artifact_permission_envelope(
    response: object,
) -> None:
    workspace = _workspace()
    original = workspace.grants.get_effective
    table_full_name = f"mip.audit.{TABLE}"

    def return_invalid_envelope(*args: Any, **kwargs: Any) -> object:
        if args[:2] == ("table", table_full_name):
            return response
        return original(*args, **kwargs)

    workspace.grants.get_effective = return_invalid_envelope

    with pytest.raises(RuntimeError, match="invalid response envelope"):
        _verify(workspace)


@pytest.mark.parametrize(
    "source",
    [
        f" {APPLICATION_ID}",
        f"{APPLICATION_ID} ",
        0,
        APPLICATION_ID.encode(),
        _StringSubclass(APPLICATION_ID),
    ],
)
def test_effective_runtime_uc_boundary_rejects_noncanonical_artifact_source(
    source: object,
) -> None:
    workspace = _workspace()
    original = workspace.grants.get_effective
    table_full_name = f"mip.audit.{TABLE}"

    def return_malformed_source(*args: Any, **kwargs: Any) -> object:
        if args[:2] == ("table", table_full_name):
            return SimpleNamespace(
                privilege_assignments=[
                    SimpleNamespace(
                        principal=source,
                        privileges=[
                            SimpleNamespace(
                                privilege="ALL_PRIVILEGES",
                                inherited_from_type=None,
                                inherited_from_name=None,
                            )
                        ],
                    )
                ],
                next_page_token=None,
            )
        return original(*args, **kwargs)

    workspace.grants.get_effective = return_malformed_source

    with pytest.raises(RuntimeError, match="noncanonical principal"):
        _verify(workspace)


@pytest.mark.parametrize("assignments", [0, False, "", (), {}])
def test_effective_runtime_uc_boundary_rejects_nonlist_artifact_assignments(
    assignments: object,
) -> None:
    workspace = _workspace()
    original = workspace.grants.get_effective
    table_full_name = f"mip.audit.{TABLE}"

    def return_malformed_assignments(*args: Any, **kwargs: Any) -> object:
        if args[:2] == ("table", table_full_name):
            return SimpleNamespace(
                privilege_assignments=assignments,
                next_page_token=None,
            )
        return original(*args, **kwargs)

    workspace.grants.get_effective = return_malformed_assignments

    with pytest.raises(RuntimeError, match="non-list privilege assignment collection"):
        _verify(workspace)


def test_effective_runtime_uc_boundary_rejects_falsey_list_with_hidden_grant() -> None:
    workspace = _workspace()
    original = workspace.grants.get_effective
    table_full_name = f"mip.audit.{TABLE}"
    assignments = _FalseyList(
        [
            SimpleNamespace(
                principal="account users",
                privileges=[
                    SimpleNamespace(
                        privilege="SELECT",
                        inherited_from_type="CATALOG",
                        inherited_from_name=CATALOG,
                    )
                ],
            )
        ]
    )

    def return_falsey_assignments(*args: Any, **kwargs: Any) -> object:
        if args[:2] == ("table", table_full_name):
            return SimpleNamespace(
                privilege_assignments=assignments,
                next_page_token=None,
            )
        return original(*args, **kwargs)

    workspace.grants.get_effective = return_falsey_assignments

    with pytest.raises(RuntimeError, match="non-list privilege assignment collection"):
        _verify(workspace)


def test_effective_runtime_uc_boundary_rejects_falsey_privilege_list() -> None:
    workspace = _workspace()
    original = workspace.grants.get_effective
    table_full_name = f"mip.audit.{TABLE}"
    privileges = _FalseyList(
        [
            SimpleNamespace(
                privilege="SELECT",
                inherited_from_type=None,
                inherited_from_name=None,
            )
        ]
    )

    def return_falsey_privileges(*args: Any, **kwargs: Any) -> object:
        if args[:2] == ("table", table_full_name):
            return SimpleNamespace(
                privilege_assignments=[
                    SimpleNamespace(
                        principal=APPLICATION_ID,
                        privileges=privileges,
                    )
                ],
                next_page_token=None,
            )
        return original(*args, **kwargs)

    workspace.grants.get_effective = return_falsey_privileges

    with pytest.raises(RuntimeError, match="non-list privilege collection"):
        _verify(workspace)


@pytest.mark.parametrize(
    "privilege",
    [
        0,
        False,
        "",
        " ALL_PRIVILEGES",
        "ALL_PRIVILEGES ",
        "all_privileges",
        _StringSubclass("ALL_PRIVILEGES"),
        _ForeignPrivilege.SELECT,
    ],
)
def test_effective_runtime_uc_boundary_rejects_noncanonical_artifact_privilege(
    privilege: object,
) -> None:
    workspace = _workspace()
    original = workspace.grants.get_effective
    table_full_name = f"mip.audit.{TABLE}"

    def return_malformed_privilege(*args: Any, **kwargs: Any) -> object:
        if args[:2] == ("table", table_full_name):
            return SimpleNamespace(
                privilege_assignments=[
                    SimpleNamespace(
                        principal=APPLICATION_ID,
                        privileges=[
                            SimpleNamespace(
                                privilege=privilege,
                                inherited_from_type=None,
                                inherited_from_name=None,
                            )
                        ],
                    )
                ],
                next_page_token=None,
            )
        return original(*args, **kwargs)

    workspace.grants.get_effective = return_malformed_privilege

    with pytest.raises(RuntimeError, match="noncanonical privilege"):
        _verify(workspace)


@pytest.mark.parametrize(
    ("inherited_type", "inherited_name"),
    [
        (" SCHEMA", "mip.audit"),
        ("SCHEMA ", "mip.audit"),
        (0, "mip.audit"),
        ("SCHEMA", 0),
        (None, "mip.audit"),
        ("SCHEMA", None),
        ("", ""),
        (_StringSubclass("SCHEMA"), "mip.audit"),
        ("SCHEMA", _StringSubclass("mip.audit")),
        (_ForeignSecurableType.SCHEMA, "mip.audit"),
    ],
)
def test_effective_runtime_uc_boundary_rejects_malformed_artifact_inheritance(
    inherited_type: object,
    inherited_name: object,
) -> None:
    workspace = _workspace()
    original = workspace.grants.get_effective
    table_full_name = f"mip.audit.{TABLE}"

    def return_malformed_inheritance(*args: Any, **kwargs: Any) -> object:
        if args[:2] == ("table", table_full_name):
            return SimpleNamespace(
                privilege_assignments=[
                    SimpleNamespace(
                        principal=APPLICATION_ID,
                        privileges=[
                            SimpleNamespace(
                                privilege="ALL_PRIVILEGES",
                                inherited_from_type=inherited_type,
                                inherited_from_name=inherited_name,
                            )
                        ],
                    )
                ],
                next_page_token=None,
            )
        return original(*args, **kwargs)

    workspace.grants.get_effective = return_malformed_inheritance

    with pytest.raises(RuntimeError, match="inheritance"):
        _verify(workspace)


@pytest.mark.parametrize(
    "next_token",
    [0, False, " page-2", "page-2 ", _StringSubclass("page-2")],
)
def test_effective_runtime_uc_boundary_rejects_noncanonical_artifact_page_token(
    next_token: object,
) -> None:
    workspace = _workspace()
    original = workspace.grants.get_effective
    table_full_name = f"mip.audit.{TABLE}"

    def return_malformed_page_token(*args: Any, **kwargs: Any) -> object:
        if args[:2] == ("table", table_full_name):
            return SimpleNamespace(
                privilege_assignments=[],
                next_page_token=next_token,
            )
        return original(*args, **kwargs)

    workspace.grants.get_effective = return_malformed_page_token

    with pytest.raises(RuntimeError, match="noncanonical pagination token"):
        _verify(workspace)


def test_effective_runtime_uc_boundary_reads_all_owned_artifact_permission_pages() -> None:
    workspace = _workspace()
    original = workspace.grants.get_effective
    table_full_name = f"mip.audit.{TABLE}"

    def paginate_owned_artifact(*args: Any, **kwargs: Any) -> object:
        if args[:2] != ("table", table_full_name):
            return original(*args, **kwargs)
        if kwargs["page_token"] is None:
            return SimpleNamespace(privilege_assignments=[], next_page_token="artifact-page-2")
        assert kwargs["page_token"] == "artifact-page-2"
        return SimpleNamespace(
            privilege_assignments=[
                _assignment(
                    "SELECT",
                    principal="account users",
                    inherited_type="CATALOG",
                    inherited_name=CATALOG,
                )
            ],
            next_page_token=None,
        )

    workspace.grants.get_effective = paginate_owned_artifact

    with pytest.raises(RuntimeError, match="direct runtime ownership"):
        _verify(workspace)


def test_owner_only_gateway_model_still_requires_exact_provenance() -> None:
    workspace = _workspace(
        {
            ("table", f"mip.audit.{TABLE}"): set(),
            ("function", MODEL): set(),
        }
    )

    with pytest.raises(RuntimeError, match="provenance"):
        _verify(
            workspace,
            registry_tags={MODEL: _provenance(source_hash="f" * 64)},
        )


def test_effective_runtime_uc_boundary_accepts_zero_access_managed_online_metadata() -> None:
    workspace = _workspace()
    _add_runtime_managed_online_catalog(workspace)

    _verify(workspace)

    assert ("catalog", "online_state", None) in workspace.grants.calls
    assert ("schema", "online_state.information_schema", None) in workspace.grants.calls
    assert ("table", "online_state.information_schema.tables", None) in workspace.grants.calls


@pytest.mark.parametrize(
    ("securable_type", "full_name", "privilege"),
    [
        ("schema", "online_state.information_schema", "USE_SCHEMA"),
        ("table", "online_state.information_schema.tables", "SELECT"),
    ],
)
def test_effective_runtime_uc_boundary_rejects_managed_online_metadata_access(
    securable_type: str,
    full_name: str,
    privilege: str,
) -> None:
    workspace = _workspace({(securable_type, full_name): {privilege}})
    _add_runtime_managed_online_catalog(workspace)

    with pytest.raises(RuntimeError, match="effective UC boundary failed"):
        _verify(workspace)


@pytest.mark.parametrize(
    ("information_schema_owner", "information_table_owner"),
    [
        ("different-owner@example.com", "online-owner@example.com"),
        ("online-owner@example.com", "different-owner@example.com"),
    ],
)
def test_effective_runtime_uc_boundary_rejects_managed_online_metadata_owner_drift(
    information_schema_owner: str,
    information_table_owner: str,
) -> None:
    workspace = _workspace()
    _add_runtime_managed_online_catalog(
        workspace,
        information_schema_owner=information_schema_owner,
        information_table_owner=information_table_owner,
    )

    with pytest.raises(RuntimeError, match="managed-online catalog owner"):
        _verify(workspace)


@pytest.mark.parametrize("securable_type", ["schema", "table"])
def test_effective_runtime_uc_boundary_rejects_ordinary_metadata_owner_drift(
    securable_type: str,
) -> None:
    workspace = _workspace()
    if securable_type == "schema":
        schema = next(
            item
            for item in workspace.schemas.list("other", include_browse=True)
            if item.name == "information_schema"
        )
        schema.owner = "different-owner@example.com"
    else:
        table = next(
            item
            for item in workspace.tables.list(
                "other",
                "information_schema",
                include_browse=True,
                omit_columns=True,
                omit_properties=True,
            )
            if item.name == "tables"
        )
        table.owner = "different-owner@example.com"

    with pytest.raises(RuntimeError, match="Databricks System user"):
        _verify(workspace)


def test_effective_runtime_uc_boundary_is_public_key_only_and_never_mutates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _ModelRegistry({MODEL: _provenance()})
    monkeypatch.delenv("MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY")
    monkeypatch.delenv("MIP_ALLOW_RUNTIME_MODEL_ATTESTATION_SIGNING")

    _verify(_workspace(), model_registry=registry)

    assert registry.set_calls == []


def test_effective_runtime_uc_boundary_hydrates_real_uc_search_shape() -> None:
    registry = _ModelRegistry({MODEL: _provenance()})
    search_rows = registry.search_model_versions(
        filter_string=f"name='{MODEL}'",
        max_results=1000,
        page_token=None,
    )
    assert callable(search_rows[0].tags)

    _verify(_workspace(), model_registry=registry)


def test_effective_runtime_uc_boundary_reads_every_model_version_page() -> None:
    class Page(list[object]):
        def __init__(self, values: list[object], token: str | None) -> None:
            super().__init__(values)
            self.token = token

    class PagedRegistry(_ModelRegistry):
        def __init__(self) -> None:
            super().__init__({MODEL: _provenance()})
            self.page_tokens: list[str | None] = []

        def search_model_versions(self, **kwargs: Any) -> Page:
            assert kwargs["filter_string"] == f"name='{MODEL}'"
            assert kwargs["max_results"] == 1000
            token = kwargs["page_token"]
            self.page_tokens.append(token)
            version = "1" if token is None else "2"
            next_token = "page-2" if token is None else None
            return Page(
                [
                    ModelVersionSearch(
                        name=MODEL,
                        version=version,
                        creation_timestamp=1,
                        source="models:/m-reviewed-proxy",
                        run_id="run-reviewed-proxy",
                        status="READY",
                    )
                ],
                next_token,
            )

    registry = PagedRegistry()

    _verify(_workspace(), model_registry=registry)

    assert registry.page_tokens == [None, "page-2"]


def test_effective_runtime_uc_boundary_rejects_repeated_model_version_page_token() -> None:
    class RepeatingPage(list[object]):
        token = "repeated"

    class RepeatingRegistry(_ModelRegistry):
        def search_model_versions(self, **_kwargs: Any) -> RepeatingPage:
            return RepeatingPage()

    with pytest.raises(RuntimeError, match="repeated a page token"):
        _verify(_workspace(), model_registry=RepeatingRegistry({MODEL: _provenance()}))


def test_effective_runtime_uc_boundary_requires_current_candidate_epoch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous_model, previous_table, previous_source_hash = _contract(verify_key=PREVIOUS_VERIFY_KEY)
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY", PREVIOUS_SIGNING_KEY)
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY", PREVIOUS_VERIFY_KEY)
    previous = _provenance(
        full_name=previous_model,
        source_hash=previous_source_hash,
    )
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY", VERIFY_KEY)
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_PREVIOUS_VERIFY_KEY", PREVIOUS_VERIFY_KEY)
    registry = _ModelRegistry({previous_model: previous})

    with pytest.raises(RuntimeError, match="previous attestation epoch"):
        _verify(
            _workspace(model=previous_model, table=previous_table),
            model=previous_model,
            model_registry=registry,
        )

    assert registry.set_calls == []


def test_cli_rotation_action_is_unavailable() -> None:
    with pytest.raises(SystemExit):
        verifier.main(
            [
                "--action",
                "rotate-retained-model-attestations",
                "--application-id",
                APPLICATION_ID,
                "--gateway-model",
                MODEL,
                "--genie-space-id",
                GENIE_SPACE_ID,
                "--inference-table-prefix",
                TABLE_PREFIX,
            ]
        )


@pytest.mark.parametrize(
    ("securable", "privileges"),
    [
        (("metastore", "metastore-id"), {"CREATE_SHARE"}),
        (("schema", "mip.gold"), {"USE_SCHEMA", "SELECT"}),
        (("table", "mip.gold.borrower_360"), {"SELECT"}),
        (("function", "mip.gold.fn_unreviewed"), {"EXECUTE"}),
        (("volume", "mip.audit.proofs"), {"READ_VOLUME"}),
        (("function", "mip.audit.unrelated_model"), {"EXECUTE"}),
        (("catalog", "other"), {"USE_CATALOG"}),
        (("schema", "other.sandbox"), {"USE_SCHEMA"}),
        (("table", "other.sandbox.secret"), {"SELECT"}),
        (("function", "other.sandbox.fn_secret"), {"EXECUTE"}),
        (("volume", "other.sandbox.private"), {"READ_VOLUME"}),
        (("function", "other.sandbox.secret_model"), {"EXECUTE"}),
        (("schema", "mip.information_schema"), set()),
        (("table", "mip.information_schema.tables"), {"SELECT", "MODIFY"}),
        (("table", "mip.information_schema.future_metadata"), {"SELECT"}),
    ],
)
def test_effective_runtime_uc_boundary_rejects_hidden_or_broad_access(
    securable: tuple[str, str],
    privileges: set[str],
) -> None:
    with pytest.raises(RuntimeError, match="effective|unexpected|forbidden"):
        _verify(_workspace({securable: privileges}))


def test_effective_runtime_uc_boundary_requires_runtime_authenticated_inventory() -> None:
    workspace = _workspace()
    workspace.current_user.me = lambda: SimpleNamespace(user_name="deployer@example.com")

    with pytest.raises(RuntimeError, match="not authenticated"):
        _verify(workspace)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("user_name", f" {APPLICATION_ID}"),
        ("application_id", f"{APPLICATION_ID} "),
        ("id", " runtime-scim-id"),
    ],
)
def test_effective_runtime_uc_boundary_rejects_noncanonical_runtime_identity(
    field: str,
    value: str,
) -> None:
    workspace = _workspace()
    caller = {
        "user_name": APPLICATION_ID,
        "application_id": APPLICATION_ID,
        "id": "runtime-scim-id",
        "groups": [],
    }
    caller[field] = value
    workspace.current_user.me = lambda: SimpleNamespace(**caller)

    with pytest.raises(RuntimeError, match="noncanonical text"):
        _verify(workspace)


@pytest.mark.parametrize("group_field", ["value", "display"])
def test_effective_runtime_uc_boundary_rejects_noncanonical_runtime_group(
    group_field: str,
) -> None:
    workspace = _workspace()
    group = {"value": "group-id", "display": "runtime-group"}
    group[group_field] = f" {group[group_field]}"
    workspace.current_user.me = lambda: SimpleNamespace(
        user_name=APPLICATION_ID,
        application_id=APPLICATION_ID,
        id="runtime-scim-id",
        groups=[SimpleNamespace(**group)],
    )

    with pytest.raises(RuntimeError, match="noncanonical text"):
        _verify(workspace)


def test_effective_runtime_uc_boundary_requires_authoritative_runtime_groups() -> None:
    workspace = _workspace()
    workspace.current_user.me = lambda: SimpleNamespace(
        user_name=APPLICATION_ID,
        application_id=APPLICATION_ID,
        id="runtime-scim-id",
    )

    with pytest.raises(RuntimeError, match="omitted.*groups"):
        _verify(workspace)


def test_effective_runtime_uc_boundary_never_treats_permission_denial_as_zero_access() -> None:
    workspace = _workspace()
    original = workspace.grants.get_effective

    def deny_foreign_catalog(*args: Any, **kwargs: Any) -> object:
        if args[:2] == ("catalog", "other"):
            raise PermissionDenied("runtime lacks USE CATALOG")
        return original(*args, **kwargs)

    workspace.grants.get_effective = deny_foreign_catalog

    with pytest.raises(PermissionDenied, match="runtime lacks USE CATALOG"):
        _verify(workspace)


def test_control_plane_proof_owns_foreign_audit_without_runtime_lookup() -> None:
    workspace = _workspace()
    original = workspace.grants.get_effective

    def deny_foreign_catalog(*args: Any, **kwargs: Any) -> object:
        if args[:2] == ("catalog", "other"):
            raise PermissionDenied("runtime lacks USE CATALOG")
        return original(*args, **kwargs)

    workspace.grants.get_effective = deny_foreign_catalog

    _verify(workspace, foreign_control_plane_proof=_foreign_proof())


def test_binding_denied_proof_requires_catalog_to_remain_hidden_from_runtime() -> None:
    with pytest.raises(RuntimeError, match="binding-denied.*became visible"):
        _verify(
            _workspace(),
            foreign_control_plane_proof=_binding_denied_proof(),
        )


def test_binding_denied_proof_accepts_catalog_and_models_hidden_from_runtime() -> None:
    workspace = _workspace()
    original_catalogs = list(workspace.catalogs.list(include_browse=True))
    workspace.catalogs.list = lambda **_kwargs: iter(
        [item for item in original_catalogs if item.name != "other"]
    )
    original_models = list(workspace.registered_models.list(include_browse=True))
    workspace.registered_models.list = lambda **_kwargs: iter(
        [item for item in original_models if item.catalog_name != "other"]
    )

    _verify(
        workspace,
        foreign_control_plane_proof=_binding_denied_proof(),
    )

    assert ("catalog", "other", None) not in workspace.grants.calls


def test_binding_denied_proof_rejects_model_visible_without_catalog() -> None:
    workspace = _workspace()
    original_catalogs = list(workspace.catalogs.list(include_browse=True))
    workspace.catalogs.list = lambda **_kwargs: iter(
        [item for item in original_catalogs if item.name != "other"]
    )

    with pytest.raises(RuntimeError, match="binding-denied.*models became visible"):
        _verify(
            workspace,
            foreign_control_plane_proof=_binding_denied_proof(),
        )


def test_control_plane_proof_cannot_be_constructed_or_forged_by_a_caller() -> None:
    with pytest.raises(TypeError):
        ControlPlaneForeignCatalogProof(  # type: ignore[call-arg]
            application_id=APPLICATION_ID,
            catalog=CATALOG,
            metastore_id="metastore-id",
            workspace_id="workspace-id",
            grant_audited_catalogs=frozenset({"other"}),
            binding_denied_catalogs=(),
        )

    forged = object.__new__(ControlPlaneForeignCatalogProof)
    for name, value in (
        ("application_id", APPLICATION_ID),
        ("catalog", CATALOG),
        ("metastore_id", "metastore-id"),
        ("workspace_id", "workspace-id"),
        ("grant_audited_catalogs", frozenset({"other"})),
        ("binding_denied_catalogs", ()),
    ):
        object.__setattr__(forged, name, value)

    with pytest.raises(RuntimeError, match="not issued by the auditor"):
        _verify(_workspace(), foreign_control_plane_proof=forged)

    issued = _foreign_proof()
    derived = copy.copy(issued)
    object.__setattr__(
        derived,
        "grant_audited_catalogs",
        frozenset({"other", "unreviewed"}),
    )
    with pytest.raises(RuntimeError, match="not issued by the auditor"):
        _verify(_workspace(), foreign_control_plane_proof=derived)

    object.__setattr__(
        issued,
        "grant_audited_catalogs",
        frozenset({"other", "unreviewed"}),
    )
    with pytest.raises(RuntimeError, match="not issued by the auditor"):
        _verify(_workspace(), foreign_control_plane_proof=issued)


def test_control_plane_proof_is_consumed_after_one_runtime_audit() -> None:
    proof = _foreign_proof()

    _verify(_workspace(), foreign_control_plane_proof=proof)

    with pytest.raises(RuntimeError, match="not issued by the auditor"):
        _verify(_workspace(), foreign_control_plane_proof=proof)


def test_consumed_control_plane_snapshot_cannot_be_changed_after_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proof = _foreign_proof()
    real_consume = verifier.consume_issued_control_plane_foreign_catalog_proof

    def consume_then_mutate(candidate: ControlPlaneForeignCatalogProof) -> object:
        snapshot = real_consume(candidate)
        object.__setattr__(candidate, "application_id", "mutated-runtime")
        object.__setattr__(candidate, "grant_audited_catalogs", frozenset())
        return snapshot

    monkeypatch.setattr(
        verifier,
        "consume_issued_control_plane_foreign_catalog_proof",
        consume_then_mutate,
    )
    workspace = _workspace()
    original = workspace.grants.get_effective

    def deny_foreign_catalog(*args: Any, **kwargs: Any) -> object:
        if args[:2] == ("catalog", "other"):
            raise PermissionDenied("runtime lacks USE CATALOG")
        return original(*args, **kwargs)

    workspace.grants.get_effective = deny_foreign_catalog

    _verify(workspace, foreign_control_plane_proof=proof)


@pytest.mark.parametrize(
    "overrides",
    [
        {"application_id": "other-runtime"},
        {"catalog": "other_mip"},
        {"metastore_id": "other-metastore"},
        {"workspace_id": "other-workspace"},
    ],
)
def test_control_plane_proof_must_match_runtime_boundary(overrides: dict[str, str]) -> None:
    with pytest.raises(RuntimeError, match="does not match"):
        _verify(_workspace(), foreign_control_plane_proof=_foreign_proof(**overrides))


def test_control_plane_proof_cannot_hide_uninventoried_foreign_catalog() -> None:
    with pytest.raises(RuntimeError, match="effective|forbidden"):
        _verify(
            _workspace({("catalog", "other"): {"BROWSE"}}),
            foreign_control_plane_proof=_foreign_proof(audited_catalogs=frozenset()),
        )


def test_control_plane_proof_rejects_runtime_config_host_workspace_drift() -> None:
    workspace = _workspace()
    workspace.config.workspace_id = "configured-other-workspace"

    with pytest.raises(RuntimeError, match="does not match.*workspace host"):
        _verify(workspace, foreign_control_plane_proof=_foreign_proof())


def test_effective_runtime_uc_boundary_requires_reviewed_schemas() -> None:
    workspace = _workspace()
    original = workspace.schemas.list
    workspace.schemas.list = lambda catalog, **kwargs: (
        iter([SimpleNamespace(name="gold", full_name="mip.gold")])
        if catalog == CATALOG
        else original(catalog, **kwargs)
    )

    with pytest.raises(RuntimeError, match="missing"):
        _verify(workspace)


def test_effective_runtime_uc_boundary_allows_source_proven_historical_model() -> None:
    upstream = "mip-mortgage-growth-supervisor-fedcba987654"
    historical, _table, source_hash = _contract(upstream=upstream, source_hash="b" * 64)
    workspace = _workspace(
        {("function", historical): {"ALL_PRIVILEGES"}},
        extra_models=[SimpleNamespace(full_name=historical, owner=APPLICATION_ID)],
    )

    _verify(
        workspace,
        registry_tags={
            MODEL: _provenance(),
            historical: _provenance(
                full_name=historical,
                source_hash=source_hash,
                upstream=upstream,
            ),
        },
    )


def test_effective_runtime_uc_boundary_rejects_historical_model_catalog_tuple_drift() -> None:
    historical, _table, _source_hash = _contract(source_hash="b" * 64)
    workspace = _workspace(
        extra_models=[SimpleNamespace(full_name=historical, owner=APPLICATION_ID)],
    )
    reviewed = next(
        item
        for item in workspace.registered_models.list(include_browse=True)
        if item.full_name == historical
    )
    reviewed.catalog_name = "other"

    with pytest.raises(RuntimeError, match="incomplete parent identity"):
        _verify(workspace)


def test_effective_runtime_uc_boundary_allows_historical_model_attested_to_prior_supervisor() -> (
    None
):
    historical_supervisor = "prior-supervisor-id"
    historical, _table, source_hash = _contract(
        source_hash="b" * 64,
        supervisor_id=historical_supervisor,
    )
    workspace = _workspace(
        {("function", historical): {"ALL_PRIVILEGES"}},
        extra_models=[SimpleNamespace(full_name=historical, owner=APPLICATION_ID)],
    )

    _verify(
        workspace,
        registry_tags={
            MODEL: _provenance(),
            historical: _provenance(
                full_name=historical,
                source_hash=source_hash,
                supervisor_id=historical_supervisor,
            ),
        },
    )


def test_effective_runtime_uc_boundary_rejects_candidate_attested_to_other_identity() -> None:
    with pytest.raises(RuntimeError, match="source-bound contract provenance|runtime identity"):
        _verify(
            _workspace(),
            registry_tags={
                MODEL: _provenance(
                    supervisor_id="other-supervisor-id",
                    runtime_application_id="other-runtime-client",
                )
            },
        )


def _previous_provenance(
    monkeypatch: pytest.MonkeyPatch,
    *,
    full_name: str,
    source_hash: str,
    upstream: str,
) -> dict[str, str]:
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY", PREVIOUS_SIGNING_KEY)
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY", PREVIOUS_VERIFY_KEY)
    tags = _provenance(
        full_name=full_name,
        source_hash=source_hash,
        upstream=upstream,
    )
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY", VERIFY_KEY)
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_PREVIOUS_VERIFY_KEY", PREVIOUS_VERIFY_KEY)
    return tags


def test_previous_epoch_retained_model_validates_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    historical_upstream = "mip-mortgage-growth-supervisor-fedcba987654"
    historical, _table, historical_hash = _contract(
        upstream=historical_upstream,
        source_hash="b" * 64,
        verify_key=PREVIOUS_VERIFY_KEY,
    )
    previous = _previous_provenance(
        monkeypatch,
        full_name=historical,
        source_hash=historical_hash,
        upstream=historical_upstream,
    )
    registry = _ModelRegistry({MODEL: _provenance(), historical: previous})
    workspace = _workspace(
        {("function", historical): {"ALL_PRIVILEGES"}},
        extra_models=[SimpleNamespace(full_name=historical, owner=APPLICATION_ID)],
    )

    _verify(workspace, model_registry=registry)

    assert registry.set_calls == []
    assert registry.tags[historical] == previous


def test_previous_epoch_retained_model_requires_epoch_bound_suffix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    historical_upstream = "mip-mortgage-growth-supervisor-fedcba987654"
    historical, _table, historical_hash = _contract(
        upstream=historical_upstream,
        source_hash="b" * 64,
    )
    previous = _previous_provenance(
        monkeypatch,
        full_name=historical,
        source_hash=historical_hash,
        upstream=historical_upstream,
    )
    registry = _ModelRegistry({MODEL: _provenance(), historical: previous})
    workspace = _workspace(
        {("function", historical): {"ALL_PRIVILEGES"}},
        extra_models=[SimpleNamespace(full_name=historical, owner=APPLICATION_ID)],
    )

    with pytest.raises(RuntimeError, match="source-bound contract provenance"):
        _verify(workspace, model_registry=registry)

    assert registry.set_calls == []


def test_effective_runtime_uc_boundary_supports_experiment_override() -> None:
    experiment = "/Shared/custom-runtime-experiment"
    model, table, source_hash = _contract(experiment=experiment)

    _verify(
        _workspace(model=model, table=table),
        model=model,
        experiment=experiment,
        registry_tags={
            model: _provenance(
                full_name=model,
                source_hash=source_hash,
                experiment=experiment,
            )
        },
    )


def test_effective_runtime_uc_boundary_rejects_unproven_model() -> None:
    with pytest.raises(RuntimeError, match="provenance"):
        _verify(
            _workspace(),
            registry_tags={MODEL: _provenance(source_hash="f" * 64)},
        )


def test_effective_runtime_uc_boundary_rejects_padded_reviewed_model_name() -> None:
    workspace = _workspace()
    models = list(workspace.registered_models.list(include_browse=True))
    reviewed = next(item for item in models if item.full_name == MODEL)
    reviewed.full_name = f" {MODEL}"
    workspace.registered_models.list = lambda **_kwargs: iter(models)

    with pytest.raises(RuntimeError, match="noncanonical text"):
        _verify(workspace)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_name", "gold"),
        ("name", "unrelated_model"),
        ("name", None),
        ("full_name", None),
    ],
)
def test_effective_runtime_uc_boundary_requires_complete_reviewed_model_parent_tuple(
    field: str,
    value: object,
) -> None:
    workspace = _workspace(
        {
            ("table", f"mip.audit.{TABLE}"): set(),
            ("function", MODEL): set(),
        }
    )
    reviewed = next(
        item
        for item in workspace.registered_models.list(include_browse=True)
        if item.full_name == MODEL
    )
    setattr(reviewed, field, value)

    with pytest.raises(RuntimeError, match="parent identity|missing|forbidden"):
        _verify(workspace)


@pytest.mark.parametrize("privileges", [set(), {"SELECT"}])
def test_effective_runtime_uc_boundary_rejects_lookalike_table(
    privileges: set[str],
) -> None:
    lookalike = f"mip.audit.{TABLE}_extra"
    workspace = _workspace({("table", lookalike): privileges})
    workspace.tables.list = lambda catalog, schema, **_kwargs: iter(
        [
            SimpleNamespace(
                name=f"{TABLE}_extra",
                full_name=lookalike,
                catalog_name=CATALOG,
                schema_name="audit",
                owner=APPLICATION_ID,
            )
        ]
        if (catalog, schema) == (CATALOG, "audit")
        else []
    )

    with pytest.raises(RuntimeError, match="effective owner of forbidden table"):
        _verify(workspace)


def test_effective_runtime_uc_boundary_rejects_padded_reviewed_table_name() -> None:
    workspace = _workspace()
    tables = list(workspace.tables.list(CATALOG, "audit", include_browse=True))
    reviewed = next(item for item in tables if item.name == TABLE)
    reviewed.name = f"{TABLE} "

    with pytest.raises(RuntimeError, match="noncanonical text"):
        _verify(workspace)


@pytest.mark.parametrize(
    "full_name",
    [
        f"other.audit.{TABLE}",
        f"{CATALOG}.gold.{TABLE}",
    ],
)
def test_effective_runtime_uc_boundary_rejects_reviewed_table_parent_drift(
    full_name: str,
) -> None:
    workspace = _workspace(
        {
            ("table", f"mip.audit.{TABLE}"): set(),
            ("function", MODEL): set(),
        }
    )
    reviewed = next(
        item
        for item in workspace.tables.list(CATALOG, "audit", include_browse=True)
        if item.name == TABLE
    )
    reviewed.full_name = full_name

    with pytest.raises(RuntimeError, match="invalid parent identity"):
        _verify(workspace)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("catalog_name", "other"),
        ("schema_name", "gold"),
        ("full_name", None),
    ],
)
def test_effective_runtime_uc_boundary_requires_complete_reviewed_table_parent_tuple(
    field: str,
    value: object,
) -> None:
    workspace = _workspace(
        {
            ("table", f"mip.audit.{TABLE}"): set(),
            ("function", MODEL): set(),
        }
    )
    reviewed = next(
        item
        for item in workspace.tables.list(CATALOG, "audit", include_browse=True)
        if item.name == TABLE
    )
    setattr(reviewed, field, value)

    with pytest.raises(RuntimeError, match="incomplete parent identity"):
        _verify(workspace)


def test_effective_runtime_uc_boundary_requires_exact_artifact_owners() -> None:
    with pytest.raises(RuntimeError, match="ownership"):
        _verify(_workspace(table_owner="human@example.com"))
    with pytest.raises(RuntimeError, match="ownership"):
        _verify(_workspace(model_owner="human@example.com"))


@pytest.mark.parametrize(
    "securable",
    ["catalog", "schema", "function", "table", "volume", "unrelated_model"],
)
def test_effective_runtime_uc_boundary_rejects_other_mip_ownership(
    securable: str,
) -> None:
    workspace = _workspace()
    if securable == "catalog":
        catalogs = list(workspace.catalogs.list(include_browse=True))
        next(item for item in catalogs if item.name == CATALOG).owner = APPLICATION_ID
        workspace.catalogs.list = lambda **_kwargs: iter(catalogs)
    elif securable == "schema":
        next(workspace.schemas.list(CATALOG)).owner = APPLICATION_ID
    elif securable == "function":
        next(workspace.functions.list(CATALOG, "gold")).owner = APPLICATION_ID
    elif securable == "table":
        next(workspace.tables.list(CATALOG, "gold")).owner = APPLICATION_ID
    elif securable == "volume":
        next(workspace.volumes.list(CATALOG, "audit")).owner = APPLICATION_ID
    else:
        models = list(workspace.registered_models.list(include_browse=True))
        next(
            item for item in models if item.full_name == "mip.audit.unrelated_model"
        ).owner = APPLICATION_ID
        workspace.registered_models.list = lambda **_kwargs: iter(models)

    with pytest.raises(RuntimeError, match="effective owner of forbidden"):
        _verify(workspace)


@pytest.mark.parametrize("owner", ["runtime-owners", "owner-group-id"])
def test_effective_runtime_uc_boundary_rejects_group_derived_mip_ownership(
    owner: str,
) -> None:
    workspace = _workspace()
    workspace.current_user.me = lambda: SimpleNamespace(
        user_name=APPLICATION_ID,
        application_id=APPLICATION_ID,
        id="runtime-scim-id",
        groups=[SimpleNamespace(value="owner-group-id", display="runtime-owners")],
    )
    catalogs = list(workspace.catalogs.list(include_browse=True))
    next(item for item in catalogs if item.name == CATALOG).owner = owner
    workspace.catalogs.list = lambda **_kwargs: iter(catalogs)

    with pytest.raises(RuntimeError, match="effective owner of forbidden catalog"):
        _verify(workspace)


def test_effective_runtime_uc_boundary_rejects_scim_id_ownership_alias() -> None:
    workspace = _workspace()
    catalogs = list(workspace.catalogs.list(include_browse=True))
    next(item for item in catalogs if item.name == CATALOG).owner = "runtime-scim-id"
    workspace.catalogs.list = lambda **_kwargs: iter(catalogs)

    with pytest.raises(RuntimeError, match="effective owner of forbidden catalog"):
        _verify(workspace)


@pytest.mark.parametrize(
    "securable",
    [
        "system_catalog",
        "samples_catalog",
        "system_schema",
        "samples_schema",
        "system_function",
        "system_table",
        "samples_table",
        "samples_volume",
        "samples_model",
        "mip_information_schema",
        "mip_information_schema_table",
    ],
)
def test_effective_runtime_uc_boundary_source_binds_platform_owners(
    securable: str,
) -> None:
    workspace = _workspace()
    if securable in {"system_catalog", "samples_catalog"}:
        catalogs = list(workspace.catalogs.list(include_browse=True))
        name = securable.removesuffix("_catalog")
        next(item for item in catalogs if item.name == name).owner = APPLICATION_ID
        workspace.catalogs.list = lambda **_kwargs: iter(catalogs)
    elif securable == "system_schema":
        next(workspace.schemas.list("system")).owner = APPLICATION_ID
    elif securable == "samples_schema":
        next(workspace.schemas.list("samples")).owner = APPLICATION_ID
    elif securable == "system_function":
        next(workspace.functions.list("system", "ai")).owner = APPLICATION_ID
    elif securable == "system_table":
        next(workspace.tables.list("system", "billing")).owner = APPLICATION_ID
    elif securable == "samples_table":
        next(workspace.tables.list("samples", "tpch")).owner = APPLICATION_ID
    elif securable == "samples_volume":
        next(workspace.volumes.list("samples", "tpch")).owner = APPLICATION_ID
    elif securable == "samples_model":
        models = list(workspace.registered_models.list(include_browse=True))
        next(model for model in models if _catalog(model) == "samples").owner = APPLICATION_ID
        workspace.registered_models.list = lambda **_kwargs: iter(models)
    elif securable == "mip_information_schema":
        next(
            schema
            for schema in workspace.schemas.list(CATALOG)
            if schema.name == "information_schema"
        ).owner = APPLICATION_ID
    else:
        next(workspace.tables.list(CATALOG, "information_schema")).owner = APPLICATION_ID

    with pytest.raises(RuntimeError, match="System user"):
        _verify(workspace)


def test_effective_runtime_uc_boundary_anchors_data_quality_platform_owner() -> None:
    workspace = _workspace()
    schema = next(
        item for item in workspace.schemas.list("system") if item.name == "data_quality_monitoring"
    )
    schema.owner = APPLICATION_ID
    with pytest.raises(RuntimeError, match="invalid platform owner"):
        _verify(workspace)

    workspace = _workspace()
    next(
        workspace.tables.list("system", "data_quality_monitoring")
    ).owner = "different-platform-principal"
    with pytest.raises(RuntimeError, match="child owner drifted"):
        _verify(workspace)


def test_effective_runtime_uc_boundary_source_binds_internal_catalog() -> None:
    with pytest.raises(RuntimeError, match="effective UC boundary"):
        _verify(_workspace({("catalog", "__databricks_internal"): {"BROWSE"}}))

    workspace = _workspace()
    catalogs = list(workspace.catalogs.list(include_browse=True))
    internal = next(item for item in catalogs if item.name == "__databricks_internal")
    internal.owner = "lookalike-owner"
    workspace.catalogs.list = lambda **_kwargs: iter(catalogs)
    with pytest.raises(RuntimeError, match="internal catalog.*fixed platform identity"):
        _verify(workspace)


@pytest.mark.parametrize(
    ("securable", "full_name", "tamper"),
    [
        ("metastore", "metastore-id", "principal"),
        ("table", "other.information_schema.tables", "principal"),
        ("table", "mip.information_schema.tables", "principal"),
        ("table", "mip.information_schema.tables", "inheritance"),
        ("schema", "samples.tpch", "inheritance"),
        ("function", "system.ai.ai_classify", "inheritance"),
    ],
)
def test_effective_runtime_uc_boundary_rejects_laundered_builtin_sources(
    securable: str,
    full_name: str,
    tamper: str,
) -> None:
    workspace = _workspace()
    original = workspace.grants.get_effective

    def altered(*args: Any, **kwargs: Any) -> object:
        response = original(*args, **kwargs)
        if args[:2] != (securable, full_name):
            return response
        for assignment in response.privilege_assignments:
            if tamper == "principal":
                assignment.principal = APPLICATION_ID
            else:
                for privilege in assignment.privileges:
                    privilege.inherited_from_type = "CATALOG"
                    privilege.inherited_from_name = "wrong-parent"
        return response

    workspace.grants.get_effective = altered
    with pytest.raises(RuntimeError, match="sources"):
        _verify(workspace)


@pytest.mark.parametrize(
    ("securable", "full_name"),
    [
        ("table", f"mip.audit.{TABLE}"),
        ("function", MODEL),
    ],
)
def test_effective_runtime_uc_boundary_rejects_laundered_owned_artifact_sources(
    securable: str,
    full_name: str,
) -> None:
    workspace = _workspace()
    original = workspace.grants.get_effective

    def altered(*args: Any, **kwargs: Any) -> object:
        response = original(*args, **kwargs)
        if args[:2] == (securable, full_name):
            for assignment in response.privilege_assignments:
                assignment.principal = "account users"
                for privilege in assignment.privileges:
                    privilege.inherited_from_type = "CATALOG"
                    privilege.inherited_from_name = CATALOG
        return response

    workspace.grants.get_effective = altered
    with pytest.raises(RuntimeError, match="direct runtime ownership"):
        _verify(workspace)


def test_effective_runtime_uc_boundary_rejects_new_or_reowned_system_model() -> None:
    workspace = _workspace()
    models = list(workspace.registered_models.list(include_browse=True))
    models.append(
        SimpleNamespace(
            full_name="system.ai.unreviewed_new_model",
            catalog_name="system",
            schema_name="ai",
            name="unreviewed_new_model",
            owner="System user",
        )
    )
    workspace.registered_models.list = lambda **_kwargs: iter(models)
    with pytest.raises(RuntimeError, match="unreviewed system.ai"):
        _verify(workspace)

    workspace = _workspace()
    models = list(workspace.registered_models.list(include_browse=True))
    system_model = next(model for model in models if _catalog(model) == "system")
    system_model.owner = "human@example.com"
    workspace.registered_models.list = lambda **_kwargs: iter(models)
    with pytest.raises(RuntimeError, match="System user"):
        _verify(workspace)


@pytest.mark.parametrize(
    "full_name",
    [
        "system.ai.databricks-claude-opus-5",
        "system.ai.databricks-gemini-3-5-flash-lite",
        "system.ai.databricks-gemini-3-6-flash",
        "system.ai.databricks-kimi-k3",
    ],
)
def test_effective_runtime_uc_boundary_accepts_reviewed_live_system_model_shape(
    full_name: str,
) -> None:
    workspace = _workspace(
        {("function", full_name): {"EXECUTE"}},
        extra_models=[
            SimpleNamespace(
                full_name=full_name,
                catalog_name="system",
                schema_name="ai",
                owner="System user",
            )
        ],
    )

    _verify(workspace)


@pytest.mark.parametrize(
    ("tamper", "match"),
    [
        ("owner", "System user"),
        ("source", "sources"),
    ],
)
def test_effective_runtime_uc_boundary_rejects_tampered_kimi_k3_platform_shape(
    tamper: str,
    match: str,
) -> None:
    full_name = "system.ai.databricks-kimi-k3"
    model = SimpleNamespace(
        full_name=full_name,
        catalog_name="system",
        schema_name="ai",
        owner="System user",
    )
    workspace = _workspace(
        {("function", full_name): {"EXECUTE"}},
        extra_models=[model],
    )

    if tamper == "owner":
        model.owner = "human@example.com"
    else:
        original = workspace.grants.get_effective

        def altered(*args: Any, **kwargs: Any) -> object:
            response = original(*args, **kwargs)
            if args[:2] == ("function", full_name):
                for assignment in response.privilege_assignments:
                    for privilege in assignment.privileges:
                        privilege.inherited_from_type = "CATALOG"
                        privilege.inherited_from_name = "system"
            return response

        workspace.grants.get_effective = altered

    with pytest.raises(RuntimeError, match=match):
        _verify(workspace)


def _catalog(model: object) -> str:
    return str(getattr(model, "catalog_name", "") or "")
