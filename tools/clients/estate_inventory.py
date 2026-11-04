"""Take the estate inventory as the metastore's direct owner: read-only (M4-PR1).

Run by the owner, never by CI or an agent, at most 30 days before an
agentic-tier run (docs/deployment.md, "Estate inventory"):

  python -m tools.clients.estate_inventory --out F [--catalog mip] [--bound-workspace-profile P ...]
  python -m tools.clients.estate_inventory --summarize F

``--out`` reads the metastore as its owner (the caller must own it directly)
and writes the canonical inventory (``tools/databricks/estate/inventory.py``)
to ``F``, mode 0600, atomically and never through a symlink. It lists every
catalog with ``include_unbound``, each one's bindings, and, below each foreign
catalog, the owner of every schema, table, function, volume and registered
model. A catalog bound only to other workspaces is read only through a
``--bound-workspace-profile`` (a Databricks CLI profile on one of its bound
workspaces, as the same owner): what the install workspace returns for it is
never trusted, because an empty answer there could be the binding. With no
such profile, or when a read is refused, the catalog is recorded as a gap,
and the deploy refuses an inventory with any gap. The account plane (the
deploy's ``DATABRICKS_ACCOUNT_*`` credential) classifies every workspace
assigned to the metastore; one it no longer has is recorded with the exact
digest of its assignments.

The tool prints counts, gap counts and the ``estate`` digest, never a name,
owner or id: export that digest as ``MIP_ACK_ESTATE_INVENTORY`` for the run
that relies on the inventory, and store the file's content as the client's
``MIP_ESTATE_INVENTORY_JSON`` GitHub Environment secret (at most 48 KB) or
point ``MIP_ESTATE_INVENTORY_FILE`` at it. ``--summarize`` recomputes both
from a written file. Exit 0: written, no gap; 3: written with gaps; 2: refused
(nothing written).
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from databricks.sdk.errors import ResourceDoesNotExist

from tools.clients.handover_journal import JournalRefused, canonical_json, write_private
from tools.databricks.agent_runtime_uc_inventory import _text
from tools.databricks.estate import inventory as estate
from tools.databricks.estate.inventory_audit import assignment_rows

_PREFIX = "[mip-estate]"


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


def _bound_readers(profiles: Iterable[str], *, owner: str, metastore_id: str) -> dict[str, Any]:
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


def _print_summary(document: Mapping[str, Any], *, now: datetime, size: int) -> int:
    parsed = estate.parse_inventory(canonical_json(document))
    print(f"{_PREFIX} {estate.summary(parsed, now=now)}")
    if size > estate.SECRET_LIMIT_BYTES:
        print(f"{_PREFIX} {size} bytes: over the 48 KB secret limit; use {estate.INVENTORY_FILE_ENV}")
    print(f"{_PREFIX} export {estate.ACK_ENV}=<the sha256 above> for each agentic run that relies on it")
    if parsed.gap_count():
        print(f"{_PREFIX} the deploy refuses an inventory with any gap; fix each read and take it again")
        return 3
    return 0


def main(
    argv: list[str] | None = None,
    *,
    workspace_factory: Callable[[], Any] | None = None,
    account_factory: Callable[[], Any] | None = None,
    now: datetime | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--out", type=Path)
    action.add_argument("--summarize", type=Path)
    parser.add_argument("--catalog", default="mip")
    parser.add_argument("--bound-workspace-profile", action="append", default=[])
    args = parser.parse_args(argv)
    moment = now or datetime.now(UTC)
    try:
        if args.summarize is not None:
            text = args.summarize.read_text(encoding="utf-8")
            return _print_summary(estate.parse_inventory(text).document, now=moment, size=len(text.encode()))
        from databricks.sdk import WorkspaceClient

        from tools.databricks.uc_owner_policy import account_client_from_env

        workspace = (workspace_factory or WorkspaceClient)()
        owner, metastore_id, _install = owner_identity(workspace)
        readers = _bound_readers(args.bound_workspace_profile, owner=owner, metastore_id=metastore_id)
        document = collect(
            workspace,
            account=(account_factory or account_client_from_env)(),
            install_catalog=args.catalog,
            now=moment,
            bound_readers=readers,
        )
        if args.out.exists() and not args.out.is_file():
            raise InventoryRefused("--out must be a regular file path")
        write_private(args.out, document)
        return _print_summary(document, now=moment, size=args.out.stat().st_size)
    except (RuntimeError, JournalRefused, OSError) as exc:
        print(f"{_PREFIX} REFUSED: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        print(f"{_PREFIX} nothing was written", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
