"""A core-tier release runs end to end with the four core identities and no agentic call (M2-PR3, M2-PR7b).

The client profile sets ``MIP_AGENTIC_TIER=off`` and holds only the normal,
operator2, admin and release-probe identities (plus the deployer's own roles
and the account principal): no verifier, agent runtime or proxy id or
secret, and no model-attestation key. The sandbox (tests/fixtures/
release_sandbox.py) runs ``scripts/deploy.sh -t client`` with ``DRY_RUN=0``
against a first install, with a passing stand-in for the live smoke.

#311 left the core release refusing before the lease, and again at step 5.
Both refusals are lifted (M2-PR7b) because every core site behind them is
covered now, so the run must:

* pass the real pre-stop probes in their core mode, then acquire the signed
  lease with the core writer, the release probe;
* complete the Lakebase migration (the verifier absence flag, no verifier),
  the reviewed-function reconcile (the real verifier against the grants it
  applied) and the UC grants, granting only the App, with the release probe
  as the Lakebase bootstrap control and that credential handed to no other
  child;
* publish the release source, activate it once with the core payload
  (``MIP_AGENT_ORCHESTRATOR=0``, ``MIP_AI_GATEWAY=0``), smoke it with no
  agentic capability expected, sign the core capture (``app_core_rollback
  capture --agentic-tier off``), prune and release the lease;
* never call an agentic tool, hand a child an agentic identity, or reach a
  serving-endpoint or Supervisor API (a spy on every module the agentic step
  files invoke, every child's environment and every CLI call).

The lease is released on every exit path: a green run, a failed smoke and a
failed capture. A missing or unreviewed tier refuses before the lease.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.fixtures.client_profiles import IDENTITY_IDS, core_tier_profile
from tests.fixtures.deploy_block_harness import drive
from tests.fixtures.deploy_script import DEPLOY_SCRIPT, deploy_entrypoint_text
from tests.fixtures.release_sandbox import core_client_ids, first_install_estate, release
from tests.fixtures.release_sandbox_python import LEASE_ID

APP_CLIENT = "app-client-EXAMPLE"
AGENTIC = (IDENTITY_IDS["verifier"], IDENTITY_IDS["runtime"], IDENTITY_IDS["proxy"])
LIB = DEPLOY_SCRIPT.parent / "lib"
AGENTIC_FILES = (
    "deploy_step_agentic_provisioning.sh",
    "deploy_step_agentic_cutover.sh",
    "deploy_step_agent_eval_and_smoke.sh",
)
GUARD = "# Agentic-only (M2-PR7b): deploy.sh sources this in the agentic tier only; the core tier refuses.\n"


def _modules_in(text: str) -> set[str]:
    return set(re.findall(r"-m (tools\.[A-Za-z0-9_.]+)", text))


def agentic_only_modules() -> set[str]:
    """Every tool the agentic step files invoke that no other deploy file does."""

    agentic = "".join((LIB / name).read_text(encoding="utf-8") for name in AGENTIC_FILES)
    others = "".join(
        path.read_text(encoding="utf-8")
        for path in [DEPLOY_SCRIPT, *sorted(LIB.glob("*.sh"))]
        if path.name not in AGENTIC_FILES
    )
    return _modules_in(agentic) - _modules_in(others)


def _core(tmp_path_factory: pytest.TempPathFactory, name: str, **kwargs):  # type: ignore[no-untyped-def]
    return release(
        tmp_path_factory.mktemp(name),
        core_tier_profile(),
        first_install_estate(),
        client_ids=core_client_ids(),
        smoke=True,
        **kwargs,
    )


@pytest.fixture(scope="module")
def core_release(tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    return _core(tmp_path_factory, "core-release")


def _detail(run) -> str:  # type: ignore[no-untyped-def]
    return run.result.stdout[-6000:] + run.result.stderr[-6000:]


def test_a_core_release_runs_end_to_end_in_the_sandbox(core_release) -> None:  # type: ignore[no-untyped-def]
    run = core_release
    assert run.result.returncode == 0, _detail(run)
    assert "[deploy] complete." in run.result.stdout
    for refusal in ("cannot release yet", "stops at the activation boundary", "agentic-only and refuses"):
        assert refusal not in run.result.stderr, refusal
    assert "agentic tier: off (core: the normal, operator2, admin and release-probe identities only)" in run.result.stdout
    order = [
        "[mip-prestop] pre-stop probes passed (read-only)",
        "acquire signed workspace lease for the exact App deployment",
        "core tier: no agent-runtime UC audit and no verifier Lakebase role",
        "create stopped Databricks App with resolved resource bindings",
        "migrate Lakebase",
        "reconcile and prove reviewed function EXECUTE grants after governed job",
        "apply UC grants to the app service principal",
        "provision dedicated signed App rollback-contract secret scope",
        "publish App release source",
        "prove blue's tier-b restore payload before green activation",
        "deploy first-install Databricks App snapshot from uploaded bundle source",
        "rebind Genie space",
        "core tier: skip the agentic provisioning, cutover and Agent Evaluation",
        "live smoke — scripts/smoke_live.sh against the deployed core App",
        "atomically restore treatment authority and persist the signed core last-good App",
        "prune App release folders no retained deployment references",
    ]
    positions = [run.result.stdout.index(step) for step in order]
    assert positions == sorted(positions), _detail(run)
    assert run.state["lease_writer"] == IDENTITY_IDS["release_probe"]
    assert run.state["lease_released"] == LEASE_ID


def test_the_one_activation_ships_the_core_payload_and_the_capture_is_the_core_capture(core_release) -> None:  # type: ignore[no-untyped-def]
    run = core_release
    payload = run.state["deploys"]
    assert len(payload) == 1, "one activation rollover"
    env = {item["name"]: item.get("value") for item in payload[0]["env_vars"]}
    assert (env["MIP_AGENT_ORCHESTRATOR"], env["MIP_AI_GATEWAY"]) == ("0", "0")
    from tools.databricks.app_rollback_record_v7 import is_agentic_env_name

    assert not [name for name in env if is_agentic_env_name(name)], "no agentic name, the two switches off"
    assert payload[0]["source_code_path"].endswith(f"/app-src/{env['MIP_GIT_SHA'][:12]}-{'0' * 12}")
    assert run.state["smoke"] == [f"agentic=0 gateway=0 sha={env['MIP_GIT_SHA']}"], "Genie is smoked, no agentic row"
    assert run.state["core_rollback"] == ["capture"]
    (capture,) = [m for m in run.modules() if m[0] == "tools.databricks.app_core_rollback"]
    pairs = dict(zip(capture[2::2], capture[3::2], strict=False))
    assert capture[1] == "capture" and pairs["--agentic-tier"] == "off"
    assert pairs["--release-id"] == payload[0]["source_code_path"].rsplit("/", 1)[1]
    assert pairs["--stage-dir"].endswith("app-release/source") and "--expected-gateway-binding" not in capture
    proof = [m for m in run.modules() if m[0] == "tools.databricks.blue_restore_proof"]
    assert len(proof) == 1 and proof[0][proof[0].index("--agentic-tier") + 1] == "off"


def test_the_core_release_makes_zero_agentic_calls(core_release) -> None:  # type: ignore[no-untyped-def]
    run = core_release
    spied = agentic_only_modules()
    assert {"tools.databricks.provision_agentic_resources", "tools.databricks.run_agent_eval"} <= spied
    assert "tools.databricks.export_gateway_runtime_contract" in spied
    called = {module[0] for module in run.modules()}
    assert not called & spied, sorted(called & spied)
    assert not any(re.search(r"gateway|supervisor|agent_proxy|agent_runtime|agent_eval", m) for m in called), called
    assert "tools.databricks.app_deployment_rollback" not in called, "the v5/v6 rollback tool is agentic's"
    for call in [*run.python_calls, *run.cli_calls]:
        assert not call["env"]["DATABRICKS_AGENT_RUNTIME_CLIENT_ID"], call["argv"]
        assert not call["env"]["DATABRICKS_VERIFIER_CLIENT_ID"], call["argv"]
    argv = json.dumps([call["argv"] for call in [*run.python_calls, *run.cli_calls]])
    assert not any(identity in argv for identity in AGENTIC)
    assert not any(call["argv"][:1] in (["serving-endpoints"], ["supervisor-agents"]) for call in run.cli_calls)


@pytest.mark.parametrize(
    ("fail", "message"),
    [({"smoke": 1}, "smoke test failed"), ({"capture": 2}, "green capture failed after treatment restoration")],
)
def test_the_lease_is_released_on_every_exit_path(
    tmp_path_factory: pytest.TempPathFactory, fail: dict[str, int], message: str
) -> None:
    run = _core(tmp_path_factory, "core-fail", fail=fail)
    assert run.result.returncode != 0, _detail(run)
    assert message in run.result.stderr, _detail(run)
    assert "[deploy] complete." not in run.result.stdout
    # The first-install compensation removed the App this run created, then released the lease.
    assert run.state["first_install_deleted"] is True, _detail(run)
    assert run.state["lease_released"] == LEASE_ID, _detail(run)


def test_both_refusals_are_lifted_and_the_agentic_steps_are_explicit_core_skips(tmp_path: Path) -> None:
    text = deploy_entrypoint_text()
    for marker in ("core tier: release refusal", "core tier: activation boundary"):
        assert marker not in text, marker
    entry = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    branch = entry[entry.index("\nif tier_is_agentic; then\n") : entry.index("\nfi\n", entry.index("deploy_step_core_activation.sh"))]
    agentic, core = branch.split("\nelse\n")
    assert all(f'. "$REPO_ROOT/scripts/lib/{name}"' in agentic for name in AGENTIC_FILES)
    assert '. "$REPO_ROOT/scripts/lib/deploy_step_core_activation.sh"' in core and "agentic" not in core.split("#")[-1]
    for name in AGENTIC_FILES:
        body = (LIB / name).read_text(encoding="utf-8").split("\n\n", 1)[1]
        assert body.startswith(GUARD), name
        guard = "\n".join(body.splitlines()[:2]) + "\n"
        assert guard.splitlines()[1].startswith("tier_is_agentic || {"), name
        for tier, code in (("off", 2), ("on", 0)):
            result = drive(
                tmp_path,
                'tier_is_agentic() { [[ "$CLIENT_AGENTIC_TIER" == "on" ]]; }\n' + guard,
                dry_run=0,
                env={"CLIENT_AGENTIC_TIER": tier},
                epilogue="",
            )
            assert result.returncode == code, (name, tier, result.stderr)
        assert "is agentic-only and refuses the core tier" in result.stderr or code == 0


def test_the_pre_stop_probes_pass_in_their_core_mode(core_release) -> None:  # type: ignore[no-untyped-def]
    run = core_release
    assert "the core tier provisions no Supervisor" in run.result.stdout
    probes = [module for module in run.modules() if module[0] == "tools.databricks.deploy_prestop_probes"]
    assert len(probes) == 1
    assert probes[0][probes[0].index("--agentic-tier") + 1] == "off"
    assert "--runtime-application-id" not in probes[0] and "--verifier-application-id" not in probes[0]


def test_the_grant_and_reconcile_steps_grant_only_the_app(core_release) -> None:  # type: ignore[no-untyped-def]
    statements = core_release.state["statements"]
    grants = [statement for statement in statements if statement.startswith("GRANT ")]
    assert grants, _detail(core_release)
    assert all(statement.endswith(f"TO `{APP_CLIENT}`") for statement in grants), grants
    assert not any(identity in statement for statement in statements for identity in AGENTIC)
    verifier = [
        module for module in core_release.modules() if module[0] == "tools.databricks.verify_reviewed_function_execute_grants"
    ]
    assert verifier and all(module[1:] == ["--catalog", "mip", "--app-application-id", APP_CLIENT, "--agentic-tier", "off"] for module in verifier)
    assert "[function-grants] verified three exact effective direct EXECUTE grants" in core_release.result.stdout
    assert core_release.state["bundle_runs"] == [
        "mip_lakebase_migrate",
        "mip_init_catalog_schemas",
        "mip_refresh_scores",
        "mip_kpi_snapshot",
    ]
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


def test_the_lakebase_roles_use_the_release_probe_as_control_and_no_other_child_holds_it(core_release) -> None:  # type: ignore[no-untyped-def]
    converges = [call for call in core_release.python_calls if _converges(call)]
    assert converges and all(
        call["env"]["MIP_LAKEBASE_BOOTSTRAP_CONTROL_CLIENT_ID"] == IDENTITY_IDS["release_probe"]
        and call["env"]["MIP_LAKEBASE_BOOTSTRAP_CONTROL_CLIENT_SECRET"]
        for call in converges
    )
    for call in [*core_release.python_calls, *core_release.cli_calls]:
        if not _converges(call):
            assert call["env"]["MIP_LAKEBASE_BOOTSTRAP_CONTROL_CLIENT_ID"] is None, call["argv"]
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
