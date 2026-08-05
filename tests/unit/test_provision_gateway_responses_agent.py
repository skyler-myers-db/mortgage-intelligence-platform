from __future__ import annotations

import base64
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
from databricks.sdk.errors import BadRequest, NotFound
from databricks.sdk.service.serving import (
    ServingEndpointAccessControlResponse,
    ServingEndpointPermission,
    ServingEndpointPermissionLevel,
    ServingEndpointPermissions,
)
from mlflow.exceptions import RestException

import backend.agents.gateway_contract as gateway_contract
from backend.services.ai_gateway_proof_attestation import derive_gateway_proof_verify_key
from tools.databricks import gateway_endpoint_contract as endpoint_contract
from tools.databricks import gateway_model_attestation as attestation
from tools.databricks import gateway_registration_journal as journal_store
from tools.databricks import gateway_registration_recovery as registration_recovery
from tools.databricks import provision_gateway_responses_agent as gateway
from tools.databricks import serving_query_group_access as query_group_access
from tools.databricks.gateway_endpoint_contract import (
    current_model_version as _current_model_version,
)
from tools.databricks.provision_gateway_responses_agent import (
    GatewayAgentDeployment,
    gateway_agent_source_hash,
)
from tools.databricks.provision_gateway_responses_agent import (
    verify_gateway_responses_agent as _verify_gateway_responses_agent,
)
from tools.databricks.serving_query_group_access import managed_query_group_name
from tools.databricks.serving_query_group_provenance import intent_external_id

_CATALOG = "mip"
_GENIE_SPACE_ID = "space-123"
_RUNTIME_APPLICATION_ID = "runtime-client"
_SUPERVISOR_ID = "supervisor-id"
_SUPERVISOR_ENDPOINT_ID = "supervisor-endpoint-id"
_PROXY_CLIENT_ID = "proxy-client"
_PROXY_CREDENTIAL_ID = "proxy-credential"
_PROXY_SECRET_REFERENCE = "{{secrets/mip-agent-proxy/oauth-client-secret-proxy-credential}}"
_WORKSPACE_HOST = "https://workspace.cloud.databricks.com"
_MODEL_SIGNING_KEY = base64.urlsafe_b64encode(b"t" * 32).decode("ascii").rstrip("=")
_MODEL_VERIFY_KEY = derive_gateway_proof_verify_key(_MODEL_SIGNING_KEY)
_PREVIOUS_MODEL_SIGNING_KEY = base64.urlsafe_b64encode(b"p" * 32).decode("ascii").rstrip("=")
_PREVIOUS_MODEL_VERIFY_KEY = derive_gateway_proof_verify_key(_PREVIOUS_MODEL_SIGNING_KEY)


def ensure_gateway_responses_agent(workspace: object, **kwargs: Any) -> GatewayAgentDeployment:
    """Supply the reviewed proxy identity to focused provisioner fixtures."""

    if getattr(workspace, "config", None) is None:
        workspace.config = SimpleNamespace(host=_WORKSPACE_HOST)
    kwargs.setdefault("proxy_caller_application_id", _PROXY_CLIENT_ID)
    kwargs.setdefault("proxy_caller_credential_id", _PROXY_CREDENTIAL_ID)
    kwargs.setdefault("proxy_caller_secret_reference", _PROXY_SECRET_REFERENCE)
    kwargs.setdefault("deployment_app_name", "mip-app")
    return gateway.ensure_gateway_responses_agent(workspace, **kwargs)


def verify_gateway_responses_agent(
    workspace: object,
    deployment: GatewayAgentDeployment,
    **kwargs: Any,
) -> None:
    """Supply the authenticated workspace origin to focused verifier fixtures."""

    if getattr(workspace, "config", None) is None:
        workspace.config = SimpleNamespace(host=_WORKSPACE_HOST)
    _verify_gateway_responses_agent(workspace, deployment, **kwargs)


def _served_entity(**kwargs: Any) -> tuple[Any, Any]:
    kwargs.setdefault("workspace_host", _WORKSPACE_HOST)
    kwargs.setdefault("proxy_caller_application_id", _PROXY_CLIENT_ID)
    kwargs.setdefault("proxy_caller_credential_id", _PROXY_CREDENTIAL_ID)
    kwargs.setdefault("proxy_caller_secret_reference", _PROXY_SECRET_REFERENCE)
    return gateway._served_entity(**kwargs)


def _assert_single_writer() -> None:
    return None


