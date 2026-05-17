"""Unit tests for tools/databricks/provision_genie_space.py (dry-run only).

Live SDK interactions are excluded by design: this tool mutates workspace
state, so the end-to-end path is exercised via ``make provision-genie``
against the DEFAULT profile. Here we verify:

* The curated YAML parses into a ``SpaceSpec`` with all trusted assets
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

from backend.services.genie_trusted_assets import trusted_assets  # noqa: E402
from backend.services.repositories.databricks_genie_trust import (  # noqa: E402
    _TRUSTED_GENIE_ASSETS,
)

EXPECTED_ASSETS = {
    "mip.gold.lead_population",
    "mip.gold.segment_population",
    "mip.gold.lead_scores",
    "mip.gold.borrower_360",
    "mip.gold.borrower_dossier",
    "mip.gold.evidence_events",
    "mip.gold.source_readiness",
    "mip.gold.lockin_cohort",
    "mip.gold.funnel_snapshot_daily",
    "mip.gold.county_rollup",
    "mip.gold.zip_rollup",
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
    assert len(spec.example_question_sqls) >= 5
    assert "measures" in spec.sql_snippets


def test_genie_allowlist_docs_match_provisioned_assets() -> None:
    instructions = (REPO_ROOT / "genie" / "instructions.md").read_text(encoding="utf-8")
    trusted_assets_doc = (REPO_ROOT / "genie" / "trusted_assets.md").read_text(encoding="utf-8")

    for asset in EXPECTED_ASSETS:
        assert asset in instructions
        assert asset in trusted_assets_doc


def test_backend_genie_allowlists_match_provisioned_assets() -> None:
    assert set(trusted_assets()) >= EXPECTED_ASSETS
    assert set(_TRUSTED_GENIE_ASSETS) >= EXPECTED_ASSETS


def test_genie_in_the_money_threshold_matches_module0_contract() -> None:
    space_text = pgs.SPACE_YAML.read_text(encoding="utf-8")
    mirror_text = (REPO_ROOT / "genie" / "instructions.md").read_text(encoding="utf-8")

    threshold_line = re.search(
        r"in-the-money\s+≥\s*(\d+)\s*bps spread and ≥\s*(\d+)% equity",
        space_text,
    )
    assert threshold_line is not None
    assert threshold_line.groups() == ("75", "15")
    assert "in-the-money means ≥ 75 bps rate\n    spread and ≥ 15% equity" in mirror_text
    assert "in-the-money\n      ≥ 50 bps spread" not in space_text


def test_genie_source_gap_questions_are_no_sql_redirects() -> None:
    space_text = pgs.SPACE_YAML.read_text(encoding="utf-8")
    mirror_text = (REPO_ROOT / "genie" / "instructions.md").read_text(encoding="utf-8")

    assert "A. SOURCE-GAP QUESTION" in space_text
    assert "DO NOT generate SQL" in space_text
    assert "I will not count the missing feed as zero demand" in space_text
    assert "Source:\n     mip.gold.source_readiness" in space_text
    assert "This bucket has priority over all other buckets" in space_text
    assert (
        "Which borrowers have both a permit signal and an equity-crossing"
        in space_text
    )
    assert "If bucket A/C/D/E fired" in space_text
    for snippet in (
        "**A. Source-gap question:**",
        "This bucket has priority",
        "Do **not** generate SQL",
        "will not be counted as zero demand",
        "`mip.gold.source_readiness`",
    ):
        assert snippet in mirror_text


def test_strategy_example_derives_offer_mix_from_borrower_360() -> None:
    spec = pgs.SpaceSpec.load(pgs.SPACE_YAML)
    question = (
        "Where should Summit Mortgage spend its next 10000 outreach touches "
        "this week, and why?"
    )
    strategy = next(
        item
        for item in spec.example_question_sqls
        if item.get("question") == question
    )
    sql_items = strategy.get("sql")
    assert isinstance(sql_items, list) and sql_items
    sql = "\n".join(str(part) for part in sql_items)
    sql_nc = re.sub(r"\s+", " ", sql).lower()

    assert "from mip.gold.borrower_360" in sql_nc
    assert "recommended_offer" in sql_nc
    assert "recommended_offer_code" in sql_nc
    assert "leading_recommended_offer" in sql_nc
    assert "case when segment_code" not in sql_nc
    assert "then 'refinance + heloc'" not in sql_nc
    assert "then 'cash-out / dscr review'" not in sql_nc
    assert "then 'retention review'" not in sql_nc


def test_genie_geography_zero_count_is_not_zero_demand() -> None:
    space_text = pgs.SPACE_YAML.read_text(encoding="utf-8")
    mirror_text = (REPO_ROOT / "genie" / "instructions.md").read_text(encoding="utf-8")

    assert "Geography guard inside bucket B" in space_text
    assert "not phrase that as zero borrower demand" in space_text
    assert "not present in the current Cotality data coverage" in space_text
    assert "Atlanta/Georgia" in space_text
    assert "do **not** phrase that as zero borrower demand" in mirror_text
    assert "not present in the current Cotality data coverage" in mirror_text


def test_genie_cross_lender_customer_questions_are_out_of_scope() -> None:
    space_text = pgs.SPACE_YAML.read_text(encoding="utf-8")
    mirror_text = (REPO_ROOT / "genie" / "instructions.md").read_text(encoding="utf-8")

    assert "third-party lender or lead-vendor-owned" in space_text
    assert "configured tenant lender" in space_text
    assert "tenant is Summit\n     Mortgage" in space_text
    assert "LendingTree-sourced borrower" in space_text
    assert "Rocket\n     Mortgage customers" in space_text
    assert "Quicken Loans customers" in space_text
    assert "third-party lender or\n  lead-vendor-owned customers" in mirror_text
    assert "tenant is Summit\n  Mortgage" in mirror_text
    assert "LendingTree-sourced borrower" in mirror_text
    assert "Rocket Mortgage customers" in mirror_text


def test_genie_retention_and_evidence_vocab_instructions_match_gold_contract() -> None:
    space_text = pgs.SPACE_YAML.read_text(encoding="utf-8")
    mirror_text = (REPO_ROOT / "genie" / "instructions.md").read_text(encoding="utf-8")
    trusted_assets_doc = (REPO_ROOT / "genie" / "trusted_assets.md").read_text(encoding="utf-8")
    serialized = json.loads(pgs.SpaceSpec.load(pgs.SPACE_YAML).to_serialized_payload())
    instruction_text = "\n".join(
        part
        for item in serialized["instructions"]["text_instructions"]
        for part in item["content"]
    )

    for text in (space_text, mirror_text, instruction_text):
        assert "recommended_offer_code = 'retention'" in text
        assert "is_current_customer" in text
        assert "is_competitor_lien" in text
        assert "to both be" in text
        assert "mutually exclusive" in text
        assert "signal_type = 'competitor_lien'" in text
        assert "signal_type = 'lien-change'" in text
        assert "signal_type = 'competitor'" in text

    for text in (space_text, trusted_assets_doc, instruction_text):
        assert "competitor_lien" in text
    assert "lien-change" not in trusted_assets_doc
    assert "or lien-change analysis" not in space_text
    assert "events (rate-drop, equity-crossed,\n      lien-change)" not in space_text


def test_serialized_text_instruction_allowlist_names_every_trusted_asset() -> None:
    spec = pgs.SpaceSpec.load(pgs.SPACE_YAML)
    parsed = json.loads(spec.to_serialized_payload())
    instruction_text = "\n".join(
        part
        for item in parsed["instructions"]["text_instructions"]
        for part in item["content"]
    )

    allowlist_section = instruction_text.split("Query ONLY these assets:", 1)[1].split(
        "- `mip.ref.state_footprint`",
        1,
    )[0]
    for asset in EXPECTED_ASSETS:
        assert asset in allowlist_section


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
        if "column_configs" in t:
            column_names = [cfg["column_name"] for cfg in t["column_configs"]]
            assert column_names == sorted(column_names)
            for cfg in t["column_configs"]:
                assert "get_example_values" not in cfg
                assert "build_value_dictionary" not in cfg

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
    examples = parsed["instructions"]["example_question_sqls"]
    assert len(examples) >= 5
    assert [ex["id"] for ex in examples] == sorted(ex["id"] for ex in examples)
    for ex in examples:
        assert HEX32.match(ex["id"])
        assert isinstance(ex["question"], list)
        assert isinstance(ex["sql"], list)
        assert all(str(part).strip() for part in ex["sql"])
    assert "sql_snippets" in parsed["instructions"]
    for group in parsed["instructions"]["sql_snippets"].values():
        if isinstance(group, list):
            ids = [item["id"] for item in group]
            assert ids == sorted(ids)
            assert all(HEX32.match(item["id"]) for item in group)


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
