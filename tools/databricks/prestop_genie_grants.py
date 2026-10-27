"""Genie ACL reads for the pre-stop deploy probe (``deploy_prestop_probes``).

The global Genie audits (``agent_runtime_access._assert_no_genie_access`` and
``_assert_exact_genie_can_run``) refuse every Genie space that grants any
permission to a group an audited service principal belongs to, and the
verifier audit (``--forbid-all-genie``) refuses every grant to the verifier
itself. These helpers read every admin-visible Genie space's ACL once and list
the grants those audits would refuse after step 0a. They perform only GET/list
calls and change no exemption rule.

A grant names its space by id only, never by title: the admin sees every
team's spaces on a shared workspace, and the refusal reaches deploy stderr and
CI logs, so another team's space title is never carried or printed.

Each listed permission keeps where it comes from. A direct grant can be removed
on the space; an inherited one (``inherited_from_object``, usually a workspace
folder such as ``/Workspace/Shared``) cannot, and changing the parent folder's
ACL can affect far more than this deploy, so the remedy differs by kind.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from databricks.sdk.errors import NotFound, ResourceDoesNotExist
from tools.databricks.agent_runtime_access import (
    _field,
    _genie_spaces,
    _items,
    _permission_levels,
    _principal_entry,
    _text,
)

SHARED_FOLDER = "/Workspace/Shared"
_DIRECTORY_PREFIX = "/directories/"
DIRECT_GROUP_REMEDY = (
    "Remove each direct group grant on the space itself, with the space owner's consent."
)
INHERITED_GRANT_REMEDY = (
    "An inherited grant cannot be removed on the space: with the space owner's consent, "
    "move the space out of the named parent folder into one that does not grant that "
    "group (or let the owner trash a space nobody needs). Change the parent folder's ACL "
    "only after that folder owner's governance review; /Workspace/Shared grants 'users' "
    "by default, and changing that affects every workspace user's shared content."
)
GROUP_MEMBERSHIP_REMEDY = (
    "Alternatively remove the service principal from the listed group (not possible for "
    "the system 'users' group, which every workspace principal joins)."
)
NEVER_WIDEN = "Then re-run. Never widen the audits' exemptions instead."
DIRECT_VERIFIER_REMEDY = (
    "Remove each direct grant to the verifier service principal on the space itself, "
    "with the space owner's consent."
)
INHERITED_VERIFIER_REMEDY = (
    "An inherited verifier grant cannot be removed on the space: remove the verifier "
    "service principal's own grant on the named parent folder with that folder owner's "
    "consent (it changes only the verifier's access, to everything in that folder), or, "
    "with the space owner's consent, move the space out of that folder."
)
VERIFIER_NEEDS_NONE = "The verifier needs no Genie access. Then re-run."


@dataclass(frozen=True)
class GrantedLevel:
    """One permission level on a Genie space and the object it comes from."""

    level: str
    inherited: bool
    inherited_from: tuple[str, ...] = ()

    def describe(self, folders: Mapping[str, str]) -> str:
        if not self.inherited:
            return f"{self.level} (direct on the space)"
        sources = [
            f"{source}, which is {folders[source]}" if source in folders else source
            for source in self.inherited_from
        ]
        return f"{self.level} (inherited from {' and '.join(sources) or 'an unnamed parent object'})"


def _granted_levels(entry: object) -> tuple[GrantedLevel, ...]:
    """Every permission in an ACL entry, with its ``inherited_from_object`` source."""

    granted: list[GrantedLevel] = []
    for permission in _items(_field(entry, "all_permissions")):
        inherited = _field(permission, "inherited") is True
        sources = tuple(
            source
            for source in (
                _text(value) for value in _items(_field(permission, "inherited_from_object"))
            )
            if source
        )
        granted.append(
            GrantedLevel(
                level=_text(_field(permission, "permission_level")),
                inherited=inherited,
                inherited_from=sources if inherited else (),
            )
        )
    return tuple(granted)


@dataclass(frozen=True)
class _SpaceGrant:
    space_id: str
    granted: tuple[GrantedLevel, ...]

    @property
    def levels(self) -> tuple[str, ...]:
        return tuple(grant.level for grant in self.granted)

    @property
    def inherited(self) -> bool:
        """Whether every level is inherited, so nothing can be removed on the space."""

        return all(grant.inherited for grant in self.granted)

    @property
    def has_direct(self) -> bool:
        return any(not grant.inherited for grant in self.granted)

    @property
    def has_inherited(self) -> bool:
        return any(grant.inherited for grant in self.granted)

    def _held(self, folders: Mapping[str, str] | None) -> str:
        return ", ".join(grant.describe(folders or {}) for grant in self.granted)


@dataclass(frozen=True)
class GenieGroupGrant(_SpaceGrant):
    group: str
    principals: tuple[str, ...]

    def describe(self, folders: Mapping[str, str] | None = None) -> str:
        return (
            f"Genie space {self.space_id}: group "
            f"{self.group!r} holds {self._held(folders)}; reaches "
            f"{', '.join(self.principals)}"
        )


@dataclass(frozen=True)
class VerifierGenieGrant(_SpaceGrant):
    application_id: str

    def describe(self, folders: Mapping[str, str] | None = None) -> str:
        return (
            f"Genie space {self.space_id}: verifier service "
            f"principal {self.application_id!r} holds {self._held(folders)}"
        )


def genie_group_remedy(grants: Iterable[GenieGroupGrant]) -> str:
    """The remedy for the listed group grants, by kind: direct, inherited or both."""

    listed = list(grants)
    parts = [DIRECT_GROUP_REMEDY] if any(grant.has_direct for grant in listed) else []
    if any(grant.has_inherited for grant in listed):
        parts.append(INHERITED_GRANT_REMEDY)
    return " ".join([*parts, GROUP_MEMBERSHIP_REMEDY, NEVER_WIDEN])


def verifier_genie_remedy(grants: Iterable[VerifierGenieGrant]) -> str:
    """The remedy for the listed verifier grants, by kind: direct, inherited or both."""

    listed = list(grants)
    parts = [DIRECT_VERIFIER_REMEDY] if any(grant.has_direct for grant in listed) else []
    if any(grant.has_inherited for grant in listed):
        parts.append(INHERITED_VERIFIER_REMEDY)
    return " ".join([*parts, VERIFIER_NEEDS_NONE])


def shared_folder_names(workspace: Any, grants: Iterable[_SpaceGrant]) -> dict[str, str]:
    """Name ``/Workspace/Shared`` when a listed grant is inherited from a folder.

    Only read when some grant is inherited from a ``/directories/<id>`` object.
    A missing folder adds no name; any other read error propagates.
    """

    sources = {
        source
        for grant in grants
        for level in grant.granted
        for source in level.inherited_from
    }
    if not any(source.startswith(_DIRECTORY_PREFIX) for source in sources):
        return {}
    try:
        status = workspace.workspace.get_status(SHARED_FOLDER)
    except (NotFound, ResourceDoesNotExist):
        return {}
    object_id = _text(_field(status, "object_id"))
    return {f"{_DIRECTORY_PREFIX}{object_id}": SHARED_FOLDER} if object_id else {}


GenieAcl = tuple[str, object]


def genie_acls(workspace: Any) -> list[GenieAcl]:
    """Read every admin-visible Genie space's ACL once: ``(space id, ACL)``.

    The listing's titles are dropped here, so no refusal can print one.
    """

    acls: list[GenieAcl] = []
    for space_id in sorted(_genie_spaces(workspace)):
        try:
            permissions = workspace.api_client.do(
                "GET", f"/api/2.0/permissions/genie/{space_id}"
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"cannot inspect Genie ACL for {space_id!r}: {exc}") from exc
        acls.append((space_id, permissions))
    return acls


def genie_group_grants(
    workspace: Any,
    *,
    effective_groups: Mapping[str, frozenset[str]],
    acls: list[GenieAcl] | None = None,
) -> list[GenieGroupGrant]:
    """List every Genie group grant that reaches an audited service principal."""

    if not effective_groups:
        raise ValueError("at least one audited service principal is required")
    grants: list[GenieGroupGrant] = []
    for space_id, permissions in genie_acls(workspace) if acls is None else acls:
        for entry in _items(_field(permissions, "access_control_list")):
            group = _text(_field(entry, "group_name"))
            levels = _permission_levels(entry)
            if not group or not levels:
                continue
            reached = tuple(
                sorted(label for label, groups in effective_groups.items() if group in groups)
            )
            if reached:
                grants.append(
                    GenieGroupGrant(
                        space_id=space_id,
                        granted=_granted_levels(entry),
                        group=group,
                        principals=reached,
                    )
                )
    return grants


def verifier_genie_grants(
    acls: list[GenieAcl], *, verifier_application_id: str
) -> list[VerifierGenieGrant]:
    """List every Genie grant to the verifier, which ``--forbid-all-genie`` refuses."""

    application = verifier_application_id.strip()
    if not application:
        raise ValueError("the verifier application ID is required")
    grants: list[VerifierGenieGrant] = []
    for space_id, permissions in acls:
        entry = _principal_entry(permissions, application_id=application)
        levels = _permission_levels(entry) if entry is not None else []
        if levels:
            grants.append(
                VerifierGenieGrant(
                    space_id=space_id,
                    granted=_granted_levels(entry),
                    application_id=application,
                )
            )
    return grants
