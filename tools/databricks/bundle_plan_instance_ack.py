"""The Lakebase instance update acknowledgement of the bundle plan gate (M0-PR4).

``instance_update`` is what an update or resize of a ``database_instances``
entry will do, and ``instance_update_digest`` its sha256: the value
``MIP_ACK_DATABASE_INSTANCE_UPDATE`` must equal before
``bundle_plan_gate.evaluate`` admits the update. The pre-apply gate
recomputes it from a fresh plan, so the acknowledgement covers exactly the
reviewed update.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from tools.databricks.bundle_plan_shape import BundlePlan, sha256_hex

ACK_ENV = "MIP_ACK_DATABASE_INSTANCE_UPDATE"
INSTANCE_KIND = "database_instances"


def instance_update(plan: BundlePlan) -> tuple[Mapping[str, Any], ...]:
    """What every database-instance update or resize will do, exactly as it is acknowledged.

    The CLI's ``DoUpdate`` (``bundle/direct/dresources/database_instance.go``
    at v1.10.0) sends the whole ``new_state.value`` with ``UpdateMask: "*"``,
    so the update writes every field, not only the changes the plan classifies
    as ``update``. A remote-only difference on an input-only field
    (``node_count``, ``retention_window_in_days`` and the other
    ``ignore_remote_changes`` fields of ``resources.generated.yml``) plans
    ``skip``, yet the update reverts it to the configured value. So each item
    carries the full ``new_state`` and every change, skip included; the
    instance name and the state lineage bind it to one install, so an
    acknowledgement kept for another install's identical diff never replays.
    """

    items = []
    for entry in plan.entries:
        if entry.kind != INSTANCE_KIND or entry.action not in {"update", "resize"}:
            continue
        changes = {
            path: {
                "action": change.action,
                "reason": change.reason,
                "old": change.old,
                "new": change.new,
                "remote": change.remote,
            }
            for path, change in sorted(entry.changes.items())
        }
        new_state = dict(entry.new_value) if entry.new_value is not None else None
        items.append(
            {
                "resource": entry.symbolic,
                "action": entry.action,
                "instance_name": (new_state or {}).get("name"),
                "lineage": plan.lineage,
                "new_state": new_state,
                "new_vars": dict(entry.new_vars),
                "changes": changes,
            }
        )
    return tuple(items)


def instance_update_digest(plan: BundlePlan) -> str:
    """sha256 of every ``instance_update`` item; "" when no instance changes."""

    items = instance_update(plan)
    return sha256_hex(list(items)) if items else ""
