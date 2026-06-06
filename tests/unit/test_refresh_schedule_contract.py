"""Cost-control contracts for MIP refresh schedules."""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_mip_refresh_schedules_ship_paused_in_active_bundle() -> None:
    bundle = _load_yaml(ROOT / "databricks.yml")
    jobs = bundle["resources"]["jobs"]

    for job_key in ("mip_sync_lifecycle_state", "mip_fred_rates_ingest"):
        schedule = jobs[job_key]["schedule"]
        assert schedule["pause_status"] == "PAUSED"


def test_mip_refresh_schedules_ship_paused_in_mirror_resources() -> None:
    resources = _load_yaml(ROOT / "resources" / "jobs.yml")
    jobs = resources["resources"]["jobs"]

    for job_key in ("mip_sync_lifecycle_state", "fred_rates_ingest"):
        schedule = jobs[job_key]["schedule"]
        assert schedule["pause_status"] == "PAUSED"


def test_operator_docs_explain_app_refresh_default_and_paused_schedules() -> None:
    runbook = (ROOT / "docs" / "runbook.md").read_text(encoding="utf-8")
    deployment = (ROOT / "docs" / "deployment.md").read_text(encoding="utf-8")
    onboarding = (ROOT / "docs" / "se-onboarding.md").read_text(encoding="utf-8")
    dashboards = (ROOT / "docs" / "dashboards.md").read_text(encoding="utf-8")

    assert "Data operations" in runbook
    assert "schedules deploy **paused by" in runbook
    assert "default** in dev, prod, and prod_otlp" in runbook
    assert "All MIP refresh schedules deploy paused by default" in deployment
    assert "FRED and lifecycle fallback schedules deploy paused" in onboarding
    assert "ships `PAUSED` in every bundle target" in dashboards
    assert "auto-UNPAUSES" not in dashboards
