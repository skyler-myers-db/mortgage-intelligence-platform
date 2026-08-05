"""Fail-closed normalization for Databricks Serving GET response shapes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

SERVED_MODEL_OUTPUT_FIELDS = frozenset(
    {
        "burst_scaling_enabled",
        "creation_timestamp",
        "creator",
        "environment_vars",
        "instance_profile_arn",
        "max_provisioned_concurrency",
        "min_provisioned_concurrency",
        "model_name",
        "model_version",
        "name",
        "provisioned_model_units",
        "scale_to_zero_enabled",
        "state",
        "workload_size",
        "workload_type",
    }
)


def field(value: object, name: str) -> Any:
    """Read one SDK-object or mapping field without coercion."""

    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def serialized_fields(value: object) -> dict[str, Any] | None:
    """Return the provider-visible non-null fields, or fail closed."""

    if isinstance(value, Mapping):
        raw: object = value
    else:
        as_dict = getattr(value, "as_dict", None)
        raw = as_dict() if callable(as_dict) else getattr(value, "__dict__", None)
    if not isinstance(raw, Mapping) or any(not isinstance(key, str) for key in raw):
        return None
    return {key: field_value for key, field_value in raw.items() if field_value is not None}


def same_scalar(actual: object, expected: object) -> bool:
    """Compare SDK scalar values without Python's bool/integer aliasing."""

    actual = getattr(actual, "value", actual)
    expected = getattr(expected, "value", expected)
    return type(actual) is type(expected) and actual == expected


def provider_bool_matches(actual: object, expected: bool) -> bool:
    """Accept an omitted provider default only when the expected value is false."""

    normalized = False if actual is None and expected is False else actual
    return same_scalar(normalized, expected)


def legacy_served_model_matches(legacy: object, entity: object) -> bool:
    """Require the deprecated served-model view to exactly alias one entity."""

    fields = serialized_fields(legacy)
    if fields is None or set(fields) - SERVED_MODEL_OUTPUT_FIELDS:
        return False
    aliases = (
        ("burst_scaling_enabled", "burst_scaling_enabled"),
        ("instance_profile_arn", "instance_profile_arn"),
        ("max_provisioned_concurrency", "max_provisioned_concurrency"),
        ("min_provisioned_concurrency", "min_provisioned_concurrency"),
        ("model_name", "entity_name"),
        ("model_version", "entity_version"),
        ("name", "name"),
        ("provisioned_model_units", "provisioned_model_units"),
        ("scale_to_zero_enabled", "scale_to_zero_enabled"),
        ("workload_size", "workload_size"),
        ("workload_type", "workload_type"),
    )
    for legacy_field, entity_field in aliases:
        legacy_value = fields.get(legacy_field)
        entity_value = field(entity, entity_field)
        if legacy_field == "burst_scaling_enabled":
            if not provider_bool_matches(legacy_value, False) or not provider_bool_matches(
                entity_value, False
            ):
                return False
        elif not same_scalar(legacy_value, entity_value):
            return False
    return (
        dict(fields.get("environment_vars") or {}) == dict(field(entity, "environment_vars") or {})
        and field(legacy, "external_model") is None
        and field(legacy, "foundation_model") is None
    )


def route_targets_served_entity(route: object, served_name: str) -> bool:
    """Accept either provider route-name alias only when it targets the same entity."""

    return str(field(route, "served_entity_name") or "") == served_name and str(
        field(route, "served_model_name") or ""
    ) in {"", served_name}


def usage_tracking_is_disabled(value: object) -> bool:
    """Accept omission or the provider's explicit disabled representation."""

    if value is None:
        return True
    fields = serialized_fields(value)
    return (
        fields is not None and set(fields) == {"enabled"} and same_scalar(fields["enabled"], False)
    )
