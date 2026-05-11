from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

SCAN_ROOTS = (
    REPO / "docs",
    REPO / "pipelines",
    REPO / "scripts",
    REPO / "tools",
    REPO / "frontend" / "tests" / "e2e",
)

SCAN_FILES = (
    REPO / "CLAUDE.md",
)

FORBIDDEN_FIXED_FOOTPRINT_MARKERS = (
    "6-state footprint",
    "6-state share footprint",
    "6-state product",
    "6-state map coverage",
    "6-state coverage",
    "6 states",
    "6 metros",
    "six-state",
    "six metros",
    "SIX_STATES",
    "six-state footprint",
    "Six-state share footprint",
    "all six states",
    "All\\+6\\+states",
    'STATES = ["IL", "CA", "FL", "TX", "WA", "CO"]',
    "STATES = ['IL', 'CA', 'FL', 'TX', 'WA', 'CO']",
    "IL/CA/FL/TX/WA/CO",
    "state=FL",
    "Broward County",
    "aria-label=\"Florida\"",
    "aria-label='Florida'",
    "toContainText('IL'",
    'toContainText("IL"',
    "toContainText('606'",
    'toContainText("606"',
)

LAKEFLOW_PIPELINE = REPO / "pipelines" / "lakeflow" / "mip_feature_pipeline.py"


def _iter_release_gate_files() -> list[Path]:
    paths: list[Path] = []
    for root in SCAN_ROOTS:
        for path in root.rglob("*"):
            if path.suffix in {".sh", ".py", ".ts", ".tsx", ".md", ".yml", ".yaml"}:
                paths.append(path)
    paths.extend(path for path in SCAN_FILES if path.exists())
    return sorted(paths)


def test_release_gates_do_not_pin_cotality_fixed_demo_footprint() -> None:
    offenders: list[str] = []
    for path in _iter_release_gate_files():
        text = path.read_text(encoding="utf-8")
        for marker in FORBIDDEN_FIXED_FOOTPRINT_MARKERS:
            if marker.lower() in text.lower():
                offenders.append(f"{path.relative_to(REPO)} contains {marker!r}")

    assert not offenders


def test_lakeflow_pipeline_retains_dynamic_state_coverage() -> None:
    text = LAKEFLOW_PIPELINE.read_text(encoding="utf-8")
    fixed_state_patterns = (
        r"\.isin\(\s*[\"']IL[\"']\s*,\s*[\"']CA[\"']\s*,\s*[\"']FL[\"']\s*,\s*[\"']TX[\"']\s*,\s*[\"']WA[\"']\s*,\s*[\"']CO[\"']",
        r"\(\s*[\"']IL[\"']\s*,\s*[\"']CA[\"']\s*,\s*[\"']FL[\"']\s*,\s*[\"']TX[\"']\s*,\s*[\"']WA[\"']\s*,\s*[\"']CO[\"']\s*\)",
        r"IN\s*\(\s*[\"']IL[\"']\s*,\s*[\"']CA[\"']\s*,\s*[\"']FL[\"']\s*,\s*[\"']TX[\"']\s*,\s*[\"']WA[\"']\s*,\s*[\"']CO[\"']\s*\)",
    )
    offenders = [
        pattern
        for pattern in fixed_state_patterns
        if re.search(pattern, text, flags=re.IGNORECASE)
    ]

    assert not offenders
    assert "_valid_state(F.col(\"situs_state\"))" in text
    assert "_valid_state(F.col(\"deed_situs_state_static\"))" in text
    assert "_valid_state(F.col(\"pm.situs_state\"))" in text
    assert "situs_state RLIKE '^[A-Z]{2}$'" in text