def _production_model_tags(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.setenv("MIP_ALLOW_RUNTIME_MODEL_ATTESTATION_SIGNING", "1")
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY", _MODEL_SIGNING_KEY)
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY", _MODEL_VERIFY_KEY)
    return attestation.sign_gateway_model_contract(
        full_name="mip.audit.proxy_deadbeef1234",
        model_source="models:/m-reviewed-proxy",
        source_hash="a" * 64,
        supervisor_id=_SUPERVISOR_ID,
        supervisor_endpoint_id=_SUPERVISOR_ENDPOINT_ID,
        upstream_endpoint="managed-supervisor",
        runtime_application_id=_RUNTIME_APPLICATION_ID,
        model_family="mip.audit.proxy",
        experiment_base="mip-agent-runtime-gateway-proxy",
        catalog=_CATALOG,
        genie_space_id=_GENIE_SPACE_ID,
        inference_schema="audit",
        inference_table_prefix="mip_agent_gateway_growth_agent",
    )


def test_gateway_model_version_tags_are_within_exact_uc_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tags = _production_model_tags(monkeypatch)

    assert len(tags) == 16
    assert len(tags) <= registration_recovery._UC_MODEL_VERSION_TAG_LIMIT
    assert all(registration_recovery._UC_MODEL_VERSION_TAG_KEY.fullmatch(key) for key in tags)
    assert max(map(len, tags.values())) <= registration_recovery._UC_MODEL_VERSION_TAG_VALUE_LIMIT
    assert gateway.validated_model_version_tags(tags) == tags
    assert registration_recovery._UC_MODEL_VERSION_TAG_KEY.fullmatch("a" * 256)
    assert registration_recovery._UC_MODEL_VERSION_TAG_KEY.fullmatch("a" * 257) is None


@pytest.mark.parametrize("error_code", ("NOT_FOUND", "RESOURCE_DOES_NOT_EXIST"))
def test_registration_recovery_recognizes_real_mlflow_missing_errors(
    error_code: str,
) -> None:
    exc = RestException({"error_code": error_code, "message": "missing"})

    assert registration_recovery._missing_resource(exc) is True
    assert (
        registration_recovery._missing_resource(
            RestException({"error_code": "PERMISSION_DENIED", "message": "denied"})
        )
        is False
    )


@pytest.mark.parametrize(
    "invalid", [".", ",", "-", "=", "/", ":", ">", "<", "%", "&", "?", "\\", " "]
)
def test_gateway_model_version_tag_validator_rejects_reserved_key_characters(
    monkeypatch: pytest.MonkeyPatch,
    invalid: str,
) -> None:
    tags = _production_model_tags(monkeypatch)
    key, value = tags.popitem()
    tags[f"{key}{invalid}"] = value

    with pytest.raises(ValueError, match="invalid for Unity Catalog"):
        gateway.validated_model_version_tags(tags)


@pytest.mark.parametrize("value", ["x" * 257, " leading", "trailing ", ""])
def test_gateway_model_version_tag_validator_rejects_invalid_values(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    tags = _production_model_tags(monkeypatch)
    tags[gateway_contract.GATEWAY_MODEL_CONTRACT_FIELD_TAGS["catalog"]] = value

    with pytest.raises(ValueError, match="invalid for Unity Catalog"):
        gateway.validated_model_version_tags(tags)


def test_gateway_model_version_tag_validator_accepts_256_character_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tags = _production_model_tags(monkeypatch)
    tags[gateway_contract.GATEWAY_MODEL_CONTRACT_FIELD_TAGS["catalog"]] = "x" * 256

    assert gateway.validated_model_version_tags(tags) == tags


def test_gateway_agent_source_hash_binds_code_and_upstream_endpoint() -> None:
    first = gateway_agent_source_hash(
        upstream_endpoint="supervisor-a",
        catalog=_CATALOG,
        genie_space_id=_GENIE_SPACE_ID,
    )
    assert len(first) == 64
    assert first != gateway_agent_source_hash(
        upstream_endpoint="supervisor-b",
        catalog=_CATALOG,
        genie_space_id=_GENIE_SPACE_ID,
    )
    assert first != gateway_agent_source_hash(
        upstream_endpoint="supervisor-a",
        catalog="customer_mip",
        genie_space_id=_GENIE_SPACE_ID,
    )
    assert first != gateway_agent_source_hash(
        upstream_endpoint="supervisor-a",
        catalog=_CATALOG,
        genie_space_id="different-space",
    )


def test_gateway_source_hash_binds_runtime_contract_modules(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    first = tmp_path / "proxy.py"
    second = tmp_path / "contract.py"
    first.write_text("proxy-v1", encoding="utf-8")
    second.write_text("contract-v1", encoding="utf-8")
    monkeypatch.setattr(
        gateway_contract,
        "GATEWAY_PROXY_TRANSITIVE_SOURCES",
        (first, second),
    )
    baseline = gateway_agent_source_hash(
        upstream_endpoint="managed-supervisor",
        catalog=_CATALOG,
        genie_space_id=_GENIE_SPACE_ID,
    )
    second.write_text("contract-v2", encoding="utf-8")

    assert (
        gateway_agent_source_hash(
            upstream_endpoint="managed-supervisor",
            catalog=_CATALOG,
            genie_space_id=_GENIE_SPACE_ID,
        )
        != baseline
    )


def test_gateway_source_hash_covers_every_served_transitive_module() -> None:
    assert {path.name for path in gateway_contract.GATEWAY_PROXY_TRANSITIVE_SOURCES} == {
        "ai_gateway_proof_attestation.py",
        "gateway_contract.py",
        "gateway_live_resource_contract.py",
        "gateway_provider_shape.py",
        "mortgage_growth_supervisor_proxy.py",
        "reviewed_uc_function_contract.py",
        "supervisor_contract.py",
    }


def test_gateway_runtime_names_are_target_catalog_and_runtime_home_derived() -> None:
    assert (
        gateway._target_model_family(
            configured="mip.audit.mortgage_growth_supervisor_proxy",
            catalog="customer_catalog",
        )
        == "customer_catalog.audit.mortgage_growth_supervisor_proxy"
    )
    experiment = gateway.gateway_experiment_name(
        base_experiment_name="mip-agent-runtime-gateway-proxy",
        contract_hash="a" * 64,
        runtime_application_id="runtime-client",
    )
    assert experiment == "/Users/runtime-client/mip-agent-runtime-gateway-proxy-aaaaaaaaaaaa"
    assert not experiment.startswith("/Shared/")


def test_gateway_resource_hash_binds_every_green_allocation_input() -> None:
    source_hash = "a" * 64
    baseline = gateway.gateway_resource_hash(
        source_hash=source_hash,
        supervisor_id=_SUPERVISOR_ID,
        supervisor_endpoint_id=_SUPERVISOR_ENDPOINT_ID,
        runtime_application_id=_RUNTIME_APPLICATION_ID,
        workspace_host=_WORKSPACE_HOST,
        model_name="mip.audit.proxy",
        experiment_name="mip-agent-runtime-gateway-proxy",
        inference_schema="audit",
        inference_table_prefix="mip_agent_gateway_growth_agent",
        attestation_verify_key=_MODEL_VERIFY_KEY,
        proxy_caller_application_id=_PROXY_CLIENT_ID,
        proxy_caller_credential_id=_PROXY_CREDENTIAL_ID,
        proxy_caller_secret_reference=_PROXY_SECRET_REFERENCE,
    )
    variants = (
        {"source_hash": "b" * 64},
        {"supervisor_id": "different-supervisor"},
        {"supervisor_endpoint_id": "different-supervisor-endpoint"},
        {"runtime_application_id": "different-runtime-client"},
        {"workspace_host": "https://different.cloud.databricks.com"},
        {"model_name": "mip.audit.other"},
        {"experiment_name": "other-experiment"},
        {"inference_schema": "other"},
        {"inference_table_prefix": "mip_agent_gateway_other"},
        {
            "attestation_verify_key": derive_gateway_proof_verify_key(
                base64.urlsafe_b64encode(b"n" * 32).decode("ascii").rstrip("=")
            )
        },
        {"proxy_caller_application_id": "different-proxy-client"},
        {
            "proxy_caller_secret_reference": (
                "{{secrets/other-agent-proxy/oauth-client-secret-" + _PROXY_CREDENTIAL_ID + "}}"
            )
        },
        {
            "proxy_caller_credential_id": "different-proxy-credential",
            "proxy_caller_secret_reference": (
                "{{secrets/mip-agent-proxy/" "oauth-client-secret-different-proxy-credential}}"
            ),
        },
    )
    for override in variants:
        values = {
            "source_hash": source_hash,
            "supervisor_id": _SUPERVISOR_ID,
            "supervisor_endpoint_id": _SUPERVISOR_ENDPOINT_ID,
            "runtime_application_id": _RUNTIME_APPLICATION_ID,
            "workspace_host": _WORKSPACE_HOST,
            "model_name": "mip.audit.proxy",
            "experiment_name": "mip-agent-runtime-gateway-proxy",
            "inference_schema": "audit",
            "inference_table_prefix": "mip_agent_gateway_growth_agent",
            "attestation_verify_key": _MODEL_VERIFY_KEY,
            "proxy_caller_application_id": _PROXY_CLIENT_ID,
            "proxy_caller_credential_id": _PROXY_CREDENTIAL_ID,
            "proxy_caller_secret_reference": _PROXY_SECRET_REFERENCE,
            **override,
        }
        assert gateway.gateway_resource_hash(**values) != baseline


@pytest.mark.parametrize(
    "override",
    (
        {"proxy_caller_application_id": _RUNTIME_APPLICATION_ID.upper()},
        {
            "proxy_caller_secret_reference": (
                "{{secrets/mip-agent-proxy/oauth-client-secret-other-credential}}"
            )
        },
    ),
)
def test_gateway_resource_hash_rejects_invalid_proxy_credential_binding(
    override: dict[str, str],
) -> None:
    values = {
        "source_hash": "a" * 64,
        "supervisor_id": _SUPERVISOR_ID,
        "supervisor_endpoint_id": _SUPERVISOR_ENDPOINT_ID,
        "runtime_application_id": _RUNTIME_APPLICATION_ID,
        "workspace_host": _WORKSPACE_HOST,
        "model_name": "mip.audit.proxy",
        "experiment_name": "mip-agent-runtime-gateway-proxy",
        "inference_schema": "audit",
        "inference_table_prefix": "mip_agent_gateway_growth_agent",
        "attestation_verify_key": _MODEL_VERIFY_KEY,
        "proxy_caller_application_id": _PROXY_CLIENT_ID,
        "proxy_caller_credential_id": _PROXY_CREDENTIAL_ID,
        "proxy_caller_secret_reference": _PROXY_SECRET_REFERENCE,
        **override,
    }

    with pytest.raises(ValueError, match="proxy credential binding is invalid"):
        gateway.gateway_resource_hash(**values)


def test_current_model_version_requires_exactly_one_expected_registered_model() -> None:
    drifted = SimpleNamespace(
        config=SimpleNamespace(
            served_entities=[
                SimpleNamespace(entity_name="mip.app.other", entity_version="9"),
                SimpleNamespace(
                    entity_name="mip.audit.mortgage_growth_supervisor_proxy",
                    entity_version="3",
                ),
            ]
        )
    )

    assert (
        _current_model_version(
            drifted,
            model_name="mip.audit.mortgage_growth_supervisor_proxy",
        )
        is None
    )

    details = SimpleNamespace(
        config=SimpleNamespace(
            served_entities=[
                SimpleNamespace(
                    entity_name="mip.audit.mortgage_growth_supervisor_proxy",
                    entity_version="3",
                )
            ]
        )
    )
    assert (
        _current_model_version(
            details,
            model_name="mip.audit.mortgage_growth_supervisor_proxy",
        )
        == 3
    )


def test_current_model_version_prefers_interrupted_pending_update() -> None:
    details = SimpleNamespace(
        config=SimpleNamespace(
            served_entities=[
                SimpleNamespace(
                    entity_name="mip.audit.mortgage_growth_supervisor_proxy",
                    entity_version="3",
                )
            ]
        ),
        pending_config=SimpleNamespace(
            served_entities=[
                SimpleNamespace(
                    entity_name="mip.audit.mortgage_growth_supervisor_proxy",
                    entity_version="4",
                )
            ]
        ),
    )

    assert (
        _current_model_version(
            details,
            model_name="mip.audit.mortgage_growth_supervisor_proxy",
        )
        == 4
    )


class _Client:
    def __init__(
        self,
        versions: list[object] | None = None,
        *,
        experiment_owner: str = _RUNTIME_APPLICATION_ID,
    ) -> None:
        self.versions = versions or []
        for version in self.versions:
            if not hasattr(version, "status"):
                version.status = "READY"
        self.experiment_owner = experiment_owner
        self.version_tags = {
            str(getattr(item, "version", "")): dict(getattr(item, "tags", None) or {})
            for item in self.versions
        }
        self.version_sources = {
            str(getattr(item, "version", "")): str(
                getattr(item, "source", "models:/m-reviewed") or "models:/m-reviewed"
            )
            for item in self.versions
        }
        self.version_statuses = {
            str(getattr(item, "version", "")): str(getattr(item, "status", "") or "")
            for item in self.versions
        }
        self.experiments_by_name: dict[str, object] = {}
        self.experiments_by_id: dict[str, object] = {}
        self.logged_models: dict[str, object] = {}
        self.runs: dict[str, object] = {}
        self.model_version_searches: list[str | None] = []
        self.fail_experiment_tag_set = False
        self.fail_experiment_tag_clear = False

    def search_model_versions(
        self,
        query: str | None = None,
        *,
        filter_string: str | None = None,
        max_results: int | None = None,
        page_token: str | None = None,
    ) -> list[object]:
        assert max_results in (None, registration_recovery._MODEL_VERSION_SEARCH_PAGE_SIZE)
        assert page_token is None
        assert query is None or filter_string is None
        query = filter_string if filter_string is not None else query
        self.model_version_searches.append(query)
        if query is None:
            return self.versions
        field, expected_value = query.split("='", 1)
        expected_value = expected_value.removesuffix("'")
        if field == "source_path":
            return [
                version
                for version in self.versions
                if str(getattr(version, "source", "") or "").strip() == expected_value
            ]
        if field == "run_id":
            return [
                version
                for version in self.versions
                if str(getattr(version, "run_id", "") or "").strip() == expected_value
            ]
        assert field == "name"
        assert expected_value.startswith("mip.audit.mortgage_growth_supervisor_proxy_")
        selected = [
            version
            for version in self.versions
            if not str(getattr(version, "name", "") or "").strip()
            or str(getattr(version, "name", "") or "").strip() == expected_value
        ]
        for version in selected:
            if not str(getattr(version, "name", "") or "").strip():
                version.name = expected_value
        return selected

    def set_model_version_tag(self, *args: str) -> None:
        raise AssertionError(f"Gateway model tags must be immutable: {args!r}")

    def get_model_version(self, _name: str, version: str) -> object:
        return SimpleNamespace(
            name=_name,
            version=version,
            source=self.version_sources.get(version, "models:/m-reviewed-proxy"),
            tags=dict(self.version_tags.get(version, {})),
            status=self.version_statuses.get(version, "READY"),
        )

    def get_logged_model(self, model_id: str) -> object:
        if model_id not in self.logged_models:
            run_id = f"run-{model_id}"
            self.logged_models[model_id] = SimpleNamespace(
                model_id=model_id,
                source_run_id=run_id,
                experiment_id="experiment-7",
            )
            self.runs[run_id] = SimpleNamespace(info=SimpleNamespace(experiment_id="experiment-7"))
        return self.logged_models[model_id]

    def get_run(self, run_id: str) -> object:
        return self.runs.setdefault(
            run_id,
            SimpleNamespace(info=SimpleNamespace(experiment_id="experiment-7")),
        )

    def search_logged_models(
        self,
        *,
        experiment_ids: list[str],
        max_results: int | None = None,
        page_token: str | None = None,
    ) -> list[object]:
        assert experiment_ids == ["experiment-7"]
        assert max_results == registration_recovery._LOGGED_MODEL_SEARCH_PAGE_SIZE == 50
        assert page_token is None
        return [
            logged_model
            for logged_model in self.logged_models.values()
            if str(getattr(logged_model, "experiment_id", "") or "") in experiment_ids
        ]

    def set_experiment(self, name: str) -> object:
        if name in self.experiments_by_name:
            return self.experiments_by_name[name]
        experiment = SimpleNamespace(
            experiment_id="experiment-7",
            name=name,
            lifecycle_stage="active",
            tags={"mlflow.ownerEmail": self.experiment_owner},
        )
        self.experiments_by_name[name] = experiment
        self.experiments_by_id[experiment.experiment_id] = experiment
        return experiment

    def get_experiment_by_name(self, name: str) -> object | None:
        return self.experiments_by_name.get(name)

    def get_experiment(self, experiment_id: str) -> object | None:
        return self.experiments_by_id.get(experiment_id)

    def set_experiment_tag(self, experiment_id: str, key: str, value: str) -> None:
        if self.fail_experiment_tag_set:
            raise RuntimeError("experiment tag persistence failed")
        if self.fail_experiment_tag_clear and key.startswith(journal_store.RETIREMENT_TAG_PREFIX):
            raise RuntimeError("experiment tag retirement failed")
        experiment = self.experiments_by_id[experiment_id]
        experiment.tags[key] = value

    def delete_experiment_tag(self, experiment_id: str, key: str) -> None:
        raise AssertionError("Databricks does not expose experiment-tag deletion")


def test_existing_source_scan_rejects_previous_epoch_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version = SimpleNamespace(version="3", source="models:/m-reviewed", tags={"signed": "old"})
    client = _Client([version])
    monkeypatch.setattr(gateway, "verify_gateway_model_contract", lambda **_kwargs: False)

    with pytest.raises(RuntimeError, match="previous attestation epoch"):
        gateway._existing_source_version(
            client,
            model_name="mip.audit.mortgage_growth_supervisor_proxy_deadbeef1234",
            source_hash="a" * 64,
            supervisor_id=_SUPERVISOR_ID,
            supervisor_endpoint_id=_SUPERVISOR_ENDPOINT_ID,
            upstream_endpoint="managed-supervisor",
            runtime_application_id=_RUNTIME_APPLICATION_ID,
            model_family="mip.audit.mortgage_growth_supervisor_proxy",
            experiment_base="mip-agent-runtime-gateway-proxy",
            catalog="mip",
            genie_space_id="genie-id",
            inference_schema="audit",
            inference_table_prefix="mip_gateway_proxy",
        )


class _ServingEndpoints:
    def __init__(
        self,
        details: object | dict[str, object] | None = None,
        *,
        supervisor_endpoint_id: str = _SUPERVISOR_ENDPOINT_ID,
        supervisor_endpoint_creator: str = _RUNTIME_APPLICATION_ID,
        permissions_by_endpoint_id: dict[str, object] | None = None,
    ) -> None:
        self.details = details
        self.supervisor_endpoint_id = supervisor_endpoint_id
        self.supervisor_endpoint_creator = supervisor_endpoint_creator
        self.created: list[dict[str, Any]] = []
        self.updated: list[dict[str, Any]] = []
        self.gateway_updates: list[dict[str, Any]] = []
        self.patches: list[dict[str, Any]] = []
        self.events: list[str] = []
        self.rate_limit_puts: list[dict[str, Any]] = []
        self.permissions_by_endpoint_id = permissions_by_endpoint_id or {}

    def get(self, endpoint: str) -> object:
        if endpoint == "managed-supervisor":
            return SimpleNamespace(
                id=self.supervisor_endpoint_id,
                creator=self.supervisor_endpoint_creator,
            )
        if isinstance(self.details, dict):
            if endpoint not in self.details:
                raise NotFound("missing")
            return self.details[endpoint]
        if endpoint != "mip-growth-agent-gateway" or self.details is None:
            raise NotFound("missing")
        return self.details

    def list(self) -> list[object]:
        if isinstance(self.details, dict):
            return [SimpleNamespace(name=name) for name in self.details]
        return []

    def get_permissions(self, endpoint_id: str) -> object:
        return self.permissions_by_endpoint_id.get(
            endpoint_id,
            SimpleNamespace(access_control_list=[]),
        )

    def create(self, **kwargs: Any) -> None:
        self.created.append(kwargs)
        name = str(kwargs["name"])
        created = SimpleNamespace(
            id=f"{name}-id",
            name=name,
            creator=self.supervisor_endpoint_creator,
            state=SimpleNamespace(ready="READY"),
            task="agent/v1/responses",
            config=kwargs["config"],
            pending_config=None,
            ai_gateway=kwargs["ai_gateway"],
            tags=kwargs["tags"],
            description=kwargs["description"],
            route_optimized=kwargs["route_optimized"],
            budget_policy_id=None,
            email_notifications=None,
        )
        if isinstance(self.details, dict):
            self.details[name] = created
        elif self.details is not None and name != "mip-growth-agent-gateway":
            self.details = {
                "mip-growth-agent-gateway": self.details,
                name: created,
            }
        else:
            self.details = created

    def create_and_wait(self, **kwargs: Any) -> object:
        self.create(**kwargs)
        return self.get(str(kwargs["name"]))

    def update_config(self, **kwargs: Any) -> None:
        self.updated.append(kwargs)

    def update_config_and_wait(self, **kwargs: Any) -> object:
        self.events.append("update_config_and_wait")
        self.updated.append(kwargs)
        assert self.details is not None
        self.details.config = SimpleNamespace(
            served_entities=kwargs["served_entities"],
            traffic_config=kwargs["traffic_config"],
        )
        self.details.pending_config = None
        return self.details

    def wait_get_serving_endpoint_not_updating(self, endpoint: str) -> object:
        assert endpoint == "mip-growth-agent-gateway"
        self.events.append("wait_not_updating")
        assert self.details is not None
        self.details.pending_config = None
        return self.details

    def put_ai_gateway(self, **kwargs: Any) -> None:
        self.events.append("put_ai_gateway")
        self.gateway_updates.append(kwargs)

    def patch(self, **kwargs: Any) -> None:
        self.events.append("patch")
        self.patches.append(kwargs)

    def put(self, name: str, *, rate_limits: list[object]) -> object:
        self.rate_limit_puts.append({"name": name, "rate_limits": rate_limits})
        return SimpleNamespace(rate_limits=[])


def _patch_mlflow(monkeypatch, *, client: _Client) -> None:
    monkeypatch.setenv("MIP_ALLOW_RUNTIME_MODEL_ATTESTATION_SIGNING", "1")
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY", _MODEL_SIGNING_KEY)
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY", _MODEL_VERIFY_KEY)
    monkeypatch.setenv("MIP_APP_NAME", "mip-app")
    monkeypatch.setenv("MIP_APP_DEPLOYMENT_LEASE_ID", "test-deployment-lease")
    monkeypatch.setenv("MIP_GIT_SHA", "f" * 40)
    monkeypatch.setattr(gateway.app_deployment_lease, "assert_held", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(registration_recovery, "_EXPERIMENT_TAG_VISIBILITY_INTERVAL_S", 0.0)
    for version in client.versions:
        raw_tags = dict(getattr(version, "tags", None) or {})
        if set(raw_tags) == gateway_contract.GATEWAY_MODEL_CANONICAL_TAGS:
            continue
        source_hash = str(
            raw_tags.get(gateway.MODEL_SOURCE_HASH_TAG)
            or raw_tags.get(gateway.SOURCE_HASH_TAG)
            or ""
        )
        upstream = str(
            raw_tags.get(gateway.MODEL_UPSTREAM_TAG) or raw_tags.get(gateway.UPSTREAM_TAG) or ""
        )
        if not source_hash or not upstream:
            continue
        canonical_tags = attestation.sign_gateway_model_contract(
            full_name="mip.audit.test_proxy_deadbeef1234",
            model_source=str(getattr(version, "source", "models:/m-reviewed") or ""),
            source_hash=source_hash,
            supervisor_id=_SUPERVISOR_ID,
            supervisor_endpoint_id=_SUPERVISOR_ENDPOINT_ID,
            upstream_endpoint=upstream,
            runtime_application_id=_RUNTIME_APPLICATION_ID,
            model_family="mip.audit.mortgage_growth_supervisor_proxy",
            experiment_base="mip-agent-runtime-gateway-proxy",
            catalog=_CATALOG,
            genie_space_id=_GENIE_SPACE_ID,
            inference_schema="audit",
            inference_table_prefix="mip_agent_gateway_growth_agent",
        )
        version.tags = canonical_tags
        client.version_tags[str(getattr(version, "version", ""))] = canonical_tags
    monkeypatch.setattr(gateway, "MlflowClient", lambda: client)
    monkeypatch.setattr(gateway.mlflow, "set_tracking_uri", lambda _uri: None)
    monkeypatch.setattr(gateway.mlflow, "set_registry_uri", lambda _uri: None)
    monkeypatch.setattr(
        gateway,
        "verify_gateway_model_contract",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        gateway,
        "gateway_model_attestation_record_key",
        lambda _tags: _MODEL_VERIFY_KEY,
    )
    monkeypatch.setattr(
        gateway.mlflow,
        "set_experiment",
        client.set_experiment,
    )
    monkeypatch.setattr(
        gateway,
        "verify_gateway_responses_agent",
        lambda *_args, **_kwargs: None,
    )


def _registered(
    client: _Client,
    model_uri: str,
    *,
    version: str,
    tags: dict[str, str],
) -> object:
    client.version_tags[version] = dict(tags)
    client.version_sources[version] = model_uri
    client.version_statuses[version] = "READY"
    return SimpleNamespace(version=version, source=model_uri)


class _CleanupClient(_Client):
    def __init__(self, versions: list[object] | None = None) -> None:
        super().__init__(versions)
        self.deleted_versions: list[tuple[str, str]] = []
        self.deleted_registered_models: list[str] = []
        self.deleted_logged_models: list[str] = []
        self.deleted_runs: list[str] = []
        self.fail_version_deletes: set[str] = set()
        self.fail_registered_model_delete = False

    def delete_model_version(self, name: str, version: str) -> None:
        self.deleted_versions.append((name, version))
        if version in self.fail_version_deletes:
            raise RuntimeError(f"delete-{version}-failed")
        self.versions = [
            item
            for item in self.versions
            if not (
                str(getattr(item, "version", "") or "") == version
                and (
                    not str(getattr(item, "name", "") or "").strip()
                    or str(getattr(item, "name", "") or "").strip() == name
                )
            )
        ]

    def delete_registered_model(self, name: str) -> None:
        self.deleted_registered_models.append(name)
        if self.fail_registered_model_delete:
            raise RuntimeError("delete-registered-model-failed")

    def delete_logged_model(self, model_id: str) -> None:
        self.deleted_logged_models.append(model_id)
        self.logged_models.pop(model_id, None)

    def delete_run(self, run_id: str) -> None:
        self.deleted_runs.append(run_id)
        self.runs.pop(run_id, None)


def _cleanup_journal(
    client: _Client,
    *,
    model_source: str,
) -> registration_recovery.RegistrationCleanupJournal:
    client.get_logged_model(model_source.removeprefix("models:/"))
    return gateway._registration_cleanup_journal(
        client,
        model_source=model_source,
        expected_experiment_id="experiment-7",
    )


def _cleanup_tags(
    monkeypatch: pytest.MonkeyPatch,
    *,
    model_name: str,
    model_source: str,
) -> dict[str, str]:
    monkeypatch.setenv("MIP_ALLOW_RUNTIME_MODEL_ATTESTATION_SIGNING", "1")
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY", _MODEL_SIGNING_KEY)
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY", _MODEL_VERIFY_KEY)
    return attestation.sign_gateway_model_contract(
        full_name=model_name,
        model_source=model_source,
        source_hash="a" * 64,
        supervisor_id=_SUPERVISOR_ID,
        supervisor_endpoint_id=_SUPERVISOR_ENDPOINT_ID,
        upstream_endpoint="managed-supervisor",
        runtime_application_id=_RUNTIME_APPLICATION_ID,
        model_family="mip.audit.mortgage_growth_supervisor_proxy",
        experiment_base="mip-agent-runtime-gateway-proxy",
        catalog=_CATALOG,
        genie_space_id=_GENIE_SPACE_ID,
        inference_schema="audit",
        inference_table_prefix="mip_agent_gateway_growth_agent",
    )


def _runtime_workspace(serving: _ServingEndpoints | None = None) -> object:
    return SimpleNamespace(
        config=SimpleNamespace(host=_WORKSPACE_HOST),
        serving_endpoints=serving or _ServingEndpoints(),
        registered_models=SimpleNamespace(
            get=lambda _name: SimpleNamespace(owner=_RUNTIME_APPLICATION_ID)
        ),
    )


def _recovery_contract() -> dict[str, str]:
    return {
        "source_hash": "a" * 64,
        "supervisor_id": _SUPERVISOR_ID,
        "supervisor_endpoint_id": _SUPERVISOR_ENDPOINT_ID,
        "upstream_endpoint": "managed-supervisor",
        "runtime_application_id": _RUNTIME_APPLICATION_ID,
        "model_family": "mip.audit.mortgage_growth_supervisor_proxy",
        "experiment_base": "mip-agent-runtime-gateway-proxy",
        "catalog": _CATALOG,
        "genie_space_id": _GENIE_SPACE_ID,
        "inference_schema": "audit",
        "inference_table_prefix": "mip_agent_gateway_growth_agent",
    }


def _persisted_recovery(
    monkeypatch: pytest.MonkeyPatch,
    client: _Client,
    *,
    model_name: str,
    model_source: str,
    tags: dict[str, str] | None = None,
) -> registration_recovery.DurableRegistrationJournal:
    client.set_experiment("/Users/runtime-client/gateway-recovery")
    durable = registration_recovery.DurableRegistrationJournal(
        model_name=model_name,
        journal=_cleanup_journal(client, model_source=model_source),
        registration_tags=tags
        or _cleanup_tags(monkeypatch, model_name=model_name, model_source=model_source),
    )
    registration_recovery.persist_registration_journal(
        client,
        durable,
        assert_single_writer=lambda: None,
    )
    return durable


def _journal_state(client: _Client) -> journal_store.JournalTagState:
    return journal_store.read_journal_tag_state(client, experiment_id="experiment-7")


def _add_logged_source(
    client: _Client,
    model_id: str,
    *,
    name: str = "mortgage_growth_supervisor_proxy",
) -> str:
    run_id = f"run-{model_id}"
    client.logged_models[model_id] = SimpleNamespace(
        model_id=model_id,
        name=name,
        source_run_id=run_id,
        experiment_id="experiment-7",
    )
    client.runs[run_id] = SimpleNamespace(info=SimpleNamespace(experiment_id="experiment-7"))
    return f"models:/{model_id}"


def _reconcile_recovery(
    client: _Client,
    *,
    model_name: str,
    verify: Any = attestation.verify_gateway_model_contract,
) -> registration_recovery.RegistrationRecovery | None:
    return registration_recovery.reconcile_incomplete_source_versions(
        client,
        _runtime_workspace(),
        model_name=model_name,
        experiment_id="experiment-7",
        expected_creator_application_id=_RUNTIME_APPLICATION_ID,
        **_recovery_contract(),
        verify_attestation=verify,
        assert_single_writer=lambda: None,
    )


def _ensure_gateway(
    workspace: object,
    *,
    approved_query_application_ids: tuple[str, ...] = (),
) -> GatewayAgentDeployment:
    return ensure_gateway_responses_agent(
        workspace,
        endpoint="mip-growth-agent-gateway",
        endpoint_prefix="mip-growth-agent-gateway",
        supervisor_id=_SUPERVISOR_ID,
        upstream_endpoint="managed-supervisor",
        model_name="mip.audit.mortgage_growth_supervisor_proxy",
        experiment_name="mip-agent-runtime-gateway-proxy",
        inference_catalog="mip",
        inference_schema="audit",
        inference_table_prefix="mip_agent_gateway_growth_agent",
        genie_space_id=_GENIE_SPACE_ID,
        expected_creator_application_id=_RUNTIME_APPLICATION_ID,
        approved_query_application_ids=approved_query_application_ids,
    )


def _resource_hash(
    source_hash: str,
    *,
    supervisor_id: str = _SUPERVISOR_ID,
    supervisor_endpoint_id: str = _SUPERVISOR_ENDPOINT_ID,
    runtime_application_id: str = _RUNTIME_APPLICATION_ID,
    verify_key: str = _MODEL_VERIFY_KEY,
    proxy_caller_application_id: str = _PROXY_CLIENT_ID,
    proxy_caller_credential_id: str = _PROXY_CREDENTIAL_ID,
    proxy_caller_secret_reference: str = _PROXY_SECRET_REFERENCE,
) -> str:
    return gateway.gateway_resource_hash(
        source_hash=source_hash,
        supervisor_id=supervisor_id,
        supervisor_endpoint_id=supervisor_endpoint_id,
        runtime_application_id=runtime_application_id,
        workspace_host=_WORKSPACE_HOST,
        model_name="mip.audit.mortgage_growth_supervisor_proxy",
        experiment_name="mip-agent-runtime-gateway-proxy",
        inference_schema="audit",
        inference_table_prefix="mip_agent_gateway_growth_agent",
        attestation_verify_key=verify_key,
        proxy_caller_application_id=proxy_caller_application_id,
        proxy_caller_credential_id=proxy_caller_credential_id,
        proxy_caller_secret_reference=proxy_caller_secret_reference,
    )


def _exact_endpoint_details(
    *,
    source_hash: str,
    model_version: int = 5,
    upstream: str = "managed-supervisor",
    supervisor_id: str = _SUPERVISOR_ID,
    runtime_application_id: str = _RUNTIME_APPLICATION_ID,
    verify_key: str = _MODEL_VERIFY_KEY,
) -> object:
    served_name = f"mip-growth-supervisor-proxy-{model_version}"
    contract_hash = _resource_hash(
        source_hash,
        supervisor_id=supervisor_id,
        runtime_application_id=runtime_application_id,
        verify_key=verify_key,
    )
    model_name = gateway.gateway_agent_model_name(
        base_model_name="mip.audit.mortgage_growth_supervisor_proxy",
        contract_hash=contract_hash,
    )
    table_prefix = gateway.gateway_inference_table_prefix(
        base_prefix="mip_agent_gateway_growth_agent",
        contract_hash=contract_hash,
    )
    return SimpleNamespace(
        id="mip-growth-agent-gateway-id",
        creator=runtime_application_id,
        description=gateway._ENDPOINT_DESCRIPTION,
        route_optimized=False,
        pending_config=None,
        config=SimpleNamespace(
            served_entities=[
                SimpleNamespace(
                    entity_name=model_name,
                    entity_version=str(model_version),
                    name=served_name,
                    environment_vars={
                        **gateway._STATIC_ENV,
                        "DATABRICKS_HOST": _WORKSPACE_HOST,
                        "MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY": verify_key,
                        "MIP_UPSTREAM_SUPERVISOR_ID": supervisor_id,
                        "MIP_UPSTREAM_SUPERVISOR_ENDPOINT": upstream,
                        "MIP_UPSTREAM_SUPERVISOR_CREATOR": runtime_application_id,
                        "MIP_UPSTREAM_PROXY_CLIENT_ID": _PROXY_CLIENT_ID,
                        "MIP_UPSTREAM_PROXY_CREDENTIAL_ID": _PROXY_CREDENTIAL_ID,
                        "MIP_UPSTREAM_PROXY_CLIENT_SECRET": _PROXY_SECRET_REFERENCE,
                        "MIP_SUPERVISOR_CATALOG": _CATALOG,
                        "MIP_SUPERVISOR_GENIE_SPACE_ID": _GENIE_SPACE_ID,
                        "MIP_SUPERVISOR_CONTRACT_SHA256": gateway.supervisor_contract_hash(
                            genie_space_id=_GENIE_SPACE_ID,
                            catalog=_CATALOG,
                        ),
                        "MLFLOW_EXPERIMENT_ID": "experiment-7",
                    },
                    workload_size="Small",
                    workload_type="CPU",
                    scale_to_zero_enabled=True,
                )
            ],
            served_models=[
                SimpleNamespace(
                    model_name=model_name,
                    model_version=str(model_version),
                    name=served_name,
                    environment_vars={
                        **gateway._STATIC_ENV,
                        "DATABRICKS_HOST": _WORKSPACE_HOST,
                        "MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY": verify_key,
                        "MIP_UPSTREAM_SUPERVISOR_ID": supervisor_id,
                        "MIP_UPSTREAM_SUPERVISOR_ENDPOINT": upstream,
                        "MIP_UPSTREAM_SUPERVISOR_CREATOR": runtime_application_id,
                        "MIP_UPSTREAM_PROXY_CLIENT_ID": _PROXY_CLIENT_ID,
                        "MIP_UPSTREAM_PROXY_CREDENTIAL_ID": _PROXY_CREDENTIAL_ID,
                        "MIP_UPSTREAM_PROXY_CLIENT_SECRET": _PROXY_SECRET_REFERENCE,
                        "MIP_SUPERVISOR_CATALOG": _CATALOG,
                        "MIP_SUPERVISOR_GENIE_SPACE_ID": _GENIE_SPACE_ID,
                        "MIP_SUPERVISOR_CONTRACT_SHA256": gateway.supervisor_contract_hash(
                            genie_space_id=_GENIE_SPACE_ID,
                            catalog=_CATALOG,
                        ),
                        "MLFLOW_EXPERIMENT_ID": "experiment-7",
                    },
                    workload_size="Small",
                    workload_type="CPU",
                    scale_to_zero_enabled=True,
                )
            ],
            traffic_config=SimpleNamespace(
                routes=[
                    SimpleNamespace(
                        served_entity_name=served_name,
                        served_model_name=served_name,
                        traffic_percentage=100,
                    )
                ]
            ),
        ),
        tags=[
            SimpleNamespace(key=gateway.SOURCE_HASH_TAG, value=source_hash),
            SimpleNamespace(key=gateway.UPSTREAM_TAG, value=upstream),
        ],
        ai_gateway=SimpleNamespace(
            inference_table_config=SimpleNamespace(
                enabled=True,
                catalog_name="mip",
                schema_name="audit",
                table_name_prefix=table_prefix,
            ),
            usage_tracking_config=SimpleNamespace(enabled=False),
        ),
    )


def _exact_deployment(
    *,
    source_hash: str,
    model_version: int = 5,
    upstream: str = "managed-supervisor",
) -> GatewayAgentDeployment:
    resource_hash = _resource_hash(source_hash)
    model_name = gateway.gateway_agent_model_name(
        base_model_name="mip.audit.mortgage_growth_supervisor_proxy",
        contract_hash=resource_hash,
    )
    table_prefix = gateway.gateway_inference_table_prefix(
        base_prefix="mip_agent_gateway_growth_agent",
        contract_hash=resource_hash,
    )
    return GatewayAgentDeployment(
        endpoint="mip-growth-agent-gateway",
        supervisor_id=_SUPERVISOR_ID,
        supervisor_endpoint_id=_SUPERVISOR_ENDPOINT_ID,
        upstream_endpoint=upstream,
        runtime_application_id=_RUNTIME_APPLICATION_ID,
        workspace_host=_WORKSPACE_HOST,
        proxy_caller_application_id=_PROXY_CLIENT_ID,
        proxy_caller_credential_id=_PROXY_CREDENTIAL_ID,
        proxy_caller_secret_reference=_PROXY_SECRET_REFERENCE,
        model_name=model_name,
        model_version=model_version,
        model_source="models:/m-reviewed-proxy",
        model_attestation_verify_key=_MODEL_VERIFY_KEY,
        model_family="mip.audit.mortgage_growth_supervisor_proxy",
        source_hash=source_hash,
        resource_hash=resource_hash,
        inference_table=f"mip.audit.{table_prefix}",
        inference_table_prefix="mip_agent_gateway_growth_agent",
        experiment_base="mip-agent-runtime-gateway-proxy",
        experiment_name=gateway.gateway_experiment_name(
            base_experiment_name="mip-agent-runtime-gateway-proxy",
            contract_hash=resource_hash,
            runtime_application_id=_RUNTIME_APPLICATION_ID,
        ),
        experiment_id="experiment-7",
        catalog="mip",
        genie_space_id=_GENIE_SPACE_ID,
    )


def _tracking_client(deployment: GatewayAgentDeployment) -> object:
    experiment = SimpleNamespace(
        experiment_id=deployment.experiment_id,
        name=deployment.experiment_name,
        lifecycle_stage="active",
        tags={"mlflow.ownerEmail": _RUNTIME_APPLICATION_ID},
    )
    return SimpleNamespace(
        get_experiment=lambda _id: experiment,
        get_experiment_by_name=lambda _name: experiment,
    )


def _experiment_permissions_api() -> object:
    return SimpleNamespace(
        do=lambda method, path: (
            {
                "access_control_list": [
                    {
                        "service_principal_name": _RUNTIME_APPLICATION_ID,
                        "all_permissions": [
                            {
                                "permission_level": "CAN_MANAGE",
                                "inherited": True,
                                "inherited_from_object": ["/directories/runtime-home-id"],
                            }
                        ],
                    },
                    {
                        "group_name": "admins",
                        "all_permissions": [
                            {
                                "permission_level": "CAN_MANAGE",
                                "inherited": True,
                                "inherited_from_object": ["/directories/"],
                            }
                        ],
                    },
                ]
            }
            if (method == "GET" and path == "/api/2.0/permissions/experiments/experiment-7")
            else pytest.fail("unexpected experiment permissions request")
        )
    )


def _full_verifier_workspace(serving: _ServingEndpoints) -> Any:
    workspace: Any = _runtime_workspace(serving)
    workspace.api_client = _experiment_permissions_api()
    workspace.workspace = SimpleNamespace(
        get_status=lambda path: SimpleNamespace(
            path=path,
            object_type="DIRECTORY",
            object_id="runtime-home-id",
        )
    )
    return workspace


def test_log_gateway_model_uses_deployment_only_packaging_validation(
    monkeypatch,
) -> None:
    monkeypatch.setattr(gateway, "_start_mlflow_run", nullcontext)
    events: list[str] = []

    @contextmanager
    def packaging_validation():
        events.append("enter")
        try:
            yield
        finally:
            events.append("exit")

    monkeypatch.setattr(
        gateway,
        "responses_agent_packaging_validation",
        packaging_validation,
    )

    def fake_log_model() -> object:
        return SimpleNamespace(model_uri="models:/m-reviewed-proxy")

    monkeypatch.setattr(gateway, "_log_responses_model", fake_log_model)

    logged = gateway._log_gateway_model()

    assert logged.model_uri == "models:/m-reviewed-proxy"
    assert events == ["enter", "exit"]


def test_logged_model_declares_no_automatic_private_resources(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        gateway,
        "_MLFLOW_LOG_MODEL",
        lambda **kwargs: captured.update(kwargs)
        or SimpleNamespace(model_uri="models:/m-reviewed-proxy"),
    )

    gateway._log_responses_model()

    resources = captured["resources"]
    assert resources == []
    assert captured["input_example"]["max_output_tokens"] == 256
    assert "build_cohort" in captured["input_example"]["input"][0]["content"]


def test_ensure_gateway_agent_creates_one_responses_endpoint_with_exact_gateway_table(
    monkeypatch,
) -> None:
    client = _Client()
    _patch_mlflow(monkeypatch, client=client)
    monkeypatch.setattr(
        gateway,
        "_log_gateway_model",
        lambda **_kwargs: SimpleNamespace(model_uri="models:/m-reviewed-proxy"),
    )
    monkeypatch.setattr(
        gateway.mlflow,
        "register_model",
        lambda model_uri, _name, *, tags: _registered(
            client,
            model_uri,
            version="4",
            tags=tags,
        ),
    )
    serving = _ServingEndpoints()
    workspace = SimpleNamespace(
        serving_endpoints=serving,
        registered_models=SimpleNamespace(
            get=lambda _name: SimpleNamespace(owner=_RUNTIME_APPLICATION_ID)
        ),
    )

    deployment = ensure_gateway_responses_agent(
        workspace,
        endpoint="mip-growth-agent-gateway",
        endpoint_prefix="mip-growth-agent-gateway",
        supervisor_id=_SUPERVISOR_ID,
        upstream_endpoint="managed-supervisor",
        model_name="mip.audit.mortgage_growth_supervisor_proxy",
        experiment_name="mip-agent-runtime-gateway-proxy",
        inference_catalog="mip",
        inference_schema="audit",
        inference_table_prefix="mip_agent_gateway_growth_agent",
        genie_space_id=_GENIE_SPACE_ID,
        expected_creator_application_id=_RUNTIME_APPLICATION_ID,
    )

    assert deployment.model_version == 4
    assert deployment.experiment_name.startswith(f"/Users/{_RUNTIME_APPLICATION_ID}/")
    assert set(client.version_tags["4"]) == gateway_contract.GATEWAY_MODEL_CANONICAL_TAGS
    registered_contract = attestation.gateway_model_contract_from_tags(client.version_tags["4"])
    assert registered_contract["full_name"] == deployment.model_name
    assert registered_contract["model_source"] == "models:/m-reviewed-proxy"
    assert registered_contract["source_hash"] == deployment.source_hash
    assert registered_contract["supervisor_endpoint_id"] == _SUPERVISOR_ENDPOINT_ID
    assert registered_contract["upstream_endpoint"] == "managed-supervisor"
    expected_prefix = gateway.gateway_inference_table_prefix(
        base_prefix="mip_agent_gateway_growth_agent",
        contract_hash=_resource_hash(deployment.source_hash),
    )
    assert deployment.inference_table == f"mip.audit.{expected_prefix}"
    assert len(serving.created) == 1
    created = serving.created[0]
    assert created["route_optimized"] is False
    assert {(tag.key, tag.value) for tag in created["tags"]} == {
        ("mip_proxy_source_hash", deployment.source_hash),
        ("mip_upstream_supervisor_endpoint", "managed-supervisor"),
    }
    assert gateway.SOURCE_HASH_TAG == "mip_proxy_source_hash"
    assert gateway.UPSTREAM_TAG == "mip_upstream_supervisor_endpoint"
    assert all(
        1 <= len(key) <= 255 and not set(key).intersection(".,=/:")
        for key in (gateway.SOURCE_HASH_TAG, gateway.UPSTREAM_TAG)
    )
    assert gateway.MODEL_SOURCE_HASH_TAG == "mip_proxy_source_hash"
    assert gateway.MODEL_UPSTREAM_TAG == "mip_upstream_supervisor_endpoint"
    entity = created["config"].served_entities[0]
    assert entity.entity_name == gateway.gateway_agent_model_name(
        base_model_name="mip.audit.mortgage_growth_supervisor_proxy",
        contract_hash=_resource_hash(deployment.source_hash),
    )
    assert entity.entity_version == "4"
    assert entity.burst_scaling_enabled is False
    assert entity.workload_type.value == "CPU"
    assert entity.environment_vars["MIP_UPSTREAM_SUPERVISOR_ENDPOINT"] == "managed-supervisor"
    assert entity.environment_vars["MLFLOW_EXPERIMENT_ID"] == "experiment-7"
    inference = created["ai_gateway"].inference_table_config
    assert (inference.catalog_name, inference.schema_name, inference.table_name_prefix) == (
        "mip",
        "audit",
        expected_prefix,
    )
    assert created["ai_gateway"].rate_limits is None
    assert serving.gateway_updates == []


def test_invalid_model_version_tags_fail_before_registration_or_endpoint_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _Client()
    _patch_mlflow(monkeypatch, client=client)
    monkeypatch.setattr(
        gateway,
        "_log_gateway_model",
        lambda **_kwargs: SimpleNamespace(model_uri="models:/m-reviewed-proxy"),
    )
    monkeypatch.setattr(
        gateway,
        "sign_gateway_model_contract",
        lambda **_kwargs: {"mip.invalid": "invalid"},
    )
    registrations: list[object] = []
    monkeypatch.setattr(
        gateway.mlflow,
        "register_model",
        lambda *_args, **_kwargs: registrations.append(object()),
    )
    serving = _ServingEndpoints()

    with pytest.raises(ValueError, match="invalid for Unity Catalog"):
        ensure_gateway_responses_agent(
            SimpleNamespace(
                serving_endpoints=serving,
                registered_models=SimpleNamespace(
                    get=lambda _name: SimpleNamespace(owner=_RUNTIME_APPLICATION_ID)
                ),
            ),
            endpoint="mip-growth-agent-gateway",
            endpoint_prefix="mip-growth-agent-gateway",
            supervisor_id=_SUPERVISOR_ID,
            upstream_endpoint="managed-supervisor",
            model_name="mip.audit.mortgage_growth_supervisor_proxy",
            experiment_name="mip-agent-runtime-gateway-proxy",
            inference_catalog="mip",
            inference_schema="audit",
            inference_table_prefix="mip_agent_gateway_growth_agent",
            genie_space_id=_GENIE_SPACE_ID,
            expected_creator_application_id=_RUNTIME_APPLICATION_ID,
        )

    assert registrations == []
    assert serving.created == []


@pytest.mark.parametrize("status", [None, "COPYING"])
def test_fresh_registration_requires_explicit_authoritative_ready_status(
    monkeypatch: pytest.MonkeyPatch,
    status: str | None,
) -> None:
    client = _Client()
    _patch_mlflow(monkeypatch, client=client)
    monkeypatch.setattr(
        gateway,
        "_log_gateway_model",
        lambda **_kwargs: SimpleNamespace(model_uri="models:/m-fresh-status"),
    )

    def register(model_uri: str, _name: str, *, tags: dict[str, str]) -> object:
        registered = _registered(client, model_uri, version="4", tags=tags)
        client.version_statuses["4"] = status
        return registered

    monkeypatch.setattr(gateway.mlflow, "register_model", register)
    serving = _ServingEndpoints()

    with pytest.raises(RuntimeError, match="status|not ready"):
        ensure_gateway_responses_agent(
            _runtime_workspace(serving),
            endpoint="mip-growth-agent-gateway",
            endpoint_prefix="mip-growth-agent-gateway",
            supervisor_id=_SUPERVISOR_ID,
            upstream_endpoint="managed-supervisor",
            model_name="mip.audit.mortgage_growth_supervisor_proxy",
            experiment_name="mip-agent-runtime-gateway-proxy",
            inference_catalog="mip",
            inference_schema="audit",
            inference_table_prefix="mip_agent_gateway_growth_agent",
            genie_space_id=_GENIE_SPACE_ID,
            expected_creator_application_id=_RUNTIME_APPLICATION_ID,
        )

    assert serving.created == []


def test_failed_registration_preserves_exact_pending_uc_artifacts(monkeypatch) -> None:
    class CleanupClient(_Client):
        def __init__(self) -> None:
            super().__init__()
            self.deleted_versions: list[tuple[str, str]] = []
            self.deleted_registered_models: list[str] = []
            self.deleted_logged_models: list[str] = []
            self.deleted_runs: list[str] = []

        def delete_model_version(self, name: str, version: str) -> None:
            self.deleted_versions.append((name, version))
            self.versions = [
                item for item in self.versions if str(getattr(item, "version", "")) != version
            ]

        def delete_registered_model(self, name: str) -> None:
            self.deleted_registered_models.append(name)

        def delete_logged_model(self, model_id: str) -> None:
            self.deleted_logged_models.append(model_id)
            self.logged_models.pop(model_id, None)

        def delete_run(self, run_id: str) -> None:
            self.deleted_runs.append(run_id)

    client = CleanupClient()
    _patch_mlflow(monkeypatch, client=client)
    monkeypatch.setattr(
        gateway,
        "_log_gateway_model",
        lambda **_kwargs: SimpleNamespace(model_uri="models:/m-partial-registration"),
    )

    def fail_after_uc_create(model_uri: str, name: str, *, tags: dict[str, str]) -> object:
        version = SimpleNamespace(
            version="1",
            source=model_uri,
            tags=dict(tags),
            status="PENDING_REGISTRATION",
            run_id="run-m-partial-registration",
        )
        client.versions.append(version)
        client.version_tags["1"] = dict(tags)
        client.version_sources["1"] = model_uri
        client.version_statuses["1"] = "PENDING_REGISTRATION"
        raise ModuleNotFoundError("No module named 'boto3'")

    monkeypatch.setattr(gateway.mlflow, "register_model", fail_after_uc_create)
    serving = _ServingEndpoints()
    workspace = SimpleNamespace(
        serving_endpoints=serving,
        registered_models=SimpleNamespace(
            get=lambda _name: SimpleNamespace(owner=_RUNTIME_APPLICATION_ID)
        ),
    )

    with pytest.raises(
        registration_recovery.RegistrationReconciliationPendingError,
        match="preserving the durable journal",
    ):
        ensure_gateway_responses_agent(
            workspace,
            endpoint="mip-growth-agent-gateway",
            endpoint_prefix="mip-growth-agent-gateway",
            supervisor_id=_SUPERVISOR_ID,
            upstream_endpoint="managed-supervisor",
            model_name="mip.audit.mortgage_growth_supervisor_proxy",
            experiment_name="mip-agent-runtime-gateway-proxy",
            inference_catalog="mip",
            inference_schema="audit",
            inference_table_prefix="mip_agent_gateway_growth_agent",
            genie_space_id=_GENIE_SPACE_ID,
            expected_creator_application_id=_RUNTIME_APPLICATION_ID,
        )

    assert client.deleted_versions == []
    assert client.deleted_registered_models == []
    assert client.deleted_logged_models == []
    assert client.deleted_runs == []
    assert _journal_state(client).value is not None
    assert not _journal_state(client).retired
    assert serving.created == []


def test_ambiguous_registration_failure_surfaces_retryable_pending_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _CleanupClient()
    _patch_mlflow(monkeypatch, client=client)
    monkeypatch.setattr(registration_recovery, "_REGISTRATION_VISIBILITY_ATTEMPTS", 2)
    monkeypatch.setattr(registration_recovery, "_REGISTRATION_VISIBILITY_INTERVAL_S", 0.0)
    monkeypatch.setattr(
        gateway,
        "_log_gateway_model",
        lambda **_kwargs: SimpleNamespace(model_uri="models:/m-ambiguous-registration"),
    )
    monkeypatch.setattr(
        gateway.mlflow,
        "register_model",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionError("response lost")),
    )
    serving = _ServingEndpoints()

    with pytest.raises(
        registration_recovery.RegistrationReconciliationPendingError,
        match="preserving the durable journal",
    ) as exc_info:
        ensure_gateway_responses_agent(
            _runtime_workspace(serving),
            endpoint="mip-growth-agent-gateway",
            endpoint_prefix="mip-growth-agent-gateway",
            supervisor_id=_SUPERVISOR_ID,
            upstream_endpoint="managed-supervisor",
            model_name="mip.audit.mortgage_growth_supervisor_proxy",
            experiment_name="mip-agent-runtime-gateway-proxy",
            inference_catalog="mip",
            inference_schema="audit",
            inference_table_prefix="mip_agent_gateway_growth_agent",
            genie_space_id=_GENIE_SPACE_ID,
            expected_creator_application_id=_RUNTIME_APPLICATION_ID,
        )

    assert isinstance(exc_info.value.__cause__, ConnectionError)
    assert client.deleted_versions == []
    assert client.deleted_registered_models == []
    assert client.deleted_logged_models == []
    assert client.deleted_runs == []
    assert serving.created == []


def test_existing_source_scan_rejects_incomplete_attested_version(monkeypatch) -> None:
    source_hash = "a" * 64
    version = SimpleNamespace(
        version="3",
        source="models:/m-reviewed",
        tags={gateway.SOURCE_HASH_TAG: source_hash, gateway.UPSTREAM_TAG: "managed-supervisor"},
        status="PENDING_REGISTRATION",
    )
    client = _Client([version])
    _patch_mlflow(monkeypatch, client=client)

    with pytest.raises(RuntimeError, match="is not ready .*PENDING_REGISTRATION"):
        gateway._existing_source_version(
            client,
            model_name="mip.audit.mortgage_growth_supervisor_proxy_deadbeef1234",
            source_hash=source_hash,
            supervisor_id=_SUPERVISOR_ID,
            supervisor_endpoint_id=_SUPERVISOR_ENDPOINT_ID,
            upstream_endpoint="managed-supervisor",
            runtime_application_id=_RUNTIME_APPLICATION_ID,
            model_family="mip.audit.mortgage_growth_supervisor_proxy",
            experiment_base="mip-agent-runtime-gateway-proxy",
            catalog=_CATALOG,
            genie_space_id=_GENIE_SPACE_ID,
            inference_schema="audit",
            inference_table_prefix="mip_agent_gateway_growth_agent",
        )


@pytest.mark.parametrize("status", [None, "COPYING"])
def test_existing_source_scan_requires_explicit_supported_status(
    monkeypatch: pytest.MonkeyPatch,
    status: str | None,
) -> None:
    source_hash = "a" * 64
    version = SimpleNamespace(
        version="3",
        source="models:/m-reviewed",
        tags={gateway.SOURCE_HASH_TAG: source_hash, gateway.UPSTREAM_TAG: "managed-supervisor"},
        status=status,
    )
    client = _Client([version])
    _patch_mlflow(monkeypatch, client=client)

    with pytest.raises(RuntimeError, match="status"):
        gateway._existing_source_version(
            client,
            model_name="mip.audit.mortgage_growth_supervisor_proxy_deadbeef1234",
            source_hash=source_hash,
            supervisor_id=_SUPERVISOR_ID,
            supervisor_endpoint_id=_SUPERVISOR_ENDPOINT_ID,
            upstream_endpoint="managed-supervisor",
            runtime_application_id=_RUNTIME_APPLICATION_ID,
            model_family="mip.audit.mortgage_growth_supervisor_proxy",
            experiment_base="mip-agent-runtime-gateway-proxy",
            catalog=_CATALOG,
            genie_space_id=_GENIE_SPACE_ID,
            inference_schema="audit",
            inference_table_prefix="mip_agent_gateway_growth_agent",
        )


def test_registration_cleanup_preserves_ready_same_source_and_its_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_name = "mip.audit.mortgage_growth_supervisor_proxy_deadbeef1234"
    model_source = "models:/m-shared-ready-source"
    tags = _cleanup_tags(
        monkeypatch,
        model_name=model_name,
        model_source=model_source,
    )
    client = _CleanupClient(
        [
            SimpleNamespace(
                name=model_name,
                version="1",
                source=model_source,
                run_id="run-m-shared-ready-source",
                tags=tags,
                status="FAILED_REGISTRATION",
            ),
            SimpleNamespace(
                name="mip.audit.other_model",
                version="8",
                source=model_source,
                run_id="run-m-shared-ready-source",
                tags={"other": "contract"},
                status="READY",
            ),
        ]
    )
    journal = _cleanup_journal(client, model_source=model_source)

    gateway._compensate_failed_model_registration(
        client,
        _runtime_workspace(),
        model_name=model_name,
        journal=journal,
        registration_tags=tags,
        expected_creator_application_id=_RUNTIME_APPLICATION_ID,
        assert_single_writer=_assert_single_writer,
    )

    assert client.deleted_versions == [(model_name, "1")]
    assert client.deleted_registered_models == []
    assert client.deleted_logged_models == []
    assert client.deleted_runs == []
    assert [version.version for version in client.versions] == ["8"]


@pytest.mark.parametrize("status", [None, "COPYING"])
def test_registration_cleanup_rejects_missing_or_unknown_status_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
    status: str | None,
) -> None:
    model_name = "mip.audit.mortgage_growth_supervisor_proxy_deadbeef1234"
    model_source = "models:/m-ambiguous-status"
    tags = _cleanup_tags(
        monkeypatch,
        model_name=model_name,
        model_source=model_source,
    )
    client = _CleanupClient(
        [
            SimpleNamespace(
                name=model_name,
                version="1",
                source=model_source,
                run_id="run-m-ambiguous-status",
                tags=tags,
                status=status,
            )
        ]
    )
    journal = _cleanup_journal(client, model_source=model_source)

    with pytest.raises(RuntimeError, match="status"):
        gateway._compensate_failed_model_registration(
            client,
            _runtime_workspace(),
            model_name=model_name,
            journal=journal,
            registration_tags=tags,
            expected_creator_application_id=_RUNTIME_APPLICATION_ID,
            assert_single_writer=_assert_single_writer,
        )

    assert client.deleted_versions == []
    assert client.deleted_registered_models == []
    assert client.deleted_logged_models == []
    assert client.deleted_runs == []


def test_registration_cleanup_journal_polls_logged_model_and_run_visibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DelayedJournalClient(_Client):
        logged_reads = 0
        run_reads = 0

        def get_logged_model(self, model_id: str) -> object:
            self.logged_reads += 1
            if self.logged_reads == 1:
                raise NotFound("logged model not visible yet")
            return super().get_logged_model(model_id)

        def get_run(self, run_id: str) -> object:
            self.run_reads += 1
            if self.run_reads == 1:
                raise NotFound("run not visible yet")
            return super().get_run(run_id)

    client = DelayedJournalClient()
    monkeypatch.setattr(registration_recovery, "_JOURNAL_VISIBILITY_INTERVAL_S", 0.0)

    journal = registration_recovery.registration_cleanup_journal(
        client,
        model_source="models:/m-delayed-journal",
        expected_experiment_id="experiment-7",
        logged=SimpleNamespace(
            model_id="m-delayed-journal",
            run_id="run-m-delayed-journal",
        ),
    )

    assert journal.logged_model_id == "m-delayed-journal"
    assert journal.source_run_id == "run-m-delayed-journal"
    assert client.logged_reads == 2
    assert client.run_reads == 2


def test_journal_visibility_failure_cleans_fresh_log_before_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class InvisibleJournalClient(_CleanupClient):
        def get_logged_model(self, model_id: str) -> object:
            raise NotFound(f"{model_id} is not visible")

    client = InvisibleJournalClient()
    _patch_mlflow(monkeypatch, client=client)
    monkeypatch.setattr(registration_recovery, "_JOURNAL_VISIBILITY_ATTEMPTS", 2)
    monkeypatch.setattr(registration_recovery, "_JOURNAL_VISIBILITY_INTERVAL_S", 0.0)
    monkeypatch.setattr(
        gateway,
        "_log_gateway_model",
        lambda **_kwargs: SimpleNamespace(
            model_uri="models:/m-invisible-journal",
            model_id="m-invisible-journal",
            run_id="run-m-invisible-journal",
        ),
    )
    registrations: list[object] = []
    monkeypatch.setattr(
        gateway.mlflow,
        "register_model",
        lambda *_args, **_kwargs: registrations.append(object()),
    )
    serving = _ServingEndpoints()

    with pytest.raises(
        registration_recovery.RegistrationJournalVisibilityError,
        match="authoritatively visible",
    ):
        ensure_gateway_responses_agent(
            _runtime_workspace(serving),
            endpoint="mip-growth-agent-gateway",
            endpoint_prefix="mip-growth-agent-gateway",
            supervisor_id=_SUPERVISOR_ID,
            upstream_endpoint="managed-supervisor",
            model_name="mip.audit.mortgage_growth_supervisor_proxy",
            experiment_name="mip-agent-runtime-gateway-proxy",
            inference_catalog="mip",
            inference_schema="audit",
            inference_table_prefix="mip_agent_gateway_growth_agent",
            genie_space_id=_GENIE_SPACE_ID,
            expected_creator_application_id=_RUNTIME_APPLICATION_ID,
        )

    assert registrations == []
    assert client.deleted_logged_models == ["m-invisible-journal"]
    assert client.deleted_runs == ["run-m-invisible-journal"]
    assert serving.created == []


def test_registration_cleanup_rejects_exact_source_with_tag_drift_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_name = "mip.audit.mortgage_growth_supervisor_proxy_deadbeef1234"
    model_source = "models:/m-tag-drift"
    registration_tags = _cleanup_tags(
        monkeypatch,
        model_name=model_name,
        model_source=model_source,
    )
    drifted_tags = dict(registration_tags)
    drifted_tags[gateway.MODEL_UPSTREAM_TAG] = "drifted-supervisor"
    client = _CleanupClient(
        [
            SimpleNamespace(
                name=model_name,
                version="1",
                source=model_source,
                run_id="run-m-tag-drift",
                tags=drifted_tags,
                status="FAILED_REGISTRATION",
            )
        ]
    )
    journal = _cleanup_journal(client, model_source=model_source)

    with pytest.raises(RuntimeError, match="drifted registration tags"):
        gateway._compensate_failed_model_registration(
            client,
            _runtime_workspace(),
            model_name=model_name,
            journal=journal,
            registration_tags=registration_tags,
            expected_creator_application_id=_RUNTIME_APPLICATION_ID,
            assert_single_writer=_assert_single_writer,
        )

    assert client.deleted_versions == []
    assert client.deleted_registered_models == []
    assert client.deleted_logged_models == []
    assert client.deleted_runs == []


def test_registration_cleanup_handles_no_visible_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_name = "mip.audit.mortgage_growth_supervisor_proxy_deadbeef1234"
    model_source = "models:/m-no-candidate"
    client = _CleanupClient()
    tags = _cleanup_tags(
        monkeypatch,
        model_name=model_name,
        model_source=model_source,
    )
    journal = _cleanup_journal(client, model_source=model_source)
    monkeypatch.setattr(registration_recovery, "_REGISTRATION_VISIBILITY_ATTEMPTS", 2)
    monkeypatch.setattr(registration_recovery, "_REGISTRATION_VISIBILITY_INTERVAL_S", 0.0)

    with pytest.raises(
        registration_recovery.RegistrationReconciliationPendingError,
        match="preserving the durable journal",
    ):
        gateway._compensate_failed_model_registration(
            client,
            _runtime_workspace(),
            model_name=model_name,
            journal=journal,
            registration_tags=tags,
            expected_creator_application_id=_RUNTIME_APPLICATION_ID,
            assert_single_writer=_assert_single_writer,
        )

    assert client.deleted_versions == []
    assert client.deleted_registered_models == []
    assert client.deleted_logged_models == []
    assert client.deleted_runs == []


def test_registration_cleanup_preserves_candidate_hidden_past_visibility_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_name = "mip.audit.mortgage_growth_supervisor_proxy_deadbeef1234"
    model_source = "models:/m-late-after-bound"
    tags = _cleanup_tags(
        monkeypatch,
        model_name=model_name,
        model_source=model_source,
    )

    class LateClient(_CleanupClient):
        target_searches = 0

        def search_model_versions(
            self,
            query: str | None = None,
            *,
            filter_string: str | None = None,
            max_results: int | None = None,
            page_token: str | None = None,
        ) -> list[object]:
            query = filter_string if filter_string is not None else query
            if query == f"name='{model_name}'":
                self.target_searches += 1
                if self.target_searches <= 2:
                    return []
            return super().search_model_versions(
                query,
                max_results=max_results,
                page_token=page_token,
            )

    client = LateClient(
        [
            SimpleNamespace(
                name=model_name,
                version="1",
                source=model_source,
                run_id="run-m-late-after-bound",
                tags=tags,
                status="FAILED_REGISTRATION",
            )
        ]
    )
    journal = _cleanup_journal(client, model_source=model_source)
    monkeypatch.setattr(registration_recovery, "_REGISTRATION_VISIBILITY_ATTEMPTS", 2)
    monkeypatch.setattr(registration_recovery, "_REGISTRATION_VISIBILITY_INTERVAL_S", 0.0)

    with pytest.raises(registration_recovery.RegistrationReconciliationPendingError):
        gateway._compensate_failed_model_registration(
            client,
            _runtime_workspace(),
            model_name=model_name,
            journal=journal,
            registration_tags=tags,
            expected_creator_application_id=_RUNTIME_APPLICATION_ID,
            assert_single_writer=_assert_single_writer,
        )

    assert client.target_searches == 2
    assert [(version.version, version.source) for version in client.versions] == [
        ("1", model_source)
    ]
    assert client.deleted_versions == []
    assert client.deleted_registered_models == []
    assert client.deleted_logged_models == []
    assert client.deleted_runs == []


def test_registration_cleanup_polls_for_delayed_candidate_visibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_name = "mip.audit.mortgage_growth_supervisor_proxy_deadbeef1234"
    model_source = "models:/m-delayed-candidate"
    tags = _cleanup_tags(
        monkeypatch,
        model_name=model_name,
        model_source=model_source,
    )

    class DelayedClient(_CleanupClient):
        target_searches = 0

        def search_model_versions(
            self,
            query: str | None = None,
            *,
            filter_string: str | None = None,
            max_results: int | None = None,
            page_token: str | None = None,
        ) -> list[object]:
            query = filter_string if filter_string is not None else query
            if query is not None:
                self.target_searches += 1
                if self.target_searches == 1:
                    return []
            return super().search_model_versions(
                query,
                max_results=max_results,
                page_token=page_token,
            )

    client = DelayedClient(
        [
            SimpleNamespace(
                name=model_name,
                version="2",
                source=model_source,
                run_id="run-m-delayed-candidate",
                tags=tags,
                status="FAILED_REGISTRATION",
            )
        ]
    )
    journal = _cleanup_journal(client, model_source=model_source)
    monkeypatch.setattr(registration_recovery, "_REGISTRATION_VISIBILITY_INTERVAL_S", 0.0)

    gateway._compensate_failed_model_registration(
        client,
        _runtime_workspace(),
        model_name=model_name,
        journal=journal,
        registration_tags=tags,
        expected_creator_application_id=_RUNTIME_APPLICATION_ID,
        assert_single_writer=_assert_single_writer,
    )

    assert client.target_searches >= 2
    assert client.deleted_versions == [(model_name, "2")]
    assert client.deleted_logged_models == []
    assert client.deleted_runs == []


def test_registration_cleanup_aggregates_independent_delete_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_name = "mip.audit.mortgage_growth_supervisor_proxy_deadbeef1234"
    model_source = "models:/m-delete-failures"
    tags = _cleanup_tags(
        monkeypatch,
        model_name=model_name,
        model_source=model_source,
    )
    client = _CleanupClient(
        [
            SimpleNamespace(
                name=model_name,
                version=version,
                source=model_source,
                run_id="run-m-delete-failures",
                tags=tags,
                status="FAILED_REGISTRATION",
            )
            for version in ("1", "2")
        ]
    )
    client.fail_version_deletes = {"1", "2"}
    journal = _cleanup_journal(client, model_source=model_source)

    with pytest.raises(RuntimeError) as exc_info:
        gateway._compensate_failed_model_registration(
            client,
            _runtime_workspace(),
            model_name=model_name,
            journal=journal,
            registration_tags=tags,
            expected_creator_application_id=_RUNTIME_APPLICATION_ID,
            assert_single_writer=_assert_single_writer,
        )

    message = str(exc_info.value)
    assert "delete-1-failed" in message
    assert "delete-2-failed" in message
    assert client.deleted_versions == [(model_name, "1"), (model_name, "2")]
    assert client.deleted_registered_models == []
    assert client.deleted_logged_models == []
    assert client.deleted_runs == []


def test_registration_cleanup_never_deletes_registered_model_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_name = "mip.audit.mortgage_growth_supervisor_proxy_deadbeef1234"
    model_source = "models:/m-model-delete-failure"
    tags = _cleanup_tags(
        monkeypatch,
        model_name=model_name,
        model_source=model_source,
    )
    client = _CleanupClient(
        [
            SimpleNamespace(
                name=model_name,
                version="1",
                source=model_source,
                run_id="run-m-model-delete-failure",
                tags=tags,
                status="FAILED_REGISTRATION",
            )
        ]
    )
    client.fail_registered_model_delete = True
    journal = _cleanup_journal(client, model_source=model_source)

    gateway._compensate_failed_model_registration(
        client,
        _runtime_workspace(),
        model_name=model_name,
        journal=journal,
        registration_tags=tags,
        expected_creator_application_id=_RUNTIME_APPLICATION_ID,
        assert_single_writer=_assert_single_writer,
    )

    assert client.deleted_versions == [(model_name, "1")]
    assert client.deleted_registered_models == []
    assert client.deleted_logged_models == []
    assert client.deleted_runs == []


@pytest.mark.parametrize("reference_field", ["source", "run_id"])
def test_registration_cleanup_preserves_artifacts_referenced_by_any_surviving_version(
    monkeypatch: pytest.MonkeyPatch,
    reference_field: str,
) -> None:
    model_name = "mip.audit.mortgage_growth_supervisor_proxy_deadbeef1234"
    model_source = "models:/m-cross-model-reference"
    tags = _cleanup_tags(
        monkeypatch,
        model_name=model_name,
        model_source=model_source,
    )
    surviving = {
        "name": "mip.audit.unrelated_model",
        "version": "9",
        "source": "models:/m-unrelated",
        "run_id": "run-unrelated",
        "tags": {"unrelated": "tag"},
        "status": "READY",
    }
    surviving[reference_field] = (
        model_source if reference_field == "source" else "run-m-cross-model-reference"
    )
    client = _CleanupClient(
        [
            SimpleNamespace(
                name=model_name,
                version="1",
                source=model_source,
                run_id="run-m-cross-model-reference",
                tags=tags,
                status="FAILED_REGISTRATION",
            ),
            SimpleNamespace(**surviving),
        ]
    )
    journal = _cleanup_journal(client, model_source=model_source)

    gateway._compensate_failed_model_registration(
        client,
        _runtime_workspace(),
        model_name=model_name,
        journal=journal,
        registration_tags=tags,
        expected_creator_application_id=_RUNTIME_APPLICATION_ID,
        assert_single_writer=_assert_single_writer,
    )

    assert client.deleted_versions == [(model_name, "1")]
    assert client.deleted_logged_models == []
    assert client.deleted_runs == []


def test_registration_cleanup_preserves_run_referenced_by_another_logged_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_name = "mip.audit.mortgage_growth_supervisor_proxy_deadbeef1234"
    model_source = "models:/m-shared-logged-run"
    tags = _cleanup_tags(
        monkeypatch,
        model_name=model_name,
        model_source=model_source,
    )
    client = _CleanupClient(
        [
            SimpleNamespace(
                name=model_name,
                version="1",
                source=model_source,
                run_id="run-m-shared-logged-run",
                tags=tags,
                status="FAILED_REGISTRATION",
            )
        ]
    )
    journal = _cleanup_journal(client, model_source=model_source)
    client.logged_models["m-other-on-run"] = SimpleNamespace(
        model_id="m-other-on-run",
        source_run_id=journal.source_run_id,
        experiment_id=journal.experiment_id,
    )

    gateway._compensate_failed_model_registration(
        client,
        _runtime_workspace(),
        model_name=model_name,
        journal=journal,
        registration_tags=tags,
        expected_creator_application_id=_RUNTIME_APPLICATION_ID,
        assert_single_writer=_assert_single_writer,
    )

    assert client.deleted_versions == [(model_name, "1")]
    assert client.deleted_logged_models == []
    assert client.deleted_runs == []


def test_interrupted_registration_retry_reconciles_then_creates_fresh_ready_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_hash = gateway_agent_source_hash(
        upstream_endpoint="managed-supervisor",
        catalog=_CATALOG,
        genie_space_id=_GENIE_SPACE_ID,
    )
    resource_hash = _resource_hash(source_hash)
    model_name = gateway.gateway_agent_model_name(
        base_model_name="mip.audit.mortgage_growth_supervisor_proxy",
        contract_hash=resource_hash,
    )
    prior_source = "models:/m-interrupted-retry"
    monkeypatch.setenv("MIP_ALLOW_RUNTIME_MODEL_ATTESTATION_SIGNING", "1")
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY", _MODEL_SIGNING_KEY)
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY", _MODEL_VERIFY_KEY)
    prior_tags = attestation.sign_gateway_model_contract(
        full_name=model_name,
        model_source=prior_source,
        source_hash=source_hash,
        supervisor_id=_SUPERVISOR_ID,
        supervisor_endpoint_id=_SUPERVISOR_ENDPOINT_ID,
        upstream_endpoint="managed-supervisor",
        runtime_application_id=_RUNTIME_APPLICATION_ID,
        model_family="mip.audit.mortgage_growth_supervisor_proxy",
        experiment_base="mip-agent-runtime-gateway-proxy",
        catalog=_CATALOG,
        genie_space_id=_GENIE_SPACE_ID,
        inference_schema="audit",
        inference_table_prefix="mip_agent_gateway_growth_agent",
    )
    client = _CleanupClient(
        [
            SimpleNamespace(
                name=model_name,
                version="1",
                source=prior_source,
                run_id="run-m-interrupted-retry",
                tags=prior_tags,
                status="FAILED_REGISTRATION",
            )
        ]
    )
    _patch_mlflow(monkeypatch, client=client)
    experiment_name = gateway.gateway_experiment_name(
        base_experiment_name="mip-agent-runtime-gateway-proxy",
        contract_hash=resource_hash,
        runtime_application_id=_RUNTIME_APPLICATION_ID,
    )
    client.set_experiment(experiment_name)
    durable = registration_recovery.DurableRegistrationJournal(
        model_name=model_name,
        journal=_cleanup_journal(client, model_source=prior_source),
        registration_tags=prior_tags,
    )
    registration_recovery.persist_registration_journal(
        client,
        durable,
        assert_single_writer=_assert_single_writer,
    )
    monkeypatch.setattr(
        gateway,
        "_log_gateway_model",
        lambda **_kwargs: pytest.fail("durable retry must reuse the preserved source"),
    )
    monkeypatch.setattr(
        gateway.mlflow,
        "register_model",
        lambda model_uri, _name, *, tags: _registered(
            client,
            model_uri,
            version="2",
            tags=tags,
        ),
    )
    serving = _ServingEndpoints()

    deployment = ensure_gateway_responses_agent(
        _runtime_workspace(serving),
        endpoint="mip-growth-agent-gateway",
        endpoint_prefix="mip-growth-agent-gateway",
        supervisor_id=_SUPERVISOR_ID,
        upstream_endpoint="managed-supervisor",
        model_name="mip.audit.mortgage_growth_supervisor_proxy",
        experiment_name="mip-agent-runtime-gateway-proxy",
        inference_catalog="mip",
        inference_schema="audit",
        inference_table_prefix="mip_agent_gateway_growth_agent",
        genie_space_id=_GENIE_SPACE_ID,
        expected_creator_application_id=_RUNTIME_APPLICATION_ID,
    )

    assert client.deleted_versions == [(model_name, "1")]
    assert client.deleted_logged_models == []
    assert client.deleted_runs == []
    assert deployment.model_version == 2
    assert deployment.model_source == prior_source
    assert _journal_state(client).retired
    assert len(serving.created) == 1


def test_durable_restart_without_candidate_reuses_preserved_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_name = "mip.audit.mortgage_growth_supervisor_proxy_deadbeef1234"
    source = "models:/m-crash-restart"
    client = _CleanupClient()
    _persisted_recovery(monkeypatch, client, model_name=model_name, model_source=source)

    recovery = _reconcile_recovery(client, model_name=model_name)

    assert recovery is not None
    assert recovery.ready_version is None
    assert recovery.durable.journal.model_source == source
    assert _journal_state(client).value == registration_recovery._durable_journal_value(
        recovery.durable
    )
    assert not _journal_state(client).retired
    assert client.deleted_versions == []
    assert client.deleted_registered_models == []
    assert client.deleted_logged_models == []
    assert client.deleted_runs == []


def test_durable_reconcile_hydrates_uc_search_version_tags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_name = "mip.audit.mortgage_growth_supervisor_proxy_deadbeef1234"
    source = "models:/m-uc-search-result"
    tags = _cleanup_tags(monkeypatch, model_name=model_name, model_source=source)
    full_version = SimpleNamespace(
        name=model_name,
        version="1",
        source=source,
        run_id="run-m-uc-search-result",
        tags=tags,
        status="READY",
    )
    client = _CleanupClient([full_version])
    _persisted_recovery(
        monkeypatch,
        client,
        model_name=model_name,
        model_source=source,
        tags=tags,
    )

    class UcSearchVersion:
        def __init__(self) -> None:
            self.name = model_name
            self.version = "1"
            self.source = source
            self.run_id = "run-m-uc-search-result"
            self.status = "READY"

        def tags(self) -> None:
            raise AssertionError("UC search tags method must never be called")

    client.versions = [UcSearchVersion()]

    recovery = _reconcile_recovery(client, model_name=model_name)

    assert recovery is not None and recovery.ready_version == 1
    assert recovery.durable.registration_tags == tags
    assert client.deleted_versions == []


def test_durable_reconcile_preserves_pending_version_until_it_becomes_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_name = "mip.audit.mortgage_growth_supervisor_proxy_deadbeef1234"
    source = "models:/m-pending-then-ready"
    tags = _cleanup_tags(monkeypatch, model_name=model_name, model_source=source)
    client = _CleanupClient(
        [
            SimpleNamespace(
                name=model_name,
                version="1",
                source=source,
                run_id="run-m-pending-then-ready",
                tags=tags,
                status="PENDING_REGISTRATION",
            )
        ]
    )
    _persisted_recovery(
        monkeypatch,
        client,
        model_name=model_name,
        model_source=source,
        tags=tags,
    )

    with pytest.raises(
        registration_recovery.RegistrationReconciliationPendingError,
        match="still has a pending version",
    ):
        _reconcile_recovery(client, model_name=model_name)

    assert client.deleted_versions == []
    assert not _journal_state(client).retired

    client.version_statuses["1"] = "READY"
    recovery = _reconcile_recovery(client, model_name=model_name)

    assert recovery is not None and recovery.ready_version == 1
    assert recovery.journal_requires_clear
    assert client.deleted_versions == []
    assert not _journal_state(client).retired


def test_uc_search_hydration_rejects_immutable_source_drift() -> None:
    model_name = "mip.audit.mortgage_growth_supervisor_proxy_deadbeef1234"
    authoritative_source = "models:/m-authoritative-source"
    client = _CleanupClient(
        [
            SimpleNamespace(
                name=model_name,
                version="1",
                source=authoritative_source,
                tags={},
                status="READY",
            )
        ]
    )

    class DriftedSearchVersion:
        name = model_name
        version = "1"
        source = "models:/m-drifted-search-source"
        status = "READY"

        def tags(self) -> None:
            raise AssertionError("UC search tags method must never be called")

    client.versions = [DriftedSearchVersion()]

    with pytest.raises(RuntimeError, match="identity drifted during authoritative hydration"):
        registration_recovery._target_model_versions(client, model_name)


def test_uc_search_hydration_rejects_row_outside_exact_target_without_deletion() -> None:
    target_model = "mip.audit.mortgage_growth_supervisor_proxy_deadbeef1234"
    foreign_model = "mip.audit.mortgage_growth_supervisor_proxy_foreign12345"
    source = "models:/m-foreign-search-row"
    client = _CleanupClient(
        [
            SimpleNamespace(
                name=foreign_model,
                version="7",
                source=source,
                tags={},
                status="FAILED_REGISTRATION",
            )
        ]
    )
    client.search_model_versions = lambda **_kwargs: client.versions  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="escaped its exact target model"):
        registration_recovery._target_model_versions(client, target_model)

    assert client.deleted_versions == []


def test_durable_restart_preserves_hidden_incomplete_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_name = "mip.audit.mortgage_growth_supervisor_proxy_deadbeef1234"
    source = "models:/m-hidden-restart"
    tags = _cleanup_tags(monkeypatch, model_name=model_name, model_source=source)

    class HiddenClient(_CleanupClient):
        def search_model_versions(self, query=None, **kwargs):  # type: ignore[no-untyped-def]
            selected = kwargs.get("filter_string") or query
            if selected == f"name='{model_name}'":
                return []
            return super().search_model_versions(query, **kwargs)

    client = HiddenClient(
        [
            SimpleNamespace(
                name=model_name,
                version="1",
                source=source,
                run_id="run-m-hidden-restart",
                tags=tags,
                status="PENDING_REGISTRATION",
            )
        ]
    )
    _persisted_recovery(
        monkeypatch,
        client,
        model_name=model_name,
        model_source=source,
        tags=tags,
    )

    recovery = _reconcile_recovery(client, model_name=model_name)

    assert recovery is not None and recovery.ready_version is None
    assert client.deleted_versions == []
    assert client.deleted_registered_models == []
    assert client.deleted_logged_models == []
    assert client.deleted_runs == []


def test_durable_journal_tamper_fails_closed_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_name = "mip.audit.mortgage_growth_supervisor_proxy_deadbeef1234"
    source = "models:/m-tampered-journal"
    client = _CleanupClient()
    _persisted_recovery(monkeypatch, client, model_name=model_name, model_source=source)
    experiment = client.get_experiment("experiment-7")
    value = _journal_state(client).value
    assert value is not None
    key = journal_store.journal_tag_key(value)
    experiment.tags[key] = experiment.tags[key].replace(model_name, f"{model_name}x")

    with pytest.raises(RuntimeError, match="identity drifted"):
        _reconcile_recovery(client, model_name=model_name)

    assert client.deleted_versions == []
    assert client.deleted_registered_models == []
    assert client.deleted_logged_models == []
    assert client.deleted_runs == []


@pytest.mark.parametrize("invalid", ["{}", "[]", "x" * 5001])
def test_durable_journal_parser_rejects_noncanonical_shapes(invalid: str) -> None:
    with pytest.raises(RuntimeError, match="journal"):
        registration_recovery._parse_durable_journal(invalid)


def test_durable_journal_rejects_previous_attestation_epoch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_name = "mip.audit.mortgage_growth_supervisor_proxy_deadbeef1234"
    source = "models:/m-previous-epoch"
    monkeypatch.setenv("MIP_ALLOW_RUNTIME_MODEL_ATTESTATION_SIGNING", "1")
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY", _PREVIOUS_MODEL_SIGNING_KEY)
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY", _PREVIOUS_MODEL_VERIFY_KEY)
    tags = attestation.sign_gateway_model_contract(
        full_name=model_name,
        model_source=source,
        **_recovery_contract(),
    )
    client = _CleanupClient()
    _persisted_recovery(
        monkeypatch,
        client,
        model_name=model_name,
        model_source=source,
        tags=tags,
    )
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY", _MODEL_SIGNING_KEY)
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY", _MODEL_VERIFY_KEY)
    monkeypatch.setenv(
        "MIP_GATEWAY_MODEL_ATTESTATION_PREVIOUS_VERIFY_KEY",
        _PREVIOUS_MODEL_VERIFY_KEY,
    )

    with pytest.raises(RuntimeError, match="previous attestation epoch"):
        _reconcile_recovery(client, model_name=model_name)

    assert client.deleted_versions == []
    assert client.deleted_logged_models == []
    assert client.deleted_runs == []


def test_durable_reconcile_removes_multiple_exact_failed_versions_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_name = "mip.audit.mortgage_growth_supervisor_proxy_deadbeef1234"
    source = "models:/m-multiple-incomplete"
    tags = _cleanup_tags(monkeypatch, model_name=model_name, model_source=source)
    client = _CleanupClient(
        [
            SimpleNamespace(
                name=model_name,
                version=str(version),
                source=source,
                run_id="run-m-multiple-incomplete",
                tags=tags,
                status=status,
            )
            for version, status in ((1, "FAILED_REGISTRATION"), (2, "FAILED_REGISTRATION"))
        ]
    )
    _persisted_recovery(
        monkeypatch,
        client,
        model_name=model_name,
        model_source=source,
        tags=tags,
    )

    recovery = _reconcile_recovery(client, model_name=model_name)

    assert recovery is not None and recovery.ready_version is None
    assert client.deleted_versions == [(model_name, "1"), (model_name, "2")]
    assert client.deleted_registered_models == []
    assert client.deleted_logged_models == []
    assert client.deleted_runs == []
    assert _journal_state(client).value is not None
    assert not _journal_state(client).retired


def test_durable_persist_retries_exact_write_after_definite_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RetryClient(_CleanupClient):
        tag_writes = 0

        def set_experiment_tag(self, experiment_id: str, key: str, value: str) -> None:
            self.tag_writes += 1
            if self.tag_writes == 1:
                raise RuntimeError("write rejected before commit")
            super().set_experiment_tag(experiment_id, key, value)

    client = RetryClient()
    client.set_experiment("/Users/runtime-client/gateway-recovery")
    durable = registration_recovery.DurableRegistrationJournal(
        model_name="mip.audit.mortgage_growth_supervisor_proxy_deadbeef1234",
        journal=_cleanup_journal(client, model_source="models:/m-retry-write"),
        registration_tags=_cleanup_tags(
            monkeypatch,
            model_name="mip.audit.mortgage_growth_supervisor_proxy_deadbeef1234",
            model_source="models:/m-retry-write",
        ),
    )
    monkeypatch.setattr(registration_recovery, "_EXPERIMENT_TAG_VISIBILITY_INTERVAL_S", 0.0)

    registration_recovery.persist_registration_journal(
        client,
        durable,
        assert_single_writer=_assert_single_writer,
    )

    assert client.tag_writes == 2
    assert _journal_state(client).value == registration_recovery._durable_journal_value(durable)
    assert not _journal_state(client).retired


def test_durable_persist_rejects_lost_writer_lease_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _CleanupClient()
    client.set_experiment("/Users/runtime-client/gateway-recovery")
    durable = registration_recovery.DurableRegistrationJournal(
        model_name="mip.audit.mortgage_growth_supervisor_proxy_deadbeef1234",
        journal=_cleanup_journal(client, model_source="models:/m-lost-writer-persist"),
        registration_tags=_cleanup_tags(
            monkeypatch,
            model_name="mip.audit.mortgage_growth_supervisor_proxy_deadbeef1234",
            model_source="models:/m-lost-writer-persist",
        ),
    )

    def reject_writer() -> None:
        raise RuntimeError("deployment lease is no longer held")

    with pytest.raises(
        registration_recovery.RegistrationJournalPersistencePendingError,
        match="deployment lease is no longer held",
    ):
        registration_recovery.persist_registration_journal(
            client,
            durable,
            assert_single_writer=reject_writer,
        )

    assert _journal_state(client).value is None
    assert not _journal_state(client).retired


def test_durable_persist_survives_lost_response_and_readback_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class LostResponseClient(_CleanupClient):
        fail_readback = False
        tag_writes = 0

        def set_experiment_tag(self, experiment_id: str, key: str, value: str) -> None:
            self.tag_writes += 1
            super().set_experiment_tag(experiment_id, key, value)
            self.fail_readback = True
            raise ConnectionError("response lost after commit")

        def get_experiment(self, experiment_id: str) -> object:
            if self.fail_readback:
                self.fail_readback = False
                raise ConnectionError("readback temporarily unavailable")
            return super().get_experiment(experiment_id)

    client = LostResponseClient()
    client.set_experiment("/Users/runtime-client/gateway-recovery")
    durable = registration_recovery.DurableRegistrationJournal(
        model_name="mip.audit.mortgage_growth_supervisor_proxy_deadbeef1234",
        journal=_cleanup_journal(client, model_source="models:/m-lost-write-response"),
        registration_tags=_cleanup_tags(
            monkeypatch,
            model_name="mip.audit.mortgage_growth_supervisor_proxy_deadbeef1234",
            model_source="models:/m-lost-write-response",
        ),
    )
    monkeypatch.setattr(registration_recovery, "_EXPERIMENT_TAG_VISIBILITY_INTERVAL_S", 0.0)

    registration_recovery.persist_registration_journal(
        client,
        durable,
        assert_single_writer=_assert_single_writer,
    )

    assert client.tag_writes == 1
    assert _journal_state(client).value == registration_recovery._durable_journal_value(durable)
    assert not _journal_state(client).retired


def test_journal_retirement_survives_lost_response_and_rejects_reuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class LostRetirementResponseClient(_CleanupClient):
        def set_experiment_tag(self, experiment_id: str, key: str, value: str) -> None:
            super().set_experiment_tag(experiment_id, key, value)
            if key.startswith(journal_store.RETIREMENT_TAG_PREFIX):
                raise ConnectionError("retirement response lost after commit")

    client = LostRetirementResponseClient()
    first = _persisted_recovery(
        monkeypatch,
        client,
        model_name="mip.audit.mortgage_growth_supervisor_proxy_deadbeef1234",
        model_source="models:/m-terminal-first",
    )
    monkeypatch.setattr(registration_recovery, "_EXPERIMENT_TAG_VISIBILITY_INTERVAL_S", 0.0)

    registration_recovery.clear_registration_journal(
        client,
        first,
        assert_single_writer=_assert_single_writer,
    )
    registration_recovery.clear_registration_journal(
        client,
        first,
        assert_single_writer=_assert_single_writer,
    )

    assert (
        registration_recovery.load_registration_journal(
            client,
            model_name=first.model_name,
            experiment_id=first.journal.experiment_id,
            attestation_contract=_recovery_contract(),
            verify_attestation=lambda **_kwargs: True,
        )
        is None
    )
    assert _journal_state(client).value == registration_recovery._durable_journal_value(first)
    assert _journal_state(client).retired
    second = registration_recovery.DurableRegistrationJournal(
        model_name=first.model_name,
        journal=_cleanup_journal(client, model_source="models:/m-terminal-second"),
        registration_tags=_cleanup_tags(
            monkeypatch,
            model_name=first.model_name,
            model_source="models:/m-terminal-second",
        ),
    )
    with pytest.raises(
        registration_recovery.RegistrationJournalPersistencePendingError,
        match="terminal and cannot be reused",
    ):
        registration_recovery.persist_registration_journal(
            client,
            second,
            assert_single_writer=_assert_single_writer,
        )
    assert _journal_state(client).value == registration_recovery._durable_journal_value(first)
    assert _journal_state(client).retired


def test_journal_retirement_rejects_lost_writer_lease_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _CleanupClient()
    durable = _persisted_recovery(
        monkeypatch,
        client,
        model_name="mip.audit.mortgage_growth_supervisor_proxy_deadbeef1234",
        model_source="models:/m-lost-writer-retire",
    )

    def reject_writer() -> None:
        raise RuntimeError("deployment lease is no longer held")

    with pytest.raises(RuntimeError, match="deployment lease is no longer held"):
        registration_recovery.clear_registration_journal(
            client,
            durable,
            assert_single_writer=reject_writer,
        )

    assert _journal_state(client).value == registration_recovery._durable_journal_value(durable)
    assert not _journal_state(client).retired


def test_append_only_journal_rejects_concurrent_persist_without_overwrite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ConcurrentPersistClient(_CleanupClient):
        trigger = ""
        concurrent_key = ""
        concurrent_value = ""
        injected = False

        def set_experiment_tag(self, experiment_id: str, key: str, value: str) -> None:
            if key == self.trigger and not self.injected:
                self.injected = True
                super().set_experiment_tag(
                    experiment_id,
                    self.concurrent_key,
                    self.concurrent_value,
                )
            super().set_experiment_tag(experiment_id, key, value)

    client = ConcurrentPersistClient()
    client.set_experiment("/Users/runtime-client/gateway-recovery")
    first = registration_recovery.DurableRegistrationJournal(
        model_name="mip.audit.mortgage_growth_supervisor_proxy_deadbeef1234",
        journal=_cleanup_journal(client, model_source="models:/m-concurrent-first"),
        registration_tags=_cleanup_tags(
            monkeypatch,
            model_name="mip.audit.mortgage_growth_supervisor_proxy_deadbeef1234",
            model_source="models:/m-concurrent-first",
        ),
    )
    second = replace(
        first,
        journal=_cleanup_journal(client, model_source="models:/m-concurrent-second"),
        registration_tags=_cleanup_tags(
            monkeypatch,
            model_name=first.model_name,
            model_source="models:/m-concurrent-second",
        ),
    )
    first_value = registration_recovery._durable_journal_value(first)
    second_value = registration_recovery._durable_journal_value(second)
    client.trigger = journal_store.journal_tag_key(first_value)
    client.concurrent_key = journal_store.journal_tag_key(second_value)
    client.concurrent_value = second_value
    monkeypatch.setattr(registration_recovery, "_EXPERIMENT_TAG_VISIBILITY_INTERVAL_S", 0.0)

    with pytest.raises(
        registration_recovery.RegistrationJournalPersistencePendingError,
        match="multiple durable registration journals",
    ):
        registration_recovery.persist_registration_journal(
            client,
            first,
            assert_single_writer=_assert_single_writer,
        )

    tags = client.get_experiment("experiment-7").tags
    assert tags[journal_store.journal_tag_key(first_value)] == first_value
    assert tags[journal_store.journal_tag_key(second_value)] == second_value


def test_append_only_retirement_rejects_concurrent_journal_without_overwrite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ConcurrentRetirementClient(_CleanupClient):
        trigger = ""
        concurrent_key = ""
        concurrent_value = ""
        injected = False

        def set_experiment_tag(self, experiment_id: str, key: str, value: str) -> None:
            if key == self.trigger and not self.injected:
                self.injected = True
                super().set_experiment_tag(
                    experiment_id,
                    self.concurrent_key,
                    self.concurrent_value,
                )
            super().set_experiment_tag(experiment_id, key, value)

    client = ConcurrentRetirementClient()
    first = _persisted_recovery(
        monkeypatch,
        client,
        model_name="mip.audit.mortgage_growth_supervisor_proxy_deadbeef1234",
        model_source="models:/m-clear-race-first",
    )
    second = replace(
        first,
        journal=_cleanup_journal(client, model_source="models:/m-clear-race-second"),
        registration_tags=_cleanup_tags(
            monkeypatch,
            model_name=first.model_name,
            model_source="models:/m-clear-race-second",
        ),
    )
    first_value = registration_recovery._durable_journal_value(first)
    second_value = registration_recovery._durable_journal_value(second)
    client.trigger = journal_store.retirement_tag_key(first_value)
    client.concurrent_key = journal_store.journal_tag_key(second_value)
    client.concurrent_value = second_value

    with pytest.raises(RuntimeError, match="multiple durable registration journals"):
        registration_recovery.clear_registration_journal(
            client,
            first,
            assert_single_writer=_assert_single_writer,
        )

    tags = client.get_experiment("experiment-7").tags
    assert tags[journal_store.journal_tag_key(first_value)] == first_value
    assert tags[journal_store.journal_tag_key(second_value)] == second_value
    assert tags[journal_store.retirement_tag_key(first_value)] == (
        journal_store.retirement_tag_value(first_value)
    )


def test_journal_retirement_never_accepts_transient_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TransientAbsenceClient(_CleanupClient):
        hidden_reads = 0

        def set_experiment_tag(self, experiment_id: str, key: str, value: str) -> None:
            if key.startswith(journal_store.RETIREMENT_TAG_PREFIX):
                self.hidden_reads = 2
                raise RuntimeError("retirement rejected before commit")
            super().set_experiment_tag(experiment_id, key, value)

        def get_experiment(self, experiment_id: str) -> object:
            experiment = super().get_experiment(experiment_id)
            if self.hidden_reads:
                self.hidden_reads -= 1
                return SimpleNamespace(experiment_id=experiment_id, tags={})
            return experiment

    client = TransientAbsenceClient()
    durable = _persisted_recovery(
        monkeypatch,
        client,
        model_name="mip.audit.mortgage_growth_supervisor_proxy_deadbeef1234",
        model_source="models:/m-transient-absence",
    )
    monkeypatch.setattr(registration_recovery, "_EXPERIMENT_TAG_VISIBILITY_ATTEMPTS", 4)
    monkeypatch.setattr(registration_recovery, "_EXPERIMENT_TAG_VISIBILITY_INTERVAL_S", 0.0)

    with pytest.raises(RuntimeError, match="retirement was not authoritative"):
        registration_recovery.clear_registration_journal(
            client,
            durable,
            assert_single_writer=_assert_single_writer,
        )

    assert _journal_state(client).value == registration_recovery._durable_journal_value(durable)
    assert not _journal_state(client).retired


def test_legacy_single_tag_journal_retires_append_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _CleanupClient()
    client.set_experiment("/Users/runtime-client/gateway-recovery")
    durable = registration_recovery.DurableRegistrationJournal(
        model_name="mip.audit.mortgage_growth_supervisor_proxy_deadbeef1234",
        journal=_cleanup_journal(client, model_source="models:/m-legacy-live"),
        registration_tags=_cleanup_tags(
            monkeypatch,
            model_name="mip.audit.mortgage_growth_supervisor_proxy_deadbeef1234",
            model_source="models:/m-legacy-live",
        ),
    )
    value = registration_recovery._durable_journal_value(durable)
    client.get_experiment("experiment-7").tags[journal_store.JOURNAL_TAG] = value
    monkeypatch.setattr(registration_recovery, "_EXPERIMENT_TAG_VISIBILITY_INTERVAL_S", 0.0)

    registration_recovery.clear_registration_journal(
        client,
        durable,
        assert_single_writer=_assert_single_writer,
    )

    tags = client.get_experiment("experiment-7").tags
    assert tags[journal_store.JOURNAL_TAG] == value
    assert tags[journal_store.retirement_tag_key(value)] == (
        journal_store.retirement_tag_value(value)
    )
    assert _journal_state(client).retired


def test_forged_retired_pair_cannot_bypass_signed_journal_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_hash = gateway_agent_source_hash(
        upstream_endpoint="managed-supervisor",
        catalog=_CATALOG,
        genie_space_id=_GENIE_SPACE_ID,
    )
    resource_hash = _resource_hash(source_hash)
    model_name = gateway.gateway_agent_model_name(
        base_model_name="mip.audit.mortgage_growth_supervisor_proxy",
        contract_hash=resource_hash,
    )
    model_source = "models:/m-forged-retired-pair"
    monkeypatch.setenv("MIP_ALLOW_RUNTIME_MODEL_ATTESTATION_SIGNING", "1")
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY", _MODEL_SIGNING_KEY)
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY", _MODEL_VERIFY_KEY)
    tags = attestation.sign_gateway_model_contract(
        full_name=model_name,
        model_source=model_source,
        source_hash=source_hash,
        supervisor_id=_SUPERVISOR_ID,
        supervisor_endpoint_id=_SUPERVISOR_ENDPOINT_ID,
        upstream_endpoint="managed-supervisor",
        runtime_application_id=_RUNTIME_APPLICATION_ID,
        model_family="mip.audit.mortgage_growth_supervisor_proxy",
        experiment_base="mip-agent-runtime-gateway-proxy",
        catalog=_CATALOG,
        genie_space_id=_GENIE_SPACE_ID,
        inference_schema="audit",
        inference_table_prefix="mip_agent_gateway_growth_agent",
    )
    client = _CleanupClient(
        [
            SimpleNamespace(
                name=model_name,
                version="5",
                source=model_source,
                run_id="run-forged-retired-pair",
                tags=tags,
                status="READY",
            )
        ]
    )
    _patch_mlflow(monkeypatch, client=client)
    experiment_name = gateway.gateway_experiment_name(
        base_experiment_name="mip-agent-runtime-gateway-proxy",
        contract_hash=resource_hash,
        runtime_application_id=_RUNTIME_APPLICATION_ID,
    )
    experiment = client.set_experiment(experiment_name)
    forged = "not-a-signed-or-canonical-journal"
    experiment.tags[journal_store.journal_tag_key(forged)] = forged
    experiment.tags[journal_store.retirement_tag_key(forged)] = journal_store.retirement_tag_value(
        forged
    )
    serving = _ServingEndpoints()
    monkeypatch.setattr(
        gateway,
        "_log_gateway_model",
        lambda **_kwargs: pytest.fail("forged retired bytes must fail before model logging"),
    )

    with pytest.raises(RuntimeError, match="not strict JSON"):
        _ensure_gateway(_runtime_workspace(serving))

    assert serving.created == []


def test_gateway_endpoint_creation_reasserts_exclusive_deployment_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_hash = gateway_agent_source_hash(
        upstream_endpoint="managed-supervisor",
        catalog=_CATALOG,
        genie_space_id=_GENIE_SPACE_ID,
    )
    resource_hash = _resource_hash(source_hash)
    model_name = gateway.gateway_agent_model_name(
        base_model_name="mip.audit.mortgage_growth_supervisor_proxy",
        contract_hash=resource_hash,
    )
    model_source = "models:/m-lease-recheck"
    monkeypatch.setenv("MIP_ALLOW_RUNTIME_MODEL_ATTESTATION_SIGNING", "1")
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY", _MODEL_SIGNING_KEY)
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY", _MODEL_VERIFY_KEY)
    tags = attestation.sign_gateway_model_contract(
        full_name=model_name,
        model_source=model_source,
        source_hash=source_hash,
        supervisor_id=_SUPERVISOR_ID,
        supervisor_endpoint_id=_SUPERVISOR_ENDPOINT_ID,
        upstream_endpoint="managed-supervisor",
        runtime_application_id=_RUNTIME_APPLICATION_ID,
        model_family="mip.audit.mortgage_growth_supervisor_proxy",
        experiment_base="mip-agent-runtime-gateway-proxy",
        catalog=_CATALOG,
        genie_space_id=_GENIE_SPACE_ID,
        inference_schema="audit",
        inference_table_prefix="mip_agent_gateway_growth_agent",
    )
    client = _Client(
        [
            SimpleNamespace(
                name=model_name, version="8", source=model_source, tags=tags, status="READY"
            )
        ]
    )
    _patch_mlflow(monkeypatch, client=client)
    lease_checks = 0

    def assert_held(*_args: object, **_kwargs: object) -> dict[str, str]:
        nonlocal lease_checks
        lease_checks += 1
        if lease_checks == 3:
            raise RuntimeError("deployment lease disappeared before endpoint creation")
        return {}

    monkeypatch.setattr(gateway.app_deployment_lease, "assert_held", assert_held)
    serving = _ServingEndpoints()

    with pytest.raises(RuntimeError, match="lease disappeared before endpoint creation"):
        _ensure_gateway(_runtime_workspace(serving))

    assert lease_checks == 3
    assert serving.created == []


def test_definite_journal_write_failure_quarantines_orphan_on_fresh_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _CleanupClient()
    client.fail_experiment_tag_set = True
    _patch_mlflow(monkeypatch, client=client)
    monkeypatch.setattr(
        gateway,
        "_log_gateway_model",
        lambda **_kwargs: SimpleNamespace(model_uri="models:/m-definite-write-failure"),
    )
    registrations: list[str] = []
    monkeypatch.setattr(
        gateway.mlflow,
        "register_model",
        lambda model_uri, *_args, **_kwargs: registrations.append(model_uri),
    )
    serving = _ServingEndpoints()

    with pytest.raises(
        registration_recovery.RegistrationJournalPersistencePendingError,
        match="preserving source",
    ):
        _ensure_gateway(_runtime_workspace(serving))

    assert registrations == []
    assert "m-definite-write-failure" in client.logged_models
    assert "run-m-definite-write-failure" in client.runs
    assert client.deleted_logged_models == []
    assert client.deleted_runs == []
    assert serving.created == []

    client.fail_experiment_tag_set = False
    monkeypatch.setattr(
        gateway,
        "_log_gateway_model",
        lambda **_kwargs: pytest.fail("quarantined process must not log another source"),
    )
    monkeypatch.setattr(
        gateway.mlflow,
        "register_model",
        lambda model_uri, _name, *, tags: _registered(
            client,
            model_uri,
            version="4",
            tags=tags,
        ),
    )

    with pytest.raises(RuntimeError, match="operator quarantine required"):
        _ensure_gateway(_runtime_workspace(serving))

    assert registrations == []
    assert client.deleted_logged_models == []
    assert client.deleted_runs == []
    assert serving.created == []


def test_orphan_discovery_fails_closed_on_multiple_sources_without_mutation() -> None:
    client = _CleanupClient()
    _add_logged_source(client, "m-first-orphan")
    _add_logged_source(client, "m-second-orphan")

    with pytest.raises(RuntimeError, match="unjournaled.*m-first-orphan,m-second-orphan"):
        registration_recovery.require_no_unjournaled_gateway_sources(
            client,
            experiment_id="experiment-7",
            expected_logged_model_name="mortgage_growth_supervisor_proxy",
        )

    assert client.deleted_versions == []
    assert client.deleted_logged_models == []
    assert client.deleted_runs == []


def test_orphan_discovery_ignores_exact_registered_references() -> None:
    source = "models:/m-referenced-log"
    client = _CleanupClient(
        [
            SimpleNamespace(
                name="mip.audit.other_model",
                version="9",
                source=source,
                run_id="run-m-referenced-log",
                tags={},
                status="READY",
            )
        ]
    )
    _add_logged_source(client, "m-referenced-log")

    registration_recovery.require_no_unjournaled_gateway_sources(
        client,
        experiment_id="experiment-7",
        expected_logged_model_name="mortgage_growth_supervisor_proxy",
    )
    assert client.deleted_versions == []
    assert client.deleted_logged_models == []
    assert client.deleted_runs == []


def test_orphan_discovery_exhausts_pages_before_selecting_source() -> None:
    class Page(list[object]):
        def __init__(self, values: list[object], token: str = "") -> None:
            super().__init__(values)
            self.token = token

    class PaginatedClient(_CleanupClient):
        logged_page_tokens: list[str | None] = []
        logged_page_sizes: list[int | None] = []

        def search_logged_models(
            self,
            *,
            experiment_ids: list[str],
            max_results: int | None = None,
            page_token: str | None = None,
        ) -> Page:
            assert experiment_ids == ["experiment-7"]
            assert max_results == registration_recovery._LOGGED_MODEL_SEARCH_PAGE_SIZE == 50
            self.logged_page_tokens.append(page_token)
            self.logged_page_sizes.append(max_results)
            values = list(self.logged_models.values())
            return Page(values[:1], "next") if page_token is None else Page(values[1:])

    client = PaginatedClient()
    _add_logged_source(client, "m-page-one")
    _add_logged_source(client, "m-page-two")
    client.versions.append(
        SimpleNamespace(
            name="mip.audit.other_model",
            version="8",
            source="models:/m-page-one",
            run_id="run-m-page-one",
            tags={},
            status="READY",
        )
    )

    with pytest.raises(RuntimeError, match="operator quarantine required: m-page-two"):
        registration_recovery.require_no_unjournaled_gateway_sources(
            client,
            experiment_id="experiment-7",
            expected_logged_model_name="mortgage_growth_supervisor_proxy",
        )

    assert client.logged_page_tokens == [None, "next"]
    assert client.logged_page_sizes == [50, 50]


def test_orphan_discovery_rejects_repeated_logged_model_page_token() -> None:
    class Page(list[object]):
        token = "repeated"

    class RepeatedTokenClient(_CleanupClient):
        logged_page_sizes: list[int | None] = []

        def search_logged_models(
            self,
            *,
            experiment_ids: list[str],
            max_results: int | None = None,
            page_token: str | None = None,
        ) -> Page:
            assert experiment_ids == ["experiment-7"]
            assert page_token in (None, "repeated")
            assert max_results == registration_recovery._LOGGED_MODEL_SEARCH_PAGE_SIZE == 50
            self.logged_page_sizes.append(max_results)
            return Page()

    client = RepeatedTokenClient()

    with pytest.raises(RuntimeError, match="logged-model search repeated a pagination token"):
        registration_recovery.require_no_unjournaled_gateway_sources(
            client,
            experiment_id="experiment-7",
            expected_logged_model_name="mortgage_growth_supervisor_proxy",
        )

    assert client.logged_page_sizes == [50, 50]


def test_orphan_discovery_rejects_name_and_identity_drift() -> None:
    wrong_name = _CleanupClient()
    _add_logged_source(wrong_name, "m-wrong-name", name="another_model")
    with pytest.raises(RuntimeError, match="unexpected logged model"):
        registration_recovery.require_no_unjournaled_gateway_sources(
            wrong_name,
            experiment_id="experiment-7",
            expected_logged_model_name="mortgage_growth_supervisor_proxy",
        )

    wrong_identity = _CleanupClient()
    _add_logged_source(wrong_identity, "m-wrong-identity")
    wrong_identity.logged_models["m-wrong-identity"].source_run_id = "run-drifted"
    wrong_identity.runs["run-drifted"] = SimpleNamespace(
        info=SimpleNamespace(experiment_id="another-experiment")
    )
    with pytest.raises(RuntimeError, match="experiment identity drifted"):
        registration_recovery.require_no_unjournaled_gateway_sources(
            wrong_identity,
            experiment_id="experiment-7",
            expected_logged_model_name="mortgage_growth_supervisor_proxy",
        )


def test_registration_lost_response_reuses_authoritative_ready_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _CleanupClient()
    _patch_mlflow(monkeypatch, client=client)
    monkeypatch.setattr(
        gateway,
        "_log_gateway_model",
        lambda **_kwargs: SimpleNamespace(model_uri="models:/m-ready-after-lost-response"),
    )

    def lost_registration_response(model_uri: str, name: str, *, tags: dict[str, str]) -> object:
        version = SimpleNamespace(
            name=name,
            version="7",
            source=model_uri,
            run_id="run-m-ready-after-lost-response",
            tags=dict(tags),
            status="READY",
        )
        client.versions.append(version)
        client.version_tags["7"] = dict(tags)
        client.version_sources["7"] = model_uri
        client.version_statuses["7"] = "READY"
        raise ConnectionError("registration response lost after commit")

    monkeypatch.setattr(gateway.mlflow, "register_model", lost_registration_response)
    serving = _ServingEndpoints()

    deployment = _ensure_gateway(_runtime_workspace(serving))

    assert deployment.model_version == 7
    assert client.deleted_versions == []
    assert client.deleted_registered_models == []
    assert client.deleted_logged_models == []
    assert client.deleted_runs == []
    assert _journal_state(client).retired
    assert len(serving.created) == 1


def test_endpoint_creation_interruption_preserves_active_registration_journal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _CleanupClient()
    _patch_mlflow(monkeypatch, client=client)
    monkeypatch.setattr(
        gateway,
        "_log_gateway_model",
        lambda **_kwargs: SimpleNamespace(model_uri="models:/m-endpoint-interrupted"),
    )
    monkeypatch.setattr(
        gateway.mlflow,
        "register_model",
        lambda model_uri, _name, *, tags: _registered(
            client,
            model_uri,
            version="5",
            tags=tags,
        ),
    )

    class InterruptedCreate(_ServingEndpoints):
        def create(self, **kwargs: Any) -> None:
            self.created.append(kwargs)
            raise ConnectionError("endpoint creation response unavailable")

    serving = InterruptedCreate()

    with pytest.raises(ConnectionError, match="creation response unavailable"):
        _ensure_gateway(_runtime_workspace(serving))

    assert _journal_state(client).value is not None
    assert not _journal_state(client).retired
    assert len(serving.created) == 1


def test_registration_journal_clears_only_after_exact_endpoint_postflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _CleanupClient()
    _patch_mlflow(monkeypatch, client=client)
    monkeypatch.setattr(
        gateway,
        "_log_gateway_model",
        lambda **_kwargs: SimpleNamespace(model_uri="models:/m-postflight-before-clear"),
    )
    monkeypatch.setattr(
        gateway.mlflow,
        "register_model",
        lambda model_uri, _name, *, tags: _registered(
            client,
            model_uri,
            version="5",
            tags=tags,
        ),
    )
    serving = _ServingEndpoints()
    workspace = _full_verifier_workspace(serving)
    postflight_events: list[str] = []

    def full_postflight(
        selected_workspace: Any,
        deployment: GatewayAgentDeployment,
        **kwargs: Any,
    ) -> None:
        _verify_gateway_responses_agent(
            selected_workspace,
            deployment,
            **kwargs,
        )
        postflight_events.append("verify")

    monkeypatch.setattr(gateway, "verify_gateway_responses_agent", full_postflight)
    real_clear = gateway.clear_registration_journal
    clear_observations: list[str] = []

    def clear_after_endpoint(
        selected_client: Any,
        durable: Any,
        *,
        assert_single_writer: Any,
    ) -> None:
        assert len(serving.created) == 1
        endpoint_name = str(serving.created[0]["name"])
        details = serving.get(endpoint_name)
        assert str(getattr(details, "id", "") or "") == f"{endpoint_name}-id"
        assert getattr(details, "pending_config", None) is None
        assert postflight_events == ["verify"]
        postflight_events.append("clear")
        clear_observations.append(endpoint_name)
        real_clear(
            selected_client,
            durable,
            assert_single_writer=assert_single_writer,
        )

    monkeypatch.setattr(gateway, "clear_registration_journal", clear_after_endpoint)

    deployment = _ensure_gateway(workspace)

    assert clear_observations == [deployment.endpoint]
    assert postflight_events == ["verify", "clear"]
    assert _journal_state(client).retired


def test_registration_journal_stays_active_when_full_postflight_rejects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _CleanupClient()
    _patch_mlflow(monkeypatch, client=client)
    monkeypatch.setattr(
        gateway,
        "_log_gateway_model",
        lambda **_kwargs: SimpleNamespace(model_uri="models:/m-postflight-rejected"),
    )
    monkeypatch.setattr(
        gateway.mlflow,
        "register_model",
        lambda model_uri, _name, *, tags: _registered(
            client,
            model_uri,
            version="5",
            tags=tags,
        ),
    )
    serving = _ServingEndpoints()
    postflight_events: list[str] = []

    def reject_postflight(*_args: Any, **_kwargs: Any) -> None:
        postflight_events.append("verify")
        raise RuntimeError("full Gateway verifier rejected")

    monkeypatch.setattr(gateway, "verify_gateway_responses_agent", reject_postflight)
    monkeypatch.setattr(
        gateway,
        "clear_registration_journal",
        lambda *_args, **_kwargs: pytest.fail(
            "registration journal must remain active after verifier rejection"
        ),
    )

    with pytest.raises(RuntimeError, match="full Gateway verifier rejected"):
        _ensure_gateway(_runtime_workspace(serving))

    assert postflight_events == ["verify"]
    assert _journal_state(client).value is not None
    assert not _journal_state(client).retired
    assert len(serving.created) == 1


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        ("ready", "not READY"),
        ("task", "not agent/v1/responses"),
        ("model-owner", "registered model"),
        ("model-tags", "attestation"),
        ("experiment-identity", "name/ID binding drifted"),
        ("experiment-acl", "experiment ACL"),
    ],
)
def test_full_postflight_failure_never_retires_registration_journal(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    message: str,
) -> None:
    client = _CleanupClient()
    _patch_mlflow(monkeypatch, client=client)
    monkeypatch.setattr(
        gateway,
        "_log_gateway_model",
        lambda **_kwargs: SimpleNamespace(
            model_uri=f"models:/m-postflight-{failure}"
        ),
    )
    monkeypatch.setattr(
        gateway.mlflow,
        "register_model",
        lambda model_uri, _name, *, tags: _registered(
            client,
            model_uri,
            version="5",
            tags=tags,
        ),
    )
    serving = _ServingEndpoints()
    workspace = _full_verifier_workspace(serving)
    verifier_calls = 0

    def reject_drifted_postflight(
        selected_workspace: Any,
        deployment: GatewayAgentDeployment,
        **kwargs: Any,
    ) -> None:
        nonlocal verifier_calls
        verifier_calls += 1
        details: Any = serving.get(deployment.endpoint)
        if failure == "ready":
            details.state.ready = "NOT_READY"
        elif failure == "task":
            details.task = "llm/v1/chat"
        elif failure == "model-owner":
            selected_workspace.registered_models.get = lambda _name: SimpleNamespace(
                owner="unreviewed-owner"
            )
        elif failure == "model-tags":
            client.version_tags[str(deployment.model_version)] = {}
        elif failure == "experiment-identity":
            experiment: Any = client.get_experiment(deployment.experiment_id)
            assert experiment is not None
            experiment.name = "/Users/other/drifted-experiment"
        elif failure == "experiment-acl":
            selected_workspace.api_client = SimpleNamespace(
                do=lambda _method, _path: {
                    "access_control_list": [
                        {
                            "user_name": "unreviewed@example.com",
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
            )
        else:
            raise AssertionError(f"unexpected full-verifier failure fixture {failure}")
        _verify_gateway_responses_agent(
            selected_workspace,
            deployment,
            **kwargs,
        )

    monkeypatch.setattr(
        gateway,
        "verify_gateway_responses_agent",
        reject_drifted_postflight,
    )
    monkeypatch.setattr(
        gateway,
        "clear_registration_journal",
        lambda *_args, **_kwargs: pytest.fail(
            "registration journal must remain active after full-verifier failure"
        ),
    )

    with pytest.raises(RuntimeError, match=message):
        _ensure_gateway(workspace)

    assert verifier_calls == 1
    assert _journal_state(client).value is not None
    assert not _journal_state(client).retired
    assert len(serving.created) == 1


def test_journal_clear_failure_follows_exact_endpoint_and_preserves_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _CleanupClient()
    client.fail_experiment_tag_clear = True
    _patch_mlflow(monkeypatch, client=client)
    monkeypatch.setattr(
        gateway,
        "_log_gateway_model",
        lambda **_kwargs: SimpleNamespace(model_uri="models:/m-clear-failure"),
    )
    monkeypatch.setattr(
        gateway.mlflow,
        "register_model",
        lambda model_uri, _name, *, tags: _registered(
            client,
            model_uri,
            version="5",
            tags=tags,
        ),
    )
    serving = _ServingEndpoints()

    monkeypatch.setattr(registration_recovery, "_EXPERIMENT_TAG_VISIBILITY_INTERVAL_S", 0.0)

    with pytest.raises(RuntimeError, match="retirement was not authoritative"):
        _ensure_gateway(_runtime_workspace(serving))

    assert _journal_state(client).value is not None
    assert not _journal_state(client).retired
    assert client.deleted_logged_models == []
    assert client.deleted_runs == []
    assert len(serving.created) == 1


def test_ensure_gateway_agent_creates_versioned_green_without_mutating_live_drift(
    monkeypatch,
) -> None:
    source_hash = gateway_agent_source_hash(
        upstream_endpoint="managed-supervisor",
        catalog=_CATALOG,
        genie_space_id=_GENIE_SPACE_ID,
    )
    client = _Client(
        [
            SimpleNamespace(
                version="5",
                source="models:/m-reviewed",
                tags={
                    gateway.SOURCE_HASH_TAG: source_hash,
                    gateway.UPSTREAM_TAG: "managed-supervisor",
                },
            )
        ]
    )
    _patch_mlflow(monkeypatch, client=client)
    details = SimpleNamespace(
        creator=_RUNTIME_APPLICATION_ID,
        config=SimpleNamespace(
            served_entities=[
                SimpleNamespace(
                    entity_name="mip.audit.mortgage_growth_supervisor_proxy",
                    entity_version="4",
                )
            ]
        ),
        ai_gateway=SimpleNamespace(
            inference_table_config=SimpleNamespace(
                enabled=True,
                catalog_name="mip",
                schema_name="audit",
                table_name_prefix="stale_prefix",
            )
        ),
    )
    serving = _ServingEndpoints(details)

    deployment = ensure_gateway_responses_agent(
        SimpleNamespace(
            serving_endpoints=serving,
            registered_models=SimpleNamespace(
                get=lambda _name: SimpleNamespace(owner=_RUNTIME_APPLICATION_ID)
            ),
        ),
        endpoint="mip-growth-agent-gateway",
        endpoint_prefix="mip-growth-agent-gateway",
        supervisor_id=_SUPERVISOR_ID,
        upstream_endpoint="managed-supervisor",
        model_name="mip.audit.mortgage_growth_supervisor_proxy",
        experiment_name="mip-agent-runtime-gateway-proxy",
        inference_catalog="mip",
        inference_schema="audit",
        inference_table_prefix="mip_agent_gateway_growth_agent",
        genie_space_id=_GENIE_SPACE_ID,
        expected_creator_application_id=_RUNTIME_APPLICATION_ID,
    )

    assert deployment.model_version == 5
    assert deployment.endpoint == (
        f"mip-growth-agent-gateway-{_resource_hash(source_hash)[:12]}-mq1"
    )
    assert len(serving.created) == 1
    assert serving.created[0]["name"] == deployment.endpoint
    assert serving.updated == []
    inference = serving.created[0]["ai_gateway"].inference_table_config
    assert inference.table_name_prefix == gateway.gateway_inference_table_prefix(
        base_prefix="mip_agent_gateway_growth_agent",
        contract_hash=_resource_hash(source_hash),
    )
    assert serving.gateway_updates == []
    assert serving.patches == []
    assert serving.events == []
    assert details.config.served_entities[0].entity_version == "4"
    assert details.ai_gateway.inference_table_config.table_name_prefix == "stale_prefix"


def test_human_owned_legacy_gateway_creates_runtime_green_without_mutation(monkeypatch) -> None:
    client = _Client()
    _patch_mlflow(monkeypatch, client=client)
    monkeypatch.setattr(
        gateway,
        "_log_gateway_model",
        lambda **_kwargs: SimpleNamespace(model_uri="models:/m-reviewed-proxy"),
    )
    monkeypatch.setattr(
        gateway.mlflow,
        "register_model",
        lambda model_uri, _name, *, tags: _registered(
            client,
            model_uri,
            version="1",
            tags=tags,
        ),
    )
    legacy = SimpleNamespace(
        creator="skyler@entrada.ai",
        pending_config=None,
        config=SimpleNamespace(served_entities=[]),
        tags=[],
        ai_gateway=None,
    )
    serving = _ServingEndpoints(legacy)

    deployment = ensure_gateway_responses_agent(
        SimpleNamespace(
            serving_endpoints=serving,
            registered_models=SimpleNamespace(
                get=lambda _name: SimpleNamespace(owner=_RUNTIME_APPLICATION_ID)
            ),
        ),
        endpoint="mip-growth-agent-gateway",
        endpoint_prefix="mip-growth-agent-gateway",
        supervisor_id=_SUPERVISOR_ID,
        upstream_endpoint="managed-supervisor",
        model_name="mip.audit.mortgage_growth_supervisor_proxy",
        experiment_name="mip-agent-runtime-gateway-proxy",
        inference_catalog="mip",
        inference_schema="audit",
        inference_table_prefix="mip_agent_gateway_growth_agent",
        genie_space_id=_GENIE_SPACE_ID,
        expected_creator_application_id=_RUNTIME_APPLICATION_ID,
    )

    assert deployment.endpoint.startswith("mip-growth-agent-gateway-")
    assert len(serving.created) == 1
    assert serving.updated == []
    assert legacy.creator == "skyler@entrada.ai"


def test_key_rotation_allocates_current_green_without_mutating_previous_blue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_hash = gateway_agent_source_hash(
        upstream_endpoint="managed-supervisor",
        catalog=_CATALOG,
        genie_space_id=_GENIE_SPACE_ID,
    )
    blue = _exact_endpoint_details(
        source_hash=source_hash,
        model_version=3,
        verify_key=_PREVIOUS_MODEL_VERIFY_KEY,
    )
    blue_entity = blue.config.served_entities[0]
    blue_name = blue_entity.entity_name
    blue_environment = dict(blue_entity.environment_vars)
    previous_tags = {"immutable": "previous-signed-envelope"}

    client = _Client()
    client.version_tags["3"] = dict(previous_tags)
    client.version_sources["3"] = "models:/m-previous-reviewed-proxy"
    _patch_mlflow(monkeypatch, client=client)
    monkeypatch.setenv(
        "MIP_GATEWAY_MODEL_ATTESTATION_PREVIOUS_VERIFY_KEY",
        _PREVIOUS_MODEL_VERIFY_KEY,
    )
    monkeypatch.setattr(
        gateway,
        "_log_gateway_model",
        lambda **_kwargs: SimpleNamespace(model_uri="models:/m-current-reviewed-proxy"),
    )
    monkeypatch.setattr(
        gateway.mlflow,
        "register_model",
        lambda model_uri, _name, *, tags: _registered(
            client,
            model_uri,
            version="1",
            tags=tags,
        ),
    )
    serving = _ServingEndpoints(blue)

    deployment = ensure_gateway_responses_agent(
        SimpleNamespace(
            serving_endpoints=serving,
            registered_models=SimpleNamespace(
                get=lambda _name: SimpleNamespace(owner=_RUNTIME_APPLICATION_ID)
            ),
        ),
        endpoint="mip-growth-agent-gateway",
        endpoint_prefix="mip-growth-agent-gateway",
        supervisor_id=_SUPERVISOR_ID,
        upstream_endpoint="managed-supervisor",
        model_name="mip.audit.mortgage_growth_supervisor_proxy",
        experiment_name="mip-agent-runtime-gateway-proxy",
        inference_catalog="mip",
        inference_schema="audit",
        inference_table_prefix="mip_agent_gateway_growth_agent",
        genie_space_id=_GENIE_SPACE_ID,
        expected_creator_application_id=_RUNTIME_APPLICATION_ID,
    )

    assert deployment.model_attestation_verify_key == _MODEL_VERIFY_KEY
    assert deployment.model_name != blue_name
    assert deployment.endpoint == (
        f"mip-growth-agent-gateway-{deployment.resource_hash[:12]}-mq1"
    )
    created_environment = serving.created[0]["config"].served_entities[0].environment_vars
    assert "MIP_GATEWAY_MODEL_ATTESTATION_PREVIOUS_VERIFY_KEY" not in created_environment
    assert serving.updated == []
    assert blue.config.served_entities[0].entity_name == blue_name
    assert dict(blue.config.served_entities[0].environment_vars) == blue_environment
    assert client.version_tags["3"] == previous_tags


def _assert_identity_rotation_allocates_distinct_green(
    monkeypatch: pytest.MonkeyPatch,
    *,
    supervisor_id: str,
    supervisor_endpoint_id: str,
    runtime_application_id: str,
) -> None:
    source_hash = gateway_agent_source_hash(
        upstream_endpoint="managed-supervisor",
        catalog=_CATALOG,
        genie_space_id=_GENIE_SPACE_ID,
    )
    blue = _exact_endpoint_details(source_hash=source_hash, model_version=3)
    blue_entity = blue.config.served_entities[0]
    blue_snapshot = (
        blue.creator,
        blue_entity.entity_name,
        dict(blue_entity.environment_vars),
        blue.ai_gateway.inference_table_config.table_name_prefix,
        [(tag.key, tag.value) for tag in blue.tags],
    )
    old_hash = _resource_hash(source_hash)
    old_experiment = gateway.gateway_experiment_name(
        base_experiment_name="mip-agent-runtime-gateway-proxy",
        contract_hash=old_hash,
        runtime_application_id=_RUNTIME_APPLICATION_ID,
    )
    old_table = "mip.audit." + gateway.gateway_inference_table_prefix(
        base_prefix="mip_agent_gateway_growth_agent",
        contract_hash=old_hash,
    )

    client = _Client(experiment_owner=runtime_application_id)
    _patch_mlflow(monkeypatch, client=client)
    monkeypatch.setattr(
        gateway,
        "_log_gateway_model",
        lambda **_kwargs: SimpleNamespace(model_uri="models:/m-identity-rotated-proxy"),
    )
    monkeypatch.setattr(
        gateway.mlflow,
        "register_model",
        lambda model_uri, _name, *, tags: _registered(
            client,
            model_uri,
            version="1",
            tags=tags,
        ),
    )
    serving = _ServingEndpoints(
        blue,
        supervisor_endpoint_id=supervisor_endpoint_id,
        supervisor_endpoint_creator=runtime_application_id,
    )
    deployment = ensure_gateway_responses_agent(
        SimpleNamespace(
            serving_endpoints=serving,
            registered_models=SimpleNamespace(
                get=lambda _name: SimpleNamespace(owner=runtime_application_id)
            ),
        ),
        endpoint="mip-growth-agent-gateway",
        endpoint_prefix="mip-growth-agent-gateway",
        supervisor_id=supervisor_id,
        upstream_endpoint="managed-supervisor",
        model_name="mip.audit.mortgage_growth_supervisor_proxy",
        experiment_name="mip-agent-runtime-gateway-proxy",
        inference_catalog="mip",
        inference_schema="audit",
        inference_table_prefix="mip_agent_gateway_growth_agent",
        genie_space_id=_GENIE_SPACE_ID,
        expected_creator_application_id=runtime_application_id,
    )

    expected_hash = _resource_hash(
        source_hash,
        supervisor_id=supervisor_id,
        supervisor_endpoint_id=supervisor_endpoint_id,
        runtime_application_id=runtime_application_id,
    )
    assert deployment.resource_hash == expected_hash
    assert deployment.resource_hash != old_hash
    assert deployment.model_name != blue_entity.entity_name
    assert deployment.experiment_name != old_experiment
    assert deployment.inference_table != old_table
    assert deployment.endpoint == f"mip-growth-agent-gateway-{expected_hash[:12]}-mq1"
    assert len(serving.created) == 1
    assert serving.created[0]["name"] == deployment.endpoint
    assert serving.updated == []
    assert serving.gateway_updates == []
    assert serving.patches == []
    assert serving.events == []
    assert (
        blue.creator,
        blue_entity.entity_name,
        dict(blue_entity.environment_vars),
        blue.ai_gateway.inference_table_config.table_name_prefix,
        [(tag.key, tag.value) for tag in blue.tags],
    ) == blue_snapshot
    registered_contract = attestation.gateway_model_contract_from_tags(client.version_tags["1"])
    assert registered_contract["supervisor_id"] == supervisor_id
    assert registered_contract["supervisor_endpoint_id"] == supervisor_endpoint_id
    assert registered_contract["runtime_application_id"] == runtime_application_id


def test_supervisor_id_rotation_allocates_distinct_green_without_mutating_blue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_identity_rotation_allocates_distinct_green(
        monkeypatch,
        supervisor_id="rotated-supervisor-id",
        supervisor_endpoint_id=_SUPERVISOR_ENDPOINT_ID,
        runtime_application_id=_RUNTIME_APPLICATION_ID,
    )


def test_runtime_owner_rotation_allocates_distinct_green_without_mutating_blue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_identity_rotation_allocates_distinct_green(
        monkeypatch,
        supervisor_id=_SUPERVISOR_ID,
        supervisor_endpoint_id=_SUPERVISOR_ENDPOINT_ID,
        runtime_application_id="rotated-runtime-client",
    )


def test_supervisor_endpoint_id_rotation_allocates_distinct_green_without_mutating_blue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_identity_rotation_allocates_distinct_green(
        monkeypatch,
        supervisor_id=_SUPERVISOR_ID,
        supervisor_endpoint_id="rotated-supervisor-endpoint-id",
        runtime_application_id=_RUNTIME_APPLICATION_ID,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("supervisor_id", "mismatched-supervisor-id"),
        ("supervisor_endpoint_id", "mismatched-supervisor-endpoint-id"),
        ("runtime_application_id", "mismatched-runtime-client"),
    ),
)
def test_gateway_postflight_rejects_allocation_identity_mismatch(
    field: str,
    value: str,
) -> None:
    deployment = _exact_deployment(source_hash="a" * 64)

    with pytest.raises(RuntimeError, match="resource allocation contract drifted"):
        verify_gateway_responses_agent(
            SimpleNamespace(),
            replace(deployment, **{field: value}),
        )


def test_ensure_gateway_agent_reuses_exact_live_endpoint_without_mutation(monkeypatch) -> None:
    source_hash = gateway_agent_source_hash(
        upstream_endpoint="managed-supervisor",
        catalog=_CATALOG,
        genie_space_id=_GENIE_SPACE_ID,
    )
    client = _Client(
        [
            SimpleNamespace(
                version="5",
                source="models:/m-reviewed",
                tags={
                    gateway.SOURCE_HASH_TAG: source_hash,
                    gateway.UPSTREAM_TAG: "managed-supervisor",
                },
            )
        ]
    )
    _patch_mlflow(monkeypatch, client=client)
    serving = _ServingEndpoints(_exact_endpoint_details(source_hash=source_hash))

    deployment = ensure_gateway_responses_agent(
        SimpleNamespace(
            serving_endpoints=serving,
            registered_models=SimpleNamespace(
                get=lambda _name: SimpleNamespace(owner=_RUNTIME_APPLICATION_ID)
            ),
        ),
        endpoint="mip-growth-agent-gateway",
        endpoint_prefix="mip-growth-agent-gateway",
        supervisor_id=_SUPERVISOR_ID,
        upstream_endpoint="managed-supervisor",
        model_name="mip.audit.mortgage_growth_supervisor_proxy",
        experiment_name="mip-agent-runtime-gateway-proxy",
        inference_catalog="mip",
        inference_schema="audit",
        inference_table_prefix="mip_agent_gateway_growth_agent",
        genie_space_id=_GENIE_SPACE_ID,
        expected_creator_application_id=_RUNTIME_APPLICATION_ID,
    )

    assert deployment.endpoint == "mip-growth-agent-gateway"
    assert serving.created == []
    assert serving.events == []
    assert serving.updated == []
    assert serving.gateway_updates == []
    assert serving.patches == []


def test_completed_redeploy_reuses_gateway_with_exact_app_and_verifier_groups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_hash = gateway_agent_source_hash(
        upstream_endpoint="managed-supervisor",
        catalog=_CATALOG,
        genie_space_id=_GENIE_SPACE_ID,
    )
    client = _Client(
        [
            SimpleNamespace(
                version="5",
                source="models:/m-reviewed",
                tags={
                    gateway.SOURCE_HASH_TAG: source_hash,
                    gateway.UPSTREAM_TAG: "managed-supervisor",
                },
            )
        ]
    )
    _patch_mlflow(monkeypatch, client=client)
    endpoint = _exact_endpoint_details(source_hash=source_hash)
    applications = ("app-client", "verifier-client")
    permissions = ServingEndpointPermissions(
        access_control_list=[
            ServingEndpointAccessControlResponse(
                service_principal_name=_RUNTIME_APPLICATION_ID,
                all_permissions=[
                    ServingEndpointPermission(
                        inherited=False,
                        permission_level=ServingEndpointPermissionLevel.CAN_MANAGE,
                    )
                ],
            ),
            *[
                ServingEndpointAccessControlResponse(
                    group_name=managed_query_group_name(
                        endpoint_id=endpoint.id,
                        application_id=application_id,
                    ),
                    all_permissions=[
                        ServingEndpointPermission(
                            inherited=False,
                            permission_level=ServingEndpointPermissionLevel.CAN_QUERY,
                        )
                    ],
                )
                for application_id in applications
            ],
        ]
    )
    serving = _ServingEndpoints(
        endpoint,
        permissions_by_endpoint_id={endpoint.id: permissions},
    )
    groups = {
        application_id: SimpleNamespace(
            id=f"group-{application_id}",
            display_name=managed_query_group_name(
                endpoint_id=endpoint.id,
                application_id=application_id,
            ),
            external_id=intent_external_id(
                endpoint_id=endpoint.id,
                application_id=application_id,
                creation_nonce=f"22222222-2222-4222-8222-22222222222{index}",
            ),
            members=[SimpleNamespace(value=f"{application_id}-scim")],
            meta=SimpleNamespace(resource_type="WorkspaceGroup"),
        )
        for index, application_id in enumerate(applications)
    }
    principals = {
        application_id: SimpleNamespace(
            id=f"{application_id}-scim",
            application_id=application_id,
        )
        for application_id in applications
    }
    workspace = _runtime_workspace(serving)
    workspace.groups = SimpleNamespace(
        list=lambda **kwargs: [
            group
            for group in groups.values()
            if group.display_name
            == kwargs["filter"].removeprefix("displayName eq '").removesuffix("'")
        ],
        get=lambda group_id: next(group for group in groups.values() if group.id == group_id),
    )
    workspace.service_principals = SimpleNamespace(
        list=lambda **_kwargs: list(principals.values()),
    )
    claims = {
        (
            endpoint.id,
            application_id,
            f"{application_id}-scim",
        ): {
            "group_id": groups[application_id].id,
            "external_id": groups[application_id].external_id,
        }
        for application_id in applications
    }

    def require_claimed(
        _workspace: object,
        *,
        app_name: str,
        endpoint_id: str,
        application_id: str,
        service_principal_id: str,
        group_name: str,
    ) -> dict[str, str]:
        assert app_name == "mip-app"
        assert group_name == managed_query_group_name(
            endpoint_id=endpoint_id,
            application_id=application_id,
        )
        return claims[(endpoint_id, application_id, service_principal_id)]

    monkeypatch.setattr(
        query_group_access.group_provenance,
        "require_claimed",
        require_claimed,
    )

    deployment = _ensure_gateway(
        workspace,
        approved_query_application_ids=applications,
    )

    assert deployment.endpoint == "mip-growth-agent-gateway"
    assert serving.created == []


def test_exact_head_direct_query_gateway_rotates_without_mutating_restorable_blue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_hash = gateway_agent_source_hash(
        upstream_endpoint="managed-supervisor",
        catalog=_CATALOG,
        genie_space_id=_GENIE_SPACE_ID,
    )
    client = _Client(
        [
            SimpleNamespace(
                version="5",
                source="models:/m-reviewed",
                tags={
                    gateway.SOURCE_HASH_TAG: source_hash,
                    gateway.UPSTREAM_TAG: "managed-supervisor",
                },
            )
        ]
    )
    _patch_mlflow(monkeypatch, client=client)
    blue = _exact_endpoint_details(source_hash=source_hash)
    direct_acl = ServingEndpointPermissions(
        access_control_list=[
            ServingEndpointAccessControlResponse(
                service_principal_name=_RUNTIME_APPLICATION_ID,
                all_permissions=[
                    ServingEndpointPermission(
                        inherited=False,
                        permission_level=ServingEndpointPermissionLevel.CAN_MANAGE,
                    )
                ],
            ),
            ServingEndpointAccessControlResponse(
                service_principal_name="app-sp",
                all_permissions=[
                    ServingEndpointPermission(
                        inherited=False,
                        permission_level=ServingEndpointPermissionLevel.CAN_QUERY,
                    )
                ],
            )
        ]
    )
    serving = _ServingEndpoints(
        blue,
        permissions_by_endpoint_id={blue.id: direct_acl},
    )

    deployment = _ensure_gateway(_runtime_workspace(serving))

    assert deployment.endpoint == (
        f"mip-growth-agent-gateway-{deployment.resource_hash[:12]}-mq1"
    )
    assert serving.created[0]["name"] == deployment.endpoint
    assert serving.get("mip-growth-agent-gateway") is blue
    assert serving.get_permissions(blue.id) is direct_acl
    assert {
        entry.service_principal_name for entry in direct_acl.access_control_list or []
    } == {_RUNTIME_APPLICATION_ID, "app-sp"}
    assert serving.updated == []
    assert serving.gateway_updates == []
    assert serving.patches == []
    assert serving.events == []


def test_exact_gateway_with_only_runtime_manager_remains_reusable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_hash = gateway_agent_source_hash(
        upstream_endpoint="managed-supervisor",
        catalog=_CATALOG,
        genie_space_id=_GENIE_SPACE_ID,
    )
    client = _Client(
        [
            SimpleNamespace(
                version="5",
                source="models:/m-reviewed",
                tags={
                    gateway.SOURCE_HASH_TAG: source_hash,
                    gateway.UPSTREAM_TAG: "managed-supervisor",
                },
            )
        ]
    )
    _patch_mlflow(monkeypatch, client=client)
    endpoint = _exact_endpoint_details(source_hash=source_hash)
    permissions = ServingEndpointPermissions(
        access_control_list=[
            ServingEndpointAccessControlResponse(
                service_principal_name=_RUNTIME_APPLICATION_ID,
                all_permissions=[
                    ServingEndpointPermission(
                        inherited=False,
                        permission_level=ServingEndpointPermissionLevel.CAN_MANAGE,
                    )
                ],
            )
        ]
    )
    serving = _ServingEndpoints(
        endpoint,
        permissions_by_endpoint_id={endpoint.id: permissions},
    )

    deployment = _ensure_gateway(_runtime_workspace(serving))

    assert deployment.endpoint == "mip-growth-agent-gateway"
    assert serving.created == []
    assert serving.get_permissions(endpoint.id) is permissions


@pytest.mark.parametrize(
    ("scope", "field", "value"),
    (
        ("core", "auto_capture_config", SimpleNamespace(enabled=True)),
        ("entity", "burst_scaling_enabled", True),
        ("entity", "max_provisioned_concurrency", 8),
        ("entity", "max_provisioned_throughput", 100),
        ("entity", "min_provisioned_concurrency", 1),
        ("entity", "min_provisioned_throughput", 10),
        ("entity", "provisioned_model_units", 2),
        ("entity", "workload_type", "GPU_SMALL"),
        ("gateway", "fallback_config", SimpleNamespace(enabled=True)),
        ("gateway", "guardrails", SimpleNamespace(input=SimpleNamespace())),
        ("gateway", "rate_limits", [SimpleNamespace(calls=1)]),
        ("gateway", "usage_tracking_config", SimpleNamespace(enabled=True)),
        (
            "gateway",
            "usage_tracking_config",
            SimpleNamespace(enabled=False, destination="unreviewed"),
        ),
    ),
)
def test_gateway_exact_contract_rejects_every_unreviewed_config_field(
    scope: str,
    field: str,
    value: object,
) -> None:
    source_hash = gateway_agent_source_hash(
        upstream_endpoint="managed-supervisor",
        catalog=_CATALOG,
        genie_space_id=_GENIE_SPACE_ID,
    )
    details = _exact_endpoint_details(source_hash=source_hash)
    entity, _traffic = _served_entity(
        supervisor_id=_SUPERVISOR_ID,
        upstream_endpoint="managed-supervisor",
        runtime_application_id=_RUNTIME_APPLICATION_ID,
        catalog=_CATALOG,
        genie_space_id=_GENIE_SPACE_ID,
        model_name=details.config.served_entities[0].entity_name,
        model_version=5,
        experiment_id="experiment-7",
    )
    target = {
        "core": details.config,
        "entity": details.config.served_entities[0],
        "gateway": details.ai_gateway,
    }[scope]
    setattr(target, field, value)

    if scope == "gateway":
        assert not gateway._gateway_matches(
            details,
            catalog="mip",
            schema="audit",
            table_prefix=details.ai_gateway.inference_table_config.table_name_prefix,
        )
    else:
        assert not gateway._proxy_config_matches(details, entity=entity)


def test_gateway_exact_contract_rejects_different_legacy_route_alias() -> None:
    source_hash = gateway_agent_source_hash(
        upstream_endpoint="managed-supervisor",
        catalog=_CATALOG,
        genie_space_id=_GENIE_SPACE_ID,
    )
    details = _exact_endpoint_details(source_hash=source_hash)
    entity, _traffic = _served_entity(
        supervisor_id=_SUPERVISOR_ID,
        upstream_endpoint="managed-supervisor",
        runtime_application_id=_RUNTIME_APPLICATION_ID,
        catalog=_CATALOG,
        genie_space_id=_GENIE_SPACE_ID,
        model_name=details.config.served_entities[0].entity_name,
        model_version=5,
        experiment_id="experiment-7",
    )
    details.config.traffic_config.routes[0].served_model_name = "different-model"

    assert not gateway._proxy_config_matches(details, entity=entity)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("burst_scaling_enabled", True),
        ("instance_profile_arn", "arn:aws:iam::123456789012:instance-profile/rogue"),
        ("max_provisioned_concurrency", 8),
        ("min_provisioned_concurrency", 1),
        ("model_name", "different-model"),
        ("provisioned_model_units", 2),
        ("future_mutable_field", "unreviewed"),
    ),
)
def test_gateway_exact_contract_rejects_drifted_legacy_served_model_alias(
    field: str,
    value: object,
) -> None:
    source_hash = gateway_agent_source_hash(
        upstream_endpoint="managed-supervisor",
        catalog=_CATALOG,
        genie_space_id=_GENIE_SPACE_ID,
    )
    details = _exact_endpoint_details(source_hash=source_hash)
    entity, _traffic = _served_entity(
        supervisor_id=_SUPERVISOR_ID,
        upstream_endpoint="managed-supervisor",
        runtime_application_id=_RUNTIME_APPLICATION_ID,
        catalog=_CATALOG,
        genie_space_id=_GENIE_SPACE_ID,
        model_name=details.config.served_entities[0].entity_name,
        model_version=5,
        experiment_id="experiment-7",
    )
    setattr(details.config.served_models[0], field, value)

    assert not gateway._proxy_config_matches(details, entity=entity)


