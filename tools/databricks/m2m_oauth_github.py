"""GitHub CLI secret sink for M2M OAuth provisioning.

**One GitHub Environment, never the repository** (plan M2-PR2). The sink
is a coordinate ``<owner>/<repo>:environment:<environment>`` built by
``environment_sink``. Every call passes ``--env <environment>`` and never
``--repo``: the repository reaches gh through ``GH_REPO`` in the child
environment. A bare ``<owner>/<repo>`` (a repository-level secret, which
every workflow of the repository can read) is refused by every function
here, including the invalidator a signed-intent recovery calls: a
credential intent armed with a repository-level sink before this change
needs its armed names deleted by hand (docs/security/m2m-oauth-setup.md).

**Only a reviewed deploy environment.** ``<client>-validate`` is the
validator's federation subject and never receives an M2M secret. Before any
mint, ``require_mint_sink`` holds the target to the GitHub-environment
sink's bar (tools/clients/github_sink.py): it exists with exactly the
protections its own ``MIP_REVIEW_MODE`` names, the signed-in gh user as its
one required reviewer, no admin bypass, and a branch policy of exactly
``{main}``. These calls run under the operator's own gh login, so that login
must be the client's maintainer: any other identity, a CI token or a bot
fails the reviewer check.

Values reach gh through stdin only, never argv.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

GH_SECRET_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
GH_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
# A client's GitHub Environment: the MIP_CLIENT shape (tools/clients/schema.py).
GH_ENVIRONMENT_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}$")
SINK_SEPARATOR = ":environment:"
VALIDATE_SUFFIX = "-validate"  # tools/clients/github_protections.py
_REPOSITORY_FLAGS = frozenset({"--repo", "-R"})
_SINK_ABSENCE_OBSERVATIONS = 3
_SINK_PRESENCE_OBSERVATIONS = 3


def _diag(msg: str) -> None:
    print(f"[mip-m2m-provision] {msg}", file=sys.stderr)


def which(binary: str) -> str | None:
    for path in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(path) / binary
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def gh_available() -> bool:
    """Return whether the GitHub CLI is installed and authenticated."""
    if not which("gh"):
        return False
    try:
        subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return False
    return True


def is_environment(environment: str | None) -> bool:
    """Whether ``environment`` is a client's GitHub Environment name."""

    return environment is not None and GH_ENVIRONMENT_RE.fullmatch(environment) is not None


def _validator_refusal(environment: str) -> str | None:
    if environment.endswith(VALIDATE_SUFFIX):
        return (
            f"GitHub credential sink refuses {environment}: a {VALIDATE_SUFFIX} environment is "
            "the validator's federation subject and never holds an M2M secret"
        )
    return None


def environment_sink(repo: str, environment: str) -> str:
    """The only sink coordinate this module writes to: one environment of ``repo``."""

    if GH_REPOSITORY_RE.fullmatch(repo) is None or not is_environment(environment):
        raise ValueError(
            "GitHub credential sink must name a repository and one of its environments "
            "(--gh-repo and --gh-env); repository-level secrets are refused"
        )
    refusal = _validator_refusal(environment)
    if refusal:
        raise ValueError(refusal)
    return f"{repo}{SINK_SEPARATOR}{environment}"


def parse_sink(sink: str) -> tuple[str, str]:
    """``(repository, environment)`` of a sink coordinate; a bare repository is refused."""

    repo, separator, environment = sink.partition(SINK_SEPARATOR)
    if (
        not separator
        or GH_REPOSITORY_RE.fullmatch(repo) is None
        or not is_environment(environment)
    ):
        raise ValueError(
            "GitHub credential sink coordinates are invalid: an environment is required "
            "and repository-level secrets are refused"
        )
    refusal = _validator_refusal(environment)
    if refusal:
        raise ValueError(refusal)
    return repo, environment


class _AmbientReader:
    """Read-only gh calls for one repository, under the operator's own gh login."""

    def __init__(self, repo: str) -> None:
        self.repo = repo

    def repo_path(self, *parts: str) -> str:
        return "/".join(("repos", self.repo, *parts))

    def api(self, method: str, path: str) -> Any:
        if method != "GET":
            raise ValueError("the M2M sink's protection check is read-only")
        return self._json(["api", "--method", "GET", path])

    def variables(self, environment: str) -> Mapping[str, str]:
        listing = self._json(["variable", "list", "--env", environment, "--json", "name,value"])
        found: dict[str, str] = {}
        for item in listing if isinstance(listing, list) else [None]:
            name = item.get("name") if isinstance(item, dict) else None
            value = item.get("value") if isinstance(item, dict) else None
            if not isinstance(name, str) or not isinstance(value, str):
                raise ValueError("malformed variable listing")
            found[name] = value
        return found

    def _json(self, args: list[str]) -> Any:
        result = subprocess.run(
            ["gh", *args],
            capture_output=True,
            check=True,
            text=True,
            timeout=30,
            env={**os.environ, "GH_REPO": self.repo},
        )
        return json.loads(result.stdout) if result.stdout.strip() else None


