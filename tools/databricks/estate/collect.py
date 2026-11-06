"""Read the metastore as its direct owner into the canonical estate inventory (M4-PR1).

The owner's CLI (``tools/clients/estate_inventory.py``) calls ``collect``;
the Unity Catalog and account reads live here, beside the inventory they
build (``estate.inventory``), so the client tooling's reviewed SDK surface
(tests/unit/test_bootstrap_account_safety.py) stays the bootstrap's. Every
read is a get or a list. A catalog bound only to other workspaces is read
only through one of the owner's clients on a workspace it is bound to; the
install workspace's answer for it is never trusted, because an empty answer
there could be the binding itself. With no such client, or when a read is
refused, the catalog is recorded as a gap. Malformed answers refuse, with
nothing collected; no line names a catalog, owner or id.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from databricks.sdk.errors import ResourceDoesNotExist
from tools.databricks.agent_runtime_uc_inventory import _text
from tools.databricks.estate import inventory as estate
from tools.databricks.estate.inventory_audit import assignment_rows


class InventoryRefused(RuntimeError):
    """The inventory cannot be taken; the message names no catalog, owner or id."""


def _owner(item: object) -> str:
    owner = getattr(item, "owner", None)
    if not isinstance(owner, str) or not owner or owner != owner.strip():
        raise RuntimeError("the estate inventory read a noncanonical owner")
    return owner


def owner_identity(workspace: Any) -> tuple[str, str, str]:
    """The caller, the metastore id and the install workspace id; the caller must own the metastore."""

    from tools.databricks.agent_runtime_uc_baseline import authoritative_workspace_id

    caller = _text(getattr(workspace.current_user.me(), "user_name", None))
    metastore_id = _text(getattr(workspace.metastores.current(), "metastore_id", None))
    if not caller or not metastore_id:
        raise InventoryRefused("the caller or the current metastore has no canonical identity")
    if _owner(workspace.metastores.get(metastore_id)) != caller:
        raise InventoryRefused("run the estate inventory as the metastore's direct owner")
    return caller, metastore_id, authoritative_workspace_id(workspace)


def workspace_record(account: Any, *, metastore_id: str, install_workspace: str) -> dict[str, Any]:
    """Every assigned workspace; an exact assignments digest for each one the account no longer has."""

    assigned = sorted({_text(item) for item in account.metastore_assignments.list(metastore_id)})
    if "" in assigned or install_workspace not in assigned:
        raise InventoryRefused("the metastore's workspace assignment listing is incomplete")
    defunct: dict[str, dict[str, str]] = {}
    for workspace_id in assigned:
        try:
            account.workspaces.get(int(workspace_id))
            continue
        except ResourceDoesNotExist:
            if workspace_id == install_workspace:
                raise InventoryRefused("the account plane does not have the install workspace") from None
        try:
            rows: list[tuple[str, str, str, tuple[str, ...]]] | None = assignment_rows(
                account.workspace_assignment.list(int(workspace_id))
            )
            state = estate.LISTED
        except ResourceDoesNotExist:
            rows, state = None, estate.NOT_FOUND
        defunct[workspace_id] = {"assignments": state, "assignments_sha256": estate.assignments_digest(rows)}
    return {"assigned": assigned, "defunct": defunct}


class _Census:
    """One foreign catalog's owners, ``information_schema`` apart, and its models by full name."""

    def __init__(self) -> None:
        self.owners: dict[str, Counter[str]] = {}
        self.information: Counter[str] = Counter()
        self.models: dict[str, str] = {}

    def add(self, kind: str, owner: str, *, information_schema: bool) -> None:
        if information_schema:
            self.information[owner] += 1
        else:
            self.owners.setdefault(owner, Counter())[kind] += 1

    def model(self, full_name: str, owner: str) -> bool:
        """Record a model once; ``False`` when two listings disagree on its owner."""

        known = self.models.setdefault(full_name, owner)
        return known == owner


def _children(reader: Any, catalog: str, census: _Census) -> None:
    for schema in reader.schemas.list(catalog, include_browse=True):
        name = _text(getattr(schema, "name", None))
        if not name:
            raise InventoryRefused("a schema listing returned an empty name")
        if (_text(getattr(schema, "full_name", None)) or f"{catalog}.{name}") != f"{catalog}.{name}":
            raise InventoryRefused("a schema listing returned an invalid parent identity")
        system = name == "information_schema"
        census.add("schema", _owner(schema), information_schema=system)
        listings: tuple[tuple[str, Any], ...] = (
            ("function", reader.functions.list(catalog, name, include_browse=True)),
            ("table", reader.tables.list(catalog, name, include_browse=True, omit_columns=True, omit_properties=True)),
            ("volume", reader.volumes.list(catalog, name, include_browse=True)),
        )
        for kind, listing in listings:
            for item in listing:
                item_name = _text(getattr(item, "name", None))
                if not item_name:
                    raise InventoryRefused(f"a {kind} listing returned an empty name")
                if (_text(getattr(item, "full_name", None)) or f"{catalog}.{name}.{item_name}") != (
                    f"{catalog}.{name}.{item_name}"
                ):
                    raise InventoryRefused(f"a {kind} listing returned an invalid parent identity")
                census.add(kind, _owner(item), information_schema=system)
        for model in reader.registered_models.list(catalog_name=catalog, schema_name=name, include_browse=True):
            full = _text(getattr(model, "full_name", None))
            if full.split(".")[:2] != [catalog, name]:
                continue  # another schema's model: the metastore-wide listing places it
            if not census.model(full, _owner(model)):
                raise InventoryRefused("two listings disagree on a registered model's owner")


