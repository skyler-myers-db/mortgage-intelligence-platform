"""The owner's estate inventory: metastore-owner facts the agentic tier relies on (M4-PR1).

Without metastore ownership the deployer cannot see what an agent identity
might own where it cannot look: a catalog bound only to other workspaces, or
one whose USE_CATALOG was revoked (attack G7; whether an identity sees what
it owns without USE_CATALOG is spike S0(c), still owed, so the worst case is
assumed). The owner takes this inventory as the metastore's direct owner
(``tools/clients/estate_inventory.py``, read-only), at most 30 days before
the run that relies on it. It holds:

* ``issued_at``, the metastore id, the install workspace id and catalog;
* every catalog of the metastore, binding-denied ones included: its type,
  isolation mode, owner and workspace bindings, and, below each foreign
  catalog, how many schemas, tables, functions, volumes and registered models
  each principal owns (the ownership census; ``information_schema`` apart);
* every workspace assigned to the metastore, and an exact entry for each one
  the account plane no longer has (``defunct``): whether its assignments
  still list and the sha256 of exactly what they list;
* each gap: a foreign catalog whose children it could not read (bound only
  to workspaces it has no reader on, or a refused read). Any gap refuses.

The census names every owner, not only the agent identities: the audit judges
each identity's live aliases and groups against it, so a group joined after
the inventory was taken is still judged.

**Digest ack.** The owner hardware-key ``estate`` attestation (plan 4.2) is
deferred (M1-PR5), so the inventory is pinned by an exact digest instead: the
sha256 of the domain-separated canonical document, which names its purpose
(``estate``), so no other ack can satisfy it (``handover_journal.plan_sha256``).
A run is given it as ``MIP_ACK_ESTATE_INVENTORY``, a one-run shell export,
never a ``.env.local`` line; every consumer recomputes the digest from the
inventory immediately before relying on it. A digest is not a signature: it
proves the operator acknowledged this exact inventory, not who took it.

**Source.** ``MIP_ESTATE_INVENTORY_JSON`` (the GitHub Environment secret's
content) or ``MIP_ESTATE_INVENTORY_FILE`` (a regular file owned by the caller,
mode 0600, never a symlink), from the process environment only. The
inventory names other teams' catalogs and principals: no line here carries a
name, an owner or an id, only counts, codes and the digest.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from tools.clients.handover_journal import ack_state, canonical_json, plan_sha256
from tools.clients.redact import emit_mask
from tools.clients.shapes import DuplicateJsonKey, strict_json_loads

SCHEMA = "mip.estate-inventory.v1"
PURPOSE = "estate"
ACK_DOMAIN = "mip-estate-ack-v1"
INVENTORY_JSON_ENV = "MIP_ESTATE_INVENTORY_JSON"
INVENTORY_FILE_ENV = "MIP_ESTATE_INVENTORY_FILE"
ACK_ENV = "MIP_ACK_ESTATE_INVENTORY"
# Retired (M4-PR1): the inventory carries the reviewed binding-denied catalogs.
RETIRED_POLICY_ENV = "MIP_UC_FOREIGN_CATALOG_BINDING_POLICY"
MAX_AGE = timedelta(days=30)
# The reviewed bound on one deploy run (prestop_accepted_exposure.RUN_MARGIN):
# the pre-lease check judges age at its end, so no post-stop audit expires.
RUN_MARGIN = timedelta(hours=24)
# GitHub's limit on one secret's value.
SECRET_LIMIT_BYTES = 48 * 1024

INSTALL, PLATFORM, FOREIGN = "install", "platform", "foreign"
PLATFORM_CATALOGS = frozenset({"__databricks_internal", "samples", "system"})
OPEN, ISOLATED = "OPEN", "ISOLATED"
BINDING_TYPES = frozenset({"BINDING_TYPE_READ_ONLY", "BINDING_TYPE_READ_WRITE"})
CENSUS_TYPES = ("schema", "table", "function", "volume", "registered_model")
# Gap codes: schema vocabulary only, so a refusal line can carry them.
CATALOG_GAPS = frozenset({"binding_denied_unreadable", "read_refused"})
LISTED, NOT_FOUND = "listed", "not_found"
_TOP = frozenset(
    {"schema", "purpose", "issued_at", "metastore_id", "workspace_id", "install_catalog", "catalogs",
     "workspaces"}
)  # fmt: skip
_CATALOG = frozenset(
    {"catalog_type", "isolation_mode", "owner", "bindings", "scope", "gap", "owners",
     "information_schema_owners"}
)  # fmt: skip
_ISSUED = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
_DECIMAL = re.compile(r"[1-9][0-9]{0,18}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


class EstateInventoryRefusal(RuntimeError):
    """The estate inventory cannot be relied on; each line names a field and a rule."""

    def __init__(self, lines: Iterable[str]) -> None:
        self.lines = tuple(lines)
        super().__init__("estate inventory refused: " + "; ".join(self.lines))


@dataclass(frozen=True)
class CatalogFacts:
    """One catalog as the metastore owner read it."""

    name: str
    catalog_type: str
    isolation_mode: str
    owner: str
    bindings: tuple[tuple[str, str], ...]
    scope: str
    gap: str
    owners: Mapping[str, Mapping[str, int]]
    information_schema_owners: Mapping[str, int]

    def binding_denied(self, workspace_id: str) -> bool:
        return self.isolation_mode == ISOLATED and workspace_id not in {item[0] for item in self.bindings}

    @property
    def bound_workspace_ids(self) -> tuple[str, ...]:
        return tuple(sorted(item[0] for item in self.bindings))

    def all_owners(self) -> frozenset[str]:
        """The catalog's owner and every owner below it, ``information_schema`` included."""

        return frozenset({self.owner, *self.owners, *self.information_schema_owners})


