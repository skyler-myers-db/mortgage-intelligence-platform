"""A prebuilt release is re-hashed against its manifest and its tracked half against git (M1-PR7).

deploy-dev.yml's build job (no id-token) writes the release and its
``SHA256SUMS.json``; the deploy job verifies it before installing a wheel,
and ``scripts/deploy.sh --prebuilt-release <dir>`` verifies it again before
any Databricks call and installs it in step 1 in place of npm and the stage
(tools/databricks/prebuilt_release.py,
scripts/lib/deploy_prebuilt_release_lifecycle.sh). The manifest is the build
job's own word, so every verify also re-derives the staged App source from
the deploying checkout's commit: only the dist's bytes come from the build.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.fixtures.app_runtime_stage_repo import FAKE_VERIFIER, git, make_dist, make_repo
from tests.fixtures.deploy_sandbox import ANSI, planned_commands, sandbox
from tests.fixtures.prebuilt_release import ASSET, WHEEL, head, reseal, seal
from tools.databricks import app_runtime_closure as closure
from tools.databricks import prebuilt_release as release

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _brotli(monkeypatch: pytest.MonkeyPatch) -> None:
    """The build job checks a .br with node; here a stand-in decoder (tests/fixtures/app_runtime_stage_repo.py)."""

    monkeypatch.setattr(closure, "brotli_verifier", lambda: FAKE_VERIFIER)


def _requirements(tmp_path: Path, text: str = "fastapi==0.136.1 \\\n    --hash=sha256:" + "a" * 64 + "\n") -> Path:
    path = tmp_path / "requirements-deploy.hashes.txt"
    path.write_text(text, encoding="utf-8")
    return path


def _built(tmp_path: Path) -> tuple[Path, Path, Path, str]:
    """(release root, requirements, repo, manifest sha256) for the stage fixture repo's HEAD."""

    repo = make_repo(tmp_path)
    requirements = _requirements(tmp_path)
    digest = seal(repo, tmp_path / "release", requirements=requirements, dist=make_dist(tmp_path))
    return tmp_path / "release", requirements, repo, digest


def _verify(root: Path, requirements: Path, repo: Path, digest: str, *, git_sha: str | None = None) -> release.Verified:
    return release.verify(
        root, git_sha=git_sha or head(repo), requirements=requirements, expected_manifest_sha256=digest, repo=repo
    )


def _listed(root: Path) -> dict[str, object]:
    return json.loads((root / release.MANIFEST).read_bytes())["files"]


def test_a_manifest_lists_every_file_but_itself_and_its_digest_is_printed(tmp_path: Path) -> None:
    root, requirements, repo, digest = _built(tmp_path)
    data = (root / release.MANIFEST).read_bytes()
    body = json.loads(data)

    assert digest == hashlib.sha256(data).hexdigest()
    assert body["schema"] == release.SCHEMA and body["git_sha"] == head(repo)
    assert body["requirements_sha256"] == release.file_sha256(requirements)
    assert sorted(body["files"]) == sorted(release.walk(root)), "every file but SHA256SUMS.json itself"
    index = (root / "dist/index.html").read_bytes()
    assert body["files"]["dist/index.html"] == {"sha256": hashlib.sha256(index).hexdigest(), "size": len(index)}
    found = _verify(root, requirements, repo, digest)
    assert found.manifest == body
    assert found.tracked == len(closure.tracked_blobs(repo=repo, ref="HEAD")[1])
    assert (found.dist, found.build_only_brotli) == (6, 1), "the .br decode check is the build job's"
    with pytest.raises(release.Refused, match="made once"):
        release.write_manifest(root, git_sha=head(repo), requirements=requirements)


def _mkdir(path: Path) -> Path:
    path.mkdir(parents=True)
    return path


def _flip(path: Path) -> None:
    data = bytearray(path.read_bytes())
    data[0] ^= 0x01
    path.write_bytes(bytes(data))


