"""The bundle plan gate's adopt mode: the plan right after an adoption binds (M2-PR6).

tools/databricks/install_state_adopt.py binds the legacy map into this
deployer's state, plans again and hands that plan to
``bundle_plan_gate.evaluate(..., adopt=AdoptExpectation(...))`` at position
``post-bind``. On top of the gate's own refusals (a delete, a recreate, an
``update_id``, an unknown kind, a create of an existing name), adopt mode
refuses:

* ``instance_change``: any update or resize of the Lakebase database instance.
  ``MIP_ACK_DATABASE_INSTANCE_UPDATE`` does not apply: an adoption changes no
  instance, and a reviewed update belongs to a later release;
* ``create_set_mismatch``: the plan's creates are not exactly the bundle's
  keys minus the keys the state now binds. A child ``permissions`` or
  ``grants`` create counts too: an adoption changes no grant;
* ``dashboard_recreate`` and ``dashboard_move`` (attack O9): spike S4 measured
  that ``parent_path`` is ``spec:immutable`` in the direct engine, so a change
  plans ``recreate`` (a new id, the old dashboard trashed); a dashboard
  recreate, and any dashboard entry whose ``parent_path`` changes whatever its
  action, refuses. The CLI plans no change between ``/Users/...`` and
  ``/Workspace/Users/...`` spellings of one folder;
* ``rename_collision``: an update that renames a resource (the plan changes one
  of its name fields, the development-mode prefix dropping, say) to a name
  that already exists. The new name is looked up with the create's own
  read-only lookup (``bundle_plan_lookups.KIND_LOOKUPS``) before anything is
  renamed; a name that cannot be proven free is ``lookup_failed``.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from tools.databricks.bundle_plan_lookups import KIND_LOOKUPS, KindLookup
from tools.databricks.bundle_plan_shape import BundlePlan, PlanEntry

POSITION = "post-bind"
INSTANCE_KIND = "database_instances"
DASHBOARD_KIND = "dashboards"
PARENT_FIELD = "parent_path"

REMEDIES: Mapping[str, str] = MappingProxyType(
    {
        "instance_change": (
            "the plan after the adoption updates the Lakebase database instance; an adoption "
            "changes no instance (no acknowledgement applies here). Align the profile with the "
            "live instance, unadopt, and adopt again; a reviewed instance update is a later "
            "release's"
        ),
        "create_set_mismatch": (
            "the plan after the adoption does not create exactly the bundle's keys minus the "
            "keys the state binds (the full report lists the extra and the missing ones); the "
            "adoption was undone"
        ),
        "dashboard_recreate": (
            "the plan after the adoption recreates a dashboard, losing its id, history and "
            "shares. Spike S4: a dashboard cannot move in place, so keep "
            "MIP_DASHBOARD_PARENT_PATH at the dashboards' live folder, permanently for this "
            "install"
        ),
        "dashboard_move": (
            "the plan after the adoption changes a dashboard's parent_path. Spike S4: the field "
            "is immutable, so a move recreates the dashboard; keep MIP_DASHBOARD_PARENT_PATH at "
            "the dashboards' live folder, permanently for this install"
        ),
        "rename_collision": (
            "the plan after the adoption renames this resource to a name that already exists "
            "(the full report names it); free the name through a reviewed change, then adopt "
            "again"
        ),
    }
)


@dataclass(frozen=True)
class AdoptExpectation:
    """What the state binds after the adoption, and so the only creates the plan may hold."""

    bound: frozenset[str]
    expected_creates: frozenset[str]


def refusals(plan: BundlePlan, expected: AdoptExpectation) -> list[tuple[str, str, dict[str, Any]]]:
    """``(code, resource, detail)`` for every adopt-mode refusal but the rename lookups."""

    found: list[tuple[str, str, dict[str, Any]]] = []
    creates = set()
    for entry in plan.entries:
        if entry.action == "create":
            creates.add(entry.symbolic)
        if entry.kind == INSTANCE_KIND and entry.action in {"update", "resize"}:
            found.append(("instance_change", entry.symbolic, {"action": entry.action}))
        if entry.kind == DASHBOARD_KIND and entry.action != "recreate":
            change = entry.changes.get(PARENT_FIELD)
            if change is not None and change.action != "skip":
                found.append(("dashboard_move", entry.symbolic, {"action": entry.action}))
    extra = sorted(creates - expected.expected_creates)
    missing = sorted(expected.expected_creates - creates)
    if extra or missing:
        found.append(
            (
                "create_set_mismatch",
                "bundle.plan",
                {"extra": extra, "missing": missing, "expected": sorted(expected.expected_creates)},
            )
        )
    return found


def renames(plan: BundlePlan) -> Iterator[tuple[PlanEntry, KindLookup, tuple[str, ...] | str]]:
    """``(entry, lookup, new names)`` per update that renames; a reason when unresolved.

    A dashboard is renamed by its ``display_name`` alone here: a ``parent_path``
    change is a ``dashboard_move`` refusal, never looked up.
    """

    for entry in plan.entries:
        spec = KIND_LOOKUPS.get(entry.kind)
        if spec is None or entry.action not in {"update", "resize"}:
            continue
        changed = [
            name
            for name in spec.name_fields
            if name in entry.changes and entry.changes[name].action != "skip"
        ]
        if not changed or (entry.kind == DASHBOARD_KIND and PARENT_FIELD in changed):
            continue
        names = []
        for name_field in spec.name_fields:
            value = (entry.new_value or {}).get(name_field)
            if name_field in entry.new_vars or not isinstance(value, str) or not value.strip():
                names = []
                break
            names.append(value)
        yield entry, spec, (tuple(names) if names else f"the renamed {', '.join(changed)} is unresolved")
