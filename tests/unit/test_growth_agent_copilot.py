from __future__ import annotations

from backend.agents import mortgage_growth_copilot as copilot
from backend.config.settings import Settings
from backend.schemas.growth_agent import GrowthAgentPromptRunRequest


def _settings() -> Settings:
    return Settings(
        databricks_host="https://dbc-test.cloud.databricks.com",
        databricks_warehouse_id="wh-123",
        genie_space_id="space-abc",
    )


def test_prompt_planner_does_not_call_genie_sql_path(monkeypatch) -> None:
    def fail_if_called() -> object:
        raise AssertionError("Growth Agent prompt planning must not call the Genie SQL answer path")

    monkeypatch.setattr(copilot, "get_genie_client", fail_if_called, raising=False)
    workflow, evidence = copilot.plan_growth_agent_prompt(
        GrowthAgentPromptRunRequest(prompt="Find listed purchase opportunities"),
        settings=_settings(),
    )

    assert workflow.id == "custom_segment_watch"
    assert evidence.execution_mode == "deterministic"
    assert evidence.trace_kind == "local_hash"
    assert evidence.planner_label == "Reviewed deterministic planner"
    assert evidence.fallback_reason == "model_sql_planning_not_enabled"
    assert evidence.conversation_id is None
    assert evidence.sql_hash is None
    assert evidence.trusted_assets == ()
    assert "No model-generated SQL" in evidence.reasoning_summary


def test_prompt_planner_records_non_model_boundary_in_criteria() -> None:
    workflow, evidence = copilot.plan_growth_agent_prompt(
        GrowthAgentPromptRunRequest(prompt="Find refinance and listed opportunities"),
        settings=_settings(),
    )

    assert workflow.id == "custom_segment_watch"
    payload = evidence.criteria_json()
    assert payload["execution_mode"] == "deterministic"
    assert payload["trace_kind"] == "local_hash"
    assert payload["fallback_reason"] == "model_sql_planning_not_enabled"
    assert "conversation_id" not in payload
    assert "thoughts" not in payload
