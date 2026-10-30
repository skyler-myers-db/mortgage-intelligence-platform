"""Pre-stop probe: owner-equivalent grantees on the governed catalog, schemas and space (G12).

Ownership is not the only way to control a governed object. ``MANAGE`` and
``ALL_PRIVILEGES`` let a grantee re-grant, alter or drop the governed catalog
and its schemas, ``EXTERNAL_USE_SCHEMA`` lets it read their data outside Unity
Catalog, and ``CAN_EDIT`` or ``CAN_MANAGE`` on the governed Genie space let it
rewrite the instructions and SQL the App's answers come from (attack G12). Each
grantee of one of those on the governed catalog, any of its schemas or the
governed Genie space (the signed record's, or the one the provisioner adopts by
its exact title) must therefore be an approved principal:
``MIP_UC_APPROVED_OWNER_PRINCIPALS`` plus the deployer plus
``MIP_GENIE_MANAGER_PRINCIPALS``, compared case-insensitively. Anyone else
refuses before step 0a, and the remedy names the exact revoke or the exact
approval to add.

The probe never revokes or grants: it only reads ``grants.get`` (direct
assignments on the catalog and on each schema, so every grant is named where it
can be removed) and the space's permissions. It fails closed: a catalog that is
absent has no grantees, but any list, grants or permissions read error refuses
and is never read as "no grantees". The workspace ``admins`` group's Genie
``CAN_MANAGE`` is the platform's own on every workspace object, cannot be
revoked, and the deployer is required to be in that group, so it is the one
grant the probe accepts by name; a Unity Catalog grant to any principal,
``admins`` included, needs an approval.

No post-stop or mutating step checks owner-equivalent grantees today, so
``ASSERT`` is this probe's own assertion; it reuses ``parse_approved_owner_principals``,
the parser the owner policy reads ``MIP_UC_APPROVED_OWNER_PRINCIPALS`` with.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from databricks.sdk.errors import NotFound, ResourceDoesNotExist
from tools.databricks.agent_runtime_access import _field, _items, _text
from tools.databricks.converge_campaign_treatment_access import _privilege_name
from tools.databricks.prestop_genie_grants import (
    GenieAcl,
    GrantedLevel,
    _granted_levels,
    shared_folder_names,
)
from tools.databricks.prestop_refusals import OWNER_EQUIVALENT, PrestopRefusal
from tools.databricks.uc_owner_policy import parse_approved_owner_principals

UC_OWNER_EQUIVALENT = frozenset({"MANAGE", "ALL_PRIVILEGES", "EXTERNAL_USE_SCHEMA"})
GENIE_OWNER_EQUIVALENT = frozenset({"CAN_EDIT", "CAN_MANAGE"})
PLATFORM_GENIE_MANAGER = "admins"
_GENIE_PRINCIPAL_FIELDS = ("user_name", "service_principal_name", "group_name")
_FIELD_KIND = {
    "user_name": "user",
    "service_principal_name": "service principal",
    "group_name": "group",
}
APPROVAL_UC = "MIP_UC_APPROVED_OWNER_PRINCIPALS"
APPROVAL_GENIE = "MIP_GENIE_MANAGER_PRINCIPALS"


@dataclass(frozen=True)
class OwnerEquivalentGrant:
    """One owner-equivalent grant: where it is held, by whom, and from which object."""

    surface: str  # "catalog", "schema" or "genie"
    securable: str
    principal: str
    kind: str
    privileges: tuple[str, ...]
    # The Genie levels as the permissions API reports them; empty for Unity Catalog.
    granted: tuple[GrantedLevel, ...] = ()

    @property
    def inherited_from(self) -> tuple[str, ...]:
        """The parent objects when every level is inherited (nothing to remove on the space)."""

        if not self.granted or not all(level.inherited for level in self.granted):
            return ()
        sources = sorted({source for level in self.granted for source in level.inherited_from})
        return tuple(sources) or ("an unnamed parent object",)

    def describe(self, folders: Mapping[str, str]) -> str:
        held = ", ".join(self.privileges)
        if self.surface == "genie":
            where = f"Genie space {self.securable}"
        else:
            where = f"{self.surface} {self.securable}"
        source = ""
        if self.inherited_from:
            names = [folders.get(item, item) for item in self.inherited_from]
            source = f" (inherited from {' and '.join(names)})"
        return f"{self.kind} {self.principal!r} holds {held} on {where}{source}"

    def remedy(self) -> str:
        approval = APPROVAL_GENIE if self.surface == "genie" else APPROVAL_UC
        if self.surface == "genie":
            if self.inherited_from:
                revoke = (
                    "the grant is inherited, so it cannot be removed on the space: move the "
                    "space out of that parent folder, or have the folder owner remove "
                    f"{self.principal!r}'s grant there"
                )
            else:
                revoke = (
                    f"remove {self.principal!r}'s {'/'.join(self.privileges)} on Genie space "
                    f"{self.securable} (its Share dialog)"
                )
        else:
            keyword = "CATALOG" if self.surface == "catalog" else "SCHEMA"
            quoted = ".".join(f"`{part}`" for part in self.securable.split("."))
            privileges = ", ".join(item.replace("_", " ") for item in self.privileges)
            revoke = f"REVOKE {privileges} ON {keyword} {quoted} FROM `{self.principal}`"
        return (
            f"Revoke it ({revoke}) with the grantee's owner's consent, or, after the owner's "
            f"review, approve that exact principal in {approval}."
        )


class OwnerEquivalentRefusal(RuntimeError):
    """Unapproved owner-equivalent grantees; ``grants`` names each one."""

    def __init__(self, grants: tuple[OwnerEquivalentGrant, ...]) -> None:
        super().__init__(f"{len(grants)} unapproved owner-equivalent grant(s)")
        self.grants = grants


def approved_principals(
    *, approved_owners: str, deployer: str, genie_managers: str
) -> frozenset[str]:
    """``MIP_UC_APPROVED_OWNER_PRINCIPALS`` ∪ the deployer ∪ ``MIP_GENIE_MANAGER_PRINCIPALS``."""

    names = {
        *parse_approved_owner_principals(approved_owners),
        *parse_approved_owner_principals(genie_managers),
    }
    if deployer.strip():
        names.add(deployer.strip())
    return frozenset(name.casefold() for name in names)


def _uc_grants(workspace: Any, securable_type: str, full_name: str) -> list[OwnerEquivalentGrant]:
    grants: list[OwnerEquivalentGrant] = []
    page_token: str | None = None
    seen: set[str] = set()
    while True:
        response = workspace.grants.get(
            securable_type, full_name, page_token=page_token, max_results=0
        )
        for assignment in _items(_field(response, "privilege_assignments")):
            principal = _field(assignment, "principal")
            if not isinstance(principal, str) or not principal or principal != principal.strip():
                raise RuntimeError("the grants API returned a noncanonical principal")
            held = sorted(
                {_privilege_name(item) for item in _items(_field(assignment, "privileges"))}
                & UC_OWNER_EQUIVALENT
            )
            if held:
                grants.append(
                    OwnerEquivalentGrant(
                        surface=securable_type,
                        securable=full_name,
                        principal=principal,
                        kind="principal",
                        privileges=tuple(held),
                    )
                )
        token = _text(_field(response, "next_page_token"))
        if not token:
            return grants
        if token in seen:
            raise RuntimeError("the grants API repeated a pagination token")
        seen.add(token)
        page_token = token


def uc_owner_equivalent_grants(workspace: Any, *, catalog: str) -> list[OwnerEquivalentGrant]:
    """Direct owner-equivalent grants on the governed catalog and each of its schemas."""

    try:
        workspace.catalogs.get(catalog)
    except (NotFound, ResourceDoesNotExist):
        return []
    grants = _uc_grants(workspace, "catalog", catalog)
    for schema in workspace.schemas.list(catalog_name=catalog):
        full_name = _text(_field(schema, "full_name"))
        if not full_name.startswith(f"{catalog}."):
            raise RuntimeError("the schema listing returned a schema outside the governed catalog")
        grants.extend(_uc_grants(workspace, "schema", full_name))
    return grants


def genie_owner_equivalent_grants(
    acls: Iterable[GenieAcl], *, space_id: str
) -> list[OwnerEquivalentGrant]:
    """``CAN_EDIT`` and ``CAN_MANAGE`` grants on the governed space, with their sources."""

    matches = [permissions for listed, permissions in acls if listed == space_id]
    if len(matches) != 1:
        raise RuntimeError(
            f"the governed Genie space {space_id} is not exactly once among the "
            "admin-visible spaces, so its managers cannot be read"
        )
    grants: list[OwnerEquivalentGrant] = []
    for entry in _items(_field(matches[0], "access_control_list")):
        identities = [
            (name, _text(_field(entry, name)))
            for name in _GENIE_PRINCIPAL_FIELDS
            if _text(_field(entry, name))
        ]
        if len(identities) != 1:
            raise RuntimeError("the Genie space ACL holds an entry without exactly one principal")
        field, principal = identities[0]
        levels = [
            level for level in _granted_levels(entry) if level.level in GENIE_OWNER_EQUIVALENT
        ]
        if not levels:
            continue
        grants.append(
            OwnerEquivalentGrant(
                surface="genie",
                securable=space_id,
                principal=principal,
                kind=_FIELD_KIND[field],
                privileges=tuple(sorted({level.level for level in levels})),
                granted=tuple(levels),
            )
        )
    return grants


def _platform_manager(grant: OwnerEquivalentGrant) -> bool:
    return (
        grant.surface == "genie"
        and grant.kind == "group"
        and grant.principal == PLATFORM_GENIE_MANAGER
    )


def assert_owner_equivalent_grantees(
    grants: Iterable[OwnerEquivalentGrant], *, approved: frozenset[str]
) -> None:
    """Raise ``OwnerEquivalentRefusal`` naming every grantee outside ``approved``."""

    refused = tuple(
        grant
        for grant in grants
        if grant.principal.casefold() not in approved and not _platform_manager(grant)
    )
    if refused:
        raise OwnerEquivalentRefusal(refused)


ASSERT = assert_owner_equivalent_grantees


def owner_equivalent_refusals(
    workspace: Any,
    *,
    catalog: str,
    genie_space_id: str,
    acls: list[GenieAcl] | None,
    approved: frozenset[str],
) -> tuple[list[PrestopRefusal], list[str]]:
    """One refusal per unapproved grantee, or one for any read the probe could not finish."""

    notes: list[str] = []
    try:
        grants = uc_owner_equivalent_grants(workspace, catalog=catalog)
        if genie_space_id:
            if acls is None:
                raise RuntimeError("the Genie space ACLs were not read")
            grants.extend(genie_owner_equivalent_grants(acls, space_id=genie_space_id))
        else:
            notes.append("no governed Genie space exists yet; its managers are not audited")
        ASSERT(grants, approved=approved)
    except OwnerEquivalentRefusal as refusal:
        try:
            folders = shared_folder_names(workspace, refusal.grants)
        except Exception:  # noqa: BLE001 - the folder name is cosmetic; the refusal stands
            folders = {}
        return [
            PrestopRefusal(
                OWNER_EQUIVALENT,
                f"owner-equivalent grant outside the approved principals: "
                f"{grant.describe(folders)}. {grant.remedy()} Then re-run.",
            )
            for grant in refusal.grants
        ], notes
    except Exception as exc:  # noqa: BLE001 - never read a failed read as "no grantees"
        return [
            PrestopRefusal(
                OWNER_EQUIVALENT,
                f"cannot read the owner-equivalent grantees of the governed catalog, schemas "
                f"or Genie space: {exc}. The probe refuses rather than read an unreadable "
                "grant list as empty. Then re-run.",
            )
        ], notes
    return [], [*notes, "every owner-equivalent grantee is an approved principal"]

