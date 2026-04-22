---
name: Gold Lakeflow DLT retired — CTAS is authoritative
description: mip_gold_pipeline DLT was deleted in slice13-accuracy cleanup; gold materialisation is the CTAS chain in the mip_refresh_scores job.
type: project
---

The gold Lakeflow DLT (`pipelines/lakeflow/mip_gold_pipeline.py`) was deleted as part of the slice13-accuracy cleanup. Its `@dlt.table` functions were thin wrappers that ran `spark.sql("SELECT * FROM mip.gold.<name>")` against tables the warehouse-CTAS files (`sql/transformations/gold_*.sql`) had already populated — a dual-write mirror that added deploy surface without functional value.

**Why:** Single authoritative gold materialisation path. The CTAS chain under `mip_refresh_scores` (in `databricks.yml`) is what actually populates `mip.gold.*`; the DLT mirror read those same tables back and re-wrote them, which only risked schema drift between the two declarations.

**How to apply:** When adding a new gold table in a future slice, put the logic in `sql/transformations/gold_<name>.sql` as a CTAS (`CREATE OR REPLACE TABLE ...`). Add a `ctas_<name>` task to the `mip_refresh_scores` job in `databricks.yml` (respect dependency order). Put schema contracts in `sql/ddl/gold_<name>.sql` and reference them from `sql/ddl/003_gold_tables.sql` so the idempotent init job provisions the empty table on first deploy. Do NOT re-introduce a DLT mirror.
