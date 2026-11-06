"""What the foreign UC audit judges from the owner's estate inventory (M4-PR1).

``audit_agent_runtime_foreign_uc_access`` no longer needs the deployer to own
the metastore. The facts only a metastore owner can read come from the
inventory (``estate.inventory``); the deployer's own reads judge them:

* **Workspaces.** Every workspace assigned to the metastore is classified on
  the account plane (``account.workspaces.get``): one that exists gets the full
  assignment audit; one the account answers RESOURCE_DOES_NOT_EXIST passes
  only with the inventory's exact defunct entry, whose assignments digest must
  equal what its assignment listing returns now (or still does not return);
  any other answer refuses, never read as absent.
* **Coverage.** Every catalog the deployer lists must be in the inventory with
  the same owner, type and isolation mode, or the inventory is stale; only the
  install catalog may be newer than it.
* **Ownership.** Foreign ownership is never acceptable. Every owner of a
  binding-denied catalog or anything below it is matched against the audited
  identity's aliases and groups; every owner of an accessible foreign catalog
  or anything below it goes through ``ApprovedOwnerPolicy``, as the
  metastore-owner audit judged each object's owner; ``information_schema``
  objects keep their fixed platform owner.

Refusal lines carry counts and rules, never a catalog, owner or workspace id.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from types import SimpleNamespace
from typing import Any

from databricks.sdk.errors import ResourceDoesNotExist
from tools.databricks.agent_runtime_uc_baseline import CatalogBindingEvidence
from tools.databricks.agent_runtime_uc_inventory import _text
from tools.databricks.estate.inventory import (
    FOREIGN,
    LISTED,
    NOT_FOUND,
    PLATFORM,
    EstateInventory,
    assignments_digest,
)

LIVE, DEFUNCT = "live", "defunct"
_INTERNAL = "__databricks_internal"
_MANAGED_ONLINE = "MANAGED_ONLINE_CATALOG"
_SYSTEM_USER = "System user"


def assert_inventory_binds(
    inventory: EstateInventory, *, metastore_id: str, workspace_id: str, install_catalog: str
) -> None:
    """The inventory must describe exactly this metastore, workspace and install catalog."""

    if (inventory.metastore_id, inventory.workspace_id, inventory.install_catalog) != (
        metastore_id,
        workspace_id,
        install_catalog,
    ):
        raise RuntimeError("the estate inventory describes another metastore, workspace or install catalog")
    if inventory.gap_count():
        raise RuntimeError(f"the estate inventory has {inventory.gap_count()} gaps")


def classify_workspace(account: Any, workspace_id: str, *, install_workspace: str, inventory: EstateInventory) -> str:
    """``live`` or ``defunct`` (exactly as the inventory lists it); anything else refuses."""

    try:
        account.workspaces.get(int(workspace_id))
    except ResourceDoesNotExist:
        if workspace_id == install_workspace or workspace_id not in inventory.defunct:
            raise RuntimeError(
                "a workspace assigned to the metastore no longer exists and the estate inventory has "
                "no exact defunct entry for it"
            ) from None
        return DEFUNCT
    return LIVE


def assignment_rows(listing: Iterable[object]) -> list[tuple[str, str, str, tuple[str, ...]]]:
    """``(principal id, kind, name, permissions)`` per assignment, as the inventory digests them."""

    rows = []
    for assignment in listing:
        principal = getattr(assignment, "principal", None)
        named = [
            (kind, _text(getattr(principal, attribute, None)))
            for kind, attribute in (
                ("service_principal", "service_principal_name"),
                ("group", "group_name"),
                ("user", "user_name"),
            )
            if _text(getattr(principal, attribute, None))
        ]
        principal_id = _text(getattr(principal, "principal_id", None))
        permissions = tuple(sorted(_text(item).upper() for item in getattr(assignment, "permissions", None) or []))
        if principal is None or not principal_id or len(named) != 1 or not permissions:
            raise RuntimeError("a defunct workspace's assignment listing is incomplete")
        rows.append((principal_id, named[0][0], named[0][1], permissions))
    return rows


def defunct_assignments(account: Any, workspace_id: str, *, inventory: EstateInventory) -> list[object] | None:
    """A defunct workspace's assignments, proven equal to the inventory's exact record.

    Returns the listing (the caller still applies the assignment audit to it),
    or ``None`` when it is still RESOURCE_DOES_NOT_EXIST, as recorded.
    """

    entry = inventory.defunct[workspace_id]
    try:
        listing: list[object] | None = list(account.workspace_assignment.list(int(workspace_id)))
    except ResourceDoesNotExist:
        listing = None
    state = NOT_FOUND if listing is None else LISTED
    digest = assignments_digest(None if listing is None else assignment_rows(listing))
    if state != entry.assignments or digest != entry.assignments_sha256:
        raise RuntimeError(
            "a defunct workspace's assignments differ from the estate inventory's exact entry; take it again"
        )
    return listing


def binding_workspace_ids(inventory: EstateInventory) -> set[str]:
    """Every workspace a binding-denied foreign catalog is bound to."""

    return {item for facts in inventory.binding_denied() for item in facts.bound_workspace_ids}


def binding_evidence(inventory: EstateInventory) -> tuple[CatalogBindingEvidence, ...]:
    """The binding-denied catalogs as the proof carries them: the inventory's bindings, not live ones."""

    return tuple(
        CatalogBindingEvidence(
            catalog=facts.name,
            owner=facts.owner,
            catalog_type=facts.catalog_type,
            isolation_mode=facts.isolation_mode,
            bindings=facts.bindings,
        )
        for facts in inventory.binding_denied()
    )


