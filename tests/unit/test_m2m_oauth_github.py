from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from tools.databricks import m2m_oauth_github as github

SINK = github.environment_sink("owner/repo.with.dots", "acme")
PLAIN_SINK = github.environment_sink("owner/repo", "acme")


def _list_response(*names: str) -> object:
    return SimpleNamespace(
        stdout=json.dumps([{"name": name} for name in names])
    )


def test_every_call_names_the_environment_and_never_the_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--env`` on every call; the repository only through ``GH_REPO``."""

    seen: list[tuple[list[str], dict[str, str], object]] = []

    def run(args: list[str], **kwargs: object) -> object:
        seen.append((args, dict(kwargs["env"]), kwargs.get("input")))  # type: ignore[call-overload]
        return _list_response("CLIENT_SECRET")

    monkeypatch.setenv("GH_REPO", "attacker/ambient")
    monkeypatch.setattr(github.subprocess, "run", run)
    github.set_gh_secret(SINK, "CLIENT_SECRET", "canary-value")
    github.confirm_gh_secrets(SINK, frozenset({"CLIENT_SECRET"}), sleep=lambda _s: None)
    github.invalidate_gh_secrets(SINK, frozenset({"OTHER"}), sleep=lambda _s: None)

    assert seen
    for args, env, _stdin in seen:
        assert args[-2:] == ["--env", "acme"]
        assert "--repo" not in args and "-R" not in args
        assert env["GH_REPO"] == "owner/repo.with.dots"
        assert all("canary-value" not in arg for arg in args)
    assert seen[0] == (
        ["gh", "secret", "set", "CLIENT_SECRET", "--env", "acme"],
        seen[0][1],
        b"canary-value",
    )


@pytest.mark.parametrize(
    "sink",
    [
        "owner/repo",
        "owner/repo:environment:",
        "owner/repo:environment:Acme",
        "owner/repo:environment:acme/../x",
        "repo:environment:acme",
    ],
)
def test_a_repository_level_or_malformed_sink_is_refused_before_gh(
    monkeypatch: pytest.MonkeyPatch, sink: str
) -> None:
    def run(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("gh must not run for a refused sink")

    monkeypatch.setattr(github.subprocess, "run", run)
    with pytest.raises(SystemExit, match="repository-level secrets are refused"):
        github.set_gh_secret(sink, "CLIENT_SECRET", "value")
    for check in (github.confirm_gh_secrets, github.invalidate_gh_secrets):
        with pytest.raises(ValueError, match="repository-level secrets are refused"):
            check(sink, frozenset({"CLIENT_SECRET"}), sleep=lambda _s: None)


def test_the_environment_sink_rejects_a_bad_repository_or_environment() -> None:
    assert github.environment_sink("owner/repo", "acme-validate") == (
        "owner/repo:environment:acme-validate"
    )
    assert github.parse_sink("owner/repo:environment:acme") == ("owner/repo", "acme")
    for repo, environment in (("owner", "acme"), ("owner/repo", ""), ("owner/repo", "ACME")):
        with pytest.raises(ValueError, match="repository-level secrets are refused"):
            github.environment_sink(repo, environment)
    with pytest.raises(ValueError, match="refuses --repo"):
        github._gh(SINK, ["secret", "list", "--repo", "attacker/repo"])


def test_invalidate_deletes_only_armed_names_and_proves_repeated_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    responses = iter(
        [
            _list_response("KEEP", "CLIENT_ID", "CLIENT_SECRET"),
            SimpleNamespace(),
            SimpleNamespace(),
            _list_response("KEEP"),
            _list_response("KEEP"),
            _list_response("KEEP"),
        ]
    )

    def run(args: list[str], **_kwargs: object) -> object:
        calls.append(args)
        return next(responses)

    monkeypatch.setattr(github.subprocess, "run", run)

    github.invalidate_gh_secrets(
        SINK,
        frozenset({"CLIENT_ID", "CLIENT_SECRET"}),
        sleep=lambda _seconds: None,
    )

    delete_calls = [call for call in calls if call[2] == "delete"]
    assert delete_calls == [
        ["gh", "secret", "delete", "CLIENT_ID", "--env", "acme"],
        ["gh", "secret", "delete", "CLIENT_SECRET", "--env", "acme"],
    ]
    assert all("KEEP" not in call for call in delete_calls)
    assert len([call for call in calls if call[2] == "list"]) == 4


def test_invalidate_refuses_to_accept_a_delete_error_even_if_it_might_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def run(args: list[str], **_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        if args[2] == "list":
            return _list_response("CLIENT_SECRET")
        raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(github.subprocess, "run", run)

    with pytest.raises(RuntimeError, match="deletion is unproven"):
        github.invalidate_gh_secrets(
            PLAIN_SINK,
            frozenset({"CLIENT_SECRET"}),
            sleep=lambda _seconds: None,
        )

    assert calls == 2


def test_invalidate_rejects_stale_or_reappearing_armed_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        [
            _list_response(),
            _list_response("CLIENT_SECRET"),
        ]
    )
    monkeypatch.setattr(
        github.subprocess,
        "run",
        lambda *_args, **_kwargs: next(responses),
    )

    with pytest.raises(RuntimeError, match="still contains"):
        github.invalidate_gh_secrets(
            PLAIN_SINK,
            frozenset({"CLIENT_SECRET"}),
            sleep=lambda _seconds: None,
        )


def test_confirm_requires_repeated_presence_of_every_armed_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def run(args: list[str], **_kwargs: object) -> object:
        calls.append(args)
        return _list_response("CLIENT_ID", "CLIENT_SECRET", "UNRELATED")

    monkeypatch.setattr(github.subprocess, "run", run)

    github.confirm_gh_secrets(
        SINK,
        frozenset({"CLIENT_ID", "CLIENT_SECRET"}),
        sleep=lambda _seconds: None,
    )

    assert len(calls) == 3
    assert all(
        call
        == ["gh", "secret", "list", "--json", "name", "--env", "acme"]
        for call in calls
    )


def test_confirm_rejects_missing_or_reappearing_armed_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        [
            _list_response("CLIENT_ID", "CLIENT_SECRET"),
            _list_response("CLIENT_ID"),
        ]
    )
    monkeypatch.setattr(
        github.subprocess,
        "run",
        lambda *_args, **_kwargs: next(responses),
    )

    with pytest.raises(RuntimeError, match="acknowledgement is incomplete"):
        github.confirm_gh_secrets(
            PLAIN_SINK,
            frozenset({"CLIENT_ID", "CLIENT_SECRET"}),
            sleep=lambda _seconds: None,
        )
