"""Tests for R5-02/R5-03 -- public 503 bodies must not leak warehouse /
Lakebase exception text.

Three checks:

1. ``safe_dependency_detail`` returns a constant per-dependency string
   that does NOT interpolate any exception-derived value.
2. The global ``DependencyDownError`` handler, when the underlying
   error is a ``DatabricksSqlError`` carrying ``state=`` /
   ``statement_id=`` / warehouse ``err_msg`` substrings, emits a 503
   body whose ``detail`` leaks NONE of those substrings.
3. The 503 body includes the request's ``correlation_id``.

The handler path is exercised via TestClient against a real route
(`/api/segments`) with a repository override that raises the
DependencyDownError -- same pattern as the existing
``test_dependency_down_exception_translates_to_structured_503`` test.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from backend.main import app
from backend.services.databricks_sql import DatabricksSqlError
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.repositories import get_segment_repository
from backend.services.resilience import DependencyDownError

client = TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# 1. Helper returns a constant string -- no exception interpolation.
# ---------------------------------------------------------------------------


def test_safe_dependency_detail_is_constant_per_dependency() -> None:
    # Same dependency name -> same string, regardless of ambient state.
    assert safe_dependency_detail("warehouse") == "warehouse is temporarily unavailable"
    assert safe_dependency_detail("lakebase") == "lakebase is temporarily unavailable"
    assert safe_dependency_detail("genie") == "genie is temporarily unavailable"

    # Defensive: empty / None-ish collapses to a generic label rather
    # than crashing or leaking. ``None`` should fall into the ``or`` arm.
    assert safe_dependency_detail("") == "dependency is temporarily unavailable"
    assert safe_dependency_detail("   ") == "dependency is temporarily unavailable"

    # The string contains NO substrings that warehouse error messages
    # routinely carry (state=, statement_id=, err_msg, SQL fragments,
    # column names, table FQNs). This is the R5-02 invariant.
    out = safe_dependency_detail("warehouse")
    for leaked in ("state=", "statement_id=", "err_msg", "SELECT", "mip.gold", "borrower_id"):
        assert leaked not in out


# ---------------------------------------------------------------------------
# 2. Handler body carries no warehouse-leak substrings.
# ---------------------------------------------------------------------------


# Canary substrings a real DatabricksSqlError.str() would echo back from
# the warehouse -- if the handler regresses and goes back to `str(exc)`,
# any one of these will match.
_WAREHOUSE_LEAKS = (
    "state=",
    "statement_id=",
    "err_msg",
    "FAILED",
    "stmt-12345",
    "column borrower_id does not exist",
    "mip.gold.fact_borrower",
    "SELECT *",
    "DatabricksSqlError",
)


def _raise_wrapped_warehouse_error() -> None:
    """Produce a ``DependencyDownError`` whose ``last_error`` is a
    ``DatabricksSqlError`` constructed to mimic a real warehouse
    failure string: contains ``state=``, ``statement_id=``, the raw
    error message, and a column / table name."""
    underlying = DatabricksSqlError(
        "Databricks SQL statement did not succeed "
        "(state='FAILED' statement_id='stmt-12345'): "
        "err_msg: column borrower_id does not exist in table mip.gold.fact_borrower; "
        "SELECT * FROM mip.gold.fact_borrower",
        statement_id="stmt-12345",
        state="FAILED",
    )
    raise DependencyDownError(
        "warehouse",
        reason=f"{type(underlying).__name__}: {underlying}",
        last_error=underlying,
    )


def test_dependency_down_handler_leaks_no_warehouse_substrings() -> None:
    """Full handler round-trip: wrap a DatabricksSqlError in a
    DependencyDownError, hit a real route, assert the response body
    contains none of the canary leak substrings in the ``detail``
    field, and that the ``dependency`` field is still populated.
    """
    class _BoomSegmentRepo:
        def list(self, portfolio_id: str | None = None) -> list:
            _raise_wrapped_warehouse_error()
            return []  # unreachable, keeps type-checker happy

    previous = app.dependency_overrides.get(get_segment_repository)
    app.dependency_overrides[get_segment_repository] = lambda: _BoomSegmentRepo()
    try:
        res = client.get("/api/segments")
        assert res.status_code == 503
        body = res.json()

        # Constant-string contract.
        assert body["detail"] == "warehouse is temporarily unavailable"
        assert body["dependency"] == "warehouse"
        assert body["retryable"] is True

        # R5-02 canary: no warehouse-sourced substrings anywhere in the
        # wire-visible body. Check the whole JSON text to catch leaks
        # in any field (detail, reason, dependency, ...).
        wire_text = res.text
        for leaked in _WAREHOUSE_LEAKS:
            assert leaked not in wire_text, (
                f"R5-02 leak regressed: {leaked!r} appeared in 503 body: {wire_text}"
            )
    finally:
        if previous is None:
            del app.dependency_overrides[get_segment_repository]
        else:
            app.dependency_overrides[get_segment_repository] = previous


# ---------------------------------------------------------------------------
# 3. Correlation-id threading preserved.
# ---------------------------------------------------------------------------


def test_dependency_down_handler_includes_correlation_id() -> None:
    """The handler still threads a correlation id into the body so ops
    can stitch a client-visible 503 to the server-side structured log
    line that carries the full ``str(exc)``."""
    class _BoomSegmentRepo:
        def list(self, portfolio_id: str | None = None) -> list:
            raise DependencyDownError("warehouse", reason="circuit breaker is open")

    previous = app.dependency_overrides.get(get_segment_repository)
    app.dependency_overrides[get_segment_repository] = lambda: _BoomSegmentRepo()
    try:
        res = client.get("/api/segments", headers={"X-Correlation-ID": "test-corr-abc"})
        assert res.status_code == 503
        body = res.json()

        assert "correlation_id" in body
        assert body["correlation_id"]  # non-empty
        # When the caller supplies a correlation id, we should echo it.
        assert body["correlation_id"] == "test-corr-abc"
    finally:
        if previous is None:
            del app.dependency_overrides[get_segment_repository]
        else:
            app.dependency_overrides[get_segment_repository] = previous
