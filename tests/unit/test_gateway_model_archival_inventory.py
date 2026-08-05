"""Authoritative inventory tests for governed Gateway model archival."""

from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Any

import pytest
from databricks.sdk.errors import ResourceDoesNotExist

from backend.services.ai_gateway_proof_attestation import derive_gateway_proof_verify_key
from tools.databricks.gateway_model_archival_inventory import (
    inventory_gateway_model_archive,
    inventory_gateway_serving,
)
from tools.databricks.gateway_model_attestation import sign_gateway_model_contract
from tools.databricks.gateway_resource_identity import gateway_experiment_name

_RUNTIME_ID = "runtime-application-id"
_CATALOG = "mip"
_SCHEMA = "audit"
_MODEL_FAMILY = f"{_CATALOG}.{_SCHEMA}.mortgage_growth_supervisor_proxy"
_MODEL = f"{_MODEL_FAMILY}_aaaaaaaaaaaa"
_OTHER_MODEL = f"{_MODEL_FAMILY}_bbbbbbbbbbbb"
_TABLE_PREFIX = "mip_agent_gateway_growth_agent"
_TABLE_FAMILY = f"{_CATALOG}.{_SCHEMA}.{_TABLE_PREFIX}_aaaaaaaaaaaa"
_PAYLOAD = f"{_TABLE_FAMILY}_payload"
_EXPERIMENT_BASE = "mip-agent-runtime-gateway-proxy"
_EXPERIMENT_ID = "experiment-id"
_SOURCE = "models:/m-reviewed-gateway"
_SIGNING_KEY = base64.urlsafe_b64encode(b"t" * 32).decode("ascii").rstrip("=")
_VERIFY_KEY = derive_gateway_proof_verify_key(_SIGNING_KEY)
_PREVIOUS_SIGNING_KEY = base64.urlsafe_b64encode(b"w" * 32).decode("ascii").rstrip("=")
_PREVIOUS_VERIFY_KEY = derive_gateway_proof_verify_key(_PREVIOUS_SIGNING_KEY)


@pytest.fixture(autouse=True)
def _attestation_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIP_ALLOW_RUNTIME_MODEL_ATTESTATION_SIGNING", "1")
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY", _SIGNING_KEY)
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY", _VERIFY_KEY)
    monkeypatch.delenv("MIP_GATEWAY_MODEL_ATTESTATION_PREVIOUS_VERIFY_KEY", raising=False)


def _tags(
    *,
    full_name: str = _MODEL,
    model_source: str = _SOURCE,
) -> dict[str, str]:
    return sign_gateway_model_contract(
        full_name=full_name,
        model_source=model_source,
        source_hash="a" * 64,
        supervisor_id="supervisor-id",
        supervisor_endpoint_id="supervisor-endpoint-id",
        upstream_endpoint="upstream-endpoint",
        runtime_application_id=_RUNTIME_ID,
        model_family=_MODEL_FAMILY,
        experiment_base=_EXPERIMENT_BASE,
        catalog=_CATALOG,
        genie_space_id="genie-space-id",
        inference_schema=_SCHEMA,
        inference_table_prefix=_TABLE_PREFIX,
    )


def _version(
    *,
    name: str = _MODEL,
    version: str = "1",
    source: str = _SOURCE,
    tags: dict[str, str] | None = None,
) -> object:
    return SimpleNamespace(
        name=name,
        version=version,
        source=source,
        status="READY",
        tags=tags or _tags(full_name=name, model_source=source),
    )


class _Registry:
    def __init__(self, versions: dict[str, list[Any]] | None = None) -> None:
        self.versions = versions or {_MODEL: [_version()]}

    def search_model_versions(
        self,
        *,
        filter_string: str,
        max_results: int,
        page_token: str | None,
    ) -> list[Any]:
        assert max_results == 1000
        assert page_token is None
        name = filter_string.removeprefix("name='").removesuffix("'")
        return self.versions.get(name, [])

    def get_model_version(self, name: str, version: str) -> Any:
        return next(
            item
            for item in self.versions[name]
            if str(item.version) == version
        )


