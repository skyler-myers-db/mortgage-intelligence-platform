"""Resolve the identity an opt-in live test acts as.

W5e NB-a / integrator correction C8. Live tests act against the roster
that ``lakebase/seed_campaigns.sql`` seeds on the target Lakebase, and
that deployed sample roster is not this repository's to rename from a
test. So a live test never carries an identity literal
(``tests/unit/test_synthetic_test_identities.py`` pins that for every
``*_live.py`` file): it resolves its actor at test start instead.

Resolution order:

1. ``MIP_LIVE_TEST_ACTOR`` when the operator sets it (no default);
2. otherwise the first active ``admin`` row in ``mip_app.sales_team``
   on the connected Lakebase (ordered by email, so the choice is stable);
3. otherwise the test skips with a reason that names both options.

A real-PostgreSQL contract that applies the seed to a disposable database
reads the seeded admin with ``roster_admin`` (no environment override: the
database is the one the test just built).
"""

from __future__ import annotations

import os
from typing import Any, Protocol

import pytest

LIVE_ACTOR_ENV = "MIP_LIVE_TEST_ACTOR"

_ROSTER_ADMIN_SQL = (
    "SELECT email FROM mip_app.sales_team "
    "WHERE role = 'admin' AND active ORDER BY email LIMIT 1"
)


class _FetchOne(Protocol):
    def fetchone(self, sql: str, params: dict[str, Any] | None = None, /) -> Any: ...


def roster_admin(client: _FetchOne) -> str | None:
    """The first active admin in ``mip_app.sales_team``, or None."""
    row = client.fetchone(_ROSTER_ADMIN_SQL)
    email = str((row or {}).get("email") or "").strip()
    return email or None


def resolve_live_admin_actor(client: _FetchOne | None) -> str:
    """Return the live admin actor, or skip the calling test with a reason."""
    explicit = os.environ.get(LIVE_ACTOR_ENV, "").strip()
    if explicit:
        return explicit
    email = roster_admin(client) if client is not None else None
    if email:
        return email
    pytest.skip(
        f"no live roster actor: set {LIVE_ACTOR_ENV} or seed an active admin "
        "in mip_app.sales_team"
    )
