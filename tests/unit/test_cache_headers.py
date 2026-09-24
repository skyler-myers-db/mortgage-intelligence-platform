"""Cache-Control contract for hashed assets and API JSON.

2026-09-21 UI/UX audit, findings ``bundle-01`` and ``delivery-10``.

* ``bundle-01``: the security-headers middleware stamped
  ``public, max-age=31536000, immutable`` on every ``/assets/`` response by
  path prefix alone, including the 404 for a hashed chunk that a deploy had
  retired. A tab left open across a deploy then cached the *failure* for a
  year, so even a rollback to the identical hashes stayed broken.
* ``delivery-10``: borrower, lead and workspace JSON carried no cache
  directive at all, leaving shared booth machines on browser heuristics.

Every assertion goes through the real ``backend.main.app`` middleware stack
(backpressure, visit tracking, correlation id, security headers, gzip). The
two probe routes exist because the repo's backend CI job runs before the
frontend build, so ``frontend/dist`` (and with it the real ``/assets`` route)
is absent there; the probes put a 200/206/304/4xx/5xx ``/assets/`` response
and an API route that owns its Cache-Control behind the same real stack.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from backend.main import app

IMMUTABLE = "public, max-age=31536000, immutable"
_DIST_ASSETS = Path(__file__).resolve().parents[2] / "frontend" / "dist" / "assets"


def _asset_probe(request: Request) -> Response:
    status = int(request.path_params["status"])
    body = b"" if status == 304 else b"export default 1;\n"
    return Response(body, status_code=status, media_type="text/javascript")


def _own_cache_control_probe(_request: Request) -> Response:
    return Response(
        b"{}",
        media_type="application/json",
        headers={"Cache-Control": "public, max-age=60"},
    )


@pytest.fixture
def client_with_probes() -> Iterator[TestClient]:
    """Real app + two probe routes, inserted ahead of the ``/assets`` route
    and the ``/api/{path}`` 404 catch-all and removed afterwards."""
    probes = [
        Route("/assets/pytest-probe-{status:int}.js", _asset_probe),
        Route("/api/v1/pytest-own-cache-control", _own_cache_control_probe),
    ]
    app.router.routes[0:0] = probes
    try:
        yield TestClient(app)
    finally:
        for probe in probes:
            app.router.routes.remove(probe)


def test_missing_hashed_asset_is_404_and_never_cacheable() -> None:
    """The stale-tab case: the chunk a pre-deploy tab asks for is gone."""
    response = TestClient(app).get("/assets/lead-queue-0ld5ta1e.js")

    assert response.status_code == 404, response.text
    assert response.headers["cache-control"] == "no-store"
    assert "immutable" not in response.headers["cache-control"]


@pytest.mark.parametrize("status", [200, 206, 304])
def test_served_hashed_asset_stays_immutable(
    client_with_probes: TestClient, status: int
) -> None:
    response = client_with_probes.get(f"/assets/pytest-probe-{status}.js")

    assert response.status_code == status
    assert response.headers["cache-control"] == IMMUTABLE


@pytest.mark.parametrize("status", [403, 404, 416, 500, 503])
def test_failed_asset_response_is_forced_no_store(
    client_with_probes: TestClient, status: int
) -> None:
    response = client_with_probes.get(f"/assets/pytest-probe-{status}.js")

    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"


def test_built_asset_is_immutable_through_the_real_asset_route() -> None:
    """Exercises the negotiated ``/assets`` route itself when a build exists."""
    built = sorted(_DIST_ASSETS.glob("*.js")) if _DIST_ASSETS.is_dir() else []
    if not built:
        pytest.skip("frontend/dist is not built in this checkout")

    response = TestClient(app).get(f"/assets/{built[0].name}")

    assert response.status_code == 200
    assert response.headers["cache-control"] == IMMUTABLE


def test_api_json_defaults_to_private_no_store() -> None:
    response = TestClient(app).get("/api/v1/health")

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"


def test_api_error_responses_also_default_to_private_no_store() -> None:
    response = TestClient(app).get("/api/v1/this-route-does-not-exist")

    assert response.status_code == 404
    assert response.headers["cache-control"] == "private, no-store"


def test_api_route_that_sets_its_own_cache_control_keeps_it(
    client_with_probes: TestClient,
) -> None:
    response = client_with_probes.get("/api/v1/pytest-own-cache-control")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=60"


def test_spa_shell_and_non_api_paths_are_not_given_the_api_default() -> None:
    """The default is scoped to ``/api/``: the SPA shell keeps its own
    ``no-cache, no-store, must-revalidate`` and nothing else is stamped."""
    response = TestClient(app).get("/lead-queue")

    assert response.headers.get("cache-control") != "private, no-store"


# ---------------------------------------------------------------------------
# bundle-v1 remainder: the dormant /brand StaticFiles mount is gone.
# ---------------------------------------------------------------------------

_MAIN_SOURCE = Path(__file__).resolve().parents[2] / "backend" / "main.py"


def test_no_brand_route_or_mount_is_registered() -> None:
    """The wordmark ships as a hashed Vite asset (bfb2bdf6); nothing serves
    or references an unhashed, uncached ``/brand`` path any more."""
    paths = [getattr(route, "path", None) for route in app.routes]

    assert not any(isinstance(path, str) and path.startswith("/brand") for path in paths)


def test_main_source_carries_no_brand_mount() -> None:
    """Source pin: the mount was conditional on ``frontend/dist/brand``, and
    CI's backend job runs before the frontend build, so the route check above
    alone would pass even with the mount restored."""
    source = _MAIN_SOURCE.read_text(encoding="utf-8")

    assert '"/brand"' not in source
    assert "StaticFiles(" not in source
