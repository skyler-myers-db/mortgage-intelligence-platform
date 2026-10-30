"""Who may hold a role on the deployer, jobs and validator identities.

A Service Principal Manager on a secretless identity could mint it a
secret, and a Service Principal User could act as it. So ``observe`` (and
with it ``--plan``, every mutating action and ``--verify``) refuses any
holder but an account admin, with one reviewed exception: the deployer's
Service Principal User on jobs. Refusals name the identity, the role and
the kind of holder, never the holder.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.fixtures.bootstrap_harness import FakeSink, plan_and_apply, run_tool, write_env
from tests.fixtures.fake_databricks_account import OPERATOR, FakeEstate, app_id, assert_safe

MANAGER = "roles/servicePrincipal.manager"
USER = "roles/servicePrincipal.user"


@pytest.fixture
def estate() -> Iterator[FakeEstate]:
    found = FakeEstate()
    yield found
    assert_safe(found)


@pytest.fixture
def installed(estate: FakeEstate, tmp_path: Path) -> tuple[Path, FakeSink]:
    sink = FakeSink()
    _planned, applied = plan_and_apply(estate, write_env(tmp_path / "a.env"), sink)
    assert applied.code == 0, applied.err
    pinned = write_env(tmp_path / "pinned.env", pins=sink.pins)
    planned = run_tool(estate, "--plan", "--for", "rotate-adopted", env_file=pinned, sink=sink)
    assert run_tool(estate, "--rotate-adopted", "--ack-plan", planned.digest, env_file=pinned, sink=sink).code == 0
    assert run_tool(estate, "--verify", env_file=pinned, sink=sink).code == 0
    return pinned, sink


def _grant(estate: FakeEstate, sink: FakeSink, role: str, sp_role: str, principal: str) -> None:
    assert sink.pins is not None
    estate.rule_set(sink.pins[role]).setdefault(sp_role, set()).add(principal)


@pytest.mark.parametrize(
    ("target", "sp_role", "principal", "kind"),
    [
        ("deployer", MANAGER, f"servicePrincipals/{app_id(9999)}", "a service principal"),
        ("validator", USER, "groups/other-team-automation", "a group"),
        ("jobs", MANAGER, "users/someone@other.example", "a user who is not an account admin"),
        ("deployer", USER, "users/nobody@other.example", "a user who is not an account admin"),
    ],
)
def test_a_non_admin_role_holder_on_a_secretless_identity_is_refused(
    estate: FakeEstate, installed: tuple[Path, FakeSink], target: str, sp_role: str, principal: str, kind: str
) -> None:
    env, sink = installed
    estate.add_user("someone@other.example")
    _grant(estate, sink, target, sp_role, principal)
    before = len(estate.mutations())
    for action in ("--verify", "--plan"):
        result = run_tool(estate, action, env_file=env, sink=sink)
        assert result.code == 2, result.out
        assert f"{target}: {kind} holds {sp_role} on it and could mint it a credential" in result.err
        assert principal.split("/", 1)[1] not in result.err
    assert len(estate.mutations()) == before


def test_account_admins_and_the_reviewed_grant_pass(estate: FakeEstate, installed: tuple[Path, FakeSink]) -> None:
    env, sink = installed
    estate.add_user("second-admin@owner.example", account_admin=True)
    _grant(estate, sink, "deployer", MANAGER, "users/second-admin@owner.example")
    assert sink.pins is not None
    held = estate.rule_set(sink.pins["jobs"])
    assert held[USER] == {f"servicePrincipals/{sink.pins['deployer']}"}
    assert held[MANAGER] == {f"users/{OPERATOR}"}
    verify = run_tool(estate, "--verify", env_file=env, sink=sink)
    assert verify.code == 0, verify.out + verify.err
    assert "deployer, jobs and validator hold none and no unreviewed role" in verify.out


def test_the_reviewed_grant_is_reviewed_only_for_its_grantee_and_target(
    estate: FakeEstate, installed: tuple[Path, FakeSink]
) -> None:
    env, sink = installed
    assert sink.pins is not None
    _grant(estate, sink, "validator", USER, f"servicePrincipals/{sink.pins['deployer']}")
    _grant(estate, sink, "jobs", USER, f"servicePrincipals/{sink.pins['normal']}")
    result = run_tool(estate, "--verify", env_file=env, sink=sink)
    assert result.code == 2
    assert f"validator: a service principal holds {USER} on it" in result.err
    assert f"jobs: a service principal holds {USER} on it" in result.err


def test_a_user_name_that_cannot_be_filtered_is_no_admin(estate: FakeEstate, installed: tuple[Path, FakeSink]) -> None:
    env, sink = installed
    _grant(estate, sink, "deployer", MANAGER, f"users/x' or userName eq '{OPERATOR}")
    result = run_tool(estate, "--verify", env_file=env, sink=sink)
    assert result.code == 2 and "deployer: a user who is not an account admin holds" in result.err