def test_gateway_exact_contract_rejects_multiple_legacy_served_models() -> None:
    source_hash = gateway_agent_source_hash(
        upstream_endpoint="managed-supervisor",
        catalog=_CATALOG,
        genie_space_id=_GENIE_SPACE_ID,
    )
    details = _exact_endpoint_details(source_hash=source_hash)
    entity, _traffic = _served_entity(
        supervisor_id=_SUPERVISOR_ID,
        upstream_endpoint="managed-supervisor",
        runtime_application_id=_RUNTIME_APPLICATION_ID,
        catalog=_CATALOG,
        genie_space_id=_GENIE_SPACE_ID,
        model_name=details.config.served_entities[0].entity_name,
        model_version=5,
        experiment_id="experiment-7",
    )
    details.config.served_models.append(details.config.served_models[0])

    assert not gateway._proxy_config_matches(details, entity=entity)


@pytest.mark.parametrize("enabled", (0, 0.0, "false", 1))
def test_gateway_exact_contract_rejects_false_like_usage_tracking_values(
    enabled: object,
) -> None:
    source_hash = gateway_agent_source_hash(
        upstream_endpoint="managed-supervisor",
        catalog=_CATALOG,
        genie_space_id=_GENIE_SPACE_ID,
    )
    details = _exact_endpoint_details(source_hash=source_hash)
    details.ai_gateway.usage_tracking_config.enabled = enabled

    assert not gateway._gateway_matches(
        details,
        catalog="mip",
        schema="audit",
        table_prefix=details.ai_gateway.inference_table_config.table_name_prefix,
    )


