from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend.schemas.agent_plan import ComposePlanRequest
from backend.schemas.growth_agent import (
    GrowthAgentPromptRunRequest,
    assert_reviewed_growth_objective,
)
from backend.services.growth_agent_segment_intent import segment_mode_from_prompt
from backend.services.growth_agent_workflows import planned_workflow


@pytest.mark.parametrize(
    ("prompt", "expected_codes", "expected_mode"),
    [
        (
            "Build a custom cohort for borrowers with refi as well as HELOC signals.",
            "itm,permit",
            "all",
        ),
        (
            "Build a custom cohort for borrowers with refi together with HELOC signals.",
            "itm,permit",
            "all",
        ),
        (
            "Build a custom cohort for borrowers with refi plus HELOC signals.",
            "itm,permit",
            "all",
        ),
        (
            "Build a custom cohort for borrowers with refi or HELOC signals and prepare it "
            "for review.",
            "itm,permit",
            "any",
        ),
        (
            "Build a custom cohort across either refi or HELOC segments.",
            "itm,permit",
            "any",
        ),
        (
            "Build a custom cohort for all selected segments: refi, HELOC, and listed.",
            "itm,listed,permit",
            "all",
        ),
        (
            "Build a custom cohort across either of these reviewed signals: refi, HELOC, "
            "listed.",
            "itm,listed,permit",
            "any",
        ),
        (
            "Build the intersection of refi, HELOC, listed segments.",
            "itm,listed,permit",
            "all",
        ),
        (
            "Build the union of refi and HELOC opportunities.",
            "itm,permit",
            "any",
        ),
        (
            "Build a cohort for at least one of refi and HELOC opportunities.",
            "itm,permit",
            "any",
        ),
        (
            "Build a cohort for one or more of refi and HELOC opportunities.",
            "itm,permit",
            "any",
        ),
        (
            "Build a cohort for any refi and HELOC opportunities.",
            "itm,permit",
            "any",
        ),
        (
            "Build a custom cohort for refi, HELOC, or listed signals.",
            "itm,listed,permit",
            "any",
        ),
        (
            "Build the intersection of high equity, listed, and retention signals.",
            "listed,equity,retention",
            "all",
        ),
        (
            "Build a custom cohort for listed and for sale opportunities or refi signals.",
            "itm,listed",
            "any",
        ),
        (
            "Build a custom cohort for either refi or homes listed for sale.",
            "itm,listed",
            "any",
        ),
        (
            "Build the union of refi and homes listed for sale.",
            "itm,listed",
            "any",
        ),
        (
            "Find refi and listed borrowers in IL before the branch review.",
            "itm,listed",
            "all",
        ),
    ],
)
def test_planned_workflow_uses_relationships_between_recognized_segment_mentions(
    prompt: str,
    expected_codes: str,
    expected_mode: str,
) -> None:
    workflow, interpreted_intent = planned_workflow(GrowthAgentPromptRunRequest(prompt=prompt))

    assert workflow.id == "custom_segment_watch"
    assert workflow.route_filters["segment_codes"] == expected_codes
    assert workflow.route_filters["segment_mode"] == expected_mode
    assert interpreted_intent == (
        f"Campaign lens built a custom {expected_mode.upper()} segment workflow."
    )


@pytest.mark.parametrize(
    "prompt",
    [
        "Build a custom cohort for refi and HELOC or listed signals.",
        "Build a custom cohort for both refi or listed candidates.",
        "Build a custom cohort for refi, listed candidates.",
        "Build a custom cohort for refi and prepare it for review, then listed candidates.",
        "Build a custom cohort across either refi and HELOC segments.",
        "Build a custom cohort for refi and prepare a summary, then listed or HELOC signals.",
        "Build a custom cohort for refi or listed, and refi candidates.",
        "Build a custom cohort across either both refi and listed segments.",
        "Build a custom cohort for refi and HELOC. Listed candidates should be reviewed.",
        "Build a custom cohort for refi or HELOC, listed candidates.",
        "Build a custom cohort for refi and HELOC, listed candidates.",
        "Build a custom cohort for refi or HELOC listed candidates.",
        "Build a custom cohort for refi — or HELOC candidates.",
        "Build a custom cohort for refi / or HELOC candidates.",
        "Build a custom cohort for refi | or HELOC candidates.",
        "Build a custom cohort for refi or refi and HELOC candidates.",
        "Build a custom cohort for refi and HELOC or HELOC candidates.",
        "Build a custom cohort for refi and HELOC but exclude borrowers in both.",
        "Do not intersect refi and HELOC candidates.",
        "Build a custom cohort for refi or HELOC but not both.",
        "Build a custom cohort for refi XOR HELOC candidates.",
        "Build a custom cohort for both refi and HELOC, listed candidates.",
        "Build the intersection of refi and HELOC, listed candidates.",
        "Build the union and intersection of refi and HELOC opportunities.",
        "Build the union of both refi and HELOC opportunities.",
        "Build refi and HELOC opportunities in a union.",
        "Build the intersection of refi and HELOC, then use the union.",
    ],
)
def test_planned_workflow_rejects_mixed_or_ambiguous_segment_relationships(
    prompt: str,
) -> None:
    with pytest.raises(HTTPException) as exc_info:
        planned_workflow(GrowthAgentPromptRunRequest(prompt=prompt))

    assert exc_info.value.status_code == 422
    assert "one explicit relationship" in str(exc_info.value.detail)


