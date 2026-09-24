"""SQL text contract for the Rate Lever grid (audit wow-stage-1).

Two SQL sites compute "in the money at par + step": the gold CTAS
(``sql/transformations/gold_rate_sensitivity_rollup.sql``, the addressable
grid) and the repository's live contactable aggregate. Both must carry the
SAME note gate and per-step rule, taken from ``backend/services/rate_scenario``,
or the contactable subset and its superset would be counted by different
rules. These pins keep them in step and keep the arithmetic honest:

* both texts embed ``NOTE_RATE_GATE_SQL`` and ``SCENARIO_ITM_SQL`` verbatim
  (whitespace-normalized);
* the CTAS grid literal is ``RATE_SCENARIO_STEPS_BPS``;
* no DECIMAL ``10000.0`` literal, no bin shifting off ``rate_spread_bps``, no
  per-task ``CURRENT_TIMESTAMP`` in the CTAS;
* the base par is ``borrower_360.market_rate_fraction`` (never silver
  ``is_latest``), and the lien join is a LEFT JOIN (gated rows stay in);
* the live statement embeds ``eligible_sql_predicate()`` verbatim;
* the bundle wires the CTAS behind the freshness sentinel, through the
  rendered path, so ``./scripts/deploy.sh -t dev`` builds it.
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.services.eligibility import eligible_sql_predicate
from backend.services.rate_scenario import (
    NOTE_RATE_GATE_SQL,
    RATE_SCENARIO_STEPS_BPS,
    SCENARIO_ITM_SQL,
)
from backend.services.repositories.databricks_rate_sensitivity import RATE_SENSITIVITY_SQL

REPO_ROOT = Path(__file__).resolve().parents[2]
TRANSFORM = REPO_ROOT / "sql" / "transformations" / "gold_rate_sensitivity_rollup.sql"
DDL = REPO_ROOT / "sql" / "ddl" / "gold_rate_sensitivity_rollup.sql"
MANIFEST = REPO_ROOT / "sql" / "ddl" / "003_gold_tables.sql"
BUNDLE = REPO_ROOT / "databricks.yml"
REPOSITORY = REPO_ROOT / "backend" / "services" / "repositories" / "databricks_rate_sensitivity.py"


def _strip_line_comments(sql_text: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in sql_text.splitlines())


def _normalize(sql_text: str) -> str:
    """Collapse whitespace and the padding inside parentheses."""
    text = " ".join(_strip_line_comments(sql_text).split())
    text = re.sub(r"\(\s+", "(", text)
    return re.sub(r"\s+\)", ")", text)


def _ctas() -> str:
    """The executable CTAS statement (the COMMENT ON COLUMN tail excluded)."""
    body = _strip_line_comments(TRANSFORM.read_text(encoding="utf-8"))
    return body.split(";", 1)[0]


def test_both_sites_embed_the_shared_note_gate_and_rule() -> None:
    gate = _normalize(NOTE_RATE_GATE_SQL)
    rule = _normalize(SCENARIO_ITM_SQL)
    for name, text in (("CTAS", _ctas()), ("live statement", RATE_SENSITIVITY_SQL)):
        normalized = _normalize(text)
        assert gate in normalized, f"{name} must embed NOTE_RATE_GATE_SQL verbatim"
        assert rule in normalized, f"{name} must embed SCENARIO_ITM_SQL verbatim"


def test_ctas_grid_literal_is_the_python_grid() -> None:
    match = re.search(r"EXPLODE\(\s*ARRAY\(([^)]*)\)\s*\)\s*AS\s+step_bps", _ctas())
    assert match, "the CTAS must explode the step grid literal"
    steps = tuple(int(value) for value in match.group(1).split(","))
    assert steps == RATE_SCENARIO_STEPS_BPS


def test_ctas_arithmetic_is_double_and_never_bin_shifted() -> None:
    body = _ctas()
    assert "10000.0" not in body, "a DECIMAL literal changes the step arithmetic"
    assert "rate_spread_bps" not in body, "the scenario never shifts rounded spread bins"
    assert not re.search(r"CURRENT_TIMESTAMP\s*\(", body, re.IGNORECASE)
    assert "mip.ref.refresh_run_state" in body
    assert re.search(r"AS\s+book_as_of\b", body)
    assert re.search(r"AS\s+refreshed_at\b", body)
    # No literal threshold predicates: the refresh's own thresholds decide.
    assert not re.search(r">=\s*75\b", body)
    assert not re.search(r">=\s*15\b", body)


def test_ctas_base_par_is_the_scored_par_and_gated_rows_stay_in() -> None:
    normalized = _normalize(_ctas())
    assert "b.market_rate_fraction" in normalized
    assert "market_rates_weekly" not in normalized, "the base par is borrower_360's, not silver is_latest"
    assert "is_latest" not in normalized
    assert "LEFT JOIN mip.silver.lien_current AS lc ON lc.clip = b.clip" in normalized
    # The population is every borrower_360 row with a state, not only the gated ones.
    book = re.search(r"\bbook AS \((.*?)\), book_cells AS", normalized)
    assert book, "could not locate the book CTE"
    where = book.group(1).split(" WHERE ", 1)[1]
    assert where.strip() == "b.state IS NOT NULL", where
    # rate_movable counts only the rows a scenario can move.
    assert re.search(r"WHEN k\.note_rate_fraction IS NOT NULL THEN k\.borrower_count", normalized)


def test_live_statement_embeds_the_eligibility_predicate_verbatim() -> None:
    assert eligible_sql_predicate() in RATE_SENSITIVITY_SQL
    normalized = _normalize(RATE_SENSITIVITY_SQL)
    assert "LEFT JOIN mip.silver.lien_current AS lc ON lc.clip = b.clip" in normalized
    # The live grid is the gold grid, so the two align by construction.
    assert "SELECT DISTINCT step_bps FROM mip.gold.rate_sensitivity_rollup" in normalized
    assert "LEFT JOIN contactable AS c ON c.state = r.state AND c.step_bps = r.step_bps" in normalized
    assert "10000.0" not in normalized
    module = REPOSITORY.read_text(encoding="utf-8")
    assert module.count("self._client.execute(") == 1, "one statement per read"


def test_ddl_manifest_and_bundle_register_the_table() -> None:
    ddl = DDL.read_text(encoding="utf-8")
    manifest = MANIFEST.read_text(encoding="utf-8")
    for text in (ddl, manifest):
        assert "CREATE TABLE IF NOT EXISTS mip.gold.rate_sensitivity_rollup" in text
        assert re.search(r"^\s*step_bps\s+INT\s+NOT NULL", text, re.MULTILINE)
        assert re.search(r"^\s*in_the_money_borrowers\s+BIGINT\s+NOT NULL", text, re.MULTILINE)
        assert re.search(r"^\s*book_as_of\s+TIMESTAMP\s+NOT NULL", text, re.MULTILINE)
    cited = re.findall(r"\b((?:docs|sql|tests|backend)/[\w./-]+\.(?:md|sql|py|json))\b", ddl)
    assert cited
    for path in cited:
        assert (REPO_ROOT / path).exists(), f"{DDL.name} cites {path}, which does not exist"

    bundle = BUNDLE.read_text(encoding="utf-8")
    block = re.search(r"mip_refresh_scores:(.*?)(?:\n    [a-zA-Z_]+:|\Z)", bundle, re.DOTALL)
    assert block, "could not locate mip_refresh_scores block in databricks.yml."
    task = re.search(
        r"- task_key:\s*ctas_rate_sensitivity_rollup\s*.*?depends_on:\s*\n\s*- task_key:\s*([A-Za-z0-9_]+)"
        r".*?path:\s*(\S+)",
        block.group(1),
        re.DOTALL,
    )
    assert task, "ctas_rate_sensitivity_rollup is not wired into mip_refresh_scores."
    assert task.group(1) == "assert_borrower_360_fresh"
    assert task.group(2) == "sql/_rendered/transformations/gold_rate_sensitivity_rollup.sql"
    assert "15. rate_sensitivity_rollup" in bundle
