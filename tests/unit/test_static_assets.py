"""Unit tests for content-negotiated precompressed asset selection.

2026-06-10 perf slice. ``tools/precompress_assets.mjs`` emits ``.br`` /
``.gz`` siblings for hashed Vite assets at build time;
``backend/services/static_assets.select_asset_variant`` picks the variant
the client accepts. These tests pin:

* brotli preferred over gzip when both are acceptable and present,
* gzip fallback when brotli is absent or refused,
* identity fallback when nothing compressed is acceptable/present
  (pre-precompress dists keep working -- GZipMiddleware then compresses
  dynamically as before),
* media type derives from the ORIGINAL suffix, never ``.br``/``.gz``,
* traversal probes and missing files resolve to None (404 at the route).
"""
from __future__ import annotations

from pathlib import Path

from fastapi.responses import FileResponse
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.routing import Route

from backend.main import app
from backend.services.static_assets import accepted_encodings, select_asset_variant


def _make_assets(tmp_path: Path, *, br: bool = True, gz: bool = True) -> Path:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index-ABC123.js").write_bytes(b"console.log('mip');" * 64)
    if br:
        (assets / "index-ABC123.js.br").write_bytes(b"br-bytes")
    if gz:
        (assets / "index-ABC123.js.gz").write_bytes(b"gz-bytes")
    return assets


# ---------------------------------------------------------------------------
# accepted_encodings
# ---------------------------------------------------------------------------


def test_accepted_encodings_parses_tokens_and_q_values() -> None:
    assert accepted_encodings("gzip, br") == frozenset({"gzip", "br"})
    assert accepted_encodings("br;q=1.0, gzip;q=0.8") == frozenset({"br", "gzip"})
    # Explicit refusal via q=0 drops the coding.
    assert accepted_encodings("br;q=0, gzip") == frozenset({"gzip"})
    assert accepted_encodings("gzip;q=0.000") == frozenset()
    assert accepted_encodings(None) == frozenset()
    assert accepted_encodings("") == frozenset()
    # Case-insensitive per RFC 9110.
    assert "br" in accepted_encodings("BR")


# ---------------------------------------------------------------------------
# select_asset_variant
# ---------------------------------------------------------------------------


def test_brotli_preferred_when_accepted(tmp_path: Path) -> None:
    assets = _make_assets(tmp_path)
    variant = select_asset_variant(assets, "index-ABC123.js", "gzip, deflate, br")
    assert variant is not None
    assert variant.path.name == "index-ABC123.js.br"
    assert variant.content_encoding == "br"
    # Media type comes from .js, not .br.
    assert variant.media_type in {"text/javascript", "application/javascript"}


def test_gzip_fallback_when_brotli_refused(tmp_path: Path) -> None:
    assets = _make_assets(tmp_path)
    variant = select_asset_variant(assets, "index-ABC123.js", "br;q=0, gzip")
    assert variant is not None
    assert variant.path.name == "index-ABC123.js.gz"
    assert variant.content_encoding == "gzip"


def test_gzip_fallback_when_brotli_sibling_missing(tmp_path: Path) -> None:
    assets = _make_assets(tmp_path, br=False)
    variant = select_asset_variant(assets, "index-ABC123.js", "gzip, br")
    assert variant is not None
    assert variant.path.name == "index-ABC123.js.gz"
    assert variant.content_encoding == "gzip"


def test_identity_when_no_encodings_accepted(tmp_path: Path) -> None:
    assets = _make_assets(tmp_path)
    variant = select_asset_variant(assets, "index-ABC123.js", None)
    assert variant is not None
    assert variant.path.name == "index-ABC123.js"
    assert variant.content_encoding is None


def test_identity_when_no_siblings_exist(tmp_path: Path) -> None:
    assets = _make_assets(tmp_path, br=False, gz=False)
    variant = select_asset_variant(assets, "index-ABC123.js", "gzip, br")
    assert variant is not None
    assert variant.path.name == "index-ABC123.js"
    assert variant.content_encoding is None


def test_missing_file_returns_none(tmp_path: Path) -> None:
    assets = _make_assets(tmp_path)
    assert select_asset_variant(assets, "nope.js", "gzip, br") is None
    assert select_asset_variant(assets, "", "gzip, br") is None


def test_traversal_probe_returns_none(tmp_path: Path) -> None:
    assets = _make_assets(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("nope")
    assert select_asset_variant(assets, "../secret.txt", "gzip, br") is None
    assert select_asset_variant(assets, "..%2Fsecret.txt".replace("%2F", "/"), None) is None


def test_css_media_type(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index-XYZ.css").write_bytes(b"body{}" * 256)
    (assets / "index-XYZ.css.br").write_bytes(b"br")
    variant = select_asset_variant(assets, "index-XYZ.css", "br")
    assert variant is not None
    assert variant.media_type == "text/css"
    assert variant.content_encoding == "br"


# ---------------------------------------------------------------------------
# W5c (audit dataviz-01): the committed ZCTA geometry ships as hashed
# ``<USPS>.topo-<hash>.json`` assets with build-time ``.br`` / ``.gz``
# siblings. The fixture harness serves the build through ``vite preview``,
# which never sends brotli, so the br-encoded, immutable answer is proven
# here, through the real middleware stack.
# ---------------------------------------------------------------------------


def _make_topology_asset(tmp_path: Path) -> Path:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "IL.topo-Ab12Cd34.json").write_bytes(b'{"type":"Topology","objects":{}}' * 64)
    (assets / "IL.topo-Ab12Cd34.json.br").write_bytes(b"br-topology")
    (assets / "IL.topo-Ab12Cd34.json.gz").write_bytes(b"gz-topology")
    return assets


def test_topology_asset_is_served_brotli_as_json(tmp_path: Path) -> None:
    assets = _make_topology_asset(tmp_path)
    variant = select_asset_variant(assets, "IL.topo-Ab12Cd34.json", "gzip, deflate, br")
    assert variant is not None
    assert variant.path.name == "IL.topo-Ab12Cd34.json.br"
    assert variant.content_encoding == "br"
    assert variant.media_type == "application/json"


def test_topology_asset_is_br_encoded_and_immutable_through_the_app(tmp_path: Path) -> None:
    assets = _make_topology_asset(tmp_path)

    def _probe(request: Request) -> FileResponse:
        # The /assets handler's body (backend/main.py) over this scratch dist:
        # main.py mounts the real route only when frontend/dist exists, which
        # the backend CI job does not build.
        variant = select_asset_variant(assets, request.path_params["asset_path"], request.headers.get("accept-encoding"))
        assert variant is not None
        headers = {"Vary": "Accept-Encoding"}
        if variant.content_encoding is not None:
            headers["Content-Encoding"] = variant.content_encoding
        return FileResponse(variant.path, media_type=variant.media_type, headers=headers)

    probe = Route("/assets/{asset_path:path}", _probe)
    app.router.routes.insert(0, probe)
    try:
        response = TestClient(app).get("/assets/IL.topo-Ab12Cd34.json", headers={"Accept-Encoding": "br"})
    finally:
        app.router.routes.remove(probe)
    assert response.status_code == 200
    assert response.headers["content-encoding"] == "br"
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
