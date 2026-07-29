"""Authoritative inventory for one governed Gateway model archival."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from databricks.sdk.errors import NotFound, ResourceDoesNotExist
from tools.databricks.gateway_model_attestation import (
    gateway_model_contract_from_tags,
    verify_gateway_model_contract,
)
from tools.databricks.gateway_model_retirement_record import record_sha256
from tools.databricks.gateway_resource_identity import gateway_experiment_name
from tools.databricks.mlflow_uc_model_versions import (
    authoritative_model_version,
    model_version_field,
    model_version_tags,
)
from tools.databricks.serving_endpoint_identity import (
    is_platform_foundation_endpoint,
    uc_model_serving_identity,
)

_SOURCE = re.compile(r"models:/(?P<model_id>m-[A-Za-z0-9][A-Za-z0-9_-]*)\Z")
_MODEL_SUFFIX = re.compile(r"[0-9a-f]{12}\Z")
_MAX_ENDPOINTS = 10_000
_PRINCIPAL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@-]*\Z")
_APP_NAME = re.compile(r"[a-z0-9][a-z0-9_-]*\Z")


@dataclass(frozen=True)
class GatewayModelArchiveInventory:
    """Exact source, experiment, table, and serving state before mutation."""

    model_name: str
    model_owner: str
    versions: tuple[dict[str, Any], ...]
    versions_sha256: str
    logged_model_ids: tuple[str, ...]
    source_run_ids: tuple[str, ...]
    experiment_id: str
    experiment_name: str
    experiment_artifact_location: str
    experiment_lifecycle_state: str
    experiment_owner: str
    experiment_tags: dict[str, str]
    experiment_tags_sha256: str
    experiment_acl: tuple[dict[str, Any], ...]
    experiment_acl_sha256: str
    inference_tables: tuple[dict[str, str], ...]
    expected_absent_inference_tables: tuple[str, ...]
    serving_inventory: tuple[dict[str, Any], ...]
    serving_inventory_sha256: str
    serving_references: tuple[dict[str, Any], ...]
    serving_references_sha256: str


def archive_experiment_name(
    *,
    archive_owner: str,
    app_name: str,
    model_name: str,
) -> str:
    """Return the deterministic governance-home name for an archived experiment."""

    if (
        _PRINCIPAL.fullmatch(archive_owner) is None
        or _APP_NAME.fullmatch(app_name) is None
        or not model_name.strip()
    ):
        raise ValueError("Gateway retirement archive experiment scope is invalid")
    model_key = record_sha256(model_name)[:24]
    return (
        f"/Users/{archive_owner}/.mip-gateway-archive/"
        f"{app_name}/{model_key}"
    )


def _field(value: Any, name: str) -> str:
    raw = value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)
    enum_value = getattr(raw, "value", None)
    if type(enum_value) is str:
        raw = enum_value
    return str(raw or "").strip()


def _mapping(value: Any, *, resource: str) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    as_dict = getattr(value, "as_dict", None)
    if callable(as_dict):
        converted = as_dict()
        if isinstance(converted, Mapping):
            return converted
    raise RuntimeError(f"{resource} returned an invalid object")


def _json_value(value: Any, *, resource: str) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _json_value(item, resource=resource)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    as_dict = getattr(value, "as_dict", None)
    if callable(as_dict):
        return _json_value(as_dict(), resource=resource)
    if isinstance(value, list | tuple):
        return [_json_value(item, resource=resource) for item in value]
    if value is None or type(value) in {str, int, float, bool}:
        return value
    enum_value = getattr(value, "value", None)
    if type(enum_value) is str:
        return enum_value
    raise RuntimeError(f"{resource} returned a non-JSON value")


def exact_experiment_acl(workspace: Any, *, experiment_id: str) -> tuple[dict[str, Any], ...]:
    """Return every direct and inherited ACL entry in a canonical order."""

    response = workspace.experiments.get_permissions(experiment_id)
    document = _mapping(response, resource="Gateway experiment permissions")
    entries = document.get("access_control_list")
    if not isinstance(entries, list) or not entries:
        raise RuntimeError("Gateway experiment ACL is missing")
    normalized = [
        _json_value(entry, resource="Gateway experiment ACL")
        for entry in entries
    ]
    if any(not isinstance(entry, dict) for entry in normalized):
        raise RuntimeError("Gateway experiment ACL contains a non-object entry")
    principals: set[tuple[str, str]] = set()
    for entry in normalized:
        named = [
            (field, str(entry.get(field) or ""))
            for field in ("group_name", "service_principal_name", "user_name")
            if str(entry.get(field) or "")
        ]
        if len(named) != 1 or named[0][1] != named[0][1].strip():
            raise RuntimeError("Gateway experiment ACL principal is not canonical")
        if named[0] in principals:
            raise RuntimeError("Gateway experiment ACL contains duplicate principals")
        principals.add(named[0])
    normalized.sort(key=record_sha256)
    if len({record_sha256(entry) for entry in normalized}) != len(normalized):
        raise RuntimeError("Gateway experiment ACL contains duplicate entries")
    return tuple(normalized)


def _search_versions(model_registry: Any, *, model_name: str) -> list[Any]:
    results: list[Any] = []
    token: str | None = None
    seen: set[str] = set()
    while True:
        page = model_registry.search_model_versions(
            filter_string=f"name='{model_name}'",
            max_results=1000,
            page_token=token,
        )
        results.extend(page)
        next_token = str(getattr(page, "token", "") or "").strip()
        if not next_token:
            return results
        if next_token in seen:
            raise RuntimeError("Gateway retirement model-version pagination repeated a token")
        seen.add(next_token)
        token = next_token


def _source_identity(
    tracking_client: Any,
    *,
    model_source: str,
) -> tuple[str, str, str]:
    match = _SOURCE.fullmatch(model_source)
    if match is None:
        raise RuntimeError("Gateway retirement source is not an immutable logged-model URI")
    logged_model_id = match.group("model_id")
    logged = tracking_client.get_logged_model(logged_model_id)
    if _field(logged, "model_id") != logged_model_id:
        raise RuntimeError("Gateway retirement logged-model identity drifted")
    source_run_id = _field(logged, "source_run_id")
    experiment_id = _field(logged, "experiment_id")
    if not source_run_id or not experiment_id:
        raise RuntimeError("Gateway retirement logged model lacks run or experiment identity")
    run = tracking_client.get_run(source_run_id)
    run_info = getattr(run, "info", None)
    run_id = _field(run_info, "run_id") or _field(run, "run_id")
    run_experiment = _field(run_info, "experiment_id") or _field(run, "experiment_id")
    if run_id != source_run_id or run_experiment != experiment_id:
        raise RuntimeError("Gateway retirement source run identity drifted")
    return logged_model_id, source_run_id, experiment_id


def _model_versions(
    model_registry: Any,
    tracking_client: Any,
    *,
    model_name: str,
    runtime_application_id: str,
    model_family: str,
    experiment_base: str,
    catalog: str,
    inference_schema: str,
    inference_table_prefix: str,
) -> tuple[tuple[dict[str, Any], ...], str, str]:
    raw_versions = _search_versions(model_registry, model_name=model_name)
    if len(raw_versions) != 1:
        raise RuntimeError("Gateway retirement target requires exactly one model version")
    versions: list[dict[str, Any]] = []
    experiment_ids: set[str] = set()
    for raw in raw_versions:
        version = authoritative_model_version(
            model_registry,
            raw,
            expected_model_name=model_name,
        )
        number = model_version_field(version, "version")
        status = model_version_field(version, "status").upper()
        source = model_version_field(version, "source")
        if not number or status != "READY" or not source:
            raise RuntimeError("Gateway retirement target has a non-READY model version")
        tags = model_version_tags(
            version,
            resource=f"Gateway retirement model {model_name} v{number}",
        )
        contract = gateway_model_contract_from_tags(tags)
        current_epoch = verify_gateway_model_contract(tags=tags, **contract)
        if (
            contract["full_name"] != model_name
            or contract["model_source"] != source
            or contract["runtime_application_id"] != runtime_application_id
            or contract["model_family"] != model_family
            or contract["experiment_base"] != experiment_base
            or contract["catalog"] != catalog
            or contract["inference_schema"] != inference_schema
            or contract["inference_table_prefix"] != inference_table_prefix
        ):
            raise RuntimeError("Gateway retirement model contract escaped its exact scope")
        logged_model_id, run_id, experiment_id = _source_identity(
            tracking_client,
            model_source=source,
        )
        experiment_ids.add(experiment_id)
        versions.append(
            {
                "version": number,
                "status": status,
                "source": source,
                "source_sha256": record_sha256(source),
                "run_id": run_id,
                "logged_model_id": logged_model_id,
                "attestation_epoch": "current" if current_epoch else "previous",
                "tags": dict(sorted(tags.items())),
                "tags_sha256": record_sha256(tags),
            }
        )
    try:
        versions.sort(key=lambda item: int(str(item["version"])))
    except ValueError as exc:
        raise RuntimeError("Gateway retirement model version is not numeric") from exc
    if len(experiment_ids) != 1:
        raise RuntimeError("Gateway retirement target has multiple source experiments")
    return tuple(versions), record_sha256(versions), next(iter(experiment_ids))


def inventory_gateway_model_versions(
    model_registry: Any,
    tracking_client: Any,
    *,
    model_name: str,
    runtime_application_id: str,
    model_family: str,
    experiment_base: str,
    catalog: str,
    inference_schema: str,
    inference_table_prefix: str,
) -> tuple[tuple[dict[str, Any], ...], str, str]:
    """Expose signed source evidence for post-mutation identity checks."""

    return _model_versions(
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


def _experiment(
    workspace: Any,
    tracking_client: Any,
    *,
    experiment_id: str,
    model_name: str,
    runtime_application_id: str,
    experiment_base: str,
) -> tuple[str, str, str, str, dict[str, str], tuple[dict[str, Any], ...]]:
    experiment = tracking_client.get_experiment(experiment_id)
    if _field(experiment, "experiment_id") != experiment_id:
        raise RuntimeError("Gateway retirement experiment identity drifted")
    name = _field(experiment, "name")
    artifact_location = _field(experiment, "artifact_location")
    lifecycle = _field(experiment, "lifecycle_stage").lower()
    tags = getattr(experiment, "tags", None)
    if not isinstance(tags, Mapping) or any(
        type(key) is not str or type(value) is not str for key, value in tags.items()
    ):
        raise RuntimeError("Gateway retirement experiment tags are unavailable")
    exact_tags = {str(key): str(value) for key, value in sorted(tags.items())}
    owner = str(exact_tags.get("mlflow.ownerEmail") or "").strip()
    suffix = model_name.rsplit("_", 1)[-1]
    if _MODEL_SUFFIX.fullmatch(suffix) is None:
        raise RuntimeError("Gateway retirement model allocation suffix is invalid")
    expected_name = gateway_experiment_name(
        base_experiment_name=experiment_base,
        contract_hash=suffix,
        runtime_application_id=runtime_application_id,
    )
    if (
        not artifact_location
        or lifecycle != "active"
        or owner != runtime_application_id
        or name != expected_name
    ):
        raise RuntimeError("Gateway retirement experiment is not the exact active source")
    acl = exact_experiment_acl(workspace, experiment_id=experiment_id)
    return name, artifact_location, lifecycle, owner, exact_tags, acl


def _tables(
    workspace: Any,
    *,
    catalog: str,
    inference_schema: str,
    inference_table_prefix: str,
    model_name: str,
    runtime_application_id: str,
    delta_version_resolver: Callable[[str], str],
) -> tuple[tuple[dict[str, str], ...], tuple[str, ...]]:
    suffix = model_name.rsplit("_", 1)[-1]
    base = f"{inference_table_prefix}_{suffix}_payload"
    expected = {
        f"{catalog}.{inference_schema}.{base}",
        f"{catalog}.{inference_schema}.{base}_request_logs",
        f"{catalog}.{inference_schema}.{base}_assessment_logs",
    }
    present: list[dict[str, str]] = []
    seen: set[str] = set()
    for table in workspace.tables.list(
        catalog,
        inference_schema,
        include_browse=True,
        omit_columns=True,
        omit_properties=False,
    ):
        full_name = _field(table, "full_name")
        table_name = _field(table, "name")
        if table_name.startswith(base) and full_name not in expected:
            raise RuntimeError("Gateway retirement found an unexpected same-allocation table")
        if full_name in expected:
            if full_name in seen:
                raise RuntimeError("Gateway retirement table inventory is duplicated")
            seen.add(full_name)
    for full_name in sorted(expected):
        try:
            table = workspace.tables.get(
                full_name,
                include_browse=True,
                include_delta_metadata=True,
            )
        except (NotFound, ResourceDoesNotExist):
            continue
        if _field(table, "full_name") != full_name:
            raise RuntimeError("Gateway retirement table hydration escaped its target")
        table_id = _field(table, "table_id")
        owner = _field(table, "owner")
        storage_location = _field(table, "storage_location")
        data_source_format = _field(table, "data_source_format").upper()
        if (
            not table_id
            or owner != runtime_application_id
            or not storage_location
            or not data_source_format
        ):
            raise RuntimeError("Gateway retirement inference-table identity is invalid")
        delta_version = ""
        if data_source_format == "DELTA":
            delta_version = delta_version_resolver(full_name).strip()
            if not delta_version or not delta_version.isdigit():
                raise RuntimeError("Gateway retirement Delta version is invalid")
        present.append(
            {
                "full_name": full_name,
                "table_id": table_id,
                "owner": owner,
                "storage_location": storage_location,
                "data_source_format": data_source_format,
                "delta_latest_version": delta_version,
            }
        )
    present.sort(key=lambda item: item["full_name"])
    return tuple(present), tuple(
        sorted(expected - {item["full_name"] for item in present})
    )


def _served_collection(config: Any, name: str) -> list[Any]:
    if config is None:
        return []
    raw = config.get(name) if isinstance(config, Mapping) else getattr(config, name, None)
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise RuntimeError("Gateway retirement serving configuration is not a list")
    return raw


def _serving_inventory(
    workspace: Any,
    *,
    model_name: str,
    inference_table_family: str,
) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
    inventory: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []
    names: set[str] = set()
    for index, summary in enumerate(workspace.serving_endpoints.list()):
        if index >= _MAX_ENDPOINTS:
            raise RuntimeError("Gateway retirement endpoint inventory exceeds reviewed bound")
        name = _field(summary, "name")
        if not name or name in names:
            raise RuntimeError("Gateway retirement endpoint inventory is ambiguous")
        names.add(name)
        details = workspace.serving_endpoints.get(name)
        if is_platform_foundation_endpoint(details):
            continue
        endpoint_id = _field(details, "id")
        creator = _field(details, "creator")
        if _field(details, "name") not in {"", name} or not endpoint_id or not creator:
            raise RuntimeError("Gateway retirement endpoint identity is incomplete")
        endpoint: dict[str, Any] = {
            "name": name,
            "endpoint_id": endpoint_id,
            "creator": creator,
            "state": _json_value(
                getattr(details, "state", None),
                resource="Gateway retirement endpoint state",
            ),
            "config_version": _field(getattr(details, "config", None), "config_version"),
            "pending_config_version": _field(
                getattr(details, "pending_config", None),
                "config_version",
            ),
            "ai_gateway_inference_table": {},
            "configurations": [],
        }
        for phase, config in (
            ("current", getattr(details, "config", None)),
            ("pending", getattr(details, "pending_config", None)),
        ):
            aliases: dict[str, tuple[str, str]] = {}
            non_uc_aliases: set[str] = set()
            for collection in ("served_entities", "served_models"):
                for entity_index, entity in enumerate(_served_collection(config, collection)):
                    identity = uc_model_serving_identity(entity)
                    if identity is None:
                        entity_id = _field(entity, "name")
                        if entity_id:
                            if entity_id in aliases:
                                raise RuntimeError(
                                    "Gateway retirement serving alias is ambiguous"
                                )
                            non_uc_aliases.add(entity_id)
                        continue
                    entity_name, entity_version, entity_id = identity
                    reference = {
                        "endpoint_name": name,
                        "endpoint_id": endpoint_id,
                        "endpoint_creator": creator,
                        "phase": phase,
                        "collection": collection,
                        "index": entity_index,
                        "entity_name": entity_name,
                        "entity_version": entity_version,
                        "entity_id": entity_id,
                        "traffic_percentage": "",
                    }
                    endpoint["configurations"].append(reference)
                    if entity_id:
                        if entity_id in non_uc_aliases or (
                            entity_id in aliases
                            and aliases[entity_id]
                            != (
                                entity_name,
                                entity_version,
                            )
                        ):
                            raise RuntimeError(
                                "Gateway retirement serving alias is ambiguous"
                            )
                        aliases[entity_id] = (entity_name, entity_version)
                    if entity_name == model_name:
                        references.append(reference)
            traffic = (
                config.get("traffic_config")
                if isinstance(config, Mapping)
                else getattr(config, "traffic_config", None)
            )
            for route_index, route in enumerate(_served_collection(traffic, "routes")):
                route_aliases = {
                    _field(route, field)
                    for field in ("served_entity_name", "served_model_name")
                    if _field(route, field)
                }
                if len(route_aliases) != 1:
                    raise RuntimeError("Gateway retirement serving route is ambiguous")
                route_alias = next(iter(route_aliases))
                if route_alias in non_uc_aliases:
                    continue
                if not route_alias or route_alias not in aliases:
                    raise RuntimeError("Gateway retirement serving route is unresolved")
                entity_name, entity_version = aliases[route_alias]
                reference = {
                    "endpoint_name": name,
                    "endpoint_id": endpoint_id,
                    "endpoint_creator": creator,
                    "phase": phase,
                    "collection": "traffic_routes",
                    "index": route_index,
                    "entity_name": entity_name,
                    "entity_version": entity_version,
                    "entity_id": route_alias,
                    "traffic_percentage": _field(route, "traffic_percentage"),
                }
                endpoint["configurations"].append(reference)
                if entity_name == model_name:
                    references.append(reference)
        gateway = getattr(details, "ai_gateway", None)
        inference = (
            gateway.get("inference_table_config")
            if isinstance(gateway, Mapping)
            else getattr(gateway, "inference_table_config", None)
        )
        if inference is not None:
            exact_inference = _json_value(
                inference,
                resource="Gateway retirement inference-table configuration",
            )
            if not isinstance(exact_inference, dict):
                raise RuntimeError(
                    "Gateway retirement inference-table configuration is invalid"
                )
            endpoint["ai_gateway_inference_table"] = exact_inference
            family = ".".join(
                [
                    _field(inference, "catalog_name"),
                    _field(inference, "schema_name"),
                    _field(inference, "table_name_prefix"),
                ]
            )
            if family == inference_table_family:
                reference = {
                    "endpoint_name": name,
                    "endpoint_id": endpoint_id,
                    "endpoint_creator": creator,
                    "phase": "current",
                    "collection": "inference_table",
                    "index": 0,
                    "entity_name": family,
                    "entity_version": "",
                    "entity_id": family,
                    "traffic_percentage": "",
                }
                endpoint["configurations"].append(reference)
                references.append(reference)
        endpoint["configurations"].sort(key=record_sha256)
        inventory.append(endpoint)
    inventory.sort(key=lambda item: item["name"])
    references.sort(key=record_sha256)
    return tuple(inventory), tuple(references)


def inventory_gateway_serving(
    workspace: Any,
    *,
    model_name: str,
    inference_table_family: str,
) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
    """Expose the exhaustive serving snapshot for mutation fences and postflight."""

    return _serving_inventory(
        workspace,
        model_name=model_name,
        inference_table_family=inference_table_family,
    )


def inventory_gateway_tables(
    workspace: Any,
    *,
    catalog: str,
    inference_schema: str,
    inference_table_prefix: str,
    model_name: str,
    expected_owner: str,
    delta_version_resolver: Callable[[str], str],
) -> tuple[tuple[dict[str, str], ...], tuple[str, ...]]:
    """Expose exact table identities for owner-transfer convergence checks."""

    return _tables(
        workspace,
        catalog=catalog,
        inference_schema=inference_schema,
        inference_table_prefix=inference_table_prefix,
        model_name=model_name,
        runtime_application_id=expected_owner,
        delta_version_resolver=delta_version_resolver,
    )


def _assert_cross_model_source_uniqueness(
    workspace: Any,
    model_registry: Any,
    tracking_client: Any,
    *,
    model_family: str,
    model_name: str,
    logged_model_ids: tuple[str, ...],
    source_run_ids: tuple[str, ...],
    experiment_id: str,
) -> None:
    family_pattern = re.compile(rf"{re.escape(model_family)}_[0-9a-f]{{12}}\Z")
    target_ids = {
        *logged_model_ids,
        *source_run_ids,
        experiment_id,
    }
    for model in workspace.registered_models.list(include_browse=True):
        other_name = _field(model, "full_name")
        if other_name == model_name or family_pattern.fullmatch(other_name) is None:
            continue
        for raw in _search_versions(model_registry, model_name=other_name):
            version = authoritative_model_version(
                model_registry,
                raw,
                expected_model_name=other_name,
            )
            source = model_version_field(version, "source")
            logged_model_id, source_run_id, other_experiment_id = _source_identity(
                tracking_client,
                model_source=source,
            )
            if target_ids.intersection(
                {logged_model_id, source_run_id, other_experiment_id}
            ):
                raise RuntimeError(
                    "Gateway retirement source/run/experiment is shared by another model"
                )


def inventory_gateway_model_archive(
    workspace: Any,
    model_registry: Any,
    tracking_client: Any,
    *,
    model_name: str,
    runtime_application_id: str,
    model_family: str,
    experiment_base: str,
    catalog: str,
    inference_schema: str,
    inference_table_prefix: str,
    delta_version_resolver: Callable[[str], str],
) -> GatewayModelArchiveInventory:
    """Hydrate every mutable and retained identity before archival."""

    model = workspace.registered_models.get(model_name)
    if _field(model, "full_name") != model_name:
        raise RuntimeError("Gateway retirement registered-model identity drifted")
    model_owner = _field(model, "owner")
    if model_owner != runtime_application_id:
        raise RuntimeError("Gateway retirement target is not runtime-owned")
    versions, versions_sha256, experiment_id = _model_versions(
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
    experiment = _experiment(
        workspace,
        tracking_client,
        experiment_id=experiment_id,
        model_name=model_name,
        runtime_application_id=runtime_application_id,
        experiment_base=experiment_base,
    )
    tables, absent_tables = _tables(
        workspace,
        catalog=catalog,
        inference_schema=inference_schema,
        inference_table_prefix=inference_table_prefix,
        model_name=model_name,
        runtime_application_id=runtime_application_id,
        delta_version_resolver=delta_version_resolver,
    )
    serving_inventory, serving_references = _serving_inventory(
        workspace,
        model_name=model_name,
        inference_table_family=".".join(
            [
                catalog,
                inference_schema,
                f"{inference_table_prefix}_{model_name.rsplit('_', 1)[-1]}",
            ]
        ),
    )
    logged_model_ids = tuple(
        sorted({str(version["logged_model_id"]) for version in versions})
    )
    source_run_ids = tuple(sorted({str(version["run_id"]) for version in versions}))
    if len(logged_model_ids) != 1 or len(source_run_ids) != 1:
        raise RuntimeError("Gateway retirement requires one unique source/run identity")
    _assert_cross_model_source_uniqueness(
        workspace,
        model_registry,
        tracking_client,
        model_family=model_family,
        model_name=model_name,
        logged_model_ids=logged_model_ids,
        source_run_ids=source_run_ids,
        experiment_id=experiment_id,
    )
    return GatewayModelArchiveInventory(
        model_name=model_name,
        model_owner=model_owner,
        versions=versions,
        versions_sha256=versions_sha256,
        logged_model_ids=logged_model_ids,
        source_run_ids=source_run_ids,
        experiment_id=experiment_id,
        experiment_name=experiment[0],
        experiment_artifact_location=experiment[1],
        experiment_lifecycle_state=experiment[2],
        experiment_owner=experiment[3],
        experiment_tags=experiment[4],
        experiment_tags_sha256=record_sha256(experiment[4]),
        experiment_acl=experiment[5],
        experiment_acl_sha256=record_sha256(experiment[5]),
        inference_tables=tables,
        expected_absent_inference_tables=absent_tables,
        serving_inventory=serving_inventory,
        serving_inventory_sha256=record_sha256(serving_inventory),
        serving_references=serving_references,
        serving_references_sha256=record_sha256(serving_references),
    )
