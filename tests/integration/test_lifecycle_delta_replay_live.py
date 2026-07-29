"""Live Delta replay proof for the generated lifecycle-state MERGE.

This test is intentionally mutation-gated. It creates two uniquely named,
non-PII scratch Delta tables in the existing governed audit schema, executes
the production SQL generator against them, and drops both tables even when an
assertion fails. Local and pull-request runs skip unless the warehouse
credentials and explicit mutation opt-in are present.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from jobs import sync_lifecycle_state

_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SCRATCH_SUFFIX_RE = re.compile(r"gha_[0-9]+")
_TERMINAL_STATES = {"SUCCEEDED", "FAILED", "CANCELED", "CLOSED"}
_STATEMENT_TIMEOUT_SECONDS = 180
_REVIEW_DIGEST_ENV = "MIP_LIFECYCLE_REPLAY_REVIEW_SHA256"
_REVIEW_ARTIFACT_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "validation"
    / "lifecycle-replay-sql-2026-07-29.json"
)


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _live_warehouse_config() -> tuple[str, str, str] | None:
    if os.environ.get("MIP_LIVE_MUTATION_OK") != "1":
        return None
    host = os.environ.get("DATABRICKS_HOST") or os.environ.get("DATABRICKS_SERVER_HOSTNAME")
    token = os.environ.get("DATABRICKS_TOKEN")
    warehouse_id = os.environ.get("DATABRICKS_WAREHOUSE_ID")
    if not host or not token or not warehouse_id:
        return None
    if not host.startswith("http"):
        host = "https://" + host
    return host.rstrip("/"), token, warehouse_id


def _safe_identifier(value: str, *, field: str) -> str:
    normalized = value.strip()
    if not _IDENTIFIER_RE.fullmatch(normalized):
        raise ValueError(f"unsafe {field} identifier: {value!r}")
    return normalized


def _qualified_table(catalog: str, schema: str, table: str) -> str:
    return ".".join(
        f"`{_safe_identifier(value, field=field)}`"
        for value, field in (
            (catalog, "catalog"),
            (schema, "schema"),
            (table, "table"),
        )
    )


def _scratch_suffix() -> str:
    suffix = os.environ.get("MIP_LIVE_SCRATCH_SUFFIX", "").strip()
    if not _SCRATCH_SUFFIX_RE.fullmatch(suffix):
        raise ValueError("unsafe MIP_LIVE_SCRATCH_SUFFIX: expected deterministic gha_[0-9]+")
    return suffix


def _rewrite_generated_merge_for_scratch(
    statement: str,
    *,
    catalog: str,
    scratch_borrower_table: str,
    scratch_lifecycle_table: str,
) -> str:
    """Retarget only the two exact production FQNs in generated SQL."""
    production_borrower = sync_lifecycle_state._qualified_uc_table(
        catalog,
        "gold",
        "borrower_360",
    )
    production_lifecycle = sync_lifecycle_state._qualified_uc_table(
        catalog,
        "gold",
        "borrower_lifecycle_state",
    )
    replacements = (
        (production_borrower, scratch_borrower_table),
        (production_lifecycle, scratch_lifecycle_table),
    )
    rewritten = statement
    for production_fqn, scratch_fqn in replacements:
        occurrences = rewritten.count(production_fqn)
        if occurrences != 1:
            raise AssertionError(
                "lifecycle MERGE generator shape drifted: expected exactly one "
                f"{production_fqn}, found {occurrences}"
            )
        rewritten = rewritten.replace(production_fqn, scratch_fqn, 1)
    for production_fqn, _scratch_fqn in replacements:
        if production_fqn in rewritten:
            raise AssertionError(f"production FQN survived scratch rewrite: {production_fqn}")
    return rewritten


def _request_json(
    url: str,
    *,
    token: str,
    payload: dict[str, object] | None = None,
) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method="POST" if payload is not None else "GET",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            return dict(json.loads(response.read().decode("utf-8")))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8")[:2000]
        raise AssertionError(f"Databricks SQL API returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise AssertionError(f"Databricks SQL API was unreachable: {exc.reason}") from exc


def _execute_statement(
    config: tuple[str, str, str],
    statement: str,
) -> list[list[Any]]:
    host, token, warehouse_id = config
    endpoint = f"{host}/api/2.0/sql/statements"
    body = _request_json(
        endpoint,
        token=token,
        payload={
            "statement": statement,
            "warehouse_id": warehouse_id,
            "wait_timeout": "50s",
            "on_wait_timeout": "CONTINUE",
            "disposition": "INLINE",
            "format": "JSON_ARRAY",
        },
    )
    deadline = time.monotonic() + _STATEMENT_TIMEOUT_SECONDS
    while str(body.get("status", {}).get("state") or "") not in _TERMINAL_STATES:
        statement_id = str(body.get("statement_id") or "")
        if not statement_id:
            raise AssertionError(f"SQL response omitted statement_id: {body!r}")
        if time.monotonic() >= deadline:
            raise AssertionError(
                f"Databricks SQL statement {statement_id} exceeded "
                f"{_STATEMENT_TIMEOUT_SECONDS}s"
            )
        time.sleep(1)
        body = _request_json(f"{endpoint}/{statement_id}", token=token)

    state = str(body.get("status", {}).get("state") or "")
    if state != "SUCCEEDED":
        error = body.get("status", {}).get("error", {})
        raise AssertionError(f"Databricks SQL statement ended in {state}: {error!r}")
    result = body.get("result") or {}
    return list(result.get("data_array") or [])


def _approval_row(
    *,
    borrower_id: str,
    status: str,
    offer_code: str,
    decided_at: datetime,
    event_id: str,
) -> dict[str, object]:
    return {
        "borrower_id": borrower_id,
        "approval_status": status,
        "outreach_status": "none",
        "offer_code": offer_code,
        "approved_at": decided_at if status == "approved" else None,
        "approval_decided_at": decided_at,
        "approval_event_id": event_id,
        "outreach_at": None,
        "outreach_created_at": None,
        "outreach_event_id": None,
    }


def _selected_approval(
    config: tuple[str, str, str],
    *,
    lifecycle_table: str,
    borrower_id: str,
) -> tuple[str, str, str]:
    rows = _execute_statement(
        config,
        f"""
        SELECT approval_status, offer_code, approval_event_id
        FROM {lifecycle_table}
        WHERE borrower_id = '{borrower_id}'
        """,
    )
    assert len(rows) == 1, rows
    status, offer_code, event_id = rows[0]
    return str(status), str(offer_code), str(event_id)


def _selected_outreach(
    config: tuple[str, str, str],
    *,
    lifecycle_table: str,
    borrower_id: str,
    expected_at: datetime,
    expected_created_at: datetime,
) -> tuple[str, bool, bool, str]:
    rows = _execute_statement(
        config,
        f"""
        SELECT
          outreach_status,
          CAST(outreach_at = {sync_lifecycle_state._sql_timestamp(expected_at)} AS STRING),
          CAST(
            outreach_created_at =
              {sync_lifecycle_state._sql_timestamp(expected_created_at)}
            AS STRING
          ),
          outreach_event_id
        FROM {lifecycle_table}
        WHERE borrower_id = '{borrower_id}'
        """,
    )
    assert len(rows) == 1, rows
    status, at_matches, created_at_matches, event_id = rows[0]
    return (
        str(status),
        str(at_matches).lower() == "true",
        str(created_at_matches).lower() == "true",
        str(event_id),
    )


def _selected_refresh_marker(
    config: tuple[str, str, str],
    *,
    lifecycle_table: str,
    borrower_id: str,
) -> str:
    rows = _execute_statement(
        config,
        f"""
        SELECT CAST(refreshed_at AS STRING)
        FROM {lifecycle_table}
        WHERE borrower_id = '{borrower_id}'
        """,
    )
    assert len(rows) == 1, rows
    assert rows[0][0] is not None, rows
    return str(rows[0][0])


def _with_outreach(
    row: dict[str, object],
    *,
    occurred_at: datetime,
    created_at: datetime,
    event_id: str,
) -> dict[str, object]:
    return {
        **row,
        "outreach_status": "actioned",
        "outreach_at": occurred_at,
        "outreach_created_at": created_at,
        "outreach_event_id": event_id,
    }


def _replay_rows(borrower_id: str) -> dict[str, dict[str, object]]:
    stale_time = datetime(2026, 1, 15, 14, 0, tzinfo=UTC)
    newer_time = datetime(2026, 1, 15, 14, 1, tzinfo=UTC)
    low_event_id = "00000000-0000-4000-8000-000000000001"
    high_event_id = "00000000-0000-4000-8000-000000000002"
    stale = _approval_row(
        borrower_id=borrower_id,
        status="approved",
        offer_code="smoke_stale",
        decided_at=stale_time,
        event_id=low_event_id,
    )
    newer = _approval_row(
        borrower_id=borrower_id,
        status="rejected",
        offer_code="smoke_newer",
        decided_at=newer_time,
        event_id=low_event_id,
    )
    equal = _approval_row(
        borrower_id=borrower_id,
        status="hold",
        offer_code="smoke_equal_timestamp",
        decided_at=newer_time,
        event_id=high_event_id,
    )
    outreach_at = datetime(2026, 1, 15, 15, 0, tzinfo=UTC)
    outreach_created_at = datetime(2026, 1, 15, 15, 1, tzinfo=UTC)
    outreach_newer_at = datetime(2026, 1, 15, 15, 2, tzinfo=UTC)
    outreach_newer_created_at = datetime(2026, 1, 15, 15, 3, tzinfo=UTC)
    outreach_highest_created_at = datetime(2026, 1, 15, 15, 4, tzinfo=UTC)
    outreach_low_id = "00000000-0000-4000-8000-000000000011"
    outreach_high_id = "00000000-0000-4000-8000-000000000012"
    return {
        "stale": stale,
        "newer": newer,
        "equal": equal,
        "first_outreach": _with_outreach(
            equal,
            occurred_at=outreach_at,
            created_at=outreach_created_at,
            event_id=outreach_low_id,
        ),
        "newer_outreach": _with_outreach(
            equal,
            occurred_at=outreach_newer_at,
            created_at=outreach_newer_created_at,
            event_id=outreach_low_id,
        ),
        "stale_outreach": _with_outreach(
            equal,
            occurred_at=outreach_at,
            created_at=outreach_highest_created_at,
            event_id=outreach_high_id,
        ),
        "newer_created_at": _with_outreach(
            equal,
            occurred_at=outreach_newer_at,
            created_at=outreach_highest_created_at,
            event_id=outreach_low_id,
        ),
        "higher_outreach_id": _with_outreach(
            equal,
            occurred_at=outreach_newer_at,
            created_at=outreach_highest_created_at,
            event_id=outreach_high_id,
        ),
    }


def _ordered_replay_rows(rows: dict[str, dict[str, object]]) -> tuple[dict[str, object], ...]:
    return tuple(
        rows[name]
        for name in (
            "stale",
            "newer",
            "stale",
            "equal",
            "newer",
            "first_outreach",
            "newer_outreach",
            "stale_outreach",
            "newer_created_at",
            "higher_outreach_id",
            "newer_created_at",
        )
    )


def _render_merge_plan(
    *,
    catalog: str,
    borrower_table: str,
    lifecycle_table: str,
    rows: dict[str, dict[str, object]],
) -> tuple[str, ...]:
    return tuple(
        _rewrite_generated_merge_for_scratch(
            sync_lifecycle_state._build_lifecycle_merge([row], catalog=catalog),
            catalog=catalog,
            scratch_borrower_table=borrower_table,
            scratch_lifecycle_table=lifecycle_table,
        )
        for row in _ordered_replay_rows(rows)
    )


def _canonical_review_plan(
    statements: tuple[str, ...],
    *,
    catalog: str,
    borrower_table: str,
    lifecycle_table: str,
) -> tuple[str, ...]:
    def canonical_scratch_name(table: str) -> str:
        if not table.startswith(f"`{catalog}`.`audit`."):
            raise AssertionError("scratch table escaped the reviewed catalog/schema")
        canonical, replacements = re.subn(
            r"gha_[0-9]+`\Z",
            "gha_<run>`",
            table,
        )
        if replacements != 1:
            raise AssertionError("scratch table lost its deterministic GitHub-run suffix")
        return canonical

    canonical_borrower_table = canonical_scratch_name(borrower_table)
    canonical_lifecycle_table = canonical_scratch_name(lifecycle_table)
    canonical: list[str] = []
    for statement in statements:
        if statement.count(borrower_table) != 1 or statement.count(lifecycle_table) != 1:
            raise AssertionError("rendered replay SQL lost its exact scratch-table binding")
        canonical.append(
            statement.replace(borrower_table, canonical_borrower_table).replace(
                lifecycle_table,
                canonical_lifecycle_table,
            )
        )
    return tuple(canonical)


def _review_manifest(*, catalog: str) -> dict[str, object]:
    borrower_id = "B-REPLAY0000001"
    borrower_table = _qualified_table(
        catalog,
        "audit",
        "lifecycle_replay_borrower_gha_0",
    )
    lifecycle_table = _qualified_table(
        catalog,
        "audit",
        "lifecycle_replay_target_gha_0",
    )
    canonical = _canonical_review_plan(
        _render_merge_plan(
            catalog=catalog,
            borrower_table=borrower_table,
            lifecycle_table=lifecycle_table,
            rows=_replay_rows(borrower_id),
        ),
        catalog=catalog,
        borrower_table=borrower_table,
        lifecycle_table=lifecycle_table,
    )
    reviewed_payload = {
        "contract": "mip-lifecycle-delta-replay-v1",
        "catalog": catalog,
        "scratch_schema": "audit",
        "statements": list(canonical),
    }
    digest = hashlib.sha256(
        json.dumps(
            reviewed_payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        **reviewed_payload,
        "borrower_id": borrower_id,
        "statement_count": len(canonical),
        "sha256": digest,
    }


def _assert_reviewed_merge_plan(
    statements: tuple[str, ...],
    *,
    catalog: str,
    borrower_table: str,
    lifecycle_table: str,
) -> None:
    canonical = _canonical_review_plan(
        statements,
        catalog=catalog,
        borrower_table=borrower_table,
        lifecycle_table=lifecycle_table,
    )
    reviewed_payload = {
        "contract": "mip-lifecycle-delta-replay-v1",
        "catalog": catalog,
        "scratch_schema": "audit",
        "statements": list(canonical),
    }
    actual = hashlib.sha256(
        json.dumps(
            reviewed_payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    try:
        artifact = json.loads(
            _REVIEW_ARTIFACT_PATH.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_json_object,
        )
    except (OSError, ValueError) as exc:
        raise RuntimeError("committed lifecycle replay review artifact is unavailable") from exc
    expected_fields = {
        "borrower_id",
        "catalog",
        "contract",
        "scratch_schema",
        "sha256",
        "statement_count",
        "statements",
    }
    if not isinstance(artifact, dict) or set(artifact) != expected_fields:
        raise RuntimeError("committed lifecycle replay review artifact has invalid shape")
    artifact_statements = artifact.get("statements")
    if not isinstance(artifact_statements, list) or any(
        not isinstance(statement, str) for statement in artifact_statements
    ):
        raise RuntimeError("committed lifecycle replay review artifact has invalid SQL")
    artifact_payload = {
        "contract": artifact.get("contract"),
        "catalog": artifact.get("catalog"),
        "scratch_schema": artifact.get("scratch_schema"),
        "statements": artifact_statements,
    }
    artifact_digest = hashlib.sha256(
        json.dumps(
            artifact_payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    artifact_sha256 = artifact.get("sha256")
    if (
        artifact.get("borrower_id") != "B-REPLAY0000001"
        or artifact.get("statement_count") != len(artifact_statements)
        or artifact_sha256 != artifact_digest
    ):
        raise RuntimeError("committed lifecycle replay review artifact digest is invalid")
    if artifact_payload != reviewed_payload or artifact_sha256 != actual:
        raise RuntimeError("runtime lifecycle replay SQL differs from the committed review")
    expected = os.environ.get(_REVIEW_DIGEST_ENV, "").strip()
    if not expected:
        raise RuntimeError(
            f"{_REVIEW_DIGEST_ENV} is required; render and govern the exact SQL first"
        )
    if not re.fullmatch(r"[0-9a-f]{64}", expected) or expected != artifact_sha256:
        raise RuntimeError("rendered lifecycle replay SQL differs from the governed digest")


@pytest.fixture
def live_warehouse() -> tuple[str, str, str]:
    config = _live_warehouse_config()
    if config is None:
        pytest.skip(
            "Set DATABRICKS_HOST, DATABRICKS_TOKEN, DATABRICKS_WAREHOUSE_ID, "
            "and MIP_LIVE_MUTATION_OK=1 to run the isolated Delta replay proof."
        )
    assert config is not None
    _scratch_suffix()
    return config


def test_generated_lifecycle_merge_is_monotonic_in_live_delta(
    live_warehouse: tuple[str, str, str],
) -> None:
    catalog = _safe_identifier(
        os.environ.get("MIP_DEFAULT_CATALOG", "mip"),
        field="catalog",
    )
    # The audit schema is the reviewed mutation boundary. Environment input
    # may select a catalog, but never a less-governed schema.
    schema = "audit"
    suffix = _scratch_suffix()
    borrower_table = _qualified_table(
        catalog,
        schema,
        f"lifecycle_replay_borrower_{suffix}",
    )
    lifecycle_table = _qualified_table(
        catalog,
        schema,
        f"lifecycle_replay_target_{suffix}",
    )
    borrower_id = "B-REPLAY0000001"
    rows = _replay_rows(borrower_id)
    reviewed_merges = _render_merge_plan(
        catalog=catalog,
        borrower_table=borrower_table,
        lifecycle_table=lifecycle_table,
        rows=rows,
    )
    _assert_reviewed_merge_plan(
        reviewed_merges,
        catalog=catalog,
        borrower_table=borrower_table,
        lifecycle_table=lifecycle_table,
    )
    merge_index = 0
    low_event_id = "00000000-0000-4000-8000-000000000001"
    high_event_id = "00000000-0000-4000-8000-000000000002"

    def merge(row: dict[str, object]) -> None:
        nonlocal merge_index
        statement = _rewrite_generated_merge_for_scratch(
            sync_lifecycle_state._build_lifecycle_merge([row], catalog=catalog),
            catalog=catalog,
            scratch_borrower_table=borrower_table,
            scratch_lifecycle_table=lifecycle_table,
        )
        if merge_index >= len(reviewed_merges) or statement != reviewed_merges[merge_index]:
            raise AssertionError("lifecycle replay execution diverged from reviewed SQL order")
        merge_index += 1
        _execute_statement(live_warehouse, statement)

    try:
        _execute_statement(
            live_warehouse,
            f"""
            CREATE TABLE {borrower_table} (
              borrower_id STRING NOT NULL
            )
            USING DELTA
            COMMENT 'Ephemeral non-PII lifecycle replay borrower fixture'
            """,
        )
        _execute_statement(
            live_warehouse,
            f"""
            CREATE TABLE {lifecycle_table} (
              borrower_id STRING NOT NULL,
              approval_status STRING NOT NULL,
              outreach_status STRING NOT NULL,
              offer_code STRING,
              approved_at TIMESTAMP,
              approval_decided_at TIMESTAMP,
              approval_event_id STRING,
              outreach_at TIMESTAMP,
              outreach_created_at TIMESTAMP,
              outreach_event_id STRING,
              synced_at TIMESTAMP NOT NULL,
              refreshed_at TIMESTAMP NOT NULL
            )
            USING DELTA
            COMMENT 'Ephemeral non-PII lifecycle replay target fixture'
            """,
        )
        _execute_statement(
            live_warehouse,
            f"INSERT INTO {borrower_table} VALUES ('{borrower_id}')",
        )

        stale = rows["stale"]
        newer = rows["newer"]
        equal_timestamp_higher_id = rows["equal"]

        merge(stale)
        assert _selected_approval(
            live_warehouse,
            lifecycle_table=lifecycle_table,
            borrower_id=borrower_id,
        ) == ("approved", "smoke_stale", low_event_id)

        merge(newer)
        expected_newer = ("rejected", "smoke_newer", low_event_id)
        assert (
            _selected_approval(
                live_warehouse,
                lifecycle_table=lifecycle_table,
                borrower_id=borrower_id,
            )
            == expected_newer
        )

        refresh_after_newer = _selected_refresh_marker(
            live_warehouse,
            lifecycle_table=lifecycle_table,
            borrower_id=borrower_id,
        )
        merge(stale)
        assert (
            _selected_approval(
                live_warehouse,
                lifecycle_table=lifecycle_table,
                borrower_id=borrower_id,
            )
            == expected_newer
        )
        assert (
            _selected_refresh_marker(
                live_warehouse,
                lifecycle_table=lifecycle_table,
                borrower_id=borrower_id,
            )
            == refresh_after_newer
        )

        merge(equal_timestamp_higher_id)
        expected_equal_order_winner = (
            "hold",
            "smoke_equal_timestamp",
            high_event_id,
        )
        assert (
            _selected_approval(
                live_warehouse,
                lifecycle_table=lifecycle_table,
                borrower_id=borrower_id,
            )
            == expected_equal_order_winner
        )

        refresh_after_equal_winner = _selected_refresh_marker(
            live_warehouse,
            lifecycle_table=lifecycle_table,
            borrower_id=borrower_id,
        )
        merge(newer)
        assert (
            _selected_approval(
                live_warehouse,
                lifecycle_table=lifecycle_table,
                borrower_id=borrower_id,
            )
            == expected_equal_order_winner
        )
        assert (
            _selected_refresh_marker(
                live_warehouse,
                lifecycle_table=lifecycle_table,
                borrower_id=borrower_id,
            )
            == refresh_after_equal_winner
        )

        outreach_at = datetime(2026, 1, 15, 15, 0, tzinfo=UTC)
        outreach_created_at = datetime(2026, 1, 15, 15, 1, tzinfo=UTC)
        outreach_newer_at = datetime(2026, 1, 15, 15, 2, tzinfo=UTC)
        outreach_newer_created_at = datetime(2026, 1, 15, 15, 3, tzinfo=UTC)
        outreach_highest_created_at = datetime(2026, 1, 15, 15, 4, tzinfo=UTC)
        outreach_low_id = "00000000-0000-4000-8000-000000000011"
        outreach_high_id = "00000000-0000-4000-8000-000000000012"

        first_outreach = rows["first_outreach"]
        merge(first_outreach)
        assert _selected_outreach(
            live_warehouse,
            lifecycle_table=lifecycle_table,
            borrower_id=borrower_id,
            expected_at=outreach_at,
            expected_created_at=outreach_created_at,
        ) == ("actioned", True, True, outreach_low_id)

        newer_outreach = rows["newer_outreach"]
        merge(newer_outreach)
        assert _selected_outreach(
            live_warehouse,
            lifecycle_table=lifecycle_table,
            borrower_id=borrower_id,
            expected_at=outreach_newer_at,
            expected_created_at=outreach_newer_created_at,
        ) == ("actioned", True, True, outreach_low_id)

        # A newer subordinate tuple must not beat an older occurred_at.
        stale_outreach = rows["stale_outreach"]
        refresh_after_newer_outreach = _selected_refresh_marker(
            live_warehouse,
            lifecycle_table=lifecycle_table,
            borrower_id=borrower_id,
        )
        merge(stale_outreach)
        assert _selected_outreach(
            live_warehouse,
            lifecycle_table=lifecycle_table,
            borrower_id=borrower_id,
            expected_at=outreach_newer_at,
            expected_created_at=outreach_newer_created_at,
        ) == ("actioned", True, True, outreach_low_id)
        assert (
            _selected_refresh_marker(
                live_warehouse,
                lifecycle_table=lifecycle_table,
                borrower_id=borrower_id,
            )
            == refresh_after_newer_outreach
        )

        # Equal occurred_at is ordered by created_at, then event id.
        newer_created_at = rows["newer_created_at"]
        merge(newer_created_at)
        assert _selected_outreach(
            live_warehouse,
            lifecycle_table=lifecycle_table,
            borrower_id=borrower_id,
            expected_at=outreach_newer_at,
            expected_created_at=outreach_highest_created_at,
        ) == ("actioned", True, True, outreach_low_id)

        higher_outreach_id = rows["higher_outreach_id"]
        merge(higher_outreach_id)
        assert _selected_outreach(
            live_warehouse,
            lifecycle_table=lifecycle_table,
            borrower_id=borrower_id,
            expected_at=outreach_newer_at,
            expected_created_at=outreach_highest_created_at,
        ) == ("actioned", True, True, outreach_high_id)

        refresh_after_outreach_winner = _selected_refresh_marker(
            live_warehouse,
            lifecycle_table=lifecycle_table,
            borrower_id=borrower_id,
        )
        merge(newer_created_at)
        assert _selected_outreach(
            live_warehouse,
            lifecycle_table=lifecycle_table,
            borrower_id=borrower_id,
            expected_at=outreach_newer_at,
            expected_created_at=outreach_highest_created_at,
        ) == ("actioned", True, True, outreach_high_id)
        assert (
            _selected_refresh_marker(
                live_warehouse,
                lifecycle_table=lifecycle_table,
                borrower_id=borrower_id,
            )
            == refresh_after_outreach_winner
        )
        assert merge_index == len(reviewed_merges)
    finally:
        # Both cleanup attempts run even if table creation or a replay
        # assertion fails. A cleanup failure remains release-blocking.
        cleanup_errors: list[Exception] = []
        for table in (lifecycle_table, borrower_table):
            try:
                _execute_statement(live_warehouse, f"DROP TABLE IF EXISTS {table}")
            except Exception as exc:  # noqa: BLE001 -- preserve both cleanup attempts
                cleanup_errors.append(exc)
        if cleanup_errors:
            raise ExceptionGroup("lifecycle replay scratch cleanup failed", cleanup_errors)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render the exact canonical SQL reviewed before the live Delta replay."
    )
    parser.add_argument("--catalog", default=os.environ.get("MIP_DEFAULT_CATALOG", "mip"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    catalog = _safe_identifier(args.catalog, field="catalog")
    manifest = _review_manifest(catalog=catalog)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(str(manifest["sha256"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