@dataclass(frozen=True)
class DefunctWorkspace:
    """An assigned workspace the account plane no longer has, exactly as reviewed."""

    workspace_id: str
    assignments: str
    assignments_sha256: str


@dataclass(frozen=True)
class EstateInventory:
    """A parsed, closed-schema inventory and the digest its ack must equal."""

    document: Mapping[str, Any]
    issued_at: datetime
    metastore_id: str
    workspace_id: str
    install_catalog: str
    catalogs: Mapping[str, CatalogFacts]
    assigned_workspaces: frozenset[str]
    defunct: Mapping[str, DefunctWorkspace]
    digest: str = field(default="")

    def foreign(self) -> list[CatalogFacts]:
        return [item for name, item in sorted(self.catalogs.items()) if item.scope == FOREIGN]

    def binding_denied(self) -> list[CatalogFacts]:
        return [item for item in self.foreign() if item.binding_denied(self.workspace_id)]

    def accessible(self) -> list[CatalogFacts]:
        return [item for item in self.foreign() if not item.binding_denied(self.workspace_id)]

    def gap_count(self) -> int:
        return sum(1 for item in self.catalogs.values() if item.gap)

    def binding(self, catalog: str) -> tuple[str, tuple[str, ...]]:
        """A catalog's (isolation mode, bound workspace ids) for an exposure tuple."""

        item = self.catalogs.get(catalog)
        if item is None:
            return "", ()
        return item.isolation_mode, item.bound_workspace_ids if item.isolation_mode == ISOLATED else ()


def estate_digest(document: Mapping[str, Any]) -> str:
    """The ``estate`` ack: sha256 of the domain-separated canonical inventory, purpose named."""

    if document.get("purpose") != PURPOSE:
        raise ValueError("an estate digest covers an estate inventory only")
    return plan_sha256(document, domain=ACK_DOMAIN)


def assignments_digest(rows: Iterable[tuple[str, str, str, tuple[str, ...]]] | None) -> str:
    """sha256 of exactly what a defunct workspace's assignment listing returned, or its absence.

    Each row is ``(principal id, kind, name, permissions)``; ``None`` is a listing
    the account plane answered with RESOURCE_DOES_NOT_EXIST.
    """

    body: Any = None if rows is None else sorted([list(row[:3]) + [sorted(row[3])] for row in rows])
    return hashlib.sha256(b"mip-estate-defunct-assignments-v1\0" + canonical_json(body).encode("ascii")).hexdigest()


def _canonical_text(value: object) -> bool:
    return isinstance(value, str) and bool(value) and value == value.strip()


def _counts(value: object, keys: Iterable[str] | None, at: str, lines: list[str]) -> dict[str, int]:
    if not isinstance(value, dict):
        lines.append(f"{at}: must be an object")
        return {}
    allowed = None if keys is None else set(keys)
    result: dict[str, int] = {}
    for key, count in value.items():
        if (allowed is not None and key not in allowed) or (allowed is None and not _canonical_text(key)):
            lines.append(f"{at}: holds an unreviewed key")
        elif type(count) is not int or count <= 0:
            lines.append(f"{at}: counts are positive integers")
        else:
            result[key] = count
    return result


