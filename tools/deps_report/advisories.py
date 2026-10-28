"""Advisories: npm audit over the lock and pip-audit over uv.lock and requirements-dev.lock.

``npm --prefix frontend audit --package-lock-only --json`` needs no
node_modules; high and critical are the gate (CI's ``--audit-level=high``),
moderate and low are informational. ``pip-audit -r uv.lock -r
requirements-dev.lock --no-deps --disable-pip --strict -f json`` is the
documented lock form (a package both locks pin is audited once); every finding
is marked ignored or unignored against the ``--ignore-vuln`` ids of ci.yml's
``run: pip-audit`` line, and an unignored one is the gate. Only ids,
packages, versions and severities leave this module: never a title, a
description or an advisory URL.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

from tools.deps_report import REPO_ROOT, Signal, SourceError

CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
GATE_SEVERITIES = frozenset({"high", "critical"})
_ADVISORY_URL_ID = re.compile(r"/advisories/(GHSA(?:-[23456789cfghjmpqrvwx]{4}){3})$")
Runner = Callable[[], object]


def ci_ignored_vulns(path: Path = CI_WORKFLOW) -> list[str]:
    """The ``--ignore-vuln`` ids on ci.yml's ``run: pip-audit`` line."""

    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("run: pip-audit"):
            return re.findall(r"--ignore-vuln\s+(\S+)", line)
    raise SourceError("ci.yml pip-audit line")


def _json_command(argv: list[str], source: str) -> object:
    try:
        result = subprocess.run(argv, cwd=REPO_ROOT, capture_output=True, text=True, check=False, timeout=600)
        return json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        raise SourceError(source) from exc


def run_npm_audit() -> object:
    return _json_command(["npm", "--prefix", "frontend", "audit", "--package-lock-only", "--json"], "npm audit")


def run_pip_audit() -> object:
    argv = ["pip-audit", "-r", "uv.lock", "-r", "requirements-dev.lock", "--no-deps", "--disable-pip", "--strict", "-f", "json"]
    return _json_command(argv, "pip-audit")


def npm_findings(payload: object) -> list[dict[str, str]]:
    if not isinstance(payload, dict) or "error" in payload or not isinstance(payload.get("vulnerabilities"), dict):
        raise SourceError("npm audit")
    found: dict[tuple[str, str], dict[str, str]] = {}
    for entry in payload["vulnerabilities"].values():
        for via in entry.get("via", []) if isinstance(entry, dict) else []:
            if not isinstance(via, dict):
                continue  # a transitive pointer; the advisory is listed on its own package
            match = _ADVISORY_URL_ID.search(str(via.get("url", "")))
            advisory = match.group(1) if match else ""
            key = (str(via.get("name", "")), advisory)
            found[key] = {"package": key[0], "id": advisory, "severity": str(via.get("severity", "")).lower()}
    return sorted(found.values(), key=lambda f: (f["package"], f["id"]))


def pip_findings(payload: object, ignored: list[str]) -> list[dict[str, str]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("dependencies"), list):
        raise SourceError("pip-audit")
    findings: list[dict[str, str]] = []
    for dependency in payload["dependencies"]:
        for vuln in dependency.get("vulns", []) if isinstance(dependency, dict) else []:
            ids = {str(vuln.get("id", "")), *(str(alias) for alias in vuln.get("aliases", []))}
            findings.append(
                {
                    "package": str(dependency.get("name", "")),
                    "version": str(dependency.get("version", "")),
                    "id": str(vuln.get("id", "")),
                    "ignored": "yes" if ids & set(ignored) else "no",
                }
            )
    return sorted(findings, key=lambda f: (f["package"], f["id"]))


def gate_signals(npm: list[dict[str, str]], pip: list[dict[str, str]]) -> list[Signal]:
    signals = [Signal("npm_advisory", f["package"], f["id"]) for f in npm if f["severity"] in GATE_SEVERITIES]
    signals += [Signal("pip_advisory", f["package"], f["id"]) for f in pip if f["ignored"] == "no"]
    return signals
