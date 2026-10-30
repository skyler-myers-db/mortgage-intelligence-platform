"""deploy.sh wiring of the governed per-release App source.

Step 0 runs ``app_runtime_closure check --ref "$SOURCE_GIT_SHA"`` (git only,
no dist, no workspace call), so a commit step 1 could not stage, which would
stop and quiesce the App there, is refused before the lease. The block
mirrors the requirements-digest check beside it: a dry run reports and
continues, a real run refuses anything but ``current`` with exit 2.

Step 1 stages the closure plus the built dist into the gitignored
``.databricks/app-release`` (only a real run clears it) and reads the
release id back from the manifest. Step 5 publishes it, after the Lakebase
and reviewed-grant proofs and before the first activation, and composes the
only ``APP_SOURCE_PATH`` every activation names.

Blocks are read from ``deploy_entrypoint_text()`` (the sliced deploy.sh
layout) and driven in a small harness under ``set -Eeuo pipefail`` with an
ERR trap that exits 99, with stub tools that assert their exact arguments.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from tests.fixtures.deploy_block_harness import (
    BUNDLE,
    DIGEST,
    PRINCIPAL,
    RELEASE_ID,
    SHA,
    block,
    code,
    drive,
    function,
    stub_tool,
)
from tests.fixtures.deploy_script import deploy_entrypoint_text
from tools.databricks import app_release_contract as contract

ROOT = Path(__file__).resolve().parents[2]
CHECK_START = "# >>> app runtime closure: check >>>"
CHECK_END = "# <<< app runtime closure: check <<<"
DIGEST_END = "# <<< requirements digest: check <<<"
PROMPT = 'read -r -p "About to DEPLOY to the ${TARGET} target. Continue? [y/N] " ans'
# The client key, the client target's root leaf (M1-PR2).
LEAF = "client-one"


def _block(start: str = CHECK_START, end: str = CHECK_END) -> str:
    return block(start, end)


_code = code


def test_the_closure_check_runs_after_the_digest_check_and_before_any_workspace_call() -> None:
    text = deploy_entrypoint_text()
    check = text.index(CHECK_START)

    assert text.index(DIGEST_END) < check
    assert check < text.index("workspace_admin_inventory_principal(WorkspaceClient())")
    assert check < text.index(PROMPT) < text.index("\nrun ")
    assert check < text.index('step "acquire signed workspace lease for the exact App deployment"')
    assert text.index("verify_exact_deploy_source\n") < check  # SOURCE_GIT_SHA is bound


def test_the_block_checks_the_deploy_commit_and_refuses_only_real_runs() -> None:
    code = _code(_block())

    assert '-m tools.databricks.app_runtime_closure check --ref "$SOURCE_GIT_SHA"' in code
    assert "app_runtime_closure stage" not in code and "databricks " not in code
    assert code.count("-m tools.") == 1
    assert 'if [[ "$DRY_RUN" -eq 0 && "$APP_RUNTIME_CLOSURE_STATE" != "current" ]]; then' in code
    assert code.count("exit 2") == 1


def _harness(repo: Path, *, dry_run: int) -> subprocess.CompletedProcess[str]:
    script = repo / "preflight.sh"
    script.write_text(
        "set -Eeuo pipefail\ntrap 'exit 99' ERR\n"
        f'RED=""\nRST=""\nDRY_RUN={dry_run}\nTARGET=client\nPYTHON={shlex.quote(sys.executable)}\n'
        f'SOURCE_GIT_SHA={"0" * 40}\n{_block()}\necho reached-after-block\n',
        encoding="utf-8",
    )
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(repo)}
    return subprocess.run(
        ["bash", str(script)], cwd=repo, env=env, text=True, capture_output=True, check=False
    )


def _stub(repo: Path, exit_code: int) -> None:
    package = repo / "tools" / "databricks"
    package.mkdir(parents=True, exist_ok=True)
    for init in (repo / "tools" / "__init__.py", package / "__init__.py"):
        init.write_text("", encoding="utf-8")
    (package / "app_runtime_closure.py").write_text(
        "import sys\n"
        "assert sys.argv[1:] == ['check', '--ref', '0' * 40], sys.argv\n"
        f"print('  stub verdict')\nsys.exit({exit_code})\n",
        encoding="utf-8",
    )


def test_a_stageable_head_passes_a_real_run_in_the_real_tree(tmp_path: Path) -> None:
    head = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    script = tmp_path / "preflight.sh"
    script.write_text(
        "set -Eeuo pipefail\ntrap 'exit 99' ERR\n"
        f'RED=""\nRST=""\nDRY_RUN=0\nTARGET=client\nPYTHON={shlex.quote(sys.executable)}\n'
        f"SOURCE_GIT_SHA={head}\n{_block()}\necho reached-after-block\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", str(script)], cwd=ROOT, text=True, capture_output=True, check=False,
        env={"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path)},
    )

    assert result.returncode == 0, result.stderr
    assert "app runtime closure: current" in result.stdout
    assert "reached-after-block" in result.stdout


def test_a_current_stub_passes_a_real_run(tmp_path: Path) -> None:
    _stub(tmp_path, 0)

    result = _harness(tmp_path, dry_run=0)

    assert result.returncode == 0, result.stderr
    assert "app runtime closure: current" in result.stdout
    assert "reached-after-block" in result.stdout


@pytest.mark.parametrize(
    ("tool_exit", "state", "message"),
    [
        (2, "refused", "cannot be staged"),
        (1, "error", "the App runtime closure check failed"),
        (None, "skipped", "app_runtime_closure.py is not in this tree"),
    ],
)
def test_a_real_run_refuses_anything_but_current_with_exit_2(
    tmp_path: Path, tool_exit: int | None, state: str, message: str
) -> None:
    if tool_exit is not None:
        _stub(tmp_path, tool_exit)

    result = _harness(tmp_path, dry_run=0)

    assert result.returncode == 2
    assert f"app runtime closure: {state}" in result.stdout
    assert message in result.stderr
    assert "reached-after-block" not in result.stdout


@pytest.mark.parametrize(("tool_exit", "state"), [(2, "refused"), (1, "error"), (None, "skipped")])
def test_a_dry_run_reports_and_continues(tmp_path: Path, tool_exit: int | None, state: str) -> None:
    if tool_exit is not None:
        _stub(tmp_path, tool_exit)

    result = _harness(tmp_path, dry_run=1)

    assert result.returncode == 0, result.stderr
    assert f"app runtime closure: {state}" in result.stdout
    assert "reached-after-block" in result.stdout


# --- step 1 stage and step 5 publish ----------------------------------------

STAGE_START = "# >>> app release source: stage >>>"
STAGE_END = "# <<< app release source: stage <<<"
PUBLISH_START = "# >>> app release source: publish >>>"
PUBLISH_END = "# <<< app release source: publish <<<"
STAGE_ARGS = [
    "stage", "--ref", SHA, "--dist", "frontend/dist", "--out", ".databricks/app-release/source",
    "--manifest-out", ".databricks/app-release/manifest.json", "--require-brotli-check",
]


def _stage_env() -> dict[str, str]:
    return {
        "SOURCE_GIT_SHA": SHA,
        "APP_RELEASE_STAGE_ROOT": ".databricks/app-release",
        "APP_RELEASE_MANIFEST": ".databricks/app-release/manifest.json",
        "APP_RELEASE_ID": "",
    }


def _stage(repo: Path, *, dry_run: int, stage_exit: int = 0, release: str = "", rid_exit: int = 0):
    stale = repo / ".databricks" / "app-release" / "stale"
    stale.parent.mkdir(parents=True)
    stale.write_text("from a prior run\n", encoding="utf-8")
    staged = stub_tool(repo, "app_runtime_closure", argv=STAGE_ARGS, body=(
        "assert not os.path.exists('.databricks/app-release'), 'the stage root was not cleared'\n"
        "os.makedirs('.databricks/app-release/source')\n"
        "open('.databricks/app-release/manifest.json', 'w').write('{}')\n"
        f"sys.exit({stage_exit})\n"
    ))
    read_back = stub_tool(
        repo, "app_release_contract",
        argv=["release-id", "--manifest", ".databricks/app-release/manifest.json", "--expected-commit", SHA],
        body=f"print({(release or RELEASE_ID)!r})\nsys.exit({rid_exit})\n",
    )
    result = drive(
        repo, _block(STAGE_START, STAGE_END), dry_run=dry_run, env=_stage_env(),
        epilogue='echo "release=${APP_RELEASE_ID}"\n',
    )
    return result, stale, staged, read_back


def test_the_stage_block_follows_the_build_and_precedes_bundle_validation() -> None:
    text = deploy_entrypoint_text()
    block = _block(STAGE_START, STAGE_END)
    code = _code(block)

    assert text.index("run npm --prefix frontend run build") < text.index(STAGE_START)
    assert text.index(STAGE_END) < text.index('step "validate direct-deployment bundle against -t ${TARGET}"')
    assert (
        'run "$PYTHON" -m tools.databricks.app_runtime_closure stage \\\n'
        '  --ref "$SOURCE_GIT_SHA" \\\n'
        "  --dist frontend/dist \\\n"
        '  --out "$APP_RELEASE_STAGE_ROOT/source" \\\n'
        '  --manifest-out "$APP_RELEASE_MANIFEST" \\\n'
        "  --require-brotli-check\n"
    ) in code
    assert code.count("rm -rf") == 1
    assert 'if [[ "$DRY_RUN" -eq 0 ]]; then\n  rm -rf -- "${APP_RELEASE_STAGE_ROOT:?}"\nfi\n' in code
    assert "databricks " not in code.replace("tools.databricks.", "")
    assert text.count('APP_RELEASE_STAGE_ROOT=".databricks/app-release"\n') == 1


def test_the_stage_root_is_gitignored_and_outside_the_exact_source_gate() -> None:
    paths = [".databricks/app-release/source/app.yaml", ".databricks/app-release/manifest.json"]
    ignored = subprocess.run(
        ["git", "-C", str(ROOT), "check-ignore", *paths], capture_output=True, text=True, check=False
    )

    assert ignored.returncode == 0
    assert ignored.stdout.splitlines() == paths


def test_a_real_run_clears_the_stage_root_stages_and_reads_the_release_id_back(tmp_path: Path) -> None:
    result, stale, staged, read_back = _stage(tmp_path, dry_run=0)

    assert result.returncode == 0, result.stderr
    assert not stale.exists()
    assert staged.read_text() == read_back.read_text() == "called\n"
    assert f"release={SHA[:12]}-{DIGEST}" in result.stdout
    assert "reached-after-block" in result.stdout


def test_a_dry_run_only_prints_the_stage_and_uses_an_inert_release_id(tmp_path: Path) -> None:
    result, stale, staged, read_back = _stage(tmp_path, dry_run=1)

    assert result.returncode == 0, result.stderr
    assert stale.exists(), "a dry run never clears the stage root"
    assert not staged.exists() and not read_back.exists()
    assert f"$ {sys.executable} -m tools.databricks.app_runtime_closure {' '.join(STAGE_ARGS)}" in result.stdout
    assert f"release={SHA[:12]}-dry-run" in result.stdout


@pytest.mark.parametrize(("stage_exit", "rid_exit"), [(2, 0), (1, 0), (0, 2)])
def test_a_stage_or_release_id_refusal_trips_errexit(tmp_path: Path, stage_exit: int, rid_exit: int) -> None:
    result, *_ = _stage(tmp_path, dry_run=0, stage_exit=stage_exit, rid_exit=rid_exit)

    assert result.returncode == 99, "the ERR trap fires, as deploy.sh's on_error does"
    assert "reached-after-block" not in result.stdout


@pytest.mark.parametrize("release", [f"{'f' * 12}-{DIGEST}", f"{SHA[:12]}-dry-run", f"{SHA[:12]}-{DIGEST}x", ""])
def test_a_release_id_of_another_shape_is_refused(tmp_path: Path, release: str) -> None:
    result, *_ = _stage(tmp_path, dry_run=0, release=release or " ")

    assert result.returncode == 2
    assert "staged App release id is not <commit12>-<digest12>" in result.stderr
    assert "reached-after-block" not in result.stdout


PRINCIPAL_START = "# >>> app release principal: check >>>"
PRINCIPAL_END = "# <<< app release principal: check <<<"
# The client profile's deployer: a service principal's application id (M1-PR2).
DEPLOYER = "00000000-0000-4000-8000-0000000000aa"


def _principal_check(repo: Path, *, dry_run: int, current_user: str) -> subprocess.CompletedProcess[str]:
    return drive(
        repo,
        function("same_identity_casefold") + _block(PRINCIPAL_START, PRINCIPAL_END),
        dry_run=dry_run,
        env={"APP_CURRENT_USER": current_user, "CLIENT_DEPLOYER": DEPLOYER, "CLIENT_KEY": LEAF},
        epilogue='echo "principal=${APP_RELEASE_PRINCIPAL} leaf=${APP_RELEASE_ROOT_LEAF}"\n',
    )


def test_the_principal_check_follows_the_inventory_check_and_precedes_the_publish() -> None:
    text = deploy_entrypoint_text()

    assert text.index("bundle identity does not match the preflighted workspace-admin") < text.index(PRINCIPAL_START)
    assert text.index(PRINCIPAL_END) < text.index(PUBLISH_START)


@pytest.mark.parametrize("current_user", [DEPLOYER, DEPLOYER.upper()])
def test_a_real_run_as_the_profiles_deployer_names_it_and_the_client_key(tmp_path: Path, current_user: str) -> None:
    result = _principal_check(tmp_path, dry_run=0, current_user=current_user)

    assert result.returncode == 0, result.stderr
    assert f"principal={DEPLOYER} leaf={LEAF}\nreached-after-block\n" in result.stdout


@pytest.mark.parametrize("current_user", ["00000000-0000-4000-8000-0000000000bb", "operator@lender.example", " "])
def test_a_real_run_as_another_principal_is_refused_before_the_publish(tmp_path: Path, current_user: str) -> None:
    """Defence in depth behind the live checks: the bundle summary's user must be the profile's deployer."""

    result = _principal_check(tmp_path, dry_run=0, current_user=current_user)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "the bundle runs as another principal than the client profile's deployer" in result.stderr
    assert DEPLOYER not in result.stderr, "the refusal names no identifier"
    assert "principal=" not in result.stdout and "reached-after-block" not in result.stdout


def test_a_dry_run_names_the_deployer_without_the_identity_check(tmp_path: Path) -> None:
    """A dry run's offline summary carries no real user."""

    result = _principal_check(tmp_path, dry_run=1, current_user="dry-run-user")

    assert result.returncode == 0, result.stderr
    assert f"principal={DEPLOYER} leaf={LEAF}\n" in result.stdout


