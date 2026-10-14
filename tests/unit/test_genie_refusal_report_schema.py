"""Lakebase contract for Genie refusal false-positive reports.

The report row stays hash-only; a consented question copy lives only in the
sibling ``genie_refusal_report_texts`` table (D-audit-reads-d).
"""

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


def _texts_table_ddl() -> str:
    match = re.search(
        r"CREATE TABLE IF NOT EXISTS mip_app\.genie_refusal_report_texts \((.*?)\n\);",
        _SCHEMA,
        flags=re.DOTALL,
    )
    assert match is not None
    return match.group(1)


def test_the_text_table_holds_one_scrubbed_expiring_question_per_report() -> None:
    ddl = _texts_table_ddl()
    assert (
        "report_id      UUID PRIMARY KEY REFERENCES mip_app.genie_refusal_reports(report_id)" in ddl
    )
    assert (
        "question_text  TEXT CHECK (question_text IS NULL OR length(question_text) "
        "BETWEEN 1 AND 16000)" in ddl
    )
    assert "redacted       BOOLEAN NOT NULL" in ddl
    assert "captured_at    TIMESTAMPTZ NOT NULL DEFAULT now()" in ddl
    # No DEFAULT: the insert sets now() + 90 days (no arithmetic in the catalog).
    assert re.search(r"^\s+expires_at\s+TIMESTAMPTZ NOT NULL,$", ddl, flags=re.MULTILINE)
    assert "purged_at      TIMESTAMPTZ," in ddl
    assert "CHECK ((question_text IS NULL) = (purged_at IS NOT NULL))" in ddl
    assert "CHECK (expires_at > captured_at)" in ddl
    assert re.search(
        r"CREATE INDEX IF NOT EXISTS idx_genie_refusal_report_texts_expiry\s+"
        r"ON mip_app\.genie_refusal_report_texts \(expires_at\)\s+WHERE purged_at IS NULL;",
        _SCHEMA,
    )


def test_the_report_row_still_carries_no_text_and_the_comments_say_where_text_lives() -> None:
    assert "question_text" not in _report_table_ddl()
    assert (
        "COMMENT ON TABLE mip_app.genie_refusal_reports IS\n"
        "    'Lender reports that a governed Genie refusal was a false positive: the report "
        "row holds no question text, only its digest; a consented, scrubbed, 90-day, "
        "purge-only copy may live in genie_refusal_report_texts.';"
    ) in _SCHEMA
    assert "COMMENT ON TABLE mip_app.genie_refusal_report_texts IS" in _SCHEMA
    texts_comment = _SCHEMA[_SCHEMA.index("COMMENT ON TABLE mip_app.genie_refusal_report_texts IS") :]
    texts_comment = texts_comment[: texts_comment.index("';") ]
    for phrase in ("scrubbed", "nulled at 90 days", "never deleted", "auditors", "audited"):
        assert phrase in texts_comment, phrase
    # genie_messages still never stores a refused turn; the text-posture
    # comment names the one consented copy.
    posture = _SCHEMA[_SCHEMA.index("-- Text posture (revised 2026-08-06") :]
    posture = posture[: posture.index("CREATE TABLE IF NOT EXISTS mip_app.genie_sessions")]
    assert "turns are never recorded" in posture
    assert "genie_refusal_report_texts" in posture


def test_the_text_table_is_reachable_without_delete() -> None:
    assert lakebase_migrate._APP_ROLE_TABLE_PRIVILEGES["genie_refusal_report_texts"] == (
        "SELECT",
        "INSERT",
        "UPDATE",
    )
    assert lakebase_migrate._APP_ROLE_ROUTINE_PRIVILEGES[("prevent_refusal_text_mutation", "")] == ()
    contract = lakebase_migrate._APP_TRIGGER_CONTRACT
    assert contract[
        ("mip_app", "genie_refusal_report_texts", "trg_genie_refusal_report_texts_purge_only")
    ] == ("mip_app", "prevent_refusal_text_mutation", "", 19)
    assert contract[
        ("mip_app", "genie_refusal_report_texts", "trg_genie_refusal_report_texts_no_remove")
    ] == ("mip_app", "prevent_outreach_evidence_mutation", "", 42)
