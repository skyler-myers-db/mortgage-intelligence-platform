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