TAMPERS = {
    "a byte flipped, same size": (lambda root: _flip(root / "dist/index.html"), "1 files changed, first dist/index.html"),
    "a wheel resized": (lambda root: (root / WHEEL).write_bytes(b"PK"), "changed"),
    "a file added": (lambda root: (root / "dist/assets/extra.js").write_bytes(b"x"), "not in the manifest, first dist/assets/extra.js"),
    "a file removed": (lambda root: (root / "app-release/manifest.json").unlink(), "missing, first app-release/manifest.json"),
    "a symlink planted": (lambda root: (root / "dist/link.js").symlink_to(root / "dist/index.html"), "dist/link.js: not a regular file"),
    "the manifest edited": (lambda root: (root / release.MANIFEST).write_text("{}", encoding="utf-8"), "its sha256 is not the build job's"),
    "a part emptied": (
        lambda root: [path.unlink() for path in (root / "wheelhouse").iterdir()],
        "wheelhouse: the release's wheelhouse folder is empty",
    ),
}


@pytest.mark.parametrize("tamper", sorted(TAMPERS))
def test_any_difference_from_the_manifest_refuses(tmp_path: Path, tamper: str) -> None:
    root, requirements, repo, digest = _built(tmp_path)
    change, message = TAMPERS[tamper]
    change(root)

    with pytest.raises(release.Refused, match=message.replace("(", r"\(")):
        _verify(root, requirements, repo, digest)


def test_another_commit_another_requirements_file_or_a_bad_digest_refuses(tmp_path: Path) -> None:
    root, requirements, repo, digest = _built(tmp_path)

    with pytest.raises(release.Refused, match="another commit"):
        _verify(root, requirements, repo, digest, git_sha="2" * 40)
    (tmp_path / "other").mkdir()
    other = _requirements(tmp_path / "other", "fastapi==0.136.2\n")
    with pytest.raises(release.Refused, match="another requirements-deploy.hashes.txt"):
        _verify(root, other, repo, digest)
    with pytest.raises(release.Refused, match="64 lowercase hex"):
        _verify(root, requirements, repo, "")


def test_a_manifest_listing_a_path_outside_the_release_refuses(tmp_path: Path) -> None:
    root, requirements, repo, _ = _built(tmp_path)
    body = json.loads((root / release.MANIFEST).read_bytes())
    body["files"]["../escape"] = {"sha256": "0" * 64, "size": 0}
    data = release.manifest_bytes(head(repo), body["requirements_sha256"], body["files"])
    (root / release.MANIFEST).write_bytes(data)

    with pytest.raises(release.Refused, match="not a plain relative path"):
        _verify(root, requirements, repo, hashlib.sha256(data).hexdigest())


# ----------------------------------------------- provenance: the deploying checkout's commit


def _tampered(tmp_path: Path, change: object) -> tuple[Path, Path, Path, str]:
    """A release whose stage ``change`` rewrote, both manifests re-sealed over it, as a compromised build would."""

    root, requirements, repo, _ = _built(tmp_path)
    assert callable(change)
    change(root)
    return root, requirements, repo, reseal(root, git_sha=head(repo), requirements=requirements)


PROVENANCE = {
    "a backend file rewritten": (
        lambda root: (root / "app-release/source/backend/services/reader.py").write_text("X = 'pwned'\n", encoding="utf-8"),
        "the staged App source against this checkout's commit: 1 files changed, first backend/services/reader.py",
    ),
    "a backend file added": (
        lambda root: (root / "app-release/source/backend/services/extra.py").write_text("Y = 1\n", encoding="utf-8"),
        "the staged App source against this checkout's commit: 1 files not in the manifest, first backend/services/extra.py",
    ),
    "a tracked file dropped": (
        lambda root: (root / "app-release/source/lakebase/schema.sql").unlink(),
        "the staged App source against this checkout's commit: 1 files missing, first lakebase/schema.sql",
    ),
    "the staged dist apart from the release's": (
        lambda root: _flip(root / "app-release/source/frontend/dist/index.html"),
        "the staged dist against the release's dist: 1 files changed, first index.html",
    ),
    "a source map in both dists": (
        lambda root: [
            (root / folder / "assets/app-DnQ3_k-1.js.map").write_text("{}", encoding="utf-8")
            for folder in ("dist", "app-release/source/frontend/dist")
        ],
        "the staged dist fails the dist review: 1 problems, first source map: assets/app-DnQ3_k-1.js.map",
    ),
}


