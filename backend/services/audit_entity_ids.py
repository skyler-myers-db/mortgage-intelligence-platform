"""Letter images of hex digests for server-built audit ``entity_id`` values.

``entity_id`` is a top-level audit column, and every write runs it through
``validate_public_audit_identifier_or_none``, which refuses email-, SSN- and
phone-shaped text. Ten hex characters in sixteen are digits, so a 16-character
digest slice holds a ten-digit run (a US phone shape) about 1.4% of the time,
and the whole write is refused. A server-built id therefore never embeds raw
hex: it embeds this image, ``0-9a-f`` mapped onto ``a-p``. The image has no
digit and no ``@``, so no digest can form any of those shapes, and the
validator stays closed for every other id.

Shared by the Genie and growth-agent refusal ids (``genie_audit``) and the
``PRINT_EXPORT``, ``AUDIT_EXPORT`` and ``LEAD_EXPORT`` receipts. The mapping is
part of the stored ids: a refusal report re-derives a ``geniehash-`` id from it,
so it never changes.
"""

from __future__ import annotations

_HEX_TO_LETTERS = str.maketrans("0123456789abcdef", "abcdefghijklmnop")


def audit_safe_letters(sha256_hex: str) -> str:
    """Letter image of the first 24 characters of a SHA-256 hex digest.

    A caller that holds only the digest reproduces the id exactly: a
    hash-only refusal report holds the full digest of the refused question,
    and an auditor holding a receipt's stored ``filter_fingerprint`` derives
    the receipt's ``entity_id``.
    """

    return sha256_hex[:24].translate(_HEX_TO_LETTERS)
