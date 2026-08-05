"""Credential-free safety contracts for the active-App Lakebase gate."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.integration import test_lakebase_round_trip as live_probe

REPO = Path(__file__).resolve().parents[2]
ROUND_TRIP = REPO / "tests" / "integration" / "test_lakebase_round_trip.py"
NIGHTLY = REPO / ".github" / "workflows" / "nightly.yml"


def test_post_activation_probe_cannot_replay_schema_or_open_direct_database() -> None:
    text = ROUND_TRIP.read_text(encoding="utf-8")

    assert "lakebase/schema.sql" not in text
    assert "_apply_schema" not in text
    assert "LakebaseClient" not in text
    assert "client.execute" not in text
    assert "/api/health" in text
    assert "/api/audit/my-events?limit=1" in text


def test_nightly_documents_public_api_write_read_proof_after_read_probe() -> None:
    text = NIGHTLY.read_text(encoding="utf-8")

    read_pos = text.index("- name: Lakebase post-activation read path")
    mutation_pos = text.index("- name: Low-volume deployed workflow contracts")
    campaign_pos = text.index(
        "tests/integration/test_campaign_audit_workflow_live.py",
        mutation_pos,
    )
    assert read_pos < mutation_pos < campaign_pos
    assert "service principal performs the underlying database transaction" in text


def test_post_activation_probe_accepts_actor_audit_page_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses: Iterator[tuple[int, object]] = iter(
        (
            (200, {"circuit_breakers": {"lakebase": "closed"}}),
            (200, {"items": [], "next_cursor": None}),
        )
    )
    monkeypatch.setattr(live_probe, "_get_json", lambda _path: next(responses))

    live_probe.test_lakebase_post_activation_read_path_is_healthy()


def test_post_activation_probe_rejects_legacy_bare_event_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses: Iterator[tuple[int, object]] = iter(
        (
            (200, {"circuit_breakers": {"lakebase": "closed"}}),
            (200, []),
        )
    )
    monkeypatch.setattr(live_probe, "_get_json", lambda _path: next(responses))

    with pytest.raises(AssertionError):
        live_probe.test_lakebase_post_activation_read_path_is_healthy()