def _catalog(name: str, raw: object, *, install: str, lines: list[str]) -> CatalogFacts | None:
    at = "a catalog entry"
    if not _canonical_text(name):
        lines.append("a catalog name is empty or padded")
        return None
    if not isinstance(raw, dict) or set(raw) != _CATALOG:
        lines.append(f"{at}: has an invalid field set")
        return None
    scope = INSTALL if name == install else PLATFORM if name in PLATFORM_CATALOGS else FOREIGN
    mode, kind, owner = raw["isolation_mode"], raw["catalog_type"], raw["owner"]
    if raw["scope"] != scope:
        lines.append(f"{at}: scope does not match its name")
    if mode not in {OPEN, ISOLATED} or not _canonical_text(kind) or not _canonical_text(owner):
        lines.append(f"{at}: needs a canonical type, owner and an OPEN or ISOLATED mode")
    bindings: list[tuple[str, str]] = []
    if not isinstance(raw["bindings"], list):
        lines.append(f"{at}: bindings must be a list")
    else:
        for item in raw["bindings"]:
            if (
                not isinstance(item, dict)
                or set(item) != {"workspace_id", "binding_type"}
                or not isinstance(item["workspace_id"], str)
                or _DECIMAL.fullmatch(item["workspace_id"]) is None
                or item["binding_type"] not in BINDING_TYPES
            ):
                lines.append(f"{at}: has an invalid binding")
                continue
            bindings.append((item["workspace_id"], item["binding_type"]))
    if len({item[0] for item in bindings}) != len(bindings):
        lines.append(f"{at}: repeats a bound workspace")
    if mode == OPEN and bindings:
        lines.append(f"{at}: an OPEN catalog lists no bindings")
    gap = raw["gap"]
    if gap != "" and gap not in CATALOG_GAPS:
        lines.append(f"{at}: has an unreviewed gap code")
    owners: dict[str, dict[str, int]] = {}
    if not isinstance(raw["owners"], dict):
        lines.append(f"{at}: owners must be an object")
    else:
        for principal, counts in raw["owners"].items():
            if not _canonical_text(principal):
                lines.append(f"{at}: an owner is empty or padded")
                continue
            owners[principal] = _counts(counts, CENSUS_TYPES, f"{at} owner census", lines)
    information = _counts(raw["information_schema_owners"], None, f"{at} information_schema census", lines)
    if scope != FOREIGN and (owners or information or gap):
        lines.append(f"{at}: only a foreign catalog carries a census or a gap")
    return CatalogFacts(
        name=name,
        catalog_type=kind if isinstance(kind, str) else "",
        isolation_mode=mode if isinstance(mode, str) else "",
        owner=owner if isinstance(owner, str) else "",
        bindings=tuple(sorted(bindings)),
        scope=scope,
        gap=gap if isinstance(gap, str) else "",
        owners=owners,
        information_schema_owners=information,
    )


def _workspaces(raw: object, *, install_workspace: str, lines: list[str]) -> tuple[frozenset[str], dict[str, DefunctWorkspace]]:
    if not isinstance(raw, dict) or set(raw) != {"assigned", "defunct"}:
        lines.append("workspaces: has an invalid field set")
        return frozenset(), {}
    assigned = raw["assigned"]
    if not isinstance(assigned, list) or any(
        not isinstance(item, str) or _DECIMAL.fullmatch(item) is None for item in assigned
    ):
        lines.append("workspaces.assigned: must list decimal workspace ids")
        assigned = []
    if len(set(assigned)) != len(assigned):
        lines.append("workspaces.assigned: repeats a workspace")
    if install_workspace not in assigned:
        lines.append("workspaces.assigned: does not list the install workspace")
    defunct: dict[str, DefunctWorkspace] = {}
    if not isinstance(raw["defunct"], dict):
        lines.append("workspaces.defunct: must be an object")
        return frozenset(assigned), {}
    for workspace_id, entry in raw["defunct"].items():
        if workspace_id not in assigned or workspace_id == install_workspace:
            lines.append("workspaces.defunct: names a workspace that is not an assigned foreign one")
            continue
        if (
            not isinstance(entry, dict)
            or set(entry) != {"assignments", "assignments_sha256"}
            or entry["assignments"] not in {LISTED, NOT_FOUND}
            or not isinstance(entry["assignments_sha256"], str)
            or _SHA256.fullmatch(entry["assignments_sha256"]) is None
        ):
            lines.append("workspaces.defunct: an entry is not an exact assignments record")
            continue
        defunct[workspace_id] = DefunctWorkspace(workspace_id, entry["assignments"], entry["assignments_sha256"])
    return frozenset(assigned), defunct