class _Tracking:
    def __init__(self) -> None:
        self.logged = {
            "m-reviewed-gateway": SimpleNamespace(
                model_id="m-reviewed-gateway",
                source_run_id="source-run-id",
                experiment_id=_EXPERIMENT_ID,
            )
        }
        self.runs = {
            "source-run-id": SimpleNamespace(
                info=SimpleNamespace(
                    run_id="source-run-id",
                    experiment_id=_EXPERIMENT_ID,
                )
            )
        }
        self.experiment = SimpleNamespace(
            experiment_id=_EXPERIMENT_ID,
            name=gateway_experiment_name(
                base_experiment_name=_EXPERIMENT_BASE,
                contract_hash="aaaaaaaaaaaa",
                runtime_application_id=_RUNTIME_ID,
            ),
            artifact_location="dbfs:/experiments/experiment-id",
            lifecycle_stage="active",
            tags={"mlflow.ownerEmail": _RUNTIME_ID},
        )

    def get_logged_model(self, model_id: str) -> object:
        return self.logged[model_id]

    def get_run(self, run_id: str) -> object:
        return self.runs[run_id]

    def get_experiment(self, _experiment_id: str) -> object:
        return self.experiment


class _Tables:
    def __init__(self, *, present: bool = True, owner: str = _RUNTIME_ID) -> None:
        self.values: dict[str, object] = {}
        if present:
            self.values[_PAYLOAD] = SimpleNamespace(
                name=_PAYLOAD.rsplit(".", 1)[-1],
                full_name=_PAYLOAD,
                table_id="table-id",
                owner=owner,
                storage_location="s3://bucket/payload",
                data_source_format="DELTA",
            )
        self.extra: list[object] = []

    def list(self, *_args: Any, **_kwargs: Any) -> list[object]:
        return [*self.values.values(), *self.extra]

    def get(self, full_name: str, **_kwargs: Any) -> object:
        try:
            return self.values[full_name]
        except KeyError as exc:
            raise ResourceDoesNotExist("missing") from exc


class _RegisteredModels:
    def __init__(self, *, owner: str = _RUNTIME_ID, include_other: bool = False) -> None:
        self.target = SimpleNamespace(full_name=_MODEL, owner=owner)
        self.other = (
            SimpleNamespace(full_name=_OTHER_MODEL, owner=_RUNTIME_ID)
            if include_other
            else None
        )

    def get(self, _name: str) -> object:
        return self.target

    def list(self, **_kwargs: Any) -> list[object]:
        return [self.target, *([] if self.other is None else [self.other])]


def _workspace(
    *,
    tables: _Tables | None = None,
    registered_models: _RegisteredModels | None = None,
    endpoints: list[Any] | None = None,
) -> Any:
    endpoint_values = {
        endpoint.name: endpoint
        for endpoint in (endpoints or [])
    }
    return SimpleNamespace(
        registered_models=registered_models or _RegisteredModels(),
        tables=tables or _Tables(),
        experiments=SimpleNamespace(
            get_permissions=lambda _experiment_id: {
                "access_control_list": [
                    {
                        "service_principal_name": _RUNTIME_ID,
                        "all_permissions": [
                            {
                                "permission_level": "CAN_MANAGE",
                                "inherited": False,
                                "inherited_from_object": [],
                            }
                        ],
                    }
                ]
            }
        ),
        serving_endpoints=SimpleNamespace(
            list=lambda: [
                SimpleNamespace(name=name)
                for name in endpoint_values
            ],
            get=lambda name: endpoint_values[name],
        ),
    )


def _inventory(
    workspace: Any,
    *,
    registry: _Registry | None = None,
    tracking: _Tracking | None = None,
    delta_version: str = "7",
) -> Any:
    return inventory_gateway_model_archive(
        workspace,
        registry or _Registry(),
        tracking or _Tracking(),
        model_name=_MODEL,
        runtime_application_id=_RUNTIME_ID,
        model_family=_MODEL_FAMILY,
        experiment_base=_EXPERIMENT_BASE,
        catalog=_CATALOG,
        inference_schema=_SCHEMA,
        inference_table_prefix=_TABLE_PREFIX,
        delta_version_resolver=lambda _table: delta_version,
    )


