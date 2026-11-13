"""The identity half of the client live checks: the deploy talks to the profile's workspace as its deployer.

tools/clients/live_checks.py runs these first, before its install checks
(which reach the account principal), and refuses with every line they
return. tools/databricks/activation_prediction.py runs the same two
functions before its own reads, so a read-only measurement never runs against
a workspace or as a principal that is not the profile's. Workspace reads only:
three GETs (SCIM ``Me`` twice and the current metastore assignment); nothing
here reaches an account API. A refusal names the check, never a value; each
id read from the workspace is registered as a runner mask the moment it is
read (``redact.emit_mask``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tools.clients.redact import emit_mask
from tools.clients.schema import ClientProfile


@dataclass(frozen=True)
class LiveIdentity:
    """What the workspace says about itself and the caller, as plain strings."""

    host: str
    workspace_id: str
    metastore_id: str
    metastore_workspace_id: str
    principal: str


def _origin(value: str) -> str:
    return value.strip().rstrip("/").casefold()


def _same(left: str | None, right: str | None) -> bool:
    return bool(left and right) and str(left).strip().casefold() == str(right).strip().casefold()


def host_refusals(profile: ClientProfile, resolved_host: str) -> list[str]:
    if _origin(resolved_host) != _origin(profile.databricks_host):
        return ["DATABRICKS_HOST: the deployer's resolved workspace host is not the profile's"]
    return []


def identity_refusals(profile: ClientProfile, live: LiveIdentity) -> list[str]:
    lines = []
    if not _same(live.workspace_id, profile.expect_workspace_id):
        lines.append("MIP_EXPECT_WORKSPACE_ID: the workspace this host serves is not the profile's")
    if not _same(live.metastore_id, profile.expect_metastore_id):
        lines.append("MIP_EXPECT_METASTORE_ID: the workspace's current metastore is not the profile's")
    elif not _same(live.metastore_workspace_id, profile.expect_workspace_id):
        lines.append("MIP_EXPECT_METASTORE_ID: the metastore assignment names another workspace")
    if not _same(live.principal, profile.identity_ids.deployer):
        lines.append("MIP_IDENTITY_IDS_JSON.deployer: the authenticated principal is not the deployer")
    return lines


def read_live_identity(client: Any) -> LiveIdentity:
    """Three read-only GETs through ``client`` (a ``WorkspaceClient``)."""

    workspace_id = emit_mask(str(client.get_workspace_id()))
    assignment = client.metastores.current()
    metastore_id = emit_mask(str(getattr(assignment, "metastore_id", "") or ""))
    metastore_workspace_id = emit_mask(str(getattr(assignment, "workspace_id", "") or ""))
    principal = emit_mask(str(getattr(client.current_user.me(), "user_name", "") or ""))
    return LiveIdentity(
        host=str(getattr(client.config, "host", "") or ""),
        workspace_id=workspace_id,
        metastore_id=metastore_id,
        metastore_workspace_id=metastore_workspace_id,
        principal=principal,
    )


def identity_lines(profile: ClientProfile, client: Any) -> list[str]:
    """Every identity refusal, host first; no request is sent when the host differs."""

    lines = host_refusals(profile, str(getattr(client.config, "host", "") or ""))
    return lines or identity_refusals(profile, read_live_identity(client))
