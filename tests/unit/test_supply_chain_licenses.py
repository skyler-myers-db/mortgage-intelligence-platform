import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"


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
    assert "databricks-sql-connector==4.2.6" in requirements_in
    assert "boto3==1.43.50" in requirements_in
    assert "pg8000==1.31.5" in requirements_in
    assert "gitpython>=3.1.55,<4" in requirements_in
    assert "pyasn1>=0.6.4,<1" in requirements_in
    for required_pin in (
        "boto3==1.43.50",
        "uvicorn==0.47.0",
        "databricks-sql-connector==4.2.6",
        "pyjwt==2.13.0",
        "gitpython==3.1.56",
        "pyasn1==0.6.4",
        "pg8000==1.31.5",
        "psycopg==3.3.4",
        "opentelemetry-sdk==1.41.1",
    ):
        assert required_pin in lock

    deploy = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")
    assert "import boto3, mlflow" in deploy
    assert deploy.index("import boto3, mlflow") < deploy.index(
        "DEPLOY_INVENTORY_PRINCIPAL="
    )
