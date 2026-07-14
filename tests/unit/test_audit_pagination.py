import base64
import json
from datetime import UTC, datetime

import pytest

from backend.services.audit_pagination import (
    audit_filter_fingerprint,
    decode_audit_cursor,
    encode_audit_cursor,
)


def test_audit_cursor_round_trips_and_is_bound_to_filters() -> None:
    fingerprint = audit_filter_fingerprint(
        {"event_type": "APPROVE", "since": datetime(2026, 7, 1, tzinfo=UTC)}
    )
    value = encode_audit_cursor(
        after_at=datetime(2026, 7, 13, 12, 0, tzinfo=UTC),
        after_id="11111111-1111-1111-1111-111111111111",
        snapshot_at=datetime(2026, 7, 13, 13, 0, tzinfo=UTC),
        snapshot_id="22222222-2222-2222-2222-222222222222",
        filter_fingerprint=fingerprint,
    )

    decoded = decode_audit_cursor(value, filter_fingerprint=fingerprint)

    assert decoded.after_id == "11111111-1111-1111-1111-111111111111"
    assert decoded.snapshot_id == "22222222-2222-2222-2222-222222222222"
    with pytest.raises(ValueError, match="invalid audit cursor"):
        decode_audit_cursor(
            value,
            filter_fingerprint=audit_filter_fingerprint({"event_type": "REJECT"}),
        )


@pytest.mark.parametrize("value", ["", "not-base64", "e30", "a" * 2049])
def test_audit_cursor_rejects_malformed_values(value: str) -> None:
    with pytest.raises(ValueError, match="invalid audit cursor"):
        decode_audit_cursor(value, filter_fingerprint="f" * 64)


def test_audit_cursor_rejects_ids_that_cannot_be_compared_by_lakebase() -> None:
    fingerprint = "f" * 64
    payload = {
        "v": 1,
        "a": "2026-07-13T12:00:00+00:00",
        "i": "not-a-database-audit-id",
        "s": "2026-07-13T13:00:00+00:00",
        "j": "22222222-2222-4222-8222-222222222222",
        "f": fingerprint,
    }
    value = (
        base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        .decode("ascii")
        .rstrip("=")
    )

    with pytest.raises(ValueError, match="invalid audit cursor"):
        decode_audit_cursor(value, filter_fingerprint=fingerprint)
