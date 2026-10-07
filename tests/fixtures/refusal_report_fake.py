"""In-memory model of the refusal-report Lakebase statements (tests only).

Answers the exact SQL of ``backend/services/genie_refusal_report.py`` and
``backend/services/genie_refusal_report_reads.py`` by statement shape: the
report insert / replay lookup / audit link, the RUN_GENIE ledger probe, the
text insert, the live-text check, the expiry sweep, the auditor page, the
family counts and the question read; plus the ``action_audit`` INSERT that
``write_audit_event_in_transaction`` issues, so every report audit row runs
the real metadata validation. A transaction rolls every table back when its
body raises, like PostgreSQL. The real SQL is pinned against PostgreSQL by
tests/integration/test_refusal_report_texts_migration_postgres.py.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from backend.services.genie_refusal_reason import refusal_report_hash
from backend.services.lakebase import LakebaseError


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._rows)


class RefusalReportLakebase:
    """The genie_refusal_reports / _texts tables and the RUN_GENIE ledger."""

    def __init__(self) -> None:
        self.reports: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.texts: dict[str, dict[str, Any]] = {}
        self.audit_rows: list[dict[str, Any]] = []
        self.ledger: list[dict[str, Any]] = []
        self.order: list[str] = []
        self.down = False
        self.sweep_fails = False
        self.now = datetime.now(UTC)

    # -- arrangement helpers -------------------------------------------------
    def add_refusal(
        self,
        *,
        actor: str,
        question: str,
        action_type: str = "refused_prompt",
        age: timedelta = timedelta(minutes=5),
        event_type: str = "RUN_GENIE",
    ) -> None:
        """A RUN_GENIE row like genie_deterministic writes for a refused turn."""

        self.ledger.append(
            {
                "actor_email": actor,
                "event_type": event_type,
                "metadata": {
                    "question_hash": refusal_report_hash(question)[:16],
                    "action_type": action_type,
                },
                "event_at": self.now - age,
            }
        )

    def add_report(
        self,
        *,
        actor: str,
        question_hash: str,
        refusal_reason: str = "unreviewed_criterion",
        reported_at: datetime | None = None,
        text: str | None = None,
        expires_at: datetime | None = None,
        purged: bool = False,
    ) -> str:
        report_id = str(uuid4())
        self.reports[(actor, question_hash, refusal_reason)] = {
            "report_id": report_id,
            "actor_email": actor,
            "question_hash": question_hash,
            "refusal_reason": refusal_reason,
            "conversation_id": None,
            "message_id": None,
            "audit_event_id": str(uuid4()),
            "reported_at": reported_at or self.now,
        }
        if text is not None or purged:
            captured = (reported_at or self.now) - timedelta(seconds=1)
            self.texts[report_id] = {
                "question_text": None if purged else text,
                "redacted": False,
                "captured_at": captured,
                "expires_at": expires_at or captured + timedelta(days=90),
                "purged_at": self.now if purged else None,
            }
        return report_id

    def report_rows(self) -> list[dict[str, Any]]:
        return list(self.reports.values())

    # -- the LakebaseClient surface -------------------------------------------
    @contextmanager
    def transaction(self) -> Iterator[RefusalReportLakebase]:
        if self.down:
            raise LakebaseError("lakebase down")
        tables = ("reports", "texts", "audit_rows")
        snapshot = copy.deepcopy([getattr(self, name) for name in tables])
        try:
            yield self
        except BaseException:
            for name, value in zip(tables, snapshot, strict=True):
                setattr(self, name, value)
            raise

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> _Result:
        return _Result(self._run(sql, params or {}))

    def fetchone(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        if self.down:
            raise LakebaseError("lakebase down")
        rows = self._run(sql, params or {})
        return rows[0] if rows else None

    def fetchall(
        self, sql: str, params: dict[str, Any] | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        if self.down:
            raise LakebaseError("lakebase down")
        return self._run(sql, params or {})[:limit]

    # -- statement models -------------------------------------------------------
    def _run(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        if "SET question_text = NULL, purged_at = now()" in sql:
            return self._sweep(params)
        if "INSERT INTO mip_app.genie_refusal_reports" in sql:
            return self._insert_report(params)
        if "SELECT report_id, audit_event_id" in sql:
            row = self.reports.get(_key(params))
            return [dict(row)] if row else []
        if "UPDATE mip_app.genie_refusal_reports" in sql:
            return self._attach(params)
        if "FROM mip_app.action_audit" in sql:
            return self._probe(params)
        if "INSERT INTO mip_app.action_audit" in sql:
            return self._insert_audit(params)
        if "INSERT INTO mip_app.genie_refusal_report_texts" in sql:
            return self._insert_text(params)
        if "SELECT 1 AS live" in sql:
            return [{"live": 1}] if self._live(str(params["report_id"])) else []
        if "LEFT JOIN mip_app.genie_refusal_report_texts" in sql:
            return self._page(params)
        if "GROUP BY refusal_reason" in sql:
            return self._family_counts(params)
        if "JOIN mip_app.genie_refusal_reports r ON r.report_id = t.report_id" in sql:
            return self._question(params)
        raise AssertionError(f"unexpected SQL: {sql[:80]}")

    def _sweep(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        self.order.append("sweep")
        if self.sweep_fails:
            raise LakebaseError("sweep failed")
        due = sorted(
            (
                (row["expires_at"], report_id)
                for report_id, row in self.texts.items()
                if row["purged_at"] is None and row["expires_at"] <= self.now
            )
        )[: int(params["limit"])]
        for _expires, report_id in due:
            self.texts[report_id].update(question_text=None, purged_at=self.now)
        return [{"report_id": report_id} for _expires, report_id in due]

    def _insert_report(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        key = _key(params)
        if key in self.reports:
            self.order.append("duplicate")
            return []
        report_id = str(uuid4())
        self.reports[key] = {
            **params,
            "report_id": report_id,
            "audit_event_id": None,
            "reported_at": self.now,
        }
        self.order.append("report")
        return [{"report_id": report_id}]

    def _attach(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        for row in self.reports.values():
            if row["report_id"] == str(params["report_id"]):
                row["audit_event_id"] = params["audit_event_id"]
                self.order.append("link")
                return [{"report_id": row["report_id"]}]
        return []

    def _probe(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        self.order.append("probe")
        cutoff = self.now - timedelta(days=30)
        for row in self.ledger:
            if (
                row["actor_email"] == params["actor"]
                and row["event_type"] == "RUN_GENIE"
                and row["metadata"].get("question_hash") == params["hash16"]
                and row["metadata"].get("action_type") in list(params["types"])
                and row["event_at"] > cutoff
            ):
                return [{"hit": 1}]
        return []

    def _insert_audit(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        row = {
            "audit_id": str(uuid4()),
            "audit_sequence": len(self.audit_rows) + 1,
            "event_at": self.now,
            **params,
        }
        row["metadata_json"] = json.loads(params["metadata"])
        self.audit_rows.append(row)
        self.order.append("audit")
        return [row]

    def _insert_text(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        report_id = str(params["report_id"])
        if report_id in self.texts:
            self.order.append("text-conflict")
            return []
        text = str(params["question_text"])
        assert 1 <= len(text) <= 16000
        self.texts[report_id] = {
            "question_text": text,
            "redacted": bool(params["redacted"]),
            "captured_at": self.now,
            "expires_at": self.now + timedelta(days=90),
            "purged_at": None,
        }
        self.order.append("text")
        return [{"report_id": report_id}]

    def _live(self, report_id: str) -> bool:
        row = self.texts.get(report_id)
        return row is not None and row["purged_at"] is None and row["expires_at"] > self.now

    def _page(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        rows = [
            row
            for row in self.reports.values()
            if row["reported_at"] >= params["since"]
            and (params["family"] is None or row["refusal_reason"] == params["family"])
        ]
        rows.sort(key=lambda row: (row["reported_at"], row["report_id"]), reverse=True)
        if params["after_reported_at"] is not None:
            after = (params["after_reported_at"], str(params["after_report_id"]))
            rows = [row for row in rows if (row["reported_at"], row["report_id"]) < after]
        out = []
        for row in rows[: int(params["fetch_limit"])]:
            live = self._live(row["report_id"])
            out.append(
                {
                    **row,
                    "has_text": live,
                    "text_expires_at": self.texts[row["report_id"]]["expires_at"] if live else None,
                }
            )
        return out

    def _family_counts(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        counts: dict[str, int] = {}
        for row in self.reports.values():
            if row["reported_at"] >= params["since"]:
                counts[row["refusal_reason"]] = counts.get(row["refusal_reason"], 0) + 1
        return [{"refusal_reason": reason, "report_count": count} for reason, count in sorted(counts.items())]

    def _question(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        report_id = str(params["report_id"]).lower()
        if not self._live(report_id):
            return []
        report = next(row for row in self.reports.values() if row["report_id"] == report_id)
        text = self.texts[report_id]
        return [
            {
                "question_text": text["question_text"],
                "redacted": text["redacted"],
                "captured_at": text["captured_at"],
                "expires_at": text["expires_at"],
                "question_hash": report["question_hash"],
                "refusal_reason": report["refusal_reason"],
            }
        ]


def _key(params: dict[str, Any]) -> tuple[str, str, str]:
    return (str(params["actor_email"]), str(params["question_hash"]), str(params["refusal_reason"]))
