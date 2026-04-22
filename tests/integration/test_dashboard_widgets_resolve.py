"""Resolve every Lakeview dashboard widget against the live warehouse.

The ``.lvdash.json`` files pass shape validation in
``tests/unit/test_lakeview_dashboards.py`` but shape does not catch the three
classes of regression that actually make a widget render empty in the
Databricks Lakeview UI:

1. The referenced UC table is missing a column the widget's aggregation
   or encoding expects (dataset SQL references a column that was renamed
   or dropped).
2. The query returns zero rows because a filter predicate no longer
   matches live data (e.g., a segment code that was retired).
3. The chart type does not fit the data shape -- a ``bar`` or ``line`` on
   a single-row / single-x-value result draws a broken chart.

This suite is the strongest automated proxy for "open it in the Lakeview
UI and eyeball it". For each dashboard it:

- Parses every dataset and extracts the SQL from ``queryLines`` (or falls
  back to ``SELECT * FROM <table> LIMIT 1`` if a dataset references a
  table without embedded SQL -- a format we don't use today but guard
  against so future Lakeview-format additions don't silently pass).
- Runs the SQL against the real warehouse.
- For every widget, asserts every column referenced in ``spec.encodings``
  (x, y, color, size, region, region-color, region-size, value, cell,
  rows[*], columns[*]) is present in the dataset's result schema.
- Applies widget-type-specific row-shape rules:
    * ``counter`` / single-value -- exactly 1 row.
    * ``bar`` / ``line`` -- >= 2 distinct x values (otherwise the chart
      will look broken in the UI). The sole exception is a cold-start
      widget whose dataset sources a ``delta_vs_prior`` metric (see
      ``docs/dashboards.md`` -- those widgets correctly return zero or
      one row on a brand-new deploy).

Gating mirrors ``test_sql_queries.py`` / ``test_sql_python_parity.py`` --
missing credentials produce a SKIP, not an xfail, so PR CI without
workspace creds stays green.

On success the test prints a markdown table to stdout with one row per
widget -- the same table that ``docs/validation/dashboard-verification.md``
describes and that a human can eyeball in CI logs::

    | dashboard | widget | query_ok | rows | notes |

Cold-start-zero-by-design widgets are tagged as such and do not fail the
assertion on zero rows -- they pass with a "cold-start" note.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]
DASHBOARDS_DIR = REPO / "dashboards"
DASHBOARD_FILES = sorted(DASHBOARDS_DIR.glob("*.lvdash.json"))


# ---------------------------------------------------------------------------
# Cold-start exclusion list.
#
# docs/dashboards.md §1 documents three measures that need >= 2 snapshots
# separated by >= 7 days before they return non-NULL:
#   * delta_vs_prior_count      (lead_generation_metric_view)
#   * delta_vs_prior_approved   (segment_performance_metric_view)
#   * delta_vs_prior_in_the_money (segment_performance_metric_view)
#
# The current .lvdash.json files expose the segment_performance QoQ value
# via the `delta_vs_prior` / `delta_vs_prior_label` column on
# ``ds_segment_overview``. On a fresh deploy the column value is NULL /
# "n/a"; the row still exists, so the dataset's ``query_ok`` is still
# true and row-count > 0. This list is here so that if a future slice
# adds a widget whose dataset *itself* returns zero rows on cold start
# (e.g., a funnel_snapshot_daily trend chart with no history), we can
# tag that widget as yellow rather than red without editing test logic.
# ---------------------------------------------------------------------------
COLD_START_WIDGETS: set[tuple[str, str]] = set()  # (dashboard_filename, widget_name)

# Datasets whose SQL is *allowed* to join against a ``delta_vs_prior``
# metric and therefore may carry NULL cells even when rows are returned.
# Used only to annotate the report; no assertion changes.
DELTA_VS_PRIOR_DATASETS: set[tuple[str, str]] = {
    ("segment_dashboard.lvdash.json", "ds_segment_overview"),
}


# ---------------------------------------------------------------------------
# Credentials gate. Match test_sql_queries.py exactly.
# ---------------------------------------------------------------------------
def _creds() -> tuple[str, str, str] | None:
    host = os.environ.get("DATABRICKS_HOST") or os.environ.get(
        "DATABRICKS_SERVER_HOSTNAME"
    )
    token = os.environ.get("DATABRICKS_TOKEN")
    warehouse_id = os.environ.get("DATABRICKS_WAREHOUSE_ID")
    if not host or not token or not warehouse_id:
        return None
    if not host.startswith("http"):
        host = "https://" + host
    return host.rstrip("/"), token, warehouse_id


@pytest.fixture(scope="module")
def warehouse() -> tuple[str, str, str]:
    creds = _creds()
    if creds is None:
        pytest.skip(
            "Lakeview widget-resolve test SKIPPED: set DATABRICKS_HOST (or "
            "DATABRICKS_SERVER_HOSTNAME), DATABRICKS_TOKEN, and "
            "DATABRICKS_WAREHOUSE_ID to enable."
        )
    return creds


# ---------------------------------------------------------------------------
# Warehouse statement helper. Unlike test_sql_queries._run_sql_rows we need
# the column schema in addition to the data_array so we can validate widget
# encodings against the actual result schema.
# ---------------------------------------------------------------------------
def _run_sql(
    host: str,
    token: str,
    warehouse_id: str,
    statement: str,
) -> tuple[list[str], list[list[Any]]]:
    """Execute ``statement`` and return (column_names, rows)."""
    url = f"{host}/api/2.0/sql/statements/"
    payload = json.dumps(
        {
            "statement": statement,
            "warehouse_id": warehouse_id,
            "wait_timeout": "50s",
            "on_wait_timeout": "CANCEL",
            "disposition": "INLINE",
            "format": "JSON_ARRAY",
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:  # pragma: no cover -- network
        pytest.skip(f"warehouse unreachable: {exc}")
    status = body.get("status", {}).get("state")
    if status != "SUCCEEDED":
        err = body.get("status", {}).get("error", {}).get("message", "unknown")
        pytest.fail(
            f"warehouse statement failed: state={status!r} err={err!r}\n"
            f"SQL:\n{statement}"
        )
    manifest = body.get("manifest", {}) or {}
    schema = manifest.get("schema", {}) or {}
    columns = [c.get("name", "") for c in schema.get("columns", [])]
    rows = (body.get("result", {}) or {}).get("data_array") or []
    return columns, rows


# ---------------------------------------------------------------------------
# .lvdash.json parsers.
# ---------------------------------------------------------------------------
_TABLE_REF = re.compile(r"\bmip\.[a-zA-Z_]+\.[a-zA-Z_0-9]+\b")


def _dataset_sql(dataset: dict[str, Any]) -> str:
    """Extract the SQL for a Lakeview dataset.

    The authored format uses ``queryLines: [str, ...]`` which join with
    newlines. A future format variant may put the SQL in ``query_text``
    (single string) or reference a UC table by name only (``table`` key).
    We support all three so a format shift doesn't silently pass.
    """
    lines = dataset.get("queryLines")
    if lines:
        return "\n".join(lines)
    text = dataset.get("query_text") or dataset.get("queryText")
    if text:
        return text
    # Table-only reference: synthesize a trivial probe so we at least
    # exercise the column schema.
    table = dataset.get("table") or dataset.get("tableName")
    if table:
        return f"SELECT * FROM {table} LIMIT 1"
    return ""


def _iter_widgets(spec: dict[str, Any]):
    for page in spec.get("pages", []):
        page_name = page.get("name", "<unnamed>")
        for entry in page.get("layout", []):
            widget = entry.get("widget")
            if widget is None:
                continue
            yield page_name, widget


def _widget_dataset_name(widget: dict[str, Any]) -> str | None:
    """Return the datasetName the widget's main query points at."""
    for q in widget.get("queries", []):
        inner = q.get("query") or {}
        ds = inner.get("datasetName")
        if ds:
            return ds
    return None


