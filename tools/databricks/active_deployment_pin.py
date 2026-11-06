"""The sha256 of the App's active deployment id: printed by a plan, required by a release (M1-PR7).

A deployment id is identifying (plan §4.1), so a public log and a dispatch
input carry only its sha256. ``--phase plan`` prints the digest of the App's
active deployment (``none`` when the App or its active deployment is absent),
and a ``--phase release`` dispatched with ``expected_active_deployment_sha256``
refuses before the lease unless the live digest is still that one: the
release acts on the deployment its plan saw, never a later one. The id is a
runner mask the moment it is read. Read-only: one ``apps.get``.

  python -m tools.databricks.active_deployment_pin digest --app-name NAME
  python -m tools.databricks.active_deployment_pin check --app-name NAME --expect <sha256|none>

Exit 0, or 2 on a mismatch or a read error (named by its class only).
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from collections.abc import Callable, Sequence
from typing import Any

from databricks.sdk.errors import NotFound, ResourceDoesNotExist
from tools.clients.redact import emit_mask

NONE = "none"
_EXPECTED = re.compile(r"[0-9a-f]{64}|none")


def _client() -> Any:
    from tools.databricks.workspace_auth import deployment_workspace_client

    return deployment_workspace_client()


def active_digest(workspace: Any, app_name: str) -> str:
    """sha256 of the active deployment id, or ``none``."""

    try:
        app = workspace.apps.get(app_name)
    except (NotFound, ResourceDoesNotExist):
        return NONE
    active = getattr(app, "active_deployment", None)
    deployment_id = str(getattr(active, "deployment_id", "") or "").strip()
    if not deployment_id:
        return NONE
    emit_mask(deployment_id)
    return hashlib.sha256(deployment_id.encode("utf-8")).hexdigest()


def main(argv: Sequence[str] | None = None, *, workspace_factory: Callable[[], Any] = _client) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.databricks.active_deployment_pin", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("digest", "check"):
        command = sub.add_parser(name)
        command.add_argument("--app-name", required=True)
        if name == "check":
            command.add_argument("--expect", required=True)
    args = parser.parse_args(argv)
    if args.command == "check" and _EXPECTED.fullmatch(args.expect.strip()) is None:
        print("[active-deployment] refused: --expect is 64 lowercase hex or none", file=sys.stderr)
        return 2
    try:
        live = active_digest(workspace_factory(), args.app_name)
    except Exception as exc:  # the class only: an SDK message can carry an id
        print(f"[active-deployment] refused: the App could not be read ({type(exc).__name__})", file=sys.stderr)
        return 2
    if args.command == "digest":
        print(f"[active-deployment] {args.app_name} active deployment sha256: {live} (read-only)")
        return 0
    if live != args.expect.strip():
        print(
            f"[active-deployment] refused: {args.app_name}'s active deployment is no longer the one the dispatch "
            f"expected (live sha256 {live}); re-run --phase plan and dispatch with its digest",
            file=sys.stderr,
        )
        return 2
    print(f"[active-deployment] {args.app_name}'s active deployment is the expected one (read-only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
