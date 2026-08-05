"""Bounded serving-endpoint and query-group proof for cutover retirement."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from databricks.sdk.errors import NotFound, ResourceDoesNotExist
from tools.databricks.serving_endpoint_acl import is_platform_foundation_endpoint
from tools.databricks.serving_query_group_access import inspect_managed_query_group

MAX_CUTOVER_ENDPOINT_INVENTORY = 10_000


def assert_journal_endpoint_retired(
    workspace: Any,
    *,
    endpoint_inventory: tuple[tuple[str, str, str], ...],
    name: str,
    endpoint_id: str,
    creator: str,
    label: str,
) -> None:
    """Prove one exact endpoint absent even when the list response omits it."""

    try:
        direct = workspace.serving_endpoints.get(name)
    except (NotFound, ResourceDoesNotExist):
        direct = None
    if direct is not None:
        direct_identity = (
            str(getattr(direct, "id", "") or "").strip(),
            str(getattr(direct, "creator", "") or "").strip(),
        )
        if direct_identity == (endpoint_id, creator):
            raise RuntimeError(f"cutover journal {label} endpoint is not retired")
        raise RuntimeError(
            f"cutover journal {label} endpoint name or immutable ID was reused"
        )
    collisions = [
        endpoint
        for endpoint in endpoint_inventory
        if endpoint[0] == name or endpoint[1] == endpoint_id
    ]
    if collisions:
        raise RuntimeError(
            f"cutover journal {label} endpoint name or immutable ID was reused"
        )


def assert_retained_journal_gateway_exact(
    workspace: Any,
    *,
    endpoint_inventory: tuple[tuple[str, str, str], ...],
    name: str,
    endpoint_id: str,
    creator: str,
) -> bool:
    """Return whether a non-deletable old Gateway remains exact and inventoried."""

    try:
        details = workspace.serving_endpoints.get(name)
    except (NotFound, ResourceDoesNotExist):
        assert_journal_endpoint_retired(
            workspace,
            endpoint_inventory=endpoint_inventory,
            name=name,
            endpoint_id=endpoint_id,
            creator=creator,
            label="Gateway",
        )
        return False
    actual = (
        str(getattr(details, "id", "") or "").strip(),
        str(getattr(details, "creator", "") or "").strip(),
    )
    if actual != (endpoint_id, creator):
        raise RuntimeError(
            "cutover journal retained Gateway endpoint name or immutable ID was reused"
        )
    if (name, endpoint_id, creator) not in endpoint_inventory:
        raise RuntimeError(
            "cutover journal retained Gateway is omitted from complete endpoint inventory"
        )
    return True


def validated_cutover_endpoint_inventory(
    workspace: Any,
) -> tuple[tuple[str, str, str], ...]:
    """Hydrate one complete, bounded endpoint inventory without lossy rows."""

    inventory: list[tuple[str, str, str]] = []
    names: set[str] = set()
    endpoint_ids: set[str] = set()
    for index, raw in enumerate(workspace.serving_endpoints.list()):
        if index >= MAX_CUTOVER_ENDPOINT_INVENTORY:
            raise RuntimeError("serving endpoint inventory exceeds the reviewed bound")
        name = str(
            raw.get("name") if isinstance(raw, Mapping) else getattr(raw, "name", None) or ""
        ).strip()
        if not name or name in names:
            raise RuntimeError("serving endpoint inventory has a duplicate or missing name")
        names.add(name)
        try:
            details = workspace.serving_endpoints.get(name)
        except (NotFound, ResourceDoesNotExist) as exc:
            raise RuntimeError("serving endpoint disappeared during cutover inventory") from exc
        if is_platform_foundation_endpoint(details):
            continue
        endpoint_id = str(getattr(details, "id", "") or "").strip()
        creator = str(getattr(details, "creator", "") or "").strip()
        if not endpoint_id or not creator:
            raise RuntimeError("serving endpoint inventory has an incomplete immutable identity")
        if endpoint_id in endpoint_ids:
            raise RuntimeError("serving endpoint inventory has a duplicate immutable ID")
        endpoint_ids.add(endpoint_id)
        inventory.append((name, endpoint_id, creator))
    return tuple(inventory)


def assert_journal_query_groups_retired(
    workspace: Any,
    *,
    journal: Mapping[str, str],
    app_application_id: str,
    verifier_application_id: str,
    proxy_application_id: str,
    allow_empty_live_gateway_groups: bool = False,
) -> None:
    """Prove retired groups are absent, or empty for a retained old Gateway."""

    application_ids = {
        "App": app_application_id.strip(),
        "verifier": verifier_application_id.strip(),
        "proxy": proxy_application_id.strip(),
    }
    if any(not value for value in application_ids.values()):
        raise ValueError(
            "App, verifier, and proxy application IDs are required for cutover clearance"
        )
    if len(set(application_ids.values())) != len(application_ids):
        raise ValueError("cutover clearance application identities must be distinct")
    targets: list[tuple[str, str, str]] = []
    if journal.get("old_gateway_endpoint"):
        targets.extend(
            (
                ("Gateway App", journal["old_gateway_endpoint_id"], application_ids["App"]),
                (
                    "Gateway verifier",
                    journal["old_gateway_endpoint_id"],
                    application_ids["verifier"],
                ),
            )
        )
    if journal.get("old_id"):
        targets.extend(
            (
                ("Supervisor App", journal["old_endpoint_id"], application_ids["App"]),
                (
                    "Supervisor proxy",
                    journal["old_endpoint_id"],
                    application_ids["proxy"],
                ),
            )
        )
    for label, endpoint_id, application_id in targets:
        state = inspect_managed_query_group(
            workspace,
            endpoint_id=endpoint_id,
            application_id=application_id,
            missing_ok=True,
        )
        if state is None:
            continue
        if (
            allow_empty_live_gateway_groups
            and label.startswith("Gateway ")
            and not state.member_ids
        ):
            continue
        raise RuntimeError(f"cutover journal {label} query group is not retired")
