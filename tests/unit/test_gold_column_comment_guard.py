"""Refresh-stable metadata drift guards (re-audit 2026-06-11).

The audit-P2-8 fix re-applies column comments after every CREATE OR
REPLACE via post-CTAS ``COMMENT ON COLUMN`` statements — but nothing
guarded those 269+ statements against drifting from the DDL, and two
rebuild surfaces escaped the fix entirely (the lifecycle sync job and the
demo first-party feeds). These tests pin, for every rebuild surface:

* transformation/job comments == DDL comments, byte-identical, BOTH
  directions (a comment edited in only one place fails loudly);
* the display-LTV / capped-equity split in borrower_360 (underwater borrowers
  can show LTV > 100, while equity_pct remains fail-closed for scoring).
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TRANSFORMS = REPO / "sql" / "transformations"
DDL_DIR = REPO / "sql" / "ddl"

COMMENT_ON_RE = re.compile(
    r"COMMENT ON COLUMN\s+(?P<fqn>[\w.]+)\.(?P<col>`[^`]+`|\w+)\s+IS\s+(?P<text>'(?:[^']|'')*')\s*;"
)
# Type spec may be a flattened ARRAY<STRUCT<field: TYPE, `quoted`: TYPE>>,
# so the charclass admits ':' and backticks (re-audit parser fix).
DDL_COL_RE = re.compile(
    r"^\s*(?P<col>`[^`]+`|[\w]+)\s+[A-Z][\w<>,():` ]*?\s+(?:NOT NULL\s+)?COMMENT\s+(?P<text>'(?:[^']|'')*')",
    re.MULTILINE,
)


def _flatten_generics(body: str) -> str:
    """Join newlines that fall INSIDE angle brackets so multiline
    ARRAY<STRUCT<...>> column declarations become single lines (the
    borrower_dossier evidence/timeline columns) without disturbing the
    one-column-per-line shape elsewhere. Angle brackets inside quoted
    comment text (e.g. ``'fractional; < 0.03'``) are ignored — counting
    them swallowed every subsequent newline. A continuation line that
    holds only ``NOT NULL COMMENT ...`` (the struct columns put the
    comment on the next line) is joined to its declaration too."""
    out: list[str] = []
    depth = 0
    in_quote = False
    chars = body
    i = 0
    while i < len(chars):
        ch = chars[i]
        if ch == "'":
            # '' inside a quoted literal is an escaped quote, not a close.
            if in_quote and i + 1 < len(chars) and chars[i + 1] == "'":
                out.append("''")
                i += 2
                continue
            in_quote = not in_quote
        elif not in_quote:
            if ch == "<":
                depth += 1
            elif ch == ">":
                depth = max(0, depth - 1)
        out.append(" " if ch == "\n" and depth > 0 and not in_quote else ch)
        i += 1
    flattened = "".join(out)
    # Join "NOT NULL COMMENT '...'" continuation lines onto their column.
    return re.sub(r"\n\s+(?=(?:NOT NULL\s+)?COMMENT\s+')", " ", flattened)


def _ddl_comment_map() -> dict[str, dict[str, str]]:
    """{table_fqn: {column: 'comment literal'}} from every DDL file.

    Re-audit #3 (2026-06-12): every gold table is declared in TWO DDL
    files (the numbered bootstrap 001/003/004 AND its per-table
    ``gold_*.sql`` / ``silver_*.sql`` spec). The old ``tables[fqn] = cols``
    let whichever file sorts LAST silently shadow the other, so the guard
    validated each table against only one of its two declarations — a
    drift between the duplicates was invisible. Now duplicate
    declarations are MERGED and any disagreement (different text, or a
    column commented in one file and bare in the other while both
    declare comments) fails loudly.
    """
    tables: dict[str, dict[str, str]] = {}
    sources: dict[str, str] = {}
    conflicts: list[str] = []
    for ddl_path in sorted(DDL_DIR.glob("*.sql")):
        text = ddl_path.read_text(encoding="utf-8")
        for match in re.finditer(
            r"CREATE TABLE IF NOT EXISTS\s+(?P<fqn>[\w.]+)\s*\((?P<body>.*?)\n\)",
            text,
            re.DOTALL,
        ):
            body = _flatten_generics(match.group("body"))
            cols = {
                col_match.group("col"): col_match.group("text")
                for col_match in DDL_COL_RE.finditer(body)
                if col_match.group("col") not in {"NOT", "COMMENT", "USING"}
            }
            if not cols:
                continue
            fqn = match.group("fqn")
            if fqn not in tables:
                tables[fqn] = dict(cols)
                sources[fqn] = ddl_path.name
                continue
            prior_name = sources[fqn]
            prior = tables[fqn]
            for col in sorted(set(prior) | set(cols)):
                if col not in prior:
                    conflicts.append(
                        f"{fqn}.{col}: commented in {ddl_path.name} but not in {prior_name}"
                    )
                elif col not in cols:
                    conflicts.append(
                        f"{fqn}.{col}: commented in {prior_name} but not in {ddl_path.name}"
                    )
                elif prior[col] != cols[col]:
                    conflicts.append(
                        f"{fqn}.{col}: comment text differs between "
                        f"{prior_name} and {ddl_path.name}"
                    )
            prior.update(cols)
    assert conflicts == [], (
        "duplicate DDL declarations disagree — align the per-table spec and "
        "the numbered bootstrap file byte-for-byte:\n" + "\n".join(conflicts)
    )
    return tables


def test_duplicate_ddl_declarations_agree() -> None:
    """Direct hook for the merge-conflict assertion inside
    ``_ddl_comment_map`` so a disagreement is reported as its own failure
    (not just as collateral noise in whichever parity test runs first)."""
    _ddl_comment_map()


def _statement_comment_map(text: str) -> dict[str, dict[str, str]]:
    tables: dict[str, dict[str, str]] = {}
    for match in COMMENT_ON_RE.finditer(text):
        tables.setdefault(match.group("fqn"), {})[match.group("col")] = match.group("text")
    return tables


def test_transformation_column_comments_match_ddl_exactly() -> None:
    ddl = _ddl_comment_map()
    offenders: list[str] = []
    for path in sorted(TRANSFORMS.glob("*.sql")):
        applied = _statement_comment_map(path.read_text(encoding="utf-8"))
        for fqn, cols in applied.items():
            ddl_cols = ddl.get(fqn)
            if ddl_cols is None:
                continue  # table has no DDL comment contract (e.g. ref tables)
            for col, text in cols.items():
                if col not in ddl_cols:
                    offenders.append(f"{path.name}: {fqn}.{col} commented but not in DDL")
                elif ddl_cols[col] != text:
                    offenders.append(f"{path.name}: {fqn}.{col} text drifted from DDL")
            for col in ddl_cols:
                if col not in cols:
                    offenders.append(
                        f"{path.name}: {fqn}.{col} has a DDL comment the rebuild does not re-apply"
                    )
    assert offenders == [], "\n".join(offenders)


def test_every_gold_create_or_replace_reapplies_comments() -> None:
    """Any gold/first_party CREATE OR REPLACE whose table carries DDL
    comments must re-apply them in the same file (the defect class that
    escaped twice)."""
    ddl = _ddl_comment_map()
    offenders: list[str] = []
    for path in sorted(TRANSFORMS.glob("*.sql")):
        text = path.read_text(encoding="utf-8")
        rebuilt = set(re.findall(r"CREATE OR REPLACE TABLE\s+([\w.]+)", text))
        applied = _statement_comment_map(text)
        for fqn in rebuilt:
            if fqn in ddl and fqn not in applied:
                offenders.append(f"{path.name}: rebuilds {fqn} but re-applies no DDL comments")
    assert offenders == [], "\n".join(offenders)


def test_lifecycle_job_comments_match_ddl() -> None:
    from jobs.sync_lifecycle_state import LIFECYCLE_COLUMN_COMMENTS

    ddl = _ddl_comment_map()["mip.gold.borrower_lifecycle_state"]
    # DDL stores quoted literals; the job stores raw text. Normalize.
    ddl_raw = {col: text[1:-1].replace("''", "'") for col, text in ddl.items()}
    assert ddl_raw == LIFECYCLE_COLUMN_COMMENTS, (
        "jobs/sync_lifecycle_state.py LIFECYCLE_COLUMN_COMMENTS drifted from "
        "sql/ddl/003_gold_tables.sql §7"
    )


def test_borrower_360_display_ltv_can_exceed_100_while_equity_clamps() -> None:
    """Pin the split between display LTV and scoring equity.

    Underwater borrowers should show true LTV (>100) while equity_pct stays
    fail-closed at 0 for scoring and offer routing.
    """
    text = (TRANSFORMS / "gold_borrower_360.sql").read_text(encoding="utf-8")
    assert "estimated_current_lien_balance" in text
    assert "mip.gold.fn_estimated_upb" in text
    assert re.search(
        r"THEN ROUND\(100\.0 \* COALESCE\(b\.estimated_current_lien_balance, 0\) / b\.avm_value\)",
        text,
    ), "equity_pct no longer derives from the estimated current lien balance"
    assert re.search(
        r"AS INT\) AS ltv",
        text,
    )
    assert "GREATEST(0, LEAST(100, CASE" not in re.search(
        r"-- LTV: display truth\..*?AS INT\) AS ltv",
        text,
        re.DOTALL,
    ).group(0), "display LTV is upper-capped again"
    equity_block = re.search(r"-- Equity %.*?END AS INT\) AS equity_pct", text, re.DOTALL).group(0)
    assert equity_block.index("b.avm_value IS NOT NULL") < equity_block.index("b.estimated_cltv"), (
        "Cotality estimated_cltv must remain only a no-AVM fallback; primary "
        "equity math uses estimated current lien balance."
    )


def test_listing_price_evidence_is_plausibility_guarded() -> None:
    borrower_sql = (TRANSFORMS / "gold_borrower_360.sql").read_text(encoding="utf-8")
    evidence_sql = (TRANSFORMS / "gold_evidence_events.sql").read_text(encoding="utf-8")

    for text in (borrower_sql, evidence_sql):
        assert "listing_price < 25000" in text
        assert "listing_price < " in text and "* 0.15" in text
        assert "listing_price > " in text and "* 5.0" in text

    assert "plausible_listing_price" in evidence_sql
    assert "WHEN la.listing_price IS NOT NULL THEN CONCAT(' at $'" not in evidence_sql
