"""Make monkeypatch restore the os.environ names that code under test writes.

``monkeypatch.delenv(name, raising=False)`` records nothing when ``name`` is
absent: pytest's ``MonkeyPatch.delitem`` saves the old value only when the
name exists. In CI these names are never ambient, so code under test that
then writes one (``os.environ[...] =``, ``update``, ``setdefault``) leaves it
in os.environ for every later test on the same xdist worker. A leaked client
profile name (LAKEBASE_DATABASE) made a later dry run refuse the profile
("must agree"): PR #321, 2026-11-05.

``restore_on_teardown`` records each name's pre-test state, absent included,
and changes nothing now. tests/unit/test_env_leak_regressions.py runs the
known writers and proves os.environ is unchanged after each one.
"""

from __future__ import annotations

import os
from collections.abc import Iterable

import pytest

# Everything tools/sync_lifecycle_warehouse.py (_set_lakebase_auth) and
# tools/databricks/verify_ai_gateway_exact_proof.py (ensure_lakebase_env) write.
LAKEBASE_CONNECTION_ENV = tuple(
    f"{prefix}{field}"
    for prefix in ("LAKEBASE_", "PG")
    for field in ("HOST", "PORT", "DATABASE", "USER", "PASSWORD", "SSLMODE")
)
# Everything an exact OAuth M2M binding writes: bind_exact_workspace_m2m_auth
# (tools/databricks/m2m_workspace_auth.py) and both dual-authority verifiers.
EXACT_M2M_BINDING_ENV = (
    "DATABRICKS_HOST",
    "DATABRICKS_AUTH_TYPE",
    "DATABRICKS_CLIENT_ID",
    "DATABRICKS_CLIENT_SECRET",
    "MIP_DISABLE_DOTENV",
)

_ABSENT = "absent-before-this-test"


def restore_on_teardown(monkeypatch: pytest.MonkeyPatch, names: Iterable[str]) -> None:
    """Restore each name to its pre-test state at teardown, absent included; change nothing now."""

    for name in names:
        if name in os.environ:
            monkeypatch.setenv(name, os.environ[name])
        else:
            # setenv records "absent"; the delenv that follows restores it now.
            monkeypatch.setenv(name, _ABSENT)
            monkeypatch.delenv(name)
