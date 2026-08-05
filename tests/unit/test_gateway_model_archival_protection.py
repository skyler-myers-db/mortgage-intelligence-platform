"""Fail-closed tests for Gateway archival protection and access proofs."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from tools.databricks import gateway_model_archival_protection as protection
from tools.databricks.gateway_model_retirement_record import record_sha256

_MODEL = "mip.audit.mortgage_growth_supervisor_proxy_aaaaaaaaaaaa"
_TABLE = "mip.audit.mip_agent_gateway_growth_agent_aaaaaaaaaaaa_payload"
_IDENTITIES = {
    "runtime": "runtime-id",
    "app": "app-id",
    "proxy": "proxy-id",
    "verifier": "verifier-id",
}


class _ServicePrincipals:
    def __init__(self) -> None:
        self.groups: dict[str, list[object]] = {
            application_id: [
                SimpleNamespace(
                    display=f"{role}-group",
                    value=f"{role}-group-id",
                )
            ]
            for role, application_id in _IDENTITIES.items()
        }

    def list(self, *, filter: str, **_kwargs: Any) -> list[object]:
        application_id = filter.split('"')[1]
        if application_id not in self.groups:
            return []
        return [
            SimpleNamespace(
                id=f"scim-{application_id}",
                application_id=application_id,
            )
        ]

    def get(self, principal_id: str) -> object:
        application_id = principal_id.removeprefix("scim-")
        return SimpleNamespace(
            id=principal_id,
            application_id=application_id,
            groups=self.groups[application_id],
        )


class _ApiClient:
    def __init__(self, policy_type: str = "ROW_FILTER") -> None:
        self.policy_type = policy_type
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def do(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append((method, path, dict(query)))
        securable_type, full_name = path.rsplit("/", 2)[-2:]
        return {
            "policies": [
                {
                    "name": f"{securable_type}-{full_name}",
                    "policy_type": self.policy_type,
                    "for_securable_type": "MODEL",
                }
            ]
        }


def _access_workspace(*, policy_type: str = "ROW_FILTER") -> Any:
    return SimpleNamespace(
        service_principals=_ServicePrincipals(),
        api_client=_ApiClient(policy_type),
    )


def _zero_access(
    workspace: Any,
    *,
    experiment_acl: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], ...]:
    return protection.zero_effective_access_evidence(
        workspace,
        experiment_acl=experiment_acl or [],
        model_name=_MODEL,
        table_names=(_TABLE,),
        runtime_application_id=_IDENTITIES["runtime"],
        app_application_id=_IDENTITIES["app"],
        proxy_application_id=_IDENTITIES["proxy"],
        verifier_application_id=_IDENTITIES["verifier"],
    )


def test_zero_effective_access_binds_all_four_identities_and_abac_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _access_workspace()
    monkeypatch.setattr(
        protection,
        "_effective_privilege_sources",
        lambda *_args, **_kwargs: {},
    )

    evidence = _zero_access(workspace)

    assert [item["role"] for item in evidence] == [
        "runtime",
        "app",
        "proxy",
        "verifier",
    ]
    assert [item["application_id"] for item in evidence] == list(
        _IDENTITIES.values()
    )
    assert len({item["abac_policies_sha256"] for item in evidence}) == 1
    assert all(item["experiment_permissions"] == [] for item in evidence)
    assert all(
        resource["privileges"] == {}
        for item in evidence
        for resource in item["resources"]
    )
    assert workspace.api_client.calls == [
        (
            "GET",
            "/api/2.1/unity-catalog/policies/CATALOG/mip",
            {"include_inherited": True, "max_results": 1000},
        ),
        (
            "GET",
            "/api/2.1/unity-catalog/policies/SCHEMA/mip.audit",
            {"include_inherited": True, "max_results": 1000},
        ),
        (
            "GET",
            f"/api/2.1/unity-catalog/policies/TABLE/{_TABLE}",
            {"include_inherited": True, "max_results": 1000},
        ),
    ]


def test_zero_effective_access_rejects_abac_grant_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        protection,
        "_effective_privilege_sources",
        lambda *_args, **_kwargs: {},
    )

    with pytest.raises(RuntimeError, match="applicable ABAC model GRANT"):
        _zero_access(_access_workspace(policy_type="POLICY_TYPE_GRANT"))


def test_zero_effective_access_rejects_direct_uc_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def privileges(
        _workspace: Any,
        *,
        principal: str,
        **_kwargs: Any,
    ) -> dict[str, set[tuple[str, str, str]]]:
        return (
            {"EXECUTE": {(principal, "", "")}}
            if principal == _IDENTITIES["proxy"]
            else {}
        )

    monkeypatch.setattr(protection, "_effective_privilege_sources", privileges)

    with pytest.raises(RuntimeError, match="accessible to proxy identity"):
        _zero_access(_access_workspace())


def test_zero_effective_access_rejects_group_derived_experiment_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        protection,
        "_effective_privilege_sources",
        lambda *_args, **_kwargs: {},
    )
    acl = [
        {
            "group_name": "runtime-group",
            "all_permissions": [{"permission_level": "CAN_READ"}],
        }
    ]

    with pytest.raises(RuntimeError, match="accessible to runtime identity"):
        _zero_access(_access_workspace(), experiment_acl=acl)


def test_zero_effective_access_requires_distinct_identity_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        protection,
        "_effective_privilege_sources",
        lambda *_args, **_kwargs: {},
    )

    with pytest.raises(ValueError, match="complete and distinct"):
        protection.zero_effective_access_evidence(
            _access_workspace(),
            experiment_acl=[],
            model_name=_MODEL,
            table_names=(_TABLE,),
            runtime_application_id="same",
            app_application_id="same",
            proxy_application_id="proxy",
            verifier_application_id="verifier",
        )


def _endpoint_workspace(entity: object | None = None) -> Any:
    config = SimpleNamespace(
        served_entities=[] if entity is None else [entity],
        served_models=[],
    )
    details = SimpleNamespace(config=config, pending_config=None)
    return SimpleNamespace(
        serving_endpoints=SimpleNamespace(
            list=lambda: [SimpleNamespace(name="endpoint")],
            get=lambda _name: details,
        )
    )


def _discovery_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        protection,
        "_registration_recovery_contracts",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        protection,
        "_load_record",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("no server-owned last-good App rollback contract exists")
        ),
    )
    monkeypatch.setattr(
        protection,
        "read_signed_cutover_journal",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        protection,
        "json_pin_from_env",
        lambda _name: None,
    )


def _discover(workspace: Any) -> tuple[dict[str, Any], ...]:
    return protection.discover_protected_allocation_contracts(
        workspace,
        object(),
        object(),
        app_name="mip-app",
        runtime_application_id=_IDENTITIES["runtime"],
        rollback_scope="mip-pr105",
        expected_lakebase_instance="lakebase",
        model_family="mip.audit.mortgage_growth_supervisor_proxy",
        experiment_base="mip-agent-runtime-gateway-proxy",
        catalog="mip",
        inference_schema="audit",
        inference_table_prefix="mip_agent_gateway_growth_agent",
    )


def test_protected_inventory_authenticates_endpoint_runtime_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _discovery_defaults(monkeypatch)
    entity = SimpleNamespace(
        name="mip-growth-supervisor-proxy-1",
        entity_name=_MODEL,
        entity_version="1",
        environment_vars={
            "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_CONTRACT_JSON": "signed-value",
            "UNRELATED": "x",
        },
    )
    contract = {
        "gateway_model_name": _MODEL,
        "gateway_model_version": "1",
        "allocation": "active",
    }
    monkeypatch.setattr(
        protection,
        "verified_gateway_runtime_resource_environment",
        lambda binding: (
            contract
            if binding
            == {"MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_CONTRACT_JSON": "signed-value"}
            else {}
        ),
    )

    protected = _discover(_endpoint_workspace(entity))

    assert protected == (
        {
            "kind": "endpoint-current-endpoint-served_entities-0",
            "gateway_model_name": _MODEL,
            "contract": contract,
            "contract_sha256": record_sha256(contract),
        },
    )


def test_protected_inventory_ignores_unbound_foundation_model_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _discovery_defaults(monkeypatch)
    foundation_entity = SimpleNamespace(
        name="databricks-foundation-model",
        entity_name=None,
        model_name=None,
        foundation_model={"name": "system.ai.foundation-model"},
        environment_vars=None,
    )

    assert _discover(_endpoint_workspace(foundation_entity)) == ()


def test_protected_inventory_rejects_bound_entity_without_uc_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _discovery_defaults(monkeypatch)
    malformed_bound_entity = SimpleNamespace(
        name="governed-gateway",
        entity_name=None,
        model_name=None,
        foundation_model={"name": "system.ai.foundation-model"},
        environment_vars={
            "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_CONTRACT_JSON": "signed-value",
        },
    )

    with pytest.raises(RuntimeError, match="non-UC entity carries a runtime contract"):
        _discover(_endpoint_workspace(malformed_bound_entity))


def test_protected_inventory_rejects_family_model_with_stripped_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _discovery_defaults(monkeypatch)
    entity = SimpleNamespace(
        name="mip-growth-supervisor-proxy-1",
        entity_name=_MODEL,
        entity_version="1",
        environment_vars=None,
    )

    with pytest.raises(RuntimeError, match="lacks its runtime contract"):
        _discover(_endpoint_workspace(entity))


def test_protected_inventory_ignores_unbound_unrelated_uc_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _discovery_defaults(monkeypatch)
    entity = SimpleNamespace(
        name="unrelated",
        entity_name="customer.catalog.unrelated_model",
        entity_version="1",
        environment_vars=None,
    )

    assert _discover(_endpoint_workspace(entity)) == ()


@pytest.mark.parametrize("alias", [None, "", "unrelated-alias"])
def test_protected_inventory_rejects_noncanonical_bound_alias(
    monkeypatch: pytest.MonkeyPatch,
    alias: str | None,
) -> None:
    _discovery_defaults(monkeypatch)
    entity = SimpleNamespace(
        name=alias,
        entity_name=_MODEL,
        entity_version="1",
        environment_vars={
            "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_CONTRACT_JSON": "signed-value",
        },
    )
    monkeypatch.setattr(
        protection,
        "verified_gateway_runtime_resource_environment",
        lambda _binding: {
            "gateway_model_name": _MODEL,
            "gateway_model_version": "1",
        },
    )

    with pytest.raises(RuntimeError, match="alias is not canonical"):
        _discover(_endpoint_workspace(entity))


def test_protected_inventory_rejects_signed_model_version_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _discovery_defaults(monkeypatch)
    entity = SimpleNamespace(
        name="mip-growth-supervisor-proxy-1",
        entity_name=_MODEL,
        entity_version="999",
        environment_vars={
            "MIP_EXPECTED_AGENT_GATEWAY_RESOURCE_CONTRACT_JSON": "signed-value",
        },
    )
    monkeypatch.setattr(
        protection,
        "verified_gateway_runtime_resource_environment",
        lambda _binding: {
            "gateway_model_name": _MODEL,
            "gateway_model_version": "1",
        },
    )

    with pytest.raises(RuntimeError, match="contract has no model"):
        _discover(_endpoint_workspace(entity))


def test_protected_inventory_rejects_model_less_entity_without_provider_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _discovery_defaults(monkeypatch)
    entity = SimpleNamespace(
        name="unknown",
        entity_name=None,
        model_name=None,
        environment_vars=None,
    )

    with pytest.raises(RuntimeError, match="no recognized provider identity"):
        _discover(_endpoint_workspace(entity))


def test_protected_inventory_allows_supervisor_only_signed_cutover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _discovery_defaults(monkeypatch)
    monkeypatch.setattr(
        protection,
        "read_signed_cutover_journal",
        lambda *_args, **_kwargs: {
            "old_id": "retiring-supervisor",
            "old_endpoint": "retiring-supervisor-endpoint",
        },
    )

    assert _discover(_endpoint_workspace()) == ()


def test_protected_inventory_rejects_live_gateway_signed_cutover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _discovery_defaults(monkeypatch)
    monkeypatch.setattr(
        protection,
        "read_signed_cutover_journal",
        lambda *_args, **_kwargs: {
            "old_gateway_endpoint": "retiring-gateway",
        },
    )

    with pytest.raises(RuntimeError, match="cutover journal to be absent"):
        _discover(_endpoint_workspace())


def test_protected_inventory_rejects_blue_pin_outside_signed_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rollback = {
        "gateway_resources": {
            "gateway_endpoint": "signed-gateway",
            "gateway_endpoint_id": "signed-gateway-id",
            "gateway_endpoint_creator": "creator",
            "supervisor_id": "signed-supervisor",
            "supervisor_endpoint": "signed-supervisor-endpoint",
            "supervisor_endpoint_id": "signed-supervisor-endpoint-id",
            "supervisor_endpoint_creator": "creator",
        }
    }
    monkeypatch.setattr(protection, "_load_record", lambda *_args, **_kwargs: rollback)
    monkeypatch.setattr(
        protection,
        "read_signed_cutover_journal",
        lambda *_args, **_kwargs: None,
    )
    pins = {
        "MIP_CUTOVER_SIGNED_BLUE_GATEWAY_PIN_JSON": {
            "name": "foreign-gateway",
            "endpoint_id": "foreign-id",
            "creator": "creator",
        },
        "MIP_CUTOVER_SIGNED_BLUE_SUPERVISOR_PIN_JSON": {
            "supervisor_id": "signed-supervisor",
            "endpoint": "signed-supervisor-endpoint",
            "endpoint_id": "signed-supervisor-endpoint-id",
            "creator": "creator",
        },
    }
    monkeypatch.setattr(
        protection,
        "json_pin_from_env",
        lambda name: pins[name],
    )
    monkeypatch.setattr(
        protection,
        "_registration_recovery_contracts",
        lambda *_args, **_kwargs: [],
    )

    with pytest.raises(RuntimeError, match="signed-blue pins escaped signed rollback"):
        _discover(_endpoint_workspace())


def _registration_recovery_inventory(
    monkeypatch: pytest.MonkeyPatch,
    *,
    value: str | None,
    retired: bool,
    owner: str = _IDENTITIES["runtime"],
    attestation_epoch: str = "current",
    durable_source: str | None = None,
    durable_run_id: str | None = None,
    durable_tags: dict[str, str] | None = None,
    malformed: bool = False,
    delayed_visibility: bool = False,
) -> list[dict[str, Any]]:
    version = {
        "version": "1",
        "source": "models:/m-reviewed-gateway",
        "logged_model_id": "m-reviewed-gateway",
        "run_id": "source-run-id",
        "tags": {"mip.gateway.contract.version": "3"},
        "attestation_epoch": attestation_epoch,
    }
    experiment_id = "experiment-id"
    workspace = SimpleNamespace(
        registered_models=SimpleNamespace(
            list=lambda **_kwargs: [
                SimpleNamespace(full_name=_MODEL, owner=owner)
            ],
            get=lambda selected: SimpleNamespace(
                full_name=selected,
                owner=owner,
            ),
        )
    )
    tracking = SimpleNamespace(
        get_experiment=lambda selected: SimpleNamespace(
            experiment_id=selected,
            name=protection.gateway_experiment_name(
                base_experiment_name="mip-agent-runtime-gateway-proxy",
                contract_hash="aaaaaaaaaaaa",
                runtime_application_id=_IDENTITIES["runtime"],
            ),
            lifecycle_stage="active",
            tags={"mlflow.ownerEmail": _IDENTITIES["runtime"]},
        )
    )
    monkeypatch.setattr(
        protection,
        "inventory_gateway_model_versions",
        lambda *_args, **_kwargs: ([version], "a" * 64, experiment_id),
    )
    state = SimpleNamespace(value=value, retired=retired)
    if delayed_visibility:
        states = iter(
            [
                SimpleNamespace(value=None, retired=False),
                state,
            ]
        )
        monkeypatch.setattr(
            protection.registration_journal_store,
            "read_journal_tag_state",
            lambda *_args, **_kwargs: next(states),
        )
        monkeypatch.setattr(protection, "_REGISTRATION_VISIBILITY_ATTEMPTS", 2)
        monkeypatch.setattr(protection, "_REGISTRATION_VISIBILITY_INTERVAL_S", 0.0)
    else:
        monkeypatch.setattr(
            protection.registration_journal_store,
            "load_journal_tag_state",
            lambda *_args, **_kwargs: state,
        )
    durable = SimpleNamespace(
        model_name=_MODEL,
        journal=SimpleNamespace(
            experiment_id=experiment_id,
            model_source=durable_source or version["source"],
            logged_model_id=version["logged_model_id"],
            source_run_id=durable_run_id or version["run_id"],
        ),
        registration_tags=durable_tags or version["tags"],
    )
    monkeypatch.setattr(
        protection,
        "_parse_durable_journal",
        (
            lambda _value: (_ for _ in ()).throw(
                RuntimeError("Gateway durable registration journal is malformed")
            )
        )
        if malformed
        else lambda _value: durable,
    )
    return protection._registration_recovery_contracts(
        workspace,
        object(),
        tracking,
        runtime_application_id=_IDENTITIES["runtime"],
        model_family="mip.audit.mortgage_growth_supervisor_proxy",
        experiment_base="mip-agent-runtime-gateway-proxy",
        catalog="mip",
        inference_schema="audit",
        inference_table_prefix="mip_agent_gateway_growth_agent",
    )


def test_registration_recovery_zero_zero_state_is_an_unprotected_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _registration_recovery_inventory(
        monkeypatch,
        value=None,
        retired=False,
    ) == []


def test_registration_recovery_rejects_asymmetric_orphan_retirement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(RuntimeError, match="retirement is orphaned"):
        _registration_recovery_inventory(
            monkeypatch,
            value=None,
            retired=True,
        )


def test_exact_retired_registration_journal_is_evidence_not_protection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _registration_recovery_inventory(
        monkeypatch,
        value="signed-journal",
        retired=True,
    ) == []


def test_active_registration_journal_protects_exact_current_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    protected = _registration_recovery_inventory(
        monkeypatch,
        value="signed-journal",
        retired=False,
    )

    assert len(protected) == 1
    assert protected[0]["kind"] == "registration-recovery"
    assert protected[0]["gateway_model_name"] == _MODEL


def test_registration_recovery_polls_delayed_absence_until_active_journal_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    protected = _registration_recovery_inventory(
        monkeypatch,
        value="signed-journal",
        retired=False,
        delayed_visibility=True,
    )

    assert len(protected) == 1
    assert protected[0]["kind"] == "registration-recovery"


def test_active_registration_journal_rejects_stale_candidate_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(RuntimeError, match="candidate is stale"):
        _registration_recovery_inventory(
            monkeypatch,
            value="signed-journal",
            retired=False,
            owner="other-runtime",
        )


@pytest.mark.parametrize(
    "journal_drift",
    [
        {"durable_source": "models:/m-other-source"},
        {"durable_run_id": "other-source-run"},
        {"durable_tags": {"mip.gateway.contract.version": "other"}},
    ],
)
def test_active_registration_journal_rejects_durable_lineage_or_tag_drift(
    monkeypatch: pytest.MonkeyPatch,
    journal_drift: dict[str, Any],
) -> None:
    with pytest.raises(RuntimeError, match="journal diverged"):
        _registration_recovery_inventory(
            monkeypatch,
            value="signed-journal",
            retired=False,
            **journal_drift,
        )


def test_active_registration_journal_rejects_previous_attestation_epoch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(RuntimeError, match="candidate is stale"):
        _registration_recovery_inventory(
            monkeypatch,
            value="signed-journal",
            retired=False,
            attestation_epoch="previous",
        )


def test_malformed_active_registration_journal_error_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(RuntimeError, match="journal is malformed"):
        _registration_recovery_inventory(
            monkeypatch,
            value="malformed-journal",
            retired=False,
            malformed=True,
        )
