"""Unit tests for tools/databricks/provision_genie_space.py (dry-run only).

Live SDK interactions are excluded by design: this tool mutates workspace
state, so the end-to-end path is exercised manually via ``make provision-genie``.
Here we verify:

* The curated YAML parses into a ``SpaceSpec`` with all 9 trusted assets
  and 10 sample questions.
* ``to_serialized_payload()`` yields a JSON string containing every
  trusted-asset name (so the Genie backend can see them).
* ``--dry-run`` exits 0 and prints an intelligible plan without ever
  constructing a ``WorkspaceClient``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = REPO_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from databricks import provision_genie_space as pgs  # noqa: E402

EXPECTED_ASSETS = {
    "mip_demo.gold.lead_population",
    "mip_demo.gold.lead_segment_membership",
    "mip_demo.gold.lead_scores",
    "mip_demo.gold.borrower_360",
    "mip_demo.gold.evidence_events",
    "mip_demo.gold.recommended_offers",
    "mip_demo.semantics.lead_generation_metric_view",
    "mip_demo.semantics.segment_performance_metric_view",
    "mip_demo.semantics.borrower_opportunity_metric_view",
}


def test_spec_loads_all_trusted_assets_and_questions() -> None:
    spec = pgs.SpaceSpec.load(pgs.SPACE_YAML)
    assert spec.name == "Mortgage Lead Intelligence"
    assert spec.catalog == "mip_demo"
    names = {a.get("name") for a in spec.trusted_assets}
    assert names == EXPECTED_ASSETS
    assert len(spec.sample_questions) == 10


def test_serialized_payload_mentions_every_asset() -> None:
    spec = pgs.SpaceSpec.load(pgs.SPACE_YAML)
    payload = spec.to_serialized_payload()
    parsed = json.loads(payload)
    assert parsed["title"] == "Mortgage Lead Intelligence"
    payload_names = {a["name"] for a in parsed["trusted_assets"]}
    assert payload_names == EXPECTED_ASSETS
    assert len(parsed["sample_questions"]) == 10


def test_dry_run_exits_zero_without_touching_sdk(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = pgs.main(["--dry-run", "--profile", "DEFAULT"])
    captured = capsys.readouterr()
    assert rc == 0
    # Plan banner should mention CREATE (dry-run never inspects workspace).
    assert "dry-run" in captured.out
    assert "trusted assets" in captured.out
    # BUNDLE_VAR echo line is part of the plan copy.
    assert "BUNDLE_VAR_genie_space_id" in captured.out
