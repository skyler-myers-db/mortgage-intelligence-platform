"""Documentation contract tests for operator-facing Module 0 docs."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]

CURRENT_OPERATOR_DOCS = [
    ROOT / "README.md",
    ROOT / "SECURITY.md",
    ROOT / "CONTRIBUTING.md",
    ROOT / "CLAUDE.md",
    ROOT / "docs" / "se-onboarding.md",
    ROOT / "docs" / "runbook.md",
    ROOT / "docs" / "runbook-multi-catalog.md",
    ROOT / "docs" / "disaster-recovery.md",
    ROOT / "docs" / "deployment.md",
    ROOT / "docs" / "load-baseline.md",
    ROOT / "docs" / "validation" / "load-baseline.md",
    ROOT / "docs" / "module0-talk-track.md",
    ROOT / "docs" / "observability.md",
    ROOT / "docs" / "credential-kill-drill.md",
    ROOT / "docs" / "dashboards.md",
    ROOT / "docs" / "data-contract.md",
    ROOT / "docs" / "implementation-plan.md",
    ROOT / "docs" / "testing.md",
    ROOT / "docs" / "module0-rehearsal-checklist.md",
]

UNVERSIONED_API_RE = re.compile(
    r"(?<!v1)/api/"
    r"(?:health|admin/health|admin/|data-estate|config/options|leads|borrowers|"
    r"segments|portfolio|outreach|genie|audit|sales|geo|telemetry)"
)
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_security_policy_is_substantive_and_actionable() -> None:
    text = _read(ROOT / "SECURITY.md")

    assert len(text.splitlines()) >= 50
    for required in (
        "security@entrada.ai",
        "Acknowledgment within 1 business day",
        "Initial triage response within 5 business days",
        "/api/v1/*",
        "HMAC",
        "append-only",
        "MIP_EXPOSE_OPENAPI",
        "docs/security-and-compliance.md",
        "docs/disaster-recovery.md",
    ):
        assert required in text


def test_contributing_policy_documents_regression_gates() -> None:
    text = _read(ROOT / "CONTRIBUTING.md")

    assert len(text.splitlines()) >= 120
    for required in (
        "conventional commit",
        "CHANGELOG.md",
        "test_architecture_boundaries.py",
        "test_supply_chain_licenses.py",
        "test_openapi_contract.py",
        "test_load_test_contract.py",
        "test_documentation_contract.py",
        "ROUTE_TEST_MANIFEST",
        "openapi_baseline.json",
        "tools/load_test/baseline.json",
    ):
        assert required in text


def test_readme_surfaces_current_operator_entrypoints() -> None:
    text = _read(ROOT / "README.md")

    for required in (
        "/api/v1/health",
        "X-API-Version: v1",
        "MIP_LENDER_NAME",
        "MIP_TENANT_ID",
        "docs/se-onboarding.md",
        "docs/disaster-recovery.md",
        "docs/load-baseline.md",
        "docs/security-and-compliance.md",
        "CONTRIBUTING.md",
        "CHANGELOG.md",
    ):
        assert required in text


def test_current_operator_docs_use_canonical_api_v1_paths() -> None:
    violations: list[str] = []
    for path in CURRENT_OPERATOR_DOCS:
        text = _read(path)
        for index, line in enumerate(text.splitlines(), start=1):
            if UNVERSIONED_API_RE.search(line):
                violations.append(f"{path.relative_to(ROOT)}:{index}: {line.strip()}")

    assert violations == []


def test_load_baseline_has_one_canonical_operator_doc() -> None:
    validation = _read(ROOT / "docs" / "validation" / "load-baseline.md")
    canonical = _read(ROOT / "docs" / "load-baseline.md")

    assert "The canonical load-baseline operator document is" in validation
    assert "| `GET /api/v1/health`" in canonical
    assert "| `GET /api/v1/health`" not in validation


def test_live_smoke_script_uses_canonical_api_v1_by_default() -> None:
    source = _read(ROOT / "scripts" / "smoke_live.sh")
    deploy_source = _read(ROOT / "scripts" / "deploy.sh")

    assert 'API_PREFIX="${MIP_API_PREFIX:-/api/v1}"' in source
    assert "$API_PREFIX/health" in source
    assert 'EXPECT_GIT_SHA="${MIP_EXPECT_GIT_SHA:-}"' in source
    assert 'DEPLOYED_GIT_SHA=$(echo "$HEALTH" | jq -r' in source
    assert 'export MIP_EXPECT_GIT_SHA="$APP_GIT_SHA"' in deploy_source
    for deprecated in (
        '"/api/health"',
        '"/api/leads',
        '"/api/borrowers',
        '"/api/outreach',
        '"/api/genie',
        '"/api/admin',
    ):
        assert deprecated not in source


def test_backend_modules_have_module_docstrings() -> None:
    missing: list[str] = []
    for path in sorted((ROOT / "backend").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        module = ast.parse(_read(path), filename=str(path))
        if ast.get_docstring(module) is None:
            missing.append(str(path.relative_to(ROOT)))

    assert missing == []


def test_current_operator_docs_have_no_broken_local_file_links() -> None:
    broken: list[str] = []
    for path in CURRENT_OPERATOR_DOCS:
        for match in MARKDOWN_LINK_RE.finditer(_read(path)):
            target = match.group(1).strip()
            if (
                not target
                or target.startswith(("#", "http://", "https://", "mailto:"))
                or target.startswith("computer://")
            ):
                continue
            local_target = unquote(target.split("#", 1)[0])
            if not local_target:
                continue
            if not (path.parent / local_target).resolve().exists():
                broken.append(f"{path.relative_to(ROOT)} -> {target}")

    assert broken == []
