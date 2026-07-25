from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from tools.databricks import m2m_oauth_github as github


def _list_response(*names: str) -> object:
    return SimpleNamespace(
        stdout=json.dumps([{"name": name} for name in names])
    )


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
        "owner/repo.with.dots",
        frozenset({"CLIENT_ID", "CLIENT_SECRET"}),
        sleep=lambda _seconds: None,
    )

    delete_calls = [call for call in calls if call[2] == "delete"]
    assert delete_calls == [
        ["gh", "secret", "delete", "CLIENT_ID", "--repo", "owner/repo.with.dots"],
        [
            "gh",
            "secret",
            "delete",
            "CLIENT_SECRET",
            "--repo",
            "owner/repo.with.dots",
        ],
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
            "owner/repo",
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
            "owner/repo",
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
        "owner/repo.with.dots",
        frozenset({"CLIENT_ID", "CLIENT_SECRET"}),
        sleep=lambda _seconds: None,
    )

    assert len(calls) == 3
    assert all(
        call
        == [
            "gh",
            "secret",
            "list",
            "--repo",
            "owner/repo.with.dots",
            "--json",
            "name",
        ]
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
            "owner/repo",
            frozenset({"CLIENT_ID", "CLIENT_SECRET"}),
            sleep=lambda _seconds: None,
        )
