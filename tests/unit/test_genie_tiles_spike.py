"""wow-ai-5 spike tooling (tools/genie_tiles_spike.py): offline mode only in W5b.

The measurement is deterministic and read-only over the guards; these tests
run it on a shrunken corpus so they stay fast. The full run is
``python -m tools.genie_tiles_spike --offline`` (numbers in
docs/genie-tiles-spike.md).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tools import genie_tiles_spike as spike

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "genie_tiles_spike.py"


def test_the_offline_corpus_is_deterministic_and_tile_shaped() -> None:
    first = [spike.tile_rows(tile, 50) for tile in range(spike.TILE_COUNT)]
    second = [spike.tile_rows(tile, 50) for tile in range(spike.TILE_COUNT)]

    assert first == second
    for rows in first:
        assert len(rows) == 50
        assert all(len(row) <= spike.MAX_COLUMNS for row in rows)
        assert all(re.fullmatch(r"B-[0-9A-Z]{13}", row["top_borrower_id"]) for row in rows)
        assert all(row["contact_email"].endswith(".example") for row in rows)


def test_the_offline_measurement_counts_guard_work_and_emits_no_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(spike, "ROW_SIZES", (2, 4))
    monkeypatch.setattr(spike, "TILE_COUNT", 2)

    result = spike.measure_offline()
    text = json.dumps(result)

    assert set(result["sizes"]) == {"2", "4"}
    assert result["sizes"]["4"]["guard_calls"] > result["sizes"]["2"]["guard_calls"] > 0
    assert result["narrative_baseline"]["guard_calls"] > 0
    assert result["criterion_1"]["verdict"] in {"PASS", "FAIL"}
    for leaked in ("Texas", "County A", "B-", "summit.example", "SELECT", "Prime Refi", "512-555"):
        assert leaked not in text, leaked


def test_the_row_path_fails_closed_on_a_pii_shaped_cell() -> None:
    asset = sorted(spike._trusted_genie_asset_names())[0]
    rows = spike.tile_rows(spike.TILE_COUNT - 1, 3)

    outcome = spike.refresh_row_path(spike.tile_sql(0, asset), rows, asset)

    assert outcome["sql_trusted"] is True
    assert outcome["flagged_cells"] > 0


def test_the_tool_is_read_only_over_the_guards_and_imported_by_nothing() -> None:
    source = TOOL.read_text(encoding="utf-8")

    assert not re.search(r"\bsetattr\(|\.monkeypatch|importlib\.reload|sys\.modules\[", source)
    assert "from tests" not in source and "import tests" not in source
    for base in (ROOT / "backend", ROOT / "frontend" / "src"):
        for path in base.rglob("*"):
            if path.suffix in {".py", ".ts", ".tsx"} and "node_modules" not in path.parts:
                assert "genie_tiles_spike" not in path.read_text(encoding="utf-8"), path


def test_live_mode_is_not_built_in_this_wave() -> None:
    with pytest.raises(SystemExit):
        spike.main([])