def test_candidate_resource_binding_does_not_inherit_previous_model_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "MIP_GATEWAY_MODEL_ATTESTATION_PREVIOUS_VERIFY_KEY",
        _PREVIOUS_MODEL_VERIFY_KEY,
    )

    entity, _traffic = _served_entity(
        supervisor_id=_SUPERVISOR_ID,
        upstream_endpoint="managed-supervisor",
        runtime_application_id=_RUNTIME_APPLICATION_ID,
        catalog=_CATALOG,
        genie_space_id=_GENIE_SPACE_ID,
        model_name="mip.audit.mortgage_growth_supervisor_proxy_current",
        model_version=1,
        experiment_id="experiment-current",
        resource_binding={
            "MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY": _MODEL_VERIFY_KEY,
        },
    )

    assert entity.environment_vars["MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY"] == _MODEL_VERIFY_KEY
    assert "MIP_GATEWAY_MODEL_ATTESTATION_PREVIOUS_VERIFY_KEY" not in entity.environment_vars


def test_gateway_resource_hash_binds_cost_and_compute_contract(monkeypatch) -> None:
    baseline = _resource_hash("a" * 64)

    monkeypatch.setattr(gateway, "_BURST_SCALING_ENABLED", True)
    assert _resource_hash("a" * 64) != baseline
    monkeypatch.setattr(gateway, "_BURST_SCALING_ENABLED", False)
    monkeypatch.setattr(gateway, "_WORKLOAD_TYPE", SimpleNamespace(value="GPU_SMALL"))
    assert _resource_hash("a" * 64) != baseline
    monkeypatch.setattr(gateway, "_WORKLOAD_TYPE", SimpleNamespace(value="CPU"))
    monkeypatch.setattr(gateway, "_ENDPOINT_DESCRIPTION", "reviewed replacement description")
    assert _resource_hash("a" * 64) != baseline


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("route_optimized", True),
        ("budget_policy_id", "unreviewed-budget"),
        ("email_notifications", SimpleNamespace(on_update_failure=["ops@example.com"])),
    ),
)
def test_gateway_exact_contract_rejects_unreviewed_endpoint_policy(
    field: str,
    value: object,
) -> None:
    details = _exact_endpoint_details(source_hash="a" * 64)
    setattr(details, field, value)

    assert not gateway._endpoint_policy_matches(details)


