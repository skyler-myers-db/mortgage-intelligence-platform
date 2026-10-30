"""Read-only live checks: the deploy is talking to the profile's workspace as its deployer.

``python -m tools.clients.live_checks`` runs right after scripts/deploy.sh
binds the deployer's workspace auth and before any other Databricks call
(plan §4.5, checks 1 and 2). In order:

1. **host**: the host the deployer client resolved (the CLI's host) equals the
   profile's ``DATABRICKS_HOST``. This is a local comparison; a mismatch stops
   here, before any request is sent.
2. **workspace id**: the workspace the host serves is ``MIP_EXPECT_WORKSPACE_ID``.
3. **metastore id**: the workspace's current metastore assignment is
   ``MIP_EXPECT_METASTORE_ID``, for that same workspace.
4. **deployer**: the authenticated principal (``current_user``) is
   ``MIP_IDENTITY_IDS_JSON.deployer``.

Steps 2-4 are three GETs (SCIM ``Me`` twice and the current metastore
assignment); every refusal among them is reported. Once all four hold, the
read-only install checks run (tools/clients/install_checks.py, M1-PR3):
adoption, the ``mip.install_key`` install marker, the Cotality share, the
storage root and the account principal's roles. They never run against a
workspace or as a principal that is not the profile's. Exit 0 when every
check holds, 2 otherwise; a read error refuses, never reads as absent. A
refusal names the check, never a value: these are identifying fields, and the
line may reach a public CI log. Each id read from the workspace is registered
as a runner mask the moment it is read (``redact.emit_mask``).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools.clients import install_checks
from tools.clients.environment import from_local, resolve
from tools.clients.errors import ProfileRefusal
from tools.clients.redact import emit_mask
from tools.clients.schema import ClientProfile

REPO = Path(__file__).resolve().parents[2]


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


def check(profile: ClientProfile, client: Any, account: Callable[[], Any] | None = None) -> list[str]:
    """Every refusal, host first; no request is sent when the host differs.

    The install checks run only once the workspace and the principal are
    proven the profile's.
    """

    lines = host_refusals(profile, str(getattr(client.config, "host", "") or ""))
    if lines:
        return lines
    lines = identity_refusals(profile, read_live_identity(client))
    if lines:
        return lines
    return install_checks.check(profile, client, account)


def _default_client() -> Any:
    from tools.databricks.workspace_auth import deployment_workspace_client

    return deployment_workspace_client()


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    env_file: Path | None = None,
    client: Callable[[], Any] = _default_client,
    account: Callable[[], Any] | None = None,
) -> int:
    if argv:
        print("usage: python -m tools.clients.live_checks (no arguments)", file=sys.stderr)
        return 2
    try:
        raw = from_local(os.environ if environ is None else environ, env_file or REPO / ".env.local")
        profile = resolve(raw)
        lines = check(profile, client(), account)
    except ProfileRefusal as refusal:
        lines = list(refusal.lines)
    except Exception as exc:  # the class only: an SDK message can carry a host or an id
        lines = [f"live checks could not read the workspace: {type(exc).__name__}"]
    if lines:
        print("client live checks refused:", file=sys.stderr)
        for line in lines:
            print(f"  - {line}", file=sys.stderr)
        return 2
    print(
        "client live checks ok: host, workspace id, metastore id and deployer are the profile's; "
        "adoption, install marker, Cotality share, storage root and account principal hold"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
