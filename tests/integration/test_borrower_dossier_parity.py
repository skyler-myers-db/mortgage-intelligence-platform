"""Parity check: mip.gold.borrower_dossier == borrower_360 + top-3 evidence.

Slice13-accuracy perf: `/api/borrowers/{id}` now reads from
`mip.gold.borrower_dossier`, a pre-joined superset of `borrower_360`
plus the top-20 evidence events per CLIP. This integration test picks
5 random borrower_ids off the live warehouse, reads their dossier row,
and asserts that EVERY column matches what the old path would have
returned from `borrower_360` + `evidence_events`.

Gated on `DATABRICKS_HOST` / `DATABRICKS_TOKEN` / `DATABRICKS_WAREHOUSE_ID`
(identical gate to `test_sql_python_parity.py`). When creds are missing
the test SKIPs with a clear message; CI without workspace creds stays
green.

Why a parity test at this layer:
    * The CTAS re-shapes gold data into a pre-joined form. Any drift
      between dossier and (borrower_360 + evidence_events) is a perf
      optimisation correctness bug — the /api response would regress
      silently.
    * `backend/services/repositories/databricks_repo.py::DatabricksBorrowerRepository.get()`
      is the ONLY Python touching the dossier. A failure here points
      straight at the CTAS or the columns-in-sync invariant between
      `_BORROWER_DOSSIER_COLUMNS`, the transformation SELECT, and the
      DDL §10 block.
    * Evidence-array ordering matters: the top-3 slice in the dossier
      MUST equal the dossier's full evidence_events[:3] so the trigger
      timeline renders deterministically.

Non-negotiables:
    * Stdlib-only HTTP (urllib + json), matching the sibling
      `test_sql_python_parity.py` so no extra wheel is required.
    * Read-only: zero mutations. Every statement is a bounded SELECT.
    * Never raises on a missing-creds SKIP path.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Credentials gate + Statement Execution API wrapper
# ---------------------------------------------------------------------------


def _creds() -> tuple[str, str, str] | None:
    host = os.environ.get("DATABRICKS_HOST") or os.environ.get(
        "DATABRICKS_SERVER_HOSTNAME"
    )
    token = os.environ.get("DATABRICKS_TOKEN")
    warehouse_id = os.environ.get("DATABRICKS_WAREHOUSE_ID")
    if not host or not token or not warehouse_id:
        return None
    if not host.startswith("http"):
        host = "https://" + host
    host = host.rstrip("/")
    return host, token, warehouse_id


def _run_sql(
    host: str,
    token: str,
    warehouse_id: str,
    statement: str,
) -> list[list[Any]]:
    """Execute a SELECT and return the data_array (list of rows, each a list
    of column values). Coercion into Python types is caller-side because
    JSON_ARRAY dispositions emit everything as strings."""
    url = f"{host}/api/2.0/sql/statements/"
    payload = json.dumps(
        {
            "statement": statement,
            "warehouse_id": warehouse_id,
            "wait_timeout": "30s",
            "on_wait_timeout": "CANCEL",
            "disposition": "INLINE",
            "format": "JSON_ARRAY",
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=35) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:  # pragma: no cover -- network issue
        pytest.skip(f"warehouse unreachable: {exc}")
    status = body.get("status", {}).get("state")
    if status != "SUCCEEDED":
        err = body.get("status", {}).get("error", {}).get("message", "unknown")
        pytest.fail(f"warehouse statement failed: state={status!r} err={err!r}")
    return body.get("result", {}).get("data_array") or []


# ---------------------------------------------------------------------------
# Columns to compare. Mirror of
# backend.services.repositories.databricks_repo._BORROWER_360_COLUMNS so a
# drift in either direction is caught.
# ---------------------------------------------------------------------------

_PARITY_COLUMNS: tuple[str, ...] = (
    "clip",
    "borrower_id",
    "owner_name_hash",
    "city",
    "state",
    "zip",
    # segment_codes is an ARRAY -- compared as a JSON-encoded string.
    "segment_codes",
    "equity_estimate",
    "equity_pct",
    "rate_spread_bps",
    "market_rate_fraction",
    "opportunity_score",
    "confidence",
    "recommended_offer_code",
    "recommended_offer",
    "why_now",
    "evidence_ids",
    "approval_status",
    "owner_link_id",
    "subject_property",
    "avm_value",
    "current_lien_balance",
    "current_rate",
    "ltv",
    "related_property_count",
    "is_owner_occupied",
    "is_investor",
    "is_current_customer",
    "is_former_customer",
    "is_competitor_lien",
    "has_permit",
    "listed_for_sale",
    "second_pos_amount",
    "min_spread_bps_applied",
    "min_equity_pct_applied",
    "in_the_money",
    "trigger_timeline_json",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def warehouse() -> tuple[str, str, str]:
    creds = _creds()
    if creds is None:
        pytest.skip(
            "borrower_dossier parity test SKIPPED: set DATABRICKS_HOST (or "
            "DATABRICKS_SERVER_HOSTNAME), DATABRICKS_TOKEN, and "
            "DATABRICKS_WAREHOUSE_ID to enable."
        )
    return creds


@pytest.fixture(scope="module")
def sample_borrower_ids(warehouse: tuple[str, str, str]) -> list[str]:
    """Pick 5 random borrower_ids from the live dossier.

    Using `TABLESAMPLE (5 ROWS)` keeps the selection deterministic only
    per-run -- different runs may pick different IDs, which is exactly
    what we want for a parity sweep.
    """
    host, token, wh = warehouse
    rows = _run_sql(
        host,
        token,
        wh,
        "SELECT borrower_id FROM mip.gold.borrower_dossier "
        "TABLESAMPLE (5 ROWS)",
    )
    ids = [r[0] for r in rows if r and r[0]]
    if not ids:
        pytest.skip(
            "mip.gold.borrower_dossier is empty -- run `databricks bundle "
            "run mip_refresh_scores -t dev` before this test."
        )
    return ids


# ---------------------------------------------------------------------------
# Parity: every scalar column matches borrower_360 1:1.
# ---------------------------------------------------------------------------


def test_dossier_columns_match_borrower_360(
    warehouse: tuple[str, str, str],
    sample_borrower_ids: list[str],
) -> None:
    """For each sample borrower_id, every scalar column on
    mip.gold.borrower_dossier must equal the same column on
    mip.gold.borrower_360. A mismatch is either a CTAS re-join drift or
    a column-list sync bug between the dossier and borrower_360 CTAS.
    """
    host, token, wh = warehouse
    col_list = ", ".join(_PARITY_COLUMNS)
    for bid in sample_borrower_ids:
        # Pull both rows with a CROSS JOIN so we get a single 2-row
        # result and column alignment is obvious in the assertion.
        dossier_rows = _run_sql(
            host,
            token,
            wh,
            f"SELECT {col_list} FROM mip.gold.borrower_dossier "
            f"WHERE borrower_id = '{_sanitize(bid)}' LIMIT 1",
        )
        b360_rows = _run_sql(
            host,
            token,
            wh,
            f"SELECT {col_list} FROM mip.gold.borrower_360 "
            f"WHERE borrower_id = '{_sanitize(bid)}' LIMIT 1",
        )
        assert dossier_rows, f"dossier missing borrower_id={bid!r}"
        assert b360_rows, f"borrower_360 missing borrower_id={bid!r}"
        for i, col in enumerate(_PARITY_COLUMNS):
            d = dossier_rows[0][i]
            b = b360_rows[0][i]
            assert d == b, (
                f"borrower_id={bid!r} column={col!r}: "
                f"dossier={d!r} vs borrower_360={b!r}"
            )


# ---------------------------------------------------------------------------
# Parity: evidence array head matches the top-3 from evidence_events ORDER BY
# signal_rank, evidence_id — byte-for-byte.
# ---------------------------------------------------------------------------


def test_dossier_top3_matches_evidence_events_top3(
    warehouse: tuple[str, str, str],
    sample_borrower_ids: list[str],
) -> None:
    """The dossier's trigger_timeline (top-3) AND the head of
    evidence_events[:3] must equal gold.evidence_events for the same
    CLIP ordered by (signal_rank ASC, evidence_id ASC), filtered to
    live signal types (excluding only blocked permit rows per data-contract
    §9). MLS listing is now a live signal and must be part of the parity
    comparison."""
    host, token, wh = warehouse
    for bid in sample_borrower_ids:
        clip_rows = _run_sql(
            host,
            token,
            wh,
            "SELECT clip FROM mip.gold.borrower_dossier "
            f"WHERE borrower_id = '{_sanitize(bid)}' LIMIT 1",
        )
        assert clip_rows, f"dossier missing borrower_id={bid!r}"
        clip = clip_rows[0][0]

        # Pull authoritative top-3 straight from evidence_events.
        direct = _run_sql(
            host,
            token,
            wh,
            "SELECT evidence_id, signal_type, signal_rank "
            "FROM mip.gold.evidence_events "
            f"WHERE clip = '{_sanitize(str(clip))}' "
            "AND signal_type <> 'permit' "
            "ORDER BY signal_rank ASC, evidence_id ASC "
            "LIMIT 3",
        )

        # Pull the dossier's top-3 timeline.
        dossier = _run_sql(
            host,
            token,
            wh,
            "SELECT "
            "  get(trigger_timeline, 0).evidence_id, get(trigger_timeline, 0).signal_rank, "
            "  get(trigger_timeline, 1).evidence_id, get(trigger_timeline, 1).signal_rank, "
            "  get(trigger_timeline, 2).evidence_id, get(trigger_timeline, 2).signal_rank, "
            "  get(evidence_events, 0).evidence_id, get(evidence_events, 1).evidence_id, "
            "  get(evidence_events, 2).evidence_id "
            "FROM mip.gold.borrower_dossier "
            f"WHERE borrower_id = '{_sanitize(bid)}' LIMIT 1",
        )
        assert dossier, f"dossier missing borrower_id={bid!r}"
        r = dossier[0]
        # Build dossier-side lists, trimming to the length of `direct`
        # so CLIPs with fewer than 3 live signals don't spuriously fail.
        n = len(direct)
        dossier_timeline = [
            (evidence_id, int(signal_rank))
            for evidence_id, signal_rank in [(r[0], r[1]), (r[2], r[3]), (r[4], r[5])][:n]
        ]
        dossier_full_head = [r[6], r[7], r[8]][:n]
        direct_pairs = [(row[0], int(row[2])) for row in direct]
        direct_ids = [row[0] for row in direct]

        assert dossier_timeline == direct_pairs, (
            f"borrower_id={bid!r} trigger_timeline drift: "
            f"dossier={dossier_timeline} direct={direct_pairs}"
        )
        assert dossier_full_head == direct_ids, (
            f"borrower_id={bid!r} evidence_events[:3] drift: "
            f"dossier={dossier_full_head} direct={direct_ids}"
        )


# ---------------------------------------------------------------------------
# Parity: evidence array cap — confirms the 20-row ceiling holds.
# ---------------------------------------------------------------------------


def test_dossier_evidence_cap_20(
    warehouse: tuple[str, str, str],
    sample_borrower_ids: list[str],
) -> None:
    """`sql/transformations/gold_borrower_dossier.sql` caps
    evidence_events at 20 rows per CLIP. No dossier row may exceed
    that cap; if it does, we either dropped the WHERE rn <= 20 filter
    or the ROW_NUMBER() window function is off.
    """
    host, token, wh = warehouse
    for bid in sample_borrower_ids:
        rows = _run_sql(
            host,
            token,
            wh,
            "SELECT SIZE(evidence_events) FROM mip.gold.borrower_dossier "
            f"WHERE borrower_id = '{_sanitize(bid)}' LIMIT 1",
        )
        assert rows, f"dossier missing borrower_id={bid!r}"
        size = int(rows[0][0] or 0)
        assert 0 <= size <= 20, (
            f"borrower_id={bid!r}: evidence_events cardinality {size} "
            f"outside [0, 20] cap."
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sanitize(v: str) -> str:
    """Belt-and-suspenders against SQL injection.

    Sample IDs come from the warehouse itself, not user input, but
    we still allow-list the expected shape (alphanumerics + hyphen +
    underscore) so a future fixture source can't accidentally ship a
    SQL-breaking character into a literal.
    """
    return "".join(c for c in str(v) if c.isalnum() or c in "-_")