def test_ensure_gateway_agent_rejects_drifted_immutable_green_candidate(monkeypatch) -> None:
    source_hash = gateway_agent_source_hash(
        upstream_endpoint="managed-supervisor",
        catalog=_CATALOG,
        genie_space_id=_GENIE_SPACE_ID,
    )
    client = _Client(
        [
            SimpleNamespace(
                version="5",
                source="models:/m-reviewed",
                tags={
                    gateway.SOURCE_HASH_TAG: source_hash,
                    gateway.UPSTREAM_TAG: "managed-supervisor",
                },
            )
        ]
    )
    _patch_mlflow(monkeypatch, client=client)
    candidate = f"mip-growth-agent-gateway-{_resource_hash(source_hash)[:12]}-mq1"
    drifted = SimpleNamespace(
        creator=_RUNTIME_APPLICATION_ID,
        pending_config=None,
        config=SimpleNamespace(served_entities=[]),
        tags=[],
        ai_gateway=None,
    )
    serving = _ServingEndpoints(
        {
            "mip-growth-agent-gateway": drifted,
            candidate: drifted,
        }
    )

    with pytest.raises(RuntimeError, match="immutable green Gateway candidate drifted"):
        ensure_gateway_responses_agent(
            SimpleNamespace(
                serving_endpoints=serving,
                registered_models=SimpleNamespace(
                    get=lambda _name: SimpleNamespace(owner=_RUNTIME_APPLICATION_ID)
                ),
            ),
            endpoint="mip-growth-agent-gateway",
            endpoint_prefix="mip-growth-agent-gateway",
            supervisor_id=_SUPERVISOR_ID,
            upstream_endpoint="managed-supervisor",
            model_name="mip.audit.mortgage_growth_supervisor_proxy",
            experiment_name="mip-agent-runtime-gateway-proxy",
            inference_catalog="mip",
            inference_schema="audit",
            inference_table_prefix="mip_agent_gateway_growth_agent",
            genie_space_id=_GENIE_SPACE_ID,
            expected_creator_application_id=_RUNTIME_APPLICATION_ID,
        )

    assert serving.created == []
    assert serving.events == []


