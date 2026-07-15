"""Side-effect-free configuration discovery for M2M OAuth provisioning."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any


def load_app_name_from_bundle(path: Path) -> str:
    """Best-effort parse of the deployed App name from ``databricks.yml``."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return "mip-app"
    match = re.search(
        r"^\s+apps:\s*\n(?:\s+#[^\n]*\n)*\s+\w+:\s*\n\s+name:\s*([A-Za-z0-9_-]+)",
        text,
        re.MULTILINE,
    )
    return match.group(1) if match else "mip-app"


def infer_gh_repo(
    repo_root: Path,
    *,
    runner: Callable[..., Any] = subprocess.run,
) -> str | None:
    """Infer ``owner/repo`` from a GitHub origin without truncating dotted names."""
    try:
        out = runner(
            ["git", "remote", "get-url", "origin"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    repo_url = r"github\.com[:/](?P<owner>[^/\s]+)/(?P<repo>[^/\s]+?)(?:\.git)?(?:/|\s|$)"
    match = re.search(repo_url, out)
    if not match:
        return None
    return f"{match.group('owner')}/{match.group('repo')}"
