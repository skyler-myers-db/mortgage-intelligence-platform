"""Keyed filter fingerprints for the stored audit copies (W5c, audit 12.3).

The stored ``filter_fingerprint`` of VIEW_AUDIT_LEDGER, AUDIT_EXPORT and
VIEW_LEADS is an HMAC-SHA256 of the plain filter digest under a server key, so
a dictionary of candidate filters hashed WITHOUT the key never reproduces a
stored value. Pinned here: the keyed value differs from the plain digest, is
stable under one key and different under another, passes the audit value
policy as 64 lowercase hex, and fails closed (None, never a plain value)
outside local/test with no key configured.
"""

from __future__ import annotations

import hashlib
import json
import re

import pytest
from pydantic import SecretStr

from backend.config.settings import settings
from backend.schemas.audit import AuditExportReceiptRequest
from backend.services.audit_export_receipt import (
    AuditExportFingerprintUnavailable,
    write_audit_export_receipt,
)
from backend.services.audit_fingerprint import (
    AUDIT_LEDGER_FINGERPRINT_DOMAIN,
    LEAD_VIEW_FINGERPRINT_DOMAIN,
    keyed_filter_fingerprint,
    keyed_filter_fingerprint_candidates,
)
from backend.services.audit_metadata_public_values import _assert_public_safe_values
from backend.services.audit_pagination import audit_filter_fingerprint
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore

KEY_A = SecretStr("fingerprint-key-a-0123456789abcdef")
KEY_B = SecretStr("fingerprint-key-b-0123456789abcdef")
FILTERS = {"actor": "lo.one@summit-mortgage.example", "event_type": "APPROVE"}


def _plain(filters: dict[str, object]) -> str:
    return audit_filter_fingerprint(dict(filters))


@pytest.fixture
def keyed_a(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mip_genie_action_secret_current", KEY_A)
    monkeypatch.setattr(settings, "mip_genie_action_secret_previous", None)


def test_the_keyed_value_is_not_the_plain_digest(keyed_a: None) -> None:
    plain = _plain(FILTERS)
    keyed = keyed_filter_fingerprint(plain, domain=AUDIT_LEDGER_FINGERPRINT_DOMAIN)

    assert keyed is not None
    assert re.fullmatch(r"[0-9a-f]{64}", keyed)
    assert keyed != plain
    assert keyed != hashlib.sha256(plain.encode("ascii")).hexdigest()


def test_the_keyed_value_is_stable_under_one_key_and_differs_under_another(
    keyed_a: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    plain = _plain(FILTERS)
    first = keyed_filter_fingerprint(plain, domain=AUDIT_LEDGER_FINGERPRINT_DOMAIN)
    again = keyed_filter_fingerprint(plain, domain=AUDIT_LEDGER_FINGERPRINT_DOMAIN)
    monkeypatch.setattr(settings, "mip_genie_action_secret_current", KEY_B)
    other_key = keyed_filter_fingerprint(plain, domain=AUDIT_LEDGER_FINGERPRINT_DOMAIN)

    assert first == again
    assert other_key is not None and other_key != first


def test_the_domains_are_separated(keyed_a: None) -> None:
    plain = _plain(FILTERS)

    assert keyed_filter_fingerprint(plain, domain=AUDIT_LEDGER_FINGERPRINT_DOMAIN) != keyed_filter_fingerprint(
        plain, domain=LEAD_VIEW_FINGERPRINT_DOMAIN
    )


def test_the_keyed_value_passes_the_audit_value_policy(keyed_a: None) -> None:
    keyed = keyed_filter_fingerprint(_plain(FILTERS), domain=AUDIT_LEDGER_FINGERPRINT_DOMAIN)

    _assert_public_safe_values({"filter_fingerprint": keyed})


def test_a_candidate_email_dictionary_hashed_unkeyed_never_reproduces_a_stored_value(keyed_a: None) -> None:
    candidates = [f"lo.{name}@summit-mortgage.example" for name in ("one", "two", "three", "four")]
    stored = keyed_filter_fingerprint(_plain(FILTERS), domain=AUDIT_LEDGER_FINGERPRINT_DOMAIN)
    dictionary = {
        digest
        for email in candidates
        for digest in (
            _plain({**FILTERS, "actor": email}),
            hashlib.sha256(json.dumps({**FILTERS, "actor": email}, sort_keys=True).encode()).hexdigest(),
        )
    }

    assert _plain(FILTERS) in dictionary  # the attack recovers the PLAIN digest...
    assert stored not in dictionary  # ...but never the stored, keyed one


def test_a_rotated_secret_still_verifies_through_the_candidates(
    keyed_a: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    plain = _plain(FILTERS)
    minted = keyed_filter_fingerprint(plain, domain=LEAD_VIEW_FINGERPRINT_DOMAIN)
    monkeypatch.setattr(settings, "mip_genie_action_secret_current", KEY_B)
    monkeypatch.setattr(settings, "mip_genie_action_secret_previous", KEY_A)

    candidates = keyed_filter_fingerprint_candidates(plain, domain=LEAD_VIEW_FINGERPRINT_DOMAIN)

    assert minted in candidates
    assert candidates[0] == keyed_filter_fingerprint(plain, domain=LEAD_VIEW_FINGERPRINT_DOMAIN)


def test_no_key_outside_local_answers_none_never_a_plain_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mip_genie_action_secret_current", None)
    monkeypatch.setattr(settings, "mip_genie_action_secret", None)
    monkeypatch.setattr(settings, "mip_genie_action_secret_previous", None)
    monkeypatch.setattr(settings, "app_env", "production")

    assert keyed_filter_fingerprint(_plain(FILTERS), domain=AUDIT_LEDGER_FINGERPRINT_DOMAIN) is None
    assert keyed_filter_fingerprint_candidates(_plain(FILTERS), domain=AUDIT_LEDGER_FINGERPRINT_DOMAIN) == []


def test_local_env_without_a_secret_uses_a_process_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mip_genie_action_secret_current", None)
    monkeypatch.setattr(settings, "mip_genie_action_secret", None)
    monkeypatch.setattr(settings, "mip_genie_action_secret_previous", None)
    monkeypatch.setattr(settings, "app_env", "test")
    plain = _plain(FILTERS)

    keyed = keyed_filter_fingerprint(plain, domain=AUDIT_LEDGER_FINGERPRINT_DOMAIN)

    assert keyed is not None and keyed != plain


def test_a_non_digest_input_is_refused(keyed_a: None) -> None:
    with pytest.raises(ValueError):
        keyed_filter_fingerprint("not-a-digest", domain=AUDIT_LEDGER_FINGERPRINT_DOMAIN)


def test_the_audit_export_receipt_writes_no_row_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mip_genie_action_secret_current", None)
    monkeypatch.setattr(settings, "mip_genie_action_secret", None)
    monkeypatch.setattr(settings, "mip_genie_action_secret_previous", None)
    monkeypatch.setattr(settings, "app_env", "production")
    store = InMemoryAuditStore()
    event_ids = ["0f8fad5b-d9cb-469f-a165-70867728950e"]
    declaration = AuditExportReceiptRequest(
        row_count=1,
        csv_sha256="a" * 64,
        event_ids=event_ids,
        event_ids_sha256=hashlib.sha256(json.dumps(event_ids, separators=(",", ":")).encode()).hexdigest(),
        filters={"event_type": "APPROVE"},
    )

    with pytest.raises(AuditExportFingerprintUnavailable):
        write_audit_export_receipt(store, actor="ops@summit-mortgage.example", payload=declaration)

    assert store.list(limit=10) == []
