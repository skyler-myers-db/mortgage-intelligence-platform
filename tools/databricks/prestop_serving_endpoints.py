"""Pre-stop probe: the App's serving-endpoint access is only on record-pinned endpoints.

During cutover, after step 0a, ``audit_global_m2m_access`` audits the App's
service principal across every visible serving endpoint with
``audit_global_serving_endpoint_access``: it may query the new Gateway, the
signed record's rollback Gateway and, when it holds direct or mixed access
there, the Supervisor the cutover replaces; any other access refuses. Two
leftovers trip it on a shared workspace, with the App already stopped: a
``CAN_QUERY`` on the legacy ``mip-agent-gateway`` that no record pins any more,
and one on the endpoint of a Supervisor renamed out of the canonical name (it
is no longer the one the cutover replaces).

This probe runs the same function (``ASSERT``) first, on the same reviewed
set: the signed record's Gateway plus the canonical Supervisor's endpoint (the
only Supervisor ``pin_journal`` accepts, so the only one the cutover can
replace). The record's own Supervisor endpoint is in that set only while its
Supervisor still holds the canonical name. The set is restricted to the
endpoints the App reaches, classified by the same exact-mode reader the
cutover uses (``MODE``); each is accepted in its exact legacy query mode.
With none, ``ASSERT_NONE`` proves no access at all. The new Gateway does not
exist yet. Group membership is the workspace-visible walk plus ``users``, so
this is an early refusal; the post-stop audit still reads the account plane.
Every call is a GET or a list.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from tools.databricks.prestop_refusals import SERVING_ENDPOINT, PrestopRefusal
from tools.databricks.serving_endpoint_acl import (
    audit_global_no_serving_endpoint_access,
    audit_global_serving_endpoint_access,
    inspect_exact_query_access_mode,
)

# The functions the cutover's App audit and its old-Supervisor mode read use.
ASSERT = audit_global_serving_endpoint_access
ASSERT_NONE = audit_global_no_serving_endpoint_access
MODE = inspect_exact_query_access_mode
_REMEDY = (
    "Remove the App service principal's direct or group access on that endpoint (its "
    "Permissions dialog), with the endpoint owner's consent: the App queries only its "
    "record-pinned Gateway. Then re-run."
)


def _visible_endpoint_names(workspace: Any) -> set[str]:
    names: set[str] = set()
    for endpoint in workspace.serving_endpoints.list():
        raw = endpoint.get("name") if isinstance(endpoint, dict) else getattr(endpoint, "name", "")
        name = str(raw or "").strip()
        if not name:
            raise RuntimeError("a visible serving endpoint has no name")
        names.add(name)
    return names


def serving_endpoint_refusals(
    workspace: Any,
    *,
    app_name: str,
    app_client_id: str,
    app_scim_id: str,
    app_groups: frozenset[str],
    pinned_endpoints: Iterable[str],
) -> tuple[list[PrestopRefusal], list[str]]:
    """Refuse App serving access the post-stop cutover audit would refuse."""

    if not app_client_id:
        return [], ["no App exists yet; it holds no serving-endpoint access"]
    pinned = sorted({name.strip() for name in pinned_endpoints if name and name.strip()})
    try:
        visible = _visible_endpoint_names(workspace)
        reached = [
            name
            for name in pinned
            if name in visible
            and MODE(
                workspace,
                app_name=app_name,
                endpoint_name=name,
                service_principal=app_client_id,
                service_principal_id=app_scim_id,
                effective_group_names=set(app_groups),
                legacy_pinned=True,
            )
            != "none"
        ]
        if reached:
            ASSERT(
                workspace,
                app_name=app_name,
                reviewed_endpoint_names=reached,
                service_principal=app_client_id,
                expected_permission_level="CAN_QUERY",
                service_principal_id=app_scim_id,
                effective_group_names=set(app_groups),
                legacy_pinned_endpoint_names=reached,
            )
        else:
            ASSERT_NONE(
                workspace,
                service_principal=app_client_id,
                service_principal_id=app_scim_id,
                effective_group_names=set(app_groups),
            )
    except Exception as exc:  # noqa: BLE001 - a refused or unreadable ACL refuses
        return [
            PrestopRefusal(
                SERVING_ENDPOINT,
                "the cutover's App serving-endpoint audit runs after step 0a stops the App "
                f"and refuses: {exc}. Record-pinned endpoints: {', '.join(pinned) or 'none'}. "
                f"{_REMEDY}",
            )
        ], []
    return [], [
        f"App serving access is limited to record-pinned endpoints ({len(reached)} reached)"
    ]
