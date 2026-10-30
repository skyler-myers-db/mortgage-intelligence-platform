"""The App ACL preview of the pre-lease bundle plan gate (M0-PR5).

A governed run ends by converging the existing App's direct ACL to the runtime
release phase (``converge_app_release_access --mode runtime``): direct managers
are kept and every other direct grant is replaced by CAN_USE for the normal,
operator2 and admin service principals. A business user's direct CAN_USE is
therefore dropped, and an ACL the converger cannot read (an inherited
non-manager grant, an unexpected level, a lifecycle identity that also manages
the App) fails it after step 0a. The gate previews that convergence before the
lease with the converger's own reader (``INSPECT``) and plan (``PLAN``), so the
operator sees each change in the full report, and an ACL the converger would
refuse refuses here as ``app_acl``. Reads only: ``apps.get`` and
``apps.get_permissions``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from types import MappingProxyType
from typing import Any

from databricks.sdk.errors import NotFound, ResourceDoesNotExist
from tools.databricks.converge_app_release_access import (
    _inspect_acl,
    _validated_inputs,
    planned_release_acl,
)
from tools.databricks.deploy_plan_report import AclGrant, AppAclPreview, PlanReport, Refusal

# The converger's own ACL reader and release plan (identity, not copies).
INSPECT = _inspect_acl
PLAN = planned_release_acl
FINAL_MODE = "runtime"
REMEDY = (
    "the release access converger cannot read or converge this App ACL and would fail "
    "after step 0a; fix the ACL the full report names (an inherited non-manager grant, an "
    "unexpected level, or a release identity that also manages the App), then re-run"
)


def app_acl_preview(
    workspace: Any, *, app_name: str, identities: dict[str, str]
) -> AppAclPreview:
    """Diff the App's direct ACL against the runtime phase's planned ACL."""

    try:
        workspace.apps.get(app_name)
    except (NotFound, ResourceDoesNotExist):
        return AppAclPreview("app_absent")
    current = INSPECT(workspace.apps.get_permissions(app_name))
    planned = PLAN(current, mode=FINAL_MODE, identities=identities)
    keep: list[AclGrant] = []
    remove: list[AclGrant] = []
    change: list[tuple[AclGrant, AclGrant]] = []
    for (kind, principal), level in sorted(current.direct.items()):
        before = AclGrant(kind, principal, level)
        target = planned.get((kind, principal))
        if target is None:
            remove.append(before)
        elif target == level:
            keep.append(before)
        else:
            change.append((before, AclGrant(kind, principal, target)))
    add = [
        AclGrant(kind, principal, level)
        for (kind, principal), level in sorted(planned.items())
        if (kind, principal) not in current.direct
    ]
    code = "changes" if add or remove or change else "unchanged"
    return AppAclPreview(code, tuple(keep), tuple(add), tuple(remove), tuple(change))


def with_app_acl_preview(
    report: PlanReport,
    *,
    workspace_factory: Callable[[], Any],
    app_name: str,
    release_probe_application_id: str,
    normal_application_id: str,
    operator2_application_id: str,
    admin_application_id: str,
) -> PlanReport:
    """The report with its App ACL preview; an ACL the converger would refuse refuses."""

    try:
        _app, identities = _validated_inputs(
            app_name=app_name,
            mode=FINAL_MODE,
            release_probe_application_id=release_probe_application_id,
            normal_application_id=normal_application_id,
            operator2_application_id=operator2_application_id,
            admin_application_id=admin_application_id,
        )
        preview = app_acl_preview(workspace_factory(), app_name=app_name, identities=identities)
    except Exception as exc:  # noqa: BLE001 - an unreadable ACL never previews as unchanged
        refusal = Refusal(
            code="app_acl",
            resource=f"{report.app_resource}.permissions",
            remedy=REMEDY,
            detail=MappingProxyType({"reason": f"{type(exc).__name__}: {exc}"}),
        )
        return replace(
            report, app_acl=AppAclPreview("refused"), refusals=(*report.refusals, refusal)
        )
    return replace(report, app_acl=preview)
