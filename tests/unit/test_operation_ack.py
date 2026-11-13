"""Purpose-named exact digest acks (M2-PR7): an ack for one purpose never satisfies another."""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

from tools.databricks import operation_ack as acks

REPO = Path(__file__).resolve().parents[2]
PLAN = {"mode": "adopt", "deployment_sha256": "d" * 64, "source_tree_sha256": "e" * 64}


def test_the_canonical_body_names_the_domain_and_the_purpose() -> None:
    body = json.loads(acks.canonical_body("adopt_live", PLAN))

    assert body == {"ack": "mip-operation-ack-v1", "purpose": "adopt_live", "plan": PLAN}
    assert acks.canonical_body(
        "adopt_live", dict(reversed(list(PLAN.items())))
    ) == acks.canonical_body("adopt_live", PLAN), "key order never changes the digest"


@pytest.mark.parametrize(("given", "checked"), list(itertools.permutations(acks.PURPOSES, 2)))
def test_an_ack_for_one_purpose_never_satisfies_another(given: str, checked: str) -> None:
    digest = acks.ack_digest(given, PLAN)

    assert acks.require_ack(given, PLAN, digest) == digest
    with pytest.raises(acks.AckRefused) as refused:
        acks.require_ack(checked, PLAN, digest)
    assert refused.value.code == "ack_mismatch"


def test_only_the_purposes_the_deploy_reads_have_a_one_run_shell_export() -> None:
    assert acks.ACK_ENV == {
        "adopt_live": "MIP_ACK_ADOPT_LIVE",
        "supersede": "MIP_ACK_SUPERSEDE",
        "unadopt": "MIP_ACK_UNADOPT",
        "no_rollback_for": "MIP_ACK_NO_ROLLBACK_FOR",
        # The core release's pre-activation proof and tier-b restore read it (M2-PR7b).
        "restore_unverified_frontend": "MIP_ACK_RESTORE_UNVERIFIED_FRONTEND",
    }
    shell = "".join(
        path.read_text(encoding="utf-8") for path in sorted((REPO / "scripts").rglob("*.sh"))
    )
    for name in acks.ACK_ENV.values():
        assert name in shell, f"{name} is the export a refusal names, so the deploy must read it"
    for name in ("MIP_ACK_OVERRIDE_FREEZE", "MIP_ACK_TIER_TRANSITION"):
        assert name not in shell, "no code reads it, so no refusal may name it"
    # The thaw's ack is CI's alone (tools/clients/publisher_freeze.py): no deploy.sh export.
    assert "freeze_thaw" in acks.PURPOSES and "freeze_thaw" not in acks.ACK_ENV
    assert "MIP_ACK_FREEZE_THAW" not in shell and "export" not in acks.REMEDY["freeze_thaw"]


@pytest.mark.parametrize(
    ("purpose", "route"),
    [
        ("adopt_live", "export MIP_ACK_ADOPT_LIVE=<its sha256> for this run only"),
        ("supersede", "export MIP_ACK_SUPERSEDE=<its sha256> for this run only"),
        ("unadopt", "export MIP_ACK_UNADOPT=<its sha256> for this run only"),
        ("no_rollback_for", "export MIP_ACK_NO_ROLLBACK_FOR=<its sha256> for this run only"),
        ("override_freeze", "pass --override-freeze-ack <its sha256> to this one publish"),
        (
            "restore_unverified_frontend",
            "export MIP_ACK_RESTORE_UNVERIFIED_FRONTEND=<its sha256> for this run only",
        ),
        ("tier_transition", "pass --tier-transition-ack <its sha256> to the one blue proof"),
        # CI only (wave 7c): no shell export exists, so the refusal names the dispatch.
        (
            "freeze_thaw",
            "dispatch deploy-dev.yml with phase=thaw and the dispatch input operation_acks "
            "freeze_thaw=<its sha256>",
        ),
    ],
)
def test_a_missing_ack_names_the_route_that_purpose_is_really_handed_in_by(
    purpose: str, route: str
) -> None:
    for given in (None, "A" * 64):
        with pytest.raises(acks.AckRefused) as refused:
            acks.require_ack(purpose, PLAN, given)
        assert "MIP_ACK_OVERRIDE_FREEZE" not in str(refused.value)
        assert "MIP_ACK_TIER_TRANSITION" not in str(refused.value)
    with pytest.raises(acks.AckRefused) as refused:
        acks.require_ack(purpose, PLAN, None)
    assert route in str(refused.value)


@pytest.mark.parametrize(
    ("given", "code"),
    [
        (None, "ack_missing"),
        ("", "ack_missing"),
        ("A" * 64, "ack_malformed"),
        ("0" * 63, "ack_malformed"),
    ],
)
def test_a_missing_or_malformed_ack_refuses(given: str | None, code: str) -> None:
    with pytest.raises(acks.AckRefused) as refused:
        acks.require_ack("supersede", PLAN, given)
    assert refused.value.code == code


def test_a_changed_plan_changes_the_digest_and_the_mismatch_names_the_new_one() -> None:
    digest = acks.ack_digest("adopt_live", PLAN)
    changed = {**PLAN, "source_tree_sha256": "f" * 64}

    with pytest.raises(acks.AckRefused) as refused:
        acks.require_ack("adopt_live", changed, digest)
    assert acks.ack_digest("adopt_live", changed) in str(refused.value)
    assert "nothing was changed" in str(refused.value)


def test_an_unreviewed_purpose_is_refused() -> None:
    with pytest.raises(ValueError):
        acks.ack_digest("profile_attestation", PLAN)
