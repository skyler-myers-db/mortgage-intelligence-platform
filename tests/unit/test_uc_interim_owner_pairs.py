"""Interim owner pairs: exact matches only, at most 14 days ahead, an expired pair refuses (M2-PR8).

Spike S4 is owed, so the ownership handover leaves the pipeline-managed
datasets with their owner and the client's profile names each one in an exact
``(full_name, owner, expires_at)`` pair. The approved-owner policy admits that
owner for exactly that object; a case variant, another object, an expired
pair or a pair beyond 14 days does not pass.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from tests.fixtures.interim_pairs import pairs_from_json
from tests.fixtures.prestop_estate import FakeAccount, FakeWorkspace, clean_estate
from tools.clients.json_fields import InterimOwnerPair
from tools.databricks import prestop_owner_policy as probe
from tools.databricks.uc_interim_owner_pairs import (
    EXPIRED,
    MATCH,
    NONE,
    InterimPairRefusal,
    build_pairs,
    canonical_pairs,
)
from tools.databricks.uc_owner_policy import MetastoreOwnerPolicy, TargetServicePrincipal

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
TABLE = "mip.audit.campaign_treatment_snapshot"
INTERIM = "pipeline-owner@example.invalid"


def _pairs(*rows: dict[str, str]) -> str:
    return json.dumps(list(rows))


def _pair(full_name: str = TABLE, owner: str = INTERIM, expires_at: str = "2026-11-10") -> dict[str, str]:
    return {"full_name": full_name, "owner": owner, "expires_at": expires_at}


def test_blank_is_no_pairs_and_matches_nothing() -> None:
    pairs = pairs_from_json("", now=NOW)
    assert not pairs and pairs.verdict(full_name=TABLE, owner=INTERIM) == NONE


def test_only_the_exact_object_and_owner_match() -> None:
    pairs = pairs_from_json(_pairs(_pair()), now=NOW)

    assert pairs.verdict(full_name=TABLE, owner=INTERIM) == MATCH
    assert pairs.verdict(full_name=TABLE.upper(), owner=INTERIM) == NONE
    assert pairs.verdict(full_name=TABLE, owner=INTERIM.upper()) == NONE
    assert pairs.verdict(full_name=TABLE, owner=f" {INTERIM}") == NONE
    assert pairs.verdict(full_name="mip.audit.other_table", owner=INTERIM) == NONE
    assert pairs.verdict(full_name=TABLE, owner="someone-else@example.invalid") == NONE


def test_a_pair_at_or_past_its_expiry_is_expired() -> None:
    pairs = pairs_from_json(_pairs(_pair(expires_at="2026-11-04T12:00:00Z")), now=NOW)
    assert pairs.verdict(full_name=TABLE, owner=INTERIM) == EXPIRED
    earlier = pairs_from_json(_pairs(_pair(expires_at="2026-11-01")), now=NOW)
    assert earlier.verdict(full_name=TABLE, owner=INTERIM) == EXPIRED


def test_fourteen_days_ahead_is_the_ceiling() -> None:
    ceiling = (NOW + timedelta(days=14)).isoformat().replace("+00:00", "Z")
    assert pairs_from_json(_pairs(_pair(expires_at=ceiling)), now=NOW).verdict(full_name=TABLE, owner=INTERIM) == MATCH
    beyond = (NOW + timedelta(days=14, seconds=1)).isoformat().replace("+00:00", "Z")
    with pytest.raises(InterimPairRefusal) as refused:
        pairs_from_json(_pairs(_pair(expires_at=beyond)), now=NOW)
    assert refused.value.lines == ("pair 0: an interim owner pair expires at most 14 days ahead",)


@pytest.mark.parametrize(
    ("rows", "line"),
    [
        ([_pair(), _pair()], "pair 1: repeats pair 0's object; give each object one pair"),
        ([_pair(expires_at="2026-11-10T00:00")], "pair 0: expires_at is not a date or an offset-qualified time"),
    ],
)
def test_malformed_pairs_refuse_without_their_values(rows: list[dict[str, str]], line: str) -> None:
    """build_pairs' own refusals; the JSON shape is the profile's (test_client_profile_schema.py)."""

    with pytest.raises(InterimPairRefusal) as refused:
        build_pairs([InterimOwnerPair(**row) for row in rows], now=NOW)
    assert refused.value.lines == (line,)
    assert INTERIM not in str(refused.value)


def test_canonical_pairs_are_the_profile_line_sorted_by_object() -> None:
    text = canonical_pairs([_pair("mip.silver.zeta"), _pair("mip.silver.alpha")])
    assert "\n" not in text and " " not in text
    assert [row["full_name"] for row in json.loads(text)] == ["mip.silver.alpha", "mip.silver.zeta"]
    assert pairs_from_json(text, now=NOW).verdict(full_name="mip.silver.zeta", owner=INTERIM) == MATCH


def _estate(owner: str = INTERIM) -> dict[str, Any]:
    estate = clean_estate()
    estate["catalogs"]["mip"]["tables"]["audit.campaign_treatment_snapshot"] = owner
    estate["principals"][INTERIM] = {"kind": "user", "id": "interim-id"}
    return estate


def _refusals(estate: dict[str, Any], pairs: str) -> list[str]:
    found, _notes = probe.owner_policy_refusals(
        FakeWorkspace(estate),
        app_client_id="app-client-EXAMPLE",
        catalog="mip",
        approved_owner_principals=("mip-data-owners",),
        app_groups=frozenset({"users"}),
        metastore_owner="deployer@example.invalid",
        account_factory=lambda: FakeAccount(estate),
        interim_pairs=pairs_from_json(pairs, now=NOW),
    )
    return [refusal.message for refusal in found]


def test_the_policy_admits_the_interim_owner_for_exactly_its_object() -> None:
    assert _refusals(_estate(), "")[0].startswith("the treatment table owner")
    assert _refusals(_estate(), _pairs(_pair())) == []


def test_a_pair_for_another_object_or_a_case_variant_owner_still_refuses() -> None:
    other = _refusals(_estate(), _pairs(_pair(full_name="mip.silver.parcel_features")))
    assert len(other) == 1 and "outside the explicit approved-owner contract" in other[0]
    variant = _refusals(_estate(), _pairs(_pair(owner=INTERIM.upper())))
    assert len(variant) == 1 and "outside the explicit approved-owner contract" in variant[0]


def test_an_expired_pair_refuses_its_object() -> None:
    refused = _refusals(_estate(), _pairs(_pair(expires_at="2026-11-03")))
    assert len(refused) == 1 and "expired interim owner pair" in refused[0]


def test_a_pair_never_admits_the_apps_own_service_principal() -> None:
    estate = _estate(owner="app-client-EXAMPLE")
    refused = _refusals(estate, _pairs(_pair(owner="app-client-EXAMPLE")))
    assert len(refused) == 1 and "Target App service principal cannot own" in refused[0]


def test_the_metastore_owner_policy_takes_no_pair() -> None:
    workspace = FakeWorkspace(_estate())
    with pytest.raises(RuntimeError, match="takes no interim owner pair"):
        MetastoreOwnerPolicy(
            workspace=workspace,  # type: ignore[arg-type]
            target=TargetServicePrincipal(application_id="app-client-EXAMPLE", scim_id="app-scim-1"),
            metastore_owner="deployer@example.invalid",
            interim_pairs=pairs_from_json(_pairs(_pair()), now=NOW),
        )


def test_owner_equivalent_grantees_never_read_the_pairs() -> None:
    """A pair approves one object's owner; it never approves a grantee (prestop_owner_equivalent)."""

    from tools.databricks import prestop_owner_equivalent

    source = prestop_owner_equivalent.__file__
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    assert "interim" not in text.casefold()