def test_archive_inventory_binds_exact_source_experiment_table_and_absence() -> None:
    result = _inventory(_workspace())

    assert result.model_name == _MODEL
    assert result.model_owner == _RUNTIME_ID
    assert result.versions[0]["attestation_epoch"] == "current"
    assert result.logged_model_ids == ("m-reviewed-gateway",)
    assert result.source_run_ids == ("source-run-id",)
    assert result.experiment_id == _EXPERIMENT_ID
    assert result.experiment_owner == _RUNTIME_ID
    assert result.inference_tables == (
        {
            "full_name": _PAYLOAD,
            "table_id": "table-id",
            "owner": _RUNTIME_ID,
            "storage_location": "s3://bucket/payload",
            "data_source_format": "DELTA",
            "delta_latest_version": "7",
        },
    )
    assert result.expected_absent_inference_tables == (
        f"{_PAYLOAD}_assessment_logs",
        f"{_PAYLOAD}_request_logs",
    )
    assert result.serving_references == ()


def test_archive_inventory_allows_explicitly_absent_optional_tables() -> None:
    result = _inventory(_workspace(tables=_Tables(present=False)))

    assert result.inference_tables == ()
    assert result.expected_absent_inference_tables == (
        _PAYLOAD,
        f"{_PAYLOAD}_assessment_logs",
        f"{_PAYLOAD}_request_logs",
    )


def test_archive_inventory_requires_exactly_one_model_version() -> None:
    registry = _Registry(
        {
            _MODEL: [
                _version(),
                _version(version="2"),
            ]
        }
    )

    with pytest.raises(RuntimeError, match="exactly one model version"):
        _inventory(_workspace(), registry=registry)


def test_archive_inventory_rejects_tampered_signature_and_owner() -> None:
    tags = _tags()
    signature_key = next(key for key in tags if key.endswith("_signature"))
    tags[signature_key] = "A" * len(tags[signature_key])
    with pytest.raises(RuntimeError, match="attestation signature is invalid"):
        _inventory(
            _workspace(),
            registry=_Registry({_MODEL: [_version(tags=tags)]}),
        )

    with pytest.raises(RuntimeError, match="not runtime-owned"):
        _inventory(
            _workspace(
                registered_models=_RegisteredModels(owner="governance@example.com")
            )
        )


def test_archive_inventory_accepts_trusted_previous_attestation_epoch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY",
        _PREVIOUS_SIGNING_KEY,
    )
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY", _PREVIOUS_VERIFY_KEY)
    previous_tags = _tags()
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY", _SIGNING_KEY)
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY", _VERIFY_KEY)
    monkeypatch.setenv(
        "MIP_GATEWAY_MODEL_ATTESTATION_PREVIOUS_VERIFY_KEY",
        _PREVIOUS_VERIFY_KEY,
    )

    result = _inventory(
        _workspace(),
        registry=_Registry({_MODEL: [_version(tags=previous_tags)]}),
    )

    assert result.versions[0]["attestation_epoch"] == "previous"


def test_archive_inventory_rejects_table_owner_variant_and_delta_drift() -> None:
    with pytest.raises(RuntimeError, match="inference-table identity is invalid"):
        _inventory(_workspace(tables=_Tables(owner="other@example.com")))

    unexpected = _Tables()
    unexpected.extra.append(
        SimpleNamespace(
            name=f"{_TABLE_PREFIX}_aaaaaaaaaaaa_payload_unreviewed",
            full_name=f"{_PAYLOAD}_unreviewed",
        )
    )
    with pytest.raises(RuntimeError, match="unexpected same-allocation table"):
        _inventory(_workspace(tables=unexpected))

    with pytest.raises(RuntimeError, match="Delta version is invalid"):
        _inventory(_workspace(), delta_version="")