@pytest.mark.parametrize(
    "prompt",
    [
        "Build a custom cohort for exclusive refi and HELOC candidates.",
        "Build a custom cohort exclusively for refi and HELOC candidates.",
        "Build a mutually exclusive cohort for refi and HELOC candidates.",
        "Build a custom cohort for exactly one of refi and HELOC candidates.",
        "Build a custom cohort for only one of refi and HELOC candidates.",
        "Build a custom cohort for refi and HELOC but omit dual matches.",
        "Build a custom cohort for refi and HELOC while omitting dual matches.",
        "Build a custom cohort for refi and HELOC with omitted dual matches.",
        "Build a custom cohort for refi and HELOC but leave out dual matches.",
        "Build a custom cohort for anything but dual matches across refi and HELOC.",
        "Build a custom cohort with no dual matches across refi and HELOC.",
        "Build a custom cohort that doesn't combine refi and HELOC.",
        "Build a custom cohort for refi and HELOC; avoid intersection.",
        "Build a custom cohort for refi and HELOC, excluding dual matches.",
        "Avoid the intersection of refi and HELOC opportunities.",
        "Build refi or HELOC opportunities, omitting borrowers in both segments.",
        "Omit borrowers in both refi and HELOC segments.",
        "Leave out borrowers in both refi and HELOC segments.",
        "Anything but borrowers in both refi and HELOC segments.",
        "Build a cohort that doesn't combine refi and HELOC.",
        "Build a custom cohort for one or the other of refi and HELOC.",
        "Build a custom cohort for one-or-the-other of refi and HELOC.",
        "Build a custom cohort for refi or HELOC, never both.",
        "Build a custom cohort for refi or HELOC, never-both.",
        "Build a custom cohort for refi or HELOC with no overlap.",
        "Build a custom cohort for refi or HELOC with zero-overlap.",
        "Build disjoint refi and HELOC cohorts.",
        "Build refi and HELOC cohorts disjointly.",
        "Build a custom cohort for at-most-one of refi and HELOC.",
        "Build separate refi and HELOC cohorts.",
        "Build refi and HELOC opportunities separately.",
        "Build non-overlapping refi and HELOC cohorts.",
        "Build overlap-free refi and HELOC cohorts.",
        "Build the union of refi and HELOC, minus overlap.",
        "Build refi or HELOC opportunities and strip double matches.",
        "Build refi or HELOC opportunities and remove overlapping borrowers.",
        "Build refi or HELOC opportunities and drop dual matches.",
        "Build refi or HELOC opportunities and skip overlaps.",
        "Build refi or HELOC opportunities and filter out shared borrowers.",
    ],
)
def test_raw_segment_parser_rejects_exclusive_relationships(prompt: str) -> None:
    with pytest.raises(HTTPException) as exc_info:
        segment_mode_from_prompt(prompt)

    assert exc_info.value.status_code == 422
    assert "one explicit relationship" in str(exc_info.value.detail)


@pytest.mark.parametrize(
    "prompt",
    [
        "Build the union of refi and HELOC opportunities.",
        "Build a custom cohort for either refi or HELOC candidates.",
    ],
)
def test_raw_segment_parser_maps_supported_union_grammar_to_any(prompt: str) -> None:
    assert segment_mode_from_prompt(prompt) == "any"


@pytest.mark.parametrize(
    ("prompt", "expected_mode"),
    [
        (
            "Assemble a reviewed cohort across both refi together with HELOC signals "
            "for human review.",
            "all",
        ),
        (
            "Identify a reviewed cohort across either refi or HELOC opportunities and "
            "prepare them for review.",
            "any",
        ),
        (
            "Find refi and listed borrowers in IL before the branch review.",
            "all",
        ),
    ],
)
def test_closed_clause_parser_accepts_reviewed_prefix_gap_and_suffix_controls(
    prompt: str,
    expected_mode: str,
) -> None:
    assert segment_mode_from_prompt(prompt) == expected_mode


