from __future__ import annotations

from copy import deepcopy

import pytest

from tools.cleanup_live_campaign_fixtures import (
    _archive_campaign,
    cleanup_live_campaign_fixtures,
    run_scoped_campaign_name,
)


def test_run_scoped_name_is_exact_and_public_contract_bounded() -> None:
    assert run_scoped_campaign_name(
        "Live campaign audit contract",
        marker="ghabcdearf",
    ) == "Live campaign audit contract ghabcdearf"
    with pytest.raises(RuntimeError, match="marker or label"):
        run_scoped_campaign_name("Customer campaign", marker="ghabcdearf")
    with pytest.raises(RuntimeError, match="marker or label"):
        run_scoped_campaign_name(
            "Live campaign audit contract",
            marker="gha-123-1",
        )


def test_cleanup_archives_only_exact_marked_run_and_proves_three_absences() -> None:
    marker = "ghabcdearf"
    target_id = "target-id"
    other_run_id = "other-run-id"
    rows = {
        target_id: {
            "campaign_id": target_id,
            "name": run_scoped_campaign_name(
                "Live Lakebase approval contract",
                marker=marker,
            ),
            "status": "draft",
            "treatment_state": "ready",
        },
        other_run_id: {
            "campaign_id": other_run_id,
            "name": run_scoped_campaign_name(
                "Live Lakebase approval contract",
                marker="ghajjjrj",
            ),
            "status": "draft",
            "treatment_state": "ready",
        },
        "customer-id": {
            "campaign_id": "customer-id",
            "name": "Customer retention campaign",
            "status": "draft",
            "treatment_state": "ready",
        },
    }
    active_inventory_reads = 0

    def request(
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        *,
        token: str,
    ) -> tuple[int, object]:
        nonlocal active_inventory_reads
        if path == "/api/campaigns?limit=200":
            assert method == "GET" and token == "owner-token"
            active_inventory_reads += 1
            return 200, {
                "campaigns": [
                    deepcopy(row)
                    for row in rows.values()
                    if row["status"] != "archived"
                ]
            }
        campaign_id = path.rsplit("/", 1)[-1]
        assert token == "admin-token"
        if method == "GET":
            return 200, deepcopy(rows[campaign_id])
        assert payload == {
            "status": "archived",
            "expected_status": "draft",
            "rationale": "Archive isolated live release fixture.",
        }
        rows[campaign_id]["status"] = "archived"
        return 200, deepcopy(rows[campaign_id])

    archived = cleanup_live_campaign_fixtures(
        request,
        owner_token="owner-token",
        admin_token="admin-token",
        run_marker=marker,
        sleep=lambda _seconds: None,
    )

    assert archived == (target_id,)
    assert rows[target_id]["status"] == "archived"
    assert rows[other_run_id]["status"] == "draft"
    assert rows["customer-id"]["status"] == "draft"
    assert active_inventory_reads >= 4


def test_cleanup_recovers_only_exact_run_marked_genie_draft() -> None:
    marker = "ghabcdearf"
    rows = {
        "genie-id": {
            "campaign_id": "genie-id",
            "name": run_scoped_campaign_name("Genie strategy draft", marker=marker),
            "status": "draft",
        },
        "customer-id": {
            "campaign_id": "customer-id",
            "name": "Genie strategy draft",
            "status": "draft",
        },
    }

    def request(
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        *,
        token: str,
    ) -> tuple[int, object]:
        if path == "/api/campaigns?limit=200":
            assert token == "dedicated-owner-token"
            return 200, {
                "campaigns": [
                    deepcopy(row) for row in rows.values() if row["status"] != "archived"
                ]
            }
        campaign_id = path.rsplit("/", 1)[-1]
        assert token == "admin-token"
        if method == "GET":
            return 200, deepcopy(rows[campaign_id])
        assert payload == {
            "status": "archived",
            "expected_status": "draft",
            "rationale": "Archive isolated live release fixture.",
        }
        rows[campaign_id]["status"] = "archived"
        return 200, deepcopy(rows[campaign_id])

    archived = cleanup_live_campaign_fixtures(
        request,
        owner_token="dedicated-owner-token",
        admin_token="admin-token",
        run_marker=marker,
        sleep=lambda _seconds: None,
    )

    assert archived == ("genie-id",)
    assert rows["genie-id"]["status"] == "archived"
    assert rows["customer-id"]["status"] == "draft"


def test_all_run_cleanup_recovers_building_and_transient_patch() -> None:
    campaign_id = "abandoned-id"
    row: dict[str, object] = {
        "campaign_id": campaign_id,
        "name": run_scoped_campaign_name(
            "Live Lakebase concurrency contract",
            marker="ghajjjrj",
        ),
        "status": "draft",
        "treatment_state": "building",
    }
    elapsed = 0.0
    patch_attempts = 0

    def sleep(seconds: float) -> None:
        nonlocal elapsed
        elapsed += seconds

    def request(
        method: str,
        path: str,
        _payload: dict[str, object] | None = None,
        **_kwargs: object,
    ) -> tuple[int, object]:
        nonlocal patch_attempts
        if path == "/api/campaigns?limit=200":
            return 200, {"campaigns": [] if row["status"] == "archived" else [deepcopy(row)]}
        if method == "GET":
            return 200, deepcopy(row)
        patch_attempts += 1
        if elapsed < 300:
            return 409, {"detail": "Campaign must be rebuilt before it can advance."}
        if patch_attempts == 61:
            return 503, {"detail": "temporary"}
        row["status"] = "archived"
        row["treatment_state"] = "failed"
        return 200, deepcopy(row)

    archived = cleanup_live_campaign_fixtures(
        request,
        owner_token="owner-token",
        admin_token="admin-token",
        run_marker=None,
        sleep=sleep,
    )

    assert archived == (campaign_id,)
    assert elapsed >= 300
    assert patch_attempts == 62
    assert row["status"] == "archived"


def test_cleanup_rejects_truncated_inventory_without_absence_claim() -> None:
    def request(*_args: object, **_kwargs: object) -> tuple[int, object]:
        return 200, {
            "campaigns": [
                {
                    "campaign_id": f"customer-{index}",
                    "name": "Customer retention campaign",
                }
                for index in range(200)
            ]
        }

    with pytest.raises(RuntimeError, match="truncated"):
        cleanup_live_campaign_fixtures(
            request,
            owner_token="owner-token",
            admin_token="admin-token",
            run_marker=None,
            sleep=lambda _seconds: None,
        )


def test_archive_accepts_scalar_success_on_final_attempt_only_after_get() -> None:
    calls: list[str] = []
    status = "draft"

    def request(
        method: str,
        _path: str,
        _payload: dict[str, object] | None = None,
        **_kwargs: object,
    ) -> tuple[int, object]:
        nonlocal status
        calls.append(method)
        if method == "GET":
            return 200, {"status": status}
        status = "archived"
        return 200, "mutation response intentionally ignored"

    _archive_campaign(
        request,
        campaign_id="campaign-id",
        admin_token="admin-token",
        sleep=lambda _seconds: None,
        attempts=1,
    )

    assert calls == ["GET", "PATCH", "GET"]
