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
    OWNER_START_REFUSALS,
    REASON_NAMES,
    REASONS,
    STEP_BUDGET_S,
    WindowFacts,
    body_sha256,
    owner_start_recovery,
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


def test_a_tier_a_re_pin_between_the_gates_keeps_the_pre_lease_ack() -> None:
    """Round 2 of #334: step 0a's tier a re-pins a stopped blue onto its start before pre-apply."""

    facts = _facts("WR3")
    ack = window_plan(facts, position="pre-lease", acknowledgement="").sha256
    repinned = replace(
        facts,
        blue_deployment_id="deployment-start-PLACEHOLDER",
        blue_predecessor_deployment_id=facts.blue_deployment_id,
    )
    recheck = window_plan(repinned, position="pre-apply", acknowledgement=ack)
    assert recheck.code == "window" and recheck.ack == "matched", recheck.refusals
    assert recheck.canonical["blue_deployment_id"] == "deployment-start-PLACEHOLDER", "the start is blue now"
    assert recheck.sha256 != ack and body_sha256(recheck.canonical) == recheck.sha256


@pytest.mark.parametrize(
    ("position", "drift"),
    (
        ("pre-lease", {}),  # only the recheck after step 0a re-derives blue
        ("pre-apply", {"blue_predecessor_deployment_id": "deployment-other-PLACEHOLDER"}),
        ("pre-apply", {"restore_source": "/Workspace/Users/app-sp-PLACEHOLDER/immutable"}),
        ("pre-apply", {"source_tree_sha256": "d" * 64, "artifact_tree_sha256": "d" * 64}),
        ("pre-apply", {"migration_pending": ("0042", "0043")}),
    ),
    ids=("pre-lease", "another-predecessor", "another-source", "another-tree", "another-reason-fact"),
)
def test_a_re_pin_keeps_the_ack_only_when_nothing_else_moved(position: str, drift: dict[str, object]) -> None:
    facts = _facts("WR3")
    ack = window_plan(facts, position="pre-lease", acknowledgement="").sha256
    repinned = replace(
        facts,
        **{
            "blue_deployment_id": "deployment-start-PLACEHOLDER",
            "blue_predecessor_deployment_id": facts.blue_deployment_id,
            "default_source_code_path": str(drift.get("restore_source", facts.restore_source)),
            **drift,
        },
    )
    recheck = window_plan(repinned, position=position, acknowledgement=ack)
    assert recheck.ack == "mismatch" and "window_unacked" in recheck.refusals


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


# --- an owner's break-glass start over an open window (wave 7c) ----------------------------

OWNER_DIGEST = "d" * 64
CANDIDATE = replace(BLUE, journal_state="window_refused", owner_start_state="candidate", owner_start_sha256=OWNER_DIGEST)
# The digests origin/main computed for these facts before the owner-start fields existed.
PINNED_DIGESTS = {
    (): "e671bdfbe4bd0362dc5114840cb27a5dce9ca5fb27ff486f2ddfde9b88999525",
    ("WR1", "WR3"): "95ab5aa52f2a167c9180c1fb1d2a5b5a1fb90e1522af9038948383304033e44d",
    REASONS: "7bc0360fcc1f6de0c10450d294398e279546a3c7ff7bc1b0732de845d855f43c",
}


def _owner_plan(facts: WindowFacts, position: str, owner_ack: str, window_ack: str = "") -> object:
    return window_plan(
        facts, position=position, acknowledgement=window_ack, owner_start_acknowledgement=owner_ack
    )