def _reader_for(
    facts: Mapping[str, Any], *, install_workspace: str, workspace: Any, bound_readers: Mapping[str, Any]
) -> Any | None:
    """The owner's client to read a catalog's children with; ``None`` when none can be trusted."""

    bound = [item["workspace_id"] for item in facts["bindings"]]
    if facts["isolation_mode"] == estate.OPEN or install_workspace in bound:
        return workspace
    return next((bound_readers[item] for item in sorted(bound) if item in bound_readers), None)


def collect(
    workspace: Any,
    *,
    account: Any,
    install_catalog: str,
    now: datetime,
    bound_readers: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The canonical inventory document, read as the metastore's direct owner."""

    _caller, metastore_id, install_workspace = owner_identity(workspace)
    readers = dict(bound_readers or {})
    catalogs: dict[str, dict[str, Any]] = {}
    for item in workspace.catalogs.list(include_browse=True, include_unbound=True):
        name = _text(getattr(item, "name", None))
        if not name or name != getattr(item, "name", None) or name in catalogs:
            raise InventoryRefused("the catalog listing returned an empty, padded or repeated name")
        mode = _text(getattr(item, "isolation_mode", None)).upper()
        if mode not in {estate.OPEN, estate.ISOLATED}:
            raise InventoryRefused("a catalog has an unknown isolation mode")
        bindings: list[dict[str, str]] = []
        if mode == estate.ISOLATED:
            for binding in workspace.workspace_bindings.get_bindings("catalog", name):
                bound = _text(getattr(binding, "workspace_id", None))
                kind = _text(getattr(binding, "binding_type", None)).upper()
                if not bound or kind not in estate.BINDING_TYPES:
                    raise InventoryRefused("a catalog returned an incomplete workspace binding")
                if bound in {row["workspace_id"] for row in bindings}:
                    raise InventoryRefused("a catalog returned duplicate workspace bindings")
                bindings.append({"workspace_id": bound, "binding_type": kind})
        scope = estate.INSTALL if name == install_catalog else (
            estate.PLATFORM if name in estate.PLATFORM_CATALOGS else estate.FOREIGN
        )
        catalogs[name] = {
            "catalog_type": _text(getattr(item, "catalog_type", None)).upper(),
            "isolation_mode": mode,
            "owner": _owner(item),
            "bindings": sorted(bindings, key=lambda row: row["workspace_id"]),
            "scope": scope,
            "gap": "",
            "owners": {},
            "information_schema_owners": {},
        }
    censuses: dict[str, _Census] = {}
    for name, facts in sorted(catalogs.items()):
        if facts["scope"] != estate.FOREIGN:
            continue
        census = censuses[name] = _Census()
        reader = _reader_for(facts, install_workspace=install_workspace, workspace=workspace, bound_readers=readers)
        if reader is None:
            facts["gap"] = "binding_denied_unreadable"
            continue
        try:
            _children(reader, name, census)
        except (InventoryRefused, RuntimeError, ValueError):
            raise
        except Exception:  # noqa: BLE001 - a refused read is a gap, never an absence
            facts["gap"] = "read_refused"
    for model in workspace.registered_models.list(include_browse=True):
        full = _text(getattr(model, "full_name", None))
        catalog = _text(getattr(model, "catalog_name", None)) or full.split(".", 1)[0]
        if not full or not catalog:
            raise InventoryRefused("the registered-model listing returned an incomplete identity")
        if catalog == install_catalog or catalog in estate.PLATFORM_CATALOGS:
            continue
        if catalog not in catalogs:
            raise InventoryRefused("the registered-model catalog is absent from the catalog listing")
        if not censuses[catalog].model(full, _owner(model)):
            raise InventoryRefused("two listings disagree on a registered model's owner")
    for name, census in censuses.items():
        for owner in census.models.values():
            census.add("registered_model", owner, information_schema=False)
        catalogs[name]["owners"] = {owner: dict(sorted(counts.items())) for owner, counts in sorted(census.owners.items())}
        catalogs[name]["information_schema_owners"] = dict(sorted(census.information.items()))
    return {
        "schema": estate.SCHEMA,
        "purpose": estate.PURPOSE,
        "issued_at": now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "metastore_id": metastore_id,
        "workspace_id": install_workspace,
        "install_catalog": install_catalog,
        "catalogs": catalogs,
        "workspaces": workspace_record(account, metastore_id=metastore_id, install_workspace=install_workspace),
    }


def bound_readers(profiles: Iterable[str], *, owner: str, metastore_id: str) -> dict[str, Any]:
    """One owner client per bound workspace, each proven to be the same owner on the same metastore."""

    from databricks.sdk import WorkspaceClient
    from tools.databricks.agent_runtime_uc_baseline import authoritative_workspace_id

    readers: dict[str, Any] = {}
    for profile in profiles:
        reader = WorkspaceClient(profile=profile)
        caller, metastore, _workspace = owner_identity(reader)
        if caller != owner or metastore != metastore_id:
            raise InventoryRefused("a bound-workspace profile is not the same owner on the same metastore")
        readers[authoritative_workspace_id(reader)] = reader
    return readers




__all__ = ["InventoryRefused", "bound_readers", "collect", "owner_identity", "workspace_record"]