def test_gateway_agent_postflight_verifies_signed_v2_contract_and_exact_experiment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_hash = "a" * 64
    signing_key = base64.urlsafe_b64encode(b"v" * 32).decode("ascii").rstrip("=")
    monkeypatch.setenv("MIP_ALLOW_RUNTIME_MODEL_ATTESTATION_SIGNING", "1")
    monkeypatch.setenv("MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY", signing_key)
    monkeypatch.setenv(
        "MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY",
        derive_gateway_proof_verify_key(signing_key),
    )
    deployment = _exact_deployment(source_hash=source_hash, model_version=7)
    details = _exact_endpoint_details(source_hash=source_hash, model_version=7)
    details.state = SimpleNamespace(ready="READY")
    details.task = "agent/v1/responses"
    contract = {
        "full_name": deployment.model_name,
        "model_source": deployment.model_source,
        "source_hash": deployment.source_hash,
        "supervisor_id": deployment.supervisor_id,
        "supervisor_endpoint_id": deployment.supervisor_endpoint_id,
        "upstream_endpoint": deployment.upstream_endpoint,
        "runtime_application_id": deployment.runtime_application_id,
        "model_family": deployment.model_family,
        "experiment_base": deployment.experiment_base,
        "catalog": deployment.catalog,
        "genie_space_id": deployment.genie_space_id,
        "inference_schema": "audit",
        "inference_table_prefix": deployment.inference_table_prefix,
    }
    model_tags = attestation.sign_gateway_model_contract(**contract)
    model_registry = SimpleNamespace(
        get_model_version=lambda name, version: SimpleNamespace(
            name=name,
            version=version,
            source=deployment.model_source,
            tags=model_tags,
            status="READY",
        )
    )

    serving = _ServingEndpoints(details)
    verify_gateway_responses_agent(
        SimpleNamespace(
            api_client=_experiment_permissions_api(),
            workspace=SimpleNamespace(
                get_status=lambda path: SimpleNamespace(
                    path=path,
                    object_type="DIRECTORY",
                    object_id="runtime-home-id",
                )
            ),
            serving_endpoints=serving,
            registered_models=SimpleNamespace(
                get=lambda _name: SimpleNamespace(owner=_RUNTIME_APPLICATION_ID)
            ),
        ),
        deployment,
        model_registry=model_registry,
        tracking_client=_tracking_client(deployment),
        assert_single_writer=_assert_single_writer,
    )
    assert serving.rate_limit_puts == [{"name": "mip-growth-agent-gateway", "rate_limits": []}]


