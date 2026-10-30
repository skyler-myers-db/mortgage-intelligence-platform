"""No workflow mints, rotates or stores a credential (M0-PR3).

deploy-dev.yml's normal-credential repair rotated an OAuth secret before, and
outside, scripts/deploy.sh and wrote it into GitHub with a stored
secrets-write token. It was removed with the CI staging install. A
credential mint is owner work outside CI, so no workflow under .github/ may:

- bind a GitHub token other than the job's own ephemeral ``github.token`` /
  ``secrets.GITHUB_TOKEN`` (which cannot write secrets), whether under an
  ``env:`` or ``with:`` token name or by assigning a GitHub-token variable
  inside a script (``GH_TOKEN=...``, a ``$GITHUB_ENV`` line);
- inline a stored secret into a script (``${{ secrets.X }}`` inside ``run:``
  or github-script's ``script:``), where no binding check sees its name: a
  stored secret reaches a step only through ``env:`` or ``with:``;
- run any ``gh secret`` command, ``gh auth login`` or ``--with-token``, name
  one of GitHub's secrets REST paths (repository, environment, organization,
  Dependabot or Codespaces) or github-script's ``createOrUpdate...Secret``, or
  pass ``--set-gh-secrets``;
- call the minting tools, ``tools.databricks.provision_m2m_oauth`` or its
  GitHub sink ``m2m_oauth_github``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github"
# Environment names the gh CLI, the GitHub API clients and actions read a token from.
_GITHUB_TOKEN_ENV = frozenset({"GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"})
# Action inputs that take a GitHub token (actions/checkout `token`, actions/github-script `github-token`).
_GITHUB_TOKEN_INPUTS = frozenset({"token", "github-token", "github_token"})
_EPHEMERAL = re.compile(r"^\$\{\{\s*(?:github\.token|secrets\.GITHUB_TOKEN)\s*\}\}$")
_MINT_TEXT = re.compile(
    r"\bgh\s+secret\b|--set-gh-secrets\b|\bm2m_oauth_github\b|\bprovision_m2m_oauth\b"
    # GitHub's secrets REST paths, github-script's secret writers and the gh CLI's token login.
    r"|\b(?:actions|dependabot|codespaces|environments/[^/\n]+)/secrets\b|\bcreateOrUpdate\w*Secret\b"
    r"|\bgh\s+auth\s+login\b|--with-token\b"
)
# Step values GitHub runs as code: a shell script, or actions/github-script's JavaScript.
_SCRIPT_KEYS = frozenset({"run", "script"})
_EXPRESSION = re.compile(r"\$\{\{.*?\}\}", re.DOTALL)
_SECRETS_CONTEXT = re.compile(r"\bsecrets\b")
# A GitHub-token variable assigned in a script: `GH_TOKEN=`, `export GITHUB_TOKEN =`, a
# `$GITHUB_ENV` line, `os.environ["GH_TOKEN"] =`; never a `==` comparison.
_TOKEN_ASSIGNMENT = re.compile(r"\b(?:GH|GITHUB)(?:_ENTERPRISE)?_TOKEN\b[\"'\]\s]*=(?!=)")


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


def _scripts(node: Any, where: str = "") -> list[tuple[str, str]]:
    """Every (location, script) a step runs as code."""

    found: list[tuple[str, str]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{where}.{key}" if where else str(key)
            if key in _SCRIPT_KEYS and isinstance(value, str):
                found.append((here, value))
            found.extend(_scripts(value, here))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            found.extend(_scripts(item, f"{where}[{index}]"))
    return found


def credential_mint_findings(text: str, label: str) -> list[str]:
    """Why ``text`` (one workflow file) could mint or store a credential; empty when it cannot."""

    findings = [f"{label}: {match.group(0)!r}" for match in _MINT_TEXT.finditer(text)]
    workflow = yaml.load(text, Loader=yaml.BaseLoader) or {}
    for location, value in _token_bindings(workflow):
        if not _EPHEMERAL.match(value.strip()):
            findings.append(f"{label}: {location} binds a non-ephemeral GitHub token")
    for location, script in _scripts(workflow):
        for expression in _EXPRESSION.findall(script):
            if _SECRETS_CONTEXT.search(expression) and not _EPHEMERAL.match(expression):
                findings.append(f"{label}: {location} inlines a stored secret into a script")
        if _TOKEN_ASSIGNMENT.search(script):
            findings.append(f"{label}: {location} assigns a GitHub token inside a script")
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
    for marker in ("provision_m2m_oauth", "--set-gh-secrets", "gh secret"):
        assert any(marker in finding for finding in findings), marker


@pytest.mark.parametrize(
    ("script", "markers"),
    [
        (
            'run: GH_TOKEN="${{ secrets.SINK_PAT }}" gh api --method PUT '
            "repos/o/r/actions/secrets/DATABRICKS_CLIENT_SECRET -f encrypted_value=x",
            ("inlines a stored secret", "assigns a GitHub token", "actions/secrets"),
        ),
        (
            'run: echo "GH_TOKEN=${{ secrets.SINK_PAT }}" >> "$GITHUB_ENV"\n'
            "      - run: gh api -X PUT repos/o/r/environments/dev/secrets/X",
            ("inlines a stored secret", "assigns a GitHub token", "environments/dev/secrets"),
        ),
        (
            'run: echo "${{ secrets.SINK_PAT }}" | gh auth login --with-token',
            ("inlines a stored secret", "gh auth login", "--with-token"),
        ),
        (
            "env:\n          SINK: ${{ secrets.SINK_PAT }}\n        run: echo \"GH_TOKEN=$SINK\" >> \"$GITHUB_ENV\"",
            ("assigns a GitHub token",),
        ),
        (
            "run: echo '${{ toJSON(secrets) }}' > all.json",
            ("inlines a stored secret",),
        ),
        (
            "uses: actions/github-script@v9\n        with:\n          script: |\n"
            "            await github.rest.actions.createOrUpdateRepoSecret({owner: 'o', repo: 'r', secret_name: 'X'})",
            ("createOrUpdateRepoSecret",),
        ),
    ],
    ids=["inline-gh-api", "github-env", "auth-login", "env-indirection", "all-secrets", "github-script"],
)
def test_a_token_reaching_a_script_or_a_secrets_api_is_refused(script: str, markers: tuple[str, ...]) -> None:
    """Review of #293: shapes the env:/with: binding scan alone missed, with synthetic secret names."""

    findings = credential_mint_findings(f"jobs:\n  j:\n    steps:\n      - {script}\n", "shape.yml")

    for marker in markers:
        assert any(marker in finding for finding in findings), (marker, findings)


def test_the_script_checks_leave_ordinary_steps_alone() -> None:
    """The allowed side: the job token under env:, a stored non-token secret under env:, $GITHUB_ENV values."""

    ordinary = """
jobs:
  j:
    steps:
      - env:
          GH_TOKEN: ${{ github.token }}
          DATABRICKS_HOST: ${{ secrets.DATABRICKS_HOST }}
        run: |
          gh pr view "$PR" --json state
          echo "MIP_APP_URL=$APP_URL" >> "$GITHUB_ENV"
          [ "$GH_TOKEN" == "" ] && exit 1
          curl -sf -H "Authorization: Bearer ${{ secrets.GITHUB_TOKEN }}" "https://api.github.com/repos/o/r/pulls"
"""

    assert credential_mint_findings(ordinary, "ordinary.yml") == []
