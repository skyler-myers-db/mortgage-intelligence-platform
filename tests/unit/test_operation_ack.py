"""Purpose-named exact digest acks (M2-PR7): an ack for one purpose never satisfies another."""

from __future__ import annotations

import itertools
import json

import pytest

from tools.databricks import operation_ack as acks

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


def test_each_purpose_has_its_own_one_run_shell_export() -> None:
    assert acks.ACK_ENV == {
        "adopt_live": "MIP_ACK_ADOPT_LIVE",
        "supersede": "MIP_ACK_SUPERSEDE",
        "unadopt": "MIP_ACK_UNADOPT",
        "restore_unverified_frontend": "MIP_ACK_RESTORE_UNVERIFIED_FRONTEND",
        "no_rollback_for": "MIP_ACK_NO_ROLLBACK_FOR",
        "override_freeze": "MIP_ACK_OVERRIDE_FREEZE",
    }


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
