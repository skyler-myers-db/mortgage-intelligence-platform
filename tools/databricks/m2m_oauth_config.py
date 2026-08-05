"""Side-effect-free configuration discovery for M2M OAuth provisioning."""

from __future__ import annotations

import os
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


def load_deployment_app_name(
    path: Path,
    *,
    env: Any = os.environ,
) -> str:
    """Prefer the reviewed deployment namespace over the bundle default."""

    configured = str(env.get("MIP_APP_NAME") or env.get("BUNDLE_VAR_app_name") or "").strip()
    return configured or load_app_name_from_bundle(path)


def resolve_live_app_url(client: Any, *, app_name: str) -> str:
    """Resolve the exact workspace-local App URL before identity mutation."""

    from tools.databricks.m2m_access_policy import wrap_admin_error

    try:
        app = client.apps.get(app_name)
    except Exception as exc:  # noqa: BLE001
        raise wrap_admin_error(exc, step=f"resolve URL for App {app_name!r}") from exc
    value = app.get("url") if isinstance(app, dict) else getattr(app, "url", None)
    app_url = str(value or "").strip()
    if not app_url.startswith("https://") or any(char.isspace() for char in app_url):
        raise SystemExit(
            f"App {app_name!r} returned no valid HTTPS URL; wait for App provisioning "
            "to finish or pass the reviewed --app-url explicitly"
        )
    return app_url


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
    repo_url = re.compile(
        r"^(?:"
        r"(?:https?|ssh|git)://(?:[^/@\s]+@)?github\.com/"
        r"|(?:[^@/\s]+@)?github\.com:"
        r")"
        r"(?P<owner>[A-Za-z0-9_.-]+)/"
        r"(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$",
        re.IGNORECASE,
    )
    match = repo_url.fullmatch(out)
    if not match:
        return None
    return f"{match.group('owner')}/{match.group('repo')}"