def _publish_env(files_root: str, leaf: str) -> dict[str, str]:
    return {
        **_stage_env(),
        "APP_RELEASE_ID": RELEASE_ID,
        "APP_BUNDLE_FILES_PATH": files_root,
        "APP_CURRENT_USER": PRINCIPAL,
        "APP_RELEASE_PRINCIPAL": PRINCIPAL,
        "APP_RELEASE_ROOT_LEAF": leaf,
        "TARGET": "client",
    }


def _publish(repo: Path, *, dry_run: int, files_root: str, leaf: str = LEAF, exit_code: int = 0):
    # The client target's root: the deployer's folder plus the client key
    # (databricks.yml root_path), so the leaf is the client key, never the target.
    argv = [
        "publish", "--release-id", RELEASE_ID, "--stage-dir", ".databricks/app-release/source",
        "--manifest", ".databricks/app-release/manifest.json", "--files-root", files_root,
        "--expected-commit", SHA, "--expected-principal", PRINCIPAL, "--expected-target", "client",
        "--expected-root-leaf", leaf,
    ]
    called = stub_tool(repo, "app_release_source", argv=argv, body=f"sys.exit({exit_code})\n")
    result = drive(
        repo, _block(PUBLISH_START, PUBLISH_END), dry_run=dry_run, env=_publish_env(files_root, leaf),
        epilogue='echo "source=${APP_SOURCE_PATH:-unset}"\n',
    )
    return result, called


