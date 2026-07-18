"""Authoritative hydration helpers for Unity Catalog model-version searches."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def model_version_field(value: Any, name: str) -> str:
    return str(getattr(value, name, "") or "").strip()


def model_version_tags(version: Any, *, resource: str) -> dict[str, str]:
    """Read tags only from a fully hydrated MLflow model-version entity."""

    raw_tags = getattr(version, "tags", None)
    if not isinstance(raw_tags, Mapping):
        raise RuntimeError(f"{resource} has no authoritative registration tags")
    return {str(key): str(value) for key, value in raw_tags.items()}


def authoritative_model_version(
    client: Any,
    search_result: Any,
    *,
    expected_model_name: str,
) -> Any:
    """Hydrate a UC search row and bind it to its immutable search identity."""

    name = model_version_field(search_result, "name")
    version = model_version_field(search_result, "version")
    source = model_version_field(search_result, "source")
    if not name or not version or not source:
        raise RuntimeError("Gateway model-version search result lacks immutable identity")
    if name != expected_model_name:
        raise RuntimeError("Gateway model-version search escaped its exact target model")
    authoritative = client.get_model_version(name, version)
    if (
        model_version_field(authoritative, "name") != name
        or model_version_field(authoritative, "version") != version
        or model_version_field(authoritative, "source") != source
    ):
        raise RuntimeError("Gateway model version identity drifted during authoritative hydration")
    model_version_tags(
        authoritative,
        resource=f"Gateway model version {name} v{version}",
    )
    return authoritative