def parse_inventory(text: str) -> EstateInventory:
    """Parse the closed-schema inventory; refuse with every violation at once."""

    try:
        document = strict_json_loads(text)
    except DuplicateJsonKey:
        raise EstateInventoryRefusal(["the inventory repeats a key in one JSON object"]) from None
    except ValueError:
        raise EstateInventoryRefusal(["the inventory is not JSON"]) from None
    if not isinstance(document, dict) or set(document) != _TOP:
        raise EstateInventoryRefusal(["the inventory has an invalid top-level field set"])
    lines: list[str] = []
    if document["schema"] != SCHEMA or document["purpose"] != PURPOSE:
        lines.append(f"the inventory is not a {SCHEMA} document for purpose {PURPOSE}")
    issued_text = document["issued_at"]
    issued_at = datetime.min.replace(tzinfo=UTC)
    if not isinstance(issued_text, str) or _ISSUED.fullmatch(issued_text) is None:
        lines.append("issued_at: must be YYYY-MM-DDTHH:MM:SSZ")
    else:
        try:
            issued_at = datetime.strptime(issued_text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        except ValueError:
            lines.append("issued_at: is not a real UTC time")
    metastore, workspace, install = document["metastore_id"], document["workspace_id"], document["install_catalog"]
    if not _canonical_text(metastore):
        lines.append("metastore_id: must be a canonical id")
    if not isinstance(workspace, str) or _DECIMAL.fullmatch(workspace) is None:
        lines.append("workspace_id: must be a decimal workspace id")
    if not _canonical_text(install):
        lines.append("install_catalog: must be a canonical catalog name")
    catalogs: dict[str, CatalogFacts] = {}
    if not isinstance(document["catalogs"], dict):
        lines.append("catalogs: must be an object keyed by catalog name")
    else:
        for name, raw in document["catalogs"].items():
            facts = _catalog(name, raw, install=str(install), lines=lines)
            if facts is not None:
                catalogs[name] = facts
    assigned, defunct = _workspaces(document["workspaces"], install_workspace=str(workspace), lines=lines)
    if lines:
        raise EstateInventoryRefusal(lines)
    return EstateInventory(
        document=document,
        issued_at=issued_at,
        metastore_id=str(metastore),
        workspace_id=str(workspace),
        install_catalog=str(install),
        catalogs=catalogs,
        assigned_workspaces=assigned,
        defunct=defunct,
        digest=estate_digest(document),
    )


def summary(inventory: EstateInventory, *, now: datetime) -> str:
    """One public-safe line: counts, the age in days and the digest; never a name."""

    foreign = inventory.foreign()
    denied = len(inventory.binding_denied())
    owners = {owner for item in foreign for owner in item.all_owners()}
    age = max(0, (now - inventory.issued_at).days)
    return (
        f"estate inventory: {len(inventory.catalogs)} catalogs ({len(foreign) - denied} accessible foreign, "
        f"{denied} binding-denied), {len(owners)} distinct foreign owners, {inventory.gap_count()} gaps, "
        f"{len(inventory.assigned_workspaces)} assigned workspaces ({len(inventory.defunct)} defunct); "
        f"taken {age} d ago; sha256 {inventory.digest}"
    )


def retired_policy_lines(environ: Mapping[str, str]) -> list[str]:
    """The retired binding policy refuses when set: it was an exact contract the inventory replaces."""

    if not environ.get(RETIRED_POLICY_ENV, "").strip():
        return []
    return [
        f"{RETIRED_POLICY_ENV} is retired: the owner's estate inventory ({INVENTORY_JSON_ENV} or "
        f"{INVENTORY_FILE_ENV}, acknowledged by {ACK_ENV}) now carries the reviewed binding-denied "
        "catalogs; unset it"
    ]


def _read_source(environ: Mapping[str, str]) -> str:
    inline = environ.get(INVENTORY_JSON_ENV, "")
    path_text = environ.get(INVENTORY_FILE_ENV, "").strip()
    if inline.strip() and path_text:
        raise EstateInventoryRefusal([f"set exactly one of {INVENTORY_JSON_ENV} and {INVENTORY_FILE_ENV}"])
    if inline.strip():
        return inline
    if not path_text:
        raise EstateInventoryRefusal(
            [
                "the agentic tier needs the owner's estate inventory: take it as the metastore owner "
                "(python -m tools.clients.estate_inventory), then provide it as "
                f"{INVENTORY_JSON_ENV} (an environment secret) or {INVENTORY_FILE_ENV} (a 0600 file)"
            ]
        )
    path = Path(path_text)
    try:
        info = path.lstat()
    except OSError:
        raise EstateInventoryRefusal([f"{INVENTORY_FILE_ENV}: cannot be read"]) from None
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_mode & 0o077
    ):
        raise EstateInventoryRefusal(
            [f"{INVENTORY_FILE_ENV}: must be a regular file the caller owns, mode 0600, never a symlink"]
        )
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise EstateInventoryRefusal([f"{INVENTORY_FILE_ENV}: cannot be read"]) from None


