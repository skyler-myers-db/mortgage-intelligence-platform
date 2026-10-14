"""npm drift: every exact pin in frontend/package.json against the registry.

``npm view <pkg> version time --json`` runs with cwd frontend/ and needs no
node_modules. Drift never turns the report red; a package named by a hold in
tools/dependency_holds.json renders as held, never as pending.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path

from tools.deps_report import REPO_ROOT, SourceError, drift_row, hold_for

PACKAGE_JSON = REPO_ROOT / "frontend" / "package.json"
NpmView = Callable[[str], dict[str, object]]


def exact_pins(path: Path = PACKAGE_JSON) -> dict[str, str]:
    package = json.loads(path.read_text(encoding="utf-8"))
    return {**package.get("dependencies", {}), **package.get("devDependencies", {})}


def npm_view(name: str) -> dict[str, object]:
    """The registry's latest version and publish times for one package."""

    try:
        result = subprocess.run(
            ["npm", "view", name, "version", "time", "--json"],
            cwd=REPO_ROOT / "frontend",
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        payload = json.loads(result.stdout) if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        raise SourceError("npm view") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("version"), str):
        raise SourceError("npm view")
    return payload


def package_rows(view: NpmView, holds: list[dict[str, object]], pins: dict[str, str] | None = None) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for name, pinned in sorted((pins if pins is not None else exact_pins()).items()):
        payload = view(name)
        latest = str(payload["version"])
        times = payload.get("time") if isinstance(payload.get("time"), dict) else {}
        published = str(times.get(latest, ""))[:10] if isinstance(times, dict) else ""
        rows.append(drift_row(name, pinned, latest, published, hold_for(holds, "npm", name)))
    return rows

