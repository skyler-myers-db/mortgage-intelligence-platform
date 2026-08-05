"""Protected-allocation and effective-access proofs for Gateway archival."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from backend.agents.gateway_contract import (
    GATEWAY_RUNTIME_RESOURCE_ENV,
    verified_gateway_runtime_resource_environment,
)
from tools.databricks import gateway_registration_journal as registration_journal_store
from tools.databricks.agent_runtime_uc_inventory import _effective_privilege_sources
from tools.databricks.app_gateway_access_mode import _required_pin, json_pin_from_env
from tools.databricks.app_rollback_record_contract import _load_record
from tools.databricks.cutover_journal_store import read_signed_cutover_journal
from tools.databricks.gateway_model_archival_inventory import (
    inventory_gateway_model_versions,
)
from tools.databricks.gateway_model_retirement_record import record_sha256
from tools.databricks.gateway_registration_recovery import _parse_durable_journal
from tools.databricks.gateway_resource_identity import gateway_experiment_name
from tools.databricks.serving_endpoint_identity import uc_model_serving_identity

_MODEL_SUFFIX = re.compile(r"[0-9a-f]{12}\Z")
_REGISTRATION_VISIBILITY_ATTEMPTS = 10
_REGISTRATION_VISIBILITY_INTERVAL_S = 0.5


def _field(value: Any, name: str) -> str:
    raw = value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)
    return str(raw or "").strip()


def _config_entities(config: Any, collection: str) -> list[Any]:
    if config is None:
        return []
    raw = (
        config.get(collection)
        if isinstance(config, Mapping)
        else getattr(config, collection, None)
    )
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise RuntimeError("Gateway protected-allocation endpoint shape is invalid")
    return raw


def _allocation(
    kind: str,
    contract: Mapping[str, Any],
    *,
    gateway_model_name: str = "",
) -> dict[str, Any]:
    exact = {str(key): value for key, value in contract.items()}
    return {
        "kind": kind,
        "gateway_model_name": gateway_model_name,
        "contract": exact,
        "contract_sha256": record_sha256(exact),
    }


def _endpoint_contracts(
    workspace: Any,
    *,
    model_family: str,
) -> list[dict[str, Any]]:
    protected: list[dict[str, Any]] = []
    family_pattern = re.compile(rf"{re.escape(model_family)}_[0-9a-f]{{12}}\Z")
    for summary in workspace.serving_endpoints.list():
        endpoint_name = _field(summary, "name")
        if not endpoint_name:
            raise RuntimeError("Gateway protected-allocation endpoint has no name")
        details = workspace.serving_endpoints.get(endpoint_name)
        for phase, config in (
            ("current", getattr(details, "config", None)),
            ("pending", getattr(details, "pending_config", None)),
        ):
            aliases: dict[str, str] = {}
            seen_contracts: set[tuple[str, str]] = set()
            for collection in ("served_entities", "served_models"):
                for index, entity in enumerate(_config_entities(config, collection)):
                    environment = getattr(entity, "environment_vars", None)
                    if environment is None and isinstance(entity, Mapping):
                        environment = entity.get("environment_vars")
                    raw = dict(environment or {})
                    binding = {
                        str(key): str(value)
                        for key, value in raw.items()
                        if str(key) in GATEWAY_RUNTIME_RESOURCE_ENV
                    }
                    identity = uc_model_serving_identity(entity)
                    if identity is None:
                        if binding:
                            raise RuntimeError(
                                "Gateway protected non-UC entity carries a runtime contract: "
                                f"{endpoint_name}/{phase}/{collection}[{index}]"
                            )
                        continue
                    entity_name, entity_version, alias = identity
                    if not binding:
                        if family_pattern.fullmatch(entity_name) is not None:
                            raise RuntimeError(
                                "Gateway family endpoint entity lacks its runtime contract: "
                                f"{endpoint_name}/{phase}/{collection}[{index}]"
                            )
                        continue
                    contract = verified_gateway_runtime_resource_environment(binding)
                    model_name = str(contract.get("gateway_model_name") or "").strip()
                    model_version = str(
                        contract.get("gateway_model_version") or ""
                    ).strip()
                    if (
                        not model_name
                        or model_name != entity_name
                        or not model_version
                        or model_version != entity_version
                    ):
                        raise RuntimeError("Gateway protected endpoint contract has no model")
                    expected_alias = (
                        "mip-growth-supervisor-proxy-"
                        f"{model_version}"
                    )
                    if alias != expected_alias:
                        raise RuntimeError(
                            "Gateway protected endpoint alias is not canonical"
                        )
                    if alias in aliases and aliases[alias] != entity_name:
                        raise RuntimeError("Gateway protected endpoint alias is ambiguous")
                    aliases[alias] = entity_name
                    contract_identity = (model_name, record_sha256(contract))
                    if contract_identity in seen_contracts:
                        continue
                    seen_contracts.add(contract_identity)
                    protected.append(
                        _allocation(
                            f"endpoint-{phase}-{endpoint_name}-{collection}-{index}",
                            contract,
                            gateway_model_name=model_name,
                        )
                    )
    return protected


def _registration_recovery_contracts(
    workspace: Any,
    model_registry: Any,
    tracking_client: Any,
    *,
    runtime_application_id: str,
    model_family: str,
    experiment_base: str,
    catalog: str,
    inference_schema: str,
    inference_table_prefix: str,
) -> list[dict[str, Any]]:
    """Authenticate active registration journals that preserve pre-serving candidates."""

    pattern = re.compile(rf"{re.escape(model_family)}_[0-9a-f]{{12}}\Z")
    protected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for registered in workspace.registered_models.list(include_browse=True):
        model_name = _field(registered, "full_name")
        if pattern.fullmatch(model_name) is None:
            continue
        if model_name in seen:
            raise RuntimeError("Gateway registration protection inventory is duplicated")
        seen.add(model_name)
        versions, _versions_sha256, experiment_id = inventory_gateway_model_versions(
            model_registry,
            tracking_client,
            model_name=model_name,
            runtime_application_id=runtime_application_id,
            model_family=model_family,
            experiment_base=experiment_base,
            catalog=catalog,
            inference_schema=inference_schema,
            inference_table_prefix=inference_table_prefix,
        )
        version = versions[0]
        state = registration_journal_store.load_journal_tag_state(
            tracking_client,
            experiment_id=experiment_id,
            attempts=_REGISTRATION_VISIBILITY_ATTEMPTS,
            interval_s=_REGISTRATION_VISIBILITY_INTERVAL_S,
        )
        if state.value is None:
            if state.retired:
                raise RuntimeError("Gateway registration protection retirement is orphaned")
            continue
        durable = _parse_durable_journal(state.value)
        if (
            durable.model_name != model_name
            or durable.journal.experiment_id != experiment_id
            or durable.journal.model_source != version["source"]
            or durable.journal.logged_model_id != version["logged_model_id"]
            or durable.journal.source_run_id != version["run_id"]
            or durable.registration_tags != version["tags"]
        ):
            raise RuntimeError("Gateway registration protection journal diverged")
        if state.retired:
            continue
        suffix = model_name.rsplit("_", 1)[-1]
        model_details = workspace.registered_models.get(model_name)
        experiment = tracking_client.get_experiment(experiment_id)
        tags = getattr(experiment, "tags", None)
        owner = (
            str(tags.get("mlflow.ownerEmail") or "").strip()
            if isinstance(tags, Mapping)
            else ""
        )
        lifecycle_raw = getattr(experiment, "lifecycle_stage", None)
        lifecycle = str(
            getattr(lifecycle_raw, "value", lifecycle_raw) or ""
        ).strip().lower()
        if (
            _MODEL_SUFFIX.fullmatch(suffix) is None
            or _field(model_details, "full_name") != model_name
            or _field(model_details, "owner") != runtime_application_id
            or version["attestation_epoch"] != "current"
            or _field(experiment, "experiment_id") != experiment_id
            or _field(experiment, "name")
            != gateway_experiment_name(
                base_experiment_name=experiment_base,
                contract_hash=suffix,
                runtime_application_id=runtime_application_id,
            )
            or lifecycle != "active"
            or owner != runtime_application_id
        ):
            raise RuntimeError("Gateway registration protection candidate is stale")
        protected.append(
            _allocation(
                "registration-recovery",
                {
                    "model_name": model_name,
                    "model_version": version["version"],
                    "model_source": durable.journal.model_source,
                    "logged_model_id": durable.journal.logged_model_id,
                    "source_run_id": durable.journal.source_run_id,
                    "experiment_id": experiment_id,
                    "registration_tags": durable.registration_tags,
                    "registration_tags_sha256": record_sha256(
                        durable.registration_tags
                    ),
                    "journal_sha256": registration_journal_store.journal_digest(
                        state.value
                    ),
                },
                gateway_model_name=model_name,
            )
        )
    return protected


def discover_protected_allocation_contracts(
    workspace: Any,
    model_registry: Any,
    tracking_client: Any,
    *,
    app_name: str,
    runtime_application_id: str,
    rollback_scope: str,
    expected_lakebase_instance: str,
    model_family: str,
    experiment_base: str,
    catalog: str,
    inference_schema: str,
    inference_table_prefix: str,
) -> tuple[dict[str, Any], ...]:
    """Authenticate every discoverable current, blue, rollback, and cutover pin."""

    protected = _endpoint_contracts(workspace, model_family=model_family)
    protected.extend(
        _registration_recovery_contracts(
            workspace,
            model_registry,
            tracking_client,
            runtime_application_id=runtime_application_id,
            model_family=model_family,
            experiment_base=experiment_base,
            catalog=catalog,
            inference_schema=inference_schema,
            inference_table_prefix=inference_table_prefix,
        )
    )
    rollback: Mapping[str, Any] | None = None
    try:
        rollback = _load_record(
            workspace,
            app_name=app_name,
            scope=rollback_scope,
            expected_lakebase_instance=expected_lakebase_instance,
        )
    except RuntimeError as exc:
        if "no server-owned last-good App rollback contract exists" not in str(exc):
            raise
    else:
        resources = rollback.get("gateway_resources")
        if not isinstance(resources, Mapping):
            raise RuntimeError("Gateway rollback protected allocation is invalid")
        protected.append(
            _allocation(
                "rollback",
                rollback,
                gateway_model_name=str(resources.get("gateway_model_name") or "").strip(),
            )
        )
    cutover = read_signed_cutover_journal(
        workspace,
        runtime_application_id=runtime_application_id,
    )
    # A Supervisor-only retirement journal has no Gateway allocation to
    # preserve. It may coexist with archival after an in-place Gateway handoff
    # during first governed adoption. A journal that pins an outer Gateway
    # still blocks archival until its signed-blue lifecycle is resolved.
    if cutover is not None and cutover.get("old_gateway_endpoint"):
        raise RuntimeError(
            "Gateway model archival requires the signed cutover journal to be absent"
        )
    blue_gateway = json_pin_from_env("MIP_CUTOVER_SIGNED_BLUE_GATEWAY_PIN_JSON")
    blue_supervisor = json_pin_from_env("MIP_CUTOVER_SIGNED_BLUE_SUPERVISOR_PIN_JSON")
    if (blue_gateway is None) != (blue_supervisor is None):
        raise RuntimeError("Gateway archival requires both signed-blue pins")
    if blue_gateway is not None and blue_supervisor is not None:
        if rollback is None:
            raise RuntimeError("Gateway archival signed-blue pins lack signed rollback authority")
        gateway_pin = _required_pin(
            blue_gateway,
            fields=("name", "endpoint_id", "creator"),
            label="signed-blue Gateway",
        )
        supervisor_pin = _required_pin(
            blue_supervisor,
            fields=("supervisor_id", "endpoint", "endpoint_id", "creator"),
            label="signed-blue Supervisor",
        )
        resources = rollback.get("gateway_resources")
        if not isinstance(resources, Mapping):
            raise RuntimeError("Gateway archival rollback resources are invalid")
        if gateway_pin != {
            "name": resources.get("gateway_endpoint"),
            "endpoint_id": resources.get("gateway_endpoint_id"),
            "creator": resources.get("gateway_endpoint_creator"),
        } or supervisor_pin != {
            "supervisor_id": resources.get("supervisor_id"),
            "endpoint": resources.get("supervisor_endpoint"),
            "endpoint_id": resources.get("supervisor_endpoint_id"),
            "creator": resources.get("supervisor_endpoint_creator"),
        }:
            raise RuntimeError("Gateway archival signed-blue pins escaped signed rollback")
        protected.extend(
            [
                _allocation("signed-blue-gateway", gateway_pin),
                _allocation("signed-blue-supervisor", supervisor_pin),
            ]
        )
    protected.sort(key=lambda item: (str(item["kind"]), str(item["contract_sha256"])))
    keys = [(item["kind"], item["contract_sha256"]) for item in protected]
    if len(keys) != len(set(keys)):
        raise RuntimeError("Gateway protected-allocation inventory contains duplicates")
    return tuple(protected)


def _principal_groups(workspace: Any, application_id: str) -> set[str]:
    matches = list(
        workspace.service_principals.list(
            filter=f'applicationId eq "{application_id}"',
            attributes="id,applicationId,groups",
            count=2,
        )
    )
    if len(matches) != 1 or _field(matches[0], "application_id") != application_id:
        raise RuntimeError("Gateway archival access principal identity is ambiguous")
    principal_id = _field(matches[0], "id")
    principal = workspace.service_principals.get(principal_id)
    if _field(principal, "application_id") != application_id:
        raise RuntimeError("Gateway archival access principal identity drifted")
    groups = getattr(principal, "groups", None)
    if groups is None:
        raise RuntimeError("Gateway archival access principal groups are unavailable")
    values: set[str] = set()
    for group in groups:
        display = _field(group, "display")
        value = _field(group, "value")
        if not display or not value:
            raise RuntimeError("Gateway archival access principal group is incomplete")
        values.update({display, value})
    return values


def _experiment_permissions_for(
    entries: Sequence[Mapping[str, Any]],
    *,
    application_id: str,
    groups: set[str],
) -> list[str]:
    permissions: list[str] = []
    for entry in entries:
        named = {
            str(entry.get("service_principal_name") or "").strip(),
            str(entry.get("user_name") or "").strip(),
        }
        group = str(entry.get("group_name") or "").strip()
        if application_id not in named and (not group or group not in groups):
            continue
        raw_permissions = entry.get("all_permissions")
        if not isinstance(raw_permissions, list):
            raise RuntimeError("Gateway archival experiment ACL shape drifted")
        for permission in raw_permissions:
            if not isinstance(permission, Mapping):
                raise RuntimeError("Gateway archival experiment permission shape drifted")
            level = str(permission.get("permission_level") or "").strip()
            if not level:
                raise RuntimeError("Gateway archival experiment permission is incomplete")
            permissions.append(level)
    return sorted(set(permissions))


def _abac_policy_digest(
    workspace: Any,
    *,
    model_name: str,
    table_names: Sequence[str],
) -> str:
    catalog, schema, _model = model_name.split(".", 2)
    scopes = {
        ("CATALOG", catalog),
        ("SCHEMA", f"{catalog}.{schema}"),
        *(("TABLE", table_name) for table_name in table_names),
    }
    policies: list[dict[str, Any]] = []
    for securable_type, full_name in sorted(scopes):
        query: dict[str, Any] = {"include_inherited": True, "max_results": 1000}
        seen_tokens: set[str] = set()
        while True:
            response = workspace.api_client.do(
                "GET",
                f"/api/2.1/unity-catalog/policies/{securable_type}/{full_name}",
                query=query,
            )
            if not isinstance(response, Mapping):
                raise RuntimeError("Gateway archival ABAC policy response is invalid")
            raw_policies = response.get("policies", [])
            if not isinstance(raw_policies, list) or any(
                not isinstance(policy, Mapping) for policy in raw_policies
            ):
                raise RuntimeError("Gateway archival ABAC policy response is invalid")
            for policy in raw_policies:
                exact = {str(key): value for key, value in policy.items()}
                policy_type = str(exact.get("policy_type") or "").upper()
                target_type = str(exact.get("for_securable_type") or "").upper()
                if policy_type == "POLICY_TYPE_GRANT" and target_type in {
                    "MODEL",
                    "REGISTERED_MODEL",
                    "FUNCTION",
                }:
                    raise RuntimeError(
                        "Gateway archival is blocked by an applicable ABAC model GRANT policy"
                    )
                policies.append(
                    {
                        "scope_type": securable_type,
                        "scope_name": full_name,
                        "policy": exact,
                    }
                )
            raw_token = response.get("next_page_token")
            if raw_token is None:
                token = ""
            elif type(raw_token) is not str or raw_token != raw_token.strip():
                raise RuntimeError("Gateway archival ABAC pagination token is invalid")
            else:
                token = raw_token
            if not token:
                break
            if token in seen_tokens:
                raise RuntimeError("Gateway archival ABAC pagination repeated a token")
            seen_tokens.add(token)
            query["page_token"] = token
    policies.sort(key=record_sha256)
    return record_sha256(policies)


def zero_effective_access_evidence(
    workspace: Any,
    *,
    experiment_acl: Sequence[Mapping[str, Any]],
    model_name: str,
    table_names: Sequence[str],
    runtime_application_id: str,
    app_application_id: str,
    proxy_application_id: str,
    verifier_application_id: str,
) -> tuple[dict[str, Any], ...]:
    """Prove no protected execution identity retains UC or experiment access."""

    principals = (
        ("runtime", runtime_application_id),
        ("app", app_application_id),
        ("proxy", proxy_application_id),
        ("verifier", verifier_application_id),
    )
    ids = [application_id.strip() for _role, application_id in principals]
    if any(not application_id for application_id in ids) or len(ids) != len(set(ids)):
        raise ValueError("Gateway archival protected identities must be complete and distinct")
    policy_digest = _abac_policy_digest(
        workspace,
        model_name=model_name,
        table_names=table_names,
    )
    evidence: list[dict[str, Any]] = []
    for role, application_id in principals:
        groups = _principal_groups(workspace, application_id)
        resources: list[dict[str, Any]] = []
        for securable_type, full_name in (
            ("function", model_name),
            *(("table", table_name) for table_name in sorted(table_names)),
        ):
            privileges = _effective_privilege_sources(
                workspace,
                securable_type=securable_type,
                full_name=full_name,
                principal=application_id,
            )
            normalized = {
                privilege: sorted([list(source) for source in sources])
                for privilege, sources in sorted(privileges.items())
            }
            resources.append(
                {
                    "securable_type": securable_type,
                    "full_name": full_name,
                    "privileges": normalized,
                }
            )
        experiment_permissions = _experiment_permissions_for(
            experiment_acl,
            application_id=application_id,
            groups=groups,
        )
        if any(resource["privileges"] for resource in resources) or experiment_permissions:
            raise RuntimeError(
                f"archived Gateway allocation remains accessible to {role} identity"
            )
        evidence.append(
            {
                "role": role,
                "application_id": application_id,
                "groups_sha256": record_sha256(sorted(groups)),
                "abac_policies_sha256": policy_digest,
                "resources": resources,
                "experiment_permissions": experiment_permissions,
            }
        )
    return tuple(evidence)
