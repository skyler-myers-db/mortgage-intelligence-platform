"""Wake a Follow-ups snooze on a newer event-dated signal (wow-power-3 phase 2, item 5).

The probe is gold-only and closed: four event-dated signal types, validated
and quoted masked ids in 250-id chunks, and only an id and a date in its
select list (never a CLIP). A signal wakes a snooze only when it is dated
AFTER the snooze's UTC day. The App path wakes after the lifecycle MERGE and
is fail-soft (exc_type only, the sync's result unchanged); the bundle job's
main() wakes after _write_gold and keeps its exit code. Output is counts only.
The real wake UPDATEs run against PostgreSQL in
tests/integration/test_lead_snoozes_postgres.py.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, date, datetime
from typing import Any

import pytest

from backend.services import lifecycle_sync
from jobs import sync_lifecycle_state as job

SNOOZE = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
SNOOZED_AT = datetime(2026, 10, 5, 23, 30, tzinfo=UTC)


def _open(borrower_id: str = "B-0000000000001", snooze_id: str = SNOOZE) -> dict[str, Any]:
    return {"snooze_id": snooze_id, "borrower_id": borrower_id, "snoozed_at": SNOOZED_AT}


def test_the_wake_signal_set_is_the_four_event_dated_types() -> None:
    assert job.WAKE_SIGNAL_TYPES == ("listing", "recent_refi", "recent_payoff", "recent_sale")
    assert "competitor_lien" not in job.WAKE_SIGNAL_TYPES, "ingest-dated: it would re-wake every refresh"


def test_the_probe_is_gold_only_closed_and_never_selects_a_clip() -> None:
    ids = ["B-0000000000001", "B-0000000000001", "B-0000000000002", "B-48291", "B-00000000000O'", "x"]

    [statement] = job._build_snooze_signal_probe(ids, catalog="mip")

    assert "`mip`.`gold`.`borrower_dossier` d JOIN `mip`.`gold`.`evidence_events` e" in statement
    assert "IN ('B-0000000000001', 'B-0000000000002')" in statement, "deduped; invalid ids dropped"
    assert "B-48291" not in statement and "O'" not in statement
    assert "IN ('listing', 'recent_refi', 'recent_payoff', 'recent_sale')" in statement
    select_list = statement.split(" FROM ", 1)[0]
    assert "clip" not in select_list.lower()
    assert re.findall(r"\b(silver|raw|first_party)\b", statement) == []


def test_the_probe_chunks_at_250_ids() -> None:
    ids = [f"B-{index:013d}" for index in range(600)]

    statements = job._build_snooze_signal_probe(ids, catalog="mip")

    assert [statement.count("'B-") for statement in statements] == [250, 250, 100]
    assert job._build_snooze_signal_probe([], catalog="mip") == []


@pytest.mark.parametrize(
    ("signal", "wakes"),
    [
        (date(2026, 10, 6), True),
        ("2026-10-08", True),
        (datetime(2026, 10, 6, 0, 1, tzinfo=UTC), True),
        (date(2026, 10, 5), False),  # same UTC day as the snooze: the timer still wakes it
        ("2026-10-01", False),
        (None, False),
        ("not a date", False),
    ],
)
def test_a_signal_wakes_only_when_dated_after_the_snooze_day(signal: Any, wakes: bool) -> None:
    probe = [{"borrower_id": "B-0000000000001", "latest_signal_date": signal}]

    woken = job._signal_wakes([_open(), _open("B-0000000000002", OTHER)], probe)

    assert woken == ([SNOOZE] if wakes else [])


def test_wake_runs_the_probe_then_one_apply_and_returns_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    applied: list[list[str]] = []
    monkeypatch.setattr(job, "_apply_snooze_wakes", lambda _conn, ids: applied.append(ids) or (len(ids), 4))
    statements: list[str] = []

    def execute(statement: str) -> list[dict[str, Any]]:
        statements.append(statement)
        return [{"borrower_id": "B-0000000000001", "latest_signal_date": "2026-10-07"}]

    counts = job.wake_lead_snoozes(
        execute, {"fake": True}, catalog="mip",
        fetch_open=lambda _conn: [_open(), _open("B-0000000000002", OTHER)],
    )

    assert counts == (2, 1, 4)
    assert applied == [[SNOOZE]]
    assert len(statements) == 1


def test_nothing_open_touches_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(job, "_apply_snooze_wakes", lambda *_a: pytest.fail("no apply without open snoozes"))

    counts = job.wake_lead_snoozes(lambda _s: pytest.fail("no probe"), {}, catalog="mip", fetch_open=lambda _c: [])

    assert counts == (0, 0, 0)


class _Warehouse:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: str, params: Any = None) -> list[dict[str, Any]]:
        self.statements.append(statement)
        if "information_schema" in statement:
            return [{"column_name": column} for column in job._LIFECYCLE_SCHEMA_MIGRATIONS]
        if "evidence_events" in statement:
            return [{"borrower_id": "B-0000000000001", "latest_signal_date": "2026-10-07"}]
        return []

    def execute_one(self, statement: str) -> dict[str, Any] | None:
        raise AssertionError("no aggregate on the approval path")


@pytest.fixture
def app_path(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"applied": []}
    monkeypatch.setattr(lifecycle_sync, "_resolve_connection", lambda: {"fake": True})
    monkeypatch.setattr(lifecycle_sync, "_fetch_lakebase_rows", lambda _conn: [])
    monkeypatch.setattr(lifecycle_sync, "_fetch_open_snoozes", lambda _conn: [_open()])
    monkeypatch.setattr(job, "_apply_snooze_wakes", lambda _conn, ids: state["applied"].append(ids) or (len(ids), 0))
    return state


def test_the_app_path_wakes_after_the_merge(app_path: dict[str, Any], caplog: pytest.LogCaptureFixture) -> None:
    warehouse = _Warehouse()

    with caplog.at_level(logging.INFO):
        result = lifecycle_sync.sync_lifecycle_state_via_warehouse(
            sql_client=warehouse,  # type: ignore[arg-type]
            record_funnel_snapshot=False,
        )

    merge = next(i for i, s in enumerate(warehouse.statements) if "MERGE INTO" in s)
    probe = next(i for i, s in enumerate(warehouse.statements) if "evidence_events" in s)
    assert merge < probe
    assert app_path["applied"] == [[SNOOZE]]
    assert result == lifecycle_sync.LifecycleSyncResult(lakebase_rows=0, mirrored_rows=None, funnel_snapshot_rows=None)
    [event] = [r for r in caplog.records if getattr(r, "mip_event", None) == "lead_snooze_wake"]
    assert event.mip_extras == {"open": 1, "signal": 1, "timer": 0}  # type: ignore[attr-defined]


def test_the_app_path_wake_is_fail_soft(
    app_path: dict[str, Any], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def _down(_conn: Any) -> Any:
        raise RuntimeError("connection to 10.0.0.1 failed for lo01@summit.example")

    monkeypatch.setattr(lifecycle_sync, "_fetch_open_snoozes", _down)

    with caplog.at_level(logging.INFO):
        result = lifecycle_sync.sync_lifecycle_state_via_warehouse(
            sql_client=_Warehouse(),  # type: ignore[arg-type]
            record_funnel_snapshot=False,
        )

    assert result.lakebase_rows == 0
    [event] = [r for r in caplog.records if getattr(r, "mip_event", None) == "lead_snooze_wake_failed"]
    assert event.levelno == logging.WARNING
    assert event.mip_extras == {"exc_type": "RuntimeError"}  # type: ignore[attr-defined]
    assert "10.0.0.1" not in caplog.text and "summit.example" not in caplog.text


class _Spark:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def sql(self, statement: str) -> Any:
        self.statements.append(statement)
        rows: list[dict[str, Any]] = []
        if "information_schema" in statement and "'funnel_snapshot_daily'" in statement:
            rows = [{"column_name": "competitor_lien_borrowers"}]
        elif "information_schema" in statement:
            rows = [{"column_name": column} for column in job._LIFECYCLE_SCHEMA_MIGRATIONS]
        elif "evidence_events" in statement:
            rows = [{"borrower_id": "B-0000000000001", "latest_signal_date": date(2026, 10, 7)}]
        return _Result(rows)


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def collect(self) -> list[Any]:
        return [_Row(row) for row in self.rows]


class _Row:
    def __init__(self, row: dict[str, Any]) -> None:
        self.row = row

    def asDict(self, *, recursive: bool) -> dict[str, Any]:  # noqa: N802 -- Spark's Row API
        return dict(self.row)


def test_the_bundle_job_wakes_after_write_gold_and_prints_counts_only(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    spark = _Spark()
    monkeypatch.setattr(job, "_get_spark", lambda: spark)
    monkeypatch.setattr(job, "_resolve_connection", lambda **_: {"fake": True})
    monkeypatch.setattr(job, "_fetch_lakebase_rows", lambda _conn: [])
    monkeypatch.setattr(job, "_fetch_open_snoozes", lambda _conn: [_open()])
    monkeypatch.setattr(job, "_apply_snooze_wakes", lambda _conn, ids: (len(ids), 2))

    job.main(["--catalog", "mip"])

    merge = next(i for i, s in enumerate(spark.statements) if "MERGE INTO" in s)
    probe = next(i for i, s in enumerate(spark.statements) if "evidence_events" in s)
    assert merge < probe
    out = capsys.readouterr().out
    assert "[sync-lifecycle] snooze wake: open=1 signal=1 timer=2" in out
    assert "B-0000000000001" not in out


def test_a_bundle_job_wake_failure_keeps_the_exit_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _down(_conn: Any) -> Any:
        raise RuntimeError("password=hunter2")

    monkeypatch.setattr(job, "_get_spark", lambda: _Spark())
    monkeypatch.setattr(job, "_resolve_connection", lambda **_: {"fake": True})
    monkeypatch.setattr(job, "_fetch_lakebase_rows", lambda _conn: [])
    monkeypatch.setattr(job, "_fetch_open_snoozes", _down)

    job.main(["--catalog", "mip"])  # no SystemExit

    captured = capsys.readouterr()
    assert "[sync-lifecycle] snooze wake skipped: RuntimeError" in captured.err
    assert "hunter2" not in captured.err + captured.out
