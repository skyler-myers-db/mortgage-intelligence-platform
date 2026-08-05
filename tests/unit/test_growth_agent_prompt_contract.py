from pathlib import Path

import pytest

from backend.schemas.growth_agent import GrowthAgentPromptRunRequest
from backend.services.growth_agent_workflows import planned_workflow

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("relative_path", "prompt", "workflow_id"),
    [
        (
            "frontend/src/routes/ask-genie.tsx",
            "Find prime refinance and listed-for-sale opportunities across current coverage.",
            "custom_segment_watch",
        ),
        (
            "frontend/tests/e2e/growth_agent_live.spec.ts",
            "Find prime refinance opportunities for a branch manager review.",
            "daily_refi_brief",
        ),
        (
            "frontend/tests/e2e/growth_agent_live.spec.ts",
            "Find prime refinance opportunities for a branch manager monitor.",
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
