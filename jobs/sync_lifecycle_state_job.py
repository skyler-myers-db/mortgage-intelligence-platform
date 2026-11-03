"""The ``mip_sync_lifecycle_state`` job's entrypoint: the install pins, then the sync (M2-PR5).

The App imports jobs/sync_lifecycle_state.py for its canonical MERGE builder
(backend/services/lifecycle_sync.py), so that module stays exactly what the App
runs, and the job-only gate lives here: the bundle task runs this file, which
loads jobs/_install_pins.py and jobs/sync_lifecycle_state.py from its own
folder by exact path, judges the run's parameters against the install pins
and only then calls the sync's ``main``.

Exit codes: the sync's own (jobs/sync_lifecycle_state.py), plus 5 when the run
is not the bundle's: its ``--catalog``, ``--lakebase-instance`` or
``--lakebase-database`` differs from jobs/install_pins.json, or an option the
bundle does not pass is set (``--prune-legacy-defaults`` included). Exit 5
comes before any Spark session, Lakebase credential or connection.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def _beside(filename: str, module_name: str) -> ModuleType:
    """``filename`` from this script's own folder, by exact path; exit 5 otherwise.

    Never a search and never ``sys.path``: Databricks' exec() path can drop
    ``__file__``, and its working directory is then this folder or the bundle
    files root above it.
    """
    try:
        folder = Path(__file__).resolve().parent
    except NameError:
        cwd = Path.cwd()
        folder = cwd if (cwd / "sync_lifecycle_state_job.py").is_file() else cwd / "jobs"
    source = folder / filename
    spec = importlib.util.spec_from_file_location(module_name, source)
    if spec is None or spec.loader is None or not source.is_file():
        print(
            f"[install-pins] sync_lifecycle_state REFUSED: jobs/{filename} is not beside this job",
            file=sys.stderr,
        )
        sys.exit(5)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(argv: list[str] | None = None) -> None:
    pins = _beside("_install_pins.py", "mip_install_pins")
    sync = _beside("sync_lifecycle_state.py", "mip_sync_lifecycle_state_job")
    pins.enforce(
        sync.build_parser(),
        argv,
        expected=lambda pin: {
            "catalog": pin.catalog,
            "lakebase_instance": pin.lakebase_instance,
            "lakebase_database": pin.lakebase_database,
        },
    )
    sync.main(argv)


if __name__ == "__main__":
    run()
