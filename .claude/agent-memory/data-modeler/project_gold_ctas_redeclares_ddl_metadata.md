---
name: gold-ctas-redeclares-ddl-metadata
description: Gold rebuilds must keep CLUSTER BY/TBLPROPERTIES in the CTAS clause and re-apply column comments via post-CTAS COMMENT ON COLUMN — the typeless CTAS column list is a LIVE PARSE ERROR on DBSQL (audit P2-8 + re-audit corrections)
metadata:
  type: project
---

Every `CREATE OR REPLACE TABLE` rebuild (gold transformations, the
lifecycle sync job, demo_first_party_feeds) silently DROPS the
`CLUSTER BY`, column `COMMENT`s, table `COMMENT`, and `TBLPROPERTIES`
declared in the matching DDL — the refresh jobs never re-run the
`CREATE TABLE IF NOT EXISTS` DDL.

**Why:** audit P2-8 (2026-06-11). Column comments are part of the Genie
grounding + asset-page story, so losing them is a real regression.

**How to apply — THE WORKING PATTERN (proven live by `mip_refresh_scores`
TERMINATED SUCCESS after commit bccb5b2):**

```sql
CREATE OR REPLACE TABLE mip.gold.t
CLUSTER BY (cols)
TBLPROPERTIES ( ... )
AS
WITH ... SELECT ...;

COMMENT ON COLUMN mip.gold.t.col1 IS '...';
COMMENT ON COLUMN mip.gold.t.`timestamp` IS '...';  -- backtick reserved words
```

The SQL file task executes the statements in order, so the comments are
re-applied immediately after the rebuild in the same task.

**DO NOT use a typeless CTAS column list** — `CREATE OR REPLACE TABLE t
(col COMMENT '...') AS SELECT` is a **PARSE_SYNTAX_ERROR on DBSQL**,
observed live 2026-06-11 (first gold run after the original P2-8 fix
failed exactly this way; an earlier version of this memory taught that
syntax as "working" — it is not).

Hard contracts (CI-pinned by `tests/unit/test_gold_column_comment_guard.py`,
both directions):
- every COMMENT ON COLUMN text == the DDL comment text, byte-identical;
- every rebuilt table that has DDL comments re-applies them in the same file;
- `jobs/sync_lifecycle_state.py` `LIFECYCLE_COLUMN_COMMENTS` == DDL §7;
- borrower_360 `equity_pct` stays `100 - <the clamped/rounded ltv>`
  (independent half-up rounding rendered equity+ltv=101 on exact-.5 CLTV).

Scope notes:
- `gold_funnel_snapshot_daily.sql` uses `MERGE INTO`, NOT CTAS — MERGE
  preserves table metadata, so it is OUT of this pattern.
- DDL homes: per-file `sql/ddl/gold_<t>.sql` for 10 tables; the rest
  (borrower_dossier, lockin_cohort, borrower_lifecycle_state,
  funnel_snapshot_daily, first_party.*) live in `sql/ddl/003_gold_tables.sql`.
