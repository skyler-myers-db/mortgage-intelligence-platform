"""Create and update the governed Genie space, with the missing-catalog fallback.

Split from tools/databricks/provision_genie_space.py (which resolves the
governed space by its pin, tools/databricks/genie_space_pin.py) so each stays
small. When the target catalog has not been materialized yet the Genie API
rejects the table bindings; both writes then land the curation (questions,
instructions, title, description, warehouse) without tables and report it.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tools.databricks.provision_genie_space import SpaceSpec


def _call_create(
    client: Any,
    *,
    warehouse_id: str,
    serialized: str,
    title: str,
    description: str,
) -> Any:
    return client.genie.create_space(
        warehouse_id=warehouse_id,
        serialized_space=serialized,
        title=title,
        description=description,
    )


def _is_missing_catalog_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "catalog" in msg and "does not exist" in msg


def _create_with_fallback(
    client: Any,
    spec: SpaceSpec,
    *,
    warehouse_id: str,
    title: str,
) -> tuple[Any, bool]:
    """Attempt full create; on missing-catalog error, retry with empty tables.

    Returns (created_space, tables_bound_flag).
    """
    serialized_full = spec.to_serialized_payload(include_tables=True)
    try:
        created = _call_create(
            client,
            warehouse_id=warehouse_id,
            serialized=serialized_full,
            title=title,
            description=spec.description,
        )
        return created, True
    except Exception as exc:  # noqa: BLE001
        if not _is_missing_catalog_error(exc):
            raise
        print(
            f"warning: table bindings rejected ({exc}); "
            f"creating space without tables so curation (questions, instructions) still lands.",
            file=sys.stderr,
        )
        serialized_empty = spec.to_serialized_payload(include_tables=False)
        created = _call_create(
            client,
            warehouse_id=warehouse_id,
            serialized=serialized_empty,
            title=title,
            description=spec.description,
        )
        return created, False


def _update_with_fallback(
    client: Any,
    spec: SpaceSpec,
    *,
    space_id: str,
    title: str,
    warehouse_id: str | None,
) -> bool:
    """Attempt full update; fall back to empty tables on missing-catalog error.

    Returns tables_bound_flag.
    """
    serialized_full = spec.to_serialized_payload(include_tables=True)
    try:
        kwargs: dict[str, Any] = {
            "space_id": space_id,
            "description": spec.description,
            "serialized_space": serialized_full,
            "title": title,
        }
        if warehouse_id:
            kwargs["warehouse_id"] = warehouse_id
        client.genie.update_space(**kwargs)
        return True
    except Exception as exc:  # noqa: BLE001
        if not _is_missing_catalog_error(exc):
            raise
        print(
            f"warning: table bindings rejected on update ({exc}); "
            f"updating without tables so questions/instructions still land.",
            file=sys.stderr,
        )
        serialized_empty = spec.to_serialized_payload(include_tables=False)
        kwargs = {
            "space_id": space_id,
            "description": spec.description,
            "serialized_space": serialized_empty,
            "title": title,
        }
        if warehouse_id:
            kwargs["warehouse_id"] = warehouse_id
        client.genie.update_space(**kwargs)
        return False


def _verify_round_trip(client: Any, space_id: str) -> dict[str, Any]:
    """Re-fetch the space with its serialized payload and return the dict.

    Callers assert structure, not content — e.g. that config.sample_questions
    echoes the same count we sent.
    """
    full = client.genie.get_space(space_id, include_serialized_space=True)
    d = full.as_dict() if hasattr(full, "as_dict") else {}
    ss = d.get("serialized_space")
    if isinstance(ss, str):
        try:
            d["_parsed_serialized_space"] = json.loads(ss)
        except Exception:  # noqa: BLE001
            d["_parsed_serialized_space"] = {}
    return d