def test_gateway_agent_postflight_rejects_endpoint_description_drift() -> None:
    deployment = _exact_deployment(source_hash="a" * 64, model_version=7)
    details = _exact_endpoint_details(source_hash="a" * 64, model_version=7)
    details.state = SimpleNamespace(ready="READY")
    details.task = "agent/v1/responses"
    details.description = "unreviewed description"

    with pytest.raises(RuntimeError, match="description"):
        verify_gateway_responses_agent(
            SimpleNamespace(serving_endpoints=_ServingEndpoints(details)),
            deployment,
        )


@pytest.mark.parametrize(
    "response",
    [
        SimpleNamespace(rate_limits=[SimpleNamespace(calls=1)]),
        SimpleNamespace(rate_limits=None),
        SimpleNamespace(),
    ],
)
def test_gateway_agent_postflight_rejects_inconclusive_deprecated_rate_limits(
    monkeypatch: pytest.MonkeyPatch,
    response: object,
) -> None:
    serving = _ServingEndpoints()
    monkeypatch.setattr(
        serving,
        "put",
        lambda _name, *, rate_limits: response,
    )

    with pytest.raises(RuntimeError, match="reconciliation was inconclusive"):
        gateway._clear_deprecated_endpoint_rate_limits(
            SimpleNamespace(serving_endpoints=serving),
            endpoint="mip-growth-agent-gateway",
        )


