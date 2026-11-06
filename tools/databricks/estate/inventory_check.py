"""Pre-lease, read-only: the estate inventory an agentic run relies on, and its exact ack (M4-PR1).

``scripts/deploy.sh`` runs this in the agentic tier only, before the signed
lease and any App stop (``check_estate_inventory_prelease``):

  python -m tools.databricks.estate.inventory_check --catalog mip

It reads the deployed metastore and workspace ids as the deployer, then loads
the owner's inventory exactly as every post-stop audit will
(``estate.inventory.load_estate_inventory``), judging its age at the end of
the run's margin (``RUN_MARGIN``), so an inventory that would expire with the
App stopped refuses here instead. It prints counts and the ``estate`` digest,
never a name; exit 2 refuses.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from tools.databricks.estate.inventory import (
    RUN_MARGIN,
    EstateInventoryRefusal,
    load_estate_inventory,
    read_inventory,
    summary,
)

_PREFIX = "[mip-estate]"


def prelease_refusals(workspace: Any, *, catalog: str, now: datetime | None = None) -> tuple[list[str], list[str]]:
    """``(refusals, notes)``: what the pre-lease check refuses, and the public-safe summary."""

    from tools.databricks.agent_runtime_uc_baseline import authoritative_workspace_id
    from tools.databricks.agent_runtime_uc_inventory import _text

    moment = now or datetime.now(UTC)
    metastore_id = _text(getattr(workspace.metastores.current(), "metastore_id", None))
    workspace_id = authoritative_workspace_id(workspace)
    try:
        inventory = load_estate_inventory(
            metastore_id=metastore_id,
            workspace_id=workspace_id,
            install_catalog=catalog.strip(),
            now=moment,
            margin=RUN_MARGIN,
        )
    except EstateInventoryRefusal as exc:
        try:
            notes = [summary(read_inventory(), now=moment)]
        except EstateInventoryRefusal:
            notes = []
        return list(exc.lines), notes
    return [], [summary(inventory, now=moment)]


def main(argv: list[str] | None = None, *, workspace_factory: Callable[[], Any] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--catalog", required=True)
    args = parser.parse_args(argv)
    if workspace_factory is None:
        from tools.databricks.workspace_auth import deployment_workspace_client

        workspace = deployment_workspace_client()
    else:
        workspace = workspace_factory()
    refusals, notes = prelease_refusals(workspace, catalog=args.catalog)
    for note in notes:
        print(f"{_PREFIX} {note}")
    if refusals:
        for line in refusals:
            print(f"{_PREFIX} REFUSED: {line}", file=sys.stderr)
        return 2
    print(f"{_PREFIX} the estate inventory is acknowledged for this run (read-only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
