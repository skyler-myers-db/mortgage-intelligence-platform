"""OpenAPI wire-contract compatibility gates.

The storage layer already has DDL contract tests. This file protects the
public API surface in two directions:

- Breaking drift (removals, tighter required sets, enum narrowing) fails
  ``test_openapi_wire_contract_has_no_breaking_changes``.
- Additive drift (new paths, methods, schemas, or contract changes not yet
  captured in the committed baseline) fails
  ``test_openapi_baseline_captures_current_api_surface``. Additive changes
  are still allowed — they just must be snapshotted: run
  ``python tools/regen_openapi_baseline.py`` and commit the result.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, get_origin

from fastapi.routing import APIRoute

from backend.main import API_VERSION, app
from backend.version import api_version

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "tests" / "fixtures" / "openapi_baseline.json"
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}
REGEN_HINT = (
    "The committed OpenAPI baseline is stale. "
    "Run `python tools/regen_openapi_baseline.py` and commit the result."
)


def _schema() -> dict[str, Any]:
    return app.openapi()


def _canonical_paths(schema: dict[str, Any]) -> dict[str, Any]:
    prefix = f"/api/{API_VERSION}/"
    return {
        path: methods
        for path, methods in schema.get("paths", {}).items()
        if path.startswith(prefix)
    }


def _schemas(schema: dict[str, Any]) -> dict[str, Any]:
    return schema.get("components", {}).get("schemas", {})


def _operation_contract(operation: dict[str, Any]) -> dict[str, Any]:
    """Return the operation contract fields whose narrowing is breaking."""

    return _without_schema_titles({
        "parameters": operation.get("parameters", []),
        "requestBody": operation.get("requestBody"),
        "responses": operation.get("responses", {}),
    })


def _operation_breaking_drift(
    baseline_operation: dict[str, Any],
    current_operation: dict[str, Any],
) -> bool:
    """Return TRUE when an operation removed or narrowed an existing contract.

    Additive optional query parameters are intentionally non-breaking. The
    baseline test's schema checks below still catch removed response fields,
    newly-required fields, and enum narrowing.
    """

    baseline_contract = _operation_contract(baseline_operation)
    current_contract = _operation_contract(current_operation)
    if baseline_contract.get("requestBody") != current_contract.get("requestBody"):
        return True
    current_responses = current_contract.get("responses", {})
    for status_code, baseline_response in baseline_contract.get("responses", {}).items():
        if current_responses.get(status_code) != baseline_response:
            return True

    current_params = {
        (param.get("name"), param.get("in")): param
        for param in current_contract.get("parameters", [])
    }
    baseline_param_keys = {
        (param.get("name"), param.get("in"))
        for param in baseline_contract.get("parameters", [])
    }
    for baseline_param in baseline_contract.get("parameters", []):
        key = (baseline_param.get("name"), baseline_param.get("in"))
        if current_params.get(key) != baseline_param:
            return True
    for key, current_param in current_params.items():
        if key not in baseline_param_keys and current_param.get("required"):
            return True
    return False


def _without_schema_titles(node: Any) -> Any:
    if isinstance(node, dict):
        return {
            key: _without_schema_titles(value)
            for key, value in node.items()
            if key != "title"
        }
    if isinstance(node, list):
        return [_without_schema_titles(item) for item in node]
    return node


def _enum_values(node: Any) -> set[str]:
    if isinstance(node, dict):
        values: set[str] = set()
        enum = node.get("enum")
        if isinstance(enum, list):
            values.update(str(value) for value in enum)
        for value in node.values():
            values.update(_enum_values(value))
        return values
    if isinstance(node, list):
        values: set[str] = set()
        for item in node:
            values.update(_enum_values(item))
        return values
    return set()


def test_openapi_info_version_matches_package_version() -> None:
    assert app.version == api_version()
    assert _schema()["info"]["version"] == api_version()


def test_openapi_has_versioned_primary_paths_and_deprecated_compat_aliases() -> None:
    schema = _schema()
    paths = schema["paths"]
    assert "/api/v1/health" in paths
    assert "/api/health" in paths

    for path, methods in paths.items():
        for method, operation in methods.items():
            if method not in HTTP_METHODS:
                continue
            if path.startswith(f"/api/{API_VERSION}/"):
                assert operation.get("deprecated") is not True, f"{method.upper()} {path}"
            elif path.startswith("/api/"):
                assert operation.get("deprecated") is True, f"{method.upper()} {path}"


def test_versioned_and_compat_operations_have_same_contract() -> None:
    schema = _schema()
    paths = schema["paths"]
    mismatches: list[str] = []
    for path, methods in paths.items():
        if not path.startswith(f"/api/{API_VERSION}/"):
            continue
        compat_path = "/api" + path[len(f"/api/{API_VERSION}") :]
        if compat_path not in paths:
            mismatches.append(f"{path}: missing compatibility alias")
            continue
        for method in sorted(set(methods) & HTTP_METHODS):
            if method not in paths[compat_path]:
                mismatches.append(f"{method.upper()} {path}: alias method missing")
                continue
            if _operation_contract(methods[method]) != _operation_contract(paths[compat_path][method]):
                mismatches.append(f"{method.upper()} {path}: alias contract drift")
    assert mismatches == []


def test_canonical_api_routes_do_not_use_generic_dict_response_models() -> None:
    violations: list[str] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if not route.include_in_schema or not route.path.startswith(f"/api/{API_VERSION}/"):
            continue
        model = route.response_model
        if model is None or model is dict or get_origin(model) is dict:
            methods = ",".join(sorted((route.methods or set()) - {"HEAD", "OPTIONS"}))
            violations.append(f"{methods} {route.path}: {model}")
    assert violations == []


def _api_paths(schema: dict[str, Any]) -> dict[str, Any]:
    """All /api-prefixed paths: canonical /api/v1/* plus /api/* compat aliases.

    Every /api route mounts unconditionally via ``API_ROUTERS`` in
    ``backend.main``. The SPA catch-all ``/{full_path}`` is deliberately
    excluded: it mounts only when ``frontend/dist/index.html`` exists, so its
    presence varies between environments and it carries no API contract.
    """

    return {
        path: methods
        for path, methods in schema.get("paths", {}).items()
        if path.startswith("/api/")
    }


def test_openapi_baseline_captures_current_api_surface() -> None:
    """Fail loud on additive baseline drift.

    The breaking-change test below is additive-tolerant by design, which let
    ~38 real endpoints accrue between 2026-06-25 and 2026-07-11 without a
    single test failure. This test closes that hole by requiring the committed
    baseline to exactly match the live /api surface in BOTH directions:
    paths, methods, operation contracts (title-stripped; docstrings excluded),
    and component schemas. Additive changes remain allowed — they just must be
    snapshotted in the same commit via ``tools/regen_openapi_baseline.py``.
    """

    current = _schema()
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))

    current_paths = _api_paths(current)
    baseline_paths = _api_paths(baseline)

    missing_paths = sorted(set(current_paths) - set(baseline_paths))
    assert missing_paths == [], f"Paths missing from baseline: {missing_paths}. {REGEN_HINT}"

    stale_paths = sorted(set(baseline_paths) - set(current_paths))
    assert stale_paths == [], f"Baseline paths no longer live: {stale_paths}. {REGEN_HINT}"

    method_drift: list[str] = []
    contract_drift: list[str] = []
    for path, methods in sorted(current_paths.items()):
        baseline_methods = baseline_paths[path]
        for method in sorted((set(methods) | set(baseline_methods)) & HTTP_METHODS):
            if method not in baseline_methods or method not in methods:
                method_drift.append(f"{method.upper()} {path}")
            elif _operation_contract(methods[method]) != _operation_contract(
                baseline_methods[method]
            ):
                contract_drift.append(f"{method.upper()} {path}")
    assert method_drift == [], f"Method drift vs baseline: {method_drift}. {REGEN_HINT}"
    assert contract_drift == [], f"Operation contract drift vs baseline: {contract_drift}. {REGEN_HINT}"

    current_schemas = _schemas(current)
    baseline_schemas = _schemas(baseline)
    missing_schemas = sorted(set(current_schemas) - set(baseline_schemas))
    assert missing_schemas == [], f"Schemas missing from baseline: {missing_schemas}. {REGEN_HINT}"

    stale_schemas = sorted(set(baseline_schemas) - set(current_schemas))
    assert stale_schemas == [], f"Baseline schemas no longer live: {stale_schemas}. {REGEN_HINT}"

    schema_drift = sorted(
        name
        for name, current_schema in current_schemas.items()
        if _without_schema_titles(current_schema)
        != _without_schema_titles(baseline_schemas[name])
    )
    assert schema_drift == [], f"Schema definition drift vs baseline: {schema_drift}. {REGEN_HINT}"


def test_openapi_wire_contract_has_no_breaking_changes() -> None:
    current = _schema()
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))

    current_paths = _canonical_paths(current)
    baseline_paths = _canonical_paths(baseline)
    removed_paths = sorted(set(baseline_paths) - set(current_paths))
    assert removed_paths == []

    removed_methods: list[str] = []
    for path, baseline_methods in baseline_paths.items():
        current_methods = current_paths.get(path, {})
        for method in sorted(set(baseline_methods) & HTTP_METHODS):
            if method not in current_methods:
                removed_methods.append(f"{method.upper()} {path}")
    assert removed_methods == []

    operation_drift: list[str] = []
    for path, baseline_methods in baseline_paths.items():
        current_methods = current_paths.get(path, {})
        for method in sorted(set(baseline_methods) & set(current_methods) & HTTP_METHODS):
            if _operation_breaking_drift(baseline_methods[method], current_methods[method]):
                operation_drift.append(f"{method.upper()} {path}")
    assert operation_drift == []

    current_schemas = _schemas(current)
    baseline_schemas = _schemas(baseline)
    removed_schemas = sorted(set(baseline_schemas) - set(current_schemas))
    assert removed_schemas == []

    removed_fields: list[str] = []
    optional_to_required: list[str] = []
    narrowed_enums: list[str] = []
    for schema_name, baseline_schema in sorted(baseline_schemas.items()):
        current_schema = current_schemas.get(schema_name, {})
        baseline_props = baseline_schema.get("properties") or {}
        current_props = current_schema.get("properties") or {}
        for field_name in sorted(set(baseline_props) - set(current_props)):
            removed_fields.append(f"{schema_name}.{field_name}")

        baseline_required = set(baseline_schema.get("required") or [])
        current_required = set(current_schema.get("required") or [])
        newly_required_existing = (current_required - baseline_required) & set(baseline_props)
        for field_name in sorted(newly_required_existing):
            optional_to_required.append(f"{schema_name}.{field_name}")

        baseline_enum = _enum_values(baseline_schema)
        current_enum = _enum_values(current_schema)
        if baseline_enum and not baseline_enum <= current_enum:
            narrowed_enums.append(schema_name)

    assert removed_fields == []
    assert optional_to_required == []
    assert narrowed_enums == []
