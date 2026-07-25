"""Exact JSON scalar-shape helpers for signed credential records."""

from __future__ import annotations

from collections.abc import Iterable


def has_exact_string_fields(
    record: dict[str, object],
    names: Iterable[str],
) -> bool:
    """Return whether every named value is an unnormalized JSON string."""

    return all(
        isinstance(record.get(name), str)
        and record.get(name) == str(record.get(name)).strip()
        for name in names
    )
