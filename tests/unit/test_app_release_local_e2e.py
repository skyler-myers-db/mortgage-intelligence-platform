"""The per-release App source flow end to end, locally, through each tool's CLI.

check, stage with a manifest, release-id, publish, release-mode converge, a
second publish that uploads nothing, verify-deployment against a snapshot
copy, and prune against fake deployments: tests/fixtures/app_runtime_stage_repo.py
is the repo and dist, tests/fixtures/fake_workspace.py the workspace. No
Databricks call is made.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from databricks.sdk.service.workspace import ObjectType

from tests.fixtures.app_runtime_stage_repo import FAKE_VERIFIER, git, make_dist, make_repo
from tests.fixtures.fake_workspace import FakeWorkspaceClient, deployment
from tools.databricks import app_release_contract as contract
from tools.databricks import app_release_prune as prune
from tools.databricks import app_release_source as source
from tools.databricks import app_runtime_closure as closure
from tools.databricks import converge_static_app_source as converge

PRINCIPAL = "ci-deployer@example.com"
TARGET, LEAF = "pr105-host", "dev-ci"
FILES_ROOT = contract.files_root(principal=PRINCIPAL, target=TARGET, root_leaf=LEAF)
IDENTITY = ["--expected-principal", PRINCIPAL, "--expected-target", TARGET, "--expected-root-leaf", LEAF]


def test_stage_publish_converge_republish_verify_and_prune(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(closure, "brotli_verifier", lambda: FAKE_VERIFIER)
    monkeypatch.setattr(prune.app_deployment_lease, "assert_held", lambda *_a, **_k: None)
    repo, dist = make_repo(tmp_path), make_dist(tmp_path)
    sha = git(repo, "rev-parse", "HEAD")
    stage_root = tmp_path / ".databricks" / "app-release"
    stage_dir, manifest = stage_root / "source", stage_root / "manifest.json"
    client = FakeWorkspaceClient()

    assert closure.main(["--repo", str(repo), "check", "--ref", sha]) == 0
    assert closure.main([
        "--repo", str(repo), "stage", "--ref", sha, "--dist", str(dist), "--out", str(stage_dir),
        "--manifest-out", str(manifest), "--require-brotli-check",
    ]) == 0
    capsys.readouterr()
    assert contract.main(["release-id", "--manifest", str(manifest), "--expected-commit", sha]) == 0
    release_id = capsys.readouterr().out.strip()
    assert release_id.startswith(sha[:12])

    publish = ["publish", "--release-id", release_id, "--stage-dir", str(stage_dir),
               "--manifest", str(manifest), "--files-root", FILES_ROOT, "--expected-commit", sha,
               *IDENTITY]
    assert source.main(publish, client=lambda: client) == 0
    files = len(contract.read_manifest(manifest, expected_commit=sha).entries)
    assert f"{files} uploaded, 0 already present" in capsys.readouterr().out

    # The composed path deploy.sh promotes: ${APP_BUNDLE_FILES_PATH%/files}/app-src/<id>.
    release_path = f"{FILES_ROOT.removesuffix('/files')}/app-src/{release_id}"
    assert release_path == contract.release_path(
        principal=PRINCIPAL, target=TARGET, root_leaf=LEAF, release_id=release_id, commit=sha
    )
    converge_args = ["--source-code-path", release_path, "--release-id", release_id,
                     "--release-manifest", str(manifest), "--expected-git-sha", sha, *IDENTITY]
    for _activation in range(3):  # first install, cutover, Agent Evaluation redeploy
        assert converge.main(converge_args, client=lambda: client) == 0
    assert source.main(publish, client=lambda: client) == 0
    assert f"0 uploaded, {files} already present" in capsys.readouterr().out

    # The platform snapshots the release into /src/ at deploy time.
    snapshot = "/Workspace/Users/app-sp/src/01f0-current"
    api_release = contract.api_path(release_path)
    for name in client.workspace.below(api_release):
        node = client.workspace.nodes[name]
        if node.kind is ObjectType.FILE:
            client.workspace.add_file(
                name.replace(api_release, contract.api_path(snapshot)), node.data, node.kind
            )
    old = [f"{n:012x}-{n:012x}" for n in (1, 2, 3, 4)]
    history = []
    for n, old_id in enumerate(old, start=1):
        old_path = contract.release_path(
            principal=PRINCIPAL, target=TARGET, root_leaf=LEAF, release_id=old_id
        )
        client.workspace.add_file(f"{contract.api_path(old_path)}/app.yaml", b"old\n")
        history.append(deployment(f"dep-{n}", old_path, create_time=f"2026-10-0{n}T10:00:00Z"))
    active = deployment("dep-now", release_path, create_time="2026-10-28T10:00:00Z", snapshot=snapshot)
    client.apps.app = SimpleNamespace(
        id="app-id", name="mip-app", active_deployment=active, pending_deployment=None
    )
    client.apps.deployments = [*history, active]

    assert source.main(["verify-deployment", "--app-name", "mip-app", "--manifest", str(manifest),
                        "--release-id", release_id], client=lambda: client) == 0
    assert prune.main([
        "--app-name", "mip-app", "--expected-app-id", "app-id", "--current-release", release_id,
        "--rollback-deployment-id", "dep-1", "--deployment-lease-id", "lease-1",
        "--source-git-sha", sha, "--files-root", FILES_ROOT, *IDENTITY,
    ], client=lambda: client) == 0

    out = capsys.readouterr().out
    assert f"deleted: {old[1]}" in out and out.count("deleted: ") == 1
    remaining = {
        name.rsplit("/", 1)[1] for name in client.workspace.nodes
        if name.startswith(f"{contract.api_path(release_path).rsplit('/', 1)[0]}/")
        and name.count("/") == api_release.count("/")
    }
    assert remaining == {release_id, old[0], old[2], old[3]}
    assert converge.main(converge_args, client=lambda: client) == 0
