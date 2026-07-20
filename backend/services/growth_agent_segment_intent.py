"""Compatibility exports for schema-owned Growth segment intent policy."""

from backend.schemas.growth_agent_segment_intent import (
    reject_unsupported_segment_relationships,
    require_affirmative_unsegmented_objective,
    segment_mode_from_prompt,
    segments_from_prompt,
)

__all__ = [
    "reject_unsupported_segment_relationships",
    "require_affirmative_unsegmented_objective",
    "segment_mode_from_prompt",
    "segments_from_prompt",
]
