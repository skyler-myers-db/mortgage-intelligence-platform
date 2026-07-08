"""Deterministic address canonicalization for the governed property loan lookup.

The Module 0 "property loan lookup" resolves a caller-supplied street
address + ZIP to a governed ``mip.gold.address_lookup`` row **without ever
storing or returning the street address itself**. The join key is a salt-free
SHA-256 of a canonicalized ``address || '|' || zip5`` string. The hash is
computed at ETL refresh time (see
``sql/transformations/gold_address_lookup.sql``) and re-derived here at request
time; the two implementations MUST agree byte-for-byte or every lookup misses.

Canonicalization spec (v1 -- exact-after-canonicalization, NO fuzzy matching,
NO USPS suffix/abbreviation dictionaries):

    1. UPPER-case.
    2. TRIM leading / trailing whitespace.
    3. Collapse internal whitespace runs (spaces, tabs, newlines) to a single
       ASCII space.
    4. Strip the characters ``.`` ``,`` ``#`` entirely (they are removed, not
       replaced with a space).
    5. Expand nothing else. "123 N. Main St." and "123 N Main St" canonicalize
       to the same string ("123 N MAIN ST"), but "123 North Main Street" does
       NOT -- v1 is a share-scoped exact lookup spine, not a CLIP mastering
       service.

Unicode is passed through untouched by the canonicalizer (accented characters,
non-Latin scripts survive) so that a source row carrying a unicode address and
a caller typing the same unicode string still hash-match. Only ASCII casing and
the three ASCII punctuation characters above are normalized.

The hash function mirrors Spark's ``sha2(concat_ws('|', norm, zip5), 256)``:
lowercase hex, 64 characters. ``hashlib.sha256(...).hexdigest()`` already emits
lowercase hex, matching Spark's ``sha2`` convention, so no case coercion is
needed here -- but the golden parity test pins the exact hex values so a future
edit that diverges from Spark fails loudly.
"""
from __future__ import annotations

import hashlib
import re

# Whitespace-run collapser. ``\s+`` matches spaces, tabs, newlines, and other
# Unicode whitespace; we replace every run with a single ASCII space so the
# canonical form has exactly one separator between tokens.
_WHITESPACE_RUN = re.compile(r"\s+")

# Characters removed outright (not replaced with a space) during
# canonicalization. Kept as a translation table for speed + clarity; adding a
# character here is a hashing-contract change and must be mirrored in
# ``sql/transformations/gold_address_lookup.sql``.
_STRIP_CHARS = str.maketrans("", "", ".,#")


def normalize_address(line: str) -> str:
    """Return the canonical form of a street-address line.

    Implements the v1 spec documented in the module docstring:
    UPPER -> TRIM -> collapse internal whitespace -> strip ``.`` ``,`` ``#``.
    No abbreviation expansion. Unicode passes through (only ASCII casing +
    the three ASCII punctuation characters are normalized).

    ``None``-ish / empty input canonicalizes to the empty string; the caller
    (the hash function + service) decides whether an empty canonical form is a
    usable lookup key.
    """
    if not line:
        return ""
    # 1. UPPER. 4. strip punctuation. Order between upper and strip is
    #    irrelevant (the stripped chars are case-neutral) but we upper first
    #    to match the SQL header's stated order exactly.
    upper = line.upper().translate(_STRIP_CHARS)
    # 2 + 3. collapse whitespace runs to a single space, then trim the ends.
    #    Doing the collapse before the final strip means a leading run that
    #    became a single space is removed by ``.strip()``.
    collapsed = _WHITESPACE_RUN.sub(" ", upper).strip()
    return collapsed


def _normalize_zip5(zip5: str) -> str:
    """Return the first 5 ASCII digits of ``zip5`` (no padding, no expansion).

    The gold table stores ``zip5`` already-truncated to 5 characters (see
    ``silver_lien_current.sql`` / ``silver_property_master.sql`` ZIP5 contract),
    so we mirror that: strip non-digits, keep the first 5. A caller passing a
    ZIP+4 ("60601-1234") therefore hashes identically to the stored "60601".
    """
    digits = re.sub(r"[^0-9]", "", zip5 or "")
    return digits[:5]


def address_lookup_hash(line: str, zip5: str) -> str:
    """Return the SHA-256 join key for ``(address_line, zip5)``.

    Mirrors the gold CTAS:
    ``sha2(concat(<normalized_address>, '|', <zip5>), 256)``. The separator is a
    literal ``|`` between the canonical address and the 5-digit ZIP so that
    ("12 A" , "34567") and ("12" , "A34567") cannot collide into the same key.

    Returns 64 lowercase hex characters -- the exact shape Spark's ``sha2(...,
    256)`` emits.
    """
    normalized = normalize_address(line)
    zip_part = _normalize_zip5(zip5)
    payload = f"{normalized}|{zip_part}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = ["address_lookup_hash", "normalize_address"]
