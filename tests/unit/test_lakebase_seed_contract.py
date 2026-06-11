"""Narrative-seed contract guards (2026-06-11 audit P1-5).

The Lakebase seed's approval rows are the canonical borrower-narrative
trio the Module 0 spec requires ("three high-value borrower examples").
The original seed shipped 5-digit placeholders (B-48291..) that violated
the ``B-[0-9A-Z]{13}`` masked-ID contract and joined to no real
``gold.borrower_360`` row. These guards keep that bug class dead:

* every borrower_id literal in the seed matches the masked-ID format;
* the schema migration that purges the legacy rows and adds the CHECK
  constraint stays present (deploy step 4b applies schema.sql on every
  run, so deleting the block would silently re-open the hole);
* the re-selection helper the seed comment points at actually exists.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SEED = REPO / "lakebase" / "seed_campaigns.sql"
SCHEMA = REPO / "lakebase" / "schema.sql"

BORROWER_ID_FORMAT = re.compile(r"^B-[0-9A-Z]{13}$")
SEED_ID_LITERAL = re.compile(r"'(B-[0-9A-Za-z]+)'")


def test_every_seed_borrower_id_matches_masked_format() -> None:
    text = SEED.read_text(encoding="utf-8")
    ids = SEED_ID_LITERAL.findall(text)
    assert ids, "expected borrower_id literals in the approvals seed"
    offenders = [bid for bid in ids if not BORROWER_ID_FORMAT.match(bid)]
    assert offenders == [], (
        f"seed borrower ids violate B-[0-9A-Z]{{13}}: {offenders} — "
        "re-select with tools/select_narrative_borrowers.sql"
    )


def test_schema_migration_purges_legacy_ids_and_adds_check() -> None:
    text = SCHEMA.read_text(encoding="utf-8")
    assert "2026_06_11_narrative_seed_real_ids" in text
    assert re.search(r"DELETE FROM mip_app\.approvals\s+WHERE borrower_id ~ '\^B-\[0-9\]\{5\}\$'", text)
    assert "approvals_borrower_id_format_chk" in text
    assert "CHECK (borrower_id ~ '^B-[0-9A-Z]{13}$')" in text


def test_reselection_helper_exists_and_covers_all_slots() -> None:
    helper = REPO / "tools" / "select_narrative_borrowers.sql"
    text = helper.read_text(encoding="utf-8")
    for slot in (
        "refi_approve",
        "cashout_approve",
        "heloc_approve",
        "refi_hold",
        "investor_reject",
    ):
        assert slot in text
