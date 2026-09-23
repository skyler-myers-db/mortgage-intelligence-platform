"""Lakebase contract for hash-only Genie refusal false-positive reports."""

from __future__ import annotations

import re
from pathlib import Path

from backend.api import genie_refusal_report as genie_refusal_report_api
from backend.services.genie_refusal_reason import GENIE_REFUSAL_REASONS
from jobs import lakebase_migrate

ROOT = Path(__file__).resolve().parents[2]
_SCHEMA = (ROOT / "lakebase" / "schema.sql").read_text(encoding="utf-8")


def _report_table_ddl() -> str:
    match = re.search(
        r"CREATE TABLE IF NOT EXISTS mip_app\.genie_refusal_reports \((.*?)\n\);",
        _SCHEMA,
        flags=re.DOTALL,
    )
    assert match is not None
    return match.group(1)


def test_refusal_report_schema_is_hash_only_and_replay_safe() -> None:
    ddl = _report_table_ddl()

    assert "question_hash    TEXT NOT NULL CHECK (question_hash ~ '^[0-9a-f]{64}$')" in ddl
    assert "audit_event_id   UUID REFERENCES mip_app.action_audit(audit_id)" in ddl
    assert "UNIQUE (actor_email, question_hash, refusal_reason)" in ddl
    assert "2026_09_22_genie_refusal_reports" in _SCHEMA
    # No column can hold the refused prompt: the only TEXT columns are the
    # actor, the digest, the family and two shape-checked opaque ids.
    text_columns = re.findall(r"^\s+([a-z_]+)\s+TEXT\b", ddl, flags=re.MULTILINE)
    assert set(text_columns) == {
        "actor_email",
        "question_hash",
        "refusal_reason",
        "conversation_id",
        "message_id",
    }
    assert re.search(r"\b(question|question_text|comment|prompt)\s+TEXT\b", ddl) is None


def test_refusal_report_family_check_matches_the_wire_enum() -> None:
    ddl = _report_table_ddl()
    match = re.search(r"refusal_reason IN \((.*?)\)\)", ddl, flags=re.DOTALL)
    assert match is not None
    families = set(re.findall(r"'([a-z_]+)'", match.group(1)))
    assert families == set(GENIE_REFUSAL_REASONS)


def test_refusal_report_table_is_reachable_by_the_app_role() -> None:
    assert lakebase_migrate._APP_ROLE_TABLE_PRIVILEGES["genie_refusal_reports"] == (
        "SELECT",
        "INSERT",
        "UPDATE",
    )


def test_refusal_report_id_checks_match_the_route_shape() -> None:
    # The table CHECKs and the route validator admit the same server-issued id
    # shape, so neither layer accepts a free-form token the other refuses.
    ddl = _report_table_ddl()
    sql_patterns = re.findall(r"(conversation_id|message_id) ~\* '\^(.*?)\$'", ddl)
    assert [column for column, _ in sql_patterns] == ["conversation_id", "message_id"]
    route_pattern = genie_refusal_report_api._GENIE_ID_RE.pattern
    assert route_pattern.startswith("^") and route_pattern.endswith("$")
    route_body = route_pattern[1:-1].replace("(?:", "(")
    for _, sql_body in sql_patterns:
        assert sql_body == route_body
    assert genie_refusal_report_api._GENIE_ID_RE.flags & re.IGNORECASE
