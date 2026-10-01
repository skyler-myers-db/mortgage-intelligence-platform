"""Reviewed lender co-branding marks (audit responsive-10, 12.4 #9 white-label).

A deployment may show its lender's mark beside the tenant name in the topbar
pill and the Console tenant chip. The image itself never enters the repo: an
operator points ``MIP_LENDER_MARK_FILE`` at a file outside the checkout, and
``scripts/deploy.sh`` preflight accepts it only when its sha256, media type
and pixel size equal a source-controlled entry bound to the reviewed
(lender name, NMLS id) identity in ``lender_identity``. Only PNG and WebP
without metadata, colour profiles or animation are accepted, at most 64 KiB,
32-512 px tall and 1-2 times as wide as tall. Every refusal is a fixed
sentence: no message carries a path or any byte of the file.

Stdlib only, so the deploy preflight can import it before any dependency
install.
"""

from __future__ import annotations

import hashlib
import re
import struct
import zlib
from dataclasses import dataclass
from datetime import date
from typing import Literal

from backend.schemas.lender_identity import (
    _REVIEWED_PUBLIC_LENDER_IDENTITIES,
    validate_public_lender_identity,
)

MediaType = Literal["image/png", "image/webp"]

MAX_MARK_BYTES = 65536
MIN_MARK_HEIGHT = 32
MAX_MARK_HEIGHT = 512

_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_AUTHORIZATION_REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._#/-]{2,63}")
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PNG_ALLOWED_CHUNKS = frozenset(
    {b"IHDR", b"PLTE", b"tRNS", b"sRGB", b"gAMA", b"cHRM", b"pHYs", b"IDAT", b"IEND"}
)
_WEBP_ALLOWED_CHUNKS = frozenset({b"VP8 ", b"VP8L", b"VP8X", b"ALPH"})
# VP8X feature flags that carry animation, XMP, EXIF or an ICC profile.
_VP8X_REFUSED_FLAGS = 0x02 | 0x04 | 0x08 | 0x20


@dataclass(frozen=True)
class ReviewedLenderMark:
    """The reviewed facts about one lender's mark; never the image itself."""

    nmls_id: str
    sha256: str
    media_type: MediaType
    width: int
    height: int
    authorization_ref: str
    reviewed_on: date


# Keyed by the exact reviewed lender name. Summit Mortgage deliberately has
# none: the sample lender never carries a mark. Adding an entry is an
# independently reviewed change made alongside _REVIEWED_PUBLIC_LENDER_IDENTITIES
# (lender_identity.py) that records only the sha256, media type, dimensions,
# the brand-use authorisation reference and the review date. The image itself
# never enters the repo.
_REVIEWED_LENDER_MARKS: dict[str, ReviewedLenderMark] = {}


def reviewed_lender_mark(lender_name: str) -> ReviewedLenderMark | None:
    """The reviewed mark entry for this exact lender name, if any."""

    return _REVIEWED_LENDER_MARKS.get(lender_name)


def _registry_problems() -> list[str]:
    """Every registry entry that is not bound to a reviewed identity or is malformed."""

    problems: list[str] = []
    for name, mark in _REVIEWED_LENDER_MARKS.items():
        if mark.nmls_id not in _REVIEWED_PUBLIC_LENDER_IDENTITIES.get(name, frozenset()):
            problems.append(f"{name}: not a reviewed (lender name, NMLS id) identity")
        if _SHA256_RE.fullmatch(mark.sha256) is None:
            problems.append(f"{name}: sha256 is not 64 lowercase hex characters")
        ref = mark.authorization_ref
        if _AUTHORIZATION_REF_RE.fullmatch(ref) is None or "://" in ref or "@" in ref:
            problems.append(f"{name}: authorization_ref is not a plain reference")
        reviewed_on: object = mark.reviewed_on
        if type(reviewed_on) is not date or reviewed_on > date.today():
            problems.append(f"{name}: reviewed_on is not a past calendar date")
        if mark.media_type not in ("image/png", "image/webp"):
            problems.append(f"{name}: media_type is not PNG or WebP")
    return problems


