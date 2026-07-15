"""Live Delta replay proof for the generated lifecycle-state MERGE.

This test is intentionally mutation-gated. It creates two uniquely named,
non-PII scratch Delta tables in the existing governed audit schema, executes
the production SQL generator against them, and drops both tables even when an
assertion fails. Local and pull-request runs skip unless the warehouse
credentials and explicit mutation opt-in are present.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from jobs import sync_lifecycle_state

_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9_]+")
_TERMINAL_STATES = {"SUCCEEDED", "FAILED", "CANCELED", "CLOSED"}
_STATEMENT_TIMEOUT_SECONDS = 180


def _live_warehouse_config() -> tuple[str, str, str] | None:
    if os.environ.get("MIP_LIVE_MUTATION_OK") != "1":
        return None
    host = os.environ.get("DATABRICKS_HOST") or os.environ.get(
        "DATABRICKS_SERVER_HOSTNAME"
    )
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
        raise AssertionError(
            f"Databricks SQL API returned HTTP {exc.code}: {detail}"
        ) from exc
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


@pytest.fixture
def live_warehouse() -> tuple[str, str, str]:
    config = _live_warehouse_config()
    if config is None:
        pytest.skip(
            "Set DATABRICKS_HOST, DATABRICKS_TOKEN, DATABRICKS_WAREHOUSE_ID, "
            "and MIP_LIVE_MUTATION_OK=1 to run the isolated Delta replay proof."
        )
    return config


def test_generated_lifecycle_merge_is_monotonic_in_live_delta(
    live_warehouse: tuple[str, str, str],
) -> None:
    catalog = _safe_identifier(
        os.environ.get("MIP_DEFAULT_CATALOG", "mip"),
        field="catalog",
    )
    schema = _safe_identifier(
        os.environ.get("MIP_LIFECYCLE_SMOKE_SCHEMA", "audit"),
        field="schema",
    )
    suffix = uuid4().hex.lower()
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
    borrower_id = "B-" + suffix[:13].upper()
    stale_time = datetime(2026, 1, 15, 14, 0, tzinfo=UTC)
    newer_time = datetime(2026, 1, 15, 14, 1, tzinfo=UTC)
    low_event_id = "00000000-0000-4000-8000-000000000001"
    high_event_id = "00000000-0000-4000-8000-000000000002"

    def merge(row: dict[str, object]) -> None:
        generated = sync_lifecycle_state._build_lifecycle_merge(
            [row],
            catalog=catalog,
        )
        _execute_statement(
            live_warehouse,
            _rewrite_generated_merge_for_scratch(
                generated,
                catalog=catalog,
                scratch_borrower_table=borrower_table,
                scratch_lifecycle_table=lifecycle_table,
            ),
        )

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
        equal_timestamp_higher_id = _approval_row(
            borrower_id=borrower_id,
            status="hold",
            offer_code="smoke_equal_timestamp",
            decided_at=newer_time,
            event_id=high_event_id,
        )

        merge(stale)
        assert _selected_approval(
            live_warehouse,
            lifecycle_table=lifecycle_table,
            borrower_id=borrower_id,
        ) == ("approved", "smoke_stale", low_event_id)

        merge(newer)
        expected_newer = ("rejected", "smoke_newer", low_event_id)
        assert _selected_approval(
            live_warehouse,
            lifecycle_table=lifecycle_table,
            borrower_id=borrower_id,
        ) == expected_newer

        merge(stale)
        assert _selected_approval(
            live_warehouse,
            lifecycle_table=lifecycle_table,
            borrower_id=borrower_id,
        ) == expected_newer

        merge(equal_timestamp_higher_id)
        expected_equal_order_winner = (
            "hold",
            "smoke_equal_timestamp",
            high_event_id,
        )
        assert _selected_approval(
            live_warehouse,
            lifecycle_table=lifecycle_table,
            borrower_id=borrower_id,
        ) == expected_equal_order_winner

        merge(newer)
        assert _selected_approval(
            live_warehouse,
            lifecycle_table=lifecycle_table,
            borrower_id=borrower_id,
        ) == expected_equal_order_winner
    finally:
        # Both cleanup attempts run even if table creation or a replay
        # assertion fails. A cleanup failure remains release-blocking.
        cleanup_errors: list[BaseException] = []
        for table in (lifecycle_table, borrower_table):
            try:
                _execute_statement(live_warehouse, f"DROP TABLE IF EXISTS {table}")
            except BaseException as exc:  # noqa: BLE001 -- preserve both cleanup attempts
                cleanup_errors.append(exc)
        if cleanup_errors:
            raise ExceptionGroup("lifecycle replay scratch cleanup failed", cleanup_errors)
