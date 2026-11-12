"""The pre-lease activation prediction's shell sites in the command of record (B3).

The functions are the real ones (``deploy_entrypoint_text()``), driven in the
block harness (tests/fixtures/deploy_block_harness.py) with a recording stub
predictor. Pinned here: where the prediction sits (the plan after the
active-deployment digest; a release, and a run with no --phase, after the
dispatch's expected-deployment check, all before the signed lease; nothing in
data, adopt or unadopt), that a refusal exits 2 with no lease and nothing
changed, that its payload flags are the rebuild's byte for byte, that it never
runs under the proof-signing wrapper, which acks reach it and which never do,
that a plan which stops at the client live checks measures blue's env-name
parity first, and the lexical guard that keeps the digest it prints equal to
the late gate's: no step after it assigns a payload-builder input.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from tests.fixtures.deploy_block_harness import block, drive, function
from tests.fixtures.deploy_script import deploy_entrypoint_text
from tools.databricks import activation_prediction, app_deploy_payload

REPO = Path(__file__).resolve().parents[2]
LIB = REPO / "scripts" / "lib"
BASE = {
    "_GRANTS_APP_NAME": "synthetic-app",
    "APP_ROLLBACK_SECRET_SCOPE": "synthetic-app-rollback",
    "MIP_LAKEBASE_INSTANCE": "synthetic-state",
    "LAKEBASE_DATABASE": "synthetic_state_db",
    "CLIENT_AGENTIC_TIER": "off",
    "CLIENT_INSTALL_CLASS": "demo",
    "APP_RUNTIME_ENV": "sandbox",
    "SOURCE_GIT_SHA": "0" * 40,
}
FRONTEND_ACK = "f" * 64
NO_ROLLBACK_VALUE = "e" * 64
PREDICT = 'run "$PYTHON" -m tools.databricks.activation_prediction predict "${args[@]}"'


def _stub(repo: Path, rc: int) -> Path:
    package = repo / "tools" / "databricks"
    package.mkdir(parents=True, exist_ok=True)
    for init in (repo / "tools" / "__init__.py", package / "__init__.py"):
        init.write_text("", encoding="utf-8")
    recorded = repo / "calls.jsonl"
    (package / "activation_prediction.py").write_text(
        "import json, sys\n"
        f"open({str(recorded)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
        f"sys.exit({rc})\n",
        encoding="utf-8",
    )
    return recorded


def _run(
    tmp_path: Path, body: str, *, env: dict[str, str] | None = None, rc: int = 0, dry_run: int = 0
) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    recorded = _stub(repo, rc)
    shell = (
        'run_with_proof_signing_authority() { echo "SIGNING $*" >&2; "$@"; }\n'
        'publish_plan_report() { echo plan-report-published; }\n'
        + function("tier_is_agentic")
        + function("predict_activation_rollback")
        + function("measure_blue_env_names_before_adoption")
        + body
    )
    result = drive(repo, shell, dry_run=dry_run, env={**BASE, **(env or {})}, epilogue="")
    calls = [json.loads(line) for line in recorded.read_text().splitlines()] if recorded.exists() else []
    return result, calls


def _flags(argv: list[str]) -> dict[str, str]:
    return {argv[i]: argv[i + 1] for i in range(len(argv) - 1) if argv[i].startswith("--") and not argv[i + 1].startswith("--")}


# --- where it sits --------------------------------------------------------------------------


def test_the_plan_and_the_release_predict_before_the_lease_and_nothing_else_does() -> None:
    text = deploy_entrypoint_text()
    lease = text.index("app_deployment_lease acquire")
    calls = [match.start() for match in re.finditer(r"^ *predict_activation_rollback$", text, re.M)]
    assert len(calls) == 2, "the plan block and the release/no-phase block only"
    plan, release = calls
    assert text.index("  print_active_deployment_digest\n") < plan < text.index("  publish_plan_report\n  echo")
    assert text.index("  check_expected_active_deployment\n") < release < lease
    assert text.index("run_bundle_plan_gate pre-lease") < plan
    guard = text[text.rindex("\nif ", 0, release) : release]
    assert guard == '\nif [[ "${DEPLOY_PHASE:-}" == "release" || -z "${DEPLOY_PHASE:-}" ]]; then\n', guard
    # The plan site is inside the plan block, which exits 0 (its exact code is
    # test_bundle_plan_gate_deploy_contract.py's), so data, adopt and unadopt
    # reach neither site.
    plan_block = text[text.rindex('if [[ "${DEPLOY_PHASE:-}" == "plan" ]]; then\n', 0, plan) :]
    assert plan_block.index("  exit 0\nfi\n") > plan_block.index("predict_activation_rollback")
    assert text.count("measure_blue_env_names_before_adoption") == 2, "its definition and the live-check site"


def test_a_refused_prediction_exits_2_before_the_lease_and_publishes_a_plans_report(tmp_path: Path) -> None:
    for phase in ("release", "", "plan"):
        result, calls = _run(
            tmp_path / (phase or "none"),
            "predict_activation_rollback\necho app_deployment_lease acquire\n",
            env={"DEPLOY_PHASE": phase},
            rc=2,
        )
        assert result.returncode == 2, result.stderr
        assert "refusing before the lease. Nothing was changed." in result.stderr
        assert "app_deployment_lease acquire" not in result.stdout
        assert len(calls) == 1 and calls[0][:3] == ["predict", "--phase", phase or "complete"]
        # The real publish_plan_report writes only in a plan (deploy_plan_report_lifecycle.sh).
        assert "plan-report-published" in result.stdout


def test_its_payload_flags_are_the_rebuilds_byte_for_byte_and_it_holds_no_signing_key() -> None:
    def flags(body: str) -> list[str]:
        return [line.strip().removesuffix(" \\") for line in body.splitlines() if line.strip().startswith("--")]

    rebuild = [f for f in flags(function("rebuild_core_blue_restore_payload")) if not f.startswith("--out ")]
    predicted = flags(function("predict_activation_rollback"))
    assert predicted[1:10] == rebuild, (predicted, rebuild)
    assert predicted[0] == '--phase "${DEPLOY_PHASE:-complete}"' and predicted[10] == '--source-git-sha "$SOURCE_GIT_SHA"'
    for name in ("predict_activation_rollback", "measure_blue_env_names_before_adoption"):
        body = function(name)
        assert "run_with_proof_signing_authority" not in body and "run_with_account_identity" not in body, name
        assert "activation_prediction" in body and body.count('run "$PYTHON" -m tools.databricks.activation_prediction') == 1
    assert PREDICT in function("predict_activation_rollback")


@pytest.mark.parametrize("phase", ["release", "", "plan"])
def test_the_frontend_ack_reaches_it_and_the_no_rollback_value_never_does(tmp_path: Path, phase: str) -> None:
    env = {
        "DEPLOY_PHASE": phase,
        "MIP_ACK_RESTORE_UNVERIFIED_FRONTEND": FRONTEND_ACK,
        "MIP_ACK_NO_ROLLBACK_FOR": NO_ROLLBACK_VALUE,
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
    }
    result, calls = _run(tmp_path, "predict_activation_rollback\n", env=env)
    assert result.returncode == 0, result.stderr
    (argv,) = calls
    flags = _flags(argv)
    assert flags["--restore-unverified-frontend-ack"] == FRONTEND_ACK
    assert flags["--app-name"] == "synthetic-app" and flags["--lakebase-database"] == "synthetic_state_db"
    assert NO_ROLLBACK_VALUE not in " ".join(argv) + result.stdout + result.stderr
    assert ("--no-rollback-ack-given" in argv) is (phase != "plan"), "a plan never defers"
    assert ("--step-summary" in argv) is (phase == "plan"), "only a plan writes the step summary"
    assert "SIGNING" not in result.stderr

    _result, bare = _run(tmp_path / "bare", "predict_activation_rollback\n", env={"DEPLOY_PHASE": phase})
    assert "--restore-unverified-frontend-ack" not in bare[0] and "--no-rollback-ack-given" not in bare[0]


def test_the_agentic_tier_and_a_dry_run_run_nothing(tmp_path: Path) -> None:
    agentic, calls = _run(tmp_path / "a", "predict_activation_rollback\nmeasure_blue_env_names_before_adoption\n",
                          env={"CLIENT_AGENTIC_TIER": "on", "DEPLOY_PHASE": "plan"})  # fmt: skip
    assert agentic.returncode == 0 and calls == []
    dry, calls = _run(tmp_path / "d", "predict_activation_rollback\n", env={"DEPLOY_PHASE": "release"}, dry_run=1)
    assert dry.returncode == 0 and calls == []
    assert "would predict read-only, before the lease" in dry.stdout


# --- the plans before an adoption: blue's env-name parity at the live checks ---------------


def test_a_plan_the_live_checks_refuse_measures_env_names_first_and_still_exits_2() -> None:
    text = block("# >>> client live checks >>>", "# <<< client live checks <<<")
    branch = text[text.index('if [[ "$DRY_RUN" -eq 0 ]] && ! "$PYTHON" -m tools.clients.live_checks') :]
    lines = [line.strip() for line in branch.splitlines()[1:4]]
    assert lines[0].startswith("measure_blue_env_names_before_adoption")
    assert lines[1].startswith('echo "${RED}[deploy] the client live checks refused') and lines[2] == "exit 2"


@pytest.mark.parametrize(("phase", "tier", "runs"), [("plan", "off", True), ("plan", "on", False), ("release", "off", False), ("", "off", False), ("adopt", "off", False)])
def test_env_names_runs_only_in_a_core_plan_and_never_decides_the_exit(
    tmp_path: Path, phase: str, tier: str, runs: bool
) -> None:
    result, calls = _run(
        tmp_path, "measure_blue_env_names_before_adoption\necho after-the-measure\n",
        env={"DEPLOY_PHASE": phase, "CLIENT_AGENTIC_TIER": tier, "GITHUB_STEP_SUMMARY": str(tmp_path / "s.md")}, rc=2,
    )  # fmt: skip
    assert result.returncode == 0 and "after-the-measure" in result.stdout
    assert calls == ([["env-names", "--step-summary", str(tmp_path / "s.md")]] if runs else [])


# --- the digest the plan prints is the late gate's: the shell's builder inputs ---------------

# Every payload-builder input scripts/ assigns, and why the predictor still sees
# what the late gate sees. "derived": the preflight or the automation step
# derives it after the client live checks, always non-empty (the env-names
# stand-ins, activation_prediction.GOVERNED_DERIVED_NAMES). "same": the builder
# reads the same profile value itself. "controlled": the rebuild sets it
# (blue's own commit; the lease only when blue's names carry it). "agentic":
# never in a core payload (CORE_TIER_DROPPED_NAMES). "not_a_name": decides no
# payload name, only a refusal the builder repeats. "prefix": a one-command
# environment that leaves the shell's value as it was.
SHELL_ASSIGNED = {
    "MIP_ADMIN_IDENTITIES": "derived",
    "MIP_LAKEBASE_SYNC_CATALOG": "derived",
    "MIP_LAKEBASE_SYNC_SCHEMA": "derived",
    "MIP_LAKEBASE_SYNC_TABLES": "derived",
    "MIP_LENDER_NAME": "same",
    "MIP_LENDER_NMLS_ID": "same",
    "MIP_TENANT_ID": "same",
    "MIP_LENDER_IDENTITY_JSON": "same",
    "MIP_LENDER_IDENTITY_SHA256": "same",
    "MIP_COTALITY_SOURCE_CATALOG": "same",
    "MIP_COTALITY_SOURCE_SCHEMA": "same",
    "MIP_COTALITY_SOURCE_TABLE_PREFIX": "same",
    "MIP_GIT_SHA": "controlled",
    "MIP_APP_DEPLOYMENT_LEASE_ID": "controlled",
    "MIP_SALESFORCE_BINDINGS": "not_a_name",
    "MIP_AGENT_SUPERVISOR_NAME": "agentic",
    "MIP_AI_GATEWAY_PROOF_VERIFY_KEY": "agentic",
    "MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY": "agentic",
    "MIP_GATEWAY_MODEL_ATTESTATION_PREVIOUS_VERIFY_KEY": "agentic",
    "MIP_REVIEWED_FUNCTION_OWNER": "agentic",
    # The auditor segregation-of-duties check's one-command environment, inside
    # a command substitution: the shell's own value is unchanged.
    "MIP_AUDITOR_EMAILS": "prefix",
    "MIP_AUDITOR_IDENTITIES": "prefix",
    "MIP_ADMIN_EMAILS": "prefix",
    "MIP_APPROVER_EMAILS": "prefix",
    "MIP_APPROVER_IDENTITIES": "prefix",
}


def _builder_inputs() -> set[str]:
    source = (REPO / "tools" / "databricks" / "app_deploy_payload.py").read_text(encoding="utf-8")
    from backend.config import source_names
    from backend.schemas.lender_identity_record import LEGACY_ENV_NAMES, PINNED_ENV_NAMES

    names = {*app_deploy_payload.NON_SECRET_OPERATOR_VARS, *app_deploy_payload.SECRET_RESOURCE_BINDINGS}
    names |= {*LEGACY_ENV_NAMES, *PINNED_ENV_NAMES, app_deploy_payload.SALESFORCE_BINDINGS_ENV}
    names |= {source_names.CATALOG_ENV, source_names.SCHEMA_ENV, source_names.TABLE_PREFIX_ENV}
    names |= set(re.findall(r'_env_value\("([A-Z0-9_]+)"', source)) | {"MIP_PRESENTER_MODE"}
    # Read through a constant, so the pattern above misses it: its presence adds two names.
    return names | {app_deploy_payload.PREVIOUS_SECRET_KID_ENV}


def _assignments(text: str, names: set[str]) -> set[str]:
    alternation = "|".join(sorted(names, key=len, reverse=True))
    found = set(re.findall(rf"(?<![A-Za-z0-9_$])({alternation})(?:\+)?=", text))
    for line in text.splitlines():
        code = line.split(" #", 1)[0]
        if re.match(r"\s*(export|read)\b", code):
            found |= {word for word in re.findall(r"[A-Z][A-Z0-9_]+", code) if word in names}
    return found


def _shell_texts() -> dict[str, str]:
    texts = {path.name: path.read_text(encoding="utf-8") for path in sorted(LIB.glob("*.sh"))}
    texts["deploy.sh"] = (REPO / "scripts" / "deploy.sh").read_text(encoding="utf-8")
    return {name: "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#")) for name, text in texts.items()}


def test_every_builder_input_the_shell_assigns_is_reviewed() -> None:
    names = _builder_inputs()
    assigned = set().union(*(_assignments(text, names) for text in _shell_texts().values()))
    assert assigned == set(SHELL_ASSIGNED), sorted(assigned ^ set(SHELL_ASSIGNED))
    derived = {name for name, kind in SHELL_ASSIGNED.items() if kind == "derived"}
    assert derived == set(activation_prediction.GOVERNED_DERIVED_NAMES)
    assert {n for n, k in SHELL_ASSIGNED.items() if k == "agentic"} <= app_deploy_payload.CORE_TIER_DROPPED_NAMES | {
        "MIP_REVIEWED_FUNCTION_OWNER"
    }
    # The derived ones are never blank by the activation: a default or the role ids.
    gates = (LIB / "deploy_step_preflight_gates.sh").read_text(encoding="utf-8")
    for name, default in (("CATALOG", "mip_app_state"), ("SCHEMA", "mip_sync"), ("TABLES", "'source_readiness,")):
        assert f'MIP_LAKEBASE_SYNC_{name}="$(deployment_control_value MIP_LAKEBASE_SYNC_{name} {default}' in gates
    automation = (LIB / "deploy_step_automation_identities.sh").read_text(encoding="utf-8")
    assert '"$DATABRICKS_ADMIN_CLIENT_ID" \\\n    "$DATABRICKS_RELEASE_PROBE_CLIENT_ID" <<\'PYEOF\'' in automation


def test_no_step_after_the_prediction_assigns_a_builder_input_but_the_controlled_ones() -> None:
    """The plan's digest equals the late gate's only while nothing between them changes a builder input."""

    names = _builder_inputs()
    automation = (LIB / "deploy_step_automation_identities.sh").read_text(encoding="utf-8")
    after = [automation[automation.rindex("  predict_activation_rollback\n") :]]
    order = (REPO / "scripts" / "deploy.sh").read_text(encoding="utf-8")
    later = [
        path for path in sorted(LIB.glob("deploy_step_*.sh"))
        if path.name != "deploy_step_preflight_gates.sh" and path.name != "deploy_step_automation_identities.sh"
        and order.index(path.name) > order.index("deploy_step_automation_identities.sh")
    ]  # fmt: skip
    assert {path.name for path in later} >= {
        "deploy_step_bundle_apply.sh", "deploy_step_lakebase_and_uc_grants.sh",
        "deploy_step_app_promotion_and_refresh.sh", "deploy_step_core_activation.sh",
    }  # fmt: skip
    after += [path.read_text(encoding="utf-8") for path in later]
    assigned = set().union(*(_assignments(text, names) for text in after))
    allowed = {"MIP_APP_DEPLOYMENT_LEASE_ID", "MIP_GIT_SHA", "MIP_REVIEWED_FUNCTION_OWNER"}
    assert assigned <= allowed, sorted(assigned - allowed)
    # The green payload's own commit is a prefix of its builder call only.
    promotion = (LIB / "deploy_step_app_promotion_and_refresh.sh").read_text(encoding="utf-8")
    assert 'MIP_GIT_SHA="$git_sha" "$PYTHON" -m tools.databricks.app_deploy_payload' in promotion
    # The one lifecycle function a later step calls that assigns one restores the preflight's values.
    restore = function("restore_deployment_sync_contract")
    assert 'MIP_LAKEBASE_SYNC_CATALOG="$DEPLOYMENT_SYNC_CATALOG"' in restore
    gates = (LIB / "deploy_step_preflight_gates.sh").read_text(encoding="utf-8")
    assert 'DEPLOYMENT_SYNC_CATALOG="$MIP_LAKEBASE_SYNC_CATALOG"' in gates
    assigners = {"restore_deployment_sync_contract", "resolve_salesforce_bindings_switch"}
    for text in after:
        called = set(re.findall(r"^\s*([a-z_]+)\b", text, re.M))
        assert not (called & assigners - {"restore_deployment_sync_contract"}), called & assigners


def test_no_env_file_a_core_release_sources_carries_a_builder_input_but_the_lease() -> None:
    """A tool's --out-env file is sourced under allexport, so it could move a builder input as an assignment could."""

    names = _builder_inputs()
    writers: dict[str, set[str]] = {}
    for path in sorted((REPO / "tools").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "out_env" in text or "out-env" in text or "_write_env" in text:
            found = {name for name in names if re.search(rf'"{name}"\s*:|"{name}=', text)}
            if found:
                writers[path.relative_to(REPO).as_posix()] = found
    assert writers == {"tools/databricks/app_deployment_lease_cli.py": {"MIP_APP_DEPLOYMENT_LEASE_ID"}}, writers
    # The signed blue's binding env (step 0a, the window's re-pin, the promotion) names record facts only.
    binding = (REPO / "tools" / "databricks" / "blue_restore_proof.py").read_text(encoding="utf-8")
    body = binding[binding.index("def _binding_env(") : binding.index("def inspect_v7(")]
    keys = set(re.findall(r'"([A-Z][A-Z0-9_]+)":', body))
    assert keys and all(key.startswith("MIP_APP_ROLLBACK_") for key in keys) and not keys & names
    # The agentic provisioner's cached --out-env is never carried into a core release.
    promotion = (LIB / "deploy_step_app_promotion_and_refresh.sh").read_text(encoding="utf-8")
    core_guard = 'if [[ "$DRY_RUN" -eq 0 && -f "$AGENTIC_ENV_CACHE" ]] && ! tier_is_agentic; then'
    assert promotion.count('. "$AGENTIC_ENV_CACHE"') == 1
    guard = promotion.index(core_guard)  # the sourcing sits in that guard's own elif branch
    assert guard < promotion.index("elif [[", guard) < promotion.index('. "$AGENTIC_ENV_CACHE"') < promotion.index("\nfi\n", guard)
