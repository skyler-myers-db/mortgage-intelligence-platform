"""The window plan carries the App role's CREATE ON DATABASE re-grant and the description fix-up (S2).

Spike S2 measured, on a throwaway App, that every update that carries
resources (an identical array included) grants CREATE ON DATABASE to the
App's Lakebase role again with no restart, that only the migration job's
revoke takes it back, and that a create-update masked to ``description``
changes the description alone. So:

* WR1 (a binding diff, compared as a set by name) names its re-grant in the
  acked body (``app_role_regrant: ["binding_send"]``) and budgets the
  migration job its window runs after the binding update;
* a re-grant an earlier send left behind (the migration plan's
  ``app_role_regrant: owed``) is a restorable WR2 window, never a contract
  WR3 and never a skip;
* a description-only difference opens no window and leaves the digest as it
  was; the public summary names it as an ``online_fixups`` entry.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from tools.databricks.quiesce_window_plan import (
    STEP_BUDGET_S,
    WindowFacts,
    body_sha256,
    predicted_seconds,
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
WR1 = replace(BLUE, binding_state="diff", binding_lines=("binding database: missing (update adds it)",))


def _acked(facts: WindowFacts) -> object:
    digest = window_plan(facts, position="pre-apply", acknowledgement="").sha256
    return window_plan(facts, position="pre-apply", acknowledgement=digest)


def test_a_binding_send_names_its_re_grant_and_budgets_the_migration_job() -> None:
    plan = _acked(WR1)

    assert plan.code == "window" and plan.reasons == ("WR1",) and plan.blue_restorable is True
    assert plan.canonical["app_role_regrant"] == ["binding_send"]
    assert plan.public_summary()["app_role_regrant"] == ["binding_send"]
    assert plan.predicted_seconds == (
        STEP_BUDGET_S["stop"]
        + STEP_BUDGET_S["WR1"]
        + STEP_BUDGET_S["migrate"]
        + STEP_BUDGET_S["start"]
        + STEP_BUDGET_S["repin"]
    )
    without = {key: value for key, value in plan.canonical.items() if key != "app_role_regrant"}
    assert body_sha256(without) != plan.sha256, "the ack covers the re-grant"


def test_a_wr3_window_already_budgets_the_migration_job_once() -> None:
    assert predicted_seconds(("WR1", "WR3"), blue_restorable=True) == (
        STEP_BUDGET_S["stop"]
        + STEP_BUDGET_S["WR1"]
        + STEP_BUDGET_S["WR3"]
        + STEP_BUDGET_S["start"]
        + STEP_BUDGET_S["repin"]
    )


def test_an_observed_re_grant_under_a_noop_plan_is_a_restorable_wr2_window() -> None:
    facts = replace(BLUE, app_role_regrant="owed")

    assert facts.reasons() == ("WR2",)
    plan = _acked(facts)
    assert (plan.code, plan.blue_restorable, plan.closes_at) == ("window", True, "start_repin")
    assert plan.canonical["app_role_regrant"] == ["observed"]
    both = _acked(replace(WR1, app_role_regrant="owed"))
    assert both.reasons == ("WR1", "WR2")
    assert both.canonical["app_role_regrant"] == ["binding_send", "observed"]


@pytest.mark.parametrize(
    ("decision", "reasons"), (("apply", ("WR3",)), ("refuse", ())), ids=("apply", "refuse")
)
def test_a_re_grant_is_no_wr2_when_the_migration_job_runs_anyway_or_the_plan_refuses(
    decision: str, reasons: tuple[str, ...]
) -> None:
    facts = replace(
        BLUE,
        app_role_regrant="owed" if decision == "apply" else "unchecked",
        migration_decision=decision,
        migration_pending=("0042",) if decision == "apply" else (),
    )

    assert facts.reasons() == reasons


def test_a_description_only_difference_opens_no_window_and_keeps_the_digest() -> None:
    differs = window_plan(replace(BLUE, description_state="differs"), position="pre-apply", acknowledgement="")
    equal = window_plan(BLUE, position="pre-apply", acknowledgement="")

    assert (differs.code, differs.reasons, differs.ack) == ("none", (), "not_required")
    assert differs.sha256 == equal.sha256 and differs.canonical == equal.canonical
    assert differs.public_summary()["online_fixups"] == ["description"]
    assert equal.public_summary()["online_fixups"] == []
    assert "app_role_regrant" not in equal.canonical, "a plan with no reason keeps M0-PR8's body"


def test_an_unreviewed_description_state_never_reaches_a_public_summary() -> None:
    plan = window_plan(replace(BLUE, description_state="edited"), position="pre-apply", acknowledgement="")

    with pytest.raises(ValueError, match="description state"):
        plan.public_summary()
