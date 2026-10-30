"""Render ``jobs/install_pins.json``: the parameters the App-triggerable jobs accept (M2-PR5).

tools/databricks/bundle_env.py writes it from the resolved client profile
before every bundle call it wraps, next to the job sources in the bundle root
(``DATABRICKS_BUNDLE_ROOT`` or this checkout). The file is gitignored and
databricks.yml syncs it (``sync.include``), so every bundle deploy uploads the
pins of the install it deploys into the deployer-owned root, where the App
cannot write. jobs/_install_pins.py reads it from its own folder and refuses a
run whose catalog, Lakebase instance or database differs, before any SQL.

Every pinned value is a public profile field (the catalog and the Lakebase
names) or derived from one (the data-owner group, ``<MIP_IDENTITY_PREFIX>-data-owners``,
tools/clients/bootstrap_roster.py), so the file names no identifying value.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

PINS_RELPATH = Path("jobs") / "install_pins.json"
PINS_VERSION = 1


def document(
    *,
    catalog: str,
    lakebase_instance: str,
    lakebase_database: str,
    data_owner_group: str,
) -> dict[str, object]:
    return {
        "version": PINS_VERSION,
        "catalog": catalog,
        "lakebase_instance": lakebase_instance,
        "lakebase_database": lakebase_database,
        "data_owner_group": data_owner_group,
    }


def render(bundle_root: Path, pins: dict[str, object]) -> Path:
    """Write the pins under ``bundle_root`` atomically; return the path written."""

    target = bundle_root / PINS_RELPATH
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(pins, indent=2, sort_keys=True) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix=".install_pins.", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(temporary, 0o644)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return target
