"""Unit tests for tools/databricks/provision_genie_space.py (dry-run only).

Live SDK interactions are excluded by design: this tool mutates workspace
state, so the end-to-end path is exercised via ``make provision-genie``
against the DEFAULT profile. Here we verify:

* The curated YAML parses into a ``SpaceSpec`` with all 9 trusted assets
  and 10 sample questions.
* ``to_serialized_payload()`` yields a JSON string matching the discovered
  Genie ``serialized_space`` schema:
    - top-level keys: ``version``, ``data_sources``, ``config``, ``instructions``
    - ``data_sources.tables[*].identifier`` covers every trusted-asset name
    - each ``sample_questions[*]`` and ``text_instructions[*]`` entry has a
      lowercase 32-hex ``id``
    - ``description`` / ``question`` / ``content`` values are arrays (the
      server rejects bare strings with "Expected an array").
* ``include_tables=False`` drops only the table bindings (used as a
  fallback when the target catalog is not yet materialized).
* ``--dry-run`` exits 0 without constructing a ``WorkspaceClient``.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = REPO_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from databricks import provision_genie_space as pgs  # noqa: E402

EXPECTED_ASSETS = {
    "mip.gold.lead_population",
    "mip.gold.segment_population",
    "mip.gold.lead_scores",
    "mip.gold.borrower_360",
    "mip.gold.borrower_dossier",
    "mip.gold.evidence_events",
    "mip.gold.lockin_cohort",
    "mip.semantics.lead_generation_metric_view",
    "mip.semantics.segment_performance_metric_view",
    "mip.semantics.borrower_opportunity_metric_view",
}

HEX32 = re.compile(r"^[0-9a-f]{32}$")


def test_spec_loads_all_trusted_assets_and_questions() -> None:
    spec = pgs.SpaceSpec.load(pgs.SPACE_YAML)
    assert spec.name == "Mortgage Lead Intelligence"
    assert spec.catalog == "mip"
    names = {a.get("name") for a in spec.trusted_assets}
    assert names == EXPECTED_ASSETS
    assert len(spec.sample_questions) == 10


def test_serialized_payload_matches_discovered_schema() -> None:
    """Assert structure, not content — matches the live API's expectations.

    Discovered shape (see docs/genie-sdk-notes.md):
      {"version": 2,
       "data_sources": {"tables": [{"identifier": "c.s.t", "description": [".."]}]},
       "config": {"sample_questions": [{"id": "<32hex>", "question": [".."]}]},
       "instructions": {"text_instructions": [{"id": "<32hex>", "content": [".."]}]}}
    """
    spec = pgs.SpaceSpec.load(pgs.SPACE_YAML)
    payload = spec.to_serialized_payload()
    parsed = json.loads(payload)

    assert parsed["version"] == 2

    # data_sources.tables is a list of {identifier, description: [..]} entries.
    assert "data_sources" in parsed
    tables = parsed["data_sources"]["tables"]
    assert isinstance(tables, list)
    identifiers = {t["identifier"] for t in tables}
    assert identifiers == EXPECTED_ASSETS
    for t in tables:
        # The server rejects bare-string descriptions; they must be arrays.
        if "description" in t:
            assert isinstance(t["description"], list)
            assert all(isinstance(x, str) for x in t["description"])

    # config.sample_questions is a list of {id: 32hex, question: [text]} entries.
    assert "config" in parsed
    questions = parsed["config"]["sample_questions"]
    assert isinstance(questions, list)
    assert len(questions) == 10
    question_ids = set()
    for q in questions:
        assert HEX32.match(q["id"]), f"{q['id']!r} is not 32-lowercase-hex"
        question_ids.add(q["id"])
        assert isinstance(q["question"], list)
        assert len(q["question"]) >= 1
        assert all(isinstance(x, str) and x.strip() for x in q["question"])
    # Deterministic ids → uniqueness (and thus idempotent round-trips).
    assert len(question_ids) == 10

    # instructions.text_instructions is a list of {id: 32hex, content: [text]}.
    assert "instructions" in parsed
    instructions = parsed["instructions"]["text_instructions"]
    assert isinstance(instructions, list)
    assert len(instructions) == 1  # YAML has one multiline instruction block
    inst = instructions[0]
    assert HEX32.match(inst["id"])
    assert isinstance(inst["content"], list)
    assert all(isinstance(x, str) for x in inst["content"])


def test_payload_is_idempotent_for_unchanged_spec() -> None:
    """Two builds of the same spec yield byte-identical JSON (so update = no-op)."""
    spec = pgs.SpaceSpec.load(pgs.SPACE_YAML)
    assert spec.to_serialized_payload() == spec.to_serialized_payload()


def test_payload_without_tables_drops_only_the_table_bindings() -> None:
    """include_tables=False is the fallback for a not-yet-materialized catalog."""
    spec = pgs.SpaceSpec.load(pgs.SPACE_YAML)
    payload_empty = json.loads(spec.to_serialized_payload(include_tables=False))
    payload_full = json.loads(spec.to_serialized_payload(include_tables=True))

    assert payload_empty["data_sources"]["tables"] == []
    # Questions and instructions still land so the space is curated on day 1.
    assert payload_empty["config"] == payload_full["config"]
    assert payload_empty["instructions"] == payload_full["instructions"]


def test_dry_run_exits_zero_without_touching_sdk(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = pgs.main(["--dry-run", "--profile", "DEFAULT"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "dry-run" in captured.out
    assert "trusted assets" in captured.out
    assert "BUNDLE_VAR_genie_space_id" in captured.out