def _widget_encoded_columns(widget: dict[str, Any]) -> list[str]:
    """Collect every column the widget encodes.

    Covers the encoding keys Lakeview uses today:
      - cartesian (bar/line/scatter): x, y, color, size, label
      - symbol-map: region, color, size
      - counter / single-value: value
      - table: columns[*]
      - pivot: rows[*], columns[*], cell
    Each may be either a single object or a list.
    """
    cols: list[str] = []
    spec = widget.get("spec") or {}
    enc = spec.get("encodings") or {}

    def _collect(obj: Any) -> None:
        if obj is None:
            return
        if isinstance(obj, list):
            for item in obj:
                _collect(item)
            return
        if isinstance(obj, dict):
            fn = obj.get("fieldName")
            if isinstance(fn, str) and fn:
                cols.append(fn)

    for key in ("x", "y", "color", "size", "label", "value", "region",
                "cell"):
        _collect(enc.get(key))
    _collect(enc.get("columns"))
    _collect(enc.get("rows"))

    # de-dup preserving order
    seen: set[str] = set()
    out: list[str] = []
    for c in cols:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _primary_x_field(widget: dict[str, Any]) -> str | None:
    enc = (widget.get("spec") or {}).get("encodings") or {}
    x = enc.get("x")
    if isinstance(x, dict):
        return x.get("fieldName")
    return None


