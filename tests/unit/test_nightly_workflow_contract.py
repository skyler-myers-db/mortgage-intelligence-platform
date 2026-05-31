"""Contracts for the live-UC validation workflow.

Live validation is a release-readiness gate over Databricks assets, not a daily
meter burn. It must be manual-only, and when it runs it must refresh the
governed snapshot before asserting raw-share/gold parity; otherwise weekly
upstream FRED changes can make gold look wrong until a human manually reruns
the scoring job.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
NIGHTLY = REPO / ".github" / "workflows" / "nightly.yml"


def test_live_validation_is_manual_only() -> None:
    text = NIGHTLY.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in text
    assert "\n  schedule:" not in text
    assert "cron:" not in text


def test_live_validation_refreshes_live_snapshot_before_live_parity() -> None:
    text = NIGHTLY.read_text(encoding="utf-8")

    validate_pos = text.index("databricks bundle validate -t dev --profile DEFAULT")
    fred_pos = text.index("databricks bundle run mip_fred_rates_ingest -t dev --profile DEFAULT")
    silver_pos = text.index("databricks bundle run mip_refresh_silver -t dev --profile DEFAULT")
    gold_pos = text.index("databricks bundle run mip_refresh_scores -t dev --profile DEFAULT")
    parity_pos = text.index("pytest -q tests/integration/test_sql_python_parity.py")
    segment_pos = text.index("pytest tests/integration/test_segment_count_parity.py -q --tb=short")
    source_pos = text.index("pytest tests/integration/test_source_readiness_live.py -q --tb=short")

    assert validate_pos < fred_pos < silver_pos < gold_pos < parity_pos < segment_pos < source_pos


def test_live_validation_refresh_steps_use_real_dev_bundle_profile() -> None:
    text = NIGHTLY.read_text(encoding="utf-8")

    for step_name in (
        "Refresh live FRED market rates before validation",
        "Refresh live Cotality silver features before validation",
        "Refresh live gold scoring snapshot before validation",
    ):
        step_pos = text.index(f"- name: {step_name}")
        next_step_pos = text.find("\n      - name:", step_pos + 1)
        block = text[step_pos:] if next_step_pos == -1 else text[step_pos:next_step_pos]

        assert "DATABRICKS_AUTH_TYPE: pat" in block
        assert "BUNDLE_VAR_sql_warehouse_id: ${{ secrets.DATABRICKS_WAREHOUSE_ID }}" in block
        assert "BUNDLE_VAR_genie_space_id: ${{ secrets.GENIE_SPACE_ID }}" in block
        assert "-t dev --profile DEFAULT" in block


def test_live_validation_renders_dev_demo_feeds_for_bundle_validation() -> None:
    text = NIGHTLY.read_text(encoding="utf-8")

    prepare_pos = text.index("- name: Prepare bundle sync inputs")
    export_pos = text.index("- name: Export live Databricks test env")
    block = text[prepare_pos:export_pos]

    assert "MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS=1" in block
    assert 'python tools/render_sql.py --catalog "${MIP_DEFAULT_CATALOG:-mip}"' in block
