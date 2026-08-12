"""Live governance check on the governed place dimension the Genie guard reads.

``genie_place_dimension.protected_class_safe_values`` subtracts governed city
names from the FAIR-LENDING scanner on the Ask Genie answer surface. That is
only defensible while the dimension really is a dimension of place names, so
this test asks the live table the question the unit tests can only ask a
fixture: does ``mip.gold.borrower_360.city`` contain anything that reads as a
protected class or a protected audience?

The admission gate already refuses such a value at runtime (an exemption set
can never contain one), so this test is not the safety mechanism -- it is the
alarm. A refusal firing against live gold means Cotality coverage, the silver
transform, or the gold projection put non-place text in a place column, and
that is worth a red build rather than a log line nobody reads.

Warehouse-gated like the rest of ``tests/integration``: local runs without
credentials skip.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

import pytest

from backend.services.genie_message_policy import genie_visible_text_unsafe
from backend.services.genie_place_dimension import (
    _MAX_PROTECTED_CLASS_VALUES,
    GovernedPlaceDimensionResolver,
    _default_protected_class_conflict_predicate,
    _disarms_a_protected_class_canary,
    _reset_governed_place_dimension_for_tests,
)

pytestmark = pytest.mark.integration


def _creds() -> tuple[str, str, str] | None:
    host = os.environ.get("DATABRICKS_HOST") or os.environ.get("DATABRICKS_SERVER_HOSTNAME")
    token = os.environ.get("DATABRICKS_TOKEN")
    warehouse_id = os.environ.get("DATABRICKS_WAREHOUSE_ID")
    if not host or not token or not warehouse_id:
        return None
    if not host.startswith("http"):
        host = "https://" + host
    return host.rstrip("/"), token, warehouse_id


def _run_sql_rows(host: str, token: str, warehouse_id: str, statement: str) -> list[list[Any]]:
    payload = json.dumps(
        {
            "statement": statement,
            "warehouse_id": warehouse_id,
            "wait_timeout": "50s",
            "on_wait_timeout": "CANCEL",
            "disposition": "INLINE",
            "format": "JSON_ARRAY",
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{host}/api/2.0/sql/statements/",
        data=payload,
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:  # pragma: no cover -- network
        pytest.skip(f"warehouse unreachable: {exc}")
    status = body.get("status", {}).get("state")
    if status != "SUCCEEDED":
        err = body.get("status", {}).get("error", {}).get("message", "unknown")
        pytest.fail(f"warehouse statement failed: state={status!r} err={err!r}")
    return body.get("result", {}).get("data_array") or []


@pytest.fixture(scope="module")
def live_cities() -> list[str]:
    creds = _creds()
    if creds is None:
        pytest.skip(
            "place-dimension integration test SKIPPED: set DATABRICKS_HOST (or "
            "DATABRICKS_SERVER_HOSTNAME), DATABRICKS_TOKEN, and "
            "DATABRICKS_WAREHOUSE_ID to enable."
        )
    host, token, wid = creds
    rows = _run_sql_rows(
        host,
        token,
        wid,
        """
        SELECT DISTINCT city
        FROM mip.gold.borrower_360
        WHERE city IS NOT NULL AND TRIM(city) <> ''
        ORDER BY city ASC
        """,
    )
    cities = [str(row[0]) for row in rows if row and row[0]]
    assert cities, "gold city dimension came back empty; the guard would exempt nothing"
    return cities


def test_no_live_gold_city_reads_as_a_protected_class(live_cities: list[str]) -> None:
    """The brief's requirement, asked of the real table.

    Fails if ``mip.gold.borrower_360.city`` ever contains a value that both
    wants the fair-lending exemption and is refused it -- a bare ``BLACK``, or
    an audience phrase such as ``HAWAIIAN HOMEOWNERS``. Such a value is refused
    at runtime either way; this makes the data fact visible instead of silent.

    Both halves are load-bearing. Refusal alone is not an alarm: ``PACIFIC`` is
    a real WA city that the gate refuses because masking it would break
    ``pacific islander`` in a canary, and it is not a candidate for the
    exemption in the first place (a lone ``PACIFIC`` does not trip the
    fair-lending scan), so it never reaches the gate in production. Refusing
    fragments is the conservative direction and not a data problem.
    """

    alarming = sorted(
        city
        for city in live_cities
        if _default_protected_class_conflict_predicate(city)
        and _disarms_a_protected_class_canary(city)
    )
    assert alarming == [], (
        f"{len(alarming)} live gold city value(s) read as a protected class or "
        f"protected audience: {alarming[:10]}. The runtime gate refuses them, so the "
        "guard is intact, but a place column should not contain these strings — "
        "check the silver/gold geography projection."
    )


def test_live_fair_lending_exemption_stays_small_and_gated(live_cities: list[str]) -> None:
    """The exemption is a handful of place names, not a vocabulary.

    Four of 428 qualified on paychex 2026-08-12 (``TACOMA``, ``BLACK DIAMOND``,
    ``HAWAIIAN GARDENS``, ``INDIAN HEAD PARK``). The assertion is deliberately
    a bound and a property, not that list: CLAUDE.md forbids pinning the
    product to a fixed geography, and coverage refreshes are expected.
    """

    resolver = GovernedPlaceDimensionResolver(dimension_reader=lambda: list(live_cities))
    _reset_governed_place_dimension_for_tests(resolver)
    try:
        exempt = resolver.protected_class_safe_values()

        assert len(exempt) <= _MAX_PROTECTED_CLASS_VALUES
        # Under 5% of the dimension. A larger share means the column stopped
        # being place names, or a detector changed shape.
        assert len(exempt) <= max(8, len(live_cities) // 20), (
            f"{len(exempt)} of {len(live_cities)} live city values were exempted from the "
            f"fair-lending scan: {sorted(exempt)[:20]}"
        )
        assert all(value in live_cities for value in exempt)

        # End to end through the real guard entry point, on real values. The
        # exemption has to actually clear the narrative -- and a protected term
        # standing beside the very same city has to survive it, which is the
        # property that no amount of resolver-level assertion can establish.
        for city in sorted(exempt):
            assert genie_visible_text_unsafe(f"{city} leads with 17 borrowers.") is False
            assert genie_visible_text_unsafe(f"Target black borrowers in {city}.") is True
    finally:
        _reset_governed_place_dimension_for_tests(None)
