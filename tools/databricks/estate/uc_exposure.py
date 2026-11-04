"""Observed Unity Catalog exposures for the agent-runtime and agent-proxy foreign audits.

``audit_agent_runtime_foreign_uc_access`` reads the audited principal's
effective privileges on every foreign catalog and everything below it, each
privilege with the principals and parents it comes from, and refuses any.
``UcExposureScope.residual`` removes what that identity's matcher accepts
exactly and returns the rest, which the audit still refuses.

A direct grant on a securable is one tuple: ``uc_grant``, the securable type,
id and full name, the grantee, the exact privilege set that grantee holds
there, ``direct``, and the catalog's binding (``OPEN`` with no workspace ids, or
``ISOLATED`` with its bound workspace ids). A catalog has no immutable id in the
API, so its id is its name. Once a grant is accepted on a catalog or schema,
the same grantee's privileges inherited from it below are accepted while they
are a subset of what was accepted there: the exact set was judged where the
grant is. Any other inherited privilege is its own tuple, with source
``inherited:<TYPE>/<full name>``.

The scope never touches a source the caller already expects (the platform's
``information_schema`` grants), so a reviewed baseline cannot be absorbed into
an entry and then miss its own exact check.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from threading import Lock

from tools.databricks.estate.accepted_exposure import UC_GRANT, Exposure, ExposureMatcher

# (principal, inherited_from_type, inherited_from_name), as agent_runtime_uc_inventory reads it.
PrivilegeSource = tuple[str, str, str]
_ID_FIELDS = {
    "schema": "schema_id",
    "table": "table_id",
    "function": "function_id",
    "volume": "volume_id",
}


def securable_id(item: object, *, securable_type: str, full_name: str) -> str:
    """The immutable id an entry names; a catalog's (and a model's) is its full name."""

    attribute = _ID_FIELDS.get(securable_type.lower())
    if attribute is None:
        return full_name
    value = getattr(item, attribute, None)
    return value.strip() if isinstance(value, str) else ""


@dataclass
class UcExposureScope:
    """One audit's accepted UC exposure for one identity, and the grants it accepted."""

    matcher: ExposureMatcher
    binding: Callable[[str], tuple[str, tuple[str, ...]]]
    _accepted: dict[tuple[str, str, str], frozenset[str]] = field(default_factory=dict, init=False)
    _lock: Lock = field(default_factory=Lock, init=False)

    def _accepts_direct(
        self, *, securable_type: str, full_name: str, item_id: str, grantee: str, privileges: frozenset[str]
    ) -> bool:
        isolation_mode, workspace_ids = self.binding(full_name.split(".", 1)[0])
        observed = Exposure(
            kind=UC_GRANT,
            securable_type=securable_type,
            securable_id=item_id,
            securable_name=full_name,
            grantee=grantee,
            privileges=privileges,
            source="direct",
            isolation_mode=isolation_mode,
            workspace_ids=workspace_ids,
        )
        if not self.matcher.accepts(observed):
            return False
        with self._lock:
            self._accepted[(grantee, securable_type, full_name)] = privileges
        return True

    def _accepts_inherited(
        self,
        *,
        securable_type: str,
        full_name: str,
        item_id: str,
        source: PrivilegeSource,
        privileges: frozenset[str],
    ) -> bool:
        grantee, parent_type, parent = source
        with self._lock:
            above = self._accepted.get((grantee, parent_type.upper(), parent))
        if above is not None and privileges <= above:
            return True
        isolation_mode, workspace_ids = self.binding(full_name.split(".", 1)[0])
        return self.matcher.accepts(
            Exposure(
                kind=UC_GRANT,
                securable_type=securable_type,
                securable_id=item_id,
                securable_name=full_name,
                grantee=grantee,
                privileges=privileges,
                source=f"inherited:{parent_type.upper()}/{parent}",
                isolation_mode=isolation_mode,
                workspace_ids=workspace_ids,
            )
        )

    def residual(
        self,
        *,
        securable_type: str,
        full_name: str,
        item_id: str,
        sources: Mapping[str, set[PrivilegeSource]],
        keep: Mapping[str, set[PrivilegeSource]] | None = None,
    ) -> dict[str, set[PrivilegeSource]]:
        """The effective privileges and sources this identity's matcher does not accept."""

        expected = keep or {}
        by_source: dict[PrivilegeSource, set[str]] = {}
        for privilege, items in sources.items():
            for source in items:
                if source not in expected.get(privilege, set()):
                    by_source.setdefault(source, set()).add(privilege)
        kind = securable_type.upper()
        accepted: set[PrivilegeSource] = set()
        for source, privileges in by_source.items():
            grantee, parent_type, _parent = source
            held = frozenset(privileges)
            if parent_type:
                ok = self._accepts_inherited(
                    securable_type=kind, full_name=full_name, item_id=item_id, source=source, privileges=held
                )
            else:
                ok = self._accepts_direct(
                    securable_type=kind, full_name=full_name, item_id=item_id, grantee=grantee, privileges=held
                )
            if ok:
                accepted.add(source)
        residual = {
            privilege: {source for source in items if source not in accepted}
            for privilege, items in sources.items()
        }
        return {privilege: items for privilege, items in residual.items() if items}


__all__ = ["PrivilegeSource", "UcExposureScope", "securable_id"]
