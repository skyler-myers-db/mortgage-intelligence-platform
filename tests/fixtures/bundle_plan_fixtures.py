"""Sanitized ``databricks bundle plan -o json`` documents, and a recording workspace.

No governed plan has run yet, so these are not captures. They mirror the
structs of the Databricks CLI at the installed tag, v1.10.0, field for field:

* ``bundle/deployplan/plan.go``: ``Plan`` (``plan_version`` = 2,
  ``cli_version``, ``lineage``, ``serial``, ``plan``) and ``PlanEntry``
  (``id``, ``depends_on`` of ``{node, label}``, ``action``, ``gone``,
  ``new_state``, ``remote_state``, ``changes`` of ``ChangeDesc`` ``{action,
  reason, old, new, remote}``). Every field is ``omitempty`` except
  ``ChangeDesc.action``; a direct-engine plan always sets ``plan_version``.
* ``libs/structs/structvar/structvar.go``: ``new_state`` is ``{value, vars}``.
* ``bundle/direct/bundle_plan.go``: an empty state has no lineage and serial
  0, so both are omitted, and every entry plans ``create`` with no
  ``remote_state``; ``skip`` entries lose their ``new_state``; a state entry
  the configuration dropped plans ``delete`` with only ``depends_on``.
* ``cmd/bundle/plan.go``: the document is ``json.MarshalIndent(plan, "", "  ")``.

Names are symbolic placeholders, never an install's: ``synthetic-<key>`` for a
resolved name and ``deployer@example.invalid`` for the deployer. A live
capture is owed at the first governed plan phase (M1-PR7).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml
from databricks.sdk.errors import NotFound

REPO = Path(__file__).resolve().parents[2]
CLI_VERSION = "1.10.0"
LINEAGE = "00000000-0000-4000-8000-000000000000"
DEPLOYER_ROOT = (
    "/Workspace/Users/deployer@example.invalid/.bundle/mortgage-intelligence-platform/dev"
)
APP_KEY = "mip_app"
# Every (service, method) a lookup may call; all are GETs or lists.
READ_ONLY_CALLS = frozenset(
    {
        ("apps", "get"),
        ("database", "get_database_instance"),
        ("catalogs", "get"),
        ("warehouses", "list"),
        ("jobs", "list"),
        ("pipelines", "list_pipelines"),
        ("workspace", "get_status"),
        ("experiments", "get_by_name"),
    }
)


def declared_resources() -> list[tuple[str, str]]:
    """``(kind, key)`` for every resource databricks.yml declares, targets included."""

    body = yaml.safe_load((REPO / "databricks.yml").read_text(encoding="utf-8"))
    keys = {
        (kind, key) for kind, entries in (body.get("resources") or {}).items() for key in entries
    }
    for target in (body.get("targets") or {}).values():
        for kind, entries in ((target or {}).get("resources") or {}).items():
            keys.update((kind, key) for key in entries)
    return sorted(keys)


def resolved_name(kind: str, key: str) -> dict[str, Any]:
    """The ``new_state.value`` naming fields the CLI resolves for one resource."""

    if kind == "dashboards":
        return {"display_name": f"synthetic-{key}", "parent_path": f"{DEPLOYER_ROOT}/resources"}
    return {"name": f"synthetic-{key}"}


def names_of(kind: str, key: str) -> tuple[str, ...]:
    value = resolved_name(kind, key)
    if kind == "dashboards":
        return (value["parent_path"], value["display_name"])
    return (value["name"],)


def entry(
    action: str,
    *,
    value: dict[str, Any] | None = None,
    refs: dict[str, str] | None = None,
    changes: dict[str, dict[str, Any]] | None = None,
    depends_on: list[dict[str, str]] | None = None,
    gone: bool = False,
    remote: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One ``PlanEntry`` as ``json.Marshal`` writes it (omitempty fields left out)."""

    body: dict[str, Any] = {}
    if depends_on:
        body["depends_on"] = depends_on
    body["action"] = action
    if gone:
        body["gone"] = True
    if value is not None:
        body["new_state"] = {"value": value, **({"vars": refs} if refs else {})}
    if remote is not None:
        body["remote_state"] = remote
    if changes:
        body["changes"] = changes
    return body


def document(
    entries: dict[str, dict[str, Any]], *, lineage: str = LINEAGE, serial: int = 7
) -> dict[str, Any]:
    """One ``Plan``; an empty state (``lineage=""``, ``serial=0``) omits both."""

    plan: dict[str, Any] = {"plan_version": 2, "cli_version": CLI_VERSION}
    if lineage:
        plan["lineage"] = lineage
    if serial:
        plan["serial"] = serial
    plan["plan"] = entries
    return plan