def test_raw_segment_parser_rejects_disconnected_newline() -> None:
    with pytest.raises(HTTPException) as exc_info:
        segment_mode_from_prompt("Build a custom cohort for refi\nor HELOC candidates.")

    assert exc_info.value.status_code == 422
    assert "one explicit relationship" in str(exc_info.value.detail)


def test_safe_multiline_prompt_preserves_clause_boundary_without_blocking_mode() -> None:
    payload = GrowthAgentPromptRunRequest(
        prompt="Build a custom cohort\nfor refi or HELOC candidates."
    )

    workflow, _ = planned_workflow(payload)

    assert payload.prompt == "Build a custom cohort; for refi or HELOC candidates."
    assert workflow.route_filters["segment_mode"] == "any"


def test_multiline_prompt_safety_still_scans_the_flat_text() -> None:
    with pytest.raises(ValueError, match="reviewed, non-PII"):
        assert_reviewed_growth_objective("Run this for John\nSmith refi opportunities.")


def test_explicit_reviewed_segment_mode_remains_authoritative_for_ambiguous_prose() -> None:
    workflow, interpreted_intent = planned_workflow(
        GrowthAgentPromptRunRequest(
            prompt="Build a custom cohort for refi, listed candidates.",
            segment_codes=["itm", "listed"],
            segment_mode="all",
        )
    )

    assert workflow.route_filters["segment_codes"] == "itm,listed"
    assert workflow.route_filters["segment_mode"] == "all"
    assert interpreted_intent == "Custom reviewed segment workflow using ALL semantics."


@pytest.mark.parametrize("segment_mode", ["any", "all"])
def test_explicit_fields_accept_safe_prompts_with_zero_or_one_segment_mention(
    segment_mode: str,
) -> None:
    workflow, interpreted_intent = planned_workflow(
        GrowthAgentPromptRunRequest(
            prompt="Build a reviewed custom cohort for review.",
            segment_codes=["itm", "permit"],
            segment_mode=segment_mode,
        )
    )

    assert workflow.route_filters["segment_codes"] == "itm,permit"
    assert workflow.route_filters["segment_mode"] == segment_mode
    assert interpreted_intent == (
        f"Custom reviewed segment workflow using {segment_mode.upper()} semantics."
    )


def test_explicit_fields_reject_a_conflicting_supported_relationship() -> None:
    with pytest.raises(HTTPException) as exc_info:
        planned_workflow(
            GrowthAgentPromptRunRequest(
                prompt="Build a custom cohort for refi or HELOC candidates.",
                segment_codes=["itm", "permit"],
                segment_mode="all",
            )
        )

    assert exc_info.value.status_code == 422
    assert "one explicit relationship" in str(exc_info.value.detail)

@pytest.mark.parametrize(
    "prompt",
    [
        "Build a custom cohort for refi or HELOC but not both.",
        "Build the union of refi and HELOC, minus overlap.",
        "Build disjoint refi and HELOC cohorts.",
        "Build refi or HELOC opportunities and strip double matches.",
        "Build refi or HELOC opportunities and remove overlapping borrowers.",
        "Build refi or HELOC opportunities and drop dual matches.",
        "Build refi or HELOC opportunities and skip overlaps.",
        "Build refi or HELOC opportunities and filter out shared borrowers.",
    ],
)
def test_explicit_reviewed_fields_cannot_override_unsupported_set_semantics(
    prompt: str,
) -> None:
    with pytest.raises(HTTPException) as exc_info:
        planned_workflow(
            GrowthAgentPromptRunRequest(
                prompt=prompt,
                segment_codes=["itm", "permit"],
                segment_mode="any",
            )
        )

    assert exc_info.value.status_code == 422
    assert "one explicit relationship" in str(exc_info.value.detail)


def test_unsupported_relationship_precedes_broad_health_copy_scan() -> None:
    prompt = "Build a custom cohort with no dual matches across refi and HELOC."

    with pytest.raises(HTTPException) as exc_info:
        GrowthAgentPromptRunRequest(prompt=prompt)

    assert exc_info.value.status_code == 422
    assert str(exc_info.value.detail).startswith("Multiple reviewed segments require")


def test_typed_fields_still_resolve_reviewed_ambiguous_segment_prose() -> None:
    request = GrowthAgentPromptRunRequest(
        prompt="Build a custom cohort for refi, listed candidates.",
        segment_codes=["itm", "listed"],
        segment_mode="all",
    )

    assert request.segment_codes == ["itm", "listed"]
    assert request.segment_mode == "all"


