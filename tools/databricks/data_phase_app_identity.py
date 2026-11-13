#!/usr/bin/env python3
"""The App identity ``--phase data`` grants EXECUTE to, pinned to the signed record (M1-PR6).

After every gold-touching job the data phase grants the reviewed functions'
EXECUTE to the App's service principal (attack O11,
``reconcile_reviewed_function_execute_grants``). It reads that principal here,
read-only, and never from an unpinned ``apps get``: the live App the lease
names must still carry the service principal (client id and SCIM id) of its
signed last-good rollback record, the release's own pin
(``app_rollback_record_contract._load_record``, which proves the scope's
ownership and the record's signature first). Every version a release or
adopt-live signs pins it: v5/v6, and v7 (M2-PR7), whose core record is
cutover run 1's; a v7 retired tombstone pins nothing. An App replaced under
the same name, or an install no release has captured yet, refuses with exit 2
and nothing granted. The release's distinctness refusal (the App identity is never
the bounded account credential's client) stays in the shell, beside the
release's own (``same_identity_casefold``).

Prints the pinned client id on stdout and nothing else, registered as a runner
mask first (tools/clients/redact.emit_mask): the data phase echoes it in every
grant command it runs, in a public job log. Every call is a GET or a list.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import Any

from tools.clients.redact import emit_mask
from tools.databricks.app_rollback_record_contract import _load_record

EXIT_REFUSED = 2
NO_RECORD = "no server-owned last-good App rollback contract exists"


class Refused(RuntimeError):
    """The App is not the one the signed record pins; nothing was granted."""


def pinned_client_id(workspace: Any, *, app_name: str, scope: str, lakebase_instance: str) -> str:
    """The App's client id, only while it is the signed record's service principal."""

    try:
        record = _load_record(
            workspace, app_name=app_name, scope=scope, expected_lakebase_instance=lakebase_instance, accept_v7=True
        )
    except RuntimeError as exc:
        # Only the reviewed absence message is echoed; any other failure is named by its class.
        detail = "none exists yet" if str(exc).startswith(NO_RECORD) else type(exc).__name__
        raise Refused(f"the signed last-good App rollback record is unproven ({detail}); run the release first") from None
    app = workspace.apps.get(app_name)
    live = (str(app.service_principal_client_id or "").strip(), str(app.service_principal_id or "").strip())
    pinned = (str(record["app_service_principal_client_id"]), str(record["app_service_principal_scim_id"]))
    if not all(live) or live != pinned:
        raise Refused("the App's service principal is not the one its signed rollback record pins")
    return live[0]


def main(argv: Sequence[str] | None = None, *, workspace: Any = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--app-name", required=True)
    parser.add_argument("--rollback-scope", required=True)
    parser.add_argument("--lakebase-instance", required=True)
    args = parser.parse_args(argv)
    try:
        if workspace is None:
            from tools.databricks.workspace_auth import deployment_workspace_client

            workspace = deployment_workspace_client()
        client_id = pinned_client_id(
            workspace, app_name=args.app_name, scope=args.rollback_scope, lakebase_instance=args.lakebase_instance
        )
    except Refused as exc:
        print(f"[data-phase] refused: {exc}; nothing was granted", file=sys.stderr)
        return EXIT_REFUSED
    except Exception as exc:  # the class only: an SDK message can carry a host or an id
        print(f"[data-phase] refused: {type(exc).__name__}; nothing was granted", file=sys.stderr)
        return EXIT_REFUSED
    print(emit_mask(client_id))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
