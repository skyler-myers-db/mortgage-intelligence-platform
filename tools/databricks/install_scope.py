"""Workspace-global revokes refuse objects this install does not own (W5e C7c).

The isolated CI staging install shares the bundle's anchor workspace with the
live install. Root, name and variable isolation do not isolate identities:
``warehouse_acl.converge_exact_can_use`` removes an identity's direct access on
every OTHER warehouse, and the agent-proxy convergence revokes the proxy's
access on every endpoint outside its reviewed set. If the CI install's
identity were shared with the live install, either revoke would cut the live
App off.

So, with a non-empty install suffix (``BUNDLE_VAR_install_suffix``, exported by
scripts/deploy.sh's preflight only for the CI staging install), each of those
convergences first proves, read-only, that the identity holds no access on an
object this install does not own (a warehouse whose name lacks the suffix, an
endpoint outside the reviewed set), and otherwise exits 2 BEFORE any mutation.
An empty suffix (the live install, every operator deploy) changes nothing.
Refusals print counts only, never an object name or identity.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Collection, Mapping
from typing import Any

from databricks.sdk.errors import NotFound, ResourceDoesNotExist
from tools.databricks.serving_query_group_access import managed_query_group_name

INSTALL_SUFFIX_ENV = "BUNDLE_VAR_install_suffix"


class InstallScopeRefusal(SystemExit):
    """Exit 2 with a fixed, count-only reason; raised before any mutation."""

    def __init__(self, reason: str) -> None:
        print(f"[install-scope] refused, nothing changed: {reason}", file=sys.stderr)
        super().__init__(2)
        self.reason = reason


def active_install_suffix(env: Mapping[str, str] | None = None) -> str:
    return str((os.environ if env is None else env).get(INSTALL_SUFFIX_ENV) or "").strip()


def _holds_direct_entry(permissions: object, principal: str, group_name: str | None = None) -> bool:
    for entry in getattr(permissions, "access_control_list", None) or []:
        if str(getattr(entry, "service_principal_name", "") or "") == principal:
            return True
        if group_name and str(getattr(entry, "group_name", "") or "") == group_name:
            return True
    return False


def assert_warehouse_revokes_owned(
    client: Any,
    *,
    warehouses: Mapping[str, str],
    target: str,
    service_principal: str,
    suffix: str,
) -> None:
    """Refuse when the identity holds a direct entry on a non-target, non-suffixed warehouse."""

    unowned = sum(
        1
        for warehouse_id, name in sorted(warehouses.items())
        if warehouse_id != target
        and not name.endswith(suffix)
        and _holds_direct_entry(client.warehouses.get_permissions(warehouse_id), service_principal)
    )
    if unowned:
        raise InstallScopeRefusal(
            f"the identity holds access on {unowned} SQL warehouse(s) this install does not own; "
            "give the CI staging install its own identity"
        )


def assert_endpoint_revokes_owned(client: Any, *, reviewed: Collection[str], application_id: str) -> None:
    """Refuse when the proxy holds a direct or managed-group entry outside its reviewed endpoints."""

    unowned = 0
    for endpoint in client.serving_endpoints.list():
        name = str((endpoint.get("name") if isinstance(endpoint, dict) else getattr(endpoint, "name", "")) or "").strip()
        if not name or name in reviewed:
            continue
        try:
            endpoint_id = str(getattr(client.serving_endpoints.get(name), "id", "") or "").strip()
        except (NotFound, ResourceDoesNotExist):
            continue
        if not endpoint_id:
            continue  # a platform foundation endpoint carries no per-identity ACL here
        managed = managed_query_group_name(endpoint_id=endpoint_id, application_id=application_id)
        if _holds_direct_entry(client.serving_endpoints.get_permissions(endpoint_id), application_id, managed):
            unowned += 1
    if unowned:
        raise InstallScopeRefusal(
            f"the agent proxy holds access on {unowned} serving endpoint(s) outside this install's reviewed set; "
            "give the CI staging install its own proxy identity"
        )
