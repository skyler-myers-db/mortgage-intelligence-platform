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
   bundle-level `warehouse_id` binding in `databricks.yml`'s dashboards block.

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
    EXECUTIVE.name: 11,  # 4 KPI counters + funnel bar + score line + 2 geography bars + zips table + rate hist + scatter + top-10 table
    SEGMENT.name: 7,     # overview table + 2 bars + v3 pivot + top-3 bar + evidence line + evidence bar
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

FIXED_FOOTPRINT_COPY = (
    "6-state",
    "six-state",
    "IL, CA, FL, TX, WA, CO",
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


def _encoded_columns(widget: dict[str, Any]) -> list[str]:
    """Return every fieldName referenced by a Lakeview widget encoding."""

    cols: list[str] = []

    def _collect(obj: Any) -> None:
        if isinstance(obj, list):
            for item in obj:
                _collect(item)
            return
        if isinstance(obj, dict):
            field_name = obj.get("fieldName")
            if isinstance(field_name, str) and field_name:
                cols.append(field_name)
            for value in obj.values():
                _collect(value)

    _collect((widget.get("spec") or {}).get("encodings") or {})
    return cols


def _iter_dataset_sql(spec: dict[str, Any]):
    for ds in spec.get("datasets", []):
        name = ds.get("name", "<unnamed>")
        lines = ds.get("queryLines") or []
        yield name, "".join(lines)


def _dataset_sql(path: Path, dataset_name: str) -> str:
    spec = _load(path)
    for name, sql in _iter_dataset_sql(spec):
        if name == dataset_name:
            return sql
    raise AssertionError(f"{path.name} missing dataset {dataset_name!r}")


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
def test_dashboard_uses_entrada_theme(path: Path) -> None:
    spec = _load(path)
    ui = spec.get("uiSettings")
    assert isinstance(ui, dict), f"{path.name} must include AI/BI uiSettings"
    theme = ui.get("theme") or {}
    assert ui.get("applyModeEnabled") is True
    assert theme.get("canvasBackgroundColor", {}).get("dark") == "#04101F"
    assert theme.get("widgetBackgroundColor", {}).get("dark") == "#071A2F"
    assert theme.get("selectionColor", {}).get("dark") == "#5CE1E6"
    assert theme.get("widgetHeaderAlignment") == "LEFT"
    assert "Geist" in theme.get("fontFamily", "")
    colors = set(theme.get("visualizationColors") or [])
    assert {"#5CE1E6", "#66C5FF", "#34D399", "#F472B6"} <= colors


@pytest.mark.parametrize("path", DASHBOARD_FILES, ids=lambda p: p.name)
def test_every_analytical_widget_has_fields_selected(path: Path) -> None:
    spec = _load(path)
    for _page_name, widget in _iter_widgets(spec):
        widget_type = (widget.get("spec") or {}).get("widgetType")
        if not widget_type or str(widget_type).startswith("filter"):
            continue
        encoded = _encoded_columns(widget)
        assert encoded, (
            f"{path.name} widget '{widget.get('name')}' has no encoded "
            "fieldName values; Lakeview renders this as 'Visualization has no "
            "fields selected'."
        )


@pytest.mark.parametrize("path", DASHBOARD_FILES, ids=lambda p: p.name)
def test_lakeview_widget_versions_match_renderer_safe_export_shapes(path: Path) -> None:
    spec = _load(path)
    for _page_name, widget in _iter_widgets(spec):
        wname = widget.get("name")
        widget_type = (widget.get("spec") or {}).get("widgetType")
        version = (widget.get("spec") or {}).get("version")
        if widget_type == "table":
            assert version == 2, (
                f"{path.name} table widget '{wname}' must use Lakeview table "
                "spec v2; v1 rendered as 'no fields selected' in the hosted UI."
            )
            assert (widget.get("spec") or {}).get("rowsPerPage"), (
                f"{path.name} table widget '{wname}' must set rowsPerPage."
            )
        elif widget_type == "pivot":
            assert version == 3, (
                f"{path.name} pivot widget '{wname}' must use Lakeview pivot "
                "spec v3 with multi-cell encodings."
            )
            cell = ((widget.get("spec") or {}).get("encodings") or {}).get("cell")
            assert isinstance(cell, dict) and cell.get("type") == "multi-cell"
            assert cell.get("fields"), (
                f"{path.name} pivot widget '{wname}' must declare cell.fields."
            )


def test_executive_geography_uses_renderer_safe_state_bar_not_symbol_map() -> None:
    spec = _load(EXECUTIVE)
    widgets = {widget.get("name"): widget for _page, widget in _iter_widgets(spec)}
    assert "chart_state_opportunity" in widgets
    assert widgets["chart_state_opportunity"]["spec"]["widgetType"] == "bar"
    assert all(
        (widget.get("spec") or {}).get("widgetType") != "symbol-map"
        for widget in widgets.values()
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
def test_query_lines_are_lakeview_concat_safe(path: Path) -> None:
    spec = _load(path)
    for ds in spec.get("datasets", []):
        name = ds.get("name", "<unnamed>")
        lines = ds.get("queryLines") or []
        assert len(lines) == 1, (
            f"{path.name} dataset '{name}' must keep SQL in a single "
            "newline-bearing queryLines string. Databricks Lakeview "
            "concatenates array entries without inserting whitespace."
        )
        sql = "".join(lines)
        assert re.search(r"\bFROM\s+", sql, re.IGNORECASE), (
            f"{path.name} dataset '{name}' SQL does not contain a separated "
            "FROM token; check Lakeview concat safety."
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
    # the dashboard resource level (databricks.yml `dashboards:` block) via
    # `warehouse_id: ${var.sql_warehouse_id}`.
    for banned_key in ("warehouseId", "warehouse_id", "warehouseID"):
        assert banned_key not in text, (
            f"{path.name} contains key '{banned_key}' - warehouse id must "
            "live in databricks.yml's dashboard resource, not the lvdash.json"
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


@pytest.mark.parametrize("path", DASHBOARD_FILES, ids=lambda p: p.name)
def test_dashboard_copy_does_not_pin_fixed_cotality_footprint(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    for phrase in FIXED_FOOTPRINT_COPY:
        assert phrase.lower() not in text.lower(), (
            f"{path.name} contains fixed-footprint copy {phrase!r}; dashboards "
            "must describe current Cotality data coverage dynamically"
        )


def test_executive_funnel_reads_canonical_snapshot() -> None:
    totals_sql = _dataset_sql(EXECUTIVE, "ds_funnel_totals")
    stages_sql = _dataset_sql(EXECUTIVE, "ds_funnel_stages")
    for sql in (totals_sql, stages_sql):
        normalized = sql.lower()
        assert "mip.gold.funnel_snapshot_daily" in normalized
        assert "state = '_all'" in normalized
        assert "segment_code = '_all'" in normalized
        assert "approval_status = 'actioned'" not in normalized


def test_borrower_opportunity_metric_view_aggregates_use_distinct_grain() -> None:
    for path in DASHBOARD_FILES:
        for name, sql in _iter_dataset_sql(_load(path)):
            normalized = re.sub(r"\s+", " ", sql.lower())
            if "mip.semantics.borrower_opportunity_metric_view" not in normalized:
                continue
            unsafe_count = re.search(r"\bcount\s*\(\s*\*\s*\)", normalized)
            has_distinct_clip = "count(distinct clip)" in normalized
            group_by_segment = re.search(r"group by[^;]*(segment|segment_code)", normalized)
            assert not (unsafe_count and not has_distinct_clip and not group_by_segment), (
                f"{path.name} dataset '{name}' aggregates exploded borrower_opportunity_metric_view "
                "without COUNT(DISTINCT clip) or segment grouping"
            )