def test_archive_inventory_rejects_cross_model_source_run_experiment_sharing() -> None:
    registry = _Registry(
        {
            _MODEL: [_version()],
            _OTHER_MODEL: [
                _version(
                    name=_OTHER_MODEL,
                    source=_SOURCE,
                    tags=_tags(full_name=_OTHER_MODEL),
                )
            ],
        }
    )

    with pytest.raises(RuntimeError, match="shared by another model"):
        _inventory(
            _workspace(
                registered_models=_RegisteredModels(include_other=True),
            ),
            registry=registry,
        )


def test_archive_inventory_rejects_semantic_duplicate_acl_principal() -> None:
    workspace = _workspace()
    workspace.experiments.get_permissions = lambda _experiment_id: {
        "access_control_list": [
            {
                "service_principal_name": _RUNTIME_ID,
                "all_permissions": [{"permission_level": "CAN_MANAGE"}],
            },
            {
                "service_principal_name": _RUNTIME_ID,
                "display_name": "same runtime principal",
                "all_permissions": [{"permission_level": "CAN_READ"}],
            },
        ]
    }

    with pytest.raises(RuntimeError, match="ACL contains duplicate principals"):
        _inventory(workspace)


def _endpoint(
    *,
    current: object | None = None,
    pending: object | None = None,
    inference: object | None = None,
) -> object:
    return SimpleNamespace(
        name="endpoint",
        id="endpoint-id",
        creator="creator",
        state={"ready": "READY"},
        config=current,
        pending_config=pending,
        ai_gateway=SimpleNamespace(inference_table_config=inference),
    )


def _config(
    *,
    entities: list[object] | None = None,
    models: list[object] | None = None,
    routes: list[object] | None = None,
    version: str = "1",
) -> object:
    return SimpleNamespace(
        config_version=version,
        served_entities=entities or [],
        served_models=models or [],
        traffic_config=SimpleNamespace(routes=routes or []),
    )


@pytest.mark.parametrize("phase", ["current", "pending"])
def test_serving_inventory_detects_current_and_pending_model_references(
    phase: str,
) -> None:
    entity = SimpleNamespace(
        name="target",
        entity_name=_MODEL,
        entity_version="1",
    )
    config = _config(entities=[entity])
    endpoint = _endpoint(
        current=config if phase == "current" else _config(),
        pending=config if phase == "pending" else None,
    )

    _inventory_snapshot, references = inventory_gateway_serving(
        _workspace(endpoints=[endpoint]),
        model_name=_MODEL,
        inference_table_family=_TABLE_FAMILY,
    )

    assert any(reference["phase"] == phase for reference in references)
    assert any(reference["entity_name"] == _MODEL for reference in references)


def test_serving_inventory_detects_traffic_route_and_inference_table_references() -> None:
    entity = SimpleNamespace(
        name="target-alias",
        entity_name=_MODEL,
        entity_version="1",
    )
    route = SimpleNamespace(
        served_entity_name="target-alias",
        traffic_percentage=37,
    )
    inference = {
        "catalog_name": _CATALOG,
        "schema_name": _SCHEMA,
        "table_name_prefix": f"{_TABLE_PREFIX}_aaaaaaaaaaaa",
    }
    endpoint = _endpoint(
        current=_config(entities=[entity], routes=[route]),
        inference=inference,
    )

    _inventory_snapshot, references = inventory_gateway_serving(
        _workspace(endpoints=[endpoint]),
        model_name=_MODEL,
        inference_table_family=_TABLE_FAMILY,
    )

    assert {reference["collection"] for reference in references} == {
        "served_entities",
        "traffic_routes",
        "inference_table",
    }
    traffic = next(
        reference
        for reference in references
        if reference["collection"] == "traffic_routes"
    )
    assert traffic["traffic_percentage"] == "37"