def adopted() -> dict[str, Any]:
    """The deployer's state binds every declared resource: no changes, no creates."""

    return document(
        {
            f"resources.{kind}.{key}": entry(
                "skip", remote={**resolved_name(kind, key), "id": f"id-{key}"}
            )
            for kind, key in declared_resources()
        }
    )


def first_install() -> dict[str, Any]:
    """An empty deployer state: every declared resource plans a create."""

    return document(
        {
            f"resources.{kind}.{key}": entry("create", value=resolved_name(kind, key))
            for kind, key in declared_resources()
        },
        lineage="",
        serial=0,
    )


def with_entries(base: dict[str, Any], entries: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """A copy of ``base`` with the entries at these ``resources.*`` keys replaced or added."""

    doc = copy.deepcopy(base)
    doc["plan"].update(copy.deepcopy(entries))
    return doc


def instance_update(capacity: str = "CU_2") -> dict[str, Any]:
    """A database-instance resize, as the direct engine plans a capacity change."""

    return entry(
        "update",
        value={"name": "synthetic-mip_app_state", "capacity": capacity},
        changes={
            "capacity": {"action": "update", "old": "CU_1", "new": capacity, "remote": "CU_1"},
            "effective_capacity": {"action": "skip", "reason": "backend_default", "remote": "CU_1"},
        },
        remote={"name": "synthetic-mip_app_state", "capacity": "CU_1"},
    )


def dumps(doc: dict[str, Any]) -> str:
    """The document as ``bundle plan -o json`` prints it."""

    return json.dumps(doc, indent=2) + "\n"


class RecordingWorkspace:
    """A WorkspaceClient double that answers the gate's lookups and records every call.

    ``existing`` maps a service to the names that exist there (dashboards by
    workspace path). Any attribute chain resolves, so a mutating call would be
    recorded too, and the tests assert none ever is. ``failures`` makes one
    service's every call raise.
    """

    def __init__(
        self,
        existing: dict[str, set[str]] | None = None,
        failures: dict[str, Exception] | None = None,
    ) -> None:
        self.existing = {service: set(names) for service, names in (existing or {}).items()}
        self.failures = dict(failures or {})
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def __getattr__(self, service: str) -> Any:
        if service.startswith("__"):
            raise AttributeError(service)
        workspace = self

        class _Service:
            def __getattr__(self, method: str) -> Any:
                def call(*args: Any, **kwargs: Any) -> Any:
                    workspace.calls.append((service, method, {"args": args, **kwargs}))
                    return workspace._answer(service, method, args, kwargs)

                return call

        return _Service()

    def _names(self, service: str) -> set[str]:
        return self.existing.get(service, set())

    def _answer(
        self, service: str, method: str, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> Any:
        if service in self.failures:
            raise self.failures[service]
        key = (
            kwargs.get("name")
            or kwargs.get("path")
            or kwargs.get("experiment_name")
            or (args[0] if args else None)
        )
        if (service, method) in {
            ("apps", "get"),
            ("database", "get_database_instance"),
            ("catalogs", "get"),
            ("workspace", "get_status"),
            ("experiments", "get_by_name"),
        }:
            if key in self._names(service):
                return SimpleNamespace(name=key)
            raise NotFound(f"{service} {method}: not found")
        if (service, method) == ("warehouses", "list"):
            return [SimpleNamespace(name=name) for name in sorted(self._names(service))]
        if (service, method) == ("jobs", "list"):
            wanted = str(kwargs.get("name") or "").casefold()
            return [
                SimpleNamespace(settings=SimpleNamespace(name=name))
                for name in sorted(self._names(service))
                if name.casefold() == wanted
            ]
        if (service, method) == ("pipelines", "list_pipelines"):
            return [SimpleNamespace(name=name) for name in sorted(self._names(service))]
        return None

    def mutating_calls(self) -> list[tuple[str, str]]:
        return [
            (service, method)
            for service, method, _ in self.calls
            if (service, method) not in READ_ONLY_CALLS
        ]


def existing_install() -> dict[str, set[str]]:
    """Every declared resource already exists under its resolved name."""

    services = {
        "apps": "apps",
        "database_instances": "database",
        "database_catalogs": "catalogs",
        "sql_warehouses": "warehouses",
        "jobs": "jobs",
        "pipelines": "pipelines",
        "dashboards": "workspace",
        "experiments": "experiments",
    }
    existing: dict[str, set[str]] = {}
    for kind, key in declared_resources():
        names = names_of(kind, key)
        found = f"{names[0]}/{names[1]}.lvdash.json" if kind == "dashboards" else names[0]
        existing.setdefault(services[kind], set()).add(found)
    return existing