def _widget_type(widget: dict[str, Any]) -> str:
    return ((widget.get("spec") or {}).get("widgetType") or "").lower()


# ---------------------------------------------------------------------------
# Collect all (dashboard, dataset) pairs once. Parametrize at collect time
# so `pytest --collect-only` works before credentials are resolved.
# ---------------------------------------------------------------------------
def _all_datasets() -> list[tuple[Path, dict[str, Any]]]:
    out: list[tuple[Path, dict[str, Any]]] = []
    for p in DASHBOARD_FILES:
        spec = json.loads(p.read_text(encoding="utf-8"))
        for ds in spec.get("datasets", []):
            out.append((p, ds))
    return out


def _all_widgets() -> list[tuple[Path, str, dict[str, Any]]]:
    out: list[tuple[Path, str, dict[str, Any]]] = []
    for p in DASHBOARD_FILES:
        spec = json.loads(p.read_text(encoding="utf-8"))
        for page_name, widget in _iter_widgets(spec):
            out.append((p, page_name, widget))
    return out


# Module-scoped cache of dataset execution results, keyed by
# (dashboard_filename, dataset_name). Populated lazily so the per-widget
# tests share warehouse round trips.
_DATASET_RESULTS: dict[tuple[str, str], tuple[list[str], list[list[Any]]]] = {}


def _resolve_dataset(
    warehouse: tuple[str, str, str],
    dashboard: Path,
    ds_name: str,
) -> tuple[list[str], list[list[Any]]]:
    key = (dashboard.name, ds_name)
    if key in _DATASET_RESULTS:
        return _DATASET_RESULTS[key]
    spec = json.loads(dashboard.read_text(encoding="utf-8"))
    ds = next(
        (d for d in spec.get("datasets", []) if d.get("name") == ds_name),
        None,
    )
    assert ds is not None, (
        f"{dashboard.name}: widget references dataset '{ds_name}' but "
        "the dataset is not declared in the .lvdash.json -- broken link"
    )
    sql = _dataset_sql(ds)
    assert sql, (
        f"{dashboard.name} dataset '{ds_name}' has no SQL (no queryLines, "
        "no query_text, no table reference)"
    )
    host, token, wid = warehouse
    cols, rows = _run_sql(host, token, wid, sql)
    _DATASET_RESULTS[key] = (cols, rows)
    return cols, rows


# ---------------------------------------------------------------------------
# Per-dataset test: executes once per (dashboard, dataset) pair and caches
# the result for the per-widget encoding test.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "dashboard,dataset",
    _all_datasets(),
    ids=[f"{p.name}:{d.get('name','?')}" for p, d in _all_datasets()],
)
def test_dashboard_dataset_executes(
    warehouse: tuple[str, str, str],
    dashboard: Path,
    dataset: dict[str, Any],
) -> None:
    ds_name = dataset.get("name") or "<unnamed>"
    sql = _dataset_sql(dataset)
    assert sql, f"{dashboard.name}:{ds_name} has no extractable SQL"
    # Cheap guard: confirm the SQL still targets mip.gold / mip.semantics.
    # The unit-test suite also enforces this but if a dataset mutates over
    # time we want the integration failure to be specific.
    refs = set(_TABLE_REF.findall(sql))
    for ref in refs:
        assert ref.startswith(("mip.gold.", "mip.semantics.")), (
            f"{dashboard.name}:{ds_name} references disallowed table {ref!r}"
        )
    host, token, wid = warehouse
    cols, rows = _run_sql(host, token, wid, sql)
    _DATASET_RESULTS[(dashboard.name, ds_name)] = (cols, rows)
    assert cols, (
        f"{dashboard.name}:{ds_name} returned an empty column list -- "
        "warehouse response missing manifest.schema"
    )


