"""GitHub CLI secret sink for M2M OAuth provisioning."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

GH_SECRET_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
GH_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
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


def set_gh_secret(repo: str, name: str, value: str) -> None:
    """Pipe a secret to GitHub via stdin without exposing it in argv."""
    if not GH_SECRET_NAME_RE.fullmatch(name):
        raise SystemExit(
            f"Invalid GitHub Actions secret name {name!r}; expected uppercase "
            "letters, digits, and underscores."
        )
    _diag(f"uploading gh secret {name!r} to {repo}")
    try:
        subprocess.run(
            ["gh", "secret", "set", name, "--repo", repo],
            input=value.encode("utf-8"),
            check=True,
            capture_output=True,
            timeout=30,
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
        raise SystemExit(
            f"gh secret set {name} failed (exit={exc.returncode}): {stderr[:400]}"
        ) from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SystemExit(f"gh secret set {name} raised {type(exc).__name__}: {exc}") from exc


def _secret_names(repo: str) -> frozenset[str]:
    try:
        response = subprocess.run(
            ["gh", "secret", "list", "--repo", repo, "--json", "name"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
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


def invalidate_gh_secrets(
    repo: str,
    names: frozenset[str],
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Delete only an armed sink's exact names and prove repeated absence."""

    if (
        GH_REPOSITORY_RE.fullmatch(repo) is None
        or not names
        or any(GH_SECRET_NAME_RE.fullmatch(name) is None for name in names)
    ):
        raise ValueError("GitHub credential sink coordinates are invalid")
    existing = _secret_names(repo)
    for name in sorted(names.intersection(existing)):
        try:
            subprocess.run(
                ["gh", "secret", "delete", name, "--repo", repo],
                check=True,
                capture_output=True,
                timeout=30,
            )
        except (
            OSError,
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
        ) as exc:
            raise RuntimeError(
                "GitHub credential sink deletion is unproven"
            ) from exc
    for observation in range(_SINK_ABSENCE_OBSERVATIONS):
        if names.intersection(_secret_names(repo)):
            raise RuntimeError(
                "GitHub credential sink still contains an armed secret"
            )
        if observation + 1 < _SINK_ABSENCE_OBSERVATIONS:
            sleep(1.0)


def confirm_gh_secrets(
    repo: str,
    names: frozenset[str],
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Prove every armed GitHub secret name is repeatedly visible."""

    if (
        GH_REPOSITORY_RE.fullmatch(repo) is None
        or not names
        or any(GH_SECRET_NAME_RE.fullmatch(name) is None for name in names)
    ):
        raise ValueError("GitHub credential sink coordinates are invalid")
    for observation in range(_SINK_PRESENCE_OBSERVATIONS):
        if not names.issubset(_secret_names(repo)):
            raise RuntimeError(
                "GitHub credential sink acknowledgement is incomplete"
            )
        if observation + 1 < _SINK_PRESENCE_OBSERVATIONS:
            sleep(1.0)
