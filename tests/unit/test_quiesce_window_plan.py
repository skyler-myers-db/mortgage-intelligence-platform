"""The window plan: reasons WR1-WR4, one digest at both positions, the exact digest ack (M0-PR8).

The owner hardware-key attestation is deferred, so a window opens only when
``MIP_ACK_QUIESCE_WINDOW`` equals the sha256 of the plan's canonical body. The
digest never covers the position, so the pre-apply recheck reproduces the
pre-lease digest exactly when its facts are unchanged (TOCTOU), and any drift
in a reason or a fact the window relies on changes it and refuses.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from tools.databricks.quiesce_window_plan import (
    REASON_NAMES,
    REASONS,
    STEP_BUDGET_S,
    WindowFacts,
    body_sha256,
    window_plan,
)

TREE = "a" * 64
BLUE = WindowFacts(
    applicable=True,
    blue_deployment_id="deployment-blue-PLACEHOLDER",
    blue_current=True,
    restore_source="/Workspace/Users/deployer-PLACEHOLDER/app-src/release",
    default_source_code_path="/Workspace/Users/deployer-PLACEHOLDER/app-src/release",
    env_names_sha256="b" * 64,
    source_tree_sha256=TREE,
    artifact_tree_sha256=TREE,
)
WR = {
    "WR1": {"binding_state": "diff", "binding_lines": ("binding database: database differ",)},
    "WR2": {"role_state": "drift"},
    "WR3": {"migration_decision": "apply", "migration_pending": ("0042",)},
    "WR4": {"instance_update_sha256": "c" * 64},
}


def _facts(*reasons: str, **extra: object) -> WindowFacts:
    changes: dict[str, object] = {}
    for reason in reasons:
        changes.update(WR[reason])
    return replace(BLUE, **{**changes, **extra})


def _acked(facts: WindowFacts, position: str = "pre-apply") -> object:
    digest = window_plan(facts, position=position, acknowledgement="").sha256
    return window_plan(facts, position=position, acknowledgement=digest)


def test_the_four_reasons_are_wr1_to_wr4_in_order() -> None:
    assert REASONS == ("WR1", "WR2", "WR3", "WR4")
    assert set(REASON_NAMES) == set(REASONS)
    assert _facts().reasons() == ()
    assert _facts("WR3", "WR1").reasons() == ("WR1", "WR3")
    assert _facts("WR1", "WR2", "WR3", "WR4").reasons() == REASONS
    assert _facts(binding_state="unresolved").reasons() == ("WR1",), "unresolved is a diff (S2)"


@pytest.mark.parametrize("reason", REASONS)
def test_each_reason_alone_needs_a_window_and_the_exact_digest_ack(reason: str) -> None:
    facts = _facts(reason)
    unacked = window_plan(facts, position="pre-lease", acknowledgement="")
    wrong = window_plan(facts, position="pre-lease", acknowledgement="0" * 64)
    acked = window_plan(facts, position="pre-lease", acknowledgement=unacked.sha256)

    assert unacked.code == "refused" and unacked.refusals == ("window_unacked",)
    assert unacked.ack == "absent" and wrong.ack == "mismatch"
    assert acked.code == "window" and acked.ack == "matched" and not acked.refusals
    assert acked.reasons == (reason,) and acked.tier == "a"
    assert acked.predicted_seconds == (
        STEP_BUDGET_S["stop"]
        + STEP_BUDGET_S[reason]
        + STEP_BUDGET_S["start"]
        + STEP_BUDGET_S["repin"]
    )


def test_no_reason_needs_no_window_and_no_ack() -> None:
    plan = window_plan(_facts(), position="pre-apply", acknowledgement="")
    assert (plan.code, plan.ack, plan.reasons, plan.predicted_seconds) == ("none", "not_required", (), 0)


def test_the_digest_ignores_the_position_so_the_recheck_reproduces_it() -> None:
    facts = _facts("WR1", "WR3")
    lease = window_plan(facts, position="pre-lease", acknowledgement="")
    apply = window_plan(facts, position="pre-apply", acknowledgement=lease.sha256)
    assert apply.sha256 == lease.sha256 and apply.code == "window"
    assert body_sha256(apply.canonical) == apply.sha256


@pytest.mark.parametrize(
    "drift",
    (
        {"binding_lines": ("binding warehouse: id differ",)},
        {"migration_pending": ("0042", "0043")},
        {"blue_deployment_id": "deployment-other-PLACEHOLDER"},
        {"source_tree_sha256": "d" * 64, "artifact_tree_sha256": "d" * 64},
        {"env_names_sha256": "e" * 64},
        {"role_state": "drift"},
        {"instance_update_sha256": "f" * 64},
    ),
)
def test_a_toctou_drift_changes_the_digest_and_the_old_ack_refuses(drift: dict[str, object]) -> None:
    facts = _facts("WR1", "WR3")
    ack = window_plan(facts, position="pre-lease", acknowledgement="").sha256
    recheck = window_plan(replace(facts, **drift), position="pre-apply", acknowledgement=ack)
    assert recheck.sha256 != ack
    assert "window_unacked" in recheck.refusals and recheck.ack == "mismatch"


def test_new_reasons_at_the_recheck_refuse_a_run_planned_without_a_window() -> None:
    assert window_plan(_facts(), position="pre-lease", acknowledgement="").code == "none"
    recheck = window_plan(_facts("WR2"), position="pre-apply", acknowledgement="")
    assert recheck.code == "refused" and recheck.refusals == ("window_unacked",)


def test_tier_a_refuses_when_blue_is_not_the_last_deployment_at_the_recheck() -> None:
    facts = _facts("WR1", blue_current=False)
    assert _acked(facts, "pre-lease").code == "window", "pre-lease: step 0a may restore blue"
    refused = _acked(facts, "pre-apply")
    assert refused.refusals == ("window_blue_not_current",)


@pytest.mark.parametrize(
    "drift",
    (
        {"default_source_code_path": "/Workspace/Users/deployer-PLACEHOLDER/files"},
        {"artifact_tree_sha256": "9" * 64},
        {"source_tree_sha256": "", "artifact_tree_sha256": ""},
    ),
)
def test_a_start_that_would_snapshot_other_bytes_refuses_before_the_stop(
    drift: dict[str, object],
) -> None:
    plan = _acked(_facts("WR3", **drift))
    assert plan.code == "refused" and plan.refusals == ("window_start_source_drift",)


def test_a_contract_migration_keeps_the_window_open_until_green_activation() -> None:
    facts = _facts("WR3", migration_blue_restorable=False, migration_pending_contract=("0042",))
    plan = _acked(facts)
    assert plan.code == "window" and plan.blue_restorable is False
    assert plan.closes_at == "green_activation"
    assert plan.canonical["blue_restorable"] is False, "the acked digest names the outage"
    assert plan.predicted_seconds == STEP_BUDGET_S["stop"] + STEP_BUDGET_S["WR3"]
    expand = _acked(_facts("WR3"))
    assert expand.blue_restorable is True and expand.sha256 != plan.sha256


@pytest.mark.parametrize(
    ("facts", "code"),
    (
        (replace(BLUE, journal_state="window_open"), "window_journal_open"),
        (replace(BLUE, journal_state="window_refused"), "window_journal_open"),
        (replace(BLUE, migration_decision="refuse"), "window_migration_refused"),
        (_facts("WR1", env_names_sha256=""), "window_facts_incomplete"),
    ),
)
def test_refusals_without_a_window_still_refuse(facts: WindowFacts, code: str) -> None:
    plan = _acked(facts)
    assert plan.code == "refused" and code in plan.refusals


def test_an_ungoverned_install_is_not_applicable() -> None:
    plan = window_plan(
        WindowFacts(applicable=False, not_applicable_reason="no_app"),
        position="pre-lease",
        acknowledgement="",
    )
    assert (plan.code, plan.reasons, plan.ack) == ("not_applicable", (), "not_required")


def test_the_public_summary_carries_codes_counts_and_digests_only() -> None:
    plan = _acked(_facts("WR1", "WR3"))
    public = json.dumps(plan.public_summary(), sort_keys=True)
    assert "PLACEHOLDER" not in public and "binding database" not in public
    assert plan.sha256 in public
    full = json.dumps(plan.full(), sort_keys=True)
    assert "deployment-blue-PLACEHOLDER" in full and "binding database" in full


def test_facts_round_trip_through_the_private_plan() -> None:
    facts = _facts("WR1", "WR3", "WR4")
    assert WindowFacts.from_dict(json.loads(json.dumps(facts.as_dict()))) == facts
    with pytest.raises(ValueError, match="unknown field"):
        WindowFacts.from_dict({**facts.as_dict(), "stray": 1})
