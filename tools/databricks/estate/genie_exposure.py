"""Observed Genie-space exposures, built one way for the pre-stop probe and the audits.

The pre-stop Genie clause (``prestop_genie_grants``) and the post-stop Genie
audits (``agent_runtime_access``) read the same ACL entries. Both turn each
entry into accepted-exposure tuples here, so for one identity and one grant
they refuse or accept alike (pre/post parity).

One ACL entry can hold several levels from several places: a direct grant on
the space and grants inherited from parent folders. Each distinct source is
its own exposure, ``direct`` or ``inherited:<object>`` (the ACL's
``inherited_from_object``, such as ``/directories/<id>``), with the exact set
of levels that source grants. A space is a workspace object, so its binding is
``ISOLATED`` to the one workspace id it lives in. An inherited level without a
named parent object yields ``inherited:`` with an empty path, which no entry
can name, so it always refuses.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from tools.clients.shapes import DIRECT_SOURCE, INHERITED_SOURCE_PREFIX
from tools.databricks.estate.accepted_exposure import (
    GENIE_SPACE,
    ISOLATED,
    WORKSPACE_PERMISSION,
    Exposure,
)

_GRANTEE_FIELDS = ("group_name", "service_principal_name", "user_name")


def _field(value: object, name: str) -> object:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _text(value: object) -> str:
    return str(getattr(value, "value", value) or "").strip()


def _items(value: object) -> list[object]:
    # The same contract as agent_runtime_access._items: a malformed collection fails.
    if value is None:
        return []
    if not isinstance(value, list):
        raise RuntimeError("Databricks ACL inventory returned a malformed collection")
    return value


@dataclass(frozen=True)
class EntryLevel:
    """One permission level of an ACL entry and the objects it is inherited from."""

    level: str
    inherited: bool
    inherited_from: tuple[str, ...] = ()

    @property
    def source(self) -> str:
        if not self.inherited:
            return DIRECT_SOURCE
        return INHERITED_SOURCE_PREFIX + ",".join(sorted(self.inherited_from))


def entry_levels(entry: object) -> tuple[EntryLevel, ...]:
    """Every permission in an ACL entry, with its ``inherited_from_object`` source."""

    levels: list[EntryLevel] = []
    for permission in _items(_field(entry, "all_permissions")):
        inherited = _field(permission, "inherited") is True
        sources = tuple(
            source
            for source in (_text(value) for value in _items(_field(permission, "inherited_from_object")))
            if source
        )
        levels.append(
            EntryLevel(
                level=_text(_field(permission, "permission_level")),
                inherited=inherited,
                inherited_from=sources if inherited else (),
            )
        )
    return tuple(levels)


def entry_grantee(entry: object) -> str:
    """The group, service principal or user an ACL entry grants to."""

    for name in _GRANTEE_FIELDS:
        grantee = _text(_field(entry, name))
        if grantee:
            return grantee
    return ""


def genie_exposures(
    levels: Iterable[EntryLevel],
    *,
    space_id: str,
    title: str,
    grantee: str,
    workspace_id: str,
) -> tuple[Exposure, ...]:
    """One exposure per distinct source, each with the exact levels that source grants."""

    by_source: dict[str, set[str]] = {}
    for level in levels:
        by_source.setdefault(level.source, set()).add(level.level)
    return tuple(
        Exposure(
            kind=WORKSPACE_PERMISSION,
            securable_type=GENIE_SPACE,
            securable_id=space_id,
            securable_name=title,
            grantee=grantee,
            privileges=frozenset(privileges),
            source=source,
            isolation_mode=ISOLATED,
            workspace_ids=(workspace_id,),
        )
        for source, privileges in sorted(by_source.items())
    )


def entry_exposures(entry: object, *, space_id: str, title: str, workspace_id: str) -> tuple[Exposure, ...]:
    """The exposures one raw ACL entry grants its grantee on one space."""

    return genie_exposures(
        entry_levels(entry),
        space_id=space_id,
        title=title,
        grantee=entry_grantee(entry),
        workspace_id=workspace_id,
    )


__all__ = ["EntryLevel", "entry_exposures", "entry_grantee", "entry_levels", "genie_exposures"]
