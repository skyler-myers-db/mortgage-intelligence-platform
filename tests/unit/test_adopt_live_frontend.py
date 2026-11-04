"""adopt-live's frontend proof (M2-PR7, attack G13): only its own build of the health sha verifies.

No directory an operator names can make ``frontend/dist`` verified (a copy of
the live dist itself compares equal), so adopt-live builds the reference
itself from ``git archive`` of the health sha, with an allowlisted
environment, and the plan and the record name the digest it matched.
"""

from __future__ import annotations

import dataclasses
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures import rollback_records as rr
from tests.fixtures.adopt_live_world import DIST, World, make_repo
from tests.unit.test_adopt_live import _cli_argv
from tools.databricks import adopt_live
from tools.databricks import adopted_source_copy as source
from tools.databricks import app_release_contract as release
from tools.databricks import app_rollback_record_contract as contract
from tools.databricks.adopt_live_facts import Readers


@pytest.fixture(autouse=True)
def _keys(monkeypatch: pytest.MonkeyPatch) -> None:
    rr.signing_env(monkeypatch)
    monkeypatch.setattr(contract, "assert_owned_app_rollback_scope", lambda *_a, **_kw: None)


@pytest.fixture
def world(tmp_path: Path) -> World:
    repo, main, side = make_repo(tmp_path)
    return World(repo=repo, main=main, side=side)


def _builder(tmp_path: Path, files: dict[str, bytes], built: list[str]) -> Any:
    def build(commit: str) -> Path:
        built.append(commit)
        dist = tmp_path / f"reference-{len(built)}"
        for relpath, data in files.items():
            target = dist / relpath.removeprefix("frontend/dist/")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        return dist

    return build


def test_the_frontend_is_verified_only_against_the_tools_own_build_of_the_health_sha(
    world: World, tmp_path: Path
) -> None:
    built: list[str] = []
    rebuilt = {**DIST, "frontend/dist/assets/app-abcd1234.js": b"rebuilt\n"}

    verified = adopt_live.compute_plan(
        world,
        "adopt",
        world.target(),
        world.readers(build_reference=_builder(tmp_path, DIST, built)),
    )[0]
    unverified = adopt_live.compute_plan(
        world,
        "adopt",
        world.target(),
        world.readers(build_reference=_builder(tmp_path, rebuilt, built)),
    )[0]

    def failed(_commit: str) -> Path:
        raise RuntimeError("npm ci failed in /private/path")

    broken = adopt_live.compute_plan(
        world, "adopt", world.target(), world.readers(build_reference=failed)
    )[0]
    assert built == [world.main, world.main], "the reference is built from the health sha"
    assert (verified["frontend_provenance"], verified["frontend_reference"]) == (
        "verified",
        "built_from_git_sha",
    )
    assert verified["frontend_reference_sha256"] == source.dist_digest(
        {path: data for path, data in DIST.items()}
    ), "the ack covers the digest verified was judged against"
    assert (unverified["frontend_provenance"], unverified["frontend_reference_sha256"]) == (
        "unverified",
        None,
    )
    assert (broken["frontend_provenance"], broken["frontend_reference"]) == (
        "unverified",
        "build_failed",
    )
    record = world.apply(readers=world.readers(build_reference=_builder(tmp_path, DIST, built)))
    assert record["frontend_provenance"] == "verified"
    assert record["frontend_reference_sha256"] == verified["frontend_reference_sha256"]


def test_no_operator_named_directory_can_make_the_frontend_verified(world: World) -> None:
    """A copy of the live dist itself compared equal: the reference is never an operator input."""

    assert "reference_dist" not in {field.name for field in dataclasses.fields(Readers)}
    with pytest.raises(SystemExit):
        adopt_live._parser().parse_args(
            [*_cli_argv(world), "--reference-dist", "/tmp/copied-from-the-live-folder"]
        )
    args = adopt_live._parser().parse_args([*_cli_argv(world), "--build-reference-dist"])
    assert args.build_reference_dist is True


def test_the_reference_build_archives_the_sha_and_sees_no_credential(
    world: World, tmp_path: Path
) -> None:
    calls: list[tuple[list[str], dict[str, str] | None]] = []

    def run(argv: list[str], **kwargs: Any) -> Any:
        calls.append((argv, kwargs.get("env")))
        if argv[0] == "git":
            return subprocess.run(argv, capture_output=True, check=True)
        dist = Path(argv[2]) / "dist"
        dist.mkdir(parents=True, exist_ok=True)
        (dist / "index.html").write_bytes(b"built\n")
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    environ = {
        "PATH": "/usr/bin",
        "HOME": "/home/x",
        "npm_config_registry": "https://registry.example.invalid",
        "MIP_LENDER_NAME": "Summit Mortgage",
        "MIP_AI_GATEWAY_PROOF_SIGNING_KEY": "PLACEHOLDER",
        "DATABRICKS_CLIENT_SECRET": "PLACEHOLDER",
        "MIP_BEARER_TOKEN": "PLACEHOLDER",
    }
    dist = source.build_reference_dist(
        world.repo, world.main, tmp_path / "w", run=run, environ=environ
    )

    assert (dist / "index.html").read_bytes() == b"built\n"
    assert (tmp_path / "w" / "tree" / "backend" / "main.py").read_bytes() == b"print('mip')\n"
    (git, _), (ci, ci_env), (build, build_env) = calls
    assert git[-2:] == ["--format=tar", world.main]
    assert ci[1:] == [
        "--prefix",
        str(tmp_path / "w" / "tree" / "frontend"),
        "ci",
        "--no-audit",
        "--no-fund",
    ]
    assert build[3:] == ["run", "build"]
    assert (
        ci_env
        == build_env
        == {
            "PATH": "/usr/bin",
            "HOME": "/home/x",
            "npm_config_registry": "https://registry.example.invalid",
            "MIP_LENDER_NAME": "Summit Mortgage",
        }
    )
    with pytest.raises(release.ReleaseContractError):
        source.build_reference_dist(world.repo, "main", tmp_path / "x", run=run, environ=environ)
