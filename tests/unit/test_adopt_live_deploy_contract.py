"""adopt-live and the pre-activation proof in the command of record (M2-PR7).

The three shell functions of scripts/lib/deploy_adopt_phase_lifecycle.sh that
call adopt-live and the pre-activation proof are driven in the block harness
with a recording stub tool, and their call sites are pinned by position on
``deploy_entrypoint_text()``: adopt-live's plan runs before the lease and its
apply after the bind, under it; the proof runs once, at the top of the
release's activation function, after the flag the entrypoint resets.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.fixtures.deploy_block_harness import drive, function
from tests.fixtures.deploy_script import deploy_entrypoint_text

ACK = "a" * 64
OTHER = "b" * 64
SUPERSEDE = "c" * 64
RELEASE_ID = f"{'0' * 12}-{'1' * 12}"


def _tool(repo: Path, module: str, rc: int) -> Path:
    package = repo / "tools" / "databricks"
    package.mkdir(parents=True, exist_ok=True)
    for init in (repo / "tools" / "__init__.py", package / "__init__.py"):
        init.write_text("", encoding="utf-8")
    recorded = repo / f"{module}.argv.jsonl"
    (package / f"{module}.py").write_text(
        f"import json, sys\nopen({str(recorded)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\nsys.exit({rc})\n",
        encoding="utf-8",
    )
    return recorded


def _calls(recorded: Path) -> list[list[str]]:
    if not recorded.exists():
        return []
    return [json.loads(line) for line in recorded.read_text(encoding="utf-8").splitlines()]


def _drive(
    tmp_path: Path,
    body: str,
    *,
    module: str,
    rc: int = 0,
    env: dict[str, str] | None = None,
    dry_run: int = 0,
):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    recorded = _tool(repo, module, rc)
    shell = (
        f'mint_m2m_token() {{ echo "minted $1" >> {str(repo / "mint")!r}; }}\n'
        'run_with_proof_signing_authority() { "$@"; }\n'
        + function("tier_is_agentic")
        + function("adopt_live_mode")
        + function("run_adopt_live_plan")
        + function("run_adopt_live_apply")
        + function("prove_blue_restorable_before_activation")
        + function("rebuild_core_blue_restore_payload")
        + body
    )
    values = {
        "_GRANTS_APP_NAME": "synthetic-app",
        "APP_ROLLBACK_SECRET_SCOPE": "synthetic-app-rollback",
        "MIP_LAKEBASE_INSTANCE": "synthetic-state",
        "CLIENT_DEPLOYER": "00000000-0000-4000-8000-000000000001",
        "CLIENT_KEY": "client-one",
        "CLIENT_AGENTIC_TIER": "off",
        "SOURCE_GIT_SHA": "0" * 40,
        "APP_RELEASE_ID": RELEASE_ID,
        "MIP_APP_DEPLOYMENT_LEASE_ID": "lease-1",
        "BLUE_RESTORE_PROVEN": "0",
        **(env or {}),
    }
    result = drive(repo, shell, dry_run=dry_run, env=values, epilogue="echo reached-the-end\n")
    return result, _calls(recorded), (repo / "mint")


# --- adopt-live's plan, before the lease ----------------------------------------------


def test_the_plan_passes_the_mode_and_the_profiles_target(tmp_path: Path) -> None:
    result, calls, mint = _drive(tmp_path, "run_adopt_live_plan adopt\n", module="adopt_live")

    assert result.returncode == 0, result.stderr
    (argv,) = calls
    assert argv[:3] == ["plan", "--mode", "adopt"]
    pairs = dict(zip(argv[3::2], argv[4::2], strict=False))
    assert pairs["--expected-principal"] == "00000000-0000-4000-8000-000000000001"
    assert pairs["--expected-root-leaf"] == "client-one" and pairs["--agentic-tier"] == "off"
    assert pairs["--main-ref"] == "refs/remotes/origin/main", "main is never the run's own HEAD"
    assert pairs["--source-git-sha"] == "0" * 40 and "--ack" not in argv
    assert "--build-reference-dist" not in argv
    assert (
        mint.read_text(encoding="utf-8") == "minted MIP_BEARER_TOKEN\n"
    ), "health needs the normal bearer"


def test_the_bind_ack_without_the_adopt_live_ack_refuses_before_the_lease(tmp_path: Path) -> None:
    result, calls, _ = _drive(
        tmp_path,
        "run_adopt_live_plan adopt\n",
        module="adopt_live",
        env={"MIP_ADOPT_PLAN_ACK": ACK},
    )

    assert result.returncode == 2 and "reached-the-end" not in result.stdout
    assert "export MIP_ACK_ADOPT_LIVE=<the adopt plan sha256 above>" in result.stderr
    assert len(calls) == 1, "the plan ran read-only first"


def test_the_adopt_live_ack_is_passed_to_its_plan(tmp_path: Path) -> None:
    env = {"MIP_ADOPT_PLAN_ACK": ACK, "MIP_ACK_ADOPT_LIVE": OTHER}
    result, calls, _ = _drive(tmp_path, "run_adopt_live_plan adopt\n", module="adopt_live", env=env)

    assert result.returncode == 0, result.stderr
    assert calls[0][-2:] == ["--ack", OTHER]


def test_supersede_takes_its_own_ack_never_the_adopt_live_one(tmp_path: Path) -> None:
    env = {
        "MIP_ADOPT_PLAN_ACK": ACK,
        "MIP_ACK_ADOPT_LIVE": OTHER,
        "MIP_ADOPT_LIVE_SUPERSEDE": SUPERSEDE,
    }
    refused, calls, _ = _drive(
        tmp_path, "run_adopt_live_plan adopt\n", module="adopt_live", env=env
    )

    assert refused.returncode == 2 and "export MIP_ACK_SUPERSEDE=" in refused.stderr
    assert calls[0][:3] == ["plan", "--mode", "supersede"] and "--ack" not in calls[0]
    assert calls[0][calls[0].index("--supersede") + 1] == SUPERSEDE

    accepted, calls, _ = _drive(
        tmp_path / "second",
        "run_adopt_live_plan adopt\n",
        module="adopt_live",
        env={**env, "MIP_ACK_SUPERSEDE": ACK},
    )
    assert accepted.returncode == 0, accepted.stderr
    assert calls[0][-2:] == ["--ack", ACK]


def test_unadopt_writes_the_tombstone_with_its_own_ack_and_no_bearer(tmp_path: Path) -> None:
    env = {"MIP_ADOPT_PLAN_ACK": ACK, "MIP_ACK_UNADOPT": OTHER}
    result, calls, mint = _drive(
        tmp_path, "run_adopt_live_plan unadopt\n", module="adopt_live", env=env
    )

    assert result.returncode == 0, result.stderr
    assert calls[0][:3] == ["plan", "--mode", "unadopt"] and calls[0][-2:] == ["--ack", OTHER]
    assert not mint.exists(), "unadopt reads the record only"


def test_supersede_is_refused_in_the_unadopt_phase(tmp_path: Path) -> None:
    result, calls, _ = _drive(
        tmp_path,
        "run_adopt_live_plan unadopt\n",
        module="adopt_live",
        env={"MIP_ADOPT_LIVE_SUPERSEDE": SUPERSEDE},
    )

    assert result.returncode == 2 and calls == []
    assert "MIP_ADOPT_LIVE_SUPERSEDE goes with --phase adopt only" in result.stderr


def test_a_refused_plan_exits_2_before_the_lease(tmp_path: Path) -> None:
    result, _calls_made, _ = _drive(
        tmp_path, "run_adopt_live_plan adopt\n", module="adopt_live", rc=2
    )

    assert result.returncode == 2 and "reached-the-end" not in result.stdout
    assert "adopt-live (adopt) refused before the lease; nothing was changed" in result.stderr


def test_a_dry_run_prints_the_plan_and_mints_nothing(tmp_path: Path) -> None:
    result, calls, mint = _drive(
        tmp_path, "run_adopt_live_plan adopt\n", module="adopt_live", dry_run=1
    )

    assert result.returncode == 0, result.stderr
    assert calls == [] and not mint.exists()
    assert "-m tools.databricks.adopt_live plan --mode adopt" in result.stdout


# --- adopt-live's apply, under the lease ----------------------------------------------


@pytest.mark.parametrize(("rc", "expected"), ((0, 0), (2, 2), (1, 2)))
def test_the_apply_runs_under_the_lease_and_a_refusal_ends_the_run(
    tmp_path: Path, rc: int, expected: int
) -> None:
    result, calls, _ = _drive(
        tmp_path,
        "run_adopt_live_apply adopt\n",
        module="adopt_live",
        rc=rc,
        env={"MIP_ACK_ADOPT_LIVE": OTHER},
    )

    assert result.returncode == expected, result.stdout + result.stderr
    (argv,) = calls
    assert argv[:3] == ["apply", "--mode", "adopt"]
    assert argv[-2:] == ["--lease-id", "lease-1"]
    assert argv.count("--source-git-sha") == 1 and argv[argv.index("--source-git-sha") + 1] == (
        "0" * 40
    )
    assert "--ack" in argv and argv[argv.index("--ack") + 1] == OTHER
    if expected:
        assert "no record version was written and the App was never stopped" in result.stderr


def test_the_reference_build_is_asked_for_by_a_one_run_export_never_a_directory(
    tmp_path: Path,
) -> None:
    result, calls, _ = _drive(
        tmp_path,
        "run_adopt_live_plan adopt\n",
        module="adopt_live",
        env={"MIP_ADOPT_BUILD_REFERENCE_DIST": "1", "MIP_ADOPT_REFERENCE_DIST": "/tmp/dist"},
    )

    assert result.returncode == 0, result.stderr
    (argv,) = calls
    assert "--build-reference-dist" in argv and "--reference-dist" not in argv
    assert "/tmp/dist" not in argv


# --- the pre-activation proof ---------------------------------------------------------


def test_the_proof_runs_once_and_passes_the_no_rollback_ack(tmp_path: Path) -> None:
    body = "prove_blue_restorable_before_activation\nprove_blue_restorable_before_activation\n"
    result, calls, _ = _drive(
        tmp_path, body, module="blue_restore_proof", env={"MIP_ACK_NO_ROLLBACK_FOR": ACK}
    )

    assert result.returncode == 0, result.stderr
    (argv,) = calls
    assert argv[0] == "prepare-activation" and argv[-2:] == ["--no-rollback-ack", ACK]
    pairs = dict(zip(argv[1::2], argv[2::2], strict=False))
    assert pairs["--lease-id"] == "lease-1"
    assert (pairs["--source-git-sha"], pairs["--release-id"]) == (
        "0" * 40,
        RELEASE_ID,
    ), "the no-rollback ack covers this release only, so the proof is told which one"
    assert pairs["--agentic-tier"] == "off", "a v7 blue's tier is checked against the profile's"


def test_the_agentic_tier_passes_no_tier_flag_so_its_plan_is_unchanged(tmp_path: Path) -> None:
    """The proof's default is the agentic tier (#311's idiom: only the core tier says off)."""

    result, calls, _ = _drive(
        tmp_path,
        "prove_blue_restorable_before_activation\n",
        module="blue_restore_proof",
        env={"CLIENT_AGENTIC_TIER": "on"},
    )

    assert result.returncode == 0, result.stderr
    (argv,) = calls
    assert "--agentic-tier" not in argv and "--release-id" in argv


def test_a_refused_proof_exits_2_before_the_activation(tmp_path: Path) -> None:
    result, calls, _ = _drive(
        tmp_path, "prove_blue_restorable_before_activation\n", module="blue_restore_proof", rc=2
    )

    assert result.returncode == 2 and "reached-the-end" not in result.stdout
    assert "green activation refused before it began" in result.stderr
    assert "--no-rollback-ack" not in calls[0]


# --- call sites -------------------------------------------------------------------------


def test_the_proof_opens_every_activation_and_its_flag_is_reset_by_the_entrypoint() -> None:
    text = deploy_entrypoint_text()
    activation = function("deploy_app_snapshot")

    assert activation.splitlines()[1:4] == [
        '  local label="$1"',
        "  # Attack O2 (M2-PR7): blue's tier-b restore proof, once, before the first activation.",
        "  prove_blue_restorable_before_activation",
    ]
    reset = text.index("\nBLUE_RESTORE_PROVEN=0\n")
    assert reset < text.index('step "preflight — check .env.local, databricks CLI, venv"')
    assert text.count("BLUE_RESTORE_PROVEN=") == 2, "the reset and the proof's own success"


def test_adopt_live_is_planned_before_the_lease_and_applied_after_the_bind() -> None:
    plan, apply = function("run_adopt_phase_plan"), function("run_adopt_phase_apply")

    assert plan.index("install_state_adopt plan") < plan.index('run_adopt_live_plan "$phase"')
    assert plan.index('run_adopt_live_plan "$phase"') < plan.index(
        'if [[ "${#ack_args[@]}" -eq 0 ]]; then'
    )
    assert apply.index("install_state_adopt apply") < apply.index('run_adopt_live_apply "$phase"')
    assert apply.index('run_adopt_live_apply "$phase"') < apply.index(
        "phase complete: bundle state edited"
    )


def test_the_governed_publish_proves_its_held_lease() -> None:
    text = deploy_entrypoint_text()
    publish = text[text.index('run "$PYTHON" -m tools.databricks.app_release_source publish') :]
    publish = publish[: publish.index("\n\n")]

    assert '--held-lease-app-name "$_GRANTS_APP_NAME"' in publish
    assert '--held-lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID"' in publish
    assert (
        re.search(r"--override-freeze", text) is None
    ), "the governed run never overrides a freeze"