def test_gateway_agent_postflight_accepts_typed_custom_model_rate_limit_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    serving = _ServingEndpoints()

    def reject_custom_model(_name: str, *, rate_limits: list[object]) -> object:
        assert rate_limits == []
        raise BadRequest(endpoint_contract._CUSTOM_MODEL_RATE_LIMITS_UNSUPPORTED)

    monkeypatch.setattr(serving, "put", reject_custom_model)

    gateway._clear_deprecated_endpoint_rate_limits(
        SimpleNamespace(serving_endpoints=serving),
        endpoint="mip-growth-agent-gateway",
    )


@pytest.mark.parametrize(
    "error",
    [
        BadRequest("Rate limits are not authorized."),
        PermissionError(endpoint_contract._CUSTOM_MODEL_RATE_LIMITS_UNSUPPORTED),
    ],
)
def test_gateway_agent_postflight_rejects_other_rate_limit_failures(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    serving = _ServingEndpoints()

    def fail(_name: str, *, rate_limits: list[object]) -> object:
        assert rate_limits == []
        raise error

    monkeypatch.setattr(serving, "put", fail)

    with pytest.raises(RuntimeError, match="reconciliation failed"):
        gateway._clear_deprecated_endpoint_rate_limits(
            SimpleNamespace(serving_endpoints=serving),
            endpoint="mip-growth-agent-gateway",
        )


def test_gateway_agent_postflight_rejects_rogue_served_model_version_tags() -> None:
    source_hash = "a" * 64
    details = _exact_endpoint_details(source_hash=source_hash, model_version=7)
    details.state = SimpleNamespace(ready="READY")
    details.task = "agent/v1/responses"
    deployment = _exact_deployment(source_hash=source_hash, model_version=7)
    rogue_tags = {key: "x" for key in gateway_contract.GATEWAY_MODEL_CANONICAL_TAGS}
    rogue_tags[gateway.MODEL_SOURCE_HASH_TAG] = "b" * 64
    rogue_tags[gateway.MODEL_UPSTREAM_TAG] = "rogue-supervisor"
    rogue_registry = SimpleNamespace(
        get_model_version=lambda name, version: SimpleNamespace(
            name=name,
            version=version,
            source=deployment.model_source,
            tags=rogue_tags,
            status="READY",
        )
    )

    try:
        verify_gateway_responses_agent(
            SimpleNamespace(
                serving_endpoints=_ServingEndpoints(details),
                registered_models=SimpleNamespace(
                    get=lambda _name: SimpleNamespace(owner=_RUNTIME_APPLICATION_ID)
                ),
            ),
            deployment,
            model_registry=rogue_registry,
            tracking_client=_tracking_client(deployment),
        )
    except RuntimeError as exc:
        assert "Model version tags do not bind" in str(exc)
    else:  # pragma: no cover - model-version proof is load-bearing
        raise AssertionError("rogue served model version tags must fail the postflight")


def test_gateway_agent_postflight_rejects_non_responses_task() -> None:
    deployment = _exact_deployment(source_hash="a" * 64, model_version=7)
    details = SimpleNamespace(
        creator=_RUNTIME_APPLICATION_ID,
        state=SimpleNamespace(ready="READY"),
        task="llm/v1/chat",
    )

    try:
        verify_gateway_responses_agent(
            SimpleNamespace(serving_endpoints=_ServingEndpoints(details)),
            deployment,
        )
    except RuntimeError as exc:
        assert "not agent/v1/responses" in str(exc)
    else:  # pragma: no cover - fail-closed task proof is load-bearing
        raise AssertionError("non-Responses task must fail provisioning postflight")
