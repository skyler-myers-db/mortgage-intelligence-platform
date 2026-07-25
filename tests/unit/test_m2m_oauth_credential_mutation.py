from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools.databricks import m2m_oauth_credential_mutation as mutation

_HEAD = "a" * 40


def _git_runner(*, status: str = "") -> object:
    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if command == ["git", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(command, 0, f"{_HEAD}\n", "")
        if command == [
            "git",
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ]:
            return subprocess.CompletedProcess(command, 0, status, "")
        raise AssertionError(f"unexpected command: {command}")

    return run


def test_standalone_credential_source_requires_clean_exact_head(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("MIP_APP_DEPLOYMENT_LEASE_ID", raising=False)
    monkeypatch.setenv("MIP_DEPLOYMENT_SOURCE_GIT_SHA", _HEAD)
    monkeypatch.setattr(subprocess, "run", _git_runner())

    assert mutation.credential_source_git_sha(tmp_path) == _HEAD


@pytest.mark.parametrize(
    "status",
    (
        " M tools/databricks/provision_m2m_oauth.py\n",
        "?? tools/databricks/unreviewed_credential_path.py\n",
    ),
)
def test_standalone_credential_source_rejects_dirty_worktree(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    status: str,
) -> None:
    monkeypatch.delenv("MIP_APP_DEPLOYMENT_LEASE_ID", raising=False)
    monkeypatch.delenv("MIP_DEPLOYMENT_SOURCE_GIT_SHA", raising=False)
    monkeypatch.setattr(subprocess, "run", _git_runner(status=status))

    with pytest.raises(RuntimeError, match="clean tracked and untracked"):
        mutation.credential_source_git_sha(tmp_path)


def test_standalone_credential_source_rejects_configured_sha_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("MIP_APP_DEPLOYMENT_LEASE_ID", raising=False)
    monkeypatch.setenv("MIP_DEPLOYMENT_SOURCE_GIT_SHA", "b" * 40)
    monkeypatch.setattr(subprocess, "run", _git_runner())

    with pytest.raises(RuntimeError, match="does not match HEAD"):
        mutation.credential_source_git_sha(tmp_path)


def test_borrowed_deploy_lease_uses_its_preflighted_source_without_git(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(
        "MIP_APP_DEPLOYMENT_LEASE_ID",
        "11111111-1111-4111-8111-111111111111",
    )
    monkeypatch.setenv("MIP_DEPLOYMENT_SOURCE_GIT_SHA", _HEAD)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail(
            "borrowed lease source was already preflighted"
        ),
    )

    assert mutation.credential_source_git_sha(tmp_path) == _HEAD


def test_borrowed_deploy_lease_requires_explicit_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(
        "MIP_APP_DEPLOYMENT_LEASE_ID",
        "11111111-1111-4111-8111-111111111111",
    )
    monkeypatch.delenv("MIP_DEPLOYMENT_SOURCE_GIT_SHA", raising=False)

    with pytest.raises(RuntimeError, match="requires its exact source"):
        mutation.credential_source_git_sha(tmp_path)
