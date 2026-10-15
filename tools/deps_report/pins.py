"""Literal tool pins in .github/workflows/*.yml and their latest GitHub release.

Collected: every ``GITLEAKS_VERSION``, ``SHELLCHECK_VERSION``, the bandit and
pip-audit pip pins, every ``mcr.microsoft.com/playwright:vX`` image, the
postgres service image (digest-pinned: shown as pinned, never looked up) and
every ``uses: owner/action@ref`` (a SHA pin reads its ``# vX`` comment). A tool
pinned to two different literals is the gate unless an unexpired
``tool-literal`` hold in tools/dependency_holds.json names those literals.
The Playwright image against the @playwright/test pin is gated separately by
tests/unit/test_ci_frontend_gates.py; here it is only a drift row.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import date
from pathlib import Path

from tools.deps_report import REPO_ROOT, Signal, SourceError, classify_gap, repo_path, version_tuple

WORKFLOWS = REPO_ROOT / ".github" / "workflows"
Releases = Callable[[str], dict[str, object]]
_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("gitleaks", "gitleaks/gitleaks", re.compile(r'GITLEAKS_VERSION="v?([0-9][^"]*)"')),
    ("shellcheck", "koalaman/shellcheck", re.compile(r"SHELLCHECK_VERSION:\s*'v?([0-9][^']*)'")),
    ("bandit", "PyCQA/bandit", re.compile(r"'bandit(?:\[[a-z]+\])?==([0-9][^']*)'")),
    ("pip-audit", "pypa/pip-audit", re.compile(r"'pip-audit==([0-9][^']*)'")),
    ("playwright-image", "microsoft/playwright", re.compile(r"mcr\.microsoft\.com/playwright:v([0-9][0-9.]*)-")),
    ("postgres-image", "", re.compile(r"image:\s*postgres:([^\s@]+)@sha256:[0-9a-f]{64}")),
)
_USES = re.compile(r"uses:\s*([A-Za-z0-9-]+/[A-Za-z0-9._-]+)(?:/[^@\s]*)?@([A-Za-z0-9._-]+)(?:\s*#\s*(v?[0-9][0-9.]*))?")


def collect(workflows: Path = WORKFLOWS) -> list[dict[str, str]]:
    literals: list[dict[str, str]] = []
    for path in sorted(workflows.glob("*.yml")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            where = f"{repo_path(path)}:{number}"
            for tool, repo, pattern in _PATTERNS:
                for match in pattern.finditer(line):
                    literals.append({"tool": tool, "repo": repo, "literal": match.group(1), "where": where, "ref": ""})
            for match in _USES.finditer(line):
                ref = match.group(2)
                version = match.group(3) or ref
                literals.append({"tool": match.group(1), "repo": match.group(1), "literal": version, "where": where, "ref": f"{match.group(1)}@{ref}"})
    return literals


def github_release(repo: str) -> dict[str, object]:
    """GET /repos/<repo>/releases/latest, with GITHUB_TOKEN or GH_TOKEN when set."""

    headers = {"Accept": "application/vnd.github+json", "User-Agent": "mip-deps-report", "X-GitHub-Api-Version": "2022-11-28"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases/latest", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:  # the repository publishes tags only (databricks/setup-cli)
            return {"tag_name": ""}
        raise SourceError("github releases") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SourceError("github releases") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("tag_name"), str):
        raise SourceError("github releases")
    return payload


def _gap(literal: str, latest: str) -> str:
    if re.fullmatch(r"v?\d+", literal):  # a floating major tag tracks its major
        have, want = version_tuple(literal), version_tuple(latest)
        return "current" if have and want and want[0] <= have[0] else "major"
    return classify_gap(literal, latest)


def _status(repo: str, tag: object) -> str:
    if not repo:
        return "pinned (digest)"
    if tag is None:
        return "unavailable"
    return "no release" if not tag else ""


def pin_rows(literals: list[dict[str, str]], releases: Releases) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    latest: dict[str, dict[str, object]] = {}
    for tool in sorted({item["tool"] for item in literals}):
        group = [item for item in literals if item["tool"] == tool]
        repo = group[0]["repo"]
        if repo and repo not in latest:
            try:
                latest[repo] = releases(repo)
            except SourceError:
                latest[repo] = {"tag_name": None}
        tag = str(latest[repo]["tag_name"] or "") if repo else ""
        status = _status(repo, latest.get(repo, {}).get("tag_name"))
        for value in sorted({item["literal"] for item in group}):
            sites = [item["where"] for item in group if item["literal"] == value]
            rows.append(
                {
                    "tool": tool,
                    "literal": value,
                    "ref": next((item["ref"] for item in group if item["literal"] == value), ""),
                    "where": ", ".join(sites),
                    "latest": tag.removeprefix("v") if tag else "",
                    "status": _gap(value, tag) if tag else status,
                }
            )
    return rows


def _held_literals(hold: dict[str, object]) -> set[str]:
    held = hold.get("literals")
    return {str(literal) for literal in held} if isinstance(held, list) else set()


def mismatch_signals(literals: list[dict[str, str]], holds: list[dict[str, object]], today: date) -> list[Signal]:
    signals: list[Signal] = []
    for tool in sorted({item["tool"] for item in literals}):
        values = sorted({item["literal"] for item in literals if item["tool"] == tool})
        if len(values) < 2:
            continue
        covered = any(
            hold["ecosystem"] == "tool-literal"
            and hold["package"] == tool
            and set(values) <= _held_literals(hold)
            and date.fromisoformat(str(hold["review_by"])) >= today
            for hold in holds
        )
        if not covered:
            signals.append(Signal("literal_mismatch", tool, " ".join(values)))
    return signals
