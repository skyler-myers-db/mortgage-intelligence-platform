"""Validated file inputs for the App rollback command."""

from __future__ import annotations

import json
from pathlib import Path

from tools.databricks.app_rollback_resource_contract import (
    reviewed_app_resource_contract,
    validated_app_resource_contract,
)


def payload_file(path: str) -> dict[str, object]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("App deployment payload file is invalid") from exc
    if not isinstance(value, dict):
        raise RuntimeError("App deployment payload file is not an object")
    return value


def reviewed_resources_file(path: str) -> list[dict[str, object]]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("bundle summary file is invalid") from exc
    resources = value.get("resources")
    if isinstance(resources, list):
        return validated_app_resource_contract(resources)
    return reviewed_app_resource_contract(value)
