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
        after_sequence=7,
        snapshot_sequence=11,
        snapshot_token="10:20:11,14",
        filter_fingerprint=fingerprint,
    )

    decoded = decode_audit_cursor(value, filter_fingerprint=fingerprint)

    assert decoded.after_sequence == 7
    assert decoded.snapshot_sequence == 11
    assert decoded.snapshot_token == "10:20:11,14"
    with pytest.raises(ValueError, match="invalid audit cursor"):
        decode_audit_cursor(
            value,
            filter_fingerprint=audit_filter_fingerprint({"event_type": "REJECT"}),
        )


@pytest.mark.parametrize("value", ["", "not-base64", "e30", "a" * 2049])
def test_audit_cursor_rejects_malformed_values(value: str) -> None:
    with pytest.raises(ValueError, match="invalid audit cursor"):
        decode_audit_cursor(value, filter_fingerprint="f" * 64)


def test_audit_cursor_rejects_client_modified_boundaries() -> None:
    fingerprint = "f" * 64
    value = encode_audit_cursor(
        after_sequence=7,
        snapshot_sequence=11,
        snapshot_token="10:20:",
        filter_fingerprint=fingerprint,
    )
    encoded_payload, signature = value.split(".")
    replacement = ("A" if encoded_payload[0] != "A" else "B") + encoded_payload[1:]

    with pytest.raises(ValueError, match="invalid audit cursor"):
        decode_audit_cursor(f"{replacement}.{signature}", filter_fingerprint=fingerprint)


@pytest.mark.parametrize(
    ("after_sequence", "snapshot_sequence"),
    [(0, 1), (1, 0), (12, 11), (-1, 1)],
)
def test_audit_cursor_rejects_invalid_sequence_boundaries(
    after_sequence: int,
    snapshot_sequence: int,
) -> None:
    value = encode_audit_cursor(
        after_sequence=after_sequence,
        snapshot_sequence=snapshot_sequence,
        snapshot_token="10:20:",
        filter_fingerprint="f" * 64,
    )
    with pytest.raises(ValueError, match="invalid audit cursor"):
        decode_audit_cursor(value, filter_fingerprint="f" * 64)


@pytest.mark.parametrize("snapshot_token", ["", "forged", "10:20:x", "memory:-1"])
def test_audit_cursor_rejects_invalid_snapshot_tokens(snapshot_token: str) -> None:
    value = encode_audit_cursor(
        after_sequence=1,
        snapshot_sequence=2,
        snapshot_token=snapshot_token,
        filter_fingerprint="f" * 64,
    )
    with pytest.raises(ValueError, match="invalid audit cursor"):
        decode_audit_cursor(value, filter_fingerprint="f" * 64)