def test_publish_follows_the_lakebase_and_grant_proofs_and_precedes_every_activation() -> None:
    text = deploy_entrypoint_text()
    publish = text.index(PUBLISH_START)

    assert text.index("LAKEBASE_RUNTIME_ACCESS_PROVEN=1\n") < publish
    assert text.index("\ninitialize_uc_targets_and_reconcile_function_grants\n") < publish
    assert text.index("bundle identity does not match the preflighted workspace-admin") < publish
    assert text.index(PUBLISH_END) < text.index('deploy_app_snapshot "deploy first-install')
    assert text.count('deploy_app_snapshot "') == 3, "first install, cutover, Agent Evaluation"
    assert text.count("-m tools.databricks.app_release_source publish") == 1
    assert "verify-deployment" not in text


def test_app_source_path_is_assigned_once_from_the_files_root_and_the_release_id() -> None:
    text = deploy_entrypoint_text()
    block = _code(_block(PUBLISH_START, PUBLISH_END))
    assignment = 'APP_SOURCE_PATH="${APP_BUNDLE_FILES_PATH%/files}/app-src/${APP_RELEASE_ID}"'

    assert text.count("APP_SOURCE_PATH=") == 1
    assert block.splitlines().count(assignment) == 1
    assert block.index("-m tools.databricks.app_release_source publish") < block.index(assignment)
    assert '    --source-code-path "$APP_SOURCE_PATH" \\\n' in function("deploy_app_snapshot")