def test_unsupported_relationship_precedence_cannot_mask_health_criteria() -> None:
    prompt = (
        "Build a custom cohort with no dual matches across refi and HELOC. "
        "Recipients have zyrplax."
    )

    with pytest.raises(ValueError, match="reviewed, non-PII"):
        GrowthAgentPromptRunRequest(prompt=prompt)


@pytest.mark.parametrize(
    "prompt",
    [
        "Build the symmetric difference of refi and HELOC.",
        "Build a cohort where each borrower is in just one of refi and HELOC.",
        "Build a cohort where each borrower is in only one of refi and HELOC.",
        "Build refi or HELOC opportunities and eliminate borrowers in both.",
        "Build the union of refi and HELOC and subtract common members.",
        "Build the union of refi and HELOC, subtracting the intersection.",
        "Build refi or HELOC opportunities and discard shared members.",
        "Build refi or HELOC opportunities and prune overlap.",
        "Build refi or HELOC opportunities and prune overlaps.",
        "Build refi or HELOC opportunities and prune overlapping borrowers.",
        "Build refi or HELOC opportunities and delete dual matches.",
        "Build refi or HELOC opportunities and deduplicate the overlap.",
        "Build a distinct-membership cohort for refi and HELOC.",
        "Build refi or HELOC opportunities after subtracting common borrowers.",
    ],
)
def test_closed_clause_parser_rejects_unreviewed_set_relationship_language(
    prompt: str,
) -> None:
    with pytest.raises(HTTPException) as exc_info:
        segment_mode_from_prompt(prompt)

    assert exc_info.value.status_code == 422
    assert "one explicit relationship" in str(exc_info.value.detail)


@pytest.mark.parametrize(
    "prompt",
    [
        "Find refinance leads but not refi borrowers.",
        "Find refi leads excluding refinance borrowers.",
        "Find cash-out candidates but not high equity borrowers.",
        "Find borrowers excluding refinance leads.",
        "Find borrowers without refi signals.",
        "Find borrowers other than refinance leads.",
    ],
)
def test_planner_rejects_negative_same_or_single_segment_aliases(prompt: str) -> None:
    with pytest.raises(HTTPException) as exc_info:
        planned_workflow(GrowthAgentPromptRunRequest(prompt=prompt))

    assert exc_info.value.status_code == 422


@pytest.mark.parametrize("explicit_fields", [False, True])
@pytest.mark.parametrize(
    "prompt",
    [
        "Lack refi signals.",
        "Aren't refi candidates.",
        "Zero refi signals.",
        "Refi absent.",
    ],
)
def test_single_segment_parser_requires_closed_affirmative_grammar(
    prompt: str,
    explicit_fields: bool,
) -> None:
    payload: dict[str, object] = {"prompt": prompt}
    if explicit_fields:
        payload.update({"segment_codes": ["itm"], "segment_mode": "any"})

    with pytest.raises(HTTPException) as exc_info:
        planned_workflow(GrowthAgentPromptRunRequest(**payload))

    assert exc_info.value.status_code == 422
    assert "affirmative segment criteria" in str(exc_info.value.detail)


@pytest.mark.parametrize(
    "prompt",
    [
        "Find prime refinance opportunities in Illinois for branch review.",
        "Find refinance opportunities for weekly monitoring.",
        "Top 10 prime refi candidates.",
        "Review residents dealing with high mortgage rates for refinance options.",
        "Review homeowners with current loan balances for refinance options.",
        "Review borrowers with strong rate spreads for refinance options.",
        "Build a refi cohort; health information is excluded from campaign eligibility.",
        "Review refi economics and mortgage underwriting conditions.",
    ],
)
def test_single_segment_parser_preserves_catalogued_product_prompts(prompt: str) -> None:
    workflow, _ = planned_workflow(GrowthAgentPromptRunRequest(prompt=prompt))

    expected = "daily_refi_brief" if "branch review" in prompt.lower() else "custom_segment_watch"
    assert workflow.id == expected


def test_same_segment_aliases_are_coalesced_for_safe_listing_language() -> None:
    workflow, _ = planned_workflow(
        GrowthAgentPromptRunRequest(
            prompt="Track listed-for-sale purchase opportunities in Illinois."
        )
    )

    assert workflow.id == "listing_watch"
    assert workflow.route_filters["segment"] == "listed"


