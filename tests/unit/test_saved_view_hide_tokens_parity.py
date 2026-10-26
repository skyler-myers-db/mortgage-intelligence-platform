"""The ``hide`` vocabulary is one list on both sides (W5e, tables-05 step 2).

``frontend/src/components/mortgage/LeadTable.columns.ts`` exports
``LEAD_TABLE_HIDE_VOCABULARY`` (URL token -> column key, in column-model
order); ``backend/services/saved_view_params.py`` mirrors the tokens as
``LEAD_TABLE_HIDE_TOKENS`` so a saved view validates exactly what the Lead
Queue writes. This parses the exported TS array LITERAL, so a token added,
renamed or reordered on one side only fails here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.services.saved_view_params import (
    LEAD_TABLE_HIDE_TOKENS,
    SAVED_VIEW_PARAMS_REFUSED,
    canonical_saved_view_params,
)

COLUMNS_TS = Path(__file__).resolve().parents[2] / "frontend/src/components/mortgage/LeadTable.columns.ts"
NEVER_HIDEABLE_KEYS = {"select", "expand", "borrower", "offer", "score", "confidence", "approval"}


def _ts_vocabulary() -> list[tuple[str, str]]:
    source = COLUMNS_TS.read_text(encoding="utf-8")
    block = re.search(r"export const LEAD_TABLE_HIDE_VOCABULARY = \[(.*?)\] as const", source, re.S)
    assert block, "LEAD_TABLE_HIDE_VOCABULARY literal not found"
    return re.findall(r"\{\s*token:\s*'([a-z-]+)',\s*key:\s*'([A-Za-z]+)'\s*\}", block.group(1))


def test_the_backend_tokens_equal_the_frontend_vocabulary_in_order() -> None:
    entries = _ts_vocabulary()
    assert len(entries) == 9
    assert tuple(token for token, _key in entries) == LEAD_TABLE_HIDE_TOKENS


def test_no_evidence_confidence_or_approval_column_is_hideable() -> None:
    keys = {key for _token, key in _ts_vocabulary()}
    assert not keys & NEVER_HIDEABLE_KEYS


def test_hide_is_canonicalised_in_column_order() -> None:
    canonical, _digest = canonical_saved_view_params("state=IL&hide=rate,segments,location")
    assert canonical == "hide=location%2Csegments%2Crate&state=IL"


@pytest.mark.parametrize(
    "raw",
    ["hide=none", "hide=rate,rate", "hide=borrower", "hide=score", "hide=approval", "hide=", "hide=rate,", "hide=zyrplax"],
)
def test_a_sentinel_duplicate_unknown_or_unhideable_token_is_refused(raw: str) -> None:
    with pytest.raises(ValueError) as refused:
        canonical_saved_view_params(raw)
    assert str(refused.value) == SAVED_VIEW_PARAMS_REFUSED
