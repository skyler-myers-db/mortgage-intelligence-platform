"""The read-only plan of the Unity Catalog ownership handover (M2-PR8, cutover step 7).

The handover moves the install's governed objects from the legacy owner (the
identity that runs it: the person who deployed the install by hand) to the
data-owner group, ``<MIP_IDENTITY_PREFIX>-data-owners`` (plan section 4.3).
This module only reads. It enumerates the governed catalog
(``MIP_DEFAULT_CATALOG``): the catalog, its schemas, tables, views, volumes,
functions and registered models, and plans an owner change for each object
the legacy owner owns, leaves first, so the caller never loses a parent it
still needs. The Lakebase sync catalog (``MIP_LAKEBASE_SYNC_CATALOG``) is
planned at catalog level only: what a managed-online catalog holds follows
its database and its sync pipelines, which spike S3 has not rehearsed.

Never touched, each refused explicitly (``Refusal.code``):

* the **metastore** (``metastore``): no change of any kind names it;
* a **Delta Sharing** catalog (``share_catalog``), a **foreign** catalog or
  table (``foreign_catalog``, ``foreign_securable``) and a **system** catalog
  (``system_catalog``);
* an object the **System user** owns: never a change; a change from or to it
  refuses (``system_owned``);
* the users, account users and admins vocabulary as the target.

**Pipeline-managed datasets** (a streaming table, a materialized view, or any
table a pipeline publishes) are excluded until spike S4 proves ``ALTER OWNER``
on them survives the pipeline's run_as change. The plan lists each with its
owner, and the private report suggests the interim pair the client's profile
must carry (``MIP_UC_INTERIM_OWNER_PAIRS_JSON``, at most 14 days). A
configured pair that has expired refuses the plan (``interim_pair_expired``).

Every change is one typed Unity Catalog update with only ``owner`` set
(``api``); ``sql`` is the equivalent statement, for review. The plan's sha256
covers every change, every pipeline dataset and the counts left alone, so an
object created, dropped or re-owned in between refuses the apply.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from tools.clients.handover_journal import plan_sha256
from tools.clients.shapes import is_vocabulary
from tools.databricks.uc_interim_owner_pairs import (
    EXPIRED,
    MAX_AHEAD,
    InterimOwnerPairs,
    canonical_pairs,
)

SCHEMA = "mip.uc-ownership-handover.plan.v1"
DIGEST_DOMAIN = "mip-uc-ownership-handover-plan"
SYSTEM_USER = "System user"
GOVERNED, SYNC = "governed", "sync"
# Apply order: leaves first, the catalog last (revert runs the reverse).
ORDER = (
    "FUNCTION",
    "REGISTERED_MODEL",
    "VOLUME",
    "VIEW",
    "TABLE",
    "SCHEMA",
    "CATALOG",
)
# information_schema.tables.table_type -> the change kind.
TABLE_KINDS = {"MANAGED": "TABLE", "EXTERNAL": "TABLE", "VIEW": "VIEW", "METRIC_VIEW": "VIEW"}
PIPELINE_TYPES = frozenset({"STREAMING_TABLE", "MATERIALIZED_VIEW"})
CATALOG_TYPES = {"MANAGED_CATALOG": GOVERNED, "MANAGED_ONLINE_CATALOG": SYNC}
SYSTEM_CATALOGS = frozenset({"system", "hive_metastore", "samples", "__databricks_internal"})
# The SQL keyword of each change kind, and the REST collection its update PATCHes.
SQL_KEYWORD = {
    "CATALOG": "CATALOG",
    "SCHEMA": "SCHEMA",
    "TABLE": "TABLE",
    "VIEW": "VIEW",
    "VOLUME": "VOLUME",
    "FUNCTION": "FUNCTION",
}
API_COLLECTION = {
    "CATALOG": "catalogs",
    "SCHEMA": "schemas",
    "TABLE": "tables",
    "VIEW": "tables",
    "VOLUME": "volumes",
    "FUNCTION": "functions",
    "REGISTERED_MODEL": "models",
}


class HandoverRefused(RuntimeError):
    """A change the handover must never make; the message names no object or owner."""


@dataclass(frozen=True)
class Refusal:
    code: str
    where: str  # a kind and index, never a name
    reason: str


def _text(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").strip()


def _quote(name: str) -> str:
    return ".".join(f"`{part.replace('`', '``')}`" for part in name.split("."))


def change(kind: str, full_name: str, *, prior: str, owner: str) -> dict[str, str]:
    """One owner change, refused for the metastore, the System user or an unreviewed kind."""

    if kind == "METASTORE":
        raise HandoverRefused("the handover never changes the metastore")
    if kind not in API_COLLECTION:
        raise HandoverRefused("the handover has no reviewed change for this kind")
    if SYSTEM_USER.casefold() in {prior.casefold(), owner.casefold()}:
        raise HandoverRefused("the handover never moves an object from or to the System user")
    if is_vocabulary(owner) or not owner or not prior or owner == prior:
        raise HandoverRefused("the handover's target must be one exact principal other than the owner")
    keyword = SQL_KEYWORD.get(kind)
    return {
        "key": f"{kind}:{full_name}",
        "kind": kind,
        "full_name": full_name,
        "prior_owner": prior,
        "new_owner": owner,
        "api": f"PATCH /api/2.1/unity-catalog/{API_COLLECTION[kind]}/{full_name} "
        f'{{"owner":"{owner}"}}',
        "sql": f"ALTER {keyword} {_quote(full_name)} OWNER TO `{owner}`" if keyword else "",
    }


@dataclass
class UcPlan:
    document: dict[str, Any]
    refusals: list[Refusal] = field(default_factory=list)
    suggested_pairs: list[dict[str, str]] = field(default_factory=list)

    @property
    def sha256(self) -> str:
        return plan_sha256(self.document, domain=DIGEST_DOMAIN)

    @property
    def changes(self) -> list[dict[str, str]]:
        return list(self.document["changes"])

    def public(self) -> dict[str, Any]:
        """Codes, counts and the digest: safe for a public log."""

        kinds: dict[str, int] = {}
        for item in self.document["changes"]:
            kinds[item["kind"]] = kinds.get(item["kind"], 0) + 1
        return {
            "plan_sha256": self.sha256,
            "changes": len(self.document["changes"]),
            "changes_by_kind": dict(sorted(kinds.items())),
            "pipeline_managed": len(self.document["pipeline_managed"]),
            "left_alone": dict(self.document["left_alone"]),
            "refusals": [{"code": refusal.code, "where": refusal.where} for refusal in self.refusals],
        }

    def report(self) -> dict[str, Any]:
        """The private report: the document, the refusals and the suggested interim pairs.

        ``interim_owner_pairs_json`` is the same pairs as the single-line value
        ``MIP_UC_INTERIM_OWNER_PAIRS_JSON`` takes, for the owner to review.
        """

        return {
            **self.document,
            "plan_sha256": self.sha256,
            "refusals": [vars(refusal) for refusal in self.refusals],
            "suggested_interim_pairs": self.suggested_pairs,
            "interim_owner_pairs_json": canonical_pairs(self.suggested_pairs),
        }


@dataclass(frozen=True)
class Securable:
    kind: str  # a change kind, or a pipeline/foreign/unreviewed marker
    full_name: str
    owner: str
    table_type: str = ""
    pipeline_id: str = ""


def _catalog_refusal(item: object, name: str) -> tuple[str, str] | None:
    kind = _text(getattr(item, "catalog_type", None)).split(".")[-1].upper()
    if kind == "DELTASHARING_CATALOG" or _text(getattr(item, "share_name", None)) or _text(
        getattr(item, "provider_name", None)
    ):
        return "share_catalog", "a Delta Sharing catalog is the provider's, never the install's"
    if kind == "FOREIGN_CATALOG" or (
        _text(getattr(item, "connection_name", None)) and kind != "MANAGED_ONLINE_CATALOG"
    ):
        return "foreign_catalog", "a foreign catalog mirrors another system's objects"
    if kind in {"SYSTEM_CATALOG", "INTERNAL_CATALOG"} or name in SYSTEM_CATALOGS:
        return "system_catalog", "a system catalog belongs to the platform"
    if kind not in CATALOG_TYPES:
        return "unreviewed_catalog", "the catalog type has no reviewed handover"
    return None


def _tables(workspace: Any, catalog: str, schema: str) -> Iterator[Securable]:
    for item in workspace.tables.list(catalog_name=catalog, schema_name=schema):
        table_type = _text(getattr(item, "table_type", None)).split(".")[-1].upper()
        pipeline = _text(getattr(item, "pipeline_id", None))
        if pipeline or table_type in PIPELINE_TYPES:
            kind = "PIPELINE"
        elif table_type == "FOREIGN":
            kind = "FOREIGN"
        else:
            kind = TABLE_KINDS.get(table_type, "UNREVIEWED")
        yield Securable(kind, _text(getattr(item, "full_name", None)), _text(getattr(item, "owner", None)), table_type, pipeline)


def _children(workspace: Any, catalog: str, schema: str) -> Iterator[Securable]:
    yield from _tables(workspace, catalog, schema)
    for kind, listing in (
        ("VOLUME", workspace.volumes.list(catalog_name=catalog, schema_name=schema)),
        ("FUNCTION", workspace.functions.list(catalog_name=catalog, schema_name=schema)),
        ("REGISTERED_MODEL", workspace.registered_models.list(catalog_name=catalog, schema_name=schema)),
    ):
        for item in listing:
            yield Securable(kind, _text(getattr(item, "full_name", None)), _text(getattr(item, "owner", None)))


def compute(
    workspace: Any,
    *,
    catalog: str,
    sync_catalog: str | None,
    target: str,
    legacy_owner: str,
    pairs: InterimOwnerPairs | None = None,
    interim_expires: date | None = None,
) -> UcPlan:
    """Enumerate read-only and plan every change; refusals refuse the whole plan."""

    refusals: list[Refusal] = []
    left = {"other_owner": 0, "system_owned": 0, "follows_catalog": 0, "sync_catalog_child": 0}
    changes: list[dict[str, str]] = []
    pipeline: list[dict[str, str]] = []
    if is_vocabulary(target) or not target:
        refusals.append(Refusal("target_vocabulary", "target", "the target must be the data-owner group"))
    if target.casefold() == legacy_owner.casefold():
        refusals.append(Refusal("target_is_legacy", "target", "the legacy owner already is the target"))
    catalogs = [(catalog, GOVERNED)] + ([(sync_catalog, SYNC)] if sync_catalog else [])
    if refusals:
        document = {"schema": SCHEMA, "target_owner": target, "legacy_owner": legacy_owner, "changes": [], "pipeline_managed": [], "left_alone": left}
        return UcPlan(document, refusals)
    for index, (name, role) in enumerate(catalogs):
        item = workspace.catalogs.get(name)
        refused = _catalog_refusal(item, name)
        if refused is None and CATALOG_TYPES[
            _text(getattr(item, "catalog_type", None)).split(".")[-1].upper()
        ] != role:
            refused = ("catalog_role", f"the {role} catalog has the other catalog type")
        if refused is not None:
            refusals.append(Refusal(refused[0], f"catalog.{index}", refused[1]))
            continue
        securables = [Securable("CATALOG", name, _text(getattr(item, "owner", None)))]
        for schema in workspace.schemas.list(catalog_name=name):
            schema_name = _text(getattr(schema, "name", None))
            full = _text(getattr(schema, "full_name", None)) or f"{name}.{schema_name}"
            owner = _text(getattr(schema, "owner", None))
            if role == SYNC:
                left["sync_catalog_child"] += 1
                continue
            securables.append(Securable("SCHEMA", full, owner))
            if schema_name == "information_schema":
                continue
            securables.extend(_children(workspace, name, schema_name))
        for position, securable in enumerate(securables):
            where = f"{securable.kind.lower()}.{index}.{position}"
            if securable.full_name.split(".", 1)[0] != name:
                refusals.append(Refusal("outside_catalog", where, "a listed object is outside its catalog"))
            elif securable.owner.casefold() == SYSTEM_USER.casefold():
                left["system_owned"] += 1
            elif securable.owner.casefold() != legacy_owner.casefold():
                left["other_owner"] += 1
            elif securable.kind == "SCHEMA" and securable.full_name.endswith(".information_schema"):
                left["follows_catalog"] += 1
            elif securable.kind == "PIPELINE":
                pipeline.append({"full_name": securable.full_name, "owner": securable.owner, "table_type": securable.table_type})
                if pairs and pairs.verdict(full_name=securable.full_name, owner=securable.owner) == EXPIRED:
                    refusals.append(Refusal("interim_pair_expired", where, "the dataset's interim owner pair expired; re-own it"))
            elif securable.kind == "FOREIGN":
                refusals.append(Refusal("foreign_securable", where, "a foreign table mirrors another system's object"))
            elif securable.kind == "UNREVIEWED":
                refusals.append(Refusal("unreviewed_type", where, "the table type has no reviewed handover"))
            else:
                try:
                    changes.append(change(securable.kind, securable.full_name, prior=securable.owner, owner=target))
                except HandoverRefused as exc:
                    refusals.append(Refusal("refused_change", where, str(exc)))
    changes.sort(key=lambda item: (ORDER.index(item["kind"]), item["full_name"]))
    pipeline.sort(key=lambda item: item["full_name"])
    document = {
        "schema": SCHEMA,
        "target_owner": target,
        "legacy_owner": legacy_owner,
        "catalogs": [{"name": name, "role": role} for name, role in catalogs],
        "changes": changes,
        "pipeline_managed": pipeline,
        "left_alone": left,
    }
    expires = (interim_expires or (datetime.now(UTC) + MAX_AHEAD).date()).isoformat()
    suggested = [{"full_name": row["full_name"], "owner": row["owner"], "expires_at": expires} for row in pipeline]
    return UcPlan(document, refusals, suggested)


def reverse_order(entries: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Revert order: the catalog first, leaves last (the exact inverse of ``ORDER``)."""

    return sorted(entries, key=lambda item: (-ORDER.index(item["kind"]), item["full_name"]))


def suggested_expiry(days: int) -> date:
    if not 1 <= days <= MAX_AHEAD.days:
        raise HandoverRefused("an interim owner pair expires 1 to 14 days ahead")
    return (datetime.now(UTC) + timedelta(days=days)).date()


__all__ = [
    "DIGEST_DOMAIN",
    "ORDER",
    "SYSTEM_USER",
    "HandoverRefused",
    "Refusal",
    "UcPlan",
    "change",
    "compute",
    "reverse_order",
    "suggested_expiry",
]
