"""The hourly purge of expired consented refusal texts (D-audit-reads-d).

A deployment with no report or auditor traffic must still forget a question
90 days after capture: the App lifespan runs ``refusal_text_purge_loop``,
which sweeps now and then hourly, drains a backlog in bounded batches,
swallows every failure with one structured event (counts only) and cancels
cleanly at shutdown.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from backend.services import genie_refusal_text_retention as retention
from backend.services.genie_refusal_reason import refusal_report_hash
from backend.services.genie_refusal_report import REFUSAL_TEXT_SWEEP_LIMIT
from tests.fixtures.refusal_report_fake import RefusalReportLakebase

ACTOR = "lo@example.com"


def _expired(fake: RefusalReportLakebase, count: int) -> list[str]:
    return [
        fake.add_report(
            actor=ACTOR,
            question_hash=refusal_report_hash(f"zyrplax question {index}"),
            reported_at=fake.now - timedelta(days=91),
            text=f"zyrplax question {index}",
            expires_at=fake.now - timedelta(minutes=index + 1),
        )
        for index in range(count)
    ]


def test_a_pass_purges_an_expired_question_with_no_traffic() -> None:
    fake = RefusalReportLakebase()
    [expired] = _expired(fake, 1)
    live = fake.add_report(actor=ACTOR, question_hash=refusal_report_hash("live"), text="live")

    purged = asyncio.run(retention.refusal_text_purge_once(client_factory=lambda: fake))

    assert purged == 1
    assert fake.texts[expired]["question_text"] is None
    assert fake.texts[expired]["purged_at"] is not None
    assert fake.texts[live]["question_text"] == "live"
    # The report row and its metadata stay; only the question goes.
    assert len(fake.reports) == 2


def test_a_backlog_drains_in_bounded_batches() -> None:
    fake = RefusalReportLakebase()
    _expired(fake, REFUSAL_TEXT_SWEEP_LIMIT * 2 + 5)

    purged = asyncio.run(retention.refusal_text_purge_once(client_factory=lambda: fake))

    assert purged == REFUSAL_TEXT_SWEEP_LIMIT * 2 + 5
    assert fake.order.count("sweep") == 3
    assert all(row["question_text"] is None for row in fake.texts.values())


def test_a_failing_pass_is_swallowed_and_logged_with_counts_only(caplog: pytest.LogCaptureFixture) -> None:
    fake = RefusalReportLakebase()
    _expired(fake, 1)
    fake.sweep_fails = True

    with caplog.at_level(logging.DEBUG, logger="backend"):
        purged = asyncio.run(retention.refusal_text_purge_once(client_factory=lambda: fake))

    assert purged == 0
    [failed] = [r for r in caplog.records if getattr(r, "mip_event", None) == "refusal_text_purge_failed"]
    assert failed.levelno == logging.WARNING
    assert getattr(failed, "mip_extras", {}) == {"error_type": "LakebaseError"}
    assert "zyrplax" not in caplog.text


def test_an_unreachable_client_is_swallowed_too() -> None:
    def _boom() -> Any:
        raise RuntimeError("no lakebase")

    assert asyncio.run(retention.refusal_text_purge_once(client_factory=_boom)) == 0


def test_the_loop_purges_at_once_then_sleeps_and_cancels_cleanly() -> None:
    fake = RefusalReportLakebase()
    _expired(fake, 1)
    passes: list[int] = []

    def _sweep(conn: Any) -> int:
        passes.append(1)
        return 0

    async def _run() -> asyncio.Task[None]:
        task = asyncio.create_task(
            retention.refusal_text_purge_loop(3600, sweep=_sweep, client_factory=lambda: fake)
        )
        for _ in range(200):
            if passes:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return task

    task = asyncio.run(_run())
    assert passes == [1]
    assert task.cancelled()


def test_the_lifespan_starts_the_loop_only_outside_pytest_and_cancels_it() -> None:
    main = Path(__file__).resolve().parents[2] / "backend" / "main.py"
    source = main.read_text(encoding="utf-8")
    lifespan = source[source.index("async def _lifespan") : source.index("app = FastAPI(")]
    started = lifespan.index("purge_task = asyncio.create_task(refusal_text_purge_loop())")
    assert lifespan.index("if not _running_under_pytest():") < started < lifespan.index("try:\n        yield")
    finally_block = lifespan[lifespan.index("    finally:") :]
    assert "purge_task.cancel()" in finally_block
    assert "await purge_task" in finally_block
