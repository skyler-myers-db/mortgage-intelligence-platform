"""S8: live segment-intersection parity — refi ∩ investor.

Acceptance: the intersected count the app computes for the Refi Propensity
∩ Investor selection must equal a DIRECTLY-executed SQL predicate
composition for the same segments, on live Unity Catalog.

Two layers are pinned, both against the real warehouse:

1. Repository layer — ``DatabricksLeadRepository.count(segment_codes=
   ['refi_propensity', 'investor'], segment_mode='all')`` (the number the
   Lead Queue reports via ``X-Total-Matching``) vs a hand-written
   ``array_contains(...) AND array_contains(...)`` count over
   ``mip.gold.lead_population``. Equality also proves the repository's
   lifecycle LEFT JOIN never duplicates rows.
2. Composer layer — the canonical ``compose_segment_predicate`` clause
   executed with bind parameters over ``mip.gold.borrower_360`` (the
   population the Segment Intelligence cards count) vs the same
   hand-written literal intersection.

Plus the set-algebra sanity that makes an accidental OR/AND swap
unmissable: |A ∩ B| <= min(|A|, |B|) and |A ∩ B| <= |A ∪ B|.

Gated exactly like the sibling live tests: skips without Databricks
credentials (env vars or CLI OAuth via ``_creds``).
"""

from __future__ import annotations

import time

import pytest

from backend.services.databricks_sql import DatabricksSqlClient, DatabricksSqlError
from backend.services.repositories.databricks_leads import DatabricksLeadRepository
from backend.services.segment_predicates import compose_segment_predicate
from tests.integration.test_segment_count_parity import _creds

SEGMENT_A = "refi_propensity"
SEGMENT_B = "investor"

_DIRECT_INTERSECTION_LEAD_POPULATION = (
    "SELECT COUNT(*) AS n FROM mip.gold.lead_population "
    f"WHERE array_contains(segment_codes, '{SEGMENT_A}') "
    f"AND array_contains(segment_codes, '{SEGMENT_B}')"
)

_DIRECT_INTERSECTION_BORROWER_360 = (
    "SELECT COUNT(*) AS n FROM mip.gold.borrower_360 "
    f"WHERE array_contains(segment_codes, '{SEGMENT_A}') "
    f"AND array_contains(segment_codes, '{SEGMENT_B}')"
)


@pytest.fixture(scope="module")
def sql_client() -> DatabricksSqlClient:
    creds = _creds()
    if creds is None:
        pytest.skip(
            "segment-intersection parity SKIPPED: set DATABRICKS_HOST + "
            "DATABRICKS_TOKEN + DATABRICKS_WAREHOUSE_ID, or configure the "
            "Databricks CLI DEFAULT profile, to enable."
        )
    host, token, warehouse_id = creds
    client = DatabricksSqlClient(host, token, warehouse_id, timeout_s=50)
    # Absorb a cold serverless start before the counted statements run, so
    # a CANCELed 50s wait can't masquerade as a parity failure.
    for attempt in range(3):
        try:
            client.execute("SELECT 1")
            break
        except DatabricksSqlError:
            if attempt == 2:
                raise
            time.sleep(10)
    return client


def _count(client: DatabricksSqlClient, sql: str, params: dict[str, object] | None = None) -> int:
    rows = client.execute(sql, params)
    assert rows, f"count query returned no rows: {sql[:80]}"
    return int(rows[0]["n"])


def test_lead_repository_intersection_matches_direct_sql(
    sql_client: DatabricksSqlClient,
) -> None:
    repo = DatabricksLeadRepository(sql_client, cache_ttl_s=0)
    app_count = repo.count(
        segment=None,
        portfolio_id=None,
        segment_codes=[SEGMENT_A, SEGMENT_B],
        segment_mode="all",
    )
    direct_count = _count(sql_client, _DIRECT_INTERSECTION_LEAD_POPULATION)
    assert app_count == direct_count, (
        f"Lead Queue intersected count {app_count} != direct SQL "
        f"intersection {direct_count} for {SEGMENT_A} ∩ {SEGMENT_B} on "
        "gold.lead_population"
    )


def test_canonical_composer_intersection_matches_direct_sql(
    sql_client: DatabricksSqlClient,
) -> None:
    clause, params = compose_segment_predicate([SEGMENT_A, SEGMENT_B], mode="all")
    composed_count = _count(
        sql_client,
        f"SELECT COUNT(*) AS n FROM mip.gold.borrower_360 WHERE {clause}",
        params,
    )
    direct_count = _count(sql_client, _DIRECT_INTERSECTION_BORROWER_360)
    assert composed_count == direct_count, (
        f"composed-predicate count {composed_count} != direct SQL "
        f"intersection {direct_count} for {SEGMENT_A} ∩ {SEGMENT_B} on "
        "gold.borrower_360"
    )


def test_intersection_set_algebra_holds_on_live_population(
    sql_client: DatabricksSqlClient,
) -> None:
    """AND must narrow and OR must widen on the real membership arrays."""
    and_clause, and_params = compose_segment_predicate([SEGMENT_A, SEGMENT_B], mode="all")
    or_clause, or_params = compose_segment_predicate([SEGMENT_A, SEGMENT_B], mode="any")
    single_a, params_a = compose_segment_predicate([SEGMENT_A])
    single_b, params_b = compose_segment_predicate([SEGMENT_B])

    base = "SELECT COUNT(*) AS n FROM mip.gold.borrower_360 WHERE {clause}"
    n_and = _count(sql_client, base.format(clause=and_clause), and_params)
    n_or = _count(sql_client, base.format(clause=or_clause), or_params)
    n_a = _count(sql_client, base.format(clause=single_a), params_a)
    n_b = _count(sql_client, base.format(clause=single_b), params_b)

    assert n_a > 0, f"{SEGMENT_A} has no live members; intersection parity is vacuous"
    assert n_b > 0, f"{SEGMENT_B} has no live members; intersection parity is vacuous"
    assert n_and <= min(n_a, n_b), (
        f"intersection {n_and} exceeds a member segment ({SEGMENT_A}={n_a}, "
        f"{SEGMENT_B}={n_b}) — AND semantics are broken"
    )
    assert n_and <= n_or, f"intersection {n_and} exceeds union {n_or}"
    # Inclusion–exclusion is exact for two sets: |A| + |B| = |A∪B| + |A∩B|.
    assert n_a + n_b == n_or + n_and, (
        f"inclusion-exclusion drift: |A|+|B|={n_a + n_b} but "
        f"|A∪B|+|A∩B|={n_or + n_and}"
    )
