"""PyPI drift: the direct pins of requirements.in, at their uv.lock versions.

Only ``name==version`` lines are read from either file; the lock is a
pip-compile constraints file and is never regenerated here (its refresh is
the narrow ``--upgrade-package`` method in docs/audits/supply-chain-audit.md).
Names are PEP 503 normalised. Drift never turns the report red.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from tools.deps_report import REPO_ROOT, SourceError, drift_row, hold_for

REQUIREMENTS_IN = REPO_ROOT / "requirements.in"
UV_LOCK = REPO_ROOT / "uv.lock"
_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?==([^\s;#]+)")
PyPI = Callable[[str], dict[str, object]]


def pep503(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def pins(path: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _PIN.match(line.strip())
        if match:
            found[pep503(match.group(1))] = match.group(2)
    return found


def pypi_json(name: str) -> dict[str, object]:
    """https://pypi.org/pypi/<name>/json (https only, 30 s)."""

    request = urllib.request.Request(f"https://pypi.org/pypi/{name}/json", headers={"User-Agent": "mip-deps-report"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SourceError("pypi") from exc
    if not isinstance(payload, dict):
        raise SourceError("pypi")
    return payload


def package_rows(fetch: PyPI, holds: list[dict[str, object]], requirements: Path = REQUIREMENTS_IN, lock: Path = UV_LOCK) -> list[dict[str, str]]:
    locked, direct = pins(lock), pins(requirements)
    rows: list[dict[str, str]] = []
    for name in sorted(direct):
        latest, published = _latest(fetch(name))
        pinned = locked.get(name) or direct[name]
        rows.append(drift_row(name, pinned, latest, published, hold_for(holds, "pypi", name)))
    return rows


def _latest(payload: dict[str, object]) -> tuple[str, str]:
    """The latest version and the upload date of its first file (``urls``)."""

    info = payload.get("info")
    if not isinstance(info, dict) or not isinstance(info.get("version"), str):
        raise SourceError("pypi")
    latest = info["version"]
    files = payload.get("urls")
    first = files[0] if isinstance(files, list) and files and isinstance(files[0], dict) else {}
    return latest, str(first.get("upload_time_iso_8601") or first.get("upload_time") or "")[:10]
