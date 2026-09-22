"""The refusal card's rephrase chips are pre-validated through the real guards.

``frontend/src/components/mortgage/genieRefusalChips.json`` is the single
source for the card's "ask this instead" chips (audit 2026-09-21 genie-05).
The guard battery is Python-only, so this test is the validation the UI
cannot run itself: every chip must pass every deterministic pre-Genie prompt
matcher, the outreach-writer detector, and the request model's own text
validator, and the JSON families must equal the wire enum exactly.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.services.genie_deterministic import _is_outreach_writer_request
from backend.services.genie_message_policy import (
    GenieMessageRequest,
    identity_prompt_match,
    protected_prompt_match,
)
from backend.services.genie_prompt_guardrails import (
    cross_lender_prompt_match,
    instruction_override_prompt_match,
    off_topic_prompt_match,
    pii_prompt_match,
    scope_bypass_prompt_match,
    source_gap_prompt_match,
)
from backend.services.genie_refusal_reason import GENIE_REFUSAL_REASONS

ROOT = Path(__file__).resolve().parents[2]
CHIPS_PATH = ROOT / "frontend" / "src" / "components" / "mortgage" / "genieRefusalChips.json"

_PROMPT_MATCHERS = (
    ("protected", protected_prompt_match),
    ("identity", identity_prompt_match),
    ("pii", pii_prompt_match),
    ("off_topic", off_topic_prompt_match),
    ("scope_bypass", scope_bypass_prompt_match),
    ("instruction_override", instruction_override_prompt_match),
    ("cross_lender", cross_lender_prompt_match),
    ("source_gap", source_gap_prompt_match),
    ("outreach_writer", _is_outreach_writer_request),
)


def _chips() -> dict[str, list[str]]:
    data = json.loads(CHIPS_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return {str(family): list(chips) for family, chips in data.items()}


def _all_chips() -> list[tuple[str, str]]:
    return [(family, chip) for family, chips in _chips().items() for chip in chips]


def test_chip_families_match_the_wire_enum_exactly() -> None:
    assert set(_chips()) == set(GENIE_REFUSAL_REASONS)


def test_every_family_offers_two_to_three_distinct_chips() -> None:
    for family, chips in _chips().items():
        assert 2 <= len(chips) <= 3, family
        assert len(set(chips)) == len(chips), family
        for chip in chips:
            assert isinstance(chip, str) and chip.strip() == chip and chip, family


@pytest.mark.parametrize(("family", "chip"), _all_chips())
def test_every_chip_passes_the_real_prompt_guards(family: str, chip: str) -> None:
    intercepted = [name for name, matcher in _PROMPT_MATCHERS if matcher(chip)]
    assert intercepted == [], f"{family}: guard(s) {intercepted} intercepted chip {chip!r}"
    assert GenieMessageRequest(question=chip).question == chip
