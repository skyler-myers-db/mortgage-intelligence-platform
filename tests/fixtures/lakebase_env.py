"""The Lakebase connection names a Lakebase tool writes straight into ``os.environ``.

tools/sync_lifecycle_warehouse.py and tools/databricks/verify_ai_gateway_exact_proof.py
mint all twelve (LAKEBASE_* and libpq's PG*) with ``os.environ.update``, which
monkeypatch never sees: a name the test did not set itself outlives the test,
and a later test in the same worker inherits it (a deploy dry run then
resolves the shell's LAKEBASE_DATABASE over its .env.local and refuses).
"""

from __future__ import annotations

import pytest

LAKEBASE_ENV_NAMES = tuple(
    f"{prefix}{suffix}" for prefix in ("LAKEBASE_", "PG") for suffix in ("HOST", "PORT", "DATABASE", "USER", "PASSWORD", "SSLMODE")
)


def isolate_lakebase_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset every name, and have monkeypatch erase whatever the code under test writes to it."""

    for name in LAKEBASE_ENV_NAMES:
        # setenv records the pre-test value (or its absence) for teardown;
        # a bare delenv of an absent name records nothing.
        monkeypatch.setenv(name, "isolate-marker")
        monkeypatch.delenv(name)
