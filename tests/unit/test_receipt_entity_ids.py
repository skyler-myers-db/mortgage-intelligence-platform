"""Receipt entity ids can never read as PII (2026-10-28).

``PRINT_EXPORT``, ``AUDIT_EXPORT`` and ``LEAD_EXPORT`` name their row by the
filter fingerprint. Until 2026-10-28 the id embedded the raw 16-hex slice. Ten
hex characters in sixteen are digits, so about 1.4% of slices hold a ten-digit
run; the audit-id PII guard (``validate_public_audit_identifier_or_none``)
refused it as a phone number and the receipt answered 422. The keyed
fingerprints failed that share of filter sets under any one secret (and that
share of pytest runs under the random local key); the unkeyed ``LEAD_EXPORT``
one failed the same filter sets in every deployment.

Pinned here: the letter image's mapping (stored ``geniehash-`` ids depend on
it), that no digest has a refused image, and a sweep of each receipt service
over many keys (for ``LEAD_EXPORT``, many filter sets) in which every write is
recorded. Each sweep carries a control: the legacy raw slice of the same
fingerprints is refused, so a sweep cannot pass vacuously.
"""

from __future__ import annotations

import hashlib
import re

import pytest
from pydantic import SecretStr

from backend.config.settings import settings
from backend.schemas.audit import AuditExportReceiptRequest
from backend.schemas.common import validate_public_audit_identifier_or_none
from backend.schemas.lead import SEGMENT_CODE_VALUES
from backend.schemas.lead_export import LeadExportReceiptRequest
from backend.schemas.print_receipt import PrintReceiptRequest
from backend.services.audit_entity_ids import audit_safe_letters
from backend.services.audit_export_receipt import event_ids_digest, write_audit_export_receipt
from backend.services.lead_export_receipt import borrower_ids_digest, write_lead_export_receipt
from backend.services.print_receipt import write_print_receipt
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore

ACTOR = "ops@summit-mortgage.example"
KEY_SWEEP = 2000
LEDGER_FILTERS = {"event_type": "APPROVE", "since": "2026-09-01T00:00:00Z"}
EVENT_IDS = ["0f8fad5b-d9cb-469f-a165-70867728950e", "7c9e6679-7425-40de-944b-e07fc1f90ae7"]
BORROWER_IDS = ["B-AAAAAAAAAAAA1", "B-AAAAAAAAAAAA2"]
CSV_SHA256 = hashlib.sha256(b"csv").hexdigest()
STATES = (
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO "
    "MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY"
).split()
DIGESTS = [
    ("a2125550123b" * 6)[:64],  # a ten-digit run between letters: a phone number
    "f15551234567" + "e" * 52,  # an eleven-digit +1 run
    "0" * 64,  # all digits (a run this long is not phone-shaped, but has no image digit)
    *(hashlib.sha256(f"digest-{n}".encode()).hexdigest() for n in range(5000)),
]


def _refused(entity_id: str) -> bool:
    try:
        validate_public_audit_identifier_or_none(entity_id)
    except ValueError:
        return True
    return False


@pytest.fixture
def only_current_secret(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """The deployed-app key path: a configured current secret, nothing else."""

    monkeypatch.setattr(settings, "mip_genie_action_secret", None)
    monkeypatch.setattr(settings, "mip_genie_action_secret_previous", None)
    return monkeypatch


def _assert_named_by_image(store: InMemoryAuditStore, prefix: str, count: int) -> int:
    """Every row is named by its fingerprint's image; answers how many legacy ids were refused."""

    rows = store.list(limit=count + 1)
    assert len(rows) == count
    legacy_refused = 0
    for row in rows:
        fingerprint = row.payload_json["filter_fingerprint"]
        assert row.entity_id == f"{prefix}-{audit_safe_letters(fingerprint)}"
        legacy_refused += _refused(f"{prefix}-{fingerprint[:16]}")
    return legacy_refused


def test_the_letter_image_mapping_never_changes() -> None:
    # A refusal report re-derives a stored ``geniehash-`` id through it.
    assert audit_safe_letters("0123456789abcdef" * 4) == "abcdefghijklmnopabcdefgh"
    assert audit_safe_letters(hashlib.sha256(b"").hexdigest()) == "odlameecjipmbmbejkplpemi"


def test_no_digest_has_a_refused_letter_image() -> None:
    for digest in DIGESTS:
        letters = audit_safe_letters(digest)
        assert re.fullmatch(r"[a-p]{24}", letters), digest
        for prefix in ("print", "export"):
            entity_id = f"{prefix}-{letters}"
            assert validate_public_audit_identifier_or_none(entity_id) == entity_id

    # Control: the same digests' raw slices are refused.
    assert sum(_refused(f"export-{digest[:16]}") for digest in DIGESTS) >= 50


def test_every_ledger_print_is_recorded_under_any_key(only_current_secret: pytest.MonkeyPatch) -> None:
    store = InMemoryAuditStore()
    request = PrintReceiptRequest(surface="audit_ledger", filters=dict(LEDGER_FILTERS), row_count=25)

    for step in range(KEY_SWEEP):
        only_current_secret.setattr(settings, "mip_genie_action_secret_current", SecretStr(f"sweep-{step}"))
        write_print_receipt(store, actor=ACTOR, payload=request)

    assert _assert_named_by_image(store, "print", KEY_SWEEP) >= 10


def test_every_audit_export_is_recorded_under_any_key(only_current_secret: pytest.MonkeyPatch) -> None:
    store = InMemoryAuditStore()
    request = AuditExportReceiptRequest(
        row_count=len(EVENT_IDS),
        csv_sha256=CSV_SHA256,
        event_ids=list(EVENT_IDS),
        event_ids_sha256=event_ids_digest(EVENT_IDS),
        filters=dict(LEDGER_FILTERS),
    )

    for step in range(KEY_SWEEP):
        only_current_secret.setattr(settings, "mip_genie_action_secret_current", SecretStr(f"sweep-{step}"))
        write_audit_export_receipt(store, actor=ACTOR, payload=request)

    assert _assert_named_by_image(store, "export", KEY_SWEEP) >= 10


def test_every_lead_export_filter_set_is_recorded() -> None:
    # Unkeyed: no secret varies, so the sweep is over filter sets a Lead Queue
    # reader exports (one state, alone or with one segment).
    filter_sets = [{"states": state} for state in STATES] + [
        {"states": state, "segment_codes": code} for state in STATES for code in SEGMENT_CODE_VALUES
    ]
    store = InMemoryAuditStore()

    for filters in filter_sets:
        request = LeadExportReceiptRequest(
            scope="selected",
            row_count=len(BORROWER_IDS),
            csv_sha256=CSV_SHA256,
            borrower_ids=list(BORROWER_IDS),
            borrower_ids_sha256=borrower_ids_digest(BORROWER_IDS),
            filters=filters,
        )
        write_lead_export_receipt(store, actor=ACTOR, payload=request)

    assert _assert_named_by_image(store, "export", len(filter_sets)) >= 5
