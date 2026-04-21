---
name: Gold Lakeflow pipeline delegates to warehouse CTAS SQL
description: mip_gold_pipeline.py's @dlt.table functions run `spark.sql("SELECT * FROM mip.gold.X")` rather than reimplementing the CTAS in PySpark.
type: project
---

The gold Lakeflow pipeline (`pipelines/lakeflow/mip_gold_pipeline.py`) does NOT reimplement the CTAS queries in PySpark. Each `@dlt.table` reads the table that the matching `sql/transformations/gold_*.sql` CTAS already produced on the warehouse.

**Why:** Single source of truth for the join/scoring logic. The SQL in `sql/transformations/gold_*.sql` is the authoritative query; the DLT pipeline just declares the table identity, data expectations, and clustering. Rewriting the logic in PySpark would mean two places to keep in sync and two places where SQL↔Python parity could drift.

**How to apply:** When adding a new gold table in a future slice, put the logic in `sql/transformations/<name>.sql` as a CTAS (`CREATE OR REPLACE TABLE ...`) first. Then add a thin `@dlt.table` that reads it back via `spark.sql("SELECT * FROM mip.gold.<name>")`. Put data expectations (`@dlt.expect`) on the DLT side; they run at refresh time against the materialized rows. Put schema contracts in `sql/ddl/gold_<name>.sql` so `databricks bundle` can apply them via `sql_task` independent of the pipeline.