@pytest.mark.parametrize("tamper", sorted(PROVENANCE))
def test_a_resealed_stage_that_is_not_the_commits_refuses(tmp_path: Path, tamper: str) -> None:
    """SHA256SUMS.json and manifest.json both match: only the deploying checkout's git can tell."""

    change, message = PROVENANCE[tamper]
    root, requirements, repo, digest = _tampered(tmp_path, change)
    assert release.walk(root) == _listed(root), "the transport check alone would pass"

    with pytest.raises(release.Refused) as caught:
        _verify(root, requirements, repo, digest)
    assert str(caught.value) == message


def test_a_release_manifest_that_does_not_describe_the_stage_refuses(tmp_path: Path) -> None:
    root, requirements, repo, _ = _built(tmp_path)
    manifest = json.loads((root / release.RELEASE_MANIFEST).read_text(encoding="utf-8"))
    manifest["files"] = [entry for entry in manifest["files"] if entry["path"] != "app.yaml"]
    entries = [release.release_contract.ManifestEntry(**entry) for entry in manifest["files"]]
    release.release_contract.write_manifest(root / release.RELEASE_MANIFEST, commit=head(repo), entries=entries)
    (root / release.MANIFEST).unlink()
    digest = release.write_manifest(root, git_sha=head(repo), requirements=requirements)

    with pytest.raises(release.Refused, match="the staged files against app-release/manifest.json: 1 files not in the manifest, first app.yaml"):
        _verify(root, requirements, repo, digest)


def test_provenance_is_checked_against_the_named_commit_in_the_deploying_checkout(tmp_path: Path) -> None:
    root, requirements, repo, digest = _built(tmp_path)
    sealed = head(repo)
    (repo / "backend/services/reader.py").write_text("X = 2\n", encoding="utf-8")
    git(repo, "commit", "-qam", "later")

    assert _verify(root, requirements, repo, digest, git_sha=sealed).tracked > 0, "the sealed commit, not HEAD"
    # make_repo commits the same tree, identity and message every time, so a
    # second one made in the same second (a fast CI runner) IS the sealed
    # commit, and an amend still leaves that object in the store. Build the
    # other checkout from its own tree, and prove it lacks the commit.
    unrelated = _mkdir(tmp_path / "unrelated")
    git(unrelated, "init", "-q")
    (unrelated / "README.md").write_text("an unrelated history\n", encoding="utf-8")
    git(unrelated, "add", "-A")
    git(unrelated, "commit", "-q", "-m", "an unrelated history")
    held = subprocess.run(["git", "-C", str(unrelated), "cat-file", "-e", f"{sealed}^{{commit}}"], capture_output=True)
    assert held.returncode != 0, "the other checkout must not hold the sealed commit"
    with pytest.raises(release.Refused, match="cannot stage the App closure at the commit"):
        release.provenance(root, _listed(root), repo=unrelated, git_sha=sealed)


def test_install_replaces_each_destination_whole_and_rehashes_the_copy(tmp_path: Path) -> None:
    root, requirements, repo, digest = _built(tmp_path)
    dist_out = tmp_path / "checkout" / "frontend" / "dist"
    stage_out = tmp_path / "checkout" / ".databricks" / "app-release"
    (dist_out).mkdir(parents=True)
    (dist_out / "stale.js").write_bytes(b"old build")
    common = {"git_sha": head(repo), "requirements": requirements, "expected_manifest_sha256": digest, "repo": repo}

    count = release.install(root, dist_out=dist_out, stage_out=stage_out, **common)

    assert count == len([rel for rel in _listed(root) if not rel.startswith("wheelhouse/")]), "dist and app-release only"
    assert not (dist_out / "stale.js").exists(), "an old build never survives beside the release"
    assert (dist_out / "index.html").read_bytes() == (root / "dist/index.html").read_bytes()
    assert (stage_out / "source" / "app.yaml").read_bytes() == (repo / "app.yaml").read_bytes()
    _flip(root / "dist/index.html")
    with pytest.raises(release.Refused, match="changed"):
        release.install(root, dist_out=dist_out, stage_out=stage_out, **common)