@pytest.mark.parametrize(
    ("prompt", "segment"),
    [
        ("Find listed candidates.", "listed"),
        ("Find high equity candidates.", "equity"),
        ("Find HELOC candidates.", "permit"),
        ("Find retention candidates.", "retention"),
        ("Find current customer opportunities.", "retention"),
    ],
)
def test_generic_segment_phrases_remain_exact_custom_workflows(
    prompt: str,
    segment: str,
) -> None:
    workflow, _ = planned_workflow(GrowthAgentPromptRunRequest(prompt=prompt))

    assert workflow.id == "custom_segment_watch"
    assert workflow.route_filters["segment"] == segment


def test_segment_phrase_overlap_does_not_invent_a_second_segment() -> None:
    workflow, _ = planned_workflow(
        GrowthAgentPromptRunRequest(prompt="Find home equity line opportunities.")
    )

    assert workflow.id == "custom_segment_watch"
    assert workflow.route_filters["segment"] == "permit"


@pytest.mark.parametrize(
    ("prompt", "segment_code"),
    [
        ("Find HELOC candidates.", "permit"),
        ("Find retention candidates.", "retention"),
        ("Find current customer opportunities.", "retention"),
    ],
)
def test_inferred_single_segments_match_explicit_predicates_and_routes(
    prompt: str,
    segment_code: str,
) -> None:
    inferred, _ = planned_workflow(GrowthAgentPromptRunRequest(prompt=prompt))
    explicit, _ = planned_workflow(
        GrowthAgentPromptRunRequest(
            prompt=prompt,
            segment_codes=[segment_code],  # type: ignore[list-item]
            segment_mode="any",
        )
    )

    assert inferred.id == explicit.id == "custom_segment_watch"
    assert inferred.broad_predicate == explicit.broad_predicate
    assert inferred.actionable_predicate == explicit.actionable_predicate
    assert inferred.route_filters == explicit.route_filters == {
        "segment": segment_code,
        "marketing_eligibility": "Eligible only",
    }


@pytest.mark.parametrize(
    "objective",
    [
        "Lack qualifying signals.",
        "Without competitor signal.",
    ],
)
def test_zero_recognized_segment_absence_fails_shared_run_and_compose_boundary(
    objective: str,
) -> None:
    with pytest.raises(HTTPException, match="affirmative segment criteria"):
        GrowthAgentPromptRunRequest(prompt=objective)
    with pytest.raises(HTTPException, match="affirmative segment criteria"):
        ComposePlanRequest(objective=objective)


@pytest.mark.parametrize("explicit_codes", [["listed"], ["listed", "permit"]])
def test_explicit_segment_codes_must_exactly_match_prompt_mentions(
    explicit_codes: list[str],
) -> None:
    with pytest.raises(HTTPException, match="exactly match"):
        GrowthAgentPromptRunRequest(
            prompt="Find refi opportunities.",
            segment_codes=explicit_codes,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("prompt", "workflow_id", "segment_filter"),
    [
        (
            "Find prime refinance and listed-for-sale opportunities across current coverage.",
            "custom_segment_watch",
            "itm,listed",
        ),
        (
            "Find prime refinance opportunities for a branch manager review.",
            "daily_refi_brief",
            "itm",
        ),
        (
            "Find prime refinance opportunities for a branch manager monitor.",
            "daily_refi_brief",
            "itm",
        ),
        ("Find high equity candidates.", "custom_segment_watch", "equity"),
        ("Find investor candidates.", "custom_segment_watch", "investor"),
        ("Find HELOC candidates.", "custom_segment_watch", "permit"),
        ("Show HELOC opportunities.", "custom_segment_watch", "permit"),
    ],
)
def test_visible_and_e2e_prompts_route_to_exact_reviewed_contract(
    prompt: str,
    workflow_id: str,
    segment_filter: str,
) -> None:
    workflow, _ = planned_workflow(GrowthAgentPromptRunRequest(prompt=prompt))

    assert workflow.id == workflow_id
    assert workflow.route_filters.get("segment_codes", workflow.route_filters.get("segment")) == (
        segment_filter
    )


@pytest.mark.parametrize(
    "prompt",
    [
        "Find unlisted candidates.",
        "Show refi candidates below 75 bps.",
        "Show refi candidates above 125 bps.",
        "Show prime refinance opportunities over 75 bps in Illinois.",
        "Show refi candidates under 75 bps.",
    ],
)
def test_unrepresented_direction_and_thresholds_fail_closed(prompt: str) -> None:
    with pytest.raises(HTTPException, match="affirmative segment criteria"):
        GrowthAgentPromptRunRequest(prompt=prompt)
    with pytest.raises(HTTPException, match="affirmative segment criteria"):
        ComposePlanRequest(objective=prompt)
