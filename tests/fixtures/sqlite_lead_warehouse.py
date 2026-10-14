"""An in-memory SQLite stand-in for the gold Lead Queue tables. Tests only.

The Lead Queue's ordered reads are EXECUTED against it, not string-matched:
``mip.gold.`` is stripped and the two Databricks-only spellings the default
contactability predicate uses (``CURRENT_TIMESTAMP()`` and ``INTERVAL '30'
DAYS``) are rewritten to a fixed instant. Unary minus, ``NULLS LAST``,
``CONCAT`` and named ``:params`` run as the warehouse runs them, so the
ORDER BY and keyset predicate under test are the statements the App sends.

Aggregate statements (the count, the identity proof) use Databricks
functions SQLite lacks; ``execute_one`` answers the configured count.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from backend.services.repositories.databricks_shared import (
    _LEAD_POPULATION_SELECT_FROM_B360,
    _LEAD_POPULATION_SELECT_FROM_LP,
)

_NOW = "'2026-09-30 00:00:00'"


def _columns(projection: str, alias: str) -> set[str]:
    return set(re.findall(rf"\b{alias}\.(\w+)", projection))


def lead_id(index: int) -> str:
    """A masked ``B-[0-9A-Z]{13}`` id: letters, never a phone-shaped digit run."""

    return f"B-L{index:05d}QUEUEXX"


def gold_lead_row(index: int, *, refreshed_at: str | None = "2026-09-29T06:00:00Z") -> dict[str, Any]:
    """One contactable gold row with heavy ties and NULL equity / rate runs."""

    score = 99 - (index % 50)
    return {
        "borrower_id": lead_id(index),
        "clip": str(1_000_000 + index),
        "owner_name_hash": f"{index:08x}",
        "display_name": f"Owner {index:08x}",
        "state": "IL",
        "city": "Chicago",
        "zip": "60617",
        "opportunity_score": score,
        # DENSE_RANK by score: ties share a rank, so borrower_id breaks them.
        "rank_overall": 100 - score,
        # NULL equity from row 497 on: a 500-row boundary falls INSIDE the run.
        "equity_estimate": None if index >= 497 else (index * 7919) % 400,
        # NULL rate on every other row past 760 (906 non-null for 1,050 rows).
        "rate_spread_bps": None if index > 760 and index % 2 == 0 else (index * 31) % 120 - 20,
        "confidence": index % 10,
        "marketing_eligible": 1,
        "consent_status": "opt_in",
        "approval_status": "pending",
        "refreshed_at": refreshed_at,
    }


class SqliteLeadWarehouse:
    """Answers the repository's statements from an in-memory copy of gold."""

    def __init__(self, rows: list[dict[str, Any]], *, count: int | None = None) -> None:
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.statements: list[str] = []
        self.count = len(rows) if count is None else count
        lp_cols = _columns(_LEAD_POPULATION_SELECT_FROM_LP, "lp") | {"rank_overall"}
        b_cols = _columns(_LEAD_POPULATION_SELECT_FROM_B360, "b") | {
            "county_fips_5",
            "owner_name_hash",
            "marketing_eligible",
            "consent_status",
            "suppression_reason",
            "dnc",
            "eligible_recontact_at",
            "last_touch_at",
        }
        ls_cols = {"borrower_id", "approval_status", "outreach_status", "approved_at", "outreach_at"}
        for table, cols in (
            ("lead_population", lp_cols),
            ("borrower_360", b_cols),
            ("borrower_lifecycle_state", ls_cols),
        ):
            ordered = sorted(cols)
            self.db.execute(f"CREATE TABLE {table} ({', '.join(ordered)})")
            if table == "borrower_lifecycle_state":
                continue
            self.db.executemany(
                f"INSERT INTO {table} ({', '.join(ordered)}) VALUES ({', '.join('?' for _ in ordered)})",
                [[row.get(col) for col in ordered] for row in rows],
            )

    def set_refreshed_at(self, value: str) -> None:
        for table in ("lead_population", "borrower_360"):
            self.db.execute(f"UPDATE {table} SET refreshed_at = ?", [value])

    def execute(self, sql: str, params: dict[str, object] | None = None) -> list[dict[str, Any]]:
        self.statements.append(sql)
        runnable = (
            sql.replace("mip.gold.", "")
            .replace("CURRENT_TIMESTAMP() - INTERVAL '30' DAYS", _NOW)
            .replace("CURRENT_TIMESTAMP()", _NOW)
        )
        cursor = self.db.execute(runnable, dict(params or {}))
        return [dict(row) for row in cursor.fetchall()]

    def execute_one(self, sql: str, params: dict[str, object] | None = None) -> dict[str, Any]:
        _ = params
        self.statements.append(sql)
        return {"n": self.count, "ranked_n": self.count}

    def list_statements(self) -> list[str]:
        return [sql for sql in self.statements if "LIMIT" in sql]
