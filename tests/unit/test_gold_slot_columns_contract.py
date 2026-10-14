"""The W5c gold slot columns: score points, Crossed the line, competitor liens.

Offline half of the contract for the columns this slot adds to gold
(audits wow-stage-2, wow-stage-4 and wow-ai-3):

* the five ``*_points`` columns sit right after ``confidence``, in the same
  order, as nullable ``DECIMAL(5,2)`` in every declaration of borrower_360,
  lead_population and borrower_dossier; borrower_360 projects them from its
  OWN subscores CTE (never gold.lead_scores, which the job builds after it)
  with the canonical weights and no COALESCE;
* the dossier's ``first_pos_date`` / ``first_pos_rate_type`` /
  ``first_itm_week`` sit after ``trigger_timeline`` and before
  ``refreshed_at`` in both the CTAS and the 003 declaration; the rate type is
  a closed FIX / ARM vocabulary; the crossing reuses fn_in_the_money and
  fn_rate_spread over the MORTGAGE30US weeks with the rate window's book
  eligibility;
* the Genie-reachable column audit: every new column on a Genie trusted asset
  is numeric, a date, or a closed upper-case code. None is a descriptor, so
  none can take the title-case person-name shape. The live SELECT DISTINCT
  re-run is the integrator's.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import yaml

from backend.services import scoring

REPO = Path(__file__).resolve().parents[2]
TRANSFORMS = REPO / "sql" / "transformations"
DDL = REPO / "sql" / "ddl"

POINT_COLUMNS = (
    "economic_incentive_points",
    "intent_trigger_points",
    "fit_points",
    "relationship_points",
    "evidence_points",
)
CROSSING_COLUMNS = ("first_pos_date", "first_pos_rate_type", "first_itm_week")

# Every column this slot adds, by table, with its declared type.
NEW_COLUMNS: dict[str, dict[str, str]] = {
    "mip.gold.borrower_360": {name: "DECIMAL(5,2)" for name in POINT_COLUMNS},
    "mip.gold.lead_population": {name: "DECIMAL(5,2)" for name in POINT_COLUMNS},
    "mip.gold.borrower_dossier": {
        **{name: "DECIMAL(5,2)" for name in POINT_COLUMNS},
        "first_pos_date": "DATE",
        "first_pos_rate_type": "STRING",
        "first_itm_week": "DATE",
    },
    "mip.gold.funnel_snapshot_daily": {"competitor_lien_borrowers": "INT"},
}
# The ONLY string column: a closed upper-case code vocabulary.
CLOSED_CODE_COLUMNS = {("mip.gold.borrower_dossier", "first_pos_rate_type"): {"FIX", "ARM"}}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _blocks(text: str, fqn: str) -> list[list[tuple[str, str]]]:
    """Every CREATE TABLE IF NOT EXISTS body for ``fqn``: [(column, line)]."""
    out: list[list[tuple[str, str]]] = []
    for match in re.finditer(
        rf"CREATE TABLE IF NOT EXISTS {re.escape(fqn)} \((?P<body>.*?)\n\)", text, re.DOTALL
    ):
        cols: list[tuple[str, str]] = []
        for line in match.group("body").splitlines():
            head = re.match(r"\s+`?([a-z_][a-z0-9_]*)`?\s+[A-Z]", line)
            if head:
                cols.append((head.group(1), line))
        out.append(cols)
    return out


def _declarations(fqn: str) -> list[list[tuple[str, str]]]:
    found: list[list[tuple[str, str]]] = []
    for path in sorted(DDL.glob("*.sql")):
        found.extend(_blocks(_read(path), fqn))
    assert found, fqn
    return found


def _names(cols: list[tuple[str, str]]) -> list[str]:
    return [name for name, _line in cols]


def test_points_follow_confidence_as_nullable_decimals_in_every_declaration() -> None:
    for fqn in ("mip.gold.borrower_360", "mip.gold.lead_population", "mip.gold.borrower_dossier"):
        for cols in _declarations(fqn):
            names = _names(cols)
            at = names.index("confidence")
            assert tuple(names[at + 1 : at + 6]) == POINT_COLUMNS, fqn
            for name, line in cols[at + 1 : at + 6]:
                assert re.match(rf"\s+{name}\s+DECIMAL\(5,2\)\s+COMMENT '", line), (fqn, name)
                assert "NOT NULL" not in line, (fqn, name)


def test_borrower_360_projects_points_from_its_own_subscores_with_the_canonical_weights() -> None:
    text = _read(TRANSFORMS / "gold_borrower_360.sql")
    canonical = dict(
        zip(
            ("economic_incentive", "intent_trigger", "fit", "relationship", "evidence"),
            scoring._LEAD_SCORE_WEIGHTS,
            strict=True,
        )
    )
    udf = _read(REPO / "sql/uc_functions/fn_lead_score.sql")
    udf_weights = {
        name: Decimal(weight)
        for weight, name in re.findall(r"(\d\.\d+) \* COALESCE\((\w+),", udf)
    }
    assert udf_weights == canonical
    projected = re.findall(
        r"CAST\((\d\.\d+) \* ss\.(\w+) AS DECIMAL\(5,2\)\)\s+AS (\w+)_points,", text
    )
    assert [(name, Decimal(weight)) for weight, name, _alias in projected] == list(canonical.items())
    assert all(name == alias for _w, name, alias in projected)
    final_select = text.rsplit("\nSELECT\n", 1)[1]
    assert final_select.index("AS confidence,") < final_select.index("AS economic_incentive_points")
    for name in POINT_COLUMNS:
        assert f"COALESCE(ss.{name.removesuffix('_points')}" not in final_select, name
    for path in ("gold_borrower_360.sql", "gold_lead_population.sql", "gold_borrower_dossier.sql"):
        body = "\n".join(
            line for line in _read(TRANSFORMS / path).splitlines() if not line.lstrip().startswith("--")
        )
        assert not re.search(r"(FROM|JOIN)\s+mip\.gold\.lead_scores\b", body), path


def test_lead_population_and_the_dossier_carry_the_points_after_confidence() -> None:
    population = _read(TRANSFORMS / "gold_lead_population.sql")
    ranked = population.split("WITH ranked AS (", 1)[1].split("\nSELECT\n", 1)[0]
    carried = "".join(f"    b.{name},\n" for name in POINT_COLUMNS)
    assert "    b.confidence,\n" in ranked and carried in ranked
    assert ranked.index("    b.confidence,\n") < ranked.index(carried)
    final = population.split("\nSELECT\n", 2)[-1]
    assert "  confidence,\n" + "".join(f"  {name},\n" for name in POINT_COLUMNS) in final
    dossier = _read(TRANSFORMS / "gold_borrower_dossier.sql")
    select = dossier.rsplit("\nSELECT\n", 1)[1]
    assert select.index("  b.confidence,\n") < select.index("  b.economic_incentive_points,\n")


def test_the_crossing_columns_sit_between_the_timeline_and_refreshed_at() -> None:
    for cols in _declarations("mip.gold.borrower_dossier"):
        names = _names(cols)
        at = names.index("trigger_timeline")
        assert tuple(names[at + 1 : at + 4]) == CROSSING_COLUMNS
        assert names[at + 4] == "refreshed_at" and names[-1] == "refreshed_at"
    select = _read(TRANSFORMS / "gold_borrower_dossier.sql").rsplit("\nSELECT\n", 1)[1]
    order = [
        select.index("AS trigger_timeline,"),
        select.index("lc.first_pos_date,"),
        select.index("END AS first_pos_rate_type,"),
        select.index("cx.first_itm_week,"),
        select.index("AS refreshed_at"),
    ]
    assert order == sorted(order)


def test_the_rate_type_is_a_closed_vocabulary() -> None:
    select = _read(TRANSFORMS / "gold_borrower_dossier.sql").rsplit("\nSELECT\n", 1)[1]
    case = re.search(
        r"CASE UPPER\(TRIM\(lc\.first_pos_rate_type\)\)(?P<body>.*?)END AS first_pos_rate_type",
        select,
        re.DOTALL,
    )
    assert case is not None
    branches = re.findall(r"WHEN '([A-Z]+)' THEN '([A-Z]+)'", case.group("body"))
    assert branches == [("FIX", "FIX"), ("ARM", "ARM")]
    assert "ELSE" not in case.group("body"), "anything else must become NULL"


def test_the_crossing_reuses_the_rule_and_the_rate_window_eligibility() -> None:
    dossier = _read(TRANSFORMS / "gold_borrower_dossier.sql")
    window = _read(TRANSFORMS / "gold_rate_window_weekly.sql")
    crossing = dossier.split("crossing_book AS (", 1)[1].split("\nSELECT\n  -- Every column", 1)[0]
    for term in (
        "b.current_rate > 0",
        "UPPER(TRIM(lc.first_pos_rate_type)) = 'FIX'",
        "mip.gold.fn_bounded_mortgage_rate(lc.first_pos_rate)",
        "note_rate_fraction > 0.01",
        "note_rate_fraction < 0.15",
        "series_id = 'MORTGAGE30US'",
        "rate_pct IS NOT NULL",
        "rate_fraction IS NOT NULL",
    ):
        assert term in crossing, term
        assert term in window, term
    assert "mip.gold.fn_in_the_money(\n      mip.gold.fn_rate_spread(k.note_rate_fraction, w.rate_fraction)," in crossing
    # Cells, never weeks x book rows: the cross join reads the distinct cells.
    assert "FROM crossing_cells AS k\n  CROSS JOIN crossing_weeks AS w" in crossing
    assert "CAST(DATE_TRUNC('WEEK', lc.first_pos_date) AS DATE)" in crossing


def _trusted_assets() -> set[str]:
    space = yaml.safe_load(_read(REPO / "genie/mortgage_lead_intelligence_space.yml"))
    return {asset["name"] for asset in space["trusted_assets"]}


def test_every_new_genie_reachable_column_is_numeric_a_date_or_a_closed_code() -> None:
    trusted = _trusted_assets()
    reachable = [fqn for fqn in NEW_COLUMNS if fqn in trusted]
    assert sorted(reachable) == sorted(NEW_COLUMNS), "all four tables are trusted assets"
    for fqn, columns in NEW_COLUMNS.items():
        for cols in _declarations(fqn):
            lines = dict(cols)
            for name, declared in columns.items():
                line = lines[name]
                typ = re.match(rf"\s+{name}\s+(\S+)", line)
                assert typ is not None and typ.group(1) == declared, (fqn, name)
                if declared == "STRING":
                    assert (fqn, name) in CLOSED_CODE_COLUMNS, (
                        f"{fqn}.{name}: a new STRING column must be a closed code"
                    )
                else:
                    assert declared in {"DECIMAL(5,2)", "INT", "DATE"}, (fqn, name)
    for (_fqn, _name), vocabulary in CLOSED_CODE_COLUMNS.items():
        assert all(code.isupper() and code.isalpha() for code in vocabulary)