def assert_live_catalogs(
    listing: Iterable[object], *, inventory: EstateInventory, allow_missing_install: bool
) -> None:
    """Every catalog the deployer lists is in the inventory, unchanged; the install catalog exists."""

    names: set[str] = set()
    stale = missing = 0
    for item in listing:
        name = getattr(item, "name", None)
        if not isinstance(name, str) or not name or name != name.strip() or name in names:
            raise RuntimeError("the catalog listing returned an empty, padded or repeated name")
        names.add(name)
        if name == inventory.install_catalog:
            continue  # the install's own catalog: newer than the inventory, or handed over since
        facts = inventory.catalogs.get(name)
        if facts is None:
            missing += 1
            continue
        observed = (
            getattr(item, "owner", None),
            _text(getattr(item, "catalog_type", None)).upper(),
            _text(getattr(item, "isolation_mode", None)).upper(),
        )
        stale += observed != (facts.owner, facts.catalog_type, facts.isolation_mode)
    if missing or stale:
        raise RuntimeError(
            f"the estate inventory is stale: {missing} listed catalogs are missing from it and {stale} "
            "changed owner, type or isolation since it was taken; take it again"
        )
    if inventory.install_catalog not in names and not allow_missing_install:
        raise RuntimeError("configured MIP catalog is missing from control-plane inventory")


def assert_platform_catalogs(inventory: EstateInventory) -> None:
    """The Databricks internal catalog keeps its fixed platform identity."""

    internal = inventory.catalogs.get(_INTERNAL)
    if internal is None:
        return
    if (internal.isolation_mode, internal.catalog_type, internal.owner) != ("OPEN", "INTERNAL_CATALOG", _SYSTEM_USER):
        raise RuntimeError("Databricks internal catalog does not match the fixed platform identity")


def assert_information_schema_owners(inventory: EstateInventory) -> None:
    """An accessible foreign catalog's ``information_schema`` keeps its platform owner."""

    for facts in inventory.accessible():
        expected = facts.owner if facts.catalog_type == _MANAGED_ONLINE else _SYSTEM_USER
        if set(facts.information_schema_owners) - {expected}:
            label = "the managed-online catalog owner" if facts.catalog_type == _MANAGED_ONLINE else (
                "Databricks System user"
            )
            raise RuntimeError(f"foreign information-schema object is not owned by {label}")


def owner_objects(inventory: EstateInventory) -> tuple[list[object], list[object]]:
    """One owner object per distinct owner: accessible foreign, then binding-denied."""

    accessible = sorted({owner for facts in inventory.accessible() for owner in (facts.owner, *facts.owners)})
    denied = sorted({owner for facts in inventory.binding_denied() for owner in facts.all_owners()})
    return [SimpleNamespace(owner=owner) for owner in accessible], [SimpleNamespace(owner=owner) for owner in denied]


def foreign_catalog_names(inventory: EstateInventory) -> frozenset[str]:
    return frozenset(facts.name for facts in inventory.catalogs.values() if facts.scope == FOREIGN)


def platform_catalog_names(inventory: EstateInventory) -> frozenset[str]:
    return frozenset(facts.name for facts in inventory.catalogs.values() if facts.scope == PLATFORM)


def census_owners(inventory: EstateInventory) -> Mapping[str, frozenset[str]]:
    """Each foreign catalog's owners, the catalog's own included."""

    return {facts.name: facts.all_owners() for facts in inventory.foreign()}


__all__ = [
    "DEFUNCT",
    "LIVE",
    "assert_information_schema_owners",
    "assert_inventory_binds",
    "assert_live_catalogs",
    "assert_platform_catalogs",
    "assignment_rows",
    "binding_evidence",
    "binding_workspace_ids",
    "census_owners",
    "classify_workspace",
    "defunct_assignments",
    "foreign_catalog_names",
    "owner_objects",
    "platform_catalog_names",
]
