"""Read the deployer's direct-engine bundle state, read-only (M1-PR3).

The ``client`` target's direct deployment engine keeps its remote state at
``<bundle root>/state/resources.json``, where the bundle root is
``/Workspace/Users/<deployer>/.bundle/mortgage-intelligence-platform/<client
key>`` (tools/databricks/app_release_contract.bundle_root). The document is
``{"state_version": 2, ..., "state": {"resources.<kind>.<key>": {"__id__":
<live id>, ...}}}``; an entry binds that bundle resource to a live object.

Three readers use it before the signed lease:

* tools/clients/install_checks.py: the adoption check. An App that exists
  while this deployer's state does not bind ``apps.mip_app`` to it is an
  install this deployer never adopted.
* scripts/deploy.sh, through ``python -m tools.databricks.bundle_state
  warehouse-id``: the SQL warehouse the bundle created
  (``sql_warehouses.mip_serverless_sql``), instead of an operator-supplied
  ``DATABRICKS_WAREHOUSE_ID``. It prints the id on stdout, registered as a
  runner mask first, or nothing when the state binds no warehouse yet (a
  first install, before the bundle apply creates it).
* scripts/deploy.sh, through ``python -m tools.databricks.bundle_state
  bootstrap-warehouse-id``, only when the state binds no warehouse (B1): the
  client profile's ``MIP_BOOTSTRAP_WAREHOUSE_ID``, the warehouse a first
  install's steps borrow until its bundle apply creates the bundle's own. It
  proves, read-only (one state download, one ``apps.get``, one
  ``warehouses.get``), that this is a first install (the state binds neither
  the warehouse nor ``apps.mip_app``, no App of the profile's name exists, the
  profile names no legacy root) and that the warehouse is live and PRO (pro or
  serverless: the first install creates the governed Genie space on it). It
  prints the configured id, registered as a runner mask first, or refuses.

Fail closed: only a typed not-found is "no state". Any other read error, a
document that is not this shape, an unknown state version or an entry without
a live id refuses (exit 2), never reads as absent. Refusals name the check,
never a value.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from databricks.sdk.errors import NotFound, ResourceDoesNotExist

if TYPE_CHECKING:
    from tools.clients.schema import ClientProfile

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.databricks.app_release_contract import api_path, bundle_root  # noqa: E402

STATE_FILE = "state/resources.json"
STATE_VERSION = 2
APP_KEY = "resources.apps.mip_app"
WAREHOUSE_KEY = "resources.sql_warehouses.mip_serverless_sql"
_CHUNK = 1 << 20
BOOTSTRAP_FIELD = "MIP_BOOTSTRAP_WAREHOUSE_ID"
COMMANDS = ("warehouse-id", "bootstrap-warehouse-id")
_ABSENT = (NotFound, ResourceDoesNotExist)
_GONE_STATES = frozenset({"DELETED", "DELETING"})


class BundleStateError(RuntimeError):
    """The deployer's bundle state could not be read or is not the reviewed shape."""


def state_path(*, deployer: str, client_key: str) -> str:
    """The ``/Workspace/...`` path of the client target's remote state."""

    root = bundle_root(principal=deployer.strip().lower(), target="client", root_leaf=client_key)
    return f"{root}/{STATE_FILE}"


def read_state(workspace: Any, path: str) -> dict[str, Any] | None:
    """The state document, or ``None`` when the deployer has no state yet."""

    try:
        stream = workspace.workspace.download(api_path(path))
    except (NotFound, ResourceDoesNotExist):
        return None
    chunks = []
    while chunk := stream.read(_CHUNK):
        chunks.append(chunk)
    try:
        document = json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise BundleStateError("the deployer's bundle state is not valid JSON") from None
    if not isinstance(document, dict) or not isinstance(document.get("state"), dict):
        raise BundleStateError("the deployer's bundle state has no state object")
    if document.get("state_version") != STATE_VERSION:
        raise BundleStateError("the deployer's bundle state version is not the reviewed one (2)")
    return document


def bound_id(document: Mapping[str, Any] | None, key: str) -> str | None:
    """The live id the state binds ``key`` to; ``None`` when it binds nothing."""

    if document is None:
        return None
    entry = document["state"].get(key)
    if entry is None:
        return None
    live_id = entry.get("__id__") if isinstance(entry, dict) else None
    if not isinstance(live_id, str) or not live_id.strip():
        raise BundleStateError(f"the deployer's bundle state entry {key} has no live id")
    return live_id.strip()


