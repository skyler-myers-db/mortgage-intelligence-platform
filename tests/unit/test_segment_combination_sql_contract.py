"""SQL text contract for the signal stack (audit wow-stage-5).

Two SQL sites key a borrower by the exact set of core segment codes it
carries: the gold CTAS (``sql/transformations/gold_segment_combination_rollup.sql``,
the addressable rows) and the repository's live contactable aggregate. Both
must carry the SAME key, ``COMBINATION_KEY_SQL``, or the live subset and its
precomputed superset would be keyed differently and never join. Pins:

* the key fragment is verbatim in the CTAS and in the repository statement
  (whitespace-normalized);
* its literal code list is exactly ``CORE_SEGMENT_CODES`` (the prototype's
  six core segments, the segment repository's canonical order prefix and the
  reviewed Genie replay set);
* the live statement embeds ``eligible_sql_predicate()`` verbatim and reads
  gold only;
* the CTAS reads the shared refresh anchor and never calls
  ``CURRENT_TIMESTAMP(`` itself;
* the DDL, the 003 manifest block and the bundle task are present, the task
  behind the freshness sentinel through the rendered path, so
  ``./scripts/deploy.sh -t dev`` builds the table.
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.schemas.lead import GENIE_REPLAY_SEGMENT_CODES
from backend.schemas.segment_combinations import CORE_SEGMENT_CODES
from backend.services.eligibility import eligible_sql_predicate
from backend.services.repositories.databricks_geo import DatabricksSegmentRepository
from backend.services.repositories.databricks_segment_combinations import (
    COMBINATION_KEY_SQL,
    SEGMENT_COMBINATIONS_SQL,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
TRANSFORM = REPO_ROOT / "sql" / "transformations" / "gold_segment_combination_rollup.sql"
DDL = REPO_ROOT / "sql" / "ddl" / "gold_segment_combination_rollup.sql"
MANIFEST = REPO_ROOT / "sql" / "ddl" / "003_gold_tables.sql"
BUNDLE = REPO_ROOT / "databricks.yml"
CAPTURE = REPO_ROOT / "sql" / "transformations" / "capture_refresh_timestamp.sql"
_ETL_ONLY_SCHEMA = re.compile(r"(?i)(?<![\w$])(?:[\w`]+\.)?`?(?:silver|raw)`?\.`?[a-z_]")


def _strip_line_comments(sql_text: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in sql_text.splitlines())


def _normalize(sql_text: str) -> str:
    text = " ".join(_strip_line_comments(sql_text).split())
    text = re.sub(r"\(\s+", "(", text)
    return re.sub(r"\s+\)", ")", text)


def _ctas() -> str:
    """The executable CTAS statement (the COMMENT ON tail excluded)."""
    return _strip_line_comments(TRANSFORM.read_text(encoding="utf-8")).split(";", 1)[0]


def test_core_codes_are_the_canonical_six() -> None:
    assert DatabricksSegmentRepository._CANONICAL_ORDER[:6] == CORE_SEGMENT_CODES
    assert set(CORE_SEGMENT_CODES) == set(GENIE_REPLAY_SEGMENT_CODES)


def test_key_fragment_is_verbatim_in_the_ctas_and_the_live_statement() -> None:
    key = _normalize(COMBINATION_KEY_SQL)
    assert key in _normalize(_ctas())
    assert key in _normalize(SEGMENT_COMBINATIONS_SQL)
    # Both group and join on it: the CTAS groups by the keyed column, the live
    # aggregate groups by its first (key) column and joins on it.
    assert "GROUP BY k.combination_key" in _normalize(_ctas())
    assert "l.combination_key = r.combination_key" in SEGMENT_COMBINATIONS_SQL


def test_key_literal_list_is_the_core_codes() -> None:
    literal = re.search(r"IN \(([^)]*)\)", COMBINATION_KEY_SQL)
    assert literal
    codes = tuple(part.strip().strip("'") for part in literal.group(1).split(","))
    assert codes == CORE_SEGMENT_CODES


def test_live_statement_embeds_the_eligibility_predicate_verbatim() -> None:
    assert eligible_sql_predicate() in SEGMENT_COMBINATIONS_SQL


def test_live_statement_reads_gold_only() -> None:
    hit = _ETL_ONLY_SCHEMA.search(SEGMENT_COMBINATIONS_SQL)
    assert hit is None, f"the live statement reads an ETL-only schema: {hit.group(0) if hit else ''}"
    assert "mip.gold.segment_combination_rollup" in SEGMENT_COMBINATIONS_SQL
    assert "mip.gold.borrower_360" in SEGMENT_COMBINATIONS_SQL


def test_ctas_reads_the_shared_anchor_and_never_the_clock() -> None:
    body = _strip_line_comments(TRANSFORM.read_text(encoding="utf-8"))
    assert "mip.ref.refresh_run_state" in body
    assert not re.search(r"CURRENT_TIMESTAMP\s*\(", body, re.IGNORECASE)
    assert "gold_segment_combination_rollup.sql" in CAPTURE.read_text(encoding="utf-8")
    # Only non-empty keys: a borrower with no core code has no row.
    assert "combination_key <> ''" in _normalize(_ctas())


def test_ddl_manifest_and_bundle_register_the_table() -> None:
    ddl = DDL.read_text(encoding="utf-8")
    manifest = MANIFEST.read_text(encoding="utf-8")
    assert "20. mip.gold.segment_combination_rollup" in manifest
    for text in (ddl, manifest):
        assert "CREATE TABLE IF NOT EXISTS mip.gold.segment_combination_rollup" in text
        assert re.search(r"^\s*combination_key\s+STRING\s+NOT NULL", text, re.MULTILINE)
        assert re.search(r"^\s*segment_codes\s+ARRAY<STRING>\s+NOT NULL", text, re.MULTILINE)
        assert re.search(r"^\s*signal_count\s+INT\s+NOT NULL", text, re.MULTILINE)
        assert re.search(r"^\s*addressable_borrowers\s+BIGINT\s+NOT NULL", text, re.MULTILINE)
        assert re.search(r"^\s*refreshed_at\s+TIMESTAMP\s+NOT NULL", text, re.MULTILINE)
        assert "CLUSTER BY (combination_key)" in text
        assert not re.search(r"^\s*contactable", text, re.MULTILINE), "contactable is live, never stored"
    cited = re.findall(r"\b((?:docs|sql|tests|backend)/[\w./-]+\.(?:md|sql|py|json))\b", ddl)
    assert cited
    for path in cited:
        assert (REPO_ROOT / path).exists(), f"{DDL.name} cites {path}, which does not exist"

    bundle = BUNDLE.read_text(encoding="utf-8")
    block = re.search(r"mip_refresh_scores:(.*?)(?:\n    [a-zA-Z_]+:|\Z)", bundle, re.DOTALL)
    assert block, "could not locate mip_refresh_scores block in databricks.yml."
    task = re.search(
        r"- task_key:\s*ctas_segment_combination_rollup\s*.*?depends_on:\s*\n\s*- task_key:\s*([A-Za-z0-9_]+)"
        r".*?path:\s*(\S+)",
        block.group(1),
        re.DOTALL,
    )
    assert task, "ctas_segment_combination_rollup is not wired into mip_refresh_scores."
    assert task.group(1) == "assert_borrower_360_fresh"
    assert task.group(2) == "sql/_rendered/transformations/gold_segment_combination_rollup.sql"
    assert "17. segment_combination_rollup" in bundle
