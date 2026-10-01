"""Readable labels for a forwarded identity, derived and never looked up.

Moved verbatim from ``backend/api/session.py`` so a service (the approval
ledger) can label an actor without importing a router module.
"""

import re

# ``jane.doe`` / ``jane_doe`` / ``jane-doe``: letters-only words joined by one
# separator. Anything else (digits, a single token, other punctuation) is
# shown verbatim rather than guessed at.
_NAME_WORDS_RE = re.compile(r"[A-Za-z]+(?:[._-][A-Za-z]+)+")


def display_name_for(identity: str | None) -> str | None:
    """Readable label for a forwarded identity; derived, never looked up."""
    if not identity:
        return None
    local, at, _domain = identity.partition("@")
    if not at:
        return identity
    if not local:
        return identity
    if _NAME_WORDS_RE.fullmatch(local):
        words = re.split(r"[._-]", local)
        return " ".join(word[:1].upper() + word[1:] for word in words)
    return local
