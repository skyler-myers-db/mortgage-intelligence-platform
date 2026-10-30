"""The bundle plan gate's existence lookups: one reviewed read-only call per kind (M0-PR4).

``KIND_LOOKUPS`` maps each resource kind databricks.yml declares to the
fields that name its object and the GET or list that proves the name free
(``tests/unit/test_bundle_plan_gate_kinds.py`` pins the table equal to the
declared kinds). A GET reads only not-found as absent; any other error
propagates, so ``bundle_plan_gate.evaluate`` refuses the create as
``lookup_failed`` rather than read it as free.

A list answers with what the caller may see (``warehouses.list`` "lists all
SQL warehouses that a user has access to"; ``jobs.list`` and
``pipelines.list_pipelines`` filter by the caller's ACLs too), and a GET may
answer not-found for an object the caller cannot read. So an absence in a
workspace-scoped lookup is evidence only from a caller that sees every
workspace object: ``prove_full_visibility`` requires a workspace admin first.
The metastore-scoped catalog lookup is the one exception, since workspace
admin widens nothing there.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from databricks.sdk.errors import NotFound

ADMINS_GROUP = "admins"
VISIBILITY_METHOD = "current_user.me"


class VisibilityUnproven(RuntimeError):
    """The caller is not proven to see every workspace object, so an absence proves nothing."""


def prove_full_visibility(workspace: Any) -> None:
    """Return only when the caller is a workspace admin; a ``me`` error propagates.

    The same membership read as
    ``audit_global_m2m_access.workspace_admin_inventory_principal``, which the
    deploy's preflight already requires of its ambient identity; the gate
    proves it again itself, because the gate also runs on its own.
    """

    current = workspace.current_user.me()
    groups = {
        _value(group, "display").strip().casefold()
        for group in (_value_object(current, "groups") or ())
    }
    if ADMINS_GROUP not in groups:
        raise VisibilityUnproven(
            "the caller is not a workspace admin, so a lookup that sees only what the "
            "caller may see cannot prove a workspace-scoped name free"
        )


def _found(call: Callable[[], Any]) -> bool:
    """True when the GET answers, False on not-found; any other error propagates."""

    try:
        call()
    except NotFound:  # ResourceDoesNotExist is a NotFound
        return False
    return True


def _value(item: Any, name: str) -> str:
    raw = item.get(name) if isinstance(item, Mapping) else getattr(item, name, None)
    return str(raw or "")


def _app_exists(workspace: Any, names: tuple[str, ...]) -> bool:
    return _found(lambda: workspace.apps.get(name=names[0]))


def _instance_exists(workspace: Any, names: tuple[str, ...]) -> bool:
    return _found(lambda: workspace.database.get_database_instance(name=names[0]))


def _catalog_exists(workspace: Any, names: tuple[str, ...]) -> bool:
    return _found(lambda: workspace.catalogs.get(name=names[0]))


def _warehouse_exists(workspace: Any, names: tuple[str, ...]) -> bool:
    return any(_value(item, "name") == names[0] for item in workspace.warehouses.list())


def _job_exists(workspace: Any, names: tuple[str, ...]) -> bool:
    # The API filters on the exact name, case-insensitively; a same-name job
    # would run a second copy of the schedule, so case-folded equality counts.
    wanted = names[0].casefold()
    return any(
        _value(_value_object(job, "settings"), "name").casefold() == wanted
        for job in workspace.jobs.list(name=names[0])
    )


def _pipeline_exists(workspace: Any, names: tuple[str, ...]) -> bool:
    if "'" in names[0]:
        raise ValueError("a pipeline name with a quote cannot be filtered safely")
    listed = workspace.pipelines.list_pipelines(filter=f"name LIKE '{names[0]}'")
    return any(_value(item, "name") == names[0] for item in listed)


def _dashboard_exists(workspace: Any, names: tuple[str, ...]) -> bool:
    parent, display_name = names
    path = f"{parent.rstrip('/')}/{display_name}.lvdash.json"
    return _found(lambda: workspace.workspace.get_status(path=path))


def _experiment_exists(workspace: Any, names: tuple[str, ...]) -> bool:
    return _found(lambda: workspace.experiments.get_by_name(experiment_name=names[0]))


def _value_object(item: Any, name: str) -> Any:
    return item.get(name) if isinstance(item, Mapping) else getattr(item, name, None)


@dataclass(frozen=True)
class KindLookup:
    """How one resource kind's create is proven not to collide."""

    kind: str
    name_fields: tuple[str, ...]  # new_state.value fields that name the object
    unique_within: str
    method: str  # the read-only SDK call, for the full report
    exists: Callable[[Any, tuple[str, ...]], bool]
    # True unless the name is unique within the metastore: an absence counts
    # only after prove_full_visibility (see the module docstring).
    requires_admin_visibility: bool = True


KIND_LOOKUPS: Mapping[str, KindLookup] = MappingProxyType(
    {
        spec.kind: spec
        for spec in (
            KindLookup("apps", ("name",), "workspace", "apps.get", _app_exists),
            KindLookup(
                "database_instances",
                ("name",),
                "workspace",
                "database.get_database_instance",
                _instance_exists,
            ),
            KindLookup(
                "database_catalogs",
                ("name",),
                "metastore",
                "catalogs.get",
                _catalog_exists,
                requires_admin_visibility=False,
            ),
            KindLookup(
                "sql_warehouses", ("name",), "workspace", "warehouses.list", _warehouse_exists
            ),
            KindLookup("jobs", ("name",), "workspace (by name)", "jobs.list", _job_exists),
            KindLookup(
                "pipelines", ("name",), "workspace", "pipelines.list_pipelines", _pipeline_exists
            ),
            KindLookup(
                "dashboards",
                ("parent_path", "display_name"),
                "workspace path",
                "workspace.get_status",
                _dashboard_exists,
            ),
            KindLookup(
                "experiments", ("name",), "workspace", "experiments.get_by_name", _experiment_exists
            ),
        )
    }
)
