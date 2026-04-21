---
name: No MIP_MOCK_MODE runtime toggle in Module 0
description: Module 0 backend runs on live Unity Catalog data or fails visibly; MIP_MOCK_MODE was retired in Slice 4 and must not be reintroduced
type: feedback
---

Slice 4 of the real-data migration retired ``MIP_MOCK_MODE`` entirely. Future edits must not reintroduce a runtime toggle that silently serves fixtures.

**Why:** A mid-demo mode flip produces confusing screenshots, mixes synthetic and real PII posture, and hides configuration bugs. The booth posture is "fail visibly with a clear operator message, then fix config" -- not "fall back to mock". Flakiness is handled by resilience (retry/circuit-breaker/cache), not silent substitution.

**How to apply:**
- Synthetic fixtures live under ``tests/fixtures/`` (``mock_population.py`` + ``in_process_repos.py``). Production code under ``backend/`` must never import them.
- Missing ``DATABRICKS_HOST`` / ``DATABRICKS_TOKEN`` / ``DATABRICKS_WAREHOUSE_ID`` is a fail-fast at ``backend/runtime.py`` preflight AND a FastAPI lifespan abort in ``backend/main.py``. Both paths point the operator at ``.env.local``.
- Tests bypass the startup check via ``_running_under_pytest()`` in ``backend/config/settings.py`` and inject stubs through FastAPI ``dependency_overrides`` in ``tests/conftest.py``.
- The tripwire test ``tests/unit/test_repository_seam.py`` asserts no router imports any synthetic-population module; extend the forbidden-tokens list if a new fixture path is ever added.
