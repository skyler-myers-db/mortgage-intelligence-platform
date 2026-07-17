from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Any

import pytest

from backend.agents.gateway_contract import (
    DEFAULT_GATEWAY_AGENT_EXPERIMENT,
)
from backend.services.ai_gateway_proof_attestation import derive_gateway_proof_verify_key
from tools.databricks import verify_agent_runtime_uc_grants as verifier
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
SIGNING_KEY = base64.urlsafe_b64encode(b"u" * 32).decode("ascii").rstrip("=")
VERIFY_KEY = derive_gateway_proof_verify_key(SIGNING_KEY)
PREVIOUS_SIGNING_KEY = base64.urlsafe_b64encode(b"v" * 32).decode("ascii").rstrip("=")
PREVIOUS_VERIFY_KEY = derive_gateway_proof_verify_key(PREVIOUS_SIGNING_KEY)


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
        model_name=MODEL_FAMILY,
        experiment_name=experiment,
        inference_schema="audit",
        inference_table_prefix=TABLE_PREFIX,
        attestation_verify_key=verify_key,
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
        if full_name == "system.ai.meta_llama_3_70b" or full_name in {
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

    def search_model_versions(self, query: str) -> list[object]:
        prefix = "name='"
        assert query.startswith(prefix) and query.endswith("'")
        name = query[len(prefix) : -1]
        model_tags = self.tags.get(name)
        return (
            []
            if model_tags is None
            else [
                SimpleNamespace(
                    name=name,
                    version="1",
                    source="models:/m-reviewed-proxy",
                    tags=model_tags,
                )
            ]
        )

    def set_model_version_tag(self, name: str, _version: str, key: str, value: str) -> None:
        self.set_calls.append((name, _version, key, value))
        self.tags[name][key] = value

    def get_model_version(self, name: str, version: str) -> object:
        return SimpleNamespace(name=name, version=version, tags=dict(self.tags[name]))


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
            SimpleNamespace(name="gold", full_name="mip.gold"),
            SimpleNamespace(name="audit", full_name="mip.audit"),
            SimpleNamespace(name="ref", full_name="mip.ref"),
            SimpleNamespace(
                name="information_schema",
                full_name=f"{CATALOG}.information_schema",
            ),
        ],
        "other": [
            SimpleNamespace(name="sandbox", full_name="other.sandbox"),
            SimpleNamespace(
                name="information_schema",
                full_name="other.information_schema",
            ),
        ],
        "system": [
            SimpleNamespace(name="ai", full_name="system.ai"),
            SimpleNamespace(
                name="data_quality_monitoring",
                full_name="system.data_quality_monitoring",
            ),
            SimpleNamespace(name="information_schema", full_name="system.information_schema"),
            SimpleNamespace(name="billing", full_name="system.billing"),
        ],
        "samples": [
            SimpleNamespace(name="tpch", full_name="samples.tpch"),
            SimpleNamespace(
                name="information_schema",
                full_name="samples.information_schema",
            ),
        ],
    }
    functions: dict[tuple[str, str], list[object]] = {
        (CATALOG, "gold"): [
            SimpleNamespace(name=name, full_name=f"mip.gold.{name}")
            for name in (*sorted(verifier.ALLOWED_FUNCTIONS), "fn_unreviewed")
        ],
        (CATALOG, "audit"): [],
        (CATALOG, "ref"): [],
        (CATALOG, "information_schema"): [],
        ("other", "sandbox"): [
            SimpleNamespace(name="fn_secret", full_name="other.sandbox.fn_secret")
        ],
        ("other", "information_schema"): [],
        ("system", "ai"): [SimpleNamespace(name="ai_classify", full_name="system.ai.ai_classify")],
        ("system", "data_quality_monitoring"): [],
        ("system", "information_schema"): [],
        ("system", "billing"): [],
        ("samples", "tpch"): [],
        ("samples", "information_schema"): [],
    }
    tables: dict[tuple[str, str], list[object]] = {
        (CATALOG, "gold"): [
            SimpleNamespace(name="borrower_360", full_name="mip.gold.borrower_360")
        ],
        (CATALOG, "audit"): [
            SimpleNamespace(
                name=table,
                full_name=f"mip.audit.{table}",
                owner=table_owner,
            ),
            SimpleNamespace(name="action_audit", full_name="mip.audit.action_audit"),
        ],
        (CATALOG, "ref"): [SimpleNamespace(name="offer_rules", full_name="mip.ref.offer_rules")],
        (CATALOG, "information_schema"): [
            *(
                SimpleNamespace(
                    name=name,
                    full_name=f"{CATALOG}.information_schema.{name}",
                )
                for name in sorted(verifier._CATALOG_INFORMATION_SCHEMA_TABLES)
            ),
            SimpleNamespace(
                name="future_metadata",
                full_name=f"{CATALOG}.information_schema.future_metadata",
            ),
        ],
        ("other", "sandbox"): [SimpleNamespace(name="secret", full_name="other.sandbox.secret")],
        ("other", "information_schema"): [
            SimpleNamespace(
                name="tables",
                full_name="other.information_schema.tables",
            )
        ],
        ("system", "ai"): [],
        ("system", "data_quality_monitoring"): [],
        ("system", "information_schema"): [
            SimpleNamespace(name="tables", full_name="system.information_schema.tables")
        ],
        ("system", "billing"): [SimpleNamespace(name="usage", full_name="system.billing.usage")],
        ("samples", "tpch"): [SimpleNamespace(name="orders", full_name="samples.tpch.orders")],
        ("samples", "information_schema"): [
            SimpleNamespace(
                name="tables",
                full_name="samples.information_schema.tables",
            )
        ],
    }
    volumes: dict[tuple[str, str], list[object]] = {
        (CATALOG, "gold"): [],
        (CATALOG, "audit"): [SimpleNamespace(name="proofs", full_name="mip.audit.proofs")],
        (CATALOG, "ref"): [],
        (CATALOG, "information_schema"): [],
        ("other", "sandbox"): [SimpleNamespace(name="private", full_name="other.sandbox.private")],
        ("other", "information_schema"): [],
        ("system", "ai"): [],
        ("system", "data_quality_monitoring"): [],
        ("system", "information_schema"): [],
        ("system", "billing"): [],
        ("samples", "tpch"): [SimpleNamespace(name="datasets", full_name="samples.tpch.datasets")],
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
    grants = _Grants(values)
    return SimpleNamespace(
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
            )
        ),
        catalogs=SimpleNamespace(
            list=lambda **_kwargs: iter(
                [
                    SimpleNamespace(name=CATALOG),
                    SimpleNamespace(name="other"),
                    SimpleNamespace(name="system", owner="System user"),
                    SimpleNamespace(name="samples", owner="System user"),
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


def _verify(
    workspace: Any,
    *,
    model: str = MODEL,
    experiment: str = DEFAULT_GATEWAY_AGENT_EXPERIMENT,
    registry_tags: dict[str, dict[str, str]] | None = None,
    model_registry: Any | None = None,
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
        model_registry=model_registry or _ModelRegistry(registry_tags or {model: _provenance()}),
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


def test_effective_runtime_uc_boundary_is_public_key_only_and_never_mutates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _ModelRegistry({MODEL: _provenance()})
    monkeypatch.delenv("MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY")
    monkeypatch.delenv("MIP_ALLOW_RUNTIME_MODEL_ATTESTATION_SIGNING")

    _verify(_workspace(), model_registry=registry)

    assert registry.set_calls == []


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


def test_effective_runtime_uc_boundary_rejects_lookalike_table() -> None:
    lookalike = f"mip.audit.{TABLE}_extra"
    workspace = _workspace({("table", lookalike): {"SELECT"}})
    workspace.tables.list = lambda catalog, schema, **_kwargs: iter(
        [
            SimpleNamespace(
                name=f"{TABLE}_extra",
                full_name=lookalike,
                owner=APPLICATION_ID,
            )
        ]
        if (catalog, schema) == (CATALOG, "audit")
        else []
    )

    with pytest.raises(RuntimeError, match="effective"):
        _verify(workspace)


def test_effective_runtime_uc_boundary_requires_exact_artifact_owners() -> None:
    with pytest.raises(RuntimeError, match="ownership"):
        _verify(_workspace(table_owner="human@example.com"))
    with pytest.raises(RuntimeError, match="ownership"):
        _verify(_workspace(model_owner="human@example.com"))


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


def _catalog(model: object) -> str:
    return str(getattr(model, "catalog_name", "") or "")
