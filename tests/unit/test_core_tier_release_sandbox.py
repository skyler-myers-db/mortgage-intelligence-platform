"""A core-tier release refuses before the lease; the sites behind that refusal run for real (M2-PR3).

The client profile sets ``MIP_AGENTIC_TIER=off`` and holds only the normal,
operator2, admin and release-probe identities (plus the deployer's own roles
and the account principal): no verifier, agent runtime or proxy id or
secret, and no model-attestation key. The sandbox (tests/fixtures/
release_sandbox.py) runs ``scripts/deploy.sh -t client`` with ``DRY_RUN=0``
against a first install.

**As shipped**, the run passes the real pre-stop probes in their core mode
(no runtime or verifier argument, no Supervisor listing, the App-only Genie
clause) and the plan gate, then refuses (exit 2) before the lease and any
workspace mutation: the core tier has no activation path yet, and a release
that stops at the step-5 boundary after mutating the install would do so on
every attempt.

**With that one refusal excised from the sandbox clone** (``# >>> core tier:
release refusal >>>``), the tier-aware sites it guards, which the core
activation path will reach, run for real. The run must:

* acquire the signed lease with the core writer, the release probe;
* reach and complete the Lakebase migration, the reviewed-function grant
  reconcile (proven by the real verifier against the grants it applied) and
  the UC grant block, granting only the App;
* hand the migrate job the core tier's verifier absence flag and no verifier;
* hand the Lakebase role converger the release probe as its bootstrap
  control, which the one-use creator's real control check accepts for the
  App's and the jobs reader's roles, and hand that credential to no other
  child;
* then stop at the core activation boundary (the refusal's defence in
  depth), exit 2, and release the lease after the first-install compensation
  removed the App it created.

A missing or unreviewed tier refuses before the lease.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.fixtures.client_profiles import IDENTITY_IDS, core_tier_profile
from tests.fixtures.deploy_block_harness import block
from tests.fixtures.deploy_script import deploy_entrypoint_text
from tests.fixtures.release_sandbox import core_client_ids, first_install_estate, release

APP_CLIENT = "app-client-EXAMPLE"
AGENTIC = (IDENTITY_IDS["verifier"], IDENTITY_IDS["runtime"], IDENTITY_IDS["proxy"])


RELEASE_REFUSAL = "core tier: release refusal"


@pytest.fixture(scope="module")
def core_refused(tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    return release(
        tmp_path_factory.mktemp("core-refused"),
        core_tier_profile(),
        first_install_estate(),
        client_ids=core_client_ids(),
    )


@pytest.fixture(scope="module")
def core_release(tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    return release(
        tmp_path_factory.mktemp("core-release"),
        core_tier_profile(),
        first_install_estate(),
        client_ids=core_client_ids(),
        excise=RELEASE_REFUSAL,
    )


def _detail(run) -> str:  # type: ignore[no-untyped-def]
    return run.result.stdout[-6000:] + run.result.stderr[-6000:]


def test_a_core_release_refuses_before_the_lease_and_changes_nothing(core_refused) -> None:  # type: ignore[no-untyped-def]
    run = core_refused
    assert run.result.returncode == 2, _detail(run)
    assert "the core tier (MIP_AGENTIC_TIER=off) cannot release yet" in run.result.stderr, _detail(run)
    assert "Refusing before the lease; nothing was changed." in run.result.stderr
    # Every read-only check ran first: the real core probes passed.
    assert "[mip-prestop] pre-stop probes passed (read-only)" in run.result.stdout, _detail(run)
    assert "acquire signed workspace lease" not in run.result.stdout
    assert "lease_writer" not in run.state, "no lease was taken"
    # No workspace mutation: no App, no SQL, no job run, no bundle deploy.
    assert not [
        call["argv"]
        for call in run.cli_calls
        if call["argv"][:2] in (["apps", "create"], ["api", "post"], ["bundle", "run"])
    ]
    assert not any(module[:2] == ["tools.databricks.bundle_env", "deploy"] for module in run.modules())
    assert not any(module[0] == "tools.databricks.app_deployment_lease" for module in run.modules())


def test_the_refusal_is_the_last_read_only_step_and_spares_the_plan_and_adopt_phases() -> None:
    text = deploy_entrypoint_text()
    refusal = text.index(f"# >>> {RELEASE_REFUSAL} >>>")
    assert text.count(f"# >>> {RELEASE_REFUSAL} >>>") == 1
    # After the read-only probes and the plan gate (where --phase plan ends) ...
    assert text.index("tools.databricks.deploy_prestop_probes") < refusal
    assert text.index("# <<< bundle plan gate: pre-lease <<<") < refusal
    # ... and before the lease, the adopt phases' apply and every later step.
    for later in (
        "tools.databricks.app_deployment_lease recovery-root",
        "tools.databricks.app_deployment_lease acquire",
        'run_adopt_phase_apply "$DEPLOY_PHASE"',
        "# >>> core tier: activation boundary >>>",
    ):
        assert refusal < text.index(later), later
    lines = [line for line in block(f"# >>> {RELEASE_REFUSAL} >>>", f"# <<< {RELEASE_REFUSAL} <<<").splitlines() if not line.startswith("#")]
    # A run with no phase and --phase release (M1-PR6) alike, in a dry run too.
    assert lines[0] == 'if ! tier_is_agentic && [[ -z "${DEPLOY_PHASE:-}" || "${DEPLOY_PHASE:-}" == "release" ]]; then'
    assert lines[-2:] == ["  exit 2", "fi"]


def test_the_core_release_stops_at_the_activation_boundary_after_the_grants(core_release) -> None:  # type: ignore[no-untyped-def]
    run = core_release
    assert run.result.returncode == 2, _detail(run)
    assert "the core tier (MIP_AGENTIC_TIER=off) stops at the activation boundary" in run.result.stderr, _detail(run)
    assert "agentic tier: off (core: the normal, operator2, admin and release-probe identities only)" in run.result.stdout
    # The steps before it all ran, in order, and the last is the rollback scope.
    stdout = run.result.stdout
    order = [
        "pre-stop probes:",
        "acquire signed workspace lease for the exact App deployment",
        "core tier: no agent-runtime UC audit and no verifier Lakebase role",
        "create stopped Databricks App with resolved resource bindings",
        "migrate Lakebase",
        "reconcile and prove reviewed function EXECUTE grants after governed job",
        "apply UC grants to the app service principal",
        "provision dedicated signed App rollback-contract secret scope",
    ]
    positions = [stdout.index(step) for step in order]
    assert positions == sorted(positions), _detail(run)
    # Nothing was published or activated.
    assert not any(module[0] == "tools.databricks.app_release_source" for module in run.modules())
    assert not any(call["argv"][:2] == ["apps", "deploy"] for call in run.cli_calls)


def test_the_lease_is_acquired_with_the_core_writer(core_release) -> None:  # type: ignore[no-untyped-def]
    assert core_release.state["lease_writer"] == IDENTITY_IDS["release_probe"]
    # The compensation removed the App this first install created, then released the lease.
    assert core_release.state["first_install_deleted"] is True
    assert core_release.state["lease_released"] == "lease-sandbox-1"


def test_the_pre_stop_probes_pass_in_their_core_mode(core_refused) -> None:  # type: ignore[no-untyped-def]
    run = core_refused
    assert "[mip-prestop] pre-stop probes passed (read-only)" in run.result.stdout, _detail(run)
    assert "the core tier provisions no Supervisor" in run.result.stdout
    probes = [module for module in run.modules() if module[0] == "tools.databricks.deploy_prestop_probes"]
    assert len(probes) == 1
    assert "--agentic-tier" in probes[0] and probes[0][probes[0].index("--agentic-tier") + 1] == "off"
    assert "--runtime-application-id" not in probes[0] and "--verifier-application-id" not in probes[0]
    # The core tier needs no Supervisor API: the CLI stub refuses that listing, and no call asked for it.
    assert not any(call["argv"][:1] == ["supervisor-agents"] for call in run.cli_calls)


def test_the_grant_and_reconcile_steps_grant_only_the_app(core_release) -> None:  # type: ignore[no-untyped-def]
    statements = core_release.state["statements"]
    grants = [statement for statement in statements if statement.startswith("GRANT ")]
    assert grants, _detail(core_release)
    assert all(statement.endswith(f"TO `{APP_CLIENT}`") for statement in grants), grants
    assert not any(identity in statement for statement in statements for identity in AGENTIC)
    # The reconcile ran the real verifier for the App alone, and it passed.
    verifier = [
        module for module in core_release.modules() if module[0] == "tools.databricks.verify_reviewed_function_execute_grants"
    ]
    assert verifier and all(module[1:] == ["--catalog", "mip", "--app-application-id", APP_CLIENT, "--agentic-tier", "off"] for module in verifier)
    assert "[function-grants] verified three exact effective direct EXECUTE grants" in core_release.result.stdout
    assert core_release.state["bundle_runs"] == ["mip_lakebase_migrate", "mip_init_catalog_schemas"]
    # The pipeline namespace forbids exactly the four core identities as owners.
    namespace = [module for module in core_release.modules() if module[0] == "tools.databricks.ensure_pipeline_namespace"]
    assert namespace and all(module.count("--forbidden-owner-principal") == 4 for module in namespace), namespace


def test_the_migrate_job_gets_the_absence_flag_and_no_verifier(core_release) -> None:  # type: ignore[no-untyped-def]
    migrate = [call for call in core_release.cli_calls if call["argv"][:3] == ["bundle", "run", "mip_lakebase_migrate"]]
    assert len(migrate) == 1
    env = migrate[0]["env"]
    assert env["BUNDLE_VAR_require_ai_gateway_verifier"] == "--ai-gateway-verifier-absent"
    assert env["BUNDLE_VAR_ai_gateway_verifier_client_id"] is None


def _converges(call: dict) -> bool:  # type: ignore[type-arg]
    return call["argv"][:2] == ["-m", "tools.databricks.converge_lakebase_oauth_role"]


def test_no_child_ever_holds_an_agentic_identity(core_release) -> None:  # type: ignore[no-untyped-def]
    for call in [*core_release.python_calls, *core_release.cli_calls]:
        env = call["env"]
        if not _converges(call):
            assert env["MIP_LAKEBASE_BOOTSTRAP_CONTROL_CLIENT_ID"] is None, call["argv"]
            assert env["MIP_LAKEBASE_BOOTSTRAP_CONTROL_CLIENT_SECRET"] is None, call["argv"]
        assert not env["DATABRICKS_AGENT_RUNTIME_CLIENT_ID"], call["argv"]
        assert not env["DATABRICKS_VERIFIER_CLIENT_ID"], call["argv"]
    argv = json.dumps([call["argv"] for call in core_release.python_calls])
    assert not any(identity in argv for identity in AGENTIC)
    lakebase = [module for module in core_release.modules() if module[0] == "tools.databricks.converge_lakebase_oauth_role"]
    assert lakebase and all("--role-contract" in module and "verifier" not in module for module in lakebase)


def test_the_lakebase_roles_are_created_with_the_release_probe_as_control(core_release) -> None:  # type: ignore[no-untyped-def]
    # A first install creates both roles; the one-use creator refuses without a control.
    converges = [call for call in core_release.python_calls if _converges(call)]
    assert converges and all(
        call["env"]["MIP_LAKEBASE_BOOTSTRAP_CONTROL_CLIENT_ID"] == IDENTITY_IDS["release_probe"]
        and call["env"]["MIP_LAKEBASE_BOOTSTRAP_CONTROL_CLIENT_SECRET"]
        for call in converges
    )
    assert core_release.state["lakebase_role_creates"] == [
        {"role_contract": "app", "target": APP_CLIENT, "control": IDENTITY_IDS["release_probe"]},
        {"role_contract": "jobs_reader", "target": IDENTITY_IDS["jobs"], "control": IDENTITY_IDS["release_probe"]},
    ]


@pytest.mark.parametrize("tier", [None, "core", "On", "maybe"])
def test_a_missing_or_unreviewed_tier_refuses_before_the_lease(tmp_path: Path, tier: str | None) -> None:
    run = release(
        tmp_path, core_tier_profile(MIP_AGENTIC_TIER=tier), first_install_estate(), client_ids=core_client_ids()
    )
    assert run.result.returncode == 2, _detail(run)
    assert "MIP_AGENTIC_TIER" in run.result.stderr and "the client profile was refused" in run.result.stderr
    assert "lease_writer" not in run.state, "refused before the lease"
    assert [call["argv"] for call in run.cli_calls] == [], "refused before any Databricks call"
