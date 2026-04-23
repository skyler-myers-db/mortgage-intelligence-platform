"""Constant-string sanitizer for dependency 503 error bodies.

Round-5 finding R5-02/R5-03: the prior handler + several routers
returned ``{"detail": str(exc)}`` when a dependency failed. For a
``DatabricksSqlError`` wrapped by ``DependencyDownError`` that string
includes ``state=...``, ``statement_id=...`` and the raw warehouse
``err_msg`` -- which routinely echoes column names, predicate values,
and fully-qualified table names back to the unauthenticated browser
(and into incident tickets where support pastes the error body).

The fix is small and deliberate: public 503 bodies carry only a
constant human-readable string per dependency name. The original
exception text is still emitted at WARNING via structured logging, so
operators retain the full diagnostic trail -- just not on the wire.

Kept in its own module so every router imports the same helper; a
stray ``str(exc)`` in a future router is easier to catch in review
when the approved API is this one function.
"""
from __future__ import annotations


def safe_dependency_detail(dep: str) -> str:
    """Return a constant, PII-safe 503 detail string for ``dep``.

    The return value MUST NOT interpolate any value derived from the
    underlying exception. ``dep`` is the short dependency name the UI
    already knows ("warehouse", "lakebase", "genie") and is not
    exception-derived -- routers pass a literal string, the global
    handler reads it from ``DependencyDownError.dependency`` which is
    set at construction time from a literal.
    """
    # Whitespace-stripped so a caller that accidentally passes
    # ``" warehouse "`` still produces a tidy message. Lowercased for
    # consistency with the existing ``dependency`` field in the body.
    name = (dep or "dependency").strip().lower() or "dependency"
    return f"{name} is temporarily unavailable"


__all__ = ["safe_dependency_detail"]