def read_inventory(environ: Mapping[str, str] | None = None) -> EstateInventory:
    """Parse the inventory as configured, judging nothing: for a plan's counts and digest only."""

    return parse_inventory(_read_source(os.environ if environ is None else environ))


def load_estate_inventory(
    environ: Mapping[str, str] | None = None,
    *,
    metastore_id: str,
    workspace_id: str,
    install_catalog: str,
    now: datetime | None = None,
    margin: timedelta = timedelta(0),
) -> EstateInventory:
    """Read, parse and judge the inventory for this deployed metastore, right before it is used.

    ``margin`` judges the age at ``now + margin``: the pre-lease check passes
    ``RUN_MARGIN`` so an inventory cannot expire between it and the run's last
    post-stop audit. Refuses on the retired policy, a missing or ambiguous
    source, a missing, malformed or different ack, an inventory issued in the
    future or more than 30 days before, another metastore, workspace or
    install catalog, and any gap.
    """

    env = os.environ if environ is None else environ
    moment = now or datetime.now(UTC)
    retired = retired_policy_lines(env)
    if retired:
        raise EstateInventoryRefusal(retired)
    inventory = parse_inventory(_read_source(env))
    for value in (inventory.metastore_id, inventory.workspace_id, *sorted(inventory.assigned_workspaces)):
        emit_mask(value, environ=env)
    lines: list[str] = []
    acknowledged = env.get(ACK_ENV, "").strip()
    state = ack_state(acknowledged, inventory.digest)
    if state == "absent":
        lines.append(f"the estate inventory is not acknowledged: export {ACK_ENV}=<its sha256> for this run only")
    elif _SHA256.fullmatch(acknowledged) is None:
        lines.append(f"{ACK_ENV} is not a 64-hex sha256")
    elif state != "matched":
        lines.append(f"{ACK_ENV} is not this inventory's estate digest; re-review it")
    if inventory.issued_at > moment:
        lines.append("issued_at is in the future; take the inventory again")
    elif moment + margin - inventory.issued_at > MAX_AGE:
        within = " (at the end of this run's margin)" if margin else ""
        lines.append(f"the inventory is older than 30 days{within}; take it again")
    if inventory.metastore_id != metastore_id:
        lines.append("the inventory was taken on another metastore")
    if inventory.workspace_id != workspace_id:
        lines.append("the inventory was taken for another install workspace")
    if inventory.install_catalog != install_catalog:
        lines.append("the inventory names another install catalog")
    gaps = inventory.gap_count()
    if gaps:
        lines.append(f"the inventory has {gaps} gaps; fix each read, then take it again")
    if lines:
        raise EstateInventoryRefusal(lines)
    return inventory


__all__ = [
    "ACK_ENV",
    "INVENTORY_FILE_ENV",
    "INVENTORY_JSON_ENV",
    "MAX_AGE",
    "RETIRED_POLICY_ENV",
    "RUN_MARGIN",
    "CatalogFacts",
    "DefunctWorkspace",
    "EstateInventory",
    "EstateInventoryRefusal",
    "assignments_digest",
    "estate_digest",
    "load_estate_inventory",
    "parse_inventory",
    "read_inventory",
    "retired_policy_lines",
    "summary",
]