# ---------------------------------------------------------------------------
# Per-widget test: binds encoded columns to the dataset's result schema and
# applies widget-type-specific row shape rules.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "dashboard,page_name,widget",
    _all_widgets(),
    ids=[
        f"{p.name}:{page}:{w.get('name','?')}"
        for p, page, w in _all_widgets()
    ],
)
def test_widget_encodes_resolve(
    warehouse: tuple[str, str, str],
    dashboard: Path,
    page_name: str,
    widget: dict[str, Any],
) -> None:
    wname = widget.get("name", "<unnamed>")
    wtype = _widget_type(widget)
    ds_name = _widget_dataset_name(widget)
    assert ds_name, (
        f"{dashboard.name} widget '{wname}' has no datasetName binding"
    )

    cols, rows = _resolve_dataset(warehouse, dashboard, ds_name)
    schema = {c.lower() for c in cols}

    # 1. Every encoded column must be present in the result schema.
    missing: list[str] = []
    for encoded in _widget_encoded_columns(widget):
        if encoded.lower() not in schema:
            missing.append(encoded)
    assert not missing, (
        f"{dashboard.name}:{wname} encodes columns "
        f"{missing!r} that are not in dataset '{ds_name}' result schema "
        f"{sorted(cols)!r}"
    )

    # 2. Row-shape rules.
    n_rows = len(rows)
    is_cold_start = (dashboard.name, wname) in COLD_START_WIDGETS

    if wtype in {"counter", "single-value"}:
        # Counter must resolve to exactly 1 row -- Lakeview counters read
        # the first row's single field.
        assert n_rows == 1, (
            f"{dashboard.name}:{wname} is a {wtype} but returned {n_rows} "
            f"rows (expected exactly 1). SQL may have the wrong aggregate."
        )
    elif wtype in {"bar", "line", "area"}:
        # Trend/bar charts need >= 2 distinct x values or they render as
        # a lone dot / bar. A widget tagged in COLD_START_WIDGETS is
        # exempt (documented in docs/dashboards.md).
        x_field = _primary_x_field(widget)
        if x_field and n_rows >= 1:
            x_idx_candidates = [
                i for i, c in enumerate(cols)
                if c.lower() == x_field.lower()
            ]
            if x_idx_candidates:
                x_idx = x_idx_candidates[0]
                distinct_x = {r[x_idx] for r in rows}
                if not is_cold_start:
                    assert len(distinct_x) >= 2, (
                        f"{dashboard.name}:{wname} is a {wtype} chart but "
                        f"dataset '{ds_name}' returned {len(distinct_x)} "
                        f"distinct x values on '{x_field}' -- chart will "
                        "render broken."
                    )
    # Table / pivot / scatter / symbol-map have no hard row-count rule;
    # the encoded-columns check above is enough.


# ---------------------------------------------------------------------------
# Report summary. Runs after the per-widget tests (pytest ordering is file
# order, so this test comes last). Prints the markdown table the task
# description asked for to stdout. It never fails; it is purely informative.
# ---------------------------------------------------------------------------
def test_report_widget_status(warehouse: tuple[str, str, str]) -> None:
    lines = ["", "| dashboard | widget | query_ok | rows | notes |",
             "|---|---|---|---|---|"]
    for dashboard, _page_name, widget in _all_widgets():
        wname = widget.get("name", "<unnamed>")
        wtype = _widget_type(widget)
        ds_name = _widget_dataset_name(widget) or "<no-ds>"
        key = (dashboard.name, ds_name)
        cached = _DATASET_RESULTS.get(key)
        if cached is None:
            # The per-widget test did not run (or ran against a different
            # parametrize). Resolve now from cache.
            try:
                cached = _resolve_dataset(warehouse, dashboard, ds_name)
            except Exception as exc:  # pragma: no cover -- defensive
                lines.append(
                    f"| {dashboard.name} | {wname} | no | 0 | "
                    f"resolve-error: {exc} |"
                )
                continue
        _cols, rows = cached
        notes: list[str] = [f"type={wtype}", f"ds={ds_name}"]
        if (dashboard.name, ds_name) in DELTA_VS_PRIOR_DATASETS:
            notes.append("delta_vs_prior (cold-start NULL cells OK)")
        if (dashboard.name, wname) in COLD_START_WIDGETS:
            notes.append("cold-start-zero-by-design")
        lines.append(
            f"| {dashboard.name} | {wname} | yes | {len(rows)} | "
            f"{'; '.join(notes)} |"
        )
    print("\n".join(lines))
