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
from backend.services.borrower_decision_history import DECISION_HISTORY_EVENT_TYPES

ROOT = Path(__file__).resolve().parents[2]
LABELS = ROOT / "frontend" / "src" / "components" / "admin" / "AdminAuditExplorer.labels.ts"
PRESENTATION = ROOT / "frontend" / "src" / "lib" / "auditEventPresentation.ts"

_MAP_RE = re.compile(
    r"export const AUDIT_EVENT_TYPE_LABELS[^=]*=\s*\{(?P<body>.*?)\n\};",
    re.DOTALL,
)
_ACTIVITY_MAP_RE = re.compile(
    r"export const ACTIVITY_LABELS[^=]*=\s*\{(?P<body>.*?)\n\};",
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


def test_no_view_offer_event_exists() -> None:
    """Ruling D-audit-reads-b (audit delivery-08): the Offer keeps its own
    audited reads; RECOMMEND_OFFER is the approval-surface open record, so no
    VIEW_OFFER event type is server-owned or labelled in the explorer."""

    assert "RECOMMEND_OFFER" in SERVER_OWNED_AUDIT_EVENT_TYPES, "non-vacuity: the open record is server-owned"
    assert "VIEW_OFFER" not in SERVER_OWNED_AUDIT_EVENT_TYPES
    assert "VIEW_OFFER" not in _labels()


def _activity_labels() -> dict[str, str]:
    match = _ACTIVITY_MAP_RE.search(PRESENTATION.read_text(encoding="utf-8"))
    assert match is not None, "ACTIVITY_LABELS not found"
    entries = {m["code"]: m["label"] for m in _ENTRY_RE.finditer(match["body"])}
    assert entries, "ACTIVITY_LABELS parsed empty"
    return entries


def test_suppress_contact_is_labelled_in_the_explorer() -> None:
    assert _labels()["SUPPRESS_CONTACT"] == "Contact blocked"


def test_every_decision_history_type_has_a_shared_presentation_label() -> None:
    """D-audit-reads-c2: the Borrower 360 / Offer decision history and the
    Console read the same label map (frontend/src/lib/auditEventPresentation.ts)."""

    missing = sorted(DECISION_HISTORY_EVENT_TYPES - set(_activity_labels()))
    assert missing == []


def test_the_ledger_read_events_have_shared_presentation_labels() -> None:
    # C5: only label presence; VIEW_REFUSAL_REPORT_TEXT becomes server-owned in
    # the refusal lane, and each lane must pass alone.
    labels = _activity_labels()
    assert labels["VIEW_AUDIT_LEDGER"] == "Audit ledger read"
    assert labels["VIEW_REFUSAL_REPORT_TEXT"] == "Refusal question read"
