import json
import re
from pathlib import Path

from tests.fixtures.deploy_script import deploy_entrypoint_text

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
SUPPLY_CHAIN_AUDIT = ROOT / "docs" / "audits" / "supply-chain-audit.md"

# `npm --prefix frontend audit --audit-level=<level>`, possibly wrapped across
# lines in the doc's prose.
_NPM_AUDIT_LEVEL = re.compile(r"npm\s+--prefix\s+frontend\s+audit\s+--audit-level=(\w+)")


def _package_lock() -> dict:
    return json.loads((FRONTEND / "package-lock.json").read_text(encoding="utf-8"))


def test_frontend_production_dependencies_have_no_commercial_license_blockers() -> None:
    lock = _package_lock()
    blockers: list[str] = []
    blocked_terms = (
        "agpl",
        "gpl",
        "lgpl",
        "cc-by-nc",
        "noncommercial",
        "commons clause",
    )
    for package_path, metadata in lock.get("packages", {}).items():
        if not package_path or metadata.get("dev") is True:
            continue
        license_text = str(metadata.get("license") or "").lower()
        if any(term in license_text for term in blocked_terms):
            blockers.append(
                f"{package_path}: {metadata.get('version', '<unknown>')} {metadata.get('license')}"
            )

    assert blockers == []


def test_svg_maps_noncommercial_package_is_not_in_the_frontend_contract() -> None:
    retired_map_package = "@svg-maps" + "/usa"
    package_json = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))
    all_deps = {
        **package_json.get("dependencies", {}),
        **package_json.get("devDependencies", {}),
    }
    lock = _package_lock()

    assert retired_map_package not in all_deps
    assert all(retired_map_package not in package_path for package_path in lock["packages"])


def test_third_party_license_notice_covers_weak_copyleft_and_map_data() -> None:
    notice = (ROOT / "docs" / "THIRD_PARTY_LICENSES.md").read_text(encoding="utf-8")

    for required in (
        "boto3",
        "botocore",
        "Apache-2.0",
        "psycopg",
        "LGPL-3.0-only",
        "pg8000",
        "BSD-3-Clause",
        "scramp",
        "MIT-0",
        "asn1crypto",
        "@axe-core/playwright",
        "MPL-2.0",
        "hypothesis",
        "us-atlas",
        "ISC",
        "topojson-client",
        "@fontsource-variable/geist",
        "@fontsource-variable/geist-mono",
        "OFL-1.1",
    ):
        assert required in notice


def test_python_requirements_use_real_transitive_lockfile() -> None:
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    requirements_in = (ROOT / "requirements.in").read_text(encoding="utf-8")
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")

    assert "-c uv.lock" in requirements
    assert "-r requirements.in" in requirements
    assert "Placeholder lockfile" not in lock
    assert "-r requirements.txt" not in lock
    assert "-r requirements.in" in lock
    assert "uvicorn[standard]==0.47.0" in requirements_in
    assert "databricks-sql-connector==4.4.0" in requirements_in
    assert "boto3==1.43.50" in requirements_in
    assert "pg8000==1.31.5" in requirements_in
    assert "gitpython>=3.1.59,<4" in requirements_in
    assert "cryptography==50.0.1" in requirements_in
    assert "aiohttp>=3.14.3" in requirements_in
    assert "pyasn1>=0.6.4,<1" in requirements_in
    assert "sqlparse>=0.6.0,<1" in requirements_in
    assert "thrift>=0.24.0,<0.25" in requirements_in
    for required_pin in (
        "boto3==1.43.50",
        "uvicorn==0.47.0",
        "databricks-sql-connector==4.4.0",
        "pyjwt==2.13.0",
        "gitpython==3.1.62",
        "cryptography==50.0.1",
        "mlflow==3.16.0",
        "pyarrow==25.0.0",
        "aiohttp==3.14.3",
        "pyasn1==0.6.4",
        "pg8000==1.31.5",
        "psycopg==3.3.4",
        "opentelemetry-sdk==1.41.1",
        "sqlparse==0.6.0",
        "thrift==0.24.0",
    ):
        assert required_pin in lock

    deploy = deploy_entrypoint_text()
    assert "import boto3, mlflow" in deploy
    assert deploy.index("import boto3, mlflow") < deploy.index(
        "DEPLOY_INVENTORY_PRINCIPAL="
    )


def _npm_audit_gate_levels(text: str) -> set[str]:
    """Every `--audit-level` the text runs as a gate.

    A command whose line says "not a gate" is an advisory read (the doc keeps
    `--audit-level=moderate` as a local read) and is left out.
    """
    levels: set[str] = set()
    for match in _NPM_AUDIT_LEVEL.finditer(text):
        line_end = text.find("\n", match.end())
        rest_of_line = text[match.end() : line_end if line_end != -1 else len(text)]
        if "not a gate" not in rest_of_line:
            levels.add(match.group(1))
    return levels


def test_supply_chain_audit_doc_names_the_ci_npm_audit_threshold() -> None:
    ci = CI_WORKFLOW.read_text(encoding="utf-8")
    doc = SUPPLY_CHAIN_AUDIT.read_text(encoding="utf-8")

    ci_levels = _npm_audit_gate_levels(ci)
    assert ci_levels == {"high"}, f"ci.yml npm audit gate levels changed: {ci_levels}"
    assert _npm_audit_gate_levels(doc) == ci_levels, (
        "docs/audits/supply-chain-audit.md must name the npm audit threshold ci.yml "
        "runs as its gate (mark any other level 'not a gate')"
    )


def test_supply_chain_audit_doc_carries_no_unconditional_zero_claim() -> None:
    doc = SUPPLY_CHAIN_AUDIT.read_text(encoding="utf-8")

    assert re.search(r"reports\s+zero\s+known\s+vulnerabilities", doc) is None
    # The replacement is a dated result for each ecosystem.
    assert re.search(r"Frontend `npm audit`, \d{4}-\d{2}-\d{2}", doc)
    assert re.search(r"Backend `pip-audit`, \d{4}-\d{2}-\d{2}", doc)


def test_every_ci_audit_ignore_is_named_in_the_supply_chain_audit_doc() -> None:
    ci = CI_WORKFLOW.read_text(encoding="utf-8")
    doc = SUPPLY_CHAIN_AUDIT.read_text(encoding="utf-8")

    ignored = re.findall(r"--ignore-vuln\s+([A-Za-z0-9-]+)", ci)
    assert ignored, "expected ci.yml's pip-audit step to carry its reviewed ignore"
    for advisory in ignored:
        assert advisory in doc, f"{advisory} is ignored in ci.yml but not named in the doc"
    for review_date in re.findall(r"Review date: (\d{4}-\d{2}-\d{2})", ci):
        assert review_date in doc, f"ci.yml review date {review_date} is not in the doc"
