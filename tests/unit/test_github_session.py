"""The fresh, throwaway gh session (tools/clients/github_session.py).

* The child environment drops every ``GH_*``, ``GITHUB_*`` and ``XDG_*``
  name the operator's shell holds and sets the session's own.
* An argv naming another repository or carrying a value is refused.
* The session directory is private, starts empty, refuses reuse and is
  deleted afterwards; no gh call runs before the fresh login.
* A failure names the subcommand and exit code, never gh's output.
"""

from __future__ import annotations

import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tools.clients.errors import BootstrapRefusal
from tools.clients.github_session import GhFailure, check_argv, child_environment, fresh_gh_session

CANARY = "canary-gh-output-81c2aa"


def test_the_child_environment_keeps_no_github_configuration(tmp_path: Path) -> None:
    operator = {
        "PATH": "/usr/bin",
        "LANG": "C.UTF-8",
        "GH_TOKEN": "t",
        "GITHUB_TOKEN": "t",
        "GH_ENTERPRISE_TOKEN": "t",
        "GITHUB_ENTERPRISE_TOKEN": "t",
        "GH_HOST": "evil.example",
        "GH_REPO": "attacker/repo",
        "GH_DEBUG": "api",
        "GH_CONFIG_DIR": "/operator/gh",
        "XDG_CONFIG_HOME": "/operator/config",
        "HOME": "/operator",
    }
    env = child_environment(operator, config_dir=tmp_path / "gh", home=tmp_path / "home", repository="acme-org/mip")
    assert env["PATH"] == "/usr/bin" and env["LANG"] == "C.UTF-8"
    assert env["GH_CONFIG_DIR"] == str(tmp_path / "gh") and env["HOME"] == str(tmp_path / "home")
    assert env["GH_REPO"] == "acme-org/mip" and env["GH_PROMPT_DISABLED"] == "1"
    for name in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN", "GH_HOST", "GH_DEBUG", "XDG_CONFIG_HOME"):
        assert name not in env


@pytest.mark.parametrize(
    "argv",
    [
        ["secret", "set", "X", "--repo", "a/b"],
        ["secret", "set", "X", "-R", "a/b"],
        ["secret", "set", "X", "--repo=a/b"],
        ["secret", "set", "X", "--body", "v"],
        ["variable", "set", "X", "-b", "v"],
        ["api", "--method", "PUT", "p", "-f", "a=b"],
        ["api", "--method", "PUT", "p", "--raw-field", "a=b"],
        ["auth", "login", "--with-token"],
        ["secret", "set", "--env-file", "f"],
        ["api", "--method", "PUT", '{"a": 1}'],
        ["secret", "set", "X Y"],
    ],
)
def test_argv_that_names_a_repository_or_carries_a_value_is_refused(argv: list[str]) -> None:
    with pytest.raises(BootstrapRefusal):
        check_argv(argv)


def test_plain_names_flags_and_api_paths_pass() -> None:
    check_argv(["secret", "set", "DATABRICKS_HOST", "--env", "acme"])
    check_argv(["api", "--method", "GET", "repos/acme-org/mip/environments?per_page=100"])
    check_argv(["variable", "list", "--env", "acme-validate", "--json", "name,value"])


def test_the_session_directory_is_private_and_deleted(tmp_path: Path) -> None:
    root = tmp_path / "session"
    seen: dict[str, Any] = {}
    with fresh_gh_session("acme-org/mip", environ={}, make_root=lambda: (root.mkdir(), root)[1]) as session:
        seen["modes"] = {path.name: stat.S_IMODE(path.stat().st_mode) for path in (root, session.config_dir, session.home)}
        assert list(session.config_dir.iterdir()) == []
    assert seen["modes"] == {"session": 0o700, "gh": 0o700, "home": 0o700}
    assert not root.exists()


def test_a_session_directory_that_is_not_empty_is_refused_and_kept(tmp_path: Path) -> None:
    root = tmp_path / "stale"
    (root / "gh").mkdir(parents=True)
    (root / "gh" / "hosts.yml").write_text("github.com:\n    oauth_token: stale\n", encoding="utf-8")
    with pytest.raises(BootstrapRefusal, match="not empty"), fresh_gh_session("acme-org/mip", make_root=lambda: root):
        pass
    assert (root / "gh" / "hosts.yml").exists()


def test_a_bad_repository_is_refused_before_any_directory(tmp_path: Path) -> None:
    def never() -> Path:
        raise AssertionError("no directory for a refused repository")

    for repository in ("acme", "acme/mip/extra", "acme mip", ""):
        with pytest.raises(BootstrapRefusal, match="--github-repository"), fresh_gh_session(repository, make_root=never):
            pass


def test_no_call_runs_before_the_login_and_failures_carry_no_output(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def runner(argv: list[str], **kwargs: Any) -> Any:
        calls.append(argv)
        if argv[1:3] == ["auth", "login"]:
            return SimpleNamespace(returncode=0)
        if argv[1:4] == ["api", "--method", "GET"] and argv[4] == "user":
            return SimpleNamespace(returncode=0, stdout=b'{"login": "acme-maintainer", "id": 4242}')
        if argv[1:3] == ["auth", "status"]:
            return SimpleNamespace(returncode=0, stdout=b"")
        return SimpleNamespace(returncode=1, stdout=CANARY.encode(), stderr=CANARY.encode())

    with fresh_gh_session("acme-org/mip", environ={}, make_root=lambda: (tmp_path / "s").mkdir() or tmp_path / "s", runner=runner) as session:
        with pytest.raises(BootstrapRefusal, match="no fresh gh login"):
            session.call(["secret", "list", "--env", "acme", "--json", "name"])
        assert calls == []
        assert session.login() == {"login": "acme-maintainer", "id": 4242}
        assert session.login() == {"login": "acme-maintainer", "id": 4242}  # once
        with pytest.raises(GhFailure) as failed:
            session.call(["secret", "set", "X", "--env", "acme"], stdin=b"value")
        assert str(failed.value) == "gh secret set failed (exit 1)" and CANARY not in str(failed.value)
    assert [argv[1:3] for argv in calls].count(["auth", "login"]) == 1


def test_a_login_that_does_not_complete_refuses(tmp_path: Path) -> None:
    def runner(argv: list[str], **kwargs: Any) -> Any:
        return SimpleNamespace(returncode=1)

    with fresh_gh_session("acme-org/mip", environ={}, make_root=lambda: (tmp_path / "s").mkdir() or tmp_path / "s", runner=runner) as session:
        with pytest.raises(BootstrapRefusal, match="the gh login did not complete"):
            session.login()
        assert not session.logged_in
