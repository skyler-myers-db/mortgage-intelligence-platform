"""Credential-free contracts for the live lifecycle Delta replay proof."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

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


def _install_review_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    catalog: str = "mip",
) -> dict[str, object]:
    manifest = replay_live._review_manifest(catalog=catalog)
    path = tmp_path / "reviewed-lifecycle-replay.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(replay_live, "_REVIEW_ARTIFACT_PATH", path)
    return manifest


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
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("MIP_LIVE_SCRATCH_SUFFIX", "gha_123")
    monkeypatch.setenv("MIP_LIFECYCLE_SMOKE_SCHEMA", "unreviewed_schema")
    manifest = _install_review_artifact(tmp_path, monkeypatch)
    monkeypatch.setenv(
        "MIP_LIFECYCLE_REPLAY_REVIEW_SHA256",
        str(manifest["sha256"]),
    )
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
    assert cleanup == [
        "DROP TABLE IF EXISTS `mip`.`audit`.`lifecycle_replay_target_gha_123`",
        "DROP TABLE IF EXISTS `mip`.`audit`.`lifecycle_replay_borrower_gha_123`",
    ]
    assert all("unreviewed_schema" not in statement for statement in statements)


def test_live_proof_refuses_mutation_without_reviewed_render_digest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("MIP_LIVE_SCRATCH_SUFFIX", "gha_123")
    monkeypatch.delenv("MIP_LIFECYCLE_REPLAY_REVIEW_SHA256", raising=False)
    _install_review_artifact(tmp_path, monkeypatch)
    statements: list[str] = []

    def record_statement(
        _config: tuple[str, str, str],
        statement: str,
    ) -> list[list[object]]:
        statements.append(statement)
        return []

    monkeypatch.setattr(
        replay_live,
        "_execute_statement",
        record_statement,
    )

    with pytest.raises(RuntimeError, match="render and govern the exact SQL first"):
        replay_live.test_generated_lifecycle_merge_is_monotonic_in_live_delta(
            ("https://workspace.example", "test-token", "warehouse-id")
        )

    assert statements == []


def test_rendered_review_manifest_binds_every_ordered_merge() -> None:
    manifest = replay_live._review_manifest(catalog="mip")

    assert manifest["contract"] == "mip-lifecycle-delta-replay-v1"
    assert manifest["scratch_schema"] == "audit"
    assert manifest["statement_count"] == 11
    assert len(str(manifest["sha256"])) == 64
    statements = manifest["statements"]
    assert isinstance(statements, list)
    assert len(statements) == 11
    assert all(
        "MERGE INTO `mip`.`audit`.`lifecycle_replay_target_gha_<run>`" in sql
        for sql in statements
    )
    assert all(
        "`mip`.`audit`.`lifecycle_replay_borrower_gha_<run>`" in sql
        for sql in statements
    )
    assert all("`mip`.`gold`" not in sql for sql in statements)


def test_committed_review_artifact_exactly_matches_current_renderer() -> None:
    artifact = json.loads(replay_live._REVIEW_ARTIFACT_PATH.read_text(encoding="utf-8"))

    assert artifact == replay_live._review_manifest(catalog=str(artifact["catalog"]))


def test_tampered_committed_artifact_fails_before_sql(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest = _install_review_artifact(tmp_path, monkeypatch)
    raw_statements = manifest["statements"]
    assert isinstance(raw_statements, list)
    statements = list(raw_statements)
    statements[0] += "\n-- tampered"
    manifest["statements"] = statements
    replay_live._REVIEW_ARTIFACT_PATH.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setenv(
        "MIP_LIFECYCLE_REPLAY_REVIEW_SHA256",
        str(manifest["sha256"]),
    )
    runtime = replay_live._review_manifest(catalog="mip")
    borrower_table = "`mip`.`audit`.`lifecycle_replay_borrower_gha_123`"
    lifecycle_table = "`mip`.`audit`.`lifecycle_replay_target_gha_123`"
    rows = replay_live._replay_rows("B-REPLAY0000001")
    rendered = replay_live._render_merge_plan(
        catalog="mip",
        borrower_table=borrower_table,
        lifecycle_table=lifecycle_table,
        rows=rows,
    )

    assert runtime["sha256"] == manifest["sha256"]
    with pytest.raises(RuntimeError, match="artifact digest is invalid"):
        replay_live._assert_reviewed_merge_plan(
            rendered,
            catalog="mip",
            borrower_table=borrower_table,
            lifecycle_table=lifecycle_table,
        )


def test_duplicate_artifact_key_fails_before_sql(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest = _install_review_artifact(tmp_path, monkeypatch)
    serialized = json.dumps(manifest)
    replay_live._REVIEW_ARTIFACT_PATH.write_text(
        serialized.replace("{", '{"contract":"ambiguous",', 1),
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "MIP_LIFECYCLE_REPLAY_REVIEW_SHA256",
        str(manifest["sha256"]),
    )
    borrower_table = "`mip`.`audit`.`lifecycle_replay_borrower_gha_123`"
    lifecycle_table = "`mip`.`audit`.`lifecycle_replay_target_gha_123`"
    rendered = replay_live._render_merge_plan(
        catalog="mip",
        borrower_table=borrower_table,
        lifecycle_table=lifecycle_table,
        rows=replay_live._replay_rows("B-REPLAY0000001"),
    )

    with pytest.raises(RuntimeError, match="artifact is unavailable"):
        replay_live._assert_reviewed_merge_plan(
            rendered,
            catalog="mip",
            borrower_table=borrower_table,
            lifecycle_table=lifecycle_table,
        )


def test_actions_variable_cannot_substitute_for_committed_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_review_artifact(tmp_path, monkeypatch, catalog="mip")
    foreign = replay_live._review_manifest(catalog="mip_other")
    monkeypatch.setenv(
        "MIP_LIFECYCLE_REPLAY_REVIEW_SHA256",
        str(foreign["sha256"]),
    )
    borrower_table = "`mip_other`.`audit`.`lifecycle_replay_borrower_gha_123`"
    lifecycle_table = "`mip_other`.`audit`.`lifecycle_replay_target_gha_123`"
    rendered = replay_live._render_merge_plan(
        catalog="mip_other",
        borrower_table=borrower_table,
        lifecycle_table=lifecycle_table,
        rows=replay_live._replay_rows("B-REPLAY0000001"),
    )

    with pytest.raises(RuntimeError, match="differs from the committed review"):
        replay_live._assert_reviewed_merge_plan(
            rendered,
            catalog="mip_other",
            borrower_table=borrower_table,
            lifecycle_table=lifecycle_table,
        )


def test_static_scratch_prefix_is_bound_by_review_digest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest = _install_review_artifact(tmp_path, monkeypatch)
    monkeypatch.setenv(
        "MIP_LIFECYCLE_REPLAY_REVIEW_SHA256",
        str(manifest["sha256"]),
    )
    borrower_table = "`mip`.`audit`.`different_borrower_gha_123`"
    lifecycle_table = "`mip`.`audit`.`different_target_gha_123`"
    rendered = replay_live._render_merge_plan(
        catalog="mip",
        borrower_table=borrower_table,
        lifecycle_table=lifecycle_table,
        rows=replay_live._replay_rows("B-REPLAY0000001"),
    )

    with pytest.raises(RuntimeError, match="differs from the committed review"):
        replay_live._assert_reviewed_merge_plan(
            rendered,
            catalog="mip",
            borrower_table=borrower_table,
            lifecycle_table=lifecycle_table,
        )


@pytest.mark.parametrize("unsafe", ["audit.prod", "audit;drop", "audit-name", ""])
def test_scratch_identifiers_fail_closed(unsafe: str) -> None:
    with pytest.raises(ValueError, match="unsafe schema identifier"):
        replay_live._safe_identifier(unsafe, field="schema")


@pytest.mark.parametrize(
    "suffix",
    ["", "gha_", "gha_123_extra", "manual_123", "gha-123", "gha_12.3"],
)
def test_lifecycle_scratch_suffix_is_github_run_only(
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
) -> None:
    monkeypatch.setenv("MIP_LIVE_SCRATCH_SUFFIX", suffix)

    with pytest.raises(ValueError, match=r"expected deterministic gha_\[0-9\]\+"):
        replay_live._scratch_suffix()


def test_lifecycle_scratch_suffix_accepts_numeric_github_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIP_LIVE_SCRATCH_SUFFIX", "gha_123456")

    assert replay_live._scratch_suffix() == "gha_123456"
