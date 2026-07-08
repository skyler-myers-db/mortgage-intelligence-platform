"""Golden parity tests for the property-loan-lookup address canonicalizer.

The Python ``normalize_address`` / ``address_lookup_hash`` MUST produce the
exact same hash as the Spark CTAS in
``sql/transformations/gold_address_lookup.sql`` (``sha2(CONCAT(norm, '|',
zip5), 256)``), or every lookup misses. The hex values below are pinned so a
future edit that diverges from Spark semantics fails loudly here.

Spark equivalence: ``sha2(<utf8-string>, 256)`` emits lowercase hex and is
byte-identical to ``hashlib.sha256(<string>.encode('utf-8')).hexdigest()``.
Verified against the reference string ``"123 MAIN ST|60601"`` ->
``3092b0f4...`` (see fixture 0).
"""
from __future__ import annotations

import pytest

from backend.services.address_normalization import (
    address_lookup_hash,
    normalize_address,
)

# (label, address_line, zip5, expected_normalized, expected_hash)
# >=8 pairs covering messy spacing, punctuation, case, ZIP+4, unicode.
_GOLDEN: list[tuple[str, str, str, str, str]] = [
    (
        "plain",
        "123 Main St",
        "60601",
        "123 MAIN ST",
        "3092b0f40871db7a6598a53be6f17818a36bde8b66387bca0ffe313fddd27b01",
    ),
    (
        "messy_spacing_collapses",
        "  123   Main   St  ",
        "60601",
        "123 MAIN ST",
        "3092b0f40871db7a6598a53be6f17818a36bde8b66387bca0ffe313fddd27b01",
    ),
    (
        "punctuation_stripped",
        "123 N. Main St.",
        "60601",
        "123 N MAIN ST",
        "7e899ba88803531bfa4ce44ebdb873e0b0364a8bbadc322e80ad1031e86f3a07",
    ),
    (
        "lowercase_equals_uppercase",
        "123 n main st",
        "60601",
        "123 N MAIN ST",
        "7e899ba88803531bfa4ce44ebdb873e0b0364a8bbadc322e80ad1031e86f3a07",
    ),
    (
        "comma_and_hash_stripped",
        "123 Main St, Apt #4",
        "60601",
        "123 MAIN ST APT 4",
        "c0d2a6a512c730163dfa089d11ebb89f39e1114201303a9fbf869451473ccd54",
    ),
    (
        "oak_ave",
        "456 Oak Ave",
        "90210",
        "456 OAK AVE",
        "6882db346403112d5e49c9610a205591cc4052e975af8418ff070d908765b6e3",
    ),
    (
        "oak_ave_uppercase_equal",
        "456 OAK AVE",
        "90210",
        "456 OAK AVE",
        "6882db346403112d5e49c9610a205591cc4052e975af8418ff070d908765b6e3",
    ),
    (
        "zip_plus4_truncates",
        "789 Elm Blvd",
        "10001-1234",
        "789 ELM BLVD",
        "cdfeeea00e434bbff3aed9a74cbf2b4b24b4d01f75b5c3ef2d430537fc5193ea",
    ),
    (
        "zip5_equals_zip_plus4",
        "789 Elm Blvd",
        "10001",
        "789 ELM BLVD",
        "cdfeeea00e434bbff3aed9a74cbf2b4b24b4d01f75b5c3ef2d430537fc5193ea",
    ),
    (
        "unicode_passthrough",
        "101 Café Terrace",
        "02139",
        "101 CAFÉ TERRACE",
        "b6b5340bd48f01cf17d96f7afe0a77a5136625a39c098883aacfdb6defe03b70",
    ),
    (
        "unicode_uppercase_equal",
        "101 CAFÉ TERRACE",
        "02139",
        "101 CAFÉ TERRACE",
        "b6b5340bd48f01cf17d96f7afe0a77a5136625a39c098883aacfdb6defe03b70",
    ),
]


@pytest.mark.parametrize(
    ("label", "line", "zip5", "expected_norm", "expected_hash"),
    _GOLDEN,
    ids=[row[0] for row in _GOLDEN],
)
def test_golden_hash_parity(
    label: str, line: str, zip5: str, expected_norm: str, expected_hash: str
) -> None:
    assert normalize_address(line) == expected_norm
    assert address_lookup_hash(line, zip5) == expected_hash
    # Hash is lowercase hex, 64 chars (Spark sha2(..., 256) shape).
    got = address_lookup_hash(line, zip5)
    assert len(got) == 64
    assert got == got.lower()


def test_canonicalization_equivalence_classes() -> None:
    """Messy / punctuated / cased / ZIP+4 variants collapse to one hash."""
    # Spacing + punctuation + case all canonicalize together.
    assert address_lookup_hash("  123   Main   St  ", "60601") == address_lookup_hash(
        "123 Main St", "60601"
    )
    assert address_lookup_hash("123 N. Main St.", "60601") == address_lookup_hash(
        "123 n main st", "60601"
    )
    # ZIP+4 truncates to the stored ZIP5.
    assert address_lookup_hash("789 Elm Blvd", "10001-1234") == address_lookup_hash(
        "789 Elm Blvd", "10001"
    )


def test_no_abbreviation_expansion() -> None:
    """v1 is exact-after-canonicalization: 'Street' != 'St'."""
    assert normalize_address("123 Main Street") != normalize_address("123 Main St")
    assert address_lookup_hash("123 Main Street", "60601") != address_lookup_hash(
        "123 Main St", "60601"
    )


def test_concat_separator_prevents_collision() -> None:
    """The literal '|' between address and ZIP prevents boundary collisions."""
    assert address_lookup_hash("12 A", "34567") != address_lookup_hash("12", "A34567")


def test_empty_and_blank_inputs() -> None:
    assert normalize_address("") == ""
    assert normalize_address("   ") == ""
    # A blank address still produces a (stable) hash of "|<zip>"; the gold CTAS
    # + service exclude blank canonical forms from ever becoming a key, so this
    # only asserts the function is total, not that a blank is a valid lookup.
    assert len(address_lookup_hash("", "60601")) == 64
