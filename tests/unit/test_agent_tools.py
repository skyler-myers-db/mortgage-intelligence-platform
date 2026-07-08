from __future__ import annotations

import pytest

from backend.services import growth_agent_runtime
from backend.services.agent_tools import (
    assert_tool_allowed_for_specialist,
    get_agent_tool,
    registered_agent_tool_names,
    registered_agent_tools,
)
from backend.services.audit_metadata_value_policy import validate_source_assets
from backend.services.growth_agent_workflows import WORKFLOWS


def test_growth_agent_tool_registry_is_reviewed_and_deterministic() -> None:
    names = registered_agent_tool_names()
    assert {
        "fn_build_cohort",
        "fn_segment_counts",
        "fn_lead_queue_url",
        "fn_borrower_dossier_evidence",
        "fn_property_loan_lookup",
        "fn_source_readiness",
        "source_readiness_status_rollup",
        "open_admin_data_operations",
    } <= names

    for tool in registered_agent_tools():
        assert tool.deterministic is True
        assert tool.writes_state is False
        assert tool.specialists
        validate_source_assets([tool.source_asset])
        assert ".silver." not in tool.source_asset
        assert not tool.source_asset.startswith("cotality_mortgage_data.")


def test_growth_agent_tool_registry_rejects_unknown_or_wrong_specialist_tools() -> None:
    with pytest.raises(ValueError):
        get_agent_tool("run_arbitrary_sql")

    with pytest.raises(ValueError):
        assert_tool_allowed_for_specialist("fn_source_readiness", "campaign_agent")

    tool = assert_tool_allowed_for_specialist(
        "fn_borrower_dossier_evidence",
        "borrower_dossier_agent",
    )
    assert tool.source_asset == "mip.gold.borrower_dossier"

    # Property loan lookup is reviewed for the dossier specialist only and
    # binds to the governed address_lookup spine (never silver / raw share).
    lookup_tool = assert_tool_allowed_for_specialist(
        "fn_property_loan_lookup",
        "borrower_dossier_agent",
    )
    assert lookup_tool.source_asset == "mip.gold.address_lookup"
    with pytest.raises(ValueError):
        assert_tool_allowed_for_specialist("fn_property_loan_lookup", "campaign_agent")


def test_growth_agent_runtime_refuses_state_writing_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    base_tool = get_agent_tool("fn_build_cohort")

    class _StateWritingTool:
        name = base_tool.name
        source_asset = base_tool.source_asset
        writes_state = True

    monkeypatch.setattr(
        growth_agent_runtime,
        "assert_tool_allowed_for_specialist",
        lambda _name, _specialist: _StateWritingTool(),
    )

    with pytest.raises(ValueError, match="writes state"):
        growth_agent_runtime._reviewed_tool_step(  # noqa: SLF001 - executable boundary test
            WORKFLOWS["daily_refi_brief"],
            "fn_build_cohort",
            label="Bad state-writing tool",
            status="completed",
            detail="Should be rejected before display.",
            result_hash="a" * 64,
        )
