---
name: gold-ctas-redeclares-ddl-metadata
description: Gold CTAS transformations must re-declare CLUSTER BY/TBLPROPERTIES/column COMMENTs because COR TABLE drops DDL metadata each refresh (audit P2-8 pattern)
metadata:
  type: project
---

Every `sql/transformations/gold_*.sql` that uses `CREATE OR REPLACE TABLE` (CTAS) silently DROPS the `CLUSTER BY`, column `COMMENT`s, and `TBLPROPERTIES` declared in the matching DDL on every refresh — the `mip_refresh_scores` job runs the CTAS, never the `CREATE TABLE IF NOT EXISTS` DDL again.

**Why:** audit finding P2-8 (remediated 2026-06-11 on branch fix/audit-2026-06-11-remediation). Column comments are part of the Genie grounding + asset-page story, so losing them is a real regression, not cosmetic.

**How to apply:** when you touch a gold CTAS, keep the metadata re-declaration intact. The working Databricks-SQL syntax is a typeless column-COMMENT list before `AS`:
```
CREATE OR REPLACE TABLE mip.gold.t (
  col1 COMMENT '...',
  `timestamp` COMMENT '...'   -- backtick reserved-word identifiers
)
CLUSTER BY (cols)
TBLPROPERTIES ( ... )
AS
WITH ... SELECT ...
```
The column-list order MUST equal the final SELECT projection order exactly (no types, COMMENT only). Contract is: CTAS column list == DDL column list (name+order). DDL lives in `sql/ddl/gold_<t>.sql` for 10 tables; the 4 without a per-file DDL (borrower_dossier, lockin_cohort, borrower_lifecycle_state, funnel_snapshot_daily) declare schema in `sql/ddl/003_gold_tables.sql`.

Scope notes:
- `gold_funnel_snapshot_daily.sql` uses `MERGE INTO`, NOT CTAS — MERGE preserves table metadata, so it is OUT of this pattern.
- `gold_borrower_lifecycle_state.sql` IS a CTAS (manual-fallback wipe-to-empty); `jobs/sync_lifecycle_state.py` owns the populated rewrite and must keep the same shape.
- Validation: `python3 tools/render_sql.py --catalog mip` (deploy.sh step 1a) then `.venv/bin/pytest tests/unit -k "sql or gold or silver or metric or transform"`. See also [[gold-refresh-forbids-current-timestamp]].
