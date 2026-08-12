"""The governed result grid is vocabulary, not campaign copy.

`genie_unsafe_visible_field` scans `table_rows` cell by cell. The detectors it
reuses were written for model-authored outreach prose, where "black", a
`-oma` word and a raw column name are real signals. Against a gold `city`
column and a `why_now` column they are pure false positives, and the block
surfaces to the user as "The generated response did not pass the governed
output policy" with no way to tell what happened.

Diagnosed live on paychex 2026-08-11/12 by joining the blocked turns to their
server warnings: every one logged ``unsafe_field: "table_rows"``, and the
blocked turns returned 330-363 rows while every passing turn returned 0-14.
It looked non-deterministic only because Genie re-plans its SQL per turn: a
top-10 answer never projects the offending columns, a full breakdown always
does.
"""

from __future__ import annotations

import pytest

from backend.schemas._validators_unsafe_text import (
    contains_mechanical_pii_or_raw_identifier,
)

# 322 of the 330 distinct `why_now` values in gold carry this phrasing, and
# "Owner Link" is the product's own glossary term (CLAUDE.md domain rules).
_GOVERNED_WHY_NOW = (
    "Owner Link ties 19 related properties, so route the review to an "
    "investor-lending specialist."
)


@pytest.mark.parametrize(
    "value",
    [
        _GOVERNED_WHY_NOW,
        "Owner Link",
        "owner link",
        # `_visible_text_values` flattens Mapping KEYS too, so a projection of
        # this column blocked on the header alone regardless of row content.
        "owner_link_id",
        "owner_link",
    ],
)
def test_the_owner_link_glossary_term_is_showable(value: str) -> None:
    assert contains_mechanical_pii_or_raw_identifier(value) is False, value


@pytest.mark.parametrize(
    "value",
    [
        # The shape that actually leaks an identifier stays blocked...
        "owner_link: ABC123456",
        "owner_link_id = QX7T2M9P44",
        "owner link # 8891234567",
        "clip: 8891234567",
        # ...as do the genuinely PII-bearing column names.
        "clip_ref",
        "raw_clip",
        "borrower_name",
        "owner_name",
        "customer_name",
        "street_address",
        "mailing_address",
    ],
)
def test_identifiers_with_values_and_pii_columns_stay_blocked(value: str) -> None:
    assert contains_mechanical_pii_or_raw_identifier(value) is True, value
