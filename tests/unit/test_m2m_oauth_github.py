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
    assert github.environment_sink("owner/repo", "acme") == "owner/repo:environment:acme"
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


VALIDATE_REFUSAL = "the validator's federation subject and never holds an M2M secret"


def test_the_validate_environment_is_refused_everywhere_before_gh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("gh must not run for the validator's environment")

    monkeypatch.setattr(github.subprocess, "run", run)
    with pytest.raises(ValueError, match=VALIDATE_REFUSAL):
        github.environment_sink("owner/repo", "acme-validate")
    sink = "owner/repo:environment:acme-validate"
    with pytest.raises(ValueError, match=VALIDATE_REFUSAL):
        github.parse_sink(sink)
    with pytest.raises(SystemExit, match=VALIDATE_REFUSAL):
        github.set_gh_secret(sink, "DATABRICKS_ADMIN_CLIENT_SECRET", "value")
    for check in (github.confirm_gh_secrets, github.invalidate_gh_secrets):
        with pytest.raises(ValueError, match=VALIDATE_REFUSAL):
            check(sink, frozenset({"DATABRICKS_ADMIN_CLIENT_SECRET"}), sleep=lambda _s: None)
    assert VALIDATE_REFUSAL in github.environment_refusals("owner/repo", "acme-validate")[0]
    with pytest.raises(SystemExit, match=VALIDATE_REFUSAL):
        github.require_mint_sink("owner/repo", "acme-validate", available=lambda: True)


def _protected(name: str = "acme", **overrides: object) -> dict[str, object]:
    environment: dict[str, object] = {
        "name": name,
        "can_admins_bypass": False,
        "protection_rules": [
            {
                "type": "required_reviewers",
                "prevent_self_review": False,
                "reviewers": [{"type": "User", "reviewer": {"id": 4242, "login": "maintainer"}}],
            }
        ],
        "deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True},
    }
    environment.update(overrides)
    return environment


def _github(
    environments: list[dict[str, object]],
    *,
    user_id: int = 4242,
    policies: list[dict[str, object]] | None = None,
    variables: list[dict[str, str]] | None = None,
) -> tuple[object, list[tuple[list[str], str]]]:
    """A gh that answers the protection check's reads, recording argv and GH_REPO."""

    calls: list[tuple[list[str], str]] = []
    branch = [{"id": 1, "name": "main", "type": "branch"}] if policies is None else policies
    mode = [{"name": "MIP_REVIEW_MODE", "value": "single_maintainer_audited"}] if variables is None else variables

    def run(args: list[str], **kwargs: object) -> object:
        calls.append((args, dict(kwargs["env"])["GH_REPO"]))  # type: ignore[call-overload]
        if args[1:] == ["api", "--method", "GET", "user"]:
            payload: object = {"login": "maintainer", "id": user_id}
        elif args[1:4] == ["api", "--method", "GET"] and args[4].startswith("repos/owner/repo/environments?"):
            payload = {"total_count": len(environments), "environments": environments}
        elif args[1:4] == ["api", "--method", "GET"] and "/deployment-branch-policies?" in args[4]:
            payload = {"total_count": len(branch), "branch_policies": branch}
        elif args[1:3] == ["variable", "list"]:
            payload = mode
        else:
            raise AssertionError(args)
        return SimpleNamespace(stdout=json.dumps(payload))

    return run, calls


@pytest.mark.parametrize(
    ("answers", "expected"),
    [
        ({"environments": [_protected()]}, []),
        ({"environments": [_protected()], "user_id": 7}, ["github: acme reviewers differs from single_maintainer_audited"]),
        ({"environments": [_protected(can_admins_bypass=True)]}, ["github: acme can_admins_bypass differs from single_maintainer_audited"]),
        ({"environments": [_protected(protection_rules=[])]}, ["github: acme reviewers differs from single_maintainer_audited"]),
        (
            {"environments": [_protected()], "policies": [{"id": 1, "name": "main", "type": "branch"}, {"id": 2, "name": "*", "type": "branch"}]},
            ["github: acme deployment branch policy is not exactly {main}"],
        ),
        ({"environments": [_protected("other")]}, ["github: the acme environment does not exist; run python -m tools.clients.github_environment --apply first"]),
        ({"environments": [_protected()], "variables": []}, ["MIP_REVIEW_MODE: must be one of independent, single_maintainer_audited"]),
    ],
)
def test_the_mint_target_must_be_protected_exactly_for_the_signed_in_maintainer(
    monkeypatch: pytest.MonkeyPatch, answers: dict[str, object], expected: list[str]
) -> None:
    run, calls = _github(**answers)  # type: ignore[arg-type]
    monkeypatch.setattr(github.subprocess, "run", run)
    assert github.environment_refusals("owner/repo", "acme") == expected
    for args, repo in calls:
        assert repo == "owner/repo" and "--repo" not in args and "-R" not in args
        assert args[1] in {"api", "variable"} and (args[1] != "api" or args[2:4] == ["--method", "GET"])


def test_an_unreadable_target_refuses_without_its_output(monkeypatch: pytest.MonkeyPatch) -> None:
    def run(args: list[str], **_kwargs: object) -> object:
        raise subprocess.CalledProcessError(1, args, output="canary-gh-output", stderr="canary-gh-output")

    monkeypatch.setattr(github.subprocess, "run", run)
    assert github.environment_refusals("owner/repo", "acme") == [
        "github: the protections of acme could not be read; nothing was minted"
    ]


def test_require_mint_sink_checks_gh_then_the_protections(monkeypatch: pytest.MonkeyPatch) -> None:
    checked: list[tuple[str, str]] = []

    def refusals(repo: str, environment: str) -> list[str]:
        checked.append((repo, environment))
        return ["github: acme reviewers differs from single_maintainer_audited"]

    monkeypatch.setattr(github, "environment_refusals", refusals)
    with pytest.raises(SystemExit, match="authenticated gh CLI"):
        github.require_mint_sink("owner/repo", "acme", available=lambda: False)
    assert checked == []
    with pytest.raises(SystemExit, match="refused before any mutation: github: acme reviewers differs"):
        github.require_mint_sink("owner/repo", "acme", available=lambda: True)
    assert checked == [("owner/repo", "acme")]
    monkeypatch.setattr(github, "environment_refusals", lambda _repo, _environment: [])
    github.require_mint_sink("owner/repo", "acme", available=lambda: True)
