"""Every Genie page-context template is pre-validated through the real guards.

``frontend/src/lib/genieContextTemplates.json`` is the single source for the
floating Genie panel's per-route starters and its "Ask Genie about this"
prompts (audit 2026-09-21 genie-04, phase 1). The UI PREFILLS the composer
with one of them and never submits it, so the prompt of record is exactly
what the user sends -- but a prefill the guards refuse would be a dead end the
product itself handed the user. The guard battery is Python-only, so this test
is the validation the UI cannot run itself: every rendering (every federal
state x every segment the frontend registry carries) must pass every
deterministic pre-Genie prompt matcher, the outreach-writer detector, and the
request model's own text validator.

The governed place dimension is resolved with no gold cities, which is the
production state after the startup warm: US states join the fair-lending
candidate pool through the admission gate (``Oklahoma`` hits the ``-oma``
condition morphology and is exempted there, never here). Guard code is not
touched; this test only reads it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from backend.services.genie_deterministic import _is_outreach_writer_request
from backend.services.genie_message_policy import (
    GenieMessageRequest,
    identity_prompt_match,
    protected_prompt_match,
)
from backend.services.genie_place_dimension import (
    GovernedPlaceDimensionResolver,
    _reset_governed_place_dimension_for_tests,
)
from backend.services.genie_prompt_guardrails import (
    cross_lender_prompt_match,
    instruction_override_prompt_match,
    off_topic_prompt_match,
    pii_prompt_match,
    scope_bypass_prompt_match,
    source_gap_prompt_match,
)
from backend.services.genie_sales_ops import _sales_ops_question_kind

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_PATH = ROOT / "frontend" / "src" / "lib" / "genieContextTemplates.json"
SEGMENT_REGISTRY_PATH = ROOT / "frontend" / "src" / "lib" / "segmentMetadata.ts"
ANSWER_PANEL_PATH = ROOT / "frontend" / "src" / "routes" / "ask-genie.answer-panel.tsx"
_COMPOSER_PLACEHOLDER_RE = re.compile(r"export const COMPOSER_PLACEHOLDER = '([^']+)';")

_PROMPT_MATCHERS: tuple[tuple[str, Callable[[str], object]], ...] = (
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

_SEGMENT_ENTRY_RE = re.compile(r"code:\s*'([a-z_]+)',\s*name:\s*'([^']+)'")


def _templates() -> dict[str, object]:
    data = json.loads(TEMPLATES_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _segment_registry() -> dict[str, str]:
    """``code -> name`` from the frontend registry the templates interpolate."""

    source = SEGMENT_REGISTRY_PATH.read_text(encoding="utf-8")
    return dict(_SEGMENT_ENTRY_RE.findall(source))


def _string_map(key: str) -> dict[str, str]:
    value = _templates()[key]
    assert isinstance(value, dict), key
    return {str(k): str(v) for k, v in value.items()}


def _template(key: str) -> str:
    value = _templates()[key]
    assert isinstance(value, str), key
    return value


def _segment_phrases() -> dict[str, str]:
    """The phrase the frontend renders per code (see genieContext.ts segmentPhrase):
    the ``segmentPhraseByCode`` override, else the lower-cased registry name."""

    excluded = _string_map("segmentsWithoutTemplates")
    overrides = _string_map("segmentPhraseByCode")
    return {
        code: overrides.get(code, name.lower())
        for code, name in _segment_registry().items()
        if code not in excluded
    }


def _code_templates() -> dict[str, dict[str, str]]:
    """``segmentTemplatesByCode``: the per-code sentences that replace the generic two."""

    value = _templates()["segmentTemplatesByCode"]
    assert isinstance(value, dict)
    return {str(code): {str(k): str(v) for k, v in entry.items()} for code, entry in value.items()}


def _segment_template_for(code: str) -> str:
    return _code_templates().get(code, {}).get("segmentTemplate", _template("segmentTemplate"))


def _lead_template_for(code: str) -> str:
    return _code_templates().get(code, {}).get("leadTemplate", _template("leadTemplate"))


def _state_names() -> list[str]:
    return list(_string_map("stateNames").values())


def _intercepted(prompt: str) -> list[str]:
    return [name for name, matcher in _PROMPT_MATCHERS if matcher(prompt)]


@pytest.fixture(autouse=True)
def _resolved_place_dimension() -> Iterator[None]:
    _reset_governed_place_dimension_for_tests(
        GovernedPlaceDimensionResolver(dimension_reader=lambda: [])
    )
    yield
    _reset_governed_place_dimension_for_tests(None)


def test_the_segment_registry_parse_is_real() -> None:
    registry = _segment_registry()
    # The frontend registry carries the six core segments plus the overlays.
    assert len(registry) >= 12
    assert registry["itm"] == "In the Money"
    assert "permit_activity" in registry
    for code in _string_map("segmentsWithoutTemplates"):
        assert code in registry, code


def test_the_battery_bites_on_a_protected_class_prompt() -> None:
    """Non-vacuity: the same fixture and matchers refuse a fair-lending prompt."""

    assert _intercepted("Which states have the most elderly borrowers?") != []


def _composer_example() -> str:
    """The example the Ask composer's placeholder teaches, exactly as a user would type it."""

    match = _COMPOSER_PLACEHOLDER_RE.search(ANSWER_PANEL_PATH.read_text(encoding="utf-8"))
    assert match is not None, "COMPOSER_PLACEHOLDER literal not found"
    _, marker, example = match.group(1).partition("e.g. ")
    assert marker and example, match.group(1)
    return example


