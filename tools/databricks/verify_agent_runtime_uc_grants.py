#!/usr/bin/env python3
"""Prove the agent runtime's effective privilege boundary across the MIP catalog."""

from __future__ import annotations

import argparse
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from mlflow import MlflowClient

from backend.agents.gateway_contract import DEFAULT_GATEWAY_AGENT_EXPERIMENT
from databricks.sdk import WorkspaceClient
from tools.databricks.agent_runtime_uc_baseline import (
    _ACCOUNT_USERS_DIRECT,
    _CATALOG_INFORMATION_SCHEMA_TABLES,
    _MAX_INVENTORY_WORKERS,
    _SAMPLES_CATALOG_PRIVILEGES,
    _SAMPLES_INHERITED,
    _SAMPLES_SCHEMA_PRIVILEGES,
    _SYSTEM_AI_FUNCTIONS,
    _SYSTEM_AI_INHERITED,
    _SYSTEM_AI_MODELS,
    _SYSTEM_AI_MODELS_WITH_DIRECT_EXECUTE,
    _SYSTEM_INFORMATION_SCHEMA_TABLES,
    _SYSTEM_SCHEMA_PRIVILEGES,
    ALLOWED_FUNCTIONS,
    ALLOWED_METASTORE_BASELINE,
    ControlPlaneForeignCatalogProof,
    authoritative_workspace_id,
    consume_issued_control_plane_foreign_catalog_proof,
)
from tools.databricks.agent_runtime_uc_inventory import (
    _assert_no_catalog_child_privileges,
    _assert_not_runtime_owned,
    _assert_privileges,
    _assert_system_owned,
    _catalog_name,
    _effective_privilege_sources,
    _full_name,
    _schema_name,
    _text,
)
from tools.databricks.gateway_uc_model_provenance import assert_gateway_model_provenance

_DATABRICKS_INTERNAL_CATALOG = "__databricks_internal"
_PLATFORM_RUNTIME_CATALOGS = frozenset({_DATABRICKS_INTERNAL_CATALOG, "samples", "system"})


def _reviewed_inference_table(name: str, *, family_prefix: str) -> bool:
    pattern = re.compile(
        rf"{re.escape(family_prefix)}_[0-9a-f]{{12}}_payload"
        rf"(?:_request_logs|_assessment_logs)?"
    )
    return pattern.fullmatch(name) is not None


def _reviewed_model_family(name: str, *, family_name: str) -> bool:
    return re.fullmatch(rf"{re.escape(family_name)}_[0-9a-f]{{12}}", name) is not None


def _assert_authenticated_runtime(workspace: Any, *, application_id: str) -> set[str]:
    """Bind the visibility inventory to the runtime identity whose access it proves."""

    caller = workspace.current_user.me()
    principals = {
        _text(getattr(caller, "user_name", None)),
        _text(getattr(caller, "application_id", None)),
    } - {""}
    if application_id not in principals:
        raise RuntimeError(
            "agent-runtime UC inventory is not authenticated as the expected runtime identity"
        )
    caller_id = _text(getattr(caller, "id", None))
    if not caller_id:
        raise RuntimeError("agent-runtime identity has no immutable SCIM id")
    groups = getattr(caller, "groups", None)
    if groups is None:
        raise RuntimeError("agent-runtime identity omitted its effective groups collection")
    owner_aliases = {caller_id.casefold(), *(value.casefold() for value in principals)}
    for group in groups:
        group_id = _text(getattr(group, "value", None))
        display = _text(getattr(group, "display", None))
        if not group_id or not display:
            raise RuntimeError("agent-runtime effective group identity is incomplete")
        owner_aliases.update({group_id.casefold(), display.casefold()})
    return owner_aliases


