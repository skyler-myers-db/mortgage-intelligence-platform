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
# Columns to compare. This mirrors every borrower_360 column projected by
# sql/transformations/gold_borrower_dossier.sql before the dossier-only evidence
# arrays/refreshed_at columns, so CTAS drift in live MLS/propensity fields is
# caught alongside the legacy borrower scalar fields.
# ---------------------------------------------------------------------------

_PARITY_COLUMNS: tuple[str, ...] = (
    "clip",
    "borrower_id",
    "display_name",
    "city",
    "state",
    "zip",
    "situs_cbsa_code",
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
    "current_lien_balance_low",
    "current_lien_balance_high",
    "current_rate",
    "ltv",
    "related_property_count",
    "is_owner_occupied",
    "is_absentee",
    "is_corporate_owner",
    "has_permit",
    "listed_for_sale",
    "listing_status_category",
    "listing_status_description",
    "listing_date",
    "listing_status_date",
    "listing_price",
    "listing_days_on_market",
    "listing_service",
    "heloc_propensity_score",
    "heloc_propensity_run_date",
    "has_heloc_propensity_trigger",
    "refi_propensity_score",
    "refi_propensity_run_date",
    "has_refi_propensity_trigger",
    "is_investor",
    "is_current_customer",
    "is_former_customer",
    "is_competitor_lien",
    "has_first_party_relationship",
    "first_party_relationship_depth",
    "first_party_recent_interactions",
    "first_party_recent_application",
    "first_party_synthetic_demo",
    "marketing_eligible",
    "consent_status",
    "suppression_reason",
    "last_touch_at",
    "eligible_recontact_at",
    "current_lender_ref",
    "second_pos_amount",
    "first_pos_loan_type",
    "owner_name_hash",
    "min_spread_bps_applied",
    "min_equity_pct_applied",
    "heloc_equity_min_applied",
    "cashout_equity_min_applied",
    "retention_min_spread_applied",
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
def listed_borrower_ids(warehouse: tuple[str, str, str]) -> list[str]:
    """Pick live MLS/listing borrowers when the source-readiness contract says
    MLS Listings is live.

    This prevents the parity suite from passing on an all-non-listed random
    sample after MLS/listing evidence changed the top-3 timeline contract.
    """
    host, token, wh = warehouse
    readiness = _run_sql(
        host,
        token,
        wh,
        "SELECT LOWER(status) FROM mip.gold.source_readiness "
        "WHERE source_name = 'MLS Listings' LIMIT 1",
    )
    mls_live = bool(readiness and readiness[0] and readiness[0][0] == "live")
    if not mls_live:
        return []

    rows = _run_sql(
        host,
        token,
        wh,
        "SELECT borrower_id FROM mip.gold.borrower_dossier "
        "WHERE listed_for_sale = TRUE "
        "  AND EXISTS(evidence_events, ev -> ev.signal_type = 'listing') "
        "ORDER BY opportunity_score DESC NULLS LAST, borrower_id "
        "LIMIT 3",
    )
    ids = [r[0] for r in rows if r and r[0]]
    if not ids:
        pytest.fail(
            "MLS Listings is source-ready/live, but borrower_dossier has no "
            "listed_for_sale borrowers with listing evidence."
        )
    return ids


@pytest.fixture(scope="module")
def sample_borrower_ids(
    warehouse: tuple[str, str, str],
    listed_borrower_ids: list[str],
) -> list[str]:
    """Pick random borrower_ids plus explicit listed borrowers from the live
    dossier.

    Using `TABLESAMPLE (5 ROWS)` keeps the selection deterministic only
    per-run -- different runs may pick different IDs, which is exactly what we
    want for a parity sweep. The listed slice is deterministic by score so the
    live MLS path is always exercised when that source is live.
    """
    host, token, wh = warehouse
    rows = _run_sql(
        host,
        token,
        wh,
        "SELECT borrower_id FROM mip.gold.borrower_dossier "
        "TABLESAMPLE (5 ROWS)",
    )
    ids: list[str] = []
    for row in [*rows, *[[bid] for bid in listed_borrower_ids]]:
        if row and row[0] and row[0] not in ids:
            ids.append(row[0])
    if not ids:
        pytest.skip(
            "mip.gold.borrower_dossier is empty -- run `databricks bundle "
            "run mip_refresh_scores -t dev` before this test."
        )
    return ids


def test_live_mls_listing_sample_is_present(
    warehouse: tuple[str, str, str],
    listed_borrower_ids: list[str],
) -> None:
    """When source readiness marks MLS Listings live, the suite must exercise
    borrowers with listing evidence instead of relying on random TABLESAMPLE
    coverage."""
    if not listed_borrower_ids:
        pytest.skip("MLS Listings is not live in this workspace.")

    host, token, wh = warehouse
    for bid in listed_borrower_ids:
        rows = _run_sql(
            host,
            token,
            wh,
            "SELECT listed_for_sale, "
            "       EXISTS(evidence_events, ev -> ev.signal_type = 'listing') "
            "FROM mip.gold.borrower_dossier "
            f"WHERE borrower_id = '{_sanitize(bid)}' LIMIT 1",
        )
        assert rows, f"dossier missing listed borrower_id={bid!r}"
        assert rows[0] == [True, True] or rows[0] == ["true", "true"], (
            f"borrower_id={bid!r} is not a live listed/evidence-backed sample: "
            f"{rows[0]!r}"
        )


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

        # Pull authoritative top-3 straight from evidence_events, serialized
        # with the same field order as the dossier evidence ARRAY<STRUCT>.
        direct = _run_sql(
            host,
            token,
            wh,
            "WITH ranked AS ("
            "  SELECT "
            "    STRUCT("
            "      evidence_id, source_product, source_table, signal_type, "
            "      signal_value, display_text, confidence, `timestamp`, signal_rank"
            "    ) AS ev, "
            "    ROW_NUMBER() OVER (ORDER BY signal_rank ASC, evidence_id ASC) AS rn "
            "  FROM mip.gold.evidence_events "
            f"  WHERE clip = '{_sanitize(str(clip))}' "
            "    AND signal_type <> 'permit'"
            ") "
            "SELECT to_json(array_sort(collect_list(ev), (a, b) -> CASE "
            "  WHEN a.signal_rank < b.signal_rank THEN -1 "
            "  WHEN a.signal_rank > b.signal_rank THEN 1 "
            "  WHEN a.evidence_id < b.evidence_id THEN -1 "
            "  WHEN a.evidence_id > b.evidence_id THEN 1 "
            "  ELSE 0 END)) "
            "FROM ranked WHERE rn <= 3",
        )

        # Pull the dossier's top-3 timeline and the head of the full evidence
        # array, serialized as JSON so the comparison covers every public
        # EvidenceEvent field, not only ids/ranks.
        dossier = _run_sql(
            host,
            token,
            wh,
            "SELECT to_json(trigger_timeline), to_json(slice(evidence_events, 1, 3)) "
            "FROM mip.gold.borrower_dossier "
            f"WHERE borrower_id = '{_sanitize(bid)}' LIMIT 1",
        )
        assert dossier, f"dossier missing borrower_id={bid!r}"
        direct_events = _json_array(direct[0][0] if direct and direct[0] else None)
        dossier_timeline = _json_array(dossier[0][0])
        dossier_full_head = _json_array(dossier[0][1])

        assert dossier_timeline == direct_events, (
            f"borrower_id={bid!r} trigger_timeline drift: "
            f"dossier={dossier_timeline!r} direct={direct_events!r}"
        )
        assert dossier_full_head == direct_events, (
            f"borrower_id={bid!r} evidence_events[:3] drift: "
            f"dossier={dossier_full_head!r} direct={direct_events!r}"
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


def _json_array(raw: Any) -> list[Any]:
    if raw in (None, ""):
        return []
    if isinstance(raw, list):
        return raw
    parsed = json.loads(str(raw))
    assert isinstance(parsed, list)
    return parsed
