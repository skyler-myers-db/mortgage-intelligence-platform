"""Credential-free contracts for the live lifecycle Delta replay proof."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from jobs import sync_lifecycle_state
from tests.integration import test_lifecycle_delta_replay_live as replay_live


def _event() -> dict[str, object]:
    return replay_live._approval_row(
        borrower_id="B-0000000000001",
        status="approved",
        offer_code="smoke_stale",
        decided_at=datetime(2026, 1, 15, 14, 0, tzinfo=UTC),
        event_id="00000000-0000-4000-8000-000000000001",
    )


def test_scratch_rewrite_changes_only_exact_production_tables() -> None:
    generated = sync_lifecycle_state._build_lifecycle_merge([_event()], catalog="mip")
    scratch_borrower = "`mip`.`audit`.`lifecycle_replay_borrower_contract`"
    scratch_target = "`mip`.`audit`.`lifecycle_replay_target_contract`"

    rewritten = replay_live._rewrite_generated_merge_for_scratch(
        generated,
        catalog="mip",
        scratch_borrower_table=scratch_borrower,
        scratch_lifecycle_table=scratch_target,
    )

    assert scratch_borrower in rewritten
    assert scratch_target in rewritten
    assert "`mip`.`gold`.`borrower_360`" not in rewritten
    assert "`mip`.`gold`.`borrower_lifecycle_state`" not in rewritten
    assert rewritten.count("MERGE INTO") == 1
    assert "'B-0000000000001'" in rewritten


def test_scratch_rewrite_fails_closed_when_generator_shape_drifts() -> None:
    generated = sync_lifecycle_state._build_lifecycle_merge([_event()], catalog="mip")
    missing_borrower_fqn = generated.replace("`mip`.`gold`.`borrower_360`", "")

    with pytest.raises(AssertionError, match="expected exactly one"):
        replay_live._rewrite_generated_merge_for_scratch(
            missing_borrower_fqn,
            catalog="mip",
            scratch_borrower_table="`mip`.`audit`.`borrower`",
            scratch_lifecycle_table="`mip`.`audit`.`target`",
        )


def test_live_config_requires_credentials_and_explicit_mutation_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "DATABRICKS_HOST",
        "DATABRICKS_SERVER_HOSTNAME",
        "DATABRICKS_TOKEN",
        "DATABRICKS_WAREHOUSE_ID",
        "MIP_LIVE_MUTATION_OK",
    ):
        monkeypatch.delenv(name, raising=False)
    assert replay_live._live_warehouse_config() is None

    monkeypatch.setenv("DATABRICKS_HOST", "workspace.example")
    monkeypatch.setenv("DATABRICKS_TOKEN", "test-token")
    monkeypatch.setenv("DATABRICKS_WAREHOUSE_ID", "warehouse-id")
    assert replay_live._live_warehouse_config() is None

    monkeypatch.setenv("MIP_LIVE_MUTATION_OK", "1")
    assert replay_live._live_warehouse_config() == (
        "https://workspace.example",
        "test-token",
        "warehouse-id",
    )


def test_live_proof_attempts_both_drops_after_setup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statements: list[str] = []

    def fail_target_create(
        _config: tuple[str, str, str],
        statement: str,
    ) -> list[list[object]]:
        statements.append(statement)
        normalized = " ".join(statement.split())
        if normalized.startswith("CREATE TABLE") and "replay_target" in normalized:
            raise AssertionError("forced target creation failure")
        return []

    monkeypatch.setattr(replay_live, "_execute_statement", fail_target_create)

    with pytest.raises(AssertionError, match="forced target creation failure"):
        replay_live.test_generated_lifecycle_merge_is_monotonic_in_live_delta(
            ("https://workspace.example", "test-token", "warehouse-id")
        )

    cleanup = [" ".join(statement.split()) for statement in statements[-2:]]
    assert len(cleanup) == 2
    assert cleanup[0].startswith("DROP TABLE IF EXISTS `mip`.`audit`.`lifecycle_replay_target_")
    assert cleanup[1].startswith("DROP TABLE IF EXISTS `mip`.`audit`.`lifecycle_replay_borrower_")


@pytest.mark.parametrize("unsafe", ["audit.prod", "audit;drop", "audit-name", ""])
def test_scratch_identifiers_fail_closed(unsafe: str) -> None:
    with pytest.raises(ValueError, match="unsafe schema identifier"):
        replay_live._safe_identifier(unsafe, field="schema")