def _assert_samples_catalog_baseline(workspace: Any, *, principal: str) -> None:
    """Require the exact Databricks-managed samples inheritance contract."""

    for schema in workspace.schemas.list("samples", include_browse=True):
        _assert_system_owned(schema, label="samples schema")
        schema_name = _text(getattr(schema, "name", None))
        schema_full_name = _full_name(schema, fallback=f"samples.{schema_name}")
        schema_sources = {action: set(_SAMPLES_INHERITED) for action in _SAMPLES_SCHEMA_PRIVILEGES}
        if schema_name == "information_schema":
            schema_sources["USE_SCHEMA"] = {
                *_SAMPLES_INHERITED,
                *_ACCOUNT_USERS_DIRECT,
            }
        _assert_privileges(
            workspace,
            securable_type="schema",
            full_name=schema_full_name,
            principal=principal,
            expected=set(_SAMPLES_SCHEMA_PRIVILEGES),
            expected_source_map=schema_sources,
        )
        for function in workspace.functions.list("samples", schema_name, include_browse=True):
            _assert_system_owned(function, label="samples function")
            function_name = _text(getattr(function, "name", None))
            _assert_privileges(
                workspace,
                securable_type="function",
                full_name=_full_name(
                    function,
                    fallback=f"{schema_full_name}.{function_name}",
                ),
                principal=principal,
                expected={"EXECUTE"},
                expected_source_map={"EXECUTE": set(_SAMPLES_INHERITED)},
            )
        for table in workspace.tables.list(
            "samples",
            schema_name,
            include_browse=True,
            omit_columns=True,
            omit_properties=True,
        ):
            _assert_system_owned(table, label="samples table")
            table_name = _text(getattr(table, "name", None))
            table_sources = set(_SAMPLES_INHERITED)
            if (
                schema_name == "information_schema"
                and table_name in _CATALOG_INFORMATION_SCHEMA_TABLES
            ):
                table_sources.update(_ACCOUNT_USERS_DIRECT)
            _assert_privileges(
                workspace,
                securable_type="table",
                full_name=_full_name(
                    table,
                    fallback=f"{schema_full_name}.{table_name}",
                ),
                principal=principal,
                expected={"SELECT"},
                expected_source_map={"SELECT": table_sources},
            )
        for volume in workspace.volumes.list("samples", schema_name, include_browse=True):
            _assert_system_owned(volume, label="samples volume")
            volume_name = _text(getattr(volume, "name", None))
            _assert_privileges(
                workspace,
                securable_type="volume",
                full_name=_full_name(
                    volume,
                    fallback=f"{schema_full_name}.{volume_name}",
                ),
                principal=principal,
                expected={"READ_VOLUME"},
                expected_source_map={"READ_VOLUME": set(_SAMPLES_INHERITED)},
            )