def test_the_ask_composer_example_passes_the_real_prompt_guards() -> None:
    """critic-11 / item 22: the Ask page never teaches a question Genie refuses.

    The placeholder is not prefilled, but its example is what a user copies.
    """

    example = _composer_example()
    assert "in-the-money" in example, example  # the parse reads the real literal
    assert _intercepted(example) == []
    assert GenieMessageRequest(question=example).question == example
    assert _sales_ops_question_kind(example) is None


def test_every_fixed_prompt_passes_the_real_prompt_guards() -> None:
    fixed = {**_string_map("starters"), **_string_map("kpiPrompts")}
    failures = {key: hits for key, prompt in fixed.items() if (hits := _intercepted(prompt))}
    assert failures == {}
    for prompt in fixed.values():
        assert GenieMessageRequest(question=prompt).question == prompt


def test_the_itm_overrides_are_real() -> None:
    """critic-11: itm renders its own prenominal sentences, never 'the in the money segment'."""

    assert _segment_phrases()["itm"] == "in-the-money"
    assert _segment_template_for("itm") == "Which states have the most in-the-money borrowers?"
    lead = _lead_template_for("itm").replace("{state}", "Texas")
    assert lead == "How many in-the-money borrowers are in Texas, and what is the average rate spread?"
    assert set(_code_templates()) <= set(_segment_phrases())
    # The legacy KPI keys stay beside the canonical one, all on one prompt.
    kpi = _string_map("kpiPrompts")
    assert kpi["in the money"] == kpi["refi economics screen"] == kpi["refi economics"]


def test_every_segment_prompt_passes_the_real_prompt_guards() -> None:
    failures: dict[str, list[str]] = {}
    for code, phrase in _segment_phrases().items():
        prompt = _segment_template_for(code).replace("{segment}", phrase)
        if hits := _intercepted(prompt):
            failures[code] = hits
        GenieMessageRequest(question=prompt)
    assert failures == {}


@pytest.mark.parametrize("state", _state_names())
def test_every_state_and_lead_prompt_passes_the_real_prompt_guards(state: str) -> None:
    prompts = [_template("stateTemplate").replace("{state}", state)]
    prompts.extend(
        _lead_template_for(code).replace("{state}", state).replace("{segment}", phrase)
        for code, phrase in _segment_phrases().items()
    )
    failures = {prompt: hits for prompt in prompts if (hits := _intercepted(prompt))}
    assert failures == {}
    for prompt in prompts:
        assert GenieMessageRequest(question=prompt).question == prompt


def test_each_excluded_segment_is_still_refused() -> None:
    """The exclusion list stays honest: drop an entry once the guards accept it."""

    template = _template("segmentTemplate")
    registry = _segment_registry()
    for code in _string_map("segmentsWithoutTemplates"):
        prompt = template.replace("{segment}", registry[code].lower())
        assert _intercepted(prompt) != [], f"{code} is answerable now; remove it from the exclusions"


def _reachable_prompts() -> dict[str, str]:
    """Every prompt the panel can prefill without the user typing a word."""

    starters = _string_map("starters")
    route_starters = _templates()["routeStarters"]
    assert isinstance(route_starters, dict)
    prompts: dict[str, str] = {}
    for route, keys in route_starters.items():
        assert isinstance(keys, list), route
        for key in keys:
            prompts[f"routeStarters[{route}].{key}"] = starters[str(key)]
    for key, prompt in _string_map("kpiPrompts").items():
        prompts[f"kpiPrompts.{key}"] = prompt
    phrases = _segment_phrases()
    for code, phrase in phrases.items():
        prompts[f"segmentTemplate.{code}"] = _segment_template_for(code).replace("{segment}", phrase)
    for state in _state_names():
        prompts[f"stateTemplate.{state}"] = _template("stateTemplate").replace("{state}", state)
        for code, phrase in phrases.items():
            lead = _lead_template_for(code).replace("{state}", state)
            prompts[f"leadTemplate.{state}.{code}"] = lead.replace("{segment}", phrase)
    return prompts


def test_the_sales_ops_classifier_bites_on_a_gated_question() -> None:
    """Non-vacuity: the classifier below does route a Sales Ops question."""

    assert _sales_ops_question_kind("Show approved leads that have not been touched in 7 days.") == "aging"


def test_no_prefilled_prompt_is_a_role_gated_sales_ops_question() -> None:
    """A prefill that 403s for most actors is a dead end the product handed them.

    Sales Ops questions route to an adapter that requires an active sales
    manager or admin; there is no client-side role signal for that today, so
    no route starter, KPI prompt or template rendering may be one of them.
    """

    gated = {
        where: kind
        for where, prompt in _reachable_prompts().items()
        if (kind := _sales_ops_question_kind(prompt)) is not None
    }
    assert gated == {}
