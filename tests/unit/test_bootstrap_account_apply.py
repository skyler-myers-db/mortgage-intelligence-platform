"""tools/clients/bootstrap_account.py --plan/--apply/--verify against a fake account.

Covers the M2-PR1 contract: the spec's step order, the subject-before-ADMIN
call order, idempotence (a second run makes 0 mutations), id pinning and
adoption, the secretless-identity refusals, the account principal's scope,
the data-owners group's provenance and the share grants. Every test ends
with the fake's safety spy (``assert_safe``).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.fixtures.bootstrap_harness import (
    CORE_ROLES,
    RESERVED_CORE,
    FakeSink,
    plan_and_apply,
    run_tool,
    write_env,
)
from tests.fixtures.fake_databricks_account import (
    ACCOUNT_ID,
    AUDIENCE,
    METASTORE_ID,
    OPERATOR,
    REPOSITORY,
    SOURCE_CATALOG,
    SOURCE_SCHEMA,
    WORKSPACE_HOST,
    WORKSPACE_ID,
    FakeEstate,
    app_id,
    assert_safe,
)
from tools.clients.bootstrap_roster import roster

SUBJECT = f"repo:{REPOSITORY}:environment:acme"
# The account grants the creating operator (an account admin) Manager on what it creates.
CREATOR = {"roles/servicePrincipal.manager": {f"users/{OPERATOR}"}}


@pytest.fixture
def estate() -> Iterator[FakeEstate]:
    found = FakeEstate()
    yield found
    assert_safe(found)


@pytest.fixture
def env(tmp_path: Path) -> Path:
    return write_env(tmp_path / "acme.env")


def _applied(estate: FakeEstate, env: Path, *extra: str) -> FakeSink:
    sink = FakeSink()
    _planned, applied = plan_and_apply(estate, env, sink, *extra)
    assert applied.code == 0, applied.err
    assert "converged" in applied.out
    return sink


def _sp(estate: FakeEstate, name: str):  # noqa: ANN202 - FakeSp
    found = estate.by_name(name)
    assert found is not None, name
    return found


# --- the roster ------------------------------------------------------------------


def test_the_reserved_names_keep_the_deploy_tooling_names_under_the_default_prefix() -> None:
    names = {spec.role: spec.display_name("mip") for spec in roster("agentic")}
    assert names["normal"] == "mip-nightly-ci-sp"
    assert names["operator2"] == "mip-nightly-operator2-ci-sp"
    assert names["admin"] == "mip-nightly-admin-ci-sp"
    assert names["release_probe"] == "mip-release-probe-ci-sp"
    assert names["verifier"] == "mip-ai-gateway-verifier-ci-sp"
    assert names["runtime"] == "mip-agent-runtime-ci-sp"
    assert names["proxy"] == "mip-agent-supervisor-proxy-ci-sp"
    assert [spec.role for spec in roster("core")] == list(CORE_ROLES)


# --- plan -----------------------------------------------------------------------


def test_plan_is_read_only_and_follows_the_spec_order(estate: FakeEstate, env: Path) -> None:
    result = run_tool(estate, "--plan", env_file=env)
    assert result.code == 0, result.err
    assert estate.mutations() == []
    ops = [line.split()[0] + ":" + line.split()[1].split("=")[1] for line in result.steps()]
    assert ops[:3] == [
        "create_service_principal:deployer",
        "set_federation_policy:deployer",
        "assign_workspace:deployer",
    ]
    order = [line.split()[1].split("=")[1] for line in result.steps() if line.startswith("create_service_principal")]
    assert order == ["deployer", "jobs", "validator", *RESERVED_CORE, "account_principal"]
    lines = result.steps()
    assert lines.index("grant_sp_role target=jobs grantee=deployer sp_role=user") < lines.index(
        "create_group group=acme-data-owners members=deployer,jobs"
    )
    assert not [line for line in lines if line.startswith("add_group_member")]
    assert lines[-2:] == [
        f"grant_uc securable_type=catalog securable={SOURCE_CATALOG} principal=acme-data-owners privileges=USE_CATALOG",
        f"grant_uc securable_type=schema securable={SOURCE_CATALOG}.{SOURCE_SCHEMA} "
        "principal=acme-data-owners privileges=SELECT,USE_SCHEMA",
    ]
    assert f"subject={SUBJECT} change=create" in result.out
    assert f"subject={SUBJECT}-validate change=create" in result.out


def test_plan_output_names_no_identifier(estate: FakeEstate, env: Path) -> None:
    deployer = estate.add_sp("acme-deployer")
    result = run_tool(estate, "--plan", "--adopt", f"deployer={deployer.application_id}", env_file=env)
    assert result.code == 0, result.err
    document = result.document()
    assert set(document) == {"tool", "plan_version", "action", "client", "identity_prefix", "tier", "binding", "steps", "deferred"}
    for value in (ACCOUNT_ID, METASTORE_ID, str(WORKSPACE_ID), WORKSPACE_HOST, "acme.cloud", AUDIENCE, deployer.application_id, deployer.scim_id):
        assert value not in result.out + result.err, value


def test_the_digest_binds_identifying_inputs_the_printed_plan_omits(estate: FakeEstate, tmp_path: Path) -> None:
    first = run_tool(estate, "--plan", env_file=write_env(tmp_path / "a.env"))
    other = run_tool(estate, "--plan", env_file=write_env(tmp_path / "b.env", MIP_EXPECT_METASTORE_ID=None))
    assert first.steps() == other.steps()
    assert first.digest != other.digest


# --- apply and idempotence ------------------------------------------------------


def test_apply_creates_every_identity_and_a_second_run_makes_zero_mutations(estate: FakeEstate, env: Path, tmp_path: Path) -> None:
    sink = _applied(estate, env)
    assert sink.pins is not None and sorted(sink.pins) == sorted(CORE_ROLES)
    deployer, jobs, validator = (_sp(estate, f"acme-{role}") for role in ("deployer", "jobs", "validator"))
    assert sink.pins["deployer"] == deployer.application_id
    (policy,) = estate.policies[deployer.scim_id]
    assert (policy.oidc_policy.issuer, policy.oidc_policy.subject, policy.oidc_policy.audiences) == (
        "https://token.actions.githubusercontent.com",
        SUBJECT,
        [AUDIENCE],
    )
    assert estate.policies[validator.scim_id][0].oidc_policy.subject == f"{SUBJECT}-validate"
    assert estate.policies[jobs.scim_id] == []
    assert estate.assignments[deployer.scim_id] == {"ADMIN"}
    assert estate.assignments[jobs.scim_id] == {"USER"}
    assert estate.assignments[validator.scim_id] == {"USER"}
    for role in ("deployer", "jobs", "validator"):
        assert estate.secrets[_sp(estate, f"acme-{role}").scim_id] == []
    (group_id,) = estate.groups
    assert estate.groups[group_id] == ("acme-data-owners", {deployer.scim_id, jobs.scim_id})
    assert estate.rule_set(jobs.application_id) == {
        **CREATOR,
        "roles/servicePrincipal.user": {f"servicePrincipals/{deployer.application_id}"},
    }
    assert estate.grants[("catalog", SOURCE_CATALOG)] == {"acme-data-owners": {"USE_CATALOG"}}
    assert estate.grants[("schema", f"{SOURCE_CATALOG}.{SOURCE_SCHEMA}")] == {"acme-data-owners": {"SELECT", "USE_SCHEMA"}}

    pinned = write_env(tmp_path / "pinned.env", pins=sink.pins)
    before = len(estate.mutations())
    again = run_tool(estate, "--plan", env_file=pinned, sink=sink)
    assert again.code == 0 and again.steps() == []
    applied = run_tool(estate, "--apply", "--ack-plan", again.digest, env_file=pinned, sink=sink)
    assert applied.code == 0, applied.err
    assert "0 mutation(s)" in applied.out
    assert len(estate.mutations()) == before


def test_the_deployer_subject_is_set_before_its_admin_assignment(estate: FakeEstate, env: Path) -> None:
    _applied(estate, env)
    deployer = _sp(estate, "acme-deployer")
    calls = estate.calls
    policy_at = next(
        i for i, c in enumerate(calls) if c.api == "service_principal_federation_policy" and c.method == "create" and c.args[0] == int(deployer.scim_id)
    )
    admin_at = next(
        i for i, c in enumerate(calls) if c.api == "workspace_assignment" and c.method == "update" and c.args[1] == int(deployer.scim_id)
    )
    assert policy_at < admin_at
    assert [p.value for p in calls[admin_at].kwargs["permissions"]] == ["ADMIN"]


def test_an_adopted_deployer_has_its_one_policy_moved_before_admin(estate: FakeEstate, env: Path) -> None:
    deployer = estate.add_sp("legacy-ci-deployer")
    estate.add_policy(deployer, "repo:acme-org/mip-platform:environment:dev")
    sink = _applied(estate, env, "--adopt", f"deployer={deployer.application_id}")
    assert sink.pins is not None and sink.pins["deployer"] == deployer.application_id
    (policy,) = estate.policies[deployer.scim_id]
    assert (policy.oidc_policy.subject, policy.oidc_policy.audiences) == (SUBJECT, [AUDIENCE])
    update_at = next(i for i, c in enumerate(estate.calls) if c.api == "service_principal_federation_policy" and c.method == "update")
    admin_at = next(i for i, c in enumerate(estate.calls) if c.api == "workspace_assignment" and c.method == "update")
    assert update_at < admin_at
    assert estate.by_name("acme-deployer") is None


def test_the_agentic_tier_adds_the_three_runtime_identities(estate: FakeEstate, tmp_path: Path) -> None:
    sink = _applied(estate, write_env(tmp_path / "agentic.env", MIP_AGENTIC_TIER="on"), "--tier", "agentic")
    assert sink.pins is not None and {"verifier", "runtime", "proxy"} <= set(sink.pins)
    manager = f"servicePrincipals/{sink.pins['account_principal']}"
    for role in (*RESERVED_CORE, "verifier", "runtime", "proxy"):
        assert estate.rule_set(sink.pins[role])["roles/servicePrincipal.manager"] == {manager, f"users/{OPERATOR}"}


def test_the_tier_must_agree_with_the_profile(estate: FakeEstate, tmp_path: Path) -> None:
    result = run_tool(estate, "--plan", "--tier", "agentic", env_file=write_env(tmp_path / "e.env", MIP_AGENTIC_TIER="off"))
    assert result.code == 2 and "--tier agentic: must agree with MIP_AGENTIC_TIER=off" in result.err


# --- the account principal ---------------------------------------------------------


def test_the_account_principal_manages_the_reserved_identities_only(estate: FakeEstate, env: Path) -> None:
    sink = _applied(estate, env)
    assert sink.pins is not None
    manager = f"servicePrincipals/{sink.pins['account_principal']}"
    for role in RESERVED_CORE:
        assert estate.rule_set(sink.pins[role]) == {"roles/servicePrincipal.manager": {manager, f"users/{OPERATOR}"}}
    for role in ("deployer", "jobs", "validator", "account_principal"):
        held = estate.rule_set(sink.pins[role])
        assert all(manager not in principals for principals in held.values()), role
    assert not any(c.api == "service_principals" and c.method != "list" and c.method not in {"get", "create"} for c in estate.calls)
    assert all(not sp.roles for sp in estate.sps.values())


def test_an_account_admin_account_principal_is_refused(estate: FakeEstate, env: Path) -> None:
    principal = estate.add_sp("acme-account-scim-ci-sp", roles=("account_admin",))
    result = run_tool(estate, "--plan", "--adopt", f"account_principal={principal.application_id}", env_file=env)
    assert result.code == 2
    assert "account_principal: holds account admin" in result.err
    assert estate.mutations() == []


def test_an_account_principal_with_a_role_on_the_deployer_is_refused(estate: FakeEstate, env: Path) -> None:
    deployer = estate.add_sp("acme-deployer")
    principal = estate.add_sp("acme-account-scim-ci-sp")
    estate.rule_set(deployer.application_id)["roles/servicePrincipal.manager"] = {f"servicePrincipals/{principal.application_id}"}
    result = run_tool(
        estate,
        "--plan",
        "--adopt",
        f"deployer={deployer.application_id}",
        "--adopt",
        f"account_principal={principal.application_id}",
        env_file=env,
    )
    assert result.code == 2
    assert "account_principal: holds a role on the deployer identity" in result.err


# --- provenance -----------------------------------------------------------------------


def test_an_unpinned_name_match_is_refused_and_adopt_pins_it(estate: FakeEstate, env: Path) -> None:
    jobs = estate.add_sp("acme-jobs")
    refused = run_tool(estate, "--plan", env_file=env)
    assert refused.code == 2
    assert "jobs: a service principal named acme-jobs exists that this bootstrap did not pin" in refused.err
    assert jobs.application_id not in refused.err
    sink = _applied(estate, env, "--adopt", f"jobs={jobs.application_id}")
    assert sink.pins is not None and sink.pins["jobs"] == jobs.application_id
    assert len([sp for sp in estate.sps.values() if sp.display_name == "acme-jobs"]) == 1


def test_a_pin_that_names_no_identity_is_refused(estate: FakeEstate, tmp_path: Path) -> None:
    result = run_tool(estate, "--plan", env_file=write_env(tmp_path / "p.env", pins={"deployer": app_id(4242)}))
    assert result.code == 2
    assert "MIP_IDENTITY_IDS_JSON.deployer: no account service principal has that application id" in result.err


def test_a_placeholder_pin_is_refused_before_any_lookup(estate: FakeEstate, tmp_path: Path) -> None:
    result = run_tool(estate, "--plan", env_file=write_env(tmp_path / "p.env", pins={"jobs": "<jobs-application-id>"}))
    assert result.code == 2
    assert "MIP_IDENTITY_IDS_JSON: jobs: holds a <placeholder>; set a real value" in result.err
    assert estate.calls == []


def test_an_identity_alias_must_name_a_pinned_identity(estate: FakeEstate, tmp_path: Path) -> None:
    env = write_env(tmp_path / "a.env", DATABRICKS_CLIENT_ID=app_id(77))
    result = run_tool(estate, "--plan", env_file=env)
    assert result.code == 2
    assert "DATABRICKS_CLIENT_ID: names a normal identity nothing pins" in result.err


def test_adopt_must_agree_with_a_pin(estate: FakeEstate, tmp_path: Path) -> None:
    env = write_env(tmp_path / "a.env", pins={"jobs": app_id(5)})
    result = run_tool(estate, "--plan", "--adopt", f"jobs={app_id(6)}", env_file=env)
    assert result.code == 2 and "--adopt jobs: MIP_IDENTITY_IDS_JSON already pins a different identity" in result.err
    bad = run_tool(estate, "--plan", "--adopt", "jobs=nope", "--adopt", "nobody=1", env_file=env)
    assert bad.code == 2 and "--adopt jobs: must be an application id (UUID)" in bad.err


# --- secretless identities --------------------------------------------------------------


@pytest.mark.parametrize("role", ["deployer", "jobs", "validator"])
def test_a_secretless_identity_with_an_oauth_secret_is_refused(estate: FakeEstate, env: Path, role: str) -> None:
    sp = estate.add_sp(f"acme-{role}")
    estate.add_secret(sp)
    result = run_tool(estate, "--plan", "--adopt", f"{role}={sp.application_id}", env_file=env)
    assert result.code == 2
    assert f"{role}: holds 1 OAuth secret(s) and must hold none" in result.err


def test_a_deployer_with_a_token_is_refused(estate: FakeEstate, env: Path) -> None:
    sp = estate.add_sp("acme-deployer")
    estate.token_owners.add(sp.scim_id)
    result = run_tool(estate, "--plan", "--adopt", f"deployer={sp.application_id}", env_file=env)
    assert result.code == 2 and "deployer: owns a personal access or on-behalf-of token" in result.err


def test_an_unreadable_token_inventory_fails_closed(estate: FakeEstate, env: Path) -> None:
    sp = estate.add_sp("acme-deployer")
    estate.tokens_readable = False
    result = run_tool(estate, "--plan", "--adopt", f"deployer={sp.application_id}", env_file=env)
    assert result.code == 2 and "token inventory: not readable by this session" in result.err


def test_a_deployer_with_two_policies_or_a_foreign_issuer_is_refused(estate: FakeEstate, env: Path) -> None:
    two = estate.add_sp("acme-deployer")
    estate.add_policy(two, SUBJECT)
    estate.add_policy(two, f"{SUBJECT}-other")
    result = run_tool(estate, "--plan", "--adopt", f"deployer={two.application_id}", env_file=env)
    assert result.code == 2 and "deployer: holds 2 federation policies; at most 1 allowed" in result.err
    foreign = estate.add_sp("acme-validator")
    estate.add_policy(foreign, "something", issuer="https://issuer.example")
    result = run_tool(estate, "--plan", "--adopt", f"validator={foreign.application_id}", env_file=env)
    assert result.code == 2 and "validator: its federation policy trusts an issuer other than GitHub Actions" in result.err


def test_jobs_with_any_policy_or_workspace_admin_is_refused(estate: FakeEstate, env: Path) -> None:
    jobs = estate.add_sp("acme-jobs")
    estate.add_policy(jobs, SUBJECT)
    estate.assignments[jobs.scim_id] = {"ADMIN"}
    result = run_tool(estate, "--plan", "--adopt", f"jobs={jobs.application_id}", env_file=env)
    assert result.code == 2
    assert "jobs: holds 1 federation policies; at most 0 allowed" in result.err
    assert "jobs: is a workspace admin and must not be" in result.err


# --- group, interim member, share ----------------------------------------------------------


def test_a_foreign_data_owners_group_is_refused(estate: FakeEstate, env: Path) -> None:
    outsider = estate.add_user("someone@other.example")
    group_id = estate.add_group("acme-data-owners", {outsider})
    result = run_tool(estate, "--plan", env_file=env)
    assert result.code == 2 and "acme-data-owners holds members other than" in result.err
    adopted = run_tool(estate, "--plan", "--adopt", f"data_owners={group_id}", env_file=env)
    assert adopted.code == 2 and "holds members other than" in adopted.err
    estate.groups[group_id] = ("acme-data-owners", set())
    empty = run_tool(estate, "--plan", env_file=env)
    assert empty.code == 2 and "exists that this bootstrap did not create" in empty.err
    sink = _applied(estate, env, "--adopt", f"data_owners={group_id}")
    assert len(estate.groups[group_id][1]) == 2 and sink.pins is not None


def test_the_interim_member_joins_the_group(estate: FakeEstate, env: Path) -> None:
    user_id = estate.add_user("interim@owner.example")
    _applied(estate, env, "--interim-member", "interim@owner.example")
    (members,) = (m for _name, m in estate.groups.values())
    assert user_id in members and len(members) == 3
    missing = run_tool(estate, "--plan", "--interim-member", "nobody@owner.example", env_file=env)
    assert missing.code == 2 and "--interim-member: no single account user has that user name" in missing.err


def test_share_grants_are_deferred_until_the_source_exists_and_verify_reports_it(estate: FakeEstate, env: Path, tmp_path: Path) -> None:
    estate.catalogs.clear()
    sink = FakeSink()
    planned, applied = plan_and_apply(estate, env, sink)
    assert applied.code == 0 and not any(line.startswith("grant_uc") for line in planned.steps())
    assert "deferred: share_grants:" in planned.out
    verify = run_tool(estate, "--verify", env_file=write_env(tmp_path / "v.env", pins=sink.pins), sink=sink)
    assert verify.code == 1 and "share_grants:" in verify.out


def test_existing_grants_are_kept_and_only_the_missing_ones_added(estate: FakeEstate, env: Path) -> None:
    estate.grants[("schema", f"{SOURCE_CATALOG}.{SOURCE_SCHEMA}")] = {"other-team": {"SELECT", "MODIFY"}}
    _applied(estate, env)
    assert estate.grants[("schema", f"{SOURCE_CATALOG}.{SOURCE_SCHEMA}")] == {
        "other-team": {"SELECT", "MODIFY"},
        "acme-data-owners": {"SELECT", "USE_SCHEMA"},
    }


# --- binding and ack --------------------------------------------------------------------------


def test_a_session_on_another_workspace_or_metastore_is_refused(estate: FakeEstate, env: Path) -> None:
    estate.workspace_id = 4200000999
    result = run_tool(estate, "--plan", env_file=env)
    assert result.code == 2 and "the workspace session is not MIP_EXPECT_WORKSPACE_ID" in result.err
    estate.workspace_id = WORKSPACE_ID
    estate.metastore_id = app_id(31337)
    result = run_tool(estate, "--plan", env_file=env)
    assert result.code == 2 and "MIP_EXPECT_METASTORE_ID: the workspace is not on that metastore" in result.err


def test_apply_refuses_without_an_exact_ack_and_changes_nothing(estate: FakeEstate, env: Path) -> None:
    missing = run_tool(estate, "--apply", env_file=env)
    assert missing.code == 2 and "--ack-plan: apply needs the 64-hex sha256" in missing.err
    wrong = run_tool(estate, "--apply", "--ack-plan", "0" * 64, env_file=env)
    assert wrong.code == 2 and "does not match the apply plan computed now; nothing was changed" in wrong.err
    planned = run_tool(estate, "--plan", "--for", "rotate-adopted", env_file=env)
    assert planned.code == 2  # nothing pinned yet
    plan_for_apply = run_tool(estate, "--plan", env_file=env)
    estate.add_sp("acme-validator")  # the account moved between plan and apply
    moved = run_tool(estate, "--apply", "--ack-plan", plan_for_apply.digest, env_file=env)
    assert moved.code == 2
    assert estate.mutations() == []
    stray = run_tool(estate, "--plan", "--ack-plan", "0" * 64, env_file=env)
    assert stray.code == 2 and "--ack-plan: applies to --apply" in stray.err
    misplaced = run_tool(estate, "--apply", "--for", "rotate-adopted", "--ack-plan", "0" * 64, env_file=env)
    assert misplaced.code == 2 and "--for: applies to --plan only" in misplaced.err


def test_the_default_sink_refuses_before_the_first_mutation(estate: FakeEstate, env: Path) -> None:
    from tools.clients.credential_sink import UnconfiguredSink

    planned = run_tool(estate, "--plan", env_file=env)
    result = run_tool(estate, "--apply", "--ack-plan", planned.digest, env_file=env, deps_sink=UnconfiguredSink())
    assert result.code == 2 and "credential sink: none is selected; pass --sink github-environment" in result.err
    assert estate.mutations() == []


def test_input_refusals_name_fields_never_values(estate: FakeEstate, tmp_path: Path) -> None:
    env = write_env(tmp_path / "bad.env", DATABRICKS_HOST="https://acme.azuredatabricks.net", MIP_EXPECT_WORKSPACE_ID="<workspace-id>")
    result = run_tool(estate, "--plan", env_file=env)
    assert result.code == 2
    assert "MIP_EXPECT_WORKSPACE_ID: holds a <placeholder>; set a real value" in result.err
    assert "acme.azuredatabricks.net" not in result.err
    host = run_tool(estate, "--plan", env_file=write_env(tmp_path / "h.env", DATABRICKS_HOST="https://acme.azuredatabricks.net"))
    assert host.code == 2 and "DATABRICKS_HOST: must be a workspace host on aws" in host.err