def test_an_owner_start_passes_the_open_journal_only_before_the_lease_with_its_exact_ack() -> None:
    acked = _owner_plan(CANDIDATE, "pre-lease", OWNER_DIGEST)
    assert acked.code == "none" and not acked.refusals and acked.owner_start_ack == "matched"
    summary = acked.public_summary()
    assert summary["owner_start_recovery"] is True and summary["deadman_recovery"] is False
    assert (summary["owner_start"], summary["owner_start_sha256"]) == ("candidate", OWNER_DIGEST)
    assert owner_start_recovery(CANDIDATE, position="pre-lease", acknowledgement=f" {OWNER_DIGEST}\n")

    # Under the lease the recovery has closed the journal, or the window refuses: never at pre-apply.
    recheck = _owner_plan(CANDIDATE, "pre-apply", OWNER_DIGEST)
    assert recheck.refusals == ("window_journal_open",) and recheck.public_summary()["owner_start_recovery"] is False
    assert not owner_start_recovery(CANDIDATE, position="pre-apply", acknowledgement=OWNER_DIGEST)

    for given, state in (
        ("", "absent"),
        ("0" * 64, "mismatch"),
        (OWNER_DIGEST.upper(), "mismatch"),
        (OWNER_DIGEST[:63], "mismatch"),
        (OWNER_DIGEST + "d", "mismatch"),
        ("é" * 64, "mismatch"),
    ):
        refused = _owner_plan(CANDIDATE, "pre-lease", given)
        assert refused.refusals == ("window_journal_open",) and refused.owner_start_ack == state, given
        assert refused.public_summary()["owner_start_sha256"] == OWNER_DIGEST, "the plan still prints the digest to ack"
    # The window's own ack is another purpose: it never opens the journal's gate.
    crossed = _owner_plan(CANDIDATE, "pre-lease", "", window_ack=OWNER_DIGEST)
    assert crossed.refusals == ("window_journal_open",)


@pytest.mark.parametrize(
    "facts",
    (
        *(replace(CANDIDATE, owner_start_state=code, owner_start_sha256="") for code in ("absent", *OWNER_START_REFUSALS)),
        replace(CANDIDATE, owner_start_sha256=""),
        replace(CANDIDATE, owner_start_sha256="not-a-digest"),
        # A digest beside a code that is not ``candidate`` is never one to acknowledge.
        *(replace(CANDIDATE, owner_start_state=code) for code in ("absent", "owner_start_ambiguous", "owner_start_none")),
    ),
    ids=lambda facts: f"{facts.owner_start_state}:{facts.owner_start_sha256[:6] or 'no-digest'}",
)
def test_only_a_candidate_with_a_digest_can_be_acknowledged(facts: WindowFacts) -> None:
    for given in ("", OWNER_DIGEST, facts.owner_start_sha256):
        plan = _owner_plan(facts, "pre-lease", given)
        assert plan.refusals == ("window_journal_open",), (facts.owner_start_state, given)
        summary = plan.public_summary()
        assert summary["owner_start_recovery"] is False
        # The summary carries a digest only for a candidate: nothing else is ever one to ack.
        candidate = facts.owner_start_state == "candidate"
        assert summary["owner_start_sha256"] == (facts.owner_start_sha256 if candidate else "")


def test_a_closed_or_absent_journal_needs_no_owner_start_ack() -> None:
    for state in ("absent", "closed"):
        plan = _owner_plan(replace(BLUE, journal_state=state), "pre-lease", OWNER_DIGEST)
        assert plan.code == "none" and plan.owner_start_ack == "not_required"
        assert plan.public_summary()["owner_start_recovery"] is False


def test_the_owner_start_fields_never_move_a_window_digest() -> None:
    for reasons, digest in PINNED_DIGESTS.items():
        plain = _facts(*reasons)
        assert window_plan(plain, position="pre-lease", acknowledgement="").sha256 == digest, reasons
        marked = replace(
            plain,
            journal_state="window_refused",
            deadman_state="deadman_skipped",
            owner_start_state="candidate",
            owner_start_sha256=OWNER_DIGEST,
            owner_start_creator="other",
        )
        for position in ("pre-lease", "pre-apply"):
            assert _owner_plan(marked, position, OWNER_DIGEST).sha256 == digest, (reasons, position)
            assert _owner_plan(marked, position, OWNER_DIGEST).canonical == window_plan(
                plain, position=position, acknowledgement=""
            ).canonical


