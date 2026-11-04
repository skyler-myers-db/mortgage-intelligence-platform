"""Take the estate inventory as the metastore's direct owner: read-only (M4-PR1).

Run by the owner, never by CI or an agent, at most 30 days before an
agentic-tier run (docs/deployment.md, "Estate inventory"):

  python -m tools.clients.estate_inventory --out F [--catalog mip] [--bound-workspace-profile P ...]
  python -m tools.clients.estate_inventory --summarize F

``--out`` reads the metastore as its owner (the caller must own it directly)
and writes the canonical inventory (``tools/databricks/estate/inventory.py``)
to ``F``, mode 0600, atomically and never through a symlink. It lists every
catalog with ``include_unbound``, each one's bindings, and, below each foreign
catalog, the owner of every schema, table, function, volume and registered
model. A catalog bound only to other workspaces is read only through a
``--bound-workspace-profile`` (a Databricks CLI profile on one of its bound
workspaces, as the same owner): what the install workspace returns for it is
never trusted, because an empty answer there could be the binding. With no
such profile, or when a read is refused, the catalog is recorded as a gap,
and the deploy refuses an inventory with any gap. The account plane (the
deploy's ``DATABRICKS_ACCOUNT_*`` credential) classifies every workspace
assigned to the metastore; one it no longer has is recorded with the exact
digest of its assignments.

The reads themselves are ``tools/databricks/estate/collect.py``'s. The tool
prints counts, gap counts and the ``estate`` digest, never a name,
owner or id: export that digest as ``MIP_ACK_ESTATE_INVENTORY`` for the run
that relies on the inventory, and store the file's content as the client's
``MIP_ESTATE_INVENTORY_JSON`` GitHub Environment secret (at most 48 KB) or
point ``MIP_ESTATE_INVENTORY_FILE`` at it. ``--summarize`` recomputes both
from a written file. Exit 0: written, no gap; 3: written with gaps; 2: refused
(nothing written).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tools.clients.handover_journal import JournalRefused, canonical_json, write_private
from tools.databricks.estate import inventory as estate
from tools.databricks.estate.collect import InventoryRefused, bound_readers, collect, owner_identity

_PREFIX = "[mip-estate]"


def _print_summary(document: Mapping[str, Any], *, now: datetime, size: int) -> int:
    parsed = estate.parse_inventory(canonical_json(document))
    print(f"{_PREFIX} {estate.summary(parsed, now=now)}")
    if size > estate.SECRET_LIMIT_BYTES:
        print(f"{_PREFIX} {size} bytes: over the 48 KB secret limit; use {estate.INVENTORY_FILE_ENV}")
    print(f"{_PREFIX} export {estate.ACK_ENV}=<the sha256 above> for each agentic run that relies on it")
    if parsed.gap_count():
        print(f"{_PREFIX} the deploy refuses an inventory with any gap; fix each read and take it again")
        return 3
    return 0


def main(
    argv: list[str] | None = None,
    *,
    workspace_factory: Callable[[], Any] | None = None,
    account_factory: Callable[[], Any] | None = None,
    now: datetime | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--out", type=Path)
    action.add_argument("--summarize", type=Path)
    parser.add_argument("--catalog", default="mip")
    parser.add_argument("--bound-workspace-profile", action="append", default=[])
    args = parser.parse_args(argv)
    moment = now or datetime.now(UTC)
    try:
        if args.summarize is not None:
            text = args.summarize.read_text(encoding="utf-8")
            return _print_summary(estate.parse_inventory(text).document, now=moment, size=len(text.encode()))
        from databricks.sdk import WorkspaceClient

        from tools.databricks.uc_owner_policy import account_client_from_env

        workspace = (workspace_factory or WorkspaceClient)()
        owner, metastore_id, _install = owner_identity(workspace)
        readers = bound_readers(args.bound_workspace_profile, owner=owner, metastore_id=metastore_id)
        document = collect(
            workspace,
            account=(account_factory or account_client_from_env)(),
            install_catalog=args.catalog,
            now=moment,
            bound_readers=readers,
        )
        if args.out.exists() and not args.out.is_file():
            raise InventoryRefused("--out must be a regular file path")
        write_private(args.out, document)
        return _print_summary(document, now=moment, size=args.out.stat().st_size)
    except (RuntimeError, JournalRefused, OSError) as exc:
        print(f"{_PREFIX} REFUSED: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        print(f"{_PREFIX} nothing was written", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