def test_install_refuses_a_copy_that_changed_after_the_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The re-hash of the copies, not the check before the copy, catches a file changed in between."""

    root, requirements, repo, digest = _built(tmp_path)
    copytree = shutil.copytree

    def copy_then_change(source: Path, destination: Path, *args: object, **kwargs: object) -> object:
        placed = copytree(source, destination, *args, **kwargs)  # type: ignore[arg-type]
        if Path(source) == root / "app-release":
            (Path(destination) / "source" / "backend" / "services" / "reader.py").write_text("X = 3\n", encoding="utf-8")
        return placed

    monkeypatch.setattr(release.shutil, "copytree", copy_then_change)
    with pytest.raises(release.Refused) as caught:
        release.install(
            root,
            git_sha=head(repo),
            requirements=requirements,
            expected_manifest_sha256=digest,
            repo=repo,
            dist_out=tmp_path / "out" / "dist",
            stage_out=tmp_path / "out" / "app-release",
        )
    assert str(caught.value) == "the installed copy: 1 files changed, first app-release/source/backend/services/reader.py"


def test_the_cli_prints_only_counts_and_digests(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(tmp_path)
    requirements = _requirements(tmp_path)
    root = tmp_path / "release"
    common = ["--root", str(root), "--git-sha", head(repo), "--requirements", str(requirements)]
    digest = seal(repo, root, requirements=requirements, dist=make_dist(tmp_path))
    (root / release.MANIFEST).unlink()

    assert release.main(["manifest", *common]) == 0
    assert capsys.readouterr().out.strip() == digest
    assert release.main(["verify", *common, "--expect-manifest-sha256", digest, "--repo", str(repo)]) == 0
    tracked = len(closure.tracked_blobs(repo=repo, ref="HEAD")[1])
    assert capsys.readouterr().out == (
        f"[prebuilt-release] verified {len(_listed(root))} files against SHA256SUMS.json (read-only)\n"
        f"[prebuilt-release] provenance: {tracked} staged source files are this checkout's commit; "
        "6 dist files passed the dist review again (1 .br decode checks are the build job's)\n"
    )
    _flip(root / "dist/index.html")
    assert release.main(["verify", *common, "--expect-manifest-sha256", digest, "--repo", str(repo)]) == 2
    err = capsys.readouterr().err
    assert err.startswith("[prebuilt-release] refused: the prebuilt release: 1 files changed")
    assert "<html>" not in err, "never file contents"


# --------------------------------------------------------------- scripts/deploy.sh


def _deploy(checkout: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "scripts/deploy.sh", "-t", "client", "--dry-run", "--no-confirm", "--skip-silver", "--skip-smoke", *args],
        cwd=checkout,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=300,
    )


def _sandbox_release(tmp_path: Path) -> tuple[Path, dict[str, str], Path, Path, str]:
    checkout, env, log = sandbox(tmp_path)
    root = tmp_path / "prebuilt"
    digest = seal(checkout, root, requirements=checkout / "requirements-deploy.hashes.txt")
    return checkout, env, log, root, digest


@pytest.mark.parametrize("value", ("relative/release", "/nonexistent/mip-release"))
def test_deploy_refuses_a_prebuilt_release_that_is_not_an_absolute_folder(value: str) -> None:
    result = subprocess.run(
        ["bash", "scripts/deploy.sh", "--prebuilt-release", value, "--dry-run"],
        cwd=REPO,
        env={name: v for name, v in os.environ.items() if not name.startswith("GITHUB_")},
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 2
    assert "--prebuilt-release must name an existing folder by its absolute path" in result.stderr


def test_a_dry_run_from_a_prebuilt_release_plans_no_npm_and_no_stage(tmp_path: Path) -> None:
    checkout, env, log, root, digest = _sandbox_release(tmp_path)

    result = _deploy(checkout, {**env, "MIP_PREBUILT_RELEASE_SHA256": digest}, "--prebuilt-release", str(root))

    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
    out = ANSI.sub("", result.stdout)
    commands = planned_commands(result.stdout)
    assert log.read_text(encoding="utf-8").splitlines() == ["--version"], "no Databricks call"
    assert f"[prebuilt-release] verified {len(_listed(root))} files against SHA256SUMS.json (read-only)" in out
    assert "staged source files are this checkout's commit" in out, "provenance in the dry run too"
    assert out.index("[prebuilt-release] verified") < out.index("profile resolved"), "verified before any step"
    assert "frontend deps: skipped (a prebuilt release carries its built dist)" in out
    assert not [command for command in commands if command.startswith("npm ")], "no npm in the deploy job"
    assert not [command for command in commands if "app_runtime_closure stage" in command]
    assert any("tools.databricks.prebuilt_release install --root <prebuilt release>" in command for command in commands)
    assert "would re-hash the prebuilt release and place its dist and staged closure" in out


@pytest.mark.parametrize("arrangement", ("tampered", "resealed", "no digest", "another digest"))
def test_a_prebuilt_release_that_differs_refuses_before_any_databricks_call(tmp_path: Path, arrangement: str) -> None:
    checkout, env, log, root, digest = _sandbox_release(tmp_path)
    if arrangement == "tampered":
        _flip(root / "dist" / ASSET)
    if arrangement == "resealed":
        (root / "app-release/source/backend/main.py").write_text("raise SystemExit('pwned')\n", encoding="utf-8")
        digest = reseal(root, git_sha=head(checkout), requirements=checkout / "requirements-deploy.hashes.txt")
    expected = {"no digest": "", "another digest": "f" * 64}.get(arrangement, digest)

    result = _deploy(checkout, {**env, "MIP_PREBUILT_RELEASE_SHA256": expected}, "--prebuilt-release", str(root))

    assert result.returncode == 2, result.stdout[-2000:] + result.stderr[-2000:]
    message = (
        "needs MIP_PREBUILT_RELEASE_SHA256"
        if arrangement == "no digest"
        else "the prebuilt release is not the one its manifest describes, or its staged source is not this checkout's commit"
    )
    assert message in result.stderr
    if arrangement == "resealed":
        assert "the staged App source against this checkout's commit: 1 files changed, first backend/main.py" in result.stderr
    assert "step 1" not in ANSI.sub("", result.stdout)
    assert not log.exists() or log.read_text(encoding="utf-8") == "", "refused before any Databricks call"


def _step_one(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """install_prebuilt_release, sourced alone, as step 1 runs it in a dry run."""

    script = (
        "set -euo pipefail\n"
        f"RED= RST= DIM= PYTHON=false DRY_RUN=1 SOURCE_GIT_SHA={'a' * 40} PREBUILT_RELEASE_DIR=/nonexistent "
        "APP_RELEASE_STAGE_ROOT=/nonexistent\n"
        ". scripts/lib/deploy_prebuilt_release_lifecycle.sh\n"
        "install_prebuilt_release\n"
        "echo reached the copy\n"
    )
    return subprocess.run(
        ["bash", "-c", script], cwd=REPO, env={"PATH": os.environ["PATH"], **env}, text=True, capture_output=True, check=False, timeout=60
    )


@pytest.mark.parametrize(
    ("env", "refusal"),
    (
        ({}, None),
        ({"MIP_DEFAULT_THEME": "dark", "MIP_DEFAULT_ACCENT": "bright"}, None),
        ({"MIP_DEFAULT_THEME": "light"}, "a tenant default theme or accent is configured"),
        ({"MIP_DEFAULT_ACCENT": "navy"}, "a tenant default theme or accent is configured"),
        ({"MIP_LENDER_MARK_SHA256": "c" * 64}, "a lender mark is configured"),
    ),
)
def test_step_1_refuses_a_prebuilt_release_for_any_tenant_build_input_the_build_job_cannot_bake(
    env: dict[str, str], refusal: str | None
) -> None:
    """vite bakes the theme, the accent and the mark into the dist; the build job sets none of them."""

    result = _step_one(env)
    if refusal is None:
        assert result.returncode == 0, result.stderr
        assert "would re-hash the prebuilt release" in result.stdout and "reached the copy" in result.stdout
    else:
        assert result.returncode == 2 and refusal in result.stderr
        assert "reached the copy" not in result.stdout


def test_the_prebuilt_appearance_defaults_are_the_frontends() -> None:
    plugin = (REPO / "frontend/src/lib/tenantAppearancePlugin.ts").read_text(encoding="utf-8")
    shell = (REPO / "scripts/lib/deploy_prebuilt_release_lifecycle.sh").read_text(encoding="utf-8")
    for constant, variable in (("DEFAULT_THEME_PREFERENCE", "PREBUILT_RELEASE_DEFAULT_THEME"), ("DEFAULT_ACCENT", "PREBUILT_RELEASE_DEFAULT_ACCENT")):
        (value,) = re.findall(rf"export const {constant} = '([a-z]+)';", plugin)
        assert f'{variable}="{value}"\n' in shell