def test_serving_inventory_ignores_platform_and_managed_foundation_entities() -> None:
    platform = SimpleNamespace(
        name="databricks-foundation",
        id=None,
        creator=None,
        state={"ready": "READY"},
        config=_config(
            entities=[
                SimpleNamespace(
                    name="foundation",
                    foundation_model=SimpleNamespace(name="system.ai.foundation"),
                )
            ]
        ),
        pending_config=None,
        ai_gateway=None,
    )
    managed = _endpoint(
        current=_config(
            entities=[
                SimpleNamespace(
                    name=None,
                    foundation_model=SimpleNamespace(name="mas-base-model-deadbeef"),
                )
            ]
        )
    )
    managed.name = "mas-deadbeef-endpoint"

    inventory, references = inventory_gateway_serving(
        _workspace(endpoints=[platform, managed]),
        model_name=_MODEL,
        inference_table_family=_TABLE_FAMILY,
    )

    assert references == ()
    assert [item["name"] for item in inventory] == ["mas-deadbeef-endpoint"]


def test_serving_inventory_rejects_model_less_entity_without_provider_marker() -> None:
    endpoint = _endpoint(
        current=_config(
            entities=[
                SimpleNamespace(
                    name="unknown",
                    entity_name=None,
                    model_name=None,
                )
            ]
        )
    )

    with pytest.raises(RuntimeError, match="no recognized provider identity"):
        inventory_gateway_serving(
            _workspace(endpoints=[endpoint]),
            model_name=_MODEL,
            inference_table_family=_TABLE_FAMILY,
        )


@pytest.mark.parametrize("drift", ["pending-model", "inference-table"])
def test_platform_foundation_shortcut_rejects_hidden_gateway_state(
    drift: str,
) -> None:
    platform = SimpleNamespace(
        name="databricks-foundation",
        id=None,
        creator=None,
        state={"ready": "READY"},
        config=_config(
            entities=[
                SimpleNamespace(
                    name="foundation",
                    foundation_model=SimpleNamespace(name="system.ai.foundation"),
                )
            ]
        ),
        pending_config=(
            _config(
                entities=[
                    SimpleNamespace(
                        name="target",
                        entity_name=_MODEL,
                        entity_version="1",
                    )
                ]
            )
            if drift == "pending-model"
            else None
        ),
        ai_gateway=(
            SimpleNamespace(
                inference_table_config={
                    "catalog_name": _CATALOG,
                    "schema_name": _SCHEMA,
                    "table_name_prefix": f"{_TABLE_PREFIX}_aaaaaaaaaaaa",
                }
            )
            if drift == "inference-table"
            else None
        ),
    )

    with pytest.raises(RuntimeError, match="endpoint identity is incomplete"):
        inventory_gateway_serving(
            _workspace(endpoints=[platform]),
            model_name=_MODEL,
            inference_table_family=_TABLE_FAMILY,
        )


def test_serving_inventory_rejects_uc_and_non_uc_alias_collision() -> None:
    endpoint = _endpoint(
        current=_config(
            entities=[
                SimpleNamespace(
                    name="shared",
                    entity_name=_MODEL,
                    entity_version="1",
                ),
                SimpleNamespace(
                    name="shared",
                    foundation_model=SimpleNamespace(name="mas-base-model-deadbeef"),
                ),
            ]
        )
    )

    with pytest.raises(RuntimeError, match="serving alias is ambiguous"):
        inventory_gateway_serving(
            _workspace(endpoints=[endpoint]),
            model_name=_MODEL,
            inference_table_family=_TABLE_FAMILY,
        )


def test_serving_inventory_rejects_conflicting_route_alias_fields() -> None:
    endpoint = _endpoint(
        current=_config(
            entities=[
                SimpleNamespace(
                    name="target",
                    entity_name=_MODEL,
                    entity_version="1",
                ),
                SimpleNamespace(
                    name="other",
                    entity_name="customer.catalog.other_model",
                    entity_version="1",
                ),
            ],
            routes=[
                SimpleNamespace(
                    served_entity_name="target",
                    served_model_name="other",
                    traffic_percentage=100,
                )
            ],
        )
    )

    with pytest.raises(RuntimeError, match="serving route is ambiguous"):
        inventory_gateway_serving(
            _workspace(endpoints=[endpoint]),
            model_name=_MODEL,
            inference_table_family=_TABLE_FAMILY,
        )
