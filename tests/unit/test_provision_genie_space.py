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

import importlib.util
import json
import os
import re
import sys
import types
from pathlib import Path

import pytest

from backend.config.settings import settings  # noqa: E402
from backend.services.genie_trusted_assets import trusted_assets  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
_PGS_PATH = REPO_ROOT / "tools" / "databricks" / "provision_genie_space.py"
_MODNAME = "mip_provision_genie_space"
_spec = importlib.util.spec_from_file_location(_MODNAME, _PGS_PATH)
assert _spec is not None and _spec.loader is not None
pgs = importlib.util.module_from_spec(_spec)
sys.modules[_MODNAME] = pgs
_spec.loader.exec_module(pgs)  # type: ignore[union-attr]

EXPECTED_ASSET_PAIRS = (
    ("gold", "lead_population"),
    ("gold", "segment_population"),
    ("gold", "lead_scores"),
    ("gold", "borrower_360"),
    ("gold", "borrower_dossier"),
    ("gold", "evidence_events"),
    ("gold", "source_readiness"),
    ("gold", "lockin_cohort"),
    ("gold", "funnel_snapshot_daily"),
    ("gold", "county_rollup"),
    ("gold", "zip_rollup"),
    ("semantics", "lead_generation_metric_view"),
    ("semantics", "segment_performance_metric_view"),
    ("semantics", "borrower_opportunity_metric_view"),
)

HEX32 = re.compile(r"^[0-9a-f]{32}$")


def _expected_assets(catalog: str = "mip") -> set[str]:
    return {f"{catalog}.{schema}.{table}" for schema, table in EXPECTED_ASSET_PAIRS}


@pytest.fixture(autouse=True)
def _default_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIP_DEFAULT_CATALOG", "mip")
    monkeypatch.setattr(settings, "mip_default_catalog", "mip")


def test_env_local_does_not_override_catalog_routing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("MIP_DEFAULT_CATALOG", raising=False)
    monkeypatch.delenv("DATABRICKS_HOST", raising=False)
    monkeypatch.setattr(pgs, "REPO_ROOT", tmp_path)
    (tmp_path / ".env.local").write_text(
        "MIP_DEFAULT_CATALOG=mip_demo\n"
        "DATABRICKS_HOST=https://dbc.example\n",
        encoding="utf-8",
    )

    pgs._load_env_local()

    assert "MIP_DEFAULT_CATALOG" not in os.environ
    assert os.environ["DATABRICKS_HOST"] == "https://dbc.example"


