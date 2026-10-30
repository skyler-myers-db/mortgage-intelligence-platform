"""App serving access only on record-pinned endpoints, refused before the stop (M0-PR5)."""

from __future__ import annotations

from typing import Any

from tests.fixtures.prestop_estate import (
    APP_CLIENT,
    APP_NAME,
    APP_SCIM,
    BLUE_GATEWAY,
    BLUE_SUPERVISOR_ENDPOINT,
    CANONICAL,
    LEGACY_GATEWAY,
    FakeWorkspace,
    acl_entry,
    clean_estate,
)
from tools.databricks import prestop_serving_endpoints as probe
from tools.databricks.prestop_probe_suite import canonical_supervisor_endpoint
from tools.databricks.prestop_refusals import SERVING_ENDPOINT


def _run(
    estate: dict[str, Any],
    *,
    pinned: tuple[str, ...] = (BLUE_GATEWAY, BLUE_SUPERVISOR_ENDPOINT),
    groups: frozenset[str] = frozenset({"users"}),
    app_client_id: str = APP_CLIENT,
) -> tuple[list[str], list[str]]:
    refusals, notes = probe.serving_endpoint_refusals(
        FakeWorkspace(estate),
        app_name=APP_NAME,
        app_client_id=app_client_id,
        app_scim_id=APP_SCIM,
        app_groups=groups,
        pinned_endpoints=pinned,
    )
    assert all(refusal.code == SERVING_ENDPOINT for refusal in refusals)
    return [refusal.message for refusal in refusals], notes


def _query(field: str = "service_principal_name", name: str = APP_CLIENT, level: str = "CAN_QUERY") -> dict[str, Any]:
    return acl_entry(field, name, level)


def test_query_access_on_the_record_gateway_only_passes() -> None:
    refusals, notes = _run(clean_estate())

    assert refusals == []
    assert notes == ["App serving access is limited to record-pinned endpoints (1 reached)"]


def test_the_legacy_gateway_no_record_pins_refuses() -> None:
    estate = clean_estate()
    estate["serving"][LEGACY_GATEWAY] = {"id": "endpoint-legacy", "acl": [_query()]}

    refusals, _notes = _run(estate)

    assert len(refusals) == 1
    assert f"retains forbidden access to unrelated serving endpoint {LEGACY_GATEWAY!r}" in refusals[0]
    assert "after step 0a stops the App" in refusals[0]
    assert "Remove the App service principal's direct or group access" in refusals[0]


def test_the_legacy_gateway_a_legacy_record_pins_passes() -> None:
    estate = clean_estate()
    estate["serving"] = {LEGACY_GATEWAY: {"id": "endpoint-legacy", "acl": [_query()]}}

    refusals, _notes = _run(estate, pinned=(LEGACY_GATEWAY,))

    assert refusals == []


def test_a_supervisor_renamed_off_the_canonical_name_is_unrelated() -> None:
    estate = clean_estate()
    estate["agents"].append(
        {
            "supervisor_agent_id": "supervisor-old",
            "display_name": f"{CANONICAL} (retired copy)",
            "creator": "operator@example.invalid",
            "endpoint_name": "mas-renamed-endpoint",
            "create_time": "2026-01-01T00:00:00.000Z",
        }
    )
    estate["serving"]["mas-renamed-endpoint"] = {"id": "endpoint-renamed", "acl": [_query()]}
    pinned = (BLUE_GATEWAY, canonical_supervisor_endpoint(estate["agents"], CANONICAL))

    refusals, _notes = _run(estate, pinned=pinned)

    assert pinned[1] == BLUE_SUPERVISOR_ENDPOINT, "the canonical one is pinned, the renamed one not"
    assert len(refusals) == 1 and "'mas-renamed-endpoint'" in refusals[0]


def test_direct_query_on_the_canonical_supervisor_the_cutover_replaces_passes() -> None:
    estate = clean_estate()
    estate["serving"][BLUE_SUPERVISOR_ENDPOINT]["acl"] = [_query()]

    refusals, notes = _run(estate)

    assert refusals == [] and "(2 reached)" in notes[0]


def test_group_access_through_users_on_an_unrelated_endpoint_refuses() -> None:
    estate = clean_estate()
    estate["serving"]["other-team-endpoint"] = {
        "id": "endpoint-other",
        "acl": [_query("group_name", "users")],
    }

    refusals, _notes = _run(estate)

    assert len(refusals) == 1 and "'other-team-endpoint'" in refusals[0]


def test_broader_than_query_on_a_pinned_gateway_refuses() -> None:
    estate = clean_estate()
    estate["serving"][BLUE_GATEWAY]["acl"] = [_query(level="CAN_MANAGE")]

    refusals, _notes = _run(estate)

    assert len(refusals) == 1 and "non-exact direct principal ACL" in refusals[0]


def test_no_reached_pinned_endpoint_needs_no_access_anywhere() -> None:
    estate = clean_estate()
    estate["serving"][BLUE_GATEWAY]["acl"] = []
    assert _run(estate) == ([], ["App serving access is limited to record-pinned endpoints (0 reached)"])

    estate["serving"]["other-team-endpoint"] = {"id": "endpoint-other", "acl": [_query()]}
    refusals, _notes = _run(estate, pinned=())
    assert len(refusals) == 1 and "'other-team-endpoint'" in refusals[0]


def test_an_unreadable_endpoint_listing_refuses() -> None:
    workspace = FakeWorkspace(clean_estate())

    def broken() -> list[Any]:
        raise RuntimeError("PERMISSION_DENIED listing serving endpoints")

    workspace.serving_endpoints.list = broken
    refusals, _notes = probe.serving_endpoint_refusals(
        workspace,
        app_name=APP_NAME,
        app_client_id=APP_CLIENT,
        app_scim_id=APP_SCIM,
        app_groups=frozenset({"users"}),
        pinned_endpoints=(BLUE_GATEWAY,),
    )

    assert len(refusals) == 1 and "PERMISSION_DENIED" in refusals[0].message


def test_no_app_holds_no_serving_access() -> None:
    assert _run(clean_estate(), app_client_id="")[0] == []