def environment_refusals(repo: str, environment: str) -> list[str]:
    """Why ``environment`` of ``repo`` may not receive M2M secrets; empty when it may."""

    from tools.clients.errors import BootstrapRefusal
    from tools.clients.github_protections import environments, protection_refusals, review_mode

    refusal = _validator_refusal(environment)
    if refusal:
        return [refusal]
    reader = _AmbientReader(repo)
    try:
        user = reader.api("GET", "user")
        if not isinstance(user, dict) or not isinstance(user.get("id"), int):
            return ["github: the signed-in gh user could not be read"]
        listing = environments(reader)
        if environment not in listing:
            return [
                f"github: the {environment} environment does not exist; run "
                "python -m tools.clients.github_environment --apply first"
            ]
        mode = review_mode(reader.variables(environment).get("MIP_REVIEW_MODE"))
        return protection_refusals(reader, environment, mode, int(user["id"]), listing=listing)
    except BootstrapRefusal as refused:
        return list(refused.lines)
    except (OSError, ValueError, subprocess.SubprocessError):
        return [f"github: the protections of {environment} could not be read; nothing was minted"]


def require_mint_sink(
    repo: str | None, environment: str | None, *, available: Callable[[], bool] = gh_available
) -> None:
    """Before any mint or SDK mutation: gh is signed in, and ``environment`` may hold the secrets."""

    if not repo or not environment or not is_environment(environment):
        raise SystemExit("Secret minting requires --gh-repo and --gh-env <client>")
    refusal = _validator_refusal(environment)
    if refusal:
        raise SystemExit(refusal)
    if not available():
        raise SystemExit(
            "Secret minting requires an installed, authenticated gh CLI; "
            "run `gh auth login` before retrying."
        )
    lines = environment_refusals(repo, environment)
    if lines:
        raise SystemExit("Secret minting refused before any mutation: " + "; ".join(lines))


def _gh(
    sink: str, args: list[str], *, stdin: bytes | None = None, text: bool = False
) -> subprocess.CompletedProcess[Any]:
    """Run ``gh <args> --env <environment>`` against the sink's repository via ``GH_REPO``."""

    repo, environment = parse_sink(sink)
    if any(arg in _REPOSITORY_FLAGS or arg.startswith("--repo=") for arg in args):
        raise ValueError("GitHub credential sink refuses --repo; the repository is GH_REPO")
    return subprocess.run(
        ["gh", *args, "--env", environment],
        input=stdin,
        check=True,
        capture_output=True,
        text=text,
        timeout=30,
        env={**os.environ, "GH_REPO": repo},
    )


def set_gh_secret(sink: str, name: str, value: str) -> None:
    """Pipe a secret to one GitHub Environment via stdin without exposing it in argv."""
    if not GH_SECRET_NAME_RE.fullmatch(name):
        raise SystemExit(
            f"Invalid GitHub Actions secret name {name!r}; expected uppercase "
            "letters, digits, and underscores."
        )
    try:
        repo, environment = parse_sink(sink)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    _diag(f"uploading gh secret {name!r} to environment {environment!r} of {repo}")
    try:
        _gh(sink, ["secret", "set", name], stdin=value.encode("utf-8"))
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
        raise SystemExit(
            f"gh secret set {name} failed (exit={exc.returncode}): {stderr[:400]}"
        ) from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SystemExit(f"gh secret set {name} raised {type(exc).__name__}: {exc}") from exc


def _secret_names(sink: str) -> frozenset[str]:
    try:
        response = _gh(sink, ["secret", "list", "--json", "name"], text=True)
        payload = json.loads(response.stdout)
    except (
        json.JSONDecodeError,
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as exc:
        raise RuntimeError(
            "GitHub credential sink inventory is unavailable"
        ) from exc
    if not isinstance(payload, list) or len(payload) > 1000:
        raise RuntimeError("GitHub credential sink inventory is malformed")
    names: list[str] = []
    for item in payload:
        name = item.get("name") if isinstance(item, dict) else None
        if (
            not isinstance(name, str)
            or GH_SECRET_NAME_RE.fullmatch(name) is None
        ):
            raise RuntimeError("GitHub credential sink inventory is malformed")
        names.append(name)
    if len(names) != len(set(names)):
        raise RuntimeError("GitHub credential sink inventory is duplicated")
    return frozenset(names)


def _checked(sink: str, names: frozenset[str]) -> None:
    parse_sink(sink)
    if not names or any(GH_SECRET_NAME_RE.fullmatch(name) is None for name in names):
        raise ValueError("GitHub credential sink coordinates are invalid")


def invalidate_gh_secrets(
    sink: str,
    names: frozenset[str],
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Delete only an armed sink's exact names and prove repeated absence."""

    _checked(sink, names)
    existing = _secret_names(sink)
    for name in sorted(names.intersection(existing)):
        try:
            _gh(sink, ["secret", "delete", name])
        except (
            OSError,
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
        ) as exc:
            raise RuntimeError(
                "GitHub credential sink deletion is unproven"
            ) from exc
    for observation in range(_SINK_ABSENCE_OBSERVATIONS):
        if names.intersection(_secret_names(sink)):
            raise RuntimeError(
                "GitHub credential sink still contains an armed secret"
            )
        if observation + 1 < _SINK_ABSENCE_OBSERVATIONS:
            sleep(1.0)


def confirm_gh_secrets(
    sink: str,
    names: frozenset[str],
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Prove every armed GitHub secret name is repeatedly visible."""

    _checked(sink, names)
    for observation in range(_SINK_PRESENCE_OBSERVATIONS):
        if not names.issubset(_secret_names(sink)):
            raise RuntimeError(
                "GitHub credential sink acknowledgement is incomplete"
            )
        if observation + 1 < _SINK_PRESENCE_OBSERVATIONS:
            sleep(1.0)
