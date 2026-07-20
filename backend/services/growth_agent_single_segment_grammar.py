"""Compatibility exports for the schema-owned Growth segment grammar."""

from backend.schemas.growth_agent_single_segment_grammar import (
    SUPPORTED_REVIEW_SUFFIXES,
    contains_only_affirmative_prefix,
    contains_only_affirmative_suffix,
    contains_only_affirmative_unsegmented_objective,
    without_supported_coverage_scope,
    without_supported_state_scope,
)

__all__ = [
    "SUPPORTED_REVIEW_SUFFIXES",
    "contains_only_affirmative_prefix",
    "contains_only_affirmative_suffix",
    "contains_only_affirmative_unsegmented_objective",
    "without_supported_coverage_scope",
    "without_supported_state_scope",
]
