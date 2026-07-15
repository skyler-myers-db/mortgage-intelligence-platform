"""GitHub CLI secret sink for M2M OAuth provisioning."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

GH_SECRET_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")


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
