"""Lakebase contract for native Genie feedback delivery state."""

from __future__ import annotations

import re
from pathlib import Path

_SCHEMA = Path("lakebase/schema.sql").read_text(encoding="utf-8")


def _feedback_table_ddl() -> str:
    match = re.search(
        r"CREATE TABLE IF NOT EXISTS mip_app\.genie_feedback_requests \((.*?)\n\);",
        _SCHEMA,
        flags=re.DOTALL,
    )
    assert match is not None
    return match.group(1)


def test_genie_feedback_schema_has_idempotent_audited_delivery_state() -> None:
    ddl = _feedback_table_ddl()

    assert "CONSTRAINT uq_genie_feedback_actor_request UNIQUE (actor_email, request_id)" in ddl
    assert "rating IN ('POSITIVE', 'NEGATIVE')" in ddl
    assert all(
        status in ddl for status in ("PENDING", "IN_FLIGHT", "SUCCEEDED", "RETRYABLE_FAILED")
    )
    assert "attempt_count" in ddl
    assert "intent_audit_event_id UUID REFERENCES mip_app.action_audit(audit_id)" in ddl
    assert "audit_event_id        UUID REFERENCES mip_app.action_audit(audit_id)" in ddl
    assert "FOREIGN KEY (conversation_id, message_id)" in ddl
    assert "2026_07_13_native_genie_feedback" in _SCHEMA


def test_genie_feedback_schema_never_persists_optional_comment_text() -> None:
    ddl = _feedback_table_ddl()

    assert "comment_present" in ddl
    assert re.search(r"\bcomment\s+TEXT\b", ddl, flags=re.IGNORECASE) is None
