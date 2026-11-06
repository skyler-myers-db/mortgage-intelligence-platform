"""A sealed prebuilt release of a git checkout, as deploy-dev.yml's build job makes it (M1-PR7).

``seal`` stages the App runtime closure at the checkout's HEAD
(tools/databricks/app_runtime_closure.stage) with a governed dist, copies that
dist beside it, adds a one-wheel wheelhouse and writes ``SHA256SUMS.json``
(tools/databricks/prebuilt_release.write_manifest). ``governed_dist`` is the
shell, one hashed asset and every frontend/public file git tracks at HEAD.
``reseal`` rewrites both manifests over a tampered stage, as a compromised
build job would. Used by tests/unit/test_prebuilt_release.py and
tests/unit/test_public_summary_redaction.py.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from tools.databricks import app_release_contract as release_contract
from tools.databricks import app_runtime_closure as closure
from tools.databricks import prebuilt_release

ASSET = "assets/app-DnQ3_k-1.js"
APP_JS = b"app();\n" * 50
WHEEL = "wheelhouse/fastapi-0.136.1-py3-none-any.whl"


def head(repo: Path) -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


def governed_dist(repo: Path, out: Path, extra: dict[str, bytes] | None = None) -> Path:
    """The shell, one hashed asset, the tracked frontend/public files and ``extra``."""

    _, _, public = closure.tracked_blobs(repo=repo, ref="HEAD")
    for rel, data in {"index.html": b"<!doctype html>\n", ASSET: APP_JS, **public, **(extra or {})}.items():
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        (out / rel).write_bytes(data)
    return out


def seal(repo: Path, root: Path, *, requirements: Path, dist: Path | None = None) -> str:
    """Write the release for ``repo``'s HEAD under ``root``; return its manifest sha256."""

    built = dist or governed_dist(repo, root.parent / f"{root.name}-built-dist")
    closure.stage(
        repo=repo,
        ref=head(repo),
        dist=built,
        out=root / "app-release" / "source",
        manifest_out=root / prebuilt_release.RELEASE_MANIFEST,
    )
    shutil.copytree(root / prebuilt_release.STAGED_DIST, root / "dist")
    (root / WHEEL).parent.mkdir(parents=True, exist_ok=True)
    (root / WHEEL).write_bytes(b"PK-not-a-real-wheel")
    return prebuilt_release.write_manifest(root, git_sha=head(repo), requirements=requirements)


def reseal(root: Path, *, git_sha: str, requirements: Path) -> str:
    """Re-write app-release/manifest.json and SHA256SUMS.json over whatever ``root`` now holds."""

    source = root / "app-release" / "source"
    release_contract.write_manifest(
        root / prebuilt_release.RELEASE_MANIFEST, commit=git_sha, entries=release_contract.manifest_entries(source)
    )
    (root / prebuilt_release.MANIFEST).unlink()
    return prebuilt_release.write_manifest(root, git_sha=git_sha, requirements=requirements)
