"""Live parity for mip.gold.segment_combination_rollup (signal stack, wow-stage-5).

The rollup claims one row per exact set of the six core segment codes a
borrower carries, so every inclusive count is an exact sum of rows. Against
real Unity Catalog:

1. Coverage -- the rows' addressable counts sum to the borrower_360 rows that
   carry at least one core code (each borrower lands in exactly one row).
2. Per code -- for each core code, the rows containing it sum to
   ``COUNT(*) WHERE array_contains(segment_codes, code)``.
3. Inclusive sets -- for all 20 core triples and every multi-code row, the sum
   over the rows containing the set equals ``COUNT(*)`` under the canonical
   ``compose_segment_predicate(codes, mode='all')``, the predicate the Lead
   Queue's ``segment_mode=all`` link applies.
4. Shape -- at most 63 unique keys, and ``signal_count`` equals the array size.
5. Live contactable -- the endpoint's statement reports, per key, exactly
   ``COUNT(*)`` of borrower_360 rows with that key under the eligibility
   predicate.
6. Anchor -- ``refreshed_at`` equals the latest ``mip.ref.refresh_run_state``
   anchor.

Gated exactly like the sibling live tests (credentials via ``_creds``);
read-only bounded SELECTs.
"""

from __future__ import annotations

import itertools
import time

import pytest

from backend.schemas.segment_combinations import CORE_SEGMENT_CODES
from backend.services.databricks_sql import DatabricksSqlClient, DatabricksSqlError
from backend.services.databricks_sql_helpers import qualify
from backend.services.eligibility import eligible_sql_predicate
from backend.services.repositories.databricks_segment_combinations import (
    COMBINATION_KEY_SQL,
    SEGMENT_COMBINATIONS_SQL,
)
from backend.services.segment_predicates import compose_segment_predicate
from tests.integration.test_segment_count_parity import _creds

pytestmark = pytest.mark.integration

_ROLLUP = qualify("gold", "segment_combination_rollup")
_B360 = qualify("gold", "borrower_360")
_ANCHOR = qualify("ref", "refresh_run_state")
_CORE_LIST = ", ".join(f"'{code}'" for code in CORE_SEGMENT_CODES)


@pytest.fixture(scope="module")
def sql_client() -> DatabricksSqlClient:
    creds = _creds()
    if creds is None:
        pytest.skip(
            "segment-combination parity SKIPPED: set DATABRICKS_HOST + DATABRICKS_TOKEN + "
            "DATABRICKS_WAREHOUSE_ID, or configure the Databricks CLI DEFAULT profile, to enable."
        )
    host, token, warehouse_id = creds
    client = DatabricksSqlClient(host, token, warehouse_id, timeout_s=50)
    for attempt in range(3):
        try:
            client.execute("SELECT 1")
            break
        except DatabricksSqlError:
            if attempt == 2:
                raise
            time.sleep(10)
    return client


@pytest.fixture(scope="module")
def rows(sql_client: DatabricksSqlClient) -> dict[str, dict[str, int]]:
    result = sql_client.execute(
        "SELECT combination_key, CAST(size(segment_codes) AS INT) AS codes, "
        f"CAST(signal_count AS INT) AS signal_count, CAST(addressable_borrowers AS BIGINT) AS n FROM {_ROLLUP}"
    )
    if not result:
        pytest.fail(f"{_ROLLUP} is empty -- run mip_refresh_scores (ctas_segment_combination_rollup) first")
    return {
        str(row["combination_key"]): {
            "codes": int(row["codes"]),
            "signal_count": int(row["signal_count"]),
            "n": int(row["n"]),
        }
        for row in result
    }


def _count(client: DatabricksSqlClient, where: str, params: dict[str, object] | None = None) -> int:
    result = client.execute(f"SELECT COUNT(*) AS n FROM {_B360} WHERE {where}", params)
    return int(result[0]["n"])


def _inclusive(rows: dict[str, dict[str, int]], codes: tuple[str, ...]) -> int:
    return sum(row["n"] for key, row in rows.items() if set(codes) <= set(key.split("+")))


def test_rows_cover_every_borrower_with_a_core_code_once(
    sql_client: DatabricksSqlClient, rows: dict[str, dict[str, int]]
) -> None:
    direct = _count(sql_client, f"arrays_overlap(segment_codes, ARRAY({_CORE_LIST}))")
    assert sum(row["n"] for row in rows.values()) == direct


@pytest.mark.parametrize("code", CORE_SEGMENT_CODES)
def test_rows_containing_a_code_sum_to_its_membership(
    sql_client: DatabricksSqlClient, rows: dict[str, dict[str, int]], code: str
) -> None:
    assert _inclusive(rows, (code,)) == _count(sql_client, f"array_contains(segment_codes, '{code}')")


def test_inclusive_sets_match_the_lead_queue_all_predicate(
    sql_client: DatabricksSqlClient, rows: dict[str, dict[str, int]]
) -> None:
    triples = list(itertools.combinations(CORE_SEGMENT_CODES, 3))
    assert len(triples) == 20
    multi = [tuple(key.split("+")) for key, row in rows.items() if row["signal_count"] >= 2]
    for codes in [*triples, *multi]:
        clause, params = compose_segment_predicate(list(codes), mode="all")
        assert _inclusive(rows, codes) == _count(sql_client, clause, params), codes


def test_shape(rows: dict[str, dict[str, int]]) -> None:
    assert 0 < len(rows) <= 63
    for key, row in rows.items():
        codes = key.split("+")
        assert codes == sorted(set(codes)) and set(codes) <= set(CORE_SEGMENT_CODES), key
        assert row["signal_count"] == row["codes"] == len(codes), key


def test_live_contactable_matches_the_eligibility_predicate(sql_client: DatabricksSqlClient) -> None:
    live = {str(row["combination_key"]): int(row["contactable"]) for row in sql_client.execute(SEGMENT_COMBINATIONS_SQL)}
    direct = {
        str(row["k"]): int(row["n"])
        for row in sql_client.execute(
            f"SELECT {COMBINATION_KEY_SQL} AS k, COUNT(*) AS n FROM {_B360} "
            f"WHERE {eligible_sql_predicate()} GROUP BY 1"
        )
    }
    for key, contactable in live.items():
        assert contactable == direct.get(key, 0), key


def test_refreshed_at_is_the_latest_anchor(sql_client: DatabricksSqlClient) -> None:
    anchor = sql_client.execute(f"SELECT refresh_at FROM {_ANCHOR} ORDER BY captured_at DESC LIMIT 1")
    stamps = sql_client.execute(f"SELECT DISTINCT refreshed_at FROM {_ROLLUP}")
    assert [row["refreshed_at"] for row in stamps] == [anchor[0]["refresh_at"]]