def test_smoke_test_rejects_prompt_echo(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class _FakeConfig:
        host = "https://dbc.example"

        def authenticate(self) -> dict[str, str]:
            return {"Authorization": "Bearer token"}

    class _FakeWorkspaceClient:
        config = _FakeConfig()

    class _EchoGenieClient:
        def __init__(self, **_: object) -> None:
            pass

        def ask(self, question: str) -> types.SimpleNamespace:
            return types.SimpleNamespace(answer_text=question, sql_query=None)

    import backend.services.genie_client as genie_client_mod

    monkeypatch.setattr(genie_client_mod, "GenieClient", _EchoGenieClient)

    assert pgs._run_smoke_test(_FakeWorkspaceClient(), "space-id") is False
    err = capsys.readouterr().err
    assert "prompt echo" in err


def test_spec_loads_all_trusted_assets_and_questions() -> None:
    spec = pgs.SpaceSpec.load(pgs.SPACE_YAML)
    assert spec.name == "Mortgage Lead Intelligence"
    assert spec.catalog == "mip"
    names = {a.get("name") for a in spec.trusted_assets}
    assert names == _expected_assets()
    assert len(spec.sample_questions) == 10
    assert len(spec.example_question_sqls) >= 5
    assert "measures" in spec.sql_snippets


def test_genie_allowlist_docs_match_provisioned_assets() -> None:
    instructions = (REPO_ROOT / "genie" / "instructions.md").read_text(encoding="utf-8")
    trusted_assets_doc = (REPO_ROOT / "genie" / "trusted_assets.md").read_text(encoding="utf-8")

    for asset in _expected_assets():
        assert asset in instructions
        assert asset in trusted_assets_doc


def test_genie_asset_descriptions_match_current_metric_contracts() -> None:
    spec = pgs.SpaceSpec.load(pgs.SPACE_YAML)
    by_name = {str(asset["name"]): str(asset["description"]) for asset in spec.trusted_assets}
    trusted_assets_doc = (REPO_ROOT / "genie" / "trusted_assets.md").read_text(encoding="utf-8")
    instructions = (REPO_ROOT / "genie" / "instructions.md").read_text(encoding="utf-8")
    bootstrap_ddl = (REPO_ROOT / "sql" / "ddl" / "005_semantics_views.sql").read_text(
        encoding="utf-8"
    )

    assert "Every in-scope analytic answer Genie returns" in trusted_assets_doc
    assert "Every answer Genie\nreturns must cite" not in trusted_assets_doc
    assert "raw Cotality shares land in `mip.raw.*`" not in trusted_assets_doc

    lead_description = by_name["mip.gold.lead_population"]
    assert "score-qualified ranked Lead Queue borrower" in lead_description
    assert "action-ready only when marketing eligibility" in lead_description
    assert "after ranking and contactability filters" not in trusted_assets_doc
    assert "score-qualified ranked Lead Queue borrower" in instructions

    segment_description = by_name["mip.semantics.segment_performance_metric_view"]
    assert "count, mean opportunity score, approval rate, outreach rate" in segment_description
    assert "does not expose borrower economics columns" in segment_description
    assert "mean lead score, mean rate spread" not in segment_description
    assert "mean score, rate spread, equity" not in trusted_assets_doc

    opportunity_description = by_name["mip.semantics.borrower_opportunity_metric_view"]
    assert "Borrower-grain opportunity surface" in opportunity_description
    assert "plain row columns" in opportunity_description
    assert "COUNT(DISTINCT clip)" in opportunity_description
    assert "state × product × trigger" not in trusted_assets_doc
    assert "one row per CLIP / borrower record" in trusted_assets_doc
    assert "plain columns, not materialized measure columns" in bootstrap_ddl


def test_backend_genie_allowlists_match_provisioned_assets() -> None:
    from backend.services.repositories import databricks_genie_trust as trust_mod

    trust = importlib.reload(trust_mod)
    assert set(trusted_assets()) >= _expected_assets()
    assert set(trust._trusted_genie_asset_names()) >= _expected_assets()
    assert set(trust._TRUSTED_GENIE_ASSETS) >= _expected_assets()


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
    assert "marketing_eligible = true" in sql_nc
    assert "consent_status = 'opt_in'" in sql_nc
    assert "recommended_offer_code <> 'nurture'" in sql_nc
    assert "recommended_offer" in sql_nc
    assert "recommended_offer_code" in sql_nc
    assert "leading_recommended_offer" in sql_nc
    assert "case when segment_code" not in sql_nc
    assert "then 'refinance + heloc'" not in sql_nc
    assert "then 'cash-out / dscr review'" not in sql_nc
    assert "then 'retention review'" not in sql_nc


def test_space_spec_substitutes_configured_tenant_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIP_LENDER_NAME", "Acme Mortgage")

    spec = pgs.SpaceSpec.load(pgs.SPACE_YAML)
    serialized = json.loads(spec.to_serialized_payload())
    instruction_text = "\n".join(
        part
        for item in serialized["instructions"]["text_instructions"]
        for part in item["content"]
    )
    instruction_words = re.sub(r"\s+", " ", instruction_text)

    example_questions = {str(item.get("question") or "") for item in spec.example_question_sqls}
    assert (
        "Where should Acme Mortgage spend its next 10000 outreach touches this week, and why?"
        in example_questions
    )
    assert "tenant is Acme Mortgage" in instruction_words
    assert "{tenant_name}" not in instruction_text


def test_space_spec_substitutes_configured_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIP_DEFAULT_CATALOG", "acme_mip")

    spec = pgs.SpaceSpec.load(pgs.SPACE_YAML)
    serialized = json.loads(spec.to_serialized_payload())
    serialized_text = json.dumps(serialized)
    instruction_text = "\n".join(
        part
        for item in serialized["instructions"]["text_instructions"]
        for part in item["content"]
    )
    table_ids = {
        str(table.get("identifier"))
        for table in serialized["data_sources"]["tables"]
    }

    assert spec.catalog == "acme_mip"
    assert table_ids
    assert all(asset.startswith("acme_mip.") for asset in table_ids)
    assert "acme_mip.gold.borrower_360" in instruction_text
    assert "trusted `acme_mip.gold`" in instruction_text
    assert "other than `acme_mip`" in instruction_text
    assert re.search(r"(?<![A-Za-z0-9_])mip\.gold\.", serialized_text) is None
    assert re.search(r"(?<![A-Za-z0-9_])mip\.semantics\.", serialized_text) is None
    assert re.search(r"(?<![A-Za-z0-9_])mip(?![A-Za-z0-9_])", serialized_text) is None


def test_backend_genie_trust_and_canonical_sql_follow_configured_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.services.repositories import databricks_genie_canonical as canonical_mod
    from backend.services.repositories import databricks_genie_trust as trust_mod

    original_catalog = settings.mip_default_catalog
    monkeypatch.setattr(settings, "mip_default_catalog", "acme_mip")
    try:
        trust = importlib.reload(trust_mod)
        canonical = importlib.reload(canonical_mod)

        assert "acme_mip.gold.borrower_360" in trust._TRUSTED_GENIE_ASSETS
        assert "mip.gold.borrower_360" not in trust._TRUSTED_GENIE_ASSETS
        assert "FROM acme_mip.gold.borrower_360" in canonical._CANONICAL_ITM_COUNT_SQL
        assert "FROM mip.gold.borrower_360" not in canonical._CANONICAL_ITM_COUNT_SQL
    finally:
        monkeypatch.setattr(settings, "mip_default_catalog", original_catalog)
        importlib.reload(trust_mod)
        importlib.reload(canonical_mod)


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
    serialized = json.loads(pgs.SpaceSpec.load(pgs.SPACE_YAML).to_serialized_payload())
    instruction_text = "\n".join(
        part
        for item in serialized["instructions"]["text_instructions"]
        for part in item["content"]
    )
    instruction_words = re.sub(r"\s+", " ", instruction_text)

    assert "third-party lender or lead-vendor-owned" in space_text
    assert "configured tenant lender" in space_text
    assert "tenant is\n     {tenant_name}" in space_text
    assert "tenant is Summit Mortgage" in instruction_words
    assert "LendingTree-sourced borrower" in space_text
    assert "Rocket\n     Mortgage customers" in space_text
    assert "Quicken Loans customers" in space_text
    assert "third-party lender or\n  lead-vendor-owned customers" in mirror_text
    assert "tenant is\n  {tenant_name}" in mirror_text
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
    for asset in _expected_assets():
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
    assert identifiers == _expected_assets()
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
