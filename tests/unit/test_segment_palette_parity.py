"""Segment colours agree everywhere the dark palette is written down (D-dataviz-geo-c2).

The dark ``--seg-*`` hexes in frontend/src/design-system/tokens.css are the
product's segment identity. The same hexes are written into:

- the gold registry (sql/transformations/gold_segment_population.sql meta
  VALUES), which becomes mip.gold.segment_population.color;
- tests/fixtures/mock_population.py (the six core segments);
- the two Lakeview dashboards' segment-identity colour mappings and the
  segment-name conditional backgrounds (bundle resources).

The code -> token map is derived from frontend/src/lib/segmentMetadata.ts,
never hard-coded here. A dashboard value resolves to a segment code when it is
a code, a label its widget's dataset assigns with ``WHEN '<code>' THEN
'<label>'``, or a gold registry name (in that order); anything else (funnel
stages, signal types, "None / Unsegmented") is not a segment and is skipped.

Stdlib only.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
TOKENS = REPO / "frontend" / "src" / "design-system" / "tokens.css"
METADATA = REPO / "frontend" / "src" / "lib" / "segmentMetadata.ts"
GOLD = REPO / "sql" / "transformations" / "gold_segment_population.sql"
MOCK = REPO / "tests" / "fixtures" / "mock_population.py"
DASHBOARDS = sorted((REPO / "dashboards").glob("*.lvdash.json"))

SEGMENT_FIELDS = {"segment", "segment_name", "segment_code", "name", "primary_segment"}
HEX = r"#[0-9A-Fa-f]{6}"


def code_to_token() -> dict[str, str]:
    text = METADATA.read_text(encoding="utf-8")
    pairs = re.findall(r"code:\s*'([a-z_]+)'[^}]*?color:\s*'var\(--seg-([a-z-]+)\)'", text)
    assert pairs, "no segment definitions parsed from segmentMetadata.ts"
    return dict(pairs)


def dark_segment_hexes() -> dict[str, str]:
    """``--seg-*`` in the first ``:root`` block of tokens.css (the dark default)."""
    text = re.sub(r"/\*.*?\*/", "", TOKENS.read_text(encoding="utf-8"), flags=re.DOTALL)
    start = text.index(":root {")
    block = text[start : text.index("}", start)]
    return {name: value.upper() for name, value in re.findall(rf"--seg-([a-z-]+):\s*({HEX})", block)}


def gold_registry() -> dict[str, tuple[str, str]]:
    """segment_code -> (name, hex) from the gold meta VALUES."""
    rows = re.findall(
        rf"\(\s*'([a-z_]+)'\s*,\s*'([^']+)'\s*,\s*'[^']*'\s*,\s*'({HEX})'\s*\)",
        GOLD.read_text(encoding="utf-8"),
    )
    return {code: (name, color.upper()) for code, name, color in rows}


def mock_colors() -> dict[str, str]:
    text = MOCK.read_text(encoding="utf-8")
    return {code: color.upper() for code, color in re.findall(rf'code="([a-z_]+)"[^\n]*?color="({HEX})"', text)}


def _dataset_labels(spec: dict[str, Any]) -> dict[str, dict[str, str]]:
    labels: dict[str, dict[str, str]] = {}
    for dataset in spec.get("datasets", []):
        query = "\n".join(dataset.get("queryLines", [])) or dataset.get("query", "")
        found = re.findall(r"WHEN\s+'([a-z_]+)'\s+THEN\s+'([^']+)'", query)
        labels[dataset["name"]] = {label: code for code, label in found}
    return labels


def _entries(node: Any, field: str | None) -> Iterator[tuple[str, str, str]]:
    """(fieldName, value, hex) for every colour mapping and conditional background."""
    if isinstance(node, dict):
        field = node.get("fieldName", field)
        scale = node.get("scale")
        if field and isinstance(scale, dict):
            for mapping in scale.get("mappings", []):
                yield field, mapping.get("value"), mapping.get("color")
        style = node.get("style")
        if field and isinstance(style, dict):
            for rule in style.get("rules", []):
                value = rule.get("condition", {}).get("operand", {}).get("value")
                if "backgroundColor" in rule:
                    yield field, value, rule["backgroundColor"]
        for child in node.values():
            yield from _entries(child, field)
    elif isinstance(node, list):
        for child in node:
            yield from _entries(child, field)


def dashboard_segment_entries(
    spec: dict[str, Any], label: str, names: dict[str, str]
) -> list[tuple[str, str, str, str]]:
    """(where, value, code, hex) for every segment-identity colour in one dashboard spec."""
    labels = _dataset_labels(spec)
    codes = set(names.values())
    out: list[tuple[str, str, str, str]] = []
    for page in spec.get("pages", []):
        for item in page.get("layout", []):
            widget = item.get("widget", {})
            local: dict[str, str] = {}
            for query in widget.get("queries", []):
                local.update(labels.get(query.get("query", {}).get("datasetName", ""), {}))
            for field, value, color in _entries(widget.get("spec", {}), None):
                if field not in SEGMENT_FIELDS or not isinstance(value, str):
                    continue
                code = value if value in codes else local.get(value) or names.get(value)
                if code:
                    out.append((f"{label} {widget.get('name', '?')} {field}", value, code, color.upper()))
    return out


def _expected() -> dict[str, str]:
    tokens = dark_segment_hexes()
    return {code: tokens[token] for code, token in code_to_token().items()}


def test_every_registered_code_has_a_dark_token() -> None:
    mapping = code_to_token()
    tokens = dark_segment_hexes()
    assert len(mapping) == 13, mapping
    assert sorted(set(mapping.values()) - set(tokens)) == []
    assert sorted(mapping) == sorted(gold_registry()), "segmentMetadata.ts and the gold registry list the same codes"


def test_gold_registry_hexes_match_the_dark_tokens() -> None:
    expected = _expected()
    mismatches = {
        code: (color, expected[code]) for code, (_name, color) in gold_registry().items() if color != expected[code]
    }
    assert mismatches == {}, "gold meta VALUES (hex, tokens.css) disagree"


def test_mock_population_colors_match_the_dark_tokens() -> None:
    expected = _expected()
    colors = mock_colors()
    assert len(colors) == 6, colors
    assert {code: (c, expected[code]) for code, c in colors.items() if c != expected[code]} == {}


def test_dashboard_segment_colors_match_the_dark_tokens() -> None:
    expected = _expected()
    names = {name: code for code, (name, _hex) in gold_registry().items()}
    entries = [
        e
        for path in DASHBOARDS
        for e in dashboard_segment_entries(json.loads(path.read_text(encoding="utf-8")), path.name, names)
    ]
    assert len(DASHBOARDS) == 2 and len(entries) >= 40, f"too few segment mappings found ({len(entries)})"
    mismatches = [
        f"{where}: {value!r} ({code}) is {color}, tokens.css says {expected[code]}"
        for where, value, code, color in entries
        if color != expected[code]
    ]
    assert mismatches == []


def test_the_resolver_skips_non_segment_fields_and_prefers_dataset_labels() -> None:
    spec = {
        "datasets": [{"name": "d", "queryLines": ["CASE c WHEN 'permit' THEN 'Permit Activity' END"]}],
        "pages": [
            {
                "layout": [
                    {
                        "widget": {
                            "name": "w",
                            "queries": [{"query": {"datasetName": "d"}}],
                            "spec": {
                                "encodings": {
                                    "color": {
                                        "fieldName": "segment",
                                        "scale": {"mappings": [{"value": "Permit Activity", "color": "#A78BFA"}]},
                                    },
                                    "x": {"fieldName": "signal_type", "scale": {"mappings": [{"value": "equity", "color": "#000000"}]}},
                                }
                            },
                        }
                    }
                ]
            }
        ],
    }
    entries = dashboard_segment_entries(spec, "probe", {"Permit Activity": "permit_activity", "x": "equity"})
    assert [(value, code) for _where, value, code, _hex in entries] == [("Permit Activity", "permit")]
