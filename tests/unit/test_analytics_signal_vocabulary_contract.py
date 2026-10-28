"""The Analytics Signal menu, the API's signal allowlist and gold evidence agree.

The Signals tab's Signal menu (``SIGNAL_FILTERS`` in
``frontend/src/routes/analytics.lib.ts``) sends each choice as ``signal_types``
to ``GET /api/v1/analytics/signals``. The router accepts only
``_ALLOWED_SIGNAL_TYPES`` (422 otherwise) and the repository binds the rest
into ``mip.gold.evidence_events.signal_type IN (...)``. The menu gained
Listing, HELOC propensity and Refi propensity (05b9a556, 2026-06-14) after the
allowlist was written (876cb8e5, 2026-05-20), so picking any of them errored
the tab with a 422. This pins:

(a) every menu value is accepted through the router and reaches the
    repository unchanged;
(b) the allowlist is exactly the menu, so the explainability-only types
    (product_type, origination_channel) stay out until the menu offers them;
(c) every allowed value is a signal_type that gold.evidence_events emits and
    names in its controlled vocabulary.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api.analytics import _ALLOWED_SIGNAL_TYPES
from backend.main import app
from backend.schemas.analytics import AnalyticsFilters, SignalAnalyticsResponse
from backend.services.repositories import get_analytics_repository

ROOT = Path(__file__).resolve().parents[2]
ANALYTICS_LIB = ROOT / "frontend" / "src" / "routes" / "analytics.lib.ts"
EVIDENCE_SQL = ROOT / "sql" / "transformations" / "gold_evidence_events.sql"

_SIGNAL_FILTERS_RE = re.compile(
    r"^export const SIGNAL_FILTERS = \[\n(?P<body>.*?)\n\] as const\b",
    re.DOTALL | re.MULTILINE,
)
_MENU_ENTRY_RE = re.compile(r"^\s*\['(?P<label>[^'\n]+)', (?:'(?P<value>[^'\n]*)'|null)\],$")
_EMITTED_RE = re.compile(r"'([^'\n]+)'\s+AS\s+signal_type\b", re.IGNORECASE)
_VOCAB_COMMENT_RE = re.compile(
    r"COMMENT ON COLUMN mip\.gold\.evidence_events\.signal_type IS 'Controlled vocab: (?P<vocab>[^.']+)\.",
)


def _menu() -> list[tuple[str, str | None]]:
    """SIGNAL_FILTERS as (label, value) pairs. Every line of the literal must
    parse, so a format change fails here instead of silently dropping an
    entry."""
    match = _SIGNAL_FILTERS_RE.search(ANALYTICS_LIB.read_text(encoding="utf-8"))
    assert match is not None, "SIGNAL_FILTERS literal not found in analytics.lib.ts"
    entries: list[tuple[str, str | None]] = []
    for line in match["body"].splitlines():
        if not line.strip():
            continue
        entry = _MENU_ENTRY_RE.match(line)
        assert entry is not None, f"unparsed SIGNAL_FILTERS line: {line!r}"
        entries.append((entry["label"], entry["value"]))
    return entries


def _menu_signal_types() -> list[str]:
    entries = _menu()
    assert [label for label, value in entries if value is None] == ["All signals"]
    values = [value for _, value in entries if value is not None]
    # Non-vacuity: the parse reached the end of the literal.
    assert {"equity", "refi_propensity"} <= set(values)
    assert len(values) == len(set(values)), "SIGNAL_FILTERS repeats a value"
    return values


def _emitted_signal_types() -> set[str]:
    text = EVIDENCE_SQL.read_text(encoding="utf-8")
    body = re.sub(r"--.*$", "", text, flags=re.MULTILINE)
    emitted = set(_EMITTED_RE.findall(body))
    assert {"equity", "listing", "product_type"} <= emitted, "emitted signal_type literals parsed short"
    return emitted


def _documented_signal_types() -> set[str]:
    match = _VOCAB_COMMENT_RE.search(EVIDENCE_SQL.read_text(encoding="utf-8"))
    assert match is not None, "evidence_events.signal_type controlled-vocab COMMENT not found"
    vocab = {part.strip() for part in match["vocab"].split("/")}
    assert {"equity", "listing", "product_type"} <= vocab, "controlled vocab parsed short"
    return vocab


@pytest.fixture
def captured_filters() -> Iterator[list[AnalyticsFilters | None]]:
    captured: list[AnalyticsFilters | None] = []

    class _CaptureAnalyticsRepo:
        def signals(self, filters: AnalyticsFilters | None = None) -> SignalAnalyticsResponse:
            captured.append(filters)
            return SignalAnalyticsResponse(evidence_daily=[], evidence_by_signal=[], evidence_examples=[])

    prior = app.dependency_overrides.get(get_analytics_repository)
    app.dependency_overrides[get_analytics_repository] = lambda: _CaptureAnalyticsRepo()
    try:
        yield captured
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_analytics_repository, None)
        else:
            app.dependency_overrides[get_analytics_repository] = prior


def test_every_signal_menu_choice_is_accepted_by_the_signals_api(
    captured_filters: list[AnalyticsFilters | None],
) -> None:
    client = TestClient(app)
    menu = _menu_signal_types()
    for signal_type in menu:
        response = client.get(f"/api/v1/analytics/signals?signal_types={signal_type}")
        assert response.status_code == 200, f"menu signal {signal_type!r} -> {response.status_code}"
        forwarded = captured_filters[-1]
        assert forwarded is not None and forwarded.signal_types == [signal_type]

    # The whole menu at once, in menu order.
    response = client.get(f"/api/v1/analytics/signals?signal_types={','.join(menu)}")
    assert response.status_code == 200
    forwarded = captured_filters[-1]
    assert forwarded is not None and forwarded.signal_types == menu


def test_signal_allowlist_is_exactly_the_signal_menu() -> None:
    menu = set(_menu_signal_types())
    assert sorted(menu - _ALLOWED_SIGNAL_TYPES) == [], "the menu offers a signal the API rejects"
    assert sorted(_ALLOWED_SIGNAL_TYPES - menu) == [], (
        "the API accepts a signal the menu does not offer; add it to SIGNAL_FILTERS or drop it"
    )


def test_signal_allowlist_is_gold_evidence_vocabulary() -> None:
    assert sorted(_ALLOWED_SIGNAL_TYPES - _emitted_signal_types()) == [], (
        "an allowed signal is never emitted by gold.evidence_events"
    )
    assert sorted(_ALLOWED_SIGNAL_TYPES - _documented_signal_types()) == [], (
        "an allowed signal is missing from evidence_events.signal_type's controlled vocab"
    )
