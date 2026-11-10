"""Canonical non-secret Databricks App resource bindings for rollback proof."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import timedelta
from typing import Any

from databricks.sdk.service.apps import App

RESOURCE_KINDS = frozenset({"database", "genie_space", "job", "secret", "sql_warehouse"})
RESOURCE_BINDING_FIELDS = {
    "database": frozenset({"database_name", "instance_name", "permission"}),
    "genie_space": frozenset({"name", "space_id", "permission"}),
    "job": frozenset({"id", "permission"}),
    "secret": frozenset({"key", "permission", "scope"}),
    "sql_warehouse": frozenset({"id", "permission"}),
}
RESOURCE_BINDING_PERMISSIONS = {
    "database": "CAN_CONNECT_AND_CREATE",
    "genie_space": "CAN_RUN",
    "job": "CAN_MANAGE_RUN",
    "secret": "READ",
    "sql_warehouse": "CAN_USE",
}
# Exact (kind, binding name) exceptions to the per-kind permission above. The
# saved-watchlist scheduler is bound read-only (flow-08 slice 2): the App reads
# its schedule state and never runs it, so CAN_VIEW is required and the
# broader CAN_MANAGE_RUN is refused. Every other job binding keeps
# CAN_MANAGE_RUN.
RESOURCE_BINDING_PERMISSION_OVERRIDES: dict[tuple[str, str], str] = {
    ("job", "growth_agent_scheduler_job"): "CAN_VIEW",
}


def _required_permission(kind: str, name: str) -> str:
    return RESOURCE_BINDING_PERMISSION_OVERRIDES.get((kind, name), RESOURCE_BINDING_PERMISSIONS[kind])
_RESOURCE_REFERENCE = re.compile(
    r"^\$\{resources\.(?P<kind>[a-z_]+)\.(?P<key>[A-Za-z0-9_-]+)\."
    r"(?P<field>[A-Za-z0-9_-]+)\}$"
)


def _plain(value: object) -> object:
    if isinstance(value, dict):
        return {
            str(key): _plain(item)
            for key, item in sorted(value.items())
            if item is not None
        }
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    if hasattr(value, "as_dict"):
        return _plain(value.as_dict())
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, str | int | float | bool):
        return enum_value
    if isinstance(value, str | int | float | bool):
        return value
    raise RuntimeError("Databricks App resource binding contains an unsupported value")


def validated_app_resource_contract(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list) or not value:
        raise RuntimeError("App rollback resource contract is empty or invalid")
    normalized: list[dict[str, object]] = []
    names: set[str] = set()
    for item in value:
        plain = _plain(item)
        if not isinstance(plain, dict):
            raise RuntimeError("App rollback resource binding is invalid")
        name = str(plain.get("name") or "").strip()
        kinds = RESOURCE_KINDS.intersection(plain)
        if not name or name in names or len(kinds) != 1:
            raise RuntimeError("App rollback resource binding is incomplete or ambiguous")
        kind = next(iter(kinds))
        binding = plain[kind]
        if not isinstance(binding, dict) or set(binding) != RESOURCE_BINDING_FIELDS[kind]:
            raise RuntimeError("App rollback resource target is invalid")
        normalized_binding: dict[str, str] = {}
        for key, raw in binding.items():
            if not isinstance(raw, str) or not raw.strip():
                raise RuntimeError("App rollback resource target is invalid")
            normalized_binding[str(key)] = raw.strip()
        if normalized_binding["permission"] != _required_permission(kind, name):
            raise RuntimeError("App rollback resource permission is invalid")
        names.add(name)
        normalized.append({"name": name, kind: normalized_binding})
    return sorted(normalized, key=lambda row: str(row["name"]))


def app_resource_contract(workspace: Any, *, app_name: str) -> list[dict[str, object]]:
    app = workspace.apps.get(app_name)
    return validated_app_resource_contract(list(getattr(app, "resources", None) or []))


def app_resource_contract_digest(resources: list[dict[str, object]]) -> str:
    canonical = json.dumps(
        validated_app_resource_contract(resources),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


# The resource send is a create-update masked to ``resources``: it changes the
# bindings alone. ``apps update`` (PATCH) would clear the description it does
# not carry (spike S2: very likely why a restored App's description reads "").
RESOURCES_UPDATE_MASK = "resources"
RESOURCE_UPDATE_TIMEOUT = timedelta(minutes=10)
# Spike S2: every update that carries resources, an identical array included,
# grants CREATE ON DATABASE to the App's Lakebase role again, with no restart.
# Only the Lakebase migration job's revoke restores the role contract, and the
# migration plan reports the re-grant (``app_role_regrant``), which the next
# quiesce window plan reads as WR2 (tools/databricks/quiesce_window_plan.py).
ROLE_REGRANT_NOTICE = (
    "[mip-app-rollback] the signed App resource bindings were restored: the App's "
    "Lakebase role holds CREATE ON DATABASE again (spike S2) until the migration job "
    "revokes it; the next release's window plan reads it as WR2"
)


def _plain_text(value: object) -> str:
    return str(value or "").strip()


def restore_signed_app_resource_contract(
    workspace: Any,
    *,
    app_name: str,
    resources: object,
) -> bool:
    """Restore signed bindings; never the description, the source deployment or the compute.

    Returns True when it sent the bindings. Every send re-grants CREATE ON
    DATABASE to the App's Lakebase role (spike S2), so a True return leaves the
    role out of its contract until the migration job revokes it: the restore
    says so on stderr (``ROLE_REGRANT_NOTICE``, names only) and the next
    migration plan reports it as ``app_role_regrant``.
    """

    expected = validated_app_resource_contract(resources)
    before = workspace.apps.get(app_name)
    if app_resource_contract(workspace, app_name=app_name) == expected:
        return False

    def state(app: object) -> tuple[str, str, str]:
        def deployment_id(field: str) -> str:
            deployment = getattr(app, field, None)
            return str(getattr(deployment, "deployment_id", None) or "").strip()

        compute = getattr(getattr(app, "compute_status", None), "state", None)
        return (
            deployment_id("active_deployment"),
            deployment_id("pending_deployment"),
            str(getattr(compute, "value", compute) or "").strip(),
        )

    before_state = state(before)
    before_description = _plain_text(getattr(before, "description", None))
    workspace.apps.create_update(
        app_name=app_name,
        update_mask=RESOURCES_UPDATE_MASK,
        app=App.from_dict({"name": app_name, "resources": expected}),
    ).result(timeout=RESOURCE_UPDATE_TIMEOUT)
    after = workspace.apps.get(app_name)
    if state(after) != before_state:
        raise RuntimeError("App resource rollback changed deployment or compute state")
    if _plain_text(getattr(after, "description", None)) != before_description:
        raise RuntimeError("App resource rollback changed the App description")
    if app_resource_contract(workspace, app_name=app_name) != expected:
        raise RuntimeError("App resource rollback did not restore the signed contract")
    print(ROLE_REGRANT_NOTICE, file=sys.stderr)
    return True


def _resolve_reference(value: object, *, resources: dict[str, object]) -> object:
    if isinstance(value, dict):
        return {
            str(key): _resolve_reference(item, resources=resources)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_resolve_reference(item, resources=resources) for item in value]
    if not isinstance(value, str):
        return value
    match = _RESOURCE_REFERENCE.fullmatch(value.strip())
    if match is None:
        if "${" in value:
            raise RuntimeError("reviewed App resource manifest contains an unresolved reference")
        return value
    kind = resources.get(match.group("kind"))
    item = kind.get(match.group("key")) if isinstance(kind, dict) else None
    resolved = item.get(match.group("field")) if isinstance(item, dict) else None
    if not isinstance(resolved, str) or not resolved.strip() or "${" in resolved:
        raise RuntimeError("reviewed App resource manifest reference did not resolve exactly")
    return resolved.strip()


def reviewed_app_resource_contract(
    bundle_summary: object,
    *,
    app_resource_key: str = "mip_app",
) -> list[dict[str, object]]:
    """Resolve the exact source-declared App bindings from bundle summary."""

    if not isinstance(bundle_summary, dict):
        raise RuntimeError("bundle summary is not an object")
    resources = bundle_summary.get("resources")
    apps = resources.get("apps") if isinstance(resources, dict) else None
    app = apps.get(app_resource_key) if isinstance(apps, dict) else None
    declared = app.get("resources") if isinstance(app, dict) else None
    if not isinstance(resources, dict) or not isinstance(declared, list):
        raise RuntimeError("bundle summary lacks the reviewed App resource manifest")
    resolved = _resolve_reference(declared, resources=resources)
    return validated_app_resource_contract(resolved)
