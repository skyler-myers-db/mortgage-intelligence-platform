from pathlib import Path

import pytest

from backend.schemas.growth_agent import GrowthAgentPromptRunRequest
from backend.services.growth_agent_workflows import planned_workflow

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("relative_path", "prompt", "workflow_id"),
    [
        (
            "frontend/src/routes/ask-genie.growth-agent-state.ts",
            "Find In the Money and listed-for-sale opportunities across current coverage.",
            "custom_segment_watch",
        ),
        (
            "frontend/tests/e2e/growth_agent_live.spec.ts",
            "Find prime refinance opportunities for a branch manager review.",
            "daily_refi_brief",
        ),
        (
            "frontend/src/routes/ask-genie.growth-agent.saved-watchlists.test.tsx",
            "Find refinance opportunities for branch follow-up.",
            "daily_refi_brief",
        ),
    ],
)
def test_frontend_growth_prompts_are_pinned_to_reviewed_backend_routes(
    relative_path: str,
    prompt: str,
    workflow_id: str,
) -> None:
    assert prompt in (REPO / relative_path).read_text(encoding="utf-8")

    workflow, _ = planned_workflow(GrowthAgentPromptRunRequest(prompt=prompt))

    assert workflow.id == workflow_id


# critic-11: the default objective's wording changed from "prime refinance" to
# the canonical label; the plan it selects must not. (The prenominal "Find
# in-the-money refinance and listed-for-sale ..." is refused by the planner's
# relationship grammar with a 422, so the label form is used.)
_OLD_DEFAULT_OBJECTIVE = "Find prime refinance and listed-for-sale opportunities across current coverage."
_NEW_DEFAULT_OBJECTIVE = "Find In the Money and listed-for-sale opportunities across current coverage."


def _plan_signature(prompt: str) -> tuple[str, tuple[str, ...], str | None]:
    """Workflow id, segment codes and mode: what the reviewed plan executes."""
    workflow, _ = planned_workflow(GrowthAgentPromptRunRequest(prompt=prompt))
    filters = workflow.route_filters
    codes = tuple(str(filters.get("segment_codes") or filters.get("segment") or "").split(","))
    mode = filters.get("segment_mode")
    return workflow.id, codes, (str(mode) if mode is not None else None)


def test_the_reworded_default_objective_plans_the_same_workflow() -> None:
    old = _plan_signature(_OLD_DEFAULT_OBJECTIVE)
    new = _plan_signature(_NEW_DEFAULT_OBJECTIVE)
    assert new == old
    assert new[0] == "custom_segment_watch"
    assert set(new[1]) == {"itm", "listed"}
    assert new[2] == "all"
