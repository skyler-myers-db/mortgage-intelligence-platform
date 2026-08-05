#!/usr/bin/env python3
"""Prove the Supervisor proxy's effective Unity Catalog privilege boundary."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from databricks.sdk import WorkspaceClient
from tools.databricks.agent_runtime_uc_baseline import (
    _ACCOUNT_USERS_DIRECT,
    _CATALOG_INFORMATION_SCHEMA_TABLES,
    _MAX_INVENTORY_WORKERS,
    _SAMPLES_CATALOG_PRIVILEGES,
    _SAMPLES_INHERITED,
    _SYSTEM_AI_INHERITED,
    _SYSTEM_AI_MODELS,
    _SYSTEM_AI_MODELS_WITH_DIRECT_EXECUTE,
    ALLOWED_FUNCTIONS,
    ALLOWED_METASTORE_BASELINE,
    ControlPlaneForeignCatalogProof,
    authoritative_workspace_id,
    consume_issued_control_plane_foreign_catalog_proof,
)
from tools.databricks.agent_runtime_uc_inventory import (
    _assert_authenticated_runtime,
    _assert_mip_child_identity,
    _assert_mip_schema_identity,
    _assert_no_catalog_child_privileges,
    _assert_not_runtime_owned,
    _assert_privileges,
    _assert_registered_model_identity,
    _assert_system_owned,
    _catalog_name,
    _effective_privilege_sources,
    _exact_owner,
    _full_name,
    _schema_name,
    _strict_text,
    _text,
)
from tools.databricks.verify_agent_runtime_uc_grants import (
    _DATABRICKS_INTERNAL_CATALOG,
    _assert_samples_catalog_baseline,
    _assert_system_catalog_baseline,
)


def _catalog_inventory(workspace: Any) -> tuple[list[object], dict[str, object]]:
    items = list(workspace.catalogs.list(include_browse=True))
    by_name: dict[str, object] = {}
    for item in items:
        name = _strict_text(getattr(item, "name", None))
        if not name or name in by_name:
            raise RuntimeError("workspace catalog inventory has an invalid identity")
        by_name[name] = item
    return items, by_name


def _audit_other_catalog(
    workspace: Any,
    *,
    catalog: object,
    principal: str,
    owner_aliases: set[str],
    grant_audited_catalogs: set[str],
) -> None:
    name = _strict_text(getattr(catalog, "name", None))
    owner = _exact_owner(catalog, label=f"catalog {name}")
    catalog_type = _text(getattr(catalog, "catalog_type", None)).upper()
    isolation_mode = _text(getattr(catalog, "isolation_mode", None)).upper()
    if name == "system":
        if owner != "System user":
            raise RuntimeError("system catalog is not owned by Databricks System user")
        _assert_privileges(
            workspace,
            securable_type="catalog",
            full_name=name,
            principal=principal,
            expected={"USE_CATALOG"},
            expected_source_map={"USE_CATALOG": set(_ACCOUNT_USERS_DIRECT)},
        )
        _assert_system_catalog_baseline(
            workspace,
            principal=principal,
            runtime_owner_aliases=owner_aliases,
        )
        return
    if name == "samples":
        if owner != "System user":
            raise RuntimeError("samples catalog is not owned by Databricks System user")
        _assert_privileges(
            workspace,
            securable_type="catalog",
            full_name=name,
            principal=principal,
            expected=set(_SAMPLES_CATALOG_PRIVILEGES),
            expected_source_map={
                action: set(_ACCOUNT_USERS_DIRECT) for action in _SAMPLES_CATALOG_PRIVILEGES
            },
        )
        _assert_samples_catalog_baseline(workspace, principal=principal)
        return
    if name == _DATABRICKS_INTERNAL_CATALOG:
        if owner != "System user" or catalog_type != "INTERNAL_CATALOG" or isolation_mode != "OPEN":
            raise RuntimeError("Databricks internal catalog identity drifted")
        _assert_privileges(
            workspace,
            securable_type="catalog",
            full_name=name,
            principal=principal,
            expected=set(),
        )
        return
    if name in grant_audited_catalogs:
        return
    _assert_not_runtime_owned(
        catalog,
        owner_aliases=owner_aliases,
        label=f"catalog {name}",
    )
    if _effective_privilege_sources(
        workspace,
        securable_type="catalog",
        full_name=name,
        principal=principal,
    ):
        raise RuntimeError(f"agent-proxy has forbidden access on catalog {name}")
    _assert_no_catalog_child_privileges(
        workspace,
        catalog=name,
        catalog_type=catalog_type,
        catalog_owner=owner,
        principal=principal,
        owner_check=lambda item: _assert_not_runtime_owned(
            item,
            owner_aliases=owner_aliases,
            label=f"foreign UC object in {name}",
        ),
    )


def _audit_mip_catalog(
    workspace: Any,
    *,
    catalog: str,
    principal: str,
    owner_aliases: set[str],
) -> None:
    direct = {(principal, "", "")}
    schemas = list(workspace.schemas.list(catalog, include_browse=True))
    names = [_strict_text(getattr(item, "name", None)) for item in schemas]
    if not names or any(not name for name in names) or len(names) != len(set(names)):
        raise RuntimeError("MIP schema inventory has an invalid identity")
    if "gold" not in names:
        raise RuntimeError("MIP catalog is missing the reviewed gold schema")
    for schema in schemas:
        schema_name = _strict_text(getattr(schema, "name", None))
        schema_full_name = _full_name(schema, fallback=f"{catalog}.{schema_name}")
        if schema_full_name != f"{catalog}.{schema_name}":
            raise RuntimeError("MIP schema inventory returned an invalid parent identity")
        _assert_mip_schema_identity(
            schema,
            catalog_name=catalog,
            schema_name=schema_name,
            full_name=schema_full_name,
        )
        if schema_name == "information_schema":
            _assert_system_owned(schema, label=f"schema {schema_full_name}")
            expected_schema = {"USE_SCHEMA"}
            schema_sources: dict[str, set[tuple[str, str, str]]] | None = {
                "USE_SCHEMA": set(_ACCOUNT_USERS_DIRECT)
            }
        else:
            _assert_not_runtime_owned(
                schema,
                owner_aliases=owner_aliases,
                label=f"schema {schema_full_name}",
            )
            expected_schema = {"USE_SCHEMA"} if schema_name == "gold" else set()
            schema_sources = {"USE_SCHEMA": set(direct)} if expected_schema else None
        _assert_privileges(
            workspace,
            securable_type="schema",
            full_name=schema_full_name,
            principal=principal,
            expected=expected_schema,
            expected_source_map=schema_sources,
        )

        functions = list(workspace.functions.list(catalog, schema_name, include_browse=True))
        function_names: set[str] = set()
        for function in functions:
            name = _strict_text(getattr(function, "name", None))
            full_name = _full_name(function, fallback=f"{schema_full_name}.{name}")
            if not name or full_name != f"{schema_full_name}.{name}" or full_name in function_names:
                raise RuntimeError("MIP function inventory has an invalid identity")
            function_names.add(full_name)
            _assert_mip_child_identity(
                function,
                catalog_name=catalog,
                schema_name=schema_name,
                item_name=name,
                full_name=full_name,
                label="function",
            )
            _assert_not_runtime_owned(
                function,
                owner_aliases=owner_aliases,
                label=f"function {full_name}",
            )
            expected = {"EXECUTE"} if schema_name == "gold" and name in ALLOWED_FUNCTIONS else set()
            _assert_privileges(
                workspace,
                securable_type="function",
                full_name=full_name,
                principal=principal,
                expected=expected,
                expected_source_map=({"EXECUTE": set(direct)} if expected else None),
            )
        if schema_name == "gold" and {
            f"{catalog}.gold.{name}" for name in ALLOWED_FUNCTIONS
        } - function_names:
            raise RuntimeError("MIP gold schema is missing a reviewed proxy function")

        tables = list(
            workspace.tables.list(
                catalog,
                schema_name,
                include_browse=True,
                omit_columns=True,
                omit_properties=True,
            )
        )
        table_names: set[str] = set()
        for table in tables:
            name = _strict_text(getattr(table, "name", None))
            full_name = _full_name(table, fallback=f"{schema_full_name}.{name}")
            if not name or full_name != f"{schema_full_name}.{name}" or full_name in table_names:
                raise RuntimeError("MIP table inventory has an invalid identity")
            table_names.add(full_name)
            _assert_mip_child_identity(
                table,
                catalog_name=catalog,
                schema_name=schema_name,
                item_name=name,
                full_name=full_name,
                label="table",
            )
            if schema_name == "information_schema":
                _assert_system_owned(table, label=f"table {full_name}")
                expected = {"SELECT"} if name in _CATALOG_INFORMATION_SCHEMA_TABLES else set()
                sources = {"SELECT": set(_ACCOUNT_USERS_DIRECT)} if expected else None
            else:
                _assert_not_runtime_owned(
                    table,
                    owner_aliases=owner_aliases,
                    label=f"table {full_name}",
                )
                expected = set()
                sources = None
            _assert_privileges(
                workspace,
                securable_type="table",
                full_name=full_name,
                principal=principal,
                expected=expected,
                expected_source_map=sources,
            )

        volumes = list(workspace.volumes.list(catalog, schema_name, include_browse=True))
        volume_names: set[str] = set()
        for volume in volumes:
            name = _strict_text(getattr(volume, "name", None))
            full_name = _full_name(volume, fallback=f"{schema_full_name}.{name}")
            if not name or full_name != f"{schema_full_name}.{name}" or full_name in volume_names:
                raise RuntimeError("MIP volume inventory has an invalid identity")
            volume_names.add(full_name)
            _assert_mip_child_identity(
                volume,
                catalog_name=catalog,
                schema_name=schema_name,
                item_name=name,
                full_name=full_name,
                label="volume",
            )
            _assert_not_runtime_owned(
                volume,
                owner_aliases=owner_aliases,
                label=f"volume {full_name}",
            )
            _assert_privileges(
                workspace,
                securable_type="volume",
                full_name=full_name,
                principal=principal,
                expected=set(),
            )


def _audit_registered_models(
    workspace: Any,
    *,
    catalog: str,
    principal: str,
    owner_aliases: set[str],
) -> None:
    models = list(workspace.registered_models.list(include_browse=True))
    names: set[str] = set()
    for model in models:
        _assert_registered_model_identity(model)
        full_name = _full_name(model)
        if full_name in names:
            raise RuntimeError("registered-model inventory returned a duplicate identity")
        names.add(full_name)
        model_catalog = _catalog_name(model)
        model_schema = _schema_name(model)
        if model_catalog in {"system", "samples"}:
            _assert_system_owned(model, label=f"registered model {full_name}")
        else:
            _assert_not_runtime_owned(
                model,
                owner_aliases=owner_aliases,
                label=f"registered model {full_name}",
            )
        expected = (
            {"EXECUTE"} if full_name in _SYSTEM_AI_MODELS or model_catalog == "samples" else set()
        )
        sources = None
        if full_name in _SYSTEM_AI_MODELS:
            inherited = set(_SYSTEM_AI_INHERITED)
            if full_name in _SYSTEM_AI_MODELS_WITH_DIRECT_EXECUTE:
                inherited.update(_ACCOUNT_USERS_DIRECT)
            sources = {"EXECUTE": inherited}
        elif model_catalog == "samples":
            sources = {"EXECUTE": set(_SAMPLES_INHERITED)}
        _assert_privileges(
            workspace,
            securable_type="function",
            full_name=full_name,
            principal=principal,
            expected=expected,
            expected_source_map=sources,
        )
        if model_catalog == catalog and model_schema == "gold":
            raise RuntimeError("reviewed proxy functions must not be registered models")


def verify_effective_agent_proxy_uc_boundary(
    workspace: Any,
    *,
    application_id: str,
    catalog: str,
    foreign_control_plane_proof: ControlPlaneForeignCatalogProof | None = None,
) -> None:
    """Require only three reviewed functions plus immutable platform baselines."""

    principal = application_id.strip()
    catalog_name = catalog.strip()
    if not principal or not catalog_name:
        raise ValueError("agent-proxy application ID and catalog are required")
    owner_aliases = _assert_authenticated_runtime(
        workspace,
        application_id=principal,
    )
    metastore_id = _strict_text(getattr(workspace.metastores.current(), "metastore_id", None))
    if not metastore_id:
        raise RuntimeError("workspace has no current metastore identity")
    proof = None
    if foreign_control_plane_proof is not None:
        proof = consume_issued_control_plane_foreign_catalog_proof(foreign_control_plane_proof)
        if (
            proof.application_id,
            proof.catalog,
            proof.metastore_id,
            proof.workspace_id,
        ) != (
            principal,
            catalog_name,
            metastore_id,
            authoritative_workspace_id(workspace),
        ):
            raise RuntimeError(
                "foreign-catalog control-plane proof does not match the proxy boundary"
            )
    _assert_privileges(
        workspace,
        securable_type="metastore",
        full_name=metastore_id,
        principal=principal,
        expected=set(ALLOWED_METASTORE_BASELINE),
        expected_source_map={"USE_MARKETPLACE_ASSETS": set(_ACCOUNT_USERS_DIRECT)},
    )
    _assert_privileges(
        workspace,
        securable_type="catalog",
        full_name=catalog_name,
        principal=principal,
        expected={"USE_CATALOG"},
        expected_source_map={"USE_CATALOG": {(principal, "", "")}},
    )
    catalogs, by_name = _catalog_inventory(workspace)
    if catalog_name not in by_name:
        raise RuntimeError("configured MIP catalog is missing from workspace inventory")
    _assert_not_runtime_owned(
        by_name[catalog_name],
        owner_aliases=owner_aliases,
        label=f"catalog {catalog_name}",
    )
    denied = (
        {item.catalog for item in proof.binding_denied_catalogs} if proof is not None else set()
    )
    if denied.intersection(by_name):
        raise RuntimeError("binding-denied foreign catalogs became visible to agent-proxy")
    grant_audited = set(proof.grant_audited_catalogs) if proof is not None else set()
    others = [item for item in catalogs if getattr(item, "name", None) != catalog_name]
    if others:
        with ThreadPoolExecutor(
            max_workers=min(_MAX_INVENTORY_WORKERS, len(others)),
            thread_name_prefix="mip-proxy-uc",
        ) as executor:
            futures = [
                executor.submit(
                    _audit_other_catalog,
                    workspace,
                    catalog=item,
                    principal=principal,
                    owner_aliases=owner_aliases,
                    grant_audited_catalogs=grant_audited,
                )
                for item in others
            ]
            for future in as_completed(futures):
                future.result()
    _audit_mip_catalog(
        workspace,
        catalog=catalog_name,
        principal=principal,
        owner_aliases=owner_aliases,
    )
    _audit_registered_models(
        workspace,
        catalog=catalog_name,
        principal=principal,
        owner_aliases=owner_aliases,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--application-id", required=True)
    parser.add_argument("--catalog", default="mip")
    args = parser.parse_args(argv)
    verify_effective_agent_proxy_uc_boundary(
        WorkspaceClient(),
        application_id=args.application_id,
        catalog=args.catalog,
    )
    print("agent-proxy effective Unity Catalog boundary: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
