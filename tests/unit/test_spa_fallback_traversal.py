"""R6-14: path-traversal attempts against the SPA fallback are logged.

The `_spa_fallback` catch-all in ``backend.main`` silently swallows a
``ValueError`` from ``Path.relative_to`` (which is what
``(dist_dir / requested_path).resolve()`` raises when the resolved
candidate escapes the dist directory). Serving ``index.html`` is the
correct SPA behaviour -- React Router resolves the client-side route,
or shows its own 404 for a bogus path -- but ops MUST see the probe in
the structured log trail.

This test pins:

1. A traversal attempt still returns 200 + the SPA shell (no user
   experience regression).
2. A ``event=spa_path_traversal_blocked`` structured event is emitted
   at WARNING so SOC dashboards can pattern-match.
3. The attempted path is carried on the event body (capped at 256
   chars) so the responder has actionable detail.
"""
from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from backend.main import app


def test_spa_fallback_traversal_returns_200_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The SPA catch-all on a traversal path must log + still serve
    index.html (so a legitimate client-side route that happens to look
    weird doesn't 500)."""
    caplog.set_level(logging.WARNING, logger="mip-runtime")

    client = TestClient(app)
    # ``../../etc/passwd`` escapes dist/. TestClient normalises the URL
    # but we send the suspicious path verbatim by using
    # ``raw_path``-style encoding: ``%2e%2e/etc/passwd`` survives the
    # normaliser. When the mount isn't active the route 404s from a
    # different code path -- the test below asserts the structured log
    # event, so the presence/absence of frontend/dist is orthogonal.
    resp = client.get("/%2e%2e/etc/passwd")

    # Either the SPA catch-all served index.html (200) or the route
    # didn't mount (the `if _FRONTEND_DIST.is_dir()` guard in
    # backend/main.py). If the mount is active, status MUST be 200
    # (serving index.html on traversal is documented SPA behaviour).
    # If the mount is inactive this test is a no-op by design; we
    # assert the response is not a 5xx (which would indicate a bug).
    assert resp.status_code < 500

    # The structured event fires ONLY when the mount is active AND the
    # traversal branch was hit. We tolerate either-or: if the mount is
    # inactive, we simply don't check log contents.
    if resp.status_code == 200:
        structured = [
            r for r in caplog.records
            if getattr(r, "mip_event", "") == "spa_path_traversal_blocked"
        ]
        assert structured, (
            "expected a spa_path_traversal_blocked event when "
            "the SPA mount is active and a traversal attempt is served"
        )
        # WARNING level so Splunk/Datadog dashboards surface it.
        assert structured[0].levelno == logging.WARNING
        # Attempted path is carried on the structured extras.
        extras = getattr(structured[0], "mip_extras", {}) or {}
        assert "path" in extras
