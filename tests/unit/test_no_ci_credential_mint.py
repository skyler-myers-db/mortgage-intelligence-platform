"""No workflow mints, rotates or stores a credential (M0-PR3).

deploy-dev.yml's normal-credential repair rotated an OAuth secret before, and
outside, scripts/deploy.sh and wrote it into GitHub with a stored
secrets-write token. It was removed with the CI staging install. A
credential mint is owner work outside CI, so no workflow under .github/ may:

- bind a GitHub token from a stored secret (only the job's own ephemeral
  ``github.token`` / ``secrets.GITHUB_TOKEN``, which cannot write secrets);
- run ``gh secret set`` or pass ``--set-gh-secrets``;
- call the minting tools, ``tools.databricks.provision_m2m_oauth`` or its
  GitHub sink ``m2m_oauth_github``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github"
# Environment names the gh CLI, the GitHub API clients and actions read a token from.
_GITHUB_TOKEN_ENV = frozenset({"GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"})
# Action inputs that take a GitHub token (actions/checkout `token`, actions/github-script `github-token`).
_GITHUB_TOKEN_INPUTS = frozenset({"token", "github-token", "github_token"})
_EPHEMERAL = re.compile(r"^\$\{\{\s*(?:github\.token|secrets\.GITHUB_TOKEN)\s*\}\}$")
_MINT_TEXT = re.compile(r"\bgh\s+secret\s+set\b|--set-gh-secrets\b|\bm2m_oauth_github\b|\bprovision_m2m_oauth\b")


def _workflow_files() -> list[Path]:
    return sorted(path for path in WORKFLOWS.rglob("*") if path.suffix in {".yml", ".yaml"})


def _token_bindings(node: Any, where: str = "") -> list[tuple[str, str]]:
    """Every (location, value) that hands a GitHub token to a step, a job or an action."""

    found: list[tuple[str, str]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{where}.{key}" if where else str(key)
            if key in {"env", "with"} and isinstance(value, dict):
                names = _GITHUB_TOKEN_ENV if key == "env" else _GITHUB_TOKEN_INPUTS
                found.extend((f"{here}.{name}", str(bound)) for name, bound in value.items() if name in names)
            found.extend(_token_bindings(value, here))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            found.extend(_token_bindings(item, f"{where}[{index}]"))
    return found


def credential_mint_findings(text: str, label: str) -> list[str]:
    """Why ``text`` (one workflow file) could mint or store a credential; empty when it cannot."""

    findings = [f"{label}: {match.group(0)!r}" for match in _MINT_TEXT.finditer(text)]
    for location, value in _token_bindings(yaml.load(text, Loader=yaml.BaseLoader) or {}):
        if not _EPHEMERAL.match(value.strip()):
            findings.append(f"{label}: {location} binds a non-ephemeral GitHub token")
    return findings


def test_the_scan_reads_every_workflow() -> None:
    names = {path.name for path in _workflow_files()}

    assert {"ci.yml", "deploy-dev.yml", "deploy-prod.yml", "deps-report.yml", "nightly.yml"} <= names


def test_no_workflow_mints_or_stores_a_credential() -> None:
    findings = [
        finding
        for path in _workflow_files()
        for finding in credential_mint_findings(path.read_text(encoding="utf-8"), str(path.relative_to(ROOT)))
    ]

    assert findings == []


def test_the_ephemeral_job_token_stays_allowed() -> None:
    """The non-vacuity control for the allowed side: today's GITHUB_TOKEN bindings pass."""

    bindings = [
        value
        for path in _workflow_files()
        for _location, value in _token_bindings(yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader) or {})
    ]

    assert bindings, "deploy-prod.yml and deps-report.yml bind the job's own token"
    assert all(_EPHEMERAL.match(value.strip()) for value in bindings)


def test_the_retired_repair_step_shape_is_refused() -> None:
    """The positive control: the removed step's shape, with a synthetic secret name, is caught three ways."""

    retired = """
jobs:
  deploy:
    steps:
      - name: Repair a credential
        env:
          GH_TOKEN: ${{ secrets.SOME_SECRETS_WRITE_TOKEN }}
        run: |
          python -m tools.databricks.provision_m2m_oauth --rotate --set-gh-secrets
      - uses: actions/github-script@v9
        with:
          github-token: ${{ secrets.ANOTHER_PAT }}
          script: core.notice('x')
      - run: printf '%s' "$VALUE" | gh secret set SOME_NAME
"""
    findings = credential_mint_findings(retired, "retired.yml")

    assert any("GH_TOKEN binds a non-ephemeral GitHub token" in finding for finding in findings)
    assert any("github-token binds a non-ephemeral GitHub token" in finding for finding in findings)
    for marker in ("provision_m2m_oauth", "--set-gh-secrets", "gh secret set"):
        assert any(marker in finding for finding in findings), marker
