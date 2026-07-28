"""Immutable identity classifiers for Databricks serving endpoints."""

from __future__ import annotations


def is_platform_foundation_endpoint(details: object) -> bool:
    """Recognize only system foundation endpoints without customer ACL IDs."""

    if (
        str(getattr(details, "id", "") or "").strip()
        or str(getattr(details, "creator", "") or "").strip()
    ):
        return False
    entities = getattr(getattr(details, "config", None), "served_entities", None) or []
    if not entities:
        return False
    for entity in entities:
        foundation = getattr(entity, "foundation_model", None)
        full_name = str(getattr(foundation, "name", "") or "").strip()
        if foundation is None or not full_name.startswith("system.ai."):
            return False
    return True
