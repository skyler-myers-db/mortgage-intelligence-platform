"""A prebuilt release is re-hashed against its manifest and refused on any difference (M1-PR7).

deploy-dev.yml's build job (no id-token) writes the release and its
``SHA256SUMS.json``; the deploy job verifies it before installing a wheel,
and ``scripts/deploy.sh --prebuilt-release <dir>`` verifies it again before
any Databricks call and installs it in step 1 in place of npm and the stage
(tools/databricks/prebuilt_release.py,
scripts/lib/deploy_prebuilt_release_lifecycle.sh).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.fixtures.deploy_sandbox import ANSI, planned_commands, sandbox
from tools.databricks import prebuilt_release as release

REPO = Path(__file__).resolve().parents[2]
SHA = "1" * 40
FILES = {
    "dist/index.html": b"<!doctype html>",
    "dist/assets/app-abc123.js": b"console.log(1)",
    "app-release/source/app.yaml": b"command: [python]",
    "app-release/manifest.json": b"{}",
    "wheelhouse/fastapi-0.136.1-py3-none-any.whl": b"PK-not-a-real-wheel",
}


def _tree(root: Path, files: dict[str, bytes] = FILES) -> Path:
    for rel, data in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


def _requirements(tmp_path: Path, text: str = "fastapi==0.136.1 \\\n    --hash=sha256:" + "a" * 64 + "\n") -> Path:
    path = tmp_path / "requirements-deploy.hashes.txt"
    path.write_text(text, encoding="utf-8")
    return path


def _built(tmp_path: Path) -> tuple[Path, Path, str]:
    root = _tree(tmp_path / "release")
    requirements = _requirements(tmp_path)
    digest = release.write_manifest(root, git_sha=SHA, requirements=requirements)
    return root, requirements, digest


def test_a_manifest_lists_every_file_but_itself_and_its_digest_is_printed(tmp_path: Path) -> None:
    root, requirements, digest = _built(tmp_path)
    data = (root / release.MANIFEST).read_bytes()
    body = json.loads(data)

    assert digest == hashlib.sha256(data).hexdigest()
    assert body["schema"] == release.SCHEMA and body["git_sha"] == SHA
    assert body["requirements_sha256"] == release.file_sha256(requirements)
    assert sorted(body["files"]) == sorted(FILES)
    assert body["files"]["dist/index.html"] == {"sha256": hashlib.sha256(FILES["dist/index.html"]).hexdigest(), "size": 15}
    assert release.verify(root, git_sha=SHA, requirements=requirements, expected_manifest_sha256=digest) == body
    with pytest.raises(release.Refused, match="made once"):
        release.write_manifest(root, git_sha=SHA, requirements=requirements)


def _flip(path: Path) -> None:
    data = bytearray(path.read_bytes())
    data[0] ^= 0x01
    path.write_bytes(bytes(data))


TAMPERS = {
    "a byte flipped, same size": (lambda root: _flip(root / "dist/index.html"), "1 files changed, first dist/index.html"),
    "a wheel resized": (lambda root: (root / "wheelhouse/fastapi-0.136.1-py3-none-any.whl").write_bytes(b"PK"), "changed"),
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
    root, requirements, digest = _built(tmp_path)
    change, message = TAMPERS[tamper]
    change(root)

    with pytest.raises(release.Refused, match=message.replace("(", r"\(")):
        release.verify(root, git_sha=SHA, requirements=requirements, expected_manifest_sha256=digest)


def test_another_commit_another_requirements_file_or_a_bad_digest_refuses(tmp_path: Path) -> None:
    root, requirements, digest = _built(tmp_path)

    with pytest.raises(release.Refused, match="another commit"):
        release.verify(root, git_sha="2" * 40, requirements=requirements, expected_manifest_sha256=digest)
    (tmp_path / "other").mkdir()
    other = _requirements(tmp_path / "other", "fastapi==0.136.2\n")
    with pytest.raises(release.Refused, match="another requirements-deploy.hashes.txt"):
        release.verify(root, git_sha=SHA, requirements=other, expected_manifest_sha256=digest)
    with pytest.raises(release.Refused, match="64 lowercase hex"):
        release.verify(root, git_sha=SHA, requirements=requirements, expected_manifest_sha256="")


def test_a_manifest_listing_a_path_outside_the_release_refuses(tmp_path: Path) -> None:
    root, requirements, _ = _built(tmp_path)
    body = json.loads((root / release.MANIFEST).read_bytes())
    body["files"]["../escape"] = {"sha256": "0" * 64, "size": 0}
    data = release.manifest_bytes(SHA, body["requirements_sha256"], body["files"])
    (root / release.MANIFEST).write_bytes(data)

    with pytest.raises(release.Refused, match="not a plain relative path"):
        release.verify(root, git_sha=SHA, requirements=requirements, expected_manifest_sha256=hashlib.sha256(data).hexdigest())


def test_install_replaces_each_destination_whole_and_rehashes_the_copy(tmp_path: Path) -> None:
    root, requirements, digest = _built(tmp_path)
    dist_out = tmp_path / "checkout" / "frontend" / "dist"
    stage_out = tmp_path / "checkout" / ".databricks" / "app-release"
    _tree(dist_out.parent, {"dist/stale.js": b"old build"})

    count = release.install(
        root, git_sha=SHA, requirements=requirements, expected_manifest_sha256=digest, dist_out=dist_out, stage_out=stage_out
    )

    assert count == 4, "dist and app-release; the wheelhouse stays in the release"
    assert not (dist_out / "stale.js").exists(), "an old build never survives beside the release"
    assert (dist_out / "index.html").read_bytes() == FILES["dist/index.html"]
    assert (stage_out / "source" / "app.yaml").read_bytes() == FILES["app-release/source/app.yaml"]
    _flip(root / "dist/index.html")
    with pytest.raises(release.Refused, match="changed"):
        release.install(
            root, git_sha=SHA, requirements=requirements, expected_manifest_sha256=digest, dist_out=dist_out, stage_out=stage_out
        )


def test_the_cli_prints_only_counts_and_digests(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _tree(tmp_path / "release")
    requirements = _requirements(tmp_path)
    common = ["--root", str(root), "--git-sha", SHA, "--requirements", str(requirements)]

    assert release.main(["manifest", *common]) == 0
    digest = capsys.readouterr().out.strip()
    assert len(digest) == 64
    assert release.main(["verify", *common, "--expect-manifest-sha256", digest]) == 0
    assert capsys.readouterr().out == f"[prebuilt-release] verified {len(FILES)} files against SHA256SUMS.json (read-only)\n"
    _flip(root / "dist/index.html")
    assert release.main(["verify", *common, "--expect-manifest-sha256", digest]) == 2
    err = capsys.readouterr().err
    assert err.startswith("[prebuilt-release] refused: the prebuilt release: 1 files changed")
    assert "doctype" not in err, "never file contents"


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
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=checkout, capture_output=True, text=True, check=True).stdout.strip()
    root = _tree(tmp_path / "prebuilt")
    digest = release.write_manifest(root, git_sha=head, requirements=checkout / "requirements-deploy.hashes.txt")
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
    assert f"[prebuilt-release] verified {len(FILES)} files against SHA256SUMS.json (read-only)" in out
    assert out.index("[prebuilt-release] verified") < out.index("profile resolved"), "verified before any step"
    assert "frontend deps: skipped (a prebuilt release carries its built dist)" in out
    assert not [command for command in commands if command.startswith("npm ")], "no npm in the deploy job"
    assert not [command for command in commands if "app_runtime_closure stage" in command]
    assert any("tools.databricks.prebuilt_release install --root <prebuilt release>" in command for command in commands)
    assert "would re-hash the prebuilt release and place its dist and staged closure" in out


@pytest.mark.parametrize("arrangement", ("tampered", "no digest", "another digest"))
def test_a_prebuilt_release_that_differs_refuses_before_any_databricks_call(tmp_path: Path, arrangement: str) -> None:
    checkout, env, log, root, digest = _sandbox_release(tmp_path)
    if arrangement == "tampered":
        _flip(root / "dist/assets/app-abc123.js")
    expected = {"tampered": digest, "no digest": "", "another digest": "f" * 64}[arrangement]

    result = _deploy(checkout, {**env, "MIP_PREBUILT_RELEASE_SHA256": expected}, "--prebuilt-release", str(root))

    assert result.returncode == 2, result.stdout[-2000:] + result.stderr[-2000:]
    message = (
        "needs MIP_PREBUILT_RELEASE_SHA256"
        if arrangement == "no digest"
        else "the prebuilt release is not the one its manifest describes"
    )
    assert message in result.stderr
    assert "step 1" not in ANSI.sub("", result.stdout)
    assert not log.exists() or log.read_text(encoding="utf-8") == "", "refused before any Databricks call"