def _assert_system_catalog_baseline(
    workspace: Any,
    *,
    principal: str,
    runtime_owner_aliases: set[str],
) -> None:
    """Allow only the reviewed immutable Databricks account-users system baseline."""

    for schema in workspace.schemas.list("system", include_browse=True):
        schema_name = _text(getattr(schema, "name", None))
        if not schema_name:
            raise RuntimeError("system schema inventory returned an empty name")
        schema_owner = _text(getattr(schema, "owner", None))
        if schema_name == "data_quality_monitoring":
            if not schema_owner or schema_owner.casefold() in runtime_owner_aliases:
                raise RuntimeError("system.data_quality_monitoring has an invalid platform owner")
        else:
            _assert_system_owned(schema, label="system schema")
        schema_full_name = _full_name(schema, fallback=f"system.{schema_name}")

        def assert_child_owner(
            item: object,
            *,
            label: str,
            system_schema_name: str = schema_name,
            system_schema_owner: str = schema_owner,
        ) -> None:
            if system_schema_name != "data_quality_monitoring":
                _assert_system_owned(item, label=label)
                return
            owner = _text(getattr(item, "owner", None))
            if owner != system_schema_owner:
                raise RuntimeError(
                    "system.data_quality_monitoring child owner drifted from its "
                    "platform schema owner"
                )

        _assert_privileges(
            workspace,
            securable_type="schema",
            full_name=schema_full_name,
            principal=principal,
            expected=set(_SYSTEM_SCHEMA_PRIVILEGES.get(schema_name, set())),
            expected_source_map={
                action: set(_ACCOUNT_USERS_DIRECT)
                for action in _SYSTEM_SCHEMA_PRIVILEGES.get(schema_name, set())
            },
        )
        for function in workspace.functions.list(
            "system",
            schema_name,
            include_browse=True,
        ):
            assert_child_owner(function, label="system function")
            function_name = _text(getattr(function, "name", None))
            _assert_privileges(
                workspace,
                securable_type="function",
                full_name=_full_name(
                    function,
                    fallback=f"{schema_full_name}.{function_name}",
                ),
                principal=principal,
                expected=(
                    {"EXECUTE"}
                    if schema_name == "ai" and function_name in _SYSTEM_AI_FUNCTIONS
                    else set()
                ),
                expected_source_map=(
                    {"EXECUTE": set(_SYSTEM_AI_INHERITED)}
                    if schema_name == "ai" and function_name in _SYSTEM_AI_FUNCTIONS
                    else None
                ),
            )
        for table in workspace.tables.list(
            "system",
            schema_name,
            include_browse=True,
            omit_columns=True,
            omit_properties=True,
        ):
            assert_child_owner(table, label="system table")
            table_name = _text(getattr(table, "name", None))
            _assert_privileges(
                workspace,
                securable_type="table",
                full_name=_full_name(
                    table,
                    fallback=f"{schema_full_name}.{table_name}",
                ),
                principal=principal,
                expected=(
                    {"SELECT"}
                    if schema_name == "information_schema"
                    and table_name in _SYSTEM_INFORMATION_SCHEMA_TABLES
                    else set()
                ),
                expected_source_map=(
                    {"SELECT": set(_ACCOUNT_USERS_DIRECT)}
                    if schema_name == "information_schema"
                    and table_name in _SYSTEM_INFORMATION_SCHEMA_TABLES
                    else None
                ),
            )
        for volume in workspace.volumes.list("system", schema_name, include_browse=True):
            assert_child_owner(volume, label="system volume")
            volume_name = _text(getattr(volume, "name", None))
            _assert_privileges(
                workspace,
                securable_type="volume",
                full_name=_full_name(
                    volume,
                    fallback=f"{schema_full_name}.{volume_name}",
                ),
                principal=principal,
                expected=set(),
            )


