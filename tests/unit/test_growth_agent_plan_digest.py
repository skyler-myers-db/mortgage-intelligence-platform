"""The server plan digest binds a composed plan to its review (audit critic-01).

``issue_plan_digest`` signs the canonical plan with the actor, the reviewed
objective and the state scope; ``verify_plan_digest`` must reject any change
to any of them, an expired or future-dated digest, a digest from another
actor, and any wire string that is not one it could have issued.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from pydantic import SecretStr

import backend.services.growth_agent_plan_digest as digest_module
from backend.config.settings import settings
from backend.schemas.agent_plan import ComposedPlan, PlanStep
from backend.services.growth_agent_plan_digest import (
    PLAN_DIGEST_TTL_S,
    PlanDigestConflict,
    PlanDigestUnavailable,
    canonical_plan_json,
    issue_plan_digest,
    verify_plan_digest,
)

ACTOR = "loan.officer@summit-mortgage.example"
OTHER_ACTOR = "branch.manager@summit-mortgage.example"
OBJECTIVE = "Compose a refi growth plan for review."
STATES = ["IL", "TX"]
NOW = 1_790_000_000


@pytest.fixture(autouse=True)
def _signing_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mip_genie_action_secret", None)
    monkeypatch.setattr(
        settings, "mip_genie_action_secret_current", SecretStr("plan-digest-current-key-0123456789")
    )
    monkeypatch.setattr(settings, "mip_genie_action_secret_kid", "v7")
    monkeypatch.setattr(settings, "mip_genie_action_secret_previous", None)
    monkeypatch.setattr(settings, "mip_genie_action_secret_previous_kid", None)


def _plan(**overrides: Any) -> ComposedPlan:
    fields: dict[str, Any] = {
        "objective_summary": "Screen refi economics, gate to eligible, then hand off for review.",
        "steps": [
            PlanStep(step_id="step-1", tool="fn_build_cohort", params={"states": ["IL"]}, rationale="Broad."),
            PlanStep(
                step_id="step-2",
                tool="fn_segment_counts",
                params={"segment_codes": ["itm"], "segment_mode": "any"},
                rationale="Gate to eligible.",
            ),
        ],
        "expected_outcome": "An eligible subset.",
        "risk_notes": "Read-only.",
        "requires_approval": False,
    }
    fields.update(overrides)
    return ComposedPlan(**fields)


def _issue(plan: ComposedPlan | None = None, *, actor: str = ACTOR, now: int = NOW) -> str:
    digest = issue_plan_digest(
        actor=actor, objective=OBJECTIVE, states=list(STATES), plan=plan or _plan(), now=now
    )
    assert digest is not None
    return digest


def _verify(digest: str, plan: ComposedPlan | None = None, **overrides: Any) -> None:
    kwargs: dict[str, Any] = {
        "actor": ACTOR,
        "objective": OBJECTIVE,
        "states": list(STATES),
        "plan": plan or _plan(),
        "now": NOW + 5,
    }
    kwargs.update(overrides)
    verify_plan_digest(digest, **kwargs)


def _conflict(digest: str, plan: ComposedPlan | None = None, **overrides: Any) -> str:
    with pytest.raises(PlanDigestConflict) as caught:
        _verify(digest, plan, **overrides)
    return caught.value.reason


def test_issue_and_verify_round_trip() -> None:
    digest = _issue()
    parts = digest.split(".")
    assert parts[0] == "v1" and parts[1] == "v7" and parts[2] == str(NOW)
    assert len(parts[3]) == 43
    _verify(digest)


def test_digest_carries_no_actor_objective_or_plan_text() -> None:
    digest = _issue()
    for secret in (ACTOR, "summit", OBJECTIVE, "refi", "fn_build_cohort", "IL"):
        assert secret.lower() not in digest.lower()


def test_canonical_json_is_stable_under_param_key_order() -> None:
    forward = _plan(
        steps=[PlanStep(step_id="s", tool="fn_segment_counts", params={"segment_codes": ["itm"], "segment_mode": "all"})]
    )
    backward = _plan(
        steps=[PlanStep(step_id="s", tool="fn_segment_counts", params={"segment_mode": "all", "segment_codes": ["itm"]})]
    )
    assert canonical_plan_json(forward) == canonical_plan_json(backward)
    _verify(_issue(forward), backward)


def _tampered_plans() -> list[tuple[str, ComposedPlan]]:
    base = _plan()
    steps = list(base.steps)
    return [
        (
            "param value",
            _plan(steps=[steps[0].model_copy(update={"params": {"states": ["IN"]}}), steps[1]]),
        ),
        ("step order", _plan(steps=[steps[1], steps[0]])),
        ("requires_approval", _plan(requires_approval=True)),
        ("rationale", _plan(steps=[steps[0].model_copy(update={"rationale": "Other."}), steps[1]])),
        ("step_id", _plan(steps=[steps[0].model_copy(update={"step_id": "step-9"}), steps[1]])),
        ("tool", _plan(steps=[steps[0].model_copy(update={"tool": "fn_offer_compare"}), steps[1]])),
        ("dropped step", _plan(steps=[steps[0]])),
        ("summary", _plan(objective_summary="Something else.")),
    ]


@pytest.mark.parametrize(("label", "tampered"), _tampered_plans(), ids=[row[0] for row in _tampered_plans()])
def test_every_plan_claim_is_bound(label: str, tampered: ComposedPlan) -> None:
    assert _conflict(_issue(), tampered) == "digest_mismatch", label


@pytest.mark.parametrize(
    ("label", "overrides"),
    [
        ("objective", {"objective": "Compose a HELOC growth plan for review."}),
        ("states", {"states": ["IL"]}),
        ("states order", {"states": ["TX", "IL"]}),
        ("no states", {"states": []}),
    ],
)
def test_objective_and_states_are_bound(label: str, overrides: dict[str, Any]) -> None:
    assert _conflict(_issue(), **overrides) == "digest_mismatch", label


def _swap(digest: str, index: int, value: str) -> str:
    parts = digest.split(".")
    parts[index] = value
    return ".".join(parts)


def test_kid_iat_and_signature_are_bound() -> None:
    digest = _issue()
    signature = digest.split(".")[3]
    flipped = ("B" if signature[0] == "A" else "A") + signature[1:]
    assert _conflict(_swap(digest, 1, "v8")) == "digest_mismatch"
    assert _conflict(_swap(digest, 2, str(NOW - 1))) == "digest_mismatch"
    assert _conflict(_swap(digest, 3, flipped)) == "digest_mismatch"


def test_a_digest_for_one_actor_is_rejected_for_another() -> None:
    digest = _issue(actor=ACTOR)
    assert _conflict(digest, actor=OTHER_ACTOR) == "digest_mismatch"
    # The binding is case- and whitespace-insensitive, like the handoff proof.
    _verify(digest, actor=f"  {ACTOR.upper()} ")


def test_expired_and_future_digests_are_rejected() -> None:
    digest = _issue(now=NOW)
    _verify(digest, now=NOW + PLAN_DIGEST_TTL_S)
    assert _conflict(digest, now=NOW + PLAN_DIGEST_TTL_S + 1) == "digest_expired"
    future = _issue(now=NOW + 61)
    assert _conflict(future, now=NOW) == "digest_mismatch"
    _verify(_issue(now=NOW + 60), now=NOW)


def test_rotation_keeps_the_previous_key_valid_and_an_unknown_kid_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    digest = _issue()
    monkeypatch.setattr(
        settings, "mip_genie_action_secret_current", SecretStr("plan-digest-rotated-key-9876543210")
    )
    monkeypatch.setattr(settings, "mip_genie_action_secret_kid", "v8")
    monkeypatch.setattr(
        settings, "mip_genie_action_secret_previous", SecretStr("plan-digest-current-key-0123456789")
    )
    monkeypatch.setattr(settings, "mip_genie_action_secret_previous_kid", "v7")
    _verify(digest)
    assert _issue().split(".")[1] == "v8"

    monkeypatch.setattr(settings, "mip_genie_action_secret_previous", None)
    assert _conflict(digest) == "digest_mismatch"


def test_the_named_kid_is_the_only_key_tried(monkeypatch: pytest.MonkeyPatch) -> None:
    # Same secret, different kid: a digest must not verify under a key id it
    # does not name, even when the bytes would match.
    digest = _issue()
    monkeypatch.setattr(settings, "mip_genie_action_secret_kid", "v9")
    assert _conflict(digest) == "digest_mismatch"


def test_a_missing_key_makes_issue_return_none_and_verify_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    digest = _issue()
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "mip_genie_action_secret_current", None)
    with caplog.at_level(logging.WARNING, logger=digest_module.__name__):
        assert (
            issue_plan_digest(actor=ACTOR, objective=OBJECTIVE, states=list(STATES), plan=_plan())
            is None
        )
    events = [record for record in caplog.records if record.getMessage() == "growth_agent_plan_digest_unavailable"]
    assert len(events) == 1
    with pytest.raises(PlanDigestUnavailable):
        _verify(digest)


def test_an_unsupported_kid_shape_is_never_signed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mip_genie_action_secret_kid", "2026.09 rotation")
    assert issue_plan_digest(actor=ACTOR, objective=OBJECTIVE, states=list(STATES), plan=_plan()) is None


@pytest.mark.parametrize(
    "wire",
    [
        "",
        "v1",
        "v2.v7.1790000000." + "A" * 43,
        "v1.v7.179000000." + "A" * 43,
        "v1.v7.1790000000." + "A" * 42,
        "v1.v7.1790000000." + "A" * 44,
        "v1.v 7.1790000000." + "A" * 43,
        "v1.v7.1790000000." + "A" * 42 + "=",
        "v1.v7.1790000000." + "A" * 42 + "B",
        "v1..1790000000." + "A" * 43,
    ],
)
def test_malformed_wire_strings_are_mismatches(wire: str) -> None:
    assert _conflict(wire) == "digest_mismatch"


def test_nothing_is_logged_about_the_plan_or_actor(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        digest = _issue()
        _verify(digest)
        _conflict(digest, actor=OTHER_ACTOR)
    text = "\n".join(record.getMessage() + str(record.__dict__) for record in caplog.records)
    for secret in (ACTOR, OTHER_ACTOR, OBJECTIVE, digest, "fn_build_cohort"):
        assert secret not in text