@pytest.mark.parametrize("leaf", [LEAF, "fictional-customer"])
def test_a_published_release_composes_the_contract_release_path(tmp_path: Path, leaf: str) -> None:
    files_root = contract.files_root(principal=PRINCIPAL, target="client", root_leaf=leaf)

    result, called = _publish(tmp_path, dry_run=0, files_root=files_root, leaf=leaf)

    expected = contract.release_path(
        principal=PRINCIPAL, target="client", root_leaf=leaf, release_id=RELEASE_ID, commit=SHA
    )
    assert result.returncode == 0, result.stderr
    assert called.exists()
    assert f"source={expected}\n" in result.stdout


def test_a_publish_refusal_trips_errexit_before_any_source_path_exists(tmp_path: Path) -> None:
    result, called = _publish(
        tmp_path, dry_run=0, files_root=contract.files_root(principal=PRINCIPAL, target="client", root_leaf=LEAF),
        exit_code=2,
    )

    assert result.returncode == 99
    assert called.exists()
    assert "source=" not in result.stdout


def test_a_dry_run_prints_the_publish_and_composes_the_placeholder_path(tmp_path: Path) -> None:
    result, called = _publish(
        tmp_path, dry_run=1, files_root="/Workspace/dry-run/mortgage-intelligence-platform"
    )

    assert result.returncode == 0, result.stderr
    assert not called.exists()
    assert f"source=/Workspace/dry-run/mortgage-intelligence-platform/app-src/{SHA[:12]}-{DIGEST}\n" in result.stdout


def _files_root_line() -> str:
    line = next(
        line for line in deploy_entrypoint_text().splitlines()
        if line.lstrip().startswith('APP_DEPLOY_META="$("$PYTHON" -c ')
    )
    return line.split(" -c '", 1)[1].rsplit("' \"$APP_BUNDLE_SUMMARY\")\"", 1)[0]


@pytest.mark.parametrize(
    ("app_source", "printed"),
    [(f"{BUNDLE}/dev/files", f"{BUNDLE}/dev/files"), (None, f"{BUNDLE}/dev/files"), (f"{BUNDLE}/dev/files/backend", "")],
)
def test_the_summary_line_prints_the_files_root_only_while_the_app_resolves_to_it(
    tmp_path: Path, app_source: str | None, printed: str
) -> None:
    app = {} if app_source is None else {"source_code_path": app_source}
    summary = tmp_path / "summary.json"
    summary.write_text(json.dumps({
        "workspace": {"file_path": f"{BUNDLE}/dev/files", "current_user": {"userName": PRINCIPAL}},
        "resources": {"apps": {"mip_app": app}},
    }), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-c", _files_root_line(), str(summary)], capture_output=True, text=True, check=True
    )

    assert result.stdout == f"{printed}\n{PRINCIPAL}\n"

