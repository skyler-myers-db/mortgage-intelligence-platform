"""
Shape validation for the Lakeview dashboards that ship with the bundle.

These tests do NOT exercise the Databricks Lakeview renderer (that happens
at `databricks bundle validate -t ci` and again at deploy). They are a cheap
local guard that:

1. Both `.lvdash.json` files are valid JSON.
2. Every page declares at least one widget.
3. Every widget's SQL references only `mip.gold.*` or `mip.semantics.*`
   - never raw/silver tables, never a default-catalog identifier.
4. No emojis leak into titles, descriptions, or SQL.
5. No hardcoded warehouse id is present - the warehouse must come from the
   bundle-level `warehouse_id` binding in `resources/dashboards.yml`.

These guards protect the Module 0 PII + provenance contracts. A dashboard
that silently selects from a raw share table would skip the PII-safe gold
projection and expose real names. A hardcoded warehouse id would break the
self-contained zero-click deploy posture (CLAUDE.md `What to optimize for`).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]
DASHBOARDS_DIR = REPO / "dashboards"

EXECUTIVE = DASHBOARDS_DIR / "executive_dashboard.lvdash.json"
SEGMENT = DASHBOARDS_DIR / "segment_dashboard.lvdash.json"

DASHBOARD_FILES = [EXECUTIVE, SEGMENT]

# Minimum widget counts per dashboard so a stub regression trips this test.
# Update when we intentionally add / remove a page's widgets.
EXPECTED_WIDGET_FLOOR = {
    EXECUTIVE.name: 11,  # 4 KPI counters + funnel bar + score line + map + avm bar + zips table + rate hist + scatter + top-10 table
    SEGMENT.name: 7,     # overview table + 2 bars + pivot + top-3 bar + evidence line + evidence bar
}

# Any table reference that is NOT in this allowlist fails the test. We
# intentionally constrain to the gold + semantics schemas - dashboards must
# never read raw or silver directly.
ALLOWED_TABLE_PREFIXES = ("mip.gold.", "mip.semantics.")

# Forbidden: any raw/silver read, any non-mip catalog read, any hardcoded
# warehouse id template (`warehouseId`, bare 32-hex workspace ids).
FORBIDDEN_TABLE_SUBSTRINGS = (
    "mip.silver.",
    "mip.raw.",
    "cotality_mortgage_data.",
    "hive_metastore.",
)

# Emoji range covers the Unicode blocks commonly used. We reject them
# entirely - dashboards rendered to executives must stay text-only.
EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"  # symbols & pictographs, extended
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F680-\U0001F6FF"  # transport & map
    "\U0001F900-\U0001F9FF"  # supplemental symbols
    "☀-➿"           # misc symbols + dingbats
    "]",
    flags=re.UNICODE,
)

# A 32-hex id would be a hardcoded warehouse or workspace id. The bundle
# injects warehouse_id at the dashboard resource level, not inside the JSON.
HEX32_RE = re.compile(r"\b[0-9a-fA-F]{32}\b")


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_widgets(spec: dict[str, Any]):
    for page in spec.get("pages", []):
        for entry in page.get("layout", []):
            widget = entry.get("widget")
            if widget is None:
                continue
            yield page.get("name", "<unnamed>"), widget


def _iter_dataset_sql(spec: dict[str, Any]):
    for ds in spec.get("datasets", []):
        name = ds.get("name", "<unnamed>")
        lines = ds.get("queryLines") or []
        yield name, "\n".join(lines)


@pytest.mark.parametrize("path", DASHBOARD_FILES, ids=lambda p: p.name)
def test_dashboard_json_parses(path: Path) -> None:
    spec = _load(path)
    assert isinstance(spec, dict), f"{path.name} root must be a JSON object"
    assert spec.get("datasets"), f"{path.name} must declare at least one dataset"
    assert spec.get("pages"), f"{path.name} must declare at least one page"


@pytest.mark.parametrize("path", DASHBOARD_FILES, ids=lambda p: p.name)
def test_every_page_has_at_least_one_widget(path: Path) -> None:
    spec = _load(path)
    for page in spec["pages"]:
        layout = page.get("layout", [])
        widgets = [entry for entry in layout if entry.get("widget")]
        assert widgets, (
            f"{path.name} page '{page.get('name')}' declares no widgets - "
            "stub regression"
        )


@pytest.mark.parametrize("path", DASHBOARD_FILES, ids=lambda p: p.name)
def test_widget_count_floor(path: Path) -> None:
    spec = _load(path)
    widgets = list(_iter_widgets(spec))
    floor = EXPECTED_WIDGET_FLOOR[path.name]
    assert len(widgets) >= floor, (
        f"{path.name} has {len(widgets)} widgets, expected at least {floor} "
        f"(see EXPECTED_WIDGET_FLOOR in this test for why)"
    )


@pytest.mark.parametrize("path", DASHBOARD_FILES, ids=lambda p: p.name)
def test_dataset_sql_targets_only_gold_or_semantics(path: Path) -> None:
    spec = _load(path)
    # Find all `mip.<schema>.<table>` references and confirm schema is
    # gold or semantics. We do NOT parse SQL - a substring scan is enough
    # because our dataset SQL is authored, not user-generated.
    table_ref = re.compile(r"\bmip\.[a-zA-Z_]+\.[a-zA-Z_0-9]+\b")
    for name, sql in _iter_dataset_sql(spec):
        refs = set(table_ref.findall(sql))
        assert refs, f"{path.name} dataset '{name}' references no mip.* table"
        for ref in refs:
            assert ref.startswith(ALLOWED_TABLE_PREFIXES), (
                f"{path.name} dataset '{name}' reads from '{ref}' - only "
                f"{ALLOWED_TABLE_PREFIXES} are allowed"
            )
        for forbidden in FORBIDDEN_TABLE_SUBSTRINGS:
            assert forbidden not in sql, (
                f"{path.name} dataset '{name}' references forbidden schema "
                f"'{forbidden}' - dashboards must read gold/semantics only"
            )


@pytest.mark.parametrize("path", DASHBOARD_FILES, ids=lambda p: p.name)
def test_no_emojis(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    match = EMOJI_RE.search(text)
    assert match is None, (
        f"{path.name} contains emoji char {match.group()!r} at index "
        f"{match.start()}"
    )


@pytest.mark.parametrize("path", DASHBOARD_FILES, ids=lambda p: p.name)
def test_no_hardcoded_warehouse_or_workspace_id(path: Path) -> None:
    spec = _load(path)
    text = path.read_text(encoding="utf-8")
    # The dashboard JSON must NOT pin a warehouse id. The bundle wires it at
    # the dashboard resource level (resources/dashboards.yml) via
    # `warehouse_id: ${var.sql_warehouse_id}`.
    for banned_key in ("warehouseId", "warehouse_id", "warehouseID"):
        assert banned_key not in text, (
            f"{path.name} contains key '{banned_key}' - warehouse id must "
            "live in resources/dashboards.yml, not in the lvdash.json"
        )
    # And no bare 32-hex workspace-id-looking token.
    hex_hits = HEX32_RE.findall(text)
    assert not hex_hits, (
        f"{path.name} contains a 32-hex token {hex_hits[0]!r} - looks like a "
        "hardcoded workspace/warehouse id"
    )
    # Defensive: spec shouldn't carry a top-level warehouseId either.
    assert "warehouseId" not in spec, (
        f"{path.name} declares a top-level warehouseId key"
    )
