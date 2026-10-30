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

Values reach gh through stdin only, never argv.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

GH_SECRET_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
GH_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
# A client's GitHub Environment: the MIP_CLIENT shape (tools/clients/schema.py).
GH_ENVIRONMENT_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}$")
SINK_SEPARATOR = ":environment:"
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


def environment_sink(repo: str, environment: str) -> str:
    """The only sink coordinate this module writes to: one environment of ``repo``."""

    if GH_REPOSITORY_RE.fullmatch(repo) is None or not is_environment(environment):
        raise ValueError(
            "GitHub credential sink must name a repository and one of its environments "
            "(--gh-repo and --gh-env); repository-level secrets are refused"
        )
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
    return repo, environment


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