def _png_dimensions(data: bytes) -> tuple[int, int]:
    offset = len(_PNG_SIGNATURE)
    chunks: list[bytes] = []
    width = height = 0
    while offset < len(data):
        if offset + 12 > len(data):
            raise ValueError("lender mark PNG is truncated")
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        kind = data[offset + 4 : offset + 8]
        end = offset + 12 + length
        if end > len(data):
            raise ValueError("lender mark PNG is truncated")
        payload = data[offset + 8 : offset + 8 + length]
        (crc,) = struct.unpack(">I", data[offset + 8 + length : end])
        if zlib.crc32(kind + payload) & 0xFFFFFFFF != crc:
            raise ValueError("lender mark PNG has a chunk with a bad checksum")
        if kind not in _PNG_ALLOWED_CHUNKS:
            raise ValueError("lender mark PNG carries metadata, a profile or animation")
        if kind == b"IHDR":
            if chunks or length != 13:
                raise ValueError("lender mark PNG must start with one IHDR")
            width, height = struct.unpack(">II", payload[:8])
        chunks.append(kind)
        offset = end
        if kind == b"IEND":
            break
    if offset != len(data):
        raise ValueError("lender mark PNG has bytes after IEND")
    if not chunks or chunks[0] != b"IHDR" or chunks[-1] != b"IEND" or b"IDAT" not in chunks:
        raise ValueError("lender mark PNG must run IHDR ... IDAT ... IEND")
    return width, height


def _webp_dimensions(data: bytes) -> tuple[int, int]:
    (riff_size,) = struct.unpack("<I", data[4:8])
    if riff_size != len(data) - 8:
        raise ValueError("lender mark WebP size does not match its RIFF header")
    offset = 12
    chunks: list[tuple[bytes, bytes]] = []
    while offset < len(data):
        if offset + 8 > len(data):
            raise ValueError("lender mark WebP is truncated")
        kind = data[offset : offset + 4]
        (length,) = struct.unpack("<I", data[offset + 4 : offset + 8])
        end = offset + 8 + length
        if end > len(data):
            raise ValueError("lender mark WebP is truncated")
        if kind not in _WEBP_ALLOWED_CHUNKS:
            raise ValueError("lender mark WebP carries metadata, a profile or animation")
        chunks.append((kind, data[offset + 8 : end]))
        offset = end + (length & 1)
    if offset != len(data):
        raise ValueError("lender mark WebP is truncated")
    bitstreams = [payload for kind, payload in chunks if kind in (b"VP8 ", b"VP8L")]
    if len(bitstreams) != 1:
        raise ValueError("lender mark WebP must hold exactly one still image")
    kinds = [kind for kind, _ in chunks]
    if b"VP8X" in kinds:
        if kinds[0] != b"VP8X":
            raise ValueError("lender mark WebP VP8X header is out of place")
        header = chunks[0][1]
        if len(header) < 10 or header[0] & _VP8X_REFUSED_FLAGS:
            raise ValueError("lender mark WebP carries metadata, a profile or animation")
        width = 1 + int.from_bytes(header[4:7], "little")
        height = 1 + int.from_bytes(header[7:10], "little")
        return width, height
    if b"ALPH" in kinds:
        raise ValueError("lender mark WebP alpha needs a VP8X header")
    kind = next(kind for kind, _ in chunks if kind in (b"VP8 ", b"VP8L"))
    payload = bitstreams[0]
    if kind == b"VP8 ":
        if len(payload) < 10 or payload[3:6] != b"\x9d\x01\x2a":
            raise ValueError("lender mark WebP VP8 frame is malformed")
        width_bits, height_bits = struct.unpack("<HH", payload[6:10])
        return width_bits & 0x3FFF, height_bits & 0x3FFF
    if len(payload) < 5 or payload[0] != 0x2F:
        raise ValueError("lender mark WebP VP8L frame is malformed")
    (bits,) = struct.unpack("<I", payload[1:5])
    return 1 + (bits & 0x3FFF), 1 + ((bits >> 14) & 0x3FFF)


def validate_lender_mark(
    lender_name: object,
    lender_nmls_id: object,
    data: bytes,
) -> ReviewedLenderMark:
    """Return the reviewed entry this file is, or raise ValueError (fixed, byte-free messages)."""

    lender, nmls_id = validate_public_lender_identity(lender_name, lender_nmls_id)
    mark = _REVIEWED_LENDER_MARKS.get(lender)
    if mark is None or mark.nmls_id != nmls_id:
        raise ValueError("no reviewed lender mark is recorded for this lender identity")
    if len(data) > MAX_MARK_BYTES:
        raise ValueError("lender mark is larger than 64 KiB")
    media_type: MediaType
    if data.startswith(_PNG_SIGNATURE):
        media_type = "image/png"
        width, height = _png_dimensions(data)
    elif len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        media_type = "image/webp"
        width, height = _webp_dimensions(data)
    else:
        raise ValueError("lender mark must be a PNG or WebP image")
    if not MIN_MARK_HEIGHT <= height <= MAX_MARK_HEIGHT or not height <= width <= 2 * height:
        raise ValueError("lender mark must be 32-512 px tall and 1-2 times as wide as tall")
    digest = hashlib.sha256(data).hexdigest()
    if (digest, media_type, width, height) != (mark.sha256, mark.media_type, mark.width, mark.height):
        raise ValueError("lender mark does not match its reviewed sha256, media type and size")
    return mark
