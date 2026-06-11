"""Content-negotiated selection of precompressed hashed assets.

2026-06-10 perf slice. ``tools/precompress_assets.mjs`` emits ``.br`` and
``.gz`` siblings next to every compressible hashed Vite asset at build
time. This module picks the smallest variant the client accepts so the
request path never pays for compression CPU and brotli's ~15-20% size
advantage over gzip reaches the wire.

Pure-function design so unit tests can drive it against a tmp dir with no
FastAPI app or built frontend present. The route wiring lives in
``backend/main.py``; identity fallback keeps working when the precompress
step has not run (dev servers, partial builds) — GZipMiddleware then
compresses dynamically exactly as before.
"""
from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from pathlib import Path

# Encodings we may serve, in preference order (smallest first in practice).
_ENCODING_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("br", ".br"),
    ("gzip", ".gz"),
)


@dataclass(frozen=True, slots=True)
class AssetVariant:
    """A resolved on-disk file to serve for one asset request."""

    path: Path
    content_encoding: str | None
    media_type: str


def accepted_encodings(accept_encoding_header: str | None) -> frozenset[str]:
    """Parse an ``Accept-Encoding`` header into the set of usable codings.

    Deliberately minimal: split on commas, lowercase the coding token, and
    drop any entry with an explicit ``q=0`` (the only q-value semantics
    that change behaviour for static selection). Unknown tokens are kept —
    we only ever look up ``br``/``gzip`` membership.
    """
    if not accept_encoding_header:
        return frozenset()
    accepted: set[str] = set()
    for part in accept_encoding_header.split(","):
        token, _, params = part.strip().partition(";")
        coding = token.strip().lower()
        if not coding:
            continue
        quality = 1.0
        for param in params.split(";"):
            name, _, value = param.strip().partition("=")
            if name.strip().lower() == "q":
                try:
                    quality = float(value.strip())
                except ValueError:
                    quality = 1.0
        if quality > 0:
            accepted.add(coding)
    return frozenset(accepted)


def select_asset_variant(
    assets_root: Path,
    asset_path: str,
    accept_encoding_header: str | None,
) -> AssetVariant | None:
    """Resolve ``asset_path`` under ``assets_root`` to the best variant.

    Returns ``None`` for missing files and for any path that escapes
    ``assets_root`` (traversal probes). The media type always derives from
    the ORIGINAL suffix (``.js``), never the variant suffix (``.br``), so
    browsers see ``text/javascript`` + ``Content-Encoding: br``.
    """
    if not asset_path:
        return None
    candidate = (assets_root / asset_path).resolve()
    try:
        candidate.relative_to(assets_root.resolve())
    except ValueError:
        return None
    if not candidate.is_file():
        return None

    media_type = (
        mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
    )
    accepted = accepted_encodings(accept_encoding_header)
    for coding, suffix in _ENCODING_SUFFIXES:
        if coding not in accepted:
            continue
        variant = candidate.with_name(candidate.name + suffix)
        if variant.is_file():
            return AssetVariant(
                path=variant,
                content_encoding=coding,
                media_type=media_type,
            )
    return AssetVariant(path=candidate, content_encoding=None, media_type=media_type)


__all__ = ["AssetVariant", "accepted_encodings", "select_asset_variant"]
