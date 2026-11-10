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
from pathlib import Path
from types import SimpleNamespace

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
    # Every window runs the migration job; a WR3 budgets it, any other window
    # gets its "migrate" budget (spike S2: the revoke after a binding send).
    migrate = 0 if reason == "WR3" else STEP_BUDGET_S["migrate"]
    assert acked.predicted_seconds == (
        STEP_BUDGET_S["stop"]
        + STEP_BUDGET_S[reason]
        + migrate
        + STEP_BUDGET_S["start"]
        + STEP_BUDGET_S["repin"]
    )


def test_a_verifier_role_rotation_is_wr2_and_forces_the_migration_job_into_a_window() -> None:
    # The converge would replace a legacy verifier role and drop its grants;
    # only the migration job re-grants it, and the job runs only in a window.
    facts = _facts(verifier_role_state="drift")
    assert facts.reasons() == ("WR2",)
    plan = _acked(facts)
    assert plan.code == "window" and plan.blue_restorable is True
    assert plan.canonical["verifier_role_state"] == "drift"
    assert window_plan(facts, position="pre-apply", acknowledgement="").refusals == ("window_unacked",)


def test_a_core_tiers_absent_verifier_role_is_no_reason_and_is_pinned() -> None:
    # M2-PR3: the core tier deploys no verifier, so nothing of it can rotate.
    facts = _facts(verifier_role_state="absent")
    plan = window_plan(facts, position="pre-lease", acknowledgement="")
    assert facts.reasons() == () and plan.code == "none"
    assert plan.canonical["verifier_role_state"] == "absent", "the digest records the absence"
    assert plan.sha256 != window_plan(_facts(), position="pre-lease", acknowledgement="").sha256


@pytest.mark.parametrize(
    "jobs_reader",
    (
        {"jobs_reader_role_state": "drift"},
        {"jobs_reader_grants": "drift"},
        {"jobs_reader_role_state": "drift", "jobs_reader_grants": "drift"},
    ),
    ids=("role", "grants", "absent"),
)
def test_a_jobs_reader_role_or_grant_drift_is_wr2_and_keeps_blue_restorable(
    jobs_reader: dict[str, str],
) -> None:
    # M2-PR5: only the migration job grants the jobs reader, and that job runs
    # only in a window; blue never reads as that role, so the window closes
    # with a start and re-pin (tier a), never at green activation.
    facts = _facts(**jobs_reader)
    assert facts.reasons() == ("WR2",)
    plan = _acked(facts)
    assert plan.code == "window" and plan.blue_restorable is True
    assert plan.closes_at == "start_repin"
    for name, state in jobs_reader.items():
        assert plan.canonical[name] == state
    assert window_plan(facts, position="pre-apply", acknowledgement="").refusals == ("window_unacked",)


@pytest.mark.parametrize("name", ("jobs_reader_role_state", "jobs_reader_grants"))
def test_an_unreviewed_jobs_reader_state_is_wr2_never_a_silent_skip(name: str) -> None:
    assert _facts(**{name: "off"}).reasons() == ("WR2",)
    assert _facts(**{name: "off", "migration_decision": "unreviewed"}).reasons() == ("WR2",)


@pytest.mark.parametrize("grants", ("drift", "off", "unchecked"))
def test_jobs_reader_grants_alone_are_no_wr2_when_the_migration_job_runs_or_the_plan_refuses(
    grants: str,
) -> None:
    # Only a skipped (noop) migration job leaves the grants as they are. A
    # pending table reads as grant drift for an exact role, and the WR3
    # window's job re-grants the role, so WR2 would only open the window early.
    apply = _acked(_facts("WR3", jobs_reader_grants=grants))
    assert (apply.reasons, apply.code) == (("WR3",), "window")
    assert apply.canonical["jobs_reader_grants"] == grants, "the acked digest still names it"
    refuse = _acked(replace(BLUE, migration_decision="refuse", jobs_reader_grants=grants))
    assert (refuse.reasons, refuse.refusals) == ((), ("window_migration_refused",))
    # The role itself stays WR2 under any decision: outside a window it is
    # re-checked exact before its converge.
    rotated = _facts("WR3", jobs_reader_role_state="drift", jobs_reader_grants=grants)
    assert rotated.reasons() == ("WR2", "WR3")


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
        {"verifier_role_state": "drift"},
        {"jobs_reader_role_state": "drift"},
        {"jobs_reader_grants": "drift"},
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
    # Review: the window stays open until green activation, so the admission
    # covers that tail too, not only the stop and the migration.
    assert plan.predicted_seconds == STEP_BUDGET_S["stop"] + STEP_BUDGET_S["WR3"] + STEP_BUDGET_S["green_activation"]
    assert plan.canonical["budget"]["predicted_window_seconds"] == plan.predicted_seconds
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


# --- the rebase the plan is told about (the one step 0a takes) ----------------------


@pytest.mark.parametrize(
    ("requested", "journal", "rebase"),
    (("1", "signed", "0"), ("1", "absent", "1"), ("1", "recover", "1"), ("0", "absent", "0")),
    ids=("signed-journal-is-signed-blue", "no-journal", "journal-owned", "not-requested"),
)
def test_applicable_resolves_the_rebase_against_the_first_install_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, requested: str, journal: str, rebase: str
) -> None:
    from tools.databricks import app_first_install_journal, quiesce_window

    read: list[str] = []

    def recovery_state(_workspace: object, **kwargs: str) -> str:
        read.append(kwargs["app_name"])
        return journal

    monkeypatch.setattr(app_first_install_journal, "recovery_state", recovery_state)
    app = SimpleNamespace(name="mip-app", service_principal_client_id="app-client-PLACEHOLDER")
    workspace = SimpleNamespace(apps=SimpleNamespace(list=lambda: [app], get=lambda _name: app))
    out = tmp_path / "applicable.env"
    argv = ["applicable", "--app-name", "mip-app", "--rebase-unverified-app", requested]
    assert quiesce_window.main([*argv, "--out-env", str(out)], workspace_factory=lambda: workspace) == 0

    assert f"MIP_QUIESCE_WINDOW_REBASE={rebase}\n" in out.read_text(encoding="utf-8")
    assert read == (["mip-app"] if requested == "1" else []), "the journal is read only on request"
