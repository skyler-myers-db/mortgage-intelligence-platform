"""Token-bound destination proof contracts for live agent evaluation."""

from __future__ import annotations

from typing import Any

from tools.databricks.agent_eval_destination_proof import with_destination_total


def test_signed_handoff_uses_actor_token_without_admin_identity_proof_flag() -> None:
    captured: dict[str, Any] = {}

    class _DestinationResponse:
        status_code = 200
        headers = {
            "X-Total-Matching": "7",
            "X-Cohort-Fingerprint": "a" * 64,
            "X-Cohort-Snapshot-ID": "snapshot-signed",
        }

    class _Client:
        def get(self, url: str, *, headers: dict[str, str]) -> _DestinationResponse:
            captured["url"] = url
            captured["headers"] = headers
            return _DestinationResponse()

    enriched = with_destination_total(
        client=_Client(),
        app_url="https://example.test",
        actor_token="normal-bearer",
        admin_token="admin-bearer",
        response={
            "route": "/lead-queue?segment=itm&growth_handoff=signed-value",
            "tool_result_hash": "b" * 64,
        },
    )

    assert captured["headers"]["Authorization"] == "Bearer normal-bearer"
    assert "include_identity_proof" not in captured["url"]
    assert enriched["destination_total"] == 7
    assert enriched["destination_cohort_fingerprint"] == "a" * 64
    assert enriched["destination_snapshot_id"] == "snapshot-signed"
