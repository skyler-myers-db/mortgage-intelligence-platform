"""Every server-owned audit event type has a human label in the explorer.

Audit flow-04 phase 1 / tables-10 (2026-09-21): the audit explorer reads each
ledger row as a human label (``AUDIT_EVENT_TYPE_LABELS`` in
``frontend/src/components/admin/AdminAuditExplorer.labels.ts``) and feeds its
event-type filter from the same map. A code the map lacks still renders (a
sentence-cased fallback) but cannot be picked in the filter, so a new
server-owned event type must arrive with its label. This pins that parity,
plus the decision codes the Decision receipt maps.
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.services.audit_event_types import SERVER_OWNED_AUDIT_EVENT_TYPES
from backend.services.audit_store_receipt import _DECISION_BY_EVENT_TYPE

ROOT = Path(__file__).resolve().parents[2]
LABELS = ROOT / "frontend" / "src" / "components" / "admin" / "AdminAuditExplorer.labels.ts"

_MAP_RE = re.compile(
    r"export const AUDIT_EVENT_TYPE_LABELS[^=]*=\s*\{(?P<body>.*?)\n\};",
    re.DOTALL,
)
_ENTRY_RE = re.compile(r"^\s+(?P<code>[A-Z][A-Z0-9_]*):\s*'(?P<label>[^']+)',\s*$", re.MULTILINE)


def _labels() -> dict[str, str]:
    match = _MAP_RE.search(LABELS.read_text(encoding="utf-8"))
    assert match is not None, "AUDIT_EVENT_TYPE_LABELS not found"
    entries = {m["code"]: m["label"] for m in _ENTRY_RE.finditer(match["body"])}
    assert entries, "AUDIT_EVENT_TYPE_LABELS parsed empty"
    return entries


def test_every_server_owned_event_type_has_an_explorer_label() -> None:
    missing = sorted(SERVER_OWNED_AUDIT_EVENT_TYPES - set(_labels()))
    assert missing == []


def test_every_decision_receipt_code_has_an_explorer_label() -> None:
    missing = sorted(set(_DECISION_BY_EVENT_TYPE) - set(_labels()))
    assert missing == []


def test_explorer_labels_are_unique_per_code() -> None:
    labels = list(_labels().values())
    assert len(labels) == len(set(labels))