def _profile_state(
    workspace: Any, environ: Mapping[str, str], env_file: Path
) -> tuple[ClientProfile, dict[str, Any] | None]:
    """The resolved client profile and its deployer's state document."""

    from tools.clients.environment import from_local, resolve

    profile = resolve(from_local(environ, env_file))
    document = read_state(workspace, state_path(deployer=profile.identity_ids.deployer, client_key=profile.client))
    return profile, document


def _enum_text(value: Any) -> str:
    """An SDK enum's value (or a plain string), stripped; ``""`` when absent."""

    return str(getattr(value, "value", value) or "").strip()


def bootstrap_warehouse_id(workspace: Any, profile: ClientProfile, document: Mapping[str, Any] | None) -> str:
    """The profile's bootstrap SQL warehouse, proven for a first install; else ``BundleStateError``.

    GET-only. Every refusal names the check, never a value: the deployer's
    host, the App's name and the warehouse id stay out of the message.
    """

    configured = profile.bootstrap_warehouse_id
    if not configured:
        raise BundleStateError(f"the client profile sets no {BOOTSTRAP_FIELD}")
    if profile.legacy_bundle_root:
        raise BundleStateError(
            f"{BOOTSTRAP_FIELD} is for a first install: an adopted install keeps the warehouse its legacy state binds"
        )
    if bound_id(document, WAREHOUSE_KEY) is not None:
        raise BundleStateError(
            "the deployer's bundle state binds the bundle's own SQL warehouse: a bootstrap warehouse is not read"
        )
    if bound_id(document, APP_KEY) is not None:
        raise BundleStateError(
            "the deployer's bundle state binds the App: an installed or adopted App keeps its state's warehouse"
        )
    try:
        workspace.apps.get(name=profile.app_name)
    except _ABSENT:
        pass
    except Exception as exc:  # the class only: an SDK message can carry a host or a name
        raise BundleStateError(f"could not prove that the App does not exist yet: {type(exc).__name__}") from None
    else:
        raise BundleStateError(f"the App exists: {BOOTSTRAP_FIELD} is for a first install only")
    try:
        warehouse = workspace.warehouses.get(id=configured)
    except _ABSENT:
        raise BundleStateError(f"{BOOTSTRAP_FIELD} names no SQL warehouse the deployer can read") from None
    except Exception as exc:  # the class only: an SDK message can carry the id
        raise BundleStateError(f"could not read the {BOOTSTRAP_FIELD} warehouse: {type(exc).__name__}") from None
    if _enum_text(getattr(warehouse, "id", None)) != configured:
        raise BundleStateError(f"the warehouse read for {BOOTSTRAP_FIELD} is another one")
    state = _enum_text(getattr(warehouse, "state", None))
    if not state or state in _GONE_STATES:
        raise BundleStateError(f"the {BOOTSTRAP_FIELD} warehouse is deleted, being deleted or has no state")
    if _enum_text(getattr(warehouse, "warehouse_type", None)) != "PRO":
        raise BundleStateError(
            f"the {BOOTSTRAP_FIELD} warehouse is not pro or serverless: the first install creates the "
            "governed Genie space on it"
        )
    return configured


def _default_client() -> Any:
    from tools.databricks.workspace_auth import deployment_workspace_client

    return deployment_workspace_client()


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    env_file: Path | None = None,
    client: Any = None,
) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or args[0] not in COMMANDS:
        print(f"usage: python -m tools.databricks.bundle_state {{{'|'.join(COMMANDS)}}}", file=sys.stderr)
        return 2
    from tools.clients.errors import ProfileRefusal
    from tools.clients.redact import emit_mask

    try:
        workspace = client if client is not None else _default_client()
        profile, document = _profile_state(
            workspace, os.environ if environ is None else environ, env_file or REPO / ".env.local"
        )
        if args == ["bootstrap-warehouse-id"]:
            print(emit_mask(bootstrap_warehouse_id(workspace, profile, document)))
            return 0
        warehouse_id = bound_id(document, WAREHOUSE_KEY)
    except ProfileRefusal as refusal:
        print("[bundle-state] client profile refused:", file=sys.stderr)
        for line in refusal.lines:
            print(f"  - {line}", file=sys.stderr)
        return 2
    except BundleStateError as exc:
        print(f"[bundle-state] {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # the class only: an SDK message can carry a host or an id
        print(f"[bundle-state] could not read the deployer's bundle state: {type(exc).__name__}", file=sys.stderr)
        return 2
    if warehouse_id is None:
        print("[bundle-state] the deployer's bundle state binds no SQL warehouse yet", file=sys.stderr)
        return 0
    print(emit_mask(warehouse_id))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
