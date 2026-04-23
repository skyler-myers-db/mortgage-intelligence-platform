---
name: Never .pop() session-scoped FastAPI dependency_overrides in unit tests
description: Test-local override must snapshot and restore the prior binding, not pop — the MIP conftest registers session-wide overrides for every repository + audit + lakebase client, and .pop strips them for the rest of the session
type: feedback
---

When a unit test needs to layer a test-local override on top of
``app.dependency_overrides`` (e.g. to inject a MagicMock lakebase client
that raises ``LakebaseError``), do NOT use
``app.dependency_overrides.pop(get_X, None)`` in teardown.

**Why:** ``tests/conftest.py`` installs a ``scope="session"``
``autouse=True`` fixture that registers ``InMemoryAuditStore`` /
``_FakeLakebaseClient`` / seven in-process repositories under
``app.dependency_overrides``. A ``.pop(...)`` in one test strips those
session-scoped bindings and every later test in the run that resolves
the same dependency falls through to the REAL Databricks / Lakebase
factory, which:

1. Times out opening a warehouse connection, or
2. Surfaces as 503 "lakebase dependency is down: circuit breaker is
   open" after five retries, or
3. Tries to import psycopg / databricks-sdk in a test env that may not
   have them.

**How to apply:** use a helper fixture that snapshots the *previous*
binding before installing the test-local one, and restores it (not
pops) on teardown. The ``override_deps`` fixture in
``tests/unit/test_outreach_reject.py`` is the reference pattern.

Also: if a test intentionally trips the Lakebase circuit breaker (five
consecutive ``LakebaseError`` raises to exercise the no-silent-fallback
path), call ``backend.services.resilience._reset_breakers_for_tests()``
in an autouse fixture's post-yield so the OPEN state doesn't poison
the next test's approve / reject / any-lakebase-touching path. The
breaker is a process-wide singleton.
