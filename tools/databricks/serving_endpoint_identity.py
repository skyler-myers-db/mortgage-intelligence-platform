"""Immutable identity classifiers for Databricks serving endpoints."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _field(value: object, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def is_platform_foundation_endpoint(details: object) -> bool:
    """Recognize only inert system foundation endpoints without customer ACL IDs."""

    if (
        str(_field(details, "id") or "").strip()
        or str(_field(details, "creator") or "").strip()
        or _field(details, "pending_config") is not None
    ):
        return False
    config = _field(details, "config")
    entities = _field(config, "served_entities") or []
    if not entities or (_field(config, "served_models") or []):
        return False
    aliases: set[str] = set()
    for entity in entities:
        foundation = _field(entity, "foundation_model")
        full_name = str(_field(foundation, "name") or "").strip()
        alias = str(_field(entity, "name") or "").strip()
        if (
            foundation is None
            or not full_name.startswith("system.ai.")
            or _field(entity, "external_model") is not None
            or str(_field(entity, "entity_name") or "").strip()
            or str(_field(entity, "model_name") or "").strip()
            or (_field(entity, "environment_vars") or {})
        ):
            return False
        if alias:
            aliases.add(alias)
    traffic = _field(config, "traffic_config")
    for route in _field(traffic, "routes") or []:
        targets = {
            str(_field(route, field) or "").strip()
            for field in ("served_entity_name", "served_model_name")
        } - {""}
        if len(targets) != 1 or not targets.issubset(aliases):
            return False
    gateway = _field(details, "ai_gateway")
    return _field(gateway, "inference_table_config") is None


def uc_model_serving_identity(entity: object) -> tuple[str, str, str] | None:
    """Classify one serving entity as a UC model or an explicit non-UC provider."""

    entity_name = str(_field(entity, "entity_name") or "").strip()
    model_name = str(_field(entity, "model_name") or "").strip()
    entity_version = str(_field(entity, "entity_version") or "").strip()
    model_version = str(_field(entity, "model_version") or "").strip()
    alias = str(_field(entity, "name") or "").strip()
    foundation = _field(entity, "foundation_model")
    external = _field(entity, "external_model")
    if (entity_name and model_name and entity_name != model_name) or (
        entity_version and model_version and entity_version != model_version
    ):
        raise RuntimeError("serving entity UC model identity is ambiguous")
    name = entity_name or model_name
    version = entity_version or model_version
    if name:
        if foundation is not None or external is not None or not version:
            raise RuntimeError("serving entity UC model identity is invalid")
        return name, version, alias
    if (foundation is None) == (external is None):
        raise RuntimeError("serving entity has no recognized provider identity")
    return None
