"""Every asset the app can open in an evidence drawer has a reviewed freshness answer.

Audit 2026-09-21 critic-03 / D-audit-reads-c1. Each registry descriptor sets
EXACTLY ONE of ``freshness_basis`` (a ``source_name`` literal of
``sql/transformations/gold_source_readiness.sql``: the asset's own row or its
primary input's) and ``freshness_not_tracked``. Every asset key the frontend
can name (an ``assetKey: '…'`` / ``assetKey === '…'`` literal under
frontend/src, the slim registry and approvalFunnelDrawerSource.ts included,
and every ASSET_KEYS_BY_SOURCE value) resolves through the backend registry,
so a drawer never asks freshness for a key the route would 404.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.services.asset_metadata import resolve_asset_descriptor
from backend.services.asset_registry import ASSET_DESCRIPTORS, AssetDescriptor

ROOT = Path(__file__).resolve().parents[2]
READINESS_SQL = ROOT / "sql" / "transformations" / "gold_source_readiness.sql"
FRONTEND_SRC = ROOT / "frontend" / "src"
DRAWER_SOURCES_TS = FRONTEND_SRC / "lib" / "drawerSources.ts"

_SOURCE_NAME_RE = re.compile(r"'([^']+)'\s+AS\s+source_name", re.IGNORECASE)
_ASSET_KEY_LITERAL_RE = re.compile(r"assetKey(?::|\s*===)\s*'([^']+)'")
_ASSET_KEYS_BY_SOURCE_RE = re.compile(
    r"export const ASSET_KEYS_BY_SOURCE[^=]*=\s*\{(?P<body>.*?)\n\};", re.DOTALL
)
_MAP_VALUE_RE = re.compile(r":\s*'([^']+)',")


def _readiness_source_names() -> set[str]:
    return set(_SOURCE_NAME_RE.findall(READINESS_SQL.read_text(encoding="utf-8")))


def _frontend_asset_keys() -> set[str]:
    keys: set[str] = set()
    for path in FRONTEND_SRC.rglob("*.ts*"):
        if ".test." in path.name or path.name.endswith(".d.ts"):
            continue
        keys.update(_ASSET_KEY_LITERAL_RE.findall(path.read_text(encoding="utf-8")))
    match = _ASSET_KEYS_BY_SOURCE_RE.search(DRAWER_SOURCES_TS.read_text(encoding="utf-8"))
    assert match, "drawerSources.ts declares ASSET_KEYS_BY_SOURCE"
    keys.update(_MAP_VALUE_RE.findall(match.group("body")))
    return keys


def test_the_registry_holds_51_descriptors_35_tracked_and_16_not() -> None:
    tracked = [d for d in ASSET_DESCRIPTORS if d.freshness_basis is not None]
    untracked = [d for d in ASSET_DESCRIPTORS if d.freshness_not_tracked]
    assert (len(ASSET_DESCRIPTORS), len(tracked), len(untracked)) == (51, 35, 16)
    assert sum(1 for d in untracked if d.key.startswith("fn_")) == 10


@pytest.mark.parametrize("descriptor", ASSET_DESCRIPTORS, ids=lambda d: d.key)
def test_every_descriptor_sets_exactly_one_freshness_field(descriptor: AssetDescriptor) -> None:
    assert (descriptor.freshness_basis is not None) != descriptor.freshness_not_tracked


def test_every_basis_is_a_reviewed_source_readiness_row() -> None:
    names = _readiness_source_names()
    assert "UC Gold Borrower 360" in names  # the parser found the literals
    unknown = sorted(
        {d.freshness_basis for d in ASSET_DESCRIPTORS if d.freshness_basis is not None} - names
    )
    assert unknown == []


def test_the_two_decision_record_corrections_hold() -> None:
    # evidence_events is built on the silver.lien_current spine
    # (test_gold_ddl_contract::test_evidence_events_uses_silver_lien_spine_not_borrower_360);
    # segment_combination_rollup reads gold.borrower_360 (absent from the record).
    by_key = {d.key: d for d in ASSET_DESCRIPTORS}
    assert by_key["evidence_events"].freshness_basis == "Voluntary Lien"
    assert by_key["segment_combination_rollup"].freshness_basis == "UC Gold Borrower 360"


def test_every_frontend_asset_key_resolves_through_the_registry() -> None:
    keys = _frontend_asset_keys()
    assert {"borrower_360", "portfolio_headline_metric_view", "segment_combination_rollup"} <= keys
    unresolved = []
    for key in sorted(keys):
        try:
            resolve_asset_descriptor(key)
        except KeyError:
            unresolved.append(key)
    assert unresolved == []