def verify_effective_uc_boundary(
    workspace: Any,
    *,
    application_id: str,
    supervisor_id: str,
    supervisor_endpoint_id: str,
    catalog: str,
    gateway_model: str,
    inference_table_prefix: str,
    gateway_model_family: str | None = None,
    gateway_experiment_base: str = DEFAULT_GATEWAY_AGENT_EXPERIMENT,
    genie_space_id: str,
    model_registry: Any | None = None,
    foreign_control_plane_proof: ControlPlaneForeignCatalogProof | None = None,
) -> None:
    """Require only reviewed functions plus runtime-owned Gateway artifacts in MIP."""

    principal = application_id.strip()
    supervisor_identity = supervisor_id.strip()
    supervisor_endpoint_identity = supervisor_endpoint_id.strip()
    catalog_name = catalog.strip()
    model_name = gateway_model.strip()
    model_family = (gateway_model_family or model_name).strip()
    experiment_base = gateway_experiment_base.strip()
    genie_id = genie_space_id.strip()
    table_prefix = inference_table_prefix.strip()
    core_required = (
        principal,
        supervisor_identity,
        supervisor_endpoint_identity,
        catalog_name,
        model_name,
    )
    resource_required = (model_family, experiment_base, genie_id, table_prefix)
    if not all(core_required + resource_required):
        raise ValueError("application ID, Supervisor ID, catalog, model, and table prefix required")
    runtime_owner_aliases = _assert_authenticated_runtime(
        workspace,
        application_id=principal,
    )

    metastore_id = _text(getattr(workspace.metastores.current(), "metastore_id", None))
    if not metastore_id:
        raise RuntimeError("workspace has no current metastore identity")
    consumed_control_plane_proof = None
    if foreign_control_plane_proof is not None:
        consumed_control_plane_proof = consume_issued_control_plane_foreign_catalog_proof(
            foreign_control_plane_proof
        )
        workspace_id = authoritative_workspace_id(workspace)
        proof_identity = (
            consumed_control_plane_proof.application_id,
            consumed_control_plane_proof.catalog,
            consumed_control_plane_proof.metastore_id,
            consumed_control_plane_proof.workspace_id,
        )
        runtime_identity = (principal, catalog_name, metastore_id, workspace_id)
        if proof_identity != runtime_identity:
            raise RuntimeError(
                "foreign-catalog control-plane proof does not match the runtime boundary"
            )
    _assert_privileges(
        workspace,
        securable_type="metastore",
        full_name=metastore_id,
        principal=principal,
        expected=set(ALLOWED_METASTORE_BASELINE),
        expected_source_map={"USE_MARKETPLACE_ASSETS": set(_ACCOUNT_USERS_DIRECT)},
    )

    runtime_direct = {(principal, "", "")}
    _assert_privileges(
        workspace,
        securable_type="catalog",
        full_name=catalog_name,
        principal=principal,
        expected={"USE_CATALOG"},
        expected_source_map={"USE_CATALOG": set(runtime_direct)},
    )
    visible_catalogs = list(workspace.catalogs.list(include_browse=True))
    visible_catalog_names = {_text(getattr(item, "name", None)) for item in visible_catalogs}
    visible_catalog_owners = {
        _text(getattr(item, "name", None)): _text(getattr(item, "owner", None))
        for item in visible_catalogs
    }
    visible_catalog_types = {
        _text(getattr(item, "name", None)): _text(getattr(item, "catalog_type", None)).upper()
        for item in visible_catalogs
    }
    visible_catalog_modes = {
        _text(getattr(item, "name", None)): _text(getattr(item, "isolation_mode", None)).upper()
        for item in visible_catalogs
    }
    if catalog_name not in visible_catalog_names:
        raise RuntimeError("configured MIP catalog is missing from workspace inventory")
    mip_catalog_object = next(
        item for item in visible_catalogs if _text(getattr(item, "name", None)) == catalog_name
    )
    _assert_not_runtime_owned(
        mip_catalog_object,
        owner_aliases=runtime_owner_aliases,
        label=f"catalog {catalog_name}",
    )
    if "" in visible_catalog_names:
        raise RuntimeError("workspace catalog inventory returned an empty name")
    other_catalogs = sorted(visible_catalog_names - {catalog_name, ""})

    def inspect_other_catalog(other_catalog: str) -> None:
        is_system = other_catalog == "system"
        if is_system:
            if visible_catalog_owners.get("system") != "System user":
                raise RuntimeError("system catalog is not owned by Databricks System user")
            _assert_privileges(
                workspace,
                securable_type="catalog",
                full_name=other_catalog,
                principal=principal,
                expected={"USE_CATALOG"},
                expected_source_map={"USE_CATALOG": set(_ACCOUNT_USERS_DIRECT)},
            )
            _assert_system_catalog_baseline(
                workspace,
                principal=principal,
                runtime_owner_aliases=runtime_owner_aliases,
            )
        elif other_catalog == "samples":
            if visible_catalog_owners.get("samples") != "System user":
                raise RuntimeError("samples catalog is not owned by Databricks System user")
            _assert_privileges(
                workspace,
                securable_type="catalog",
                full_name=other_catalog,
                principal=principal,
                expected=set(_SAMPLES_CATALOG_PRIVILEGES),
                expected_source_map={
                    action: set(_ACCOUNT_USERS_DIRECT) for action in _SAMPLES_CATALOG_PRIVILEGES
                },
            )
            _assert_samples_catalog_baseline(workspace, principal=principal)
        elif other_catalog == _DATABRICKS_INTERNAL_CATALOG:
            if (
                visible_catalog_owners.get(other_catalog) != "System user"
                or visible_catalog_types.get(other_catalog) != "INTERNAL_CATALOG"
                or visible_catalog_modes.get(other_catalog) != "OPEN"
            ):
                raise RuntimeError(
                    "Databricks internal catalog does not match the fixed platform identity"
                )
            _assert_privileges(
                workspace,
                securable_type="catalog",
                full_name=other_catalog,
                principal=principal,
                expected=set(),
            )
        elif (
            consumed_control_plane_proof is not None
            and other_catalog in consumed_control_plane_proof.audited_catalogs
        ):
            return
        else:
            catalog_sources = _effective_privilege_sources(
                workspace,
                securable_type="catalog",
                full_name=other_catalog,
                principal=principal,
            )
            if catalog_sources:
                raise RuntimeError(
                    f"agent-runtime has forbidden access on catalog {other_catalog}: "
                    f"{catalog_sources}"
                )
            _assert_no_catalog_child_privileges(
                workspace,
                catalog=other_catalog,
                principal=principal,
            )

    if other_catalogs:
        with ThreadPoolExecutor(
            max_workers=min(_MAX_INVENTORY_WORKERS, len(other_catalogs)),
            thread_name_prefix="mip-uc-inventory",
        ) as executor:
            futures = [executor.submit(inspect_other_catalog, item) for item in other_catalogs]
            for future in as_completed(futures):
                future.result()

    all_registered_models = list(workspace.registered_models.list(include_browse=True))
    if any(not _catalog_name(model) for model in all_registered_models):
        raise RuntimeError("workspace registered-model inventory lacks a catalog name")
    registered_models = [
        model for model in all_registered_models if _catalog_name(model) == catalog_name
    ]
    other_registered_models = [
        model for model in all_registered_models if _catalog_name(model) != catalog_name
    ]
    for model in registered_models:
        if not _reviewed_model_family(_full_name(model), family_name=model_family):
            _assert_not_runtime_owned(
                model,
                owner_aliases=runtime_owner_aliases,
                label=f"registered model {_full_name(model)}",
            )
    unexpected_system_models = sorted(
        _full_name(model)
        for model in other_registered_models
        if _catalog_name(model) == "system"
        and _schema_name(model) == "ai"
        and _full_name(model) not in _SYSTEM_AI_MODELS
    )
    if unexpected_system_models:
        raise RuntimeError(
            "unreviewed system.ai registered models are visible: "
            + ", ".join(unexpected_system_models)
        )
    invalid_system_owners = sorted(
        _full_name(model)
        for model in other_registered_models
        if _full_name(model) in _SYSTEM_AI_MODELS
        and _text(getattr(model, "owner", None)) != "System user"
    )
    if invalid_system_owners:
        raise RuntimeError(
            "reviewed system.ai models are not owned by System user: "
            + ", ".join(invalid_system_owners)
        )
    invalid_platform_model_owners = sorted(
        _full_name(model)
        for model in other_registered_models
        if _catalog_name(model) in _PLATFORM_RUNTIME_CATALOGS
        and _text(getattr(model, "owner", None)) != "System user"
    )
    if invalid_platform_model_owners:
        raise RuntimeError(
            "platform registered models are not owned by System user: "
            + ", ".join(invalid_platform_model_owners)
        )
    models_requiring_runtime_audit = [
        model
        for model in other_registered_models
        if (
            consumed_control_plane_proof is None
            or _catalog_name(model) in _PLATFORM_RUNTIME_CATALOGS
            or _catalog_name(model) not in consumed_control_plane_proof.audited_catalogs
        )
    ]
    if models_requiring_runtime_audit:
        with ThreadPoolExecutor(
            max_workers=min(_MAX_INVENTORY_WORKERS, len(models_requiring_runtime_audit)),
            thread_name_prefix="mip-uc-models",
        ) as executor:
            futures = [
                executor.submit(
                    _assert_privileges,
                    workspace,
                    securable_type="function",
                    full_name=_full_name(model),
                    principal=principal,
                    expected=(
                        {"EXECUTE"}
                        if (
                            (_catalog_name(model) == "system" and _schema_name(model) == "ai")
                            or _catalog_name(model) == "samples"
                        )
                        else set()
                    ),
                    expected_source_map=(
                        {
                            "EXECUTE": {
                                *_SYSTEM_AI_INHERITED,
                                *(
                                    _ACCOUNT_USERS_DIRECT
                                    if _full_name(model) in _SYSTEM_AI_MODELS_WITH_DIRECT_EXECUTE
                                    else set()
                                ),
                            }
                        }
                        if _catalog_name(model) == "system" and _schema_name(model) == "ai"
                        else (
                            {"EXECUTE": set(_SAMPLES_INHERITED)}
                            if _catalog_name(model) == "samples"
                            else None
                        )
                    ),
                )
                for model in models_requiring_runtime_audit
            ]
            for future in as_completed(futures):
                future.result()

    schemas = list(workspace.schemas.list(catalog_name, include_browse=True))
    schema_names = {_text(getattr(schema, "name", None)) for schema in schemas}
    if not {"gold", "audit"}.issubset(schema_names):
        raise RuntimeError("MIP catalog is missing the reviewed agent schemas")
    for schema in schemas:
        schema_name = _text(getattr(schema, "name", None))
        if not schema_name:
            raise RuntimeError("MIP schema inventory returned an empty name")
        schema_full_name = _full_name(schema, fallback=f"{catalog_name}.{schema_name}")
        if schema_name == "information_schema":
            _assert_system_owned(schema, label=f"schema {schema_full_name}")
        else:
            _assert_not_runtime_owned(
                schema,
                owner_aliases=runtime_owner_aliases,
                label=f"schema {schema_full_name}",
            )
        if schema_name in {"gold", "audit"}:
            expected_schema = {"USE_SCHEMA"}
            schema_sources = {"USE_SCHEMA": set(runtime_direct)}
        elif schema_name == "information_schema":
            expected_schema = {"USE_SCHEMA"}
            schema_sources = {"USE_SCHEMA": set(_ACCOUNT_USERS_DIRECT)}
        else:
            expected_schema = set()
            schema_sources = None
        _assert_privileges(
            workspace,
            securable_type="schema",
            full_name=schema_full_name,
            principal=principal,
            expected=expected_schema,
            expected_source_map=schema_sources,
        )

        for function in workspace.functions.list(
            catalog_name,
            schema_name,
            include_browse=True,
        ):
            function_name = _text(getattr(function, "name", None))
            if not function_name:
                raise RuntimeError("MIP function inventory returned an empty name")
            function_full_name = _full_name(
                function,
                fallback=f"{schema_full_name}.{function_name}",
            )
            _assert_not_runtime_owned(
                function,
                owner_aliases=runtime_owner_aliases,
                label=f"function {function_full_name}",
            )
            expected = (
                {"EXECUTE"}
                if schema_name == "gold" and function_name in ALLOWED_FUNCTIONS
                else set()
            )
            _assert_privileges(
                workspace,
                securable_type="function",
                full_name=function_full_name,
                principal=principal,
                expected=expected,
                expected_source_map=({"EXECUTE": set(runtime_direct)} if expected else None),
            )

        for table in workspace.tables.list(
            catalog_name,
            schema_name,
            include_browse=True,
            omit_columns=True,
            omit_properties=True,
        ):
            table_name = _text(getattr(table, "name", None))
            if not table_name:
                raise RuntimeError("MIP table inventory returned an empty name")
            table_full_name = _full_name(
                table,
                fallback=f"{schema_full_name}.{table_name}",
            )
            if schema_name == "information_schema":
                _assert_system_owned(table, label=f"table {table_full_name}")
                expected = {"SELECT"} if table_name in _CATALOG_INFORMATION_SCHEMA_TABLES else set()
                _assert_privileges(
                    workspace,
                    securable_type="table",
                    full_name=table_full_name,
                    principal=principal,
                    expected=expected,
                    expected_source_map=(
                        {"SELECT": set(_ACCOUNT_USERS_DIRECT)} if expected else None
                    ),
                )
                continue
            if schema_name == "audit" and _reviewed_inference_table(
                table_name,
                family_prefix=table_prefix,
            ):
                actual_sources = _effective_privilege_sources(
                    workspace,
                    securable_type="table",
                    full_name=table_full_name,
                    principal=principal,
                )
                if (
                    not actual_sources
                    or any(sources != runtime_direct for sources in actual_sources.values())
                    or _text(getattr(table, "owner", None)) != principal
                ):
                    raise RuntimeError(
                        f"agent-runtime inference table {table_name} lacks exact direct "
                        "runtime ownership"
                    )
                continue
            _assert_not_runtime_owned(
                table,
                owner_aliases=runtime_owner_aliases,
                label=f"table {table_full_name}",
            )
            _assert_privileges(
                workspace,
                securable_type="table",
                full_name=table_full_name,
                principal=principal,
                expected=set(),
            )

        for volume in workspace.volumes.list(catalog_name, schema_name, include_browse=True):
            volume_name = _text(getattr(volume, "name", None))
            if not volume_name:
                raise RuntimeError("MIP volume inventory returned an empty name")
            volume_full_name = _full_name(
                volume,
                fallback=f"{schema_full_name}.{volume_name}",
            )
            _assert_not_runtime_owned(
                volume,
                owner_aliases=runtime_owner_aliases,
                label=f"volume {volume_full_name}",
            )
            _assert_privileges(
                workspace,
                securable_type="volume",
                full_name=volume_full_name,
                principal=principal,
                expected=set(),
            )

    if model_name not in {_full_name(model) for model in registered_models}:
        raise RuntimeError("reviewed Gateway registered model is missing from the MIP catalog")
    registry = model_registry or MlflowClient(
        tracking_uri="databricks",
        registry_uri="databricks-uc",
    )
    reviewed_model_suffixes: set[str] = set()
    for model in registered_models:
        full_name = _full_name(model)
        actual_sources = _effective_privilege_sources(
            workspace,
            securable_type="function",
            full_name=full_name,
            principal=principal,
        )
        if not _reviewed_model_family(full_name, family_name=model_family):
            if actual_sources:
                raise RuntimeError(
                    f"agent-runtime has unexpected registered-model privileges on {full_name}: "
                    + ", ".join(sorted(actual_sources))
                )
            continue
        if (
            not actual_sources
            or any(sources != runtime_direct for sources in actual_sources.values())
            or _text(getattr(model, "owner", None)) != principal
        ):
            raise RuntimeError(
                f"agent-runtime Gateway model {full_name} lacks exact direct runtime ownership"
            )
        assert_gateway_model_provenance(
            model_registry=registry,
            full_name=full_name,
            model_family=model_family,
            experiment_base=experiment_base,
            supervisor_id=supervisor_identity,
            supervisor_endpoint_id=supervisor_endpoint_identity,
            runtime_application_id=principal,
            catalog=catalog_name,
            genie_space_id=genie_id,
            inference_schema="audit",
            inference_table_prefix=table_prefix,
            candidate_model=model_name,
        )
        reviewed_model_suffixes.add(full_name.rsplit("_", 1)[-1])
    inference_suffixes = {
        match.group(1)
        for schema in schemas
        if _text(getattr(schema, "name", None)) == "audit"
        for table in workspace.tables.list(
            catalog_name,
            "audit",
            include_browse=True,
            omit_columns=True,
            omit_properties=True,
        )
        if (
            match := re.fullmatch(
                rf"{re.escape(table_prefix)}_([0-9a-f]{{12}})_payload"
                rf"(?:_request_logs|_assessment_logs)?",
                _text(getattr(table, "name", None)),
            )
        )
    }
    if not inference_suffixes.issubset(reviewed_model_suffixes):
        raise RuntimeError("inference-table family is not backed by reviewed Gateway models")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--application-id", required=True)
    parser.add_argument("--supervisor-id", required=True)
    parser.add_argument("--supervisor-endpoint-id", required=True)
    parser.add_argument("--catalog", default="mip")
    parser.add_argument("--gateway-model", required=True)
    parser.add_argument("--gateway-model-family")
    parser.add_argument("--gateway-experiment-base", default=DEFAULT_GATEWAY_AGENT_EXPERIMENT)
    parser.add_argument("--genie-space-id", required=True)
    parser.add_argument("--inference-table-prefix", required=True)
    args = parser.parse_args(argv)
    verify_effective_uc_boundary(
        WorkspaceClient(),
        application_id=args.application_id,
        supervisor_id=args.supervisor_id,
        supervisor_endpoint_id=args.supervisor_endpoint_id,
        catalog=args.catalog,
        gateway_model=args.gateway_model,
        gateway_model_family=args.gateway_model_family,
        gateway_experiment_base=args.gateway_experiment_base,
        genie_space_id=args.genie_space_id,
        inference_table_prefix=args.inference_table_prefix,
    )
    print("agent-runtime effective MIP catalog privilege boundary: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