def test_a_recovering_owner_start_that_also_needs_a_window_still_needs_the_windows_own_ack() -> None:
    facts = replace(_facts("WR3"), journal_state="window_open", owner_start_state="candidate", owner_start_sha256=OWNER_DIGEST)
    unacked = _owner_plan(facts, "pre-lease", OWNER_DIGEST)
    assert unacked.refusals == ("window_unacked",), "the journal passes; the new window is its own ack"
    acked = _owner_plan(facts, "pre-lease", OWNER_DIGEST, window_ack=unacked.sha256)
    assert acked.code == "window" and not acked.refusals and acked.public_summary()["owner_start_recovery"] is True


@pytest.mark.parametrize(
    "unreviewed",
    (
        {"owner_start_state": "deployment-start-PLACEHOLDER"},
        {"deadman_state": "deadman_restarted by owner@lender.example"},
        {"owner_start_creator": "owner@lender.example"},
    ),
    ids=("owner-start", "dead-man", "creator"),
)
def test_an_unreviewed_recovery_code_never_reaches_a_public_summary(unreviewed: dict[str, str]) -> None:
    plan = _owner_plan(replace(CANDIDATE, **unreviewed), "pre-lease", "")
    with pytest.raises(ValueError, match="unreviewed recovery code"):
        plan.public_summary()


def test_the_owner_start_summary_carries_codes_and_the_digest_only() -> None:
    plan = _owner_plan(replace(CANDIDATE, deadman_state="deadman_skipped", owner_start_creator="other"), "pre-lease", "")
    public = json.dumps(plan.public_summary(), sort_keys=True)
    assert "PLACEHOLDER" not in public and "/Workspace" not in public and "@" not in public
    summary = plan.public_summary()
    assert {key: summary[key] for key in ("journal_state", "deadman_state", "owner_start", "owner_start_creator", "owner_start_ack")} == {
        "journal_state": "window_refused",
        "deadman_state": "deadman_skipped",
        "owner_start": "candidate",
        "owner_start_creator": "other",
        "owner_start_ack": "absent",
    }
    assert "deployment-blue-PLACEHOLDER" in json.dumps(plan.full(), sort_keys=True), "ids stay in the private plan"


def test_the_plan_prints_the_owner_start_route_as_codes_and_the_digest(capsys: pytest.CaptureFixture[str]) -> None:
    from tools.databricks import quiesce_window

    candidate = replace(CANDIDATE, deadman_state="deadman_skipped", owner_start_creator="other")
    quiesce_window._print_plan(_owner_plan(candidate, "pre-lease", "").public_summary(), refused=True)
    out = capsys.readouterr()
    assert (
        "owner start over the open window: candidate (journal window_refused, dead-man deadman_skipped, creator other); "
        f"re-pin it on a release with operation_acks owner_start_repin={OWNER_DIGEST}"
    ) in out.out
    assert "REFUSED window_journal_open" in out.err

    quiesce_window._print_plan(_owner_plan(candidate, "pre-lease", OWNER_DIGEST).public_summary(), refused=False)
    assert "repin --from-owner-start" in capsys.readouterr().out

    ambiguous = replace(candidate, owner_start_state="owner_start_ambiguous", owner_start_sha256="")
    quiesce_window._print_plan(_owner_plan(ambiguous, "pre-lease", "").public_summary(), refused=True)
    assert "the open window has no owner start a release can re-pin: owner_start_ambiguous (" in capsys.readouterr().out

    quiesce_window._print_plan(window_plan(BLUE, position="pre-lease", acknowledgement="").public_summary(), refused=False)
    assert "owner start" not in capsys.readouterr().out, "an install with no open window prints nothing new"
